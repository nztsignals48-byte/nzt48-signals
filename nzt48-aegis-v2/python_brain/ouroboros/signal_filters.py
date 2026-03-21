"""Signal Filters — Python-side pre-signal gates for config_writer.

SC-16: CUSUM dynamic mean (EWMA update)
SC-17: VPIN exchange-scoped bucket reset
SC-20: Half-Kelly until 250 trades
SC-21: Meta-labeler minimum sample size gate

These are SPECIFICATIONS for config_writer to emit into dynamic_weights.toml.
The Rust engine reads these as gate parameters. We don't modify the Rust code.

Usage: python3 -m python_brain.ouroboros.signal_filters
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SignalFilters] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("signal_filters")


# ---------------------------------------------------------------------------
# SC-16: CUSUM Dynamic Mean (EWMA) Configuration
# ---------------------------------------------------------------------------
# The Rust CUSUM detector uses a static mean μ set at session open.
# This function computes the EWMA decay parameter for config_writer
# to emit into dynamic_weights.toml so the engine can use it.
# Page (1954) requires reference level adaptation for structural breaks.

def compute_cusum_ewma_config(
    wal_dir: Path = WAL_DIR,
) -> dict:
    """Compute CUSUM EWMA configuration parameters.

    Returns dict with:
        cusum_ewma_enabled: bool (always True - we want dynamic mean)
        cusum_ewma_decay: float (0.94 default, Ouroboros tunes this)
        cusum_ewma_update_interval_sec: int (300 = 5 minutes)
        cusum_ewma_min_samples: int (20 bars before EWMA is trusted)
    """
    # Read any existing Ouroboros recommendations for tuned decay
    recs_file = DATA_DIR / "ouroboros_recommendations.json"
    decay = 0.94  # Default: slow decay, stable reference

    if recs_file.exists():
        try:
            recs = json.loads(recs_file.read_text())
            if "cusum_ewma_decay" in recs:
                decay = float(recs["cusum_ewma_decay"])
                decay = max(0.80, min(0.99, decay))  # Guardrail
        except Exception:
            pass

    config = {
        "cusum_ewma_enabled": True,
        "cusum_ewma_decay": round(decay, 4),
        "cusum_ewma_update_interval_sec": 300,  # 5 minutes
        "cusum_ewma_min_samples": 20,
    }
    log.info(f"SC-16 CUSUM EWMA config: decay={config['cusum_ewma_decay']}")
    return config


# ---------------------------------------------------------------------------
# SC-17: VPIN Exchange-Scoped Bucket Reset
# ---------------------------------------------------------------------------
# VPIN buckets must reset at each exchange's own market open, not at
# global mode transitions. This prevents session cross-contamination.

EXCHANGE_RESET_TIMES_UTC = {
    "TSE": "00:00",    # Tokyo Stock Exchange
    "HKEX": "01:30",   # Hong Kong Exchange
    "ASX": "00:00",    # Australian Stock Exchange (AEST), 23:00 AEDT
    "LSEETF": "08:00", # London Stock Exchange
    "SMART": "14:30",  # US exchanges (NYSE/NASDAQ via IBKR)
    "XETRA": "07:00",  # Frankfurt
    "EURONEXT": "07:00",  # Paris/Amsterdam
    "SGX": "01:00",    # Singapore
    "KSE": "00:00",    # Korea (broken, but include for completeness)
}

def compute_vpin_reset_config() -> dict:
    """Generate VPIN exchange-scoped reset configuration.

    Returns dict suitable for TOML emission:
        vpin_exchange_reset_enabled: True
        vpin_reset_times: dict of exchange → UTC reset hour:minute
        vpin_buckets_per_session: 50 (standard)
        vpin_bucket_volume_pct: 2.0 (each bucket = 2% of session volume)
    """
    config = {
        "vpin_exchange_reset_enabled": True,
        "vpin_reset_times": EXCHANGE_RESET_TIMES_UTC,
        "vpin_buckets_per_session": 50,
        "vpin_bucket_volume_pct": 2.0,
    }
    log.info(f"SC-17 VPIN exchange-scoped reset: {len(EXCHANGE_RESET_TIMES_UTC)} exchanges configured")
    return config


# ---------------------------------------------------------------------------
# SC-20: Half-Kelly Until 250 Validated Trades
# ---------------------------------------------------------------------------
# Thorp (1975): uncertain parameter estimates → half-Kelly.
# Count closed trades from WAL. If < 250, emit kelly_multiplier = 0.5.

def compute_kelly_scaling(
    wal_dir: Path = WAL_DIR,
    threshold: int = 250,
) -> dict:
    """Compute Kelly scaling factor based on trade count.

    Returns dict:
        kelly_trade_count: int (total closed trades found)
        kelly_multiplier: float (0.5 if < threshold, 1.0 otherwise)
        kelly_threshold: int (250)
    """
    trade_count = _count_closed_trades(wal_dir)
    multiplier = 0.5 if trade_count < threshold else 1.0

    config = {
        "kelly_trade_count": trade_count,
        "kelly_multiplier": round(multiplier, 2),
        "kelly_threshold": threshold,
    }
    log.info(f"SC-20 Kelly scaling: {trade_count} trades, multiplier={multiplier} (threshold={threshold})")
    return config


def _count_closed_trades(wal_dir: Path) -> int:
    """Count PositionClosed events across all WAL files."""
    count = 0
    wal_files = []

    # Current WAL files
    if wal_dir.exists():
        wal_files.extend(sorted(wal_dir.glob("*.ndjson")))

    # Archive WAL files
    archive_dir = wal_dir / "archive"
    if archive_dir.exists():
        wal_files.extend(sorted(archive_dir.glob("*.ndjson")))

    for wf in wal_files:
        try:
            with open(wf) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if event.get("event_type") == "PositionClosed":
                            count += 1
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue

    return count


# ---------------------------------------------------------------------------
# SC-21: Meta-Labeler Minimum Sample Size Gate
# ---------------------------------------------------------------------------
# de Prado (2018): logistic regression needs 1,000+ samples for stability.
# If insufficient trades, meta-labeler gate is BYPASSED (all signals pass).

def compute_meta_labeler_gate(
    wal_dir: Path = WAL_DIR,
    min_samples: int = 1000,
) -> dict:
    """Compute meta-labeler deployment gate.

    Returns dict:
        meta_labeler_enabled: bool (True only if >= min_samples)
        meta_labeler_sample_count: int
        meta_labeler_min_samples: int (1000)
    """
    sample_count = _count_closed_trades(wal_dir)
    enabled = sample_count >= min_samples

    config = {
        "meta_labeler_enabled": enabled,
        "meta_labeler_sample_count": sample_count,
        "meta_labeler_min_samples": min_samples,
    }
    log.info(f"SC-21 Meta-labeler gate: {sample_count}/{min_samples} samples, enabled={enabled}")
    return config


# ---------------------------------------------------------------------------
# Aggregated config for config_writer integration
# ---------------------------------------------------------------------------
def generate_all_signal_filter_configs(
    wal_dir: Path = WAL_DIR,
) -> dict:
    """Generate all signal filter configurations for config_writer.

    Returns combined dict with all filter params. config_writer.py
    emits these into [signal_filters] section of dynamic_weights.toml.
    """
    configs = {}
    configs.update(compute_cusum_ewma_config(wal_dir))
    configs.update(compute_vpin_reset_config())
    configs.update(compute_kelly_scaling(wal_dir))
    configs.update(compute_meta_labeler_gate(wal_dir))
    return configs


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Signal filter configuration generator")
    parser.add_argument("--wal-dir", type=Path, default=WAL_DIR)
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    configs = generate_all_signal_filter_configs(args.wal_dir)

    if args.json:
        # Convert non-serializable types
        serializable = {}
        for k, v in configs.items():
            if isinstance(v, dict):
                serializable[k] = v
            else:
                serializable[k] = v
        print(json.dumps(serializable, indent=2))
    else:
        for k, v in sorted(configs.items()):
            print(f"  {k} = {v}")


if __name__ == "__main__":
    main()
