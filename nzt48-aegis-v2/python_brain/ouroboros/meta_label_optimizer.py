"""Meta-Label F1-Optimal Threshold Optimizer — Ouroboros analytics module.

P1-14: Compute F1-optimal probability threshold per ticker from PR curve analysis.
The flat 0.55 threshold ignores class imbalance. This module:
  1. Loads WAL PositionClosed + SignalRejected events (30-day lookback)
  2. Builds binary labels: confidence → win/loss outcome
  3. Computes precision, recall, F1 at thresholds 0.40-0.90 (step 0.01)
  4. Selects threshold maximizing F1 per ticker
  5. Falls back to 0.55 if < 20 trades for a ticker

Usage: python3 -m python_brain.ouroboros.meta_label_optimizer
Cron: Runs nightly after nightly_v6 (04:53 UTC)

Quarantine rules:
  - Read-only: reads WAL events only, never places orders
  - Output: writes meta_label_thresholds.toml for engine consumption
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MetaLabel] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("meta_label_optimizer")

DEFAULT_THRESHOLD = 0.55
MIN_TRADES_FOR_OPTIMIZATION = 20
THRESHOLD_RANGE = np.arange(0.40, 0.91, 0.01)
LOOKBACK_DAYS = 30


def load_trade_outcomes(lookback_days: int = LOOKBACK_DAYS) -> Dict[str, List[Tuple[float, bool]]]:
    """Load (confidence, is_win) pairs per ticker from WAL.

    Returns: dict of ticker_symbol -> [(confidence, is_win), ...]
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    cutoff_ns = int(cutoff.timestamp() * 1e9)

    outcomes: Dict[str, List[Tuple[float, bool]]] = defaultdict(list)

    # Scan all WAL files
    wal_files = []
    if WAL_DIR.exists():
        wal_files.append(WAL_DIR / "current.ndjson")
        archive_dir = WAL_DIR / "archive"
        if archive_dir.exists():
            wal_files.extend(sorted(archive_dir.glob("*.ndjson")))

    for wal_path in wal_files:
        if not wal_path.exists():
            continue
        try:
            with open(wal_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event_time_ns = event.get("event_time_ns", 0)
                    if event_time_ns < cutoff_ns:
                        continue

                    payload = event.get("payload", {})
                    if "PositionClosed" not in payload:
                        continue

                    pc = payload["PositionClosed"]
                    symbol = pc.get("symbol", "")
                    confidence = pc.get("confidence", 0.0)
                    pnl = pc.get("final_pnl", 0.0)

                    if symbol and confidence > 0:
                        outcomes[symbol].append((confidence, pnl > 0))
        except Exception as e:
            log.warning("Error reading %s: %s", wal_path, e)

    total = sum(len(v) for v in outcomes.values())
    log.info("Loaded %d trade outcomes across %d tickers (%d-day lookback)",
             total, len(outcomes), lookback_days)
    return outcomes


def compute_f1_optimal_threshold(
    data: List[Tuple[float, bool]],
) -> Tuple[float, Dict[str, Any]]:
    """Compute F1-optimal threshold from (confidence, is_win) pairs.

    Returns: (optimal_threshold, stats_dict)
    """
    if len(data) < MIN_TRADES_FOR_OPTIMIZATION:
        return DEFAULT_THRESHOLD, {
            "n_trades": len(data),
            "method": "default",
            "reason": f"insufficient data (<{MIN_TRADES_FOR_OPTIMIZATION})",
        }

    confidences = np.array([d[0] for d in data])
    labels = np.array([d[1] for d in data], dtype=bool)

    best_f1 = 0.0
    best_threshold = DEFAULT_THRESHOLD
    best_stats = {}

    for threshold in THRESHOLD_RANGE:
        predictions = confidences >= threshold

        tp = np.sum(predictions & labels)
        fp = np.sum(predictions & ~labels)
        fn = np.sum(~predictions & labels)

        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-9)

        if f1 > best_f1:
            best_f1 = f1
            best_threshold = float(round(threshold, 2))
            best_stats = {
                "n_trades": len(data),
                "method": "f1_optimal",
                "threshold": best_threshold,
                "f1": round(float(f1), 4),
                "precision": round(float(precision), 4),
                "recall": round(float(recall), 4),
                "tp": int(tp),
                "fp": int(fp),
                "fn": int(fn),
                "win_rate": round(float(np.mean(labels)), 4),
            }

    # Sanity: don't go below 0.40 or above 0.85
    best_threshold = max(0.40, min(0.85, best_threshold))

    return best_threshold, best_stats


def generate_thresholds_toml(
    thresholds: Dict[str, Tuple[float, Dict[str, Any]]],
) -> str:
    """Generate meta_label_thresholds.toml content."""
    lines = [
        "# Meta-Label F1-Optimal Thresholds — auto-generated by meta_label_optimizer.py",
        f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"# Default threshold: {DEFAULT_THRESHOLD} (used when per-ticker data insufficient)",
        "",
        f"default_threshold = {DEFAULT_THRESHOLD}",
        "",
    ]

    for symbol, (threshold, stats) in sorted(thresholds.items()):
        lines.append(f"[{symbol.replace('.', '_')}]")
        lines.append(f'symbol = "{symbol}"')
        lines.append(f"threshold = {threshold}")
        lines.append(f'method = "{stats.get("method", "default")}"')
        lines.append(f"n_trades = {stats.get('n_trades', 0)}")
        if "f1" in stats:
            lines.append(f"f1 = {stats['f1']}")
            lines.append(f"precision = {stats['precision']}")
            lines.append(f"recall = {stats['recall']}")
        lines.append("")

    return "\n".join(lines)


def run_optimizer() -> int:
    """Execute the meta-label threshold optimizer."""
    log.info("=" * 60)
    log.info("Meta-Label F1-Optimal Threshold Optimizer")
    log.info("=" * 60)

    # Step 1: Load trade outcomes
    outcomes = load_trade_outcomes()
    if not outcomes:
        log.warning("No trade outcomes found. Using default threshold for all tickers.")
        return 0

    # Step 2: Compute optimal threshold per ticker
    thresholds: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    for symbol, data in sorted(outcomes.items()):
        threshold, stats = compute_f1_optimal_threshold(data)
        thresholds[symbol] = (threshold, stats)

        if stats.get("method") == "f1_optimal":
            log.info("  %s: threshold=%.2f (F1=%.3f, P=%.3f, R=%.3f, n=%d)",
                     symbol, threshold, stats["f1"], stats["precision"],
                     stats["recall"], stats["n_trades"])
        else:
            log.info("  %s: threshold=%.2f (default — %s)",
                     symbol, threshold, stats.get("reason", ""))

    # Step 3: Write TOML
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    toml_path = CONFIG_DIR / "meta_label_thresholds.toml"
    content = generate_thresholds_toml(thresholds)

    tmp_path = toml_path.with_suffix(".toml.tmp")
    tmp_path.write_text(content)
    os.rename(str(tmp_path), str(toml_path))

    log.info("Wrote %d ticker thresholds to %s", len(thresholds), toml_path)

    # Step 4: Write JSON sidecar for analytics
    json_path = DATA_DIR / "ouroboros_reports" / f"meta_label_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_data = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "default_threshold": DEFAULT_THRESHOLD,
        "tickers": {
            sym: {"threshold": t, **s} for sym, (t, s) in thresholds.items()
        },
    }
    json_path.write_text(json.dumps(json_data, indent=2))

    log.info("=" * 60)
    return 0


def main():
    try:
        sys.exit(run_optimizer())
    except Exception as e:
        log.error("Meta-label optimizer crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
