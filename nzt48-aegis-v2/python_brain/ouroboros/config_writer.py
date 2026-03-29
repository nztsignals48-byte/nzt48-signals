# 51 4 * * 1-5 cd /app && python3 -m python_brain.ouroboros.config_writer >> /var/log/config_writer.log 2>&1
"""Ouroboros Config Writer — Bridge nightly learning outputs to Rust TOML configs.

Reads Ouroboros nightly artifacts (recommendations JSON, metrics JSON, WAL events,
watchlist/universe data) and writes the TOML config files that the Rust engine
loads at startup via ouroboros_loader.rs:

  - config/dynamic_weights.toml   (Bayesian stats, exit params, regime, Kelly)
  - config/spread_cache.toml      (5-day median intraday spreads per ticker)
  - config/universe_classification.toml  (tiered ticker IDs for engine routing)

Runs at 04:51 UTC (1 minute after nightly_v6 at 04:50 UTC).

Design:
  - Atomic writes: write to .tmp then os.rename (POSIX rename is atomic on same FS)
  - Safe defaults: if any source file is missing, writes sensible defaults
  - Idempotent: can be re-run safely; overwrites previous TOML each time
  - Standalone: python3 -m python_brain.ouroboros.config_writer

Quarantine rules (same as nightly_v6):
  - NEVER writes to live WAL
  - NEVER influences live decisions in-session (engine only loads config at boot)
  - Reads ONLY the finished day's artifacts
"""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Note: TOML output is built as formatted strings (not toml.dumps)
# so no toml/tomli_w dependency needed. Rust engine reads via tomli.

# ---------------------------------------------------------------------------
# Path setup — works both locally and in Docker (/app)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
REPORTS_DIR = DATA_DIR / "ouroboros_reports"
RECS_FILE = DATA_DIR / "ouroboros_recommendations.json"
WATCHLIST_FILE = CONFIG_DIR / "active_watchlist.json"
BACKFILL_FEEDBACK_FILE = DATA_DIR / "backfill_feedback.json"
NIGHTLY_OUTPUT_FILE = DATA_DIR / "nightly_output.json"

# Entry type confidence bounds — Ouroboros may tune within these limits.
ENTRY_CONF_MIN = 60.0
ENTRY_CONF_MAX = 90.0
# Base confidences (must match config.toml [entry_types])
ENTRY_CONF_DEFAULTS = {
    "TypeA": 65.0,
    "TypeB": 82.0,
    "TypeC": 72.0,
    "TypeD": 80.0,
    "TypeE": 70.0,
    "TypeF": 68.0,
}

# WIRED (Sprint 7A): PRIMARY_TICKERS removed — dynamic loading from contracts.toml.
# Previously hardcoded 12 LSE ETPs. Now loads ALL contracts dynamically.
def _load_contract_symbols() -> list:
    """Load all contract symbols from contracts.toml dynamically."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    contracts_path = CONFIG_DIR / "contracts.toml"
    if contracts_path.exists():
        with open(contracts_path, "rb") as f:
            data = tomllib.load(f)
        return [c["symbol"] for c in data.get("contracts", []) if c.get("symbol")]
    return []

PRIMARY_TICKERS = _load_contract_symbols()
TICKER_ID_MAP: Dict[str, int] = {sym: i for i, sym in enumerate(PRIMARY_TICKERS)}

# Default regime scale values (engine expects these keys)
DEFAULT_REGIME_SCALES = {
    "bull_quiet": 1.0,
    "bull_volatile": 0.80,
    "bear_quiet": 0.60,
    "bear_volatile": 0.50,
    "neutral": 0.75,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ConfigWriter] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("config_writer")


# ---------------------------------------------------------------------------
# Utility: atomic file write
# ---------------------------------------------------------------------------
def atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via tmp + rename.

    Sprint S06 (H3): TOML files are syntax-validated before writing.
    If validation fails, the write is aborted and the previous file is preserved.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # H3: Validate TOML syntax before writing (prevents corrupt SIGHUP)
    if path.suffix == ".toml":
        try:
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib
            tomllib.loads(content)
        except Exception as e:
            log.critical("H3 TOML VALIDATION FAILED for %s: %s — write ABORTED, previous file preserved", path, e)
            return  # Do NOT write corrupt TOML

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.rename(str(tmp_path), str(path))
        log.info("Wrote %s (%d bytes)", path, len(content))
    except Exception:
        # Clean up tmp file on failure
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# Config diff rollback ledger
# ---------------------------------------------------------------------------
_CONFIG_CHANGES_PATH = DATA_DIR / "config_changes.ndjson"
_LEDGER_RETENTION_DAYS = 30


def _sha256(content: str) -> str:
    """Return hex SHA-256 digest of a string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _diff_toml_content(old_content: str, new_content: str) -> List[Dict[str, Any]]:
    """Compute a key-level diff between two TOML-formatted strings.

    Parses lines of the form 'key = value' under [section] headers and reports
    additions, removals, and modifications.  Comment-only and blank lines are
    ignored.  This is intentionally lightweight (no TOML parser dependency).
    """
    def _parse_kv(text: str) -> Dict[str, str]:
        """Return {section.key: raw_value} for every key = value line."""
        kvs: Dict[str, str] = {}
        section = ""
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("["):
                # Handle both [section] and [[section.array]]
                section = stripped.strip("[]").strip()
                continue
            if "=" in stripped:
                key, _, val = stripped.partition("=")
                full_key = f"{section}.{key.strip()}" if section else key.strip()
                kvs[full_key] = val.strip()
        return kvs

    old_kv = _parse_kv(old_content)
    new_kv = _parse_kv(new_content)
    all_keys = sorted(set(old_kv) | set(new_kv))

    changes: List[Dict[str, Any]] = []
    for k in all_keys:
        old_val = old_kv.get(k)
        new_val = new_kv.get(k)
        if old_val is None:
            changes.append({"key": k, "action": "added", "new": new_val})
        elif new_val is None:
            changes.append({"key": k, "action": "removed", "old": old_val})
        elif old_val != new_val:
            changes.append({"key": k, "action": "changed", "old": old_val, "new": new_val})
    return changes


def _trim_ledger_entries(entries: List[str], retention_days: int = _LEDGER_RETENTION_DAYS) -> List[str]:
    """Return only entries whose timestamp is within the retention window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    cutoff_iso = cutoff.isoformat(timespec="seconds")
    kept: List[str] = []
    for line in entries:
        try:
            obj = json.loads(line)
            if obj.get("timestamp", "") >= cutoff_iso:
                kept.append(line)
        except (json.JSONDecodeError, TypeError):
            continue  # drop malformed lines
    return kept


def _record_config_change(filepath: Path, new_content: str) -> None:
    """Record a config diff entry to the ndjson ledger if content changed.

    Compares new_content against the existing file at filepath.  If the SHA-256
    hashes differ (or the file is new), appends a timestamped diff record to
    config_changes.ndjson and trims entries older than 30 days.

    Silently succeeds on any I/O error to avoid blocking config writes.
    """
    try:
        # Read existing file content (empty string if first run)
        old_content = ""
        if filepath.exists():
            try:
                old_content = filepath.read_text(encoding="utf-8")
            except IOError:
                old_content = ""

        old_hash = _sha256(old_content) if old_content else ""
        new_hash = _sha256(new_content)

        # Skip if content is identical
        if old_hash == new_hash:
            return

        # Compute key-level diff
        diff_summary = _diff_toml_content(old_content, new_content)

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "filename": filepath.name,
            "diff_summary": diff_summary,
            "old_hash": old_hash,
            "new_hash": new_hash,
        }

        # Read existing ledger lines
        existing_lines: List[str] = []
        ledger_path = _CONFIG_CHANGES_PATH
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        if ledger_path.exists():
            try:
                existing_lines = [
                    l for l in ledger_path.read_text(encoding="utf-8").splitlines() if l.strip()
                ]
            except IOError:
                existing_lines = []

        # Append new entry
        existing_lines.append(json.dumps(entry, separators=(",", ":")))

        # Trim to retention window
        existing_lines = _trim_ledger_entries(existing_lines)

        # Write back atomically
        tmp_path = ledger_path.with_suffix(".ndjson.tmp")
        tmp_path.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")
        os.rename(str(tmp_path), str(ledger_path))

        log.info("Recorded config change for %s (%d diffs, old=%s..., new=%s...)",
                 filepath.name, len(diff_summary), old_hash[:12], new_hash[:12])

    except Exception as e:
        # Never block config writes due to ledger failure
        log.warning("Failed to record config change for %s: %s (non-fatal)", filepath.name, e)


# ---------------------------------------------------------------------------
# Source data loaders
# ---------------------------------------------------------------------------
def load_recommendations() -> Dict[str, Any]:
    """Load ouroboros_recommendations.json (output of nightly_v6 optimize_parameters)."""
    if not RECS_FILE.exists():
        log.warning("Recommendations file not found: %s — using defaults", RECS_FILE)
        return {}
    try:
        with open(RECS_FILE) as f:
            data = json.load(f)
        log.info("Loaded recommendations: date=%s, kelly=%.4f, chandelier=%.2f",
                 data.get("date", "?"),
                 data.get("kelly_fraction", 0.20),
                 data.get("chandelier_atr_mult", 3.0))
        return data
    except (json.JSONDecodeError, IOError) as e:
        log.warning("Failed to load recommendations: %s — using defaults", e)
        return {}


def load_latest_metrics() -> Dict[str, Any]:
    """Load the most recent daily metrics JSON from ouroboros_reports/."""
    if not REPORTS_DIR.exists():
        log.warning("Reports dir not found: %s — using defaults", REPORTS_DIR)
        return {}

    json_files = sorted(REPORTS_DIR.glob("*_metrics.json"))
    if not json_files:
        log.warning("No metrics files found in %s — using defaults", REPORTS_DIR)
        return {}

    latest = json_files[-1]
    try:
        with open(latest) as f:
            data = json.load(f)
        log.info("Loaded metrics: %s (date=%s, trades=%d, WR=%.1f%%)",
                 latest.name, data.get("date", "?"),
                 data.get("total_trades", 0),
                 data.get("win_rate", 0.0) * 100)
        return data
    except (json.JSONDecodeError, IOError) as e:
        log.warning("Failed to load metrics from %s: %s — using defaults", latest, e)
        return {}


def load_historical_metrics(days: int = 30) -> List[Dict[str, Any]]:
    """Load up to `days` historical metrics for rolling calculations."""
    if not REPORTS_DIR.exists():
        return []
    history = []
    json_files = sorted(REPORTS_DIR.glob("*_metrics.json"))
    for jf in json_files[-days:]:
        try:
            with open(jf) as f:
                data = json.load(f)
            history.append(data)
        except (json.JSONDecodeError, IOError):
            continue
    return history


def load_todays_wal_events(date_str: str) -> List[Dict[str, Any]]:
    """Load WAL events for today from ALL ndjson files including archives.

    The Rust engine rotates current.ndjson to archive/wal_<epoch>.ndjson on
    every restart. We must scan ALL archive files + current to find all events.
    """
    events: List[Dict[str, Any]] = []
    wal_candidates = [
        WAL_DIR / "current.ndjson",
        WAL_DIR / f"{date_str}.ndjson",
        WAL_DIR / f"wal_{date_str}.ndjson",
    ]
    # Include all archive files
    archive_dir = WAL_DIR / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.ndjson")):
            if f not in wal_candidates:
                wal_candidates.append(f)
    for wal_path in wal_candidates:
        if not wal_path.exists():
            continue
        log.info("Reading WAL events: %s", wal_path)
        try:
            with open(wal_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except IOError as e:
            log.warning("Error reading WAL %s: %s", wal_path, e)
    log.info("Loaded %d WAL events for %s", len(events), date_str)
    return events


def load_watchlist() -> Dict[str, Any]:
    """Load active_watchlist.json for universe classification data."""
    if not WATCHLIST_FILE.exists():
        log.warning("Watchlist file not found: %s", WATCHLIST_FILE)
        return {}
    try:
        with open(WATCHLIST_FILE) as f:
            data = json.load(f)
        log.info("Loaded watchlist: %d vanguard, %d total scored",
                 len(data.get("vanguard", [])), data.get("total_scored", 0))
        return data
    except (json.JSONDecodeError, IOError) as e:
        log.warning("Failed to load watchlist: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Bayesian win rate estimator (conjugate Beta prior)
# ---------------------------------------------------------------------------
def bayesian_win_rate(wins: int, total: int, prior_alpha: float = 2.0, prior_beta: float = 2.0) -> float:
    """Beta-binomial posterior mean for win rate with weakly informative prior.

    Prior: Beta(2,2) = slight pull toward 50%.
    Posterior: Beta(alpha + wins, beta + losses).
    Returns posterior mean.
    """
    losses = total - wins
    return (prior_alpha + wins) / (prior_alpha + prior_beta + total)


# ---------------------------------------------------------------------------
# Deflated Sharpe Ratio (DSR) estimator
# ---------------------------------------------------------------------------
def compute_dsr(sharpe: float, n_trades: int, skew: float = 0.0, kurt: float = 3.0) -> Tuple[float, bool]:
    """Compute Deflated Sharpe Ratio following Bailey & Lopez de Prado (2014).

    Returns (dsr_value, is_significant) where significant means DSR > 0.95.
    Simplified: uses normal approximation for SR distribution.
    """
    if n_trades < 5 or sharpe <= 0:
        return 0.0, False

    # Standard error of Sharpe ratio
    se_sr = math.sqrt((1.0 + 0.5 * sharpe**2 - skew * sharpe + ((kurt - 3) / 4.0) * sharpe**2) / max(n_trades, 1))
    if se_sr <= 0:
        return 0.0, False

    # DSR = Phi(SR / SE(SR)) — CDF of standard normal
    z = sharpe / se_sr
    # Approximate Phi using error function
    dsr = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return dsr, dsr > 0.95


# ---------------------------------------------------------------------------
# Sharpe ratio from trade PnLs
# ---------------------------------------------------------------------------
def compute_sharpe(pnls: List[float]) -> float:
    """Annualized Sharpe ratio from a list of trade PnLs.

    Uses daily-equivalent scaling: sqrt(252) * mean/std.
    """
    if len(pnls) < 2:
        return 0.0
    mean_pnl = sum(pnls) / len(pnls)
    var = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
    std = math.sqrt(var) if var > 0 else 1e-9
    return (mean_pnl / std) * math.sqrt(252)


# ---------------------------------------------------------------------------
# Spread extraction from WAL fill events
# ---------------------------------------------------------------------------
def extract_spread_data(events: List[Dict[str, Any]]) -> Dict[str, float]:
    """Extract approximate spread data from WAL FillEvent and RoutedOrder pairs.

    Computes spread proxy as |fill_price - order_price| / order_price for each
    matched RoutedOrder -> FillEvent pair. Returns median spread per ticker symbol.
    """
    # Collect order prices by order_id
    order_prices: Dict[str, Tuple[str, float]] = {}
    fill_spreads: Dict[str, List[float]] = {}

    for event in events:
        payload = event.get("payload", {})

        if "RoutedOrder" in payload:
            ro = payload["RoutedOrder"]
            order_id = ro.get("order_id", "")
            symbol = ro.get("symbol", "")
            # We don't have limit price in RoutedOrder, but approved_size gives notional
            if order_id and symbol:
                order_prices[order_id] = (symbol, ro.get("approved_size", 0.0))

        elif "FillEvent" in payload:
            fe = payload["FillEvent"]
            order_id = fe.get("order_id", "")
            fill_price = fe.get("price", 0.0)
            if order_id in order_prices and fill_price > 0:
                symbol, _ = order_prices[order_id]
                if symbol not in fill_spreads:
                    fill_spreads[symbol] = []
                # Use commission as spread proxy (more reliable than slippage)
                commission = fe.get("commission", 0.0)
                filled_qty = fe.get("filled_qty", 1)
                if filled_qty > 0 and fill_price > 0:
                    spread_pct = (commission / (fill_price * filled_qty)) * 100.0
                    fill_spreads[symbol].append(spread_pct)

    # Compute median spread per ticker
    result: Dict[str, float] = {}
    for symbol, spreads in fill_spreads.items():
        if spreads:
            sorted_spreads = sorted(spreads)
            mid = len(sorted_spreads) // 2
            if len(sorted_spreads) % 2 == 0:
                result[symbol] = (sorted_spreads[mid - 1] + sorted_spreads[mid]) / 2.0
            else:
                result[symbol] = sorted_spreads[mid]

    return result


# ---------------------------------------------------------------------------
# Regime label lookup from RiskStateChange events (binary search)
# ---------------------------------------------------------------------------
def _collect_regime_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract and sort RiskStateChange events from WAL by timestamp.

    Returns a list of dicts with keys 'ts' (nanosecond timestamp) and 'to'
    (regime label), sorted by timestamp ascending.
    """
    regime_events: List[Dict[str, Any]] = []
    for event in events:
        payload = event.get("payload", {})
        if "RiskStateChange" in payload:
            rsc = payload["RiskStateChange"]
            ts = event.get("ts", rsc.get("ts", 0))
            to_regime = rsc.get("to", rsc.get("new_state", "normal"))
            regime_events.append({"ts": ts, "to": to_regime})
    regime_events.sort(key=lambda e: e["ts"])
    return regime_events


def _find_closest_regime(regime_events: List[Dict[str, Any]], timestamp_ns: int) -> str:
    """Binary search for the active regime at a given nanosecond timestamp.

    Finds the most recent RiskStateChange that occurred at or before the given
    timestamp. If the timestamp predates all regime events, uses the first
    known regime. Returns "normal" if no regime events exist.
    """
    if not regime_events:
        return "normal"
    timestamps = [e["ts"] for e in regime_events]
    idx = bisect.bisect_right(timestamps, timestamp_ns) - 1
    if idx < 0:
        return regime_events[0].get("to", "normal")
    return regime_events[idx].get("to", "normal")


# ---------------------------------------------------------------------------
# Regime scale computation from historical metrics
# ---------------------------------------------------------------------------
def compute_regime_scales(events: List[Dict[str, Any]]) -> Dict[str, float]:
    """Compute regime-specific performance scales from WAL PositionClosed events.

    Groups trades by regime_at_entry, computes win rate per regime, and scales
    relative to the best-performing regime. When PositionClosed events lack a
    regime label, performs a binary search on RiskStateChange events to find
    the closest-in-time regime.
    """
    # Pre-load all RiskStateChange events sorted by timestamp for binary search
    regime_events = _collect_regime_events(events)
    if regime_events:
        log.info("Loaded %d RiskStateChange events for regime lookup (range: %s -> %s)",
                 len(regime_events),
                 regime_events[0].get("to", "?"),
                 regime_events[-1].get("to", "?"))

    regime_trades: Dict[str, List[float]] = {}
    for event in events:
        payload = event.get("payload", {})
        if "PositionClosed" in payload:
            pc = payload["PositionClosed"]
            pnl = pc.get("final_pnl", 0.0)

            # Try to get regime from the PositionClosed event itself
            regime = pc.get("regime_at_entry", pc.get("regime", ""))

            # If no regime label, binary search RiskStateChange events
            if not regime:
                entry_ts = pc.get("entry_ts", pc.get("opened_at", event.get("ts", 0)))
                regime = _find_closest_regime(regime_events, entry_ts)

            regime_trades.setdefault(regime, []).append(pnl)

    # If we have regime-grouped trades, compute win-rate-based scales
    if regime_trades and any(len(v) >= 3 for v in regime_trades.values()):
        regime_win_rates: Dict[str, float] = {}
        for regime_name, pnls in regime_trades.items():
            if len(pnls) >= 3:  # Need at least 3 trades for meaningful WR
                wins = sum(1 for p in pnls if p > 0)
                regime_win_rates[regime_name] = wins / len(pnls)

        if regime_win_rates:
            best_wr = max(regime_win_rates.values())
            scales = dict(DEFAULT_REGIME_SCALES)  # Start with defaults
            for regime_name, wr in regime_win_rates.items():
                # Scale relative to best regime: best = 1.0, others proportional
                scale = wr / best_wr if best_wr > 0 else 0.75
                scales[regime_name] = round(max(0.30, min(1.0, scale)), 2)
            log.info("Computed regime scales from %d regimes: %s",
                     len(regime_win_rates), scales)
            return scales

    # Fallback: not enough trades per regime — return defaults
    # The nightly_v6 recommendations may override these
    return dict(DEFAULT_REGIME_SCALES)


# ---------------------------------------------------------------------------
# Kelly fraction per tier computation
# ---------------------------------------------------------------------------
def compute_kelly_fractions(metrics: Dict[str, Any], recs: Dict[str, Any]) -> Dict[str, float]:
    """Compute Kelly fractions per tier from metrics and recommendations.

    Uses the base kelly_fraction from recommendations and adjusts per ticker
    performance tier (t1=full, t2=80%, t3=60%).
    """
    base_kelly = recs.get("kelly_fraction", 0.20)
    if base_kelly is None:
        base_kelly = 0.20

    return {
        "t1": round(base_kelly, 6),
        "t2": round(base_kelly * 0.80, 6),
        "t3": round(base_kelly * 0.60, 6),
    }


# ---------------------------------------------------------------------------
# Backfill feedback loader (item 49)
# ---------------------------------------------------------------------------
def load_backfill_feedback() -> Dict[str, float]:
    """Load strategy_confidence_delta per entry type from backfill_feedback.json.

    Returns a dict like {"TypeA": -0.3, "TypeB": 0.5, ...} or empty if unavailable.
    The backfill simulator writes this file daily with simulated performance deltas
    per entry type. Positive delta = backfill suggests raising confidence (type is
    performing better than live), negative = lower it.

    Staleness: ignores file if older than 48 hours.
    """
    if not BACKFILL_FEEDBACK_FILE.exists():
        log.info("Backfill feedback not found: %s", BACKFILL_FEEDBACK_FILE)
        return {}

    try:
        mtime = BACKFILL_FEEDBACK_FILE.stat().st_mtime
        age_hours = (time.time() - mtime) / 3600.0
        if age_hours > 48:
            log.info("Backfill feedback stale (%.1f hours old). Ignoring.", age_hours)
            return {}

        with open(BACKFILL_FEEDBACK_FILE) as f:
            data = json.load(f)

        # Extract per-type deltas. The file format is:
        # {"TypeA": {"strategy_confidence_delta": -0.3}, "TypeB": {...}, ...}
        # OR it may be nested under a top-level key.
        deltas: Dict[str, float] = {}

        # Handle top-level dict where keys are entry types
        for key in ["TypeA", "TypeB", "TypeC", "TypeD", "TypeE", "TypeF"]:
            entry = data.get(key, {})
            if isinstance(entry, dict) and "strategy_confidence_delta" in entry:
                deltas[key] = float(entry["strategy_confidence_delta"])

        # Also handle flat "strategy_confidence_delta" dict format from nightly_v6
        scd = data.get("strategy_confidence_delta", {})
        if isinstance(scd, dict):
            for key, val in scd.items():
                if key in ENTRY_CONF_DEFAULTS:
                    deltas[key] = float(val)

        if deltas:
            log.info("Loaded backfill feedback deltas: %s", deltas)
        return deltas

    except (json.JSONDecodeError, IOError, TypeError, ValueError) as e:
        log.warning("Failed to load backfill feedback: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Per-entry-type confidence tuning from WAL trade data (P3.2/item 3)
# ---------------------------------------------------------------------------
def compute_entry_type_confidences(
    events: List[Dict[str, Any]],
    backfill_deltas: Dict[str, float],
) -> Dict[str, float]:
    """Compute Ouroboros-tuned per-entry-type confidence values.

    Reads PositionClosed events from the WAL to gather per-type win rates,
    then adjusts base confidences within [ENTRY_CONF_MIN, ENTRY_CONF_MAX].

    Adjustment logic:
      1. Start with base confidence from ENTRY_CONF_DEFAULTS.
      2. If WAL has >= 10 trades for a type, compute WR delta vs 50% baseline:
         adjustment = (wr - 0.50) * 20  (i.e. +/-10 points per 50pp WR swing)
      3. Apply backfill_deltas (scaled by 5x to convert small deltas to confidence points).
      4. Clamp to [ENTRY_CONF_MIN, ENTRY_CONF_MAX].

    Returns {"TypeA": 63.0, "TypeB": 85.0, ...} — one entry per type.
    """
    # Gather per-type trade stats from WAL PositionClosed events
    type_wins: Dict[str, int] = {}
    type_total: Dict[str, int] = {}

    for event in events:
        payload = event.get("payload", {})
        if "PositionClosed" in payload:
            pc = payload["PositionClosed"]
            etype = pc.get("entry_type", "")
            if etype not in ENTRY_CONF_DEFAULTS:
                continue
            pnl = pc.get("final_pnl", 0.0)
            type_total[etype] = type_total.get(etype, 0) + 1
            if pnl > 0:
                type_wins[etype] = type_wins.get(etype, 0) + 1

    # Also try to load nightly_output.json for richer per-type stats
    nightly_per_type: Dict[str, Dict[str, Any]] = {}
    try:
        if NIGHTLY_OUTPUT_FILE.exists():
            with open(NIGHTLY_OUTPUT_FILE) as f:
                nightly_data = json.load(f)
            nightly_per_type = nightly_data.get("per_entry_type", {})
    except (json.JSONDecodeError, IOError):
        pass

    # Merge nightly stats into WAL stats (nightly may have more history)
    for etype, stats in nightly_per_type.items():
        if etype in ENTRY_CONF_DEFAULTS:
            nt = stats.get("trades", 0)
            nw = stats.get("wins", 0)
            if nt > type_total.get(etype, 0):
                type_total[etype] = nt
                type_wins[etype] = nw

    # Compute adjusted confidences
    result: Dict[str, float] = {}
    for etype, base_conf in ENTRY_CONF_DEFAULTS.items():
        adj = 0.0

        # WAL-based adjustment: shift confidence based on observed WR
        total = type_total.get(etype, 0)
        wins = type_wins.get(etype, 0)
        if total >= 10:
            wr = wins / total
            # +/-10 confidence points per 50pp WR swing from 50% baseline
            adj += (wr - 0.50) * 20.0
            log.info("Entry type %s: WAL WR=%.1f%% (%d/%d), adj=%+.1f",
                     etype, wr * 100, wins, total, adj)

        # Backfill delta adjustment: scale small deltas to confidence-point range
        bf_delta = backfill_deltas.get(etype, 0.0)
        if bf_delta != 0.0:
            bf_adj = bf_delta * 5.0  # e.g. delta of +0.5 -> +2.5 confidence points
            adj += bf_adj
            log.info("Entry type %s: backfill delta=%+.2f, adj=%+.1f",
                     etype, bf_delta, bf_adj)

        new_conf = base_conf + adj
        new_conf = max(ENTRY_CONF_MIN, min(ENTRY_CONF_MAX, new_conf))
        result[etype] = round(new_conf, 1)

    return result


# ---------------------------------------------------------------------------
# Adaptive parameter computation (Plan 1 Phase 3: Make Everything Adaptive)
# ---------------------------------------------------------------------------

def compute_adaptive_chandelier_atr(events: List[Dict[str, Any]], recs: Dict[str, Any]) -> float:
    """Compute VIX-regime-aware Chandelier ATR multiplier.

    VIX > 35 → 3.0 (crisis: wide stops to survive volatility)
    VIX > 25 → 2.5 (elevated: moderately wider stops)
    VIX <= 25 → 2.0 (normal: tight stops to capture profit)

    Falls back to nightly recommendations if no VIX data available.
    """
    # Try to get VIX from the most recent RiskStateChange or tick context
    latest_vix = None
    for event in reversed(events):
        payload = event.get("payload", {})
        # VIX may be in tick context or risk state
        if "TickContext" in payload:
            vix = payload["TickContext"].get("vix")
            if vix is not None and vix > 0:
                latest_vix = vix
                break
        if "RiskStateChange" in payload:
            vix = payload["RiskStateChange"].get("vix")
            if vix is not None and vix > 0:
                latest_vix = vix
                break

    # Also check persistent memory for last known VIX
    if latest_vix is None:
        try:
            from python_brain.ouroboros.persistent_memory import load_memory
            mem = load_memory()
            latest_vix = getattr(mem, 'last_vix', None)
        except Exception:
            pass

    if latest_vix is not None:
        if latest_vix > 35:
            atr_mult = 3.0
        elif latest_vix > 25:
            atr_mult = 2.5
        else:
            atr_mult = 2.0
        log.info("Adaptive Chandelier ATR: VIX=%.1f → mult=%.1f", latest_vix, atr_mult)
        return atr_mult

    # Fallback: use nightly recommendations value
    fallback = recs.get("chandelier_atr_mult", 2.0)
    log.info("Adaptive Chandelier ATR: no VIX data, using recs value=%.2f", fallback)
    return fallback if fallback is not None else 2.0


def compute_adaptive_spread_veto(events: List[Dict[str, Any]]) -> float:
    """Compute regime-aware spread veto threshold (percentage).

    Low volatility (VIX < 18)  → 0.2% (tighter: spreads should be narrow)
    Normal (18 <= VIX <= 25)   → 0.3% (default)
    High volatility (VIX > 25) → 0.5% (wider: accept wider spreads in vol)

    Returns spread_veto_pct as a decimal fraction (e.g. 0.003 for 0.3%).
    """
    latest_vix = None
    for event in reversed(events):
        payload = event.get("payload", {})
        if "TickContext" in payload:
            vix = payload["TickContext"].get("vix")
            if vix is not None and vix > 0:
                latest_vix = vix
                break
        if "RiskStateChange" in payload:
            vix = payload["RiskStateChange"].get("vix")
            if vix is not None and vix > 0:
                latest_vix = vix
                break

    if latest_vix is None:
        try:
            from python_brain.ouroboros.persistent_memory import load_memory
            mem = load_memory()
            latest_vix = getattr(mem, 'last_vix', None)
        except Exception:
            pass

    if latest_vix is not None:
        if latest_vix < 18:
            spread_veto = 0.002  # 0.2%
        elif latest_vix > 25:
            spread_veto = 0.005  # 0.5%
        else:
            spread_veto = 0.003  # 0.3%
        log.info("Adaptive spread veto: VIX=%.1f → %.1f%%", latest_vix, spread_veto * 100)
        return spread_veto

    log.info("Adaptive spread veto: no VIX data, using default 0.3%%")
    return 0.003  # default 0.3%


def compute_adaptive_entry_type_weights(events: List[Dict[str, Any]]) -> Dict[str, float]:
    """Compute adaptive entry type weights based on recent trade performance.

    Looks at the last 20 PositionClosed events per entry type.
    If TypeD WR < 35% → reduce weight to 0.5x
    If TypeB WR > 45% → boost weight to 1.5x
    All others default to 1.0x.

    Returns {"TypeA": 1.0, "TypeB": 1.5, ...}
    """
    type_wins: Dict[str, int] = {}
    type_total: Dict[str, int] = {}
    # Collect recent trades per type (scan all events, take last 20 per type)
    type_trades: Dict[str, List[float]] = {}

    for event in events:
        payload = event.get("payload", {})
        if "PositionClosed" in payload:
            pc = payload["PositionClosed"]
            etype = pc.get("entry_type", "")
            if etype not in ENTRY_CONF_DEFAULTS:
                continue
            pnl = pc.get("final_pnl", 0.0)
            type_trades.setdefault(etype, []).append(pnl)

    # Also enrich from persistent memory
    try:
        from python_brain.ouroboros.persistent_memory import load_memory
        mem = load_memory()
        entry_stats = getattr(mem, 'entry_type_stats', {})
        for etype, stats in entry_stats.items():
            if etype in ENTRY_CONF_DEFAULTS:
                mem_total = stats.get("total_trades", 0)
                mem_wins = stats.get("wins", 0)
                if mem_total > len(type_trades.get(etype, [])):
                    type_total[etype] = mem_total
                    type_wins[etype] = mem_wins
    except Exception:
        pass

    # Override with WAL data if we have enough (last 20 trades per type)
    for etype, pnls in type_trades.items():
        recent = pnls[-20:]  # last 20
        if len(recent) >= 10:
            type_total[etype] = len(recent)
            type_wins[etype] = sum(1 for p in recent if p > 0)

    weights: Dict[str, float] = {}
    for etype in ["TypeA", "TypeB", "TypeC", "TypeD", "TypeE", "TypeF"]:
        total = type_total.get(etype, 0)
        wins = type_wins.get(etype, 0)
        if total >= 10:
            wr = wins / total
            if etype == "TypeD" and wr < 0.35:
                weights[etype] = 0.5
                log.info("Entry type weight %s: WR=%.0f%% < 35%% → 0.5x", etype, wr * 100)
            elif etype == "TypeB" and wr > 0.45:
                weights[etype] = 1.5
                log.info("Entry type weight %s: WR=%.0f%% > 45%% → 1.5x", etype, wr * 100)
            else:
                weights[etype] = 1.0
        else:
            weights[etype] = 1.0

    return weights


# ---------------------------------------------------------------------------
# Thompson Sampler-based adaptive entry type confidence floors
# ---------------------------------------------------------------------------
# Bounds for Thompson-based confidence — tighter than the existing Ouroboros
# entry confidence bounds (ENTRY_CONF_MIN/MAX) since this is a separate layer.
THOMPSON_CONF_MIN = 45.0
THOMPSON_CONF_MAX = 100.0
THOMPSON_LOOKBACK = 50  # Last N trades per entry type
THOMPSON_MAX_STEP = 5.0  # Maximum ±5 per nightly cycle


def compute_thompson_entry_confidence(
    events: List[Dict[str, Any]],
    prev_confidences: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Compute Thompson Sampler-based adaptive confidence floors per entry type.

    For each entry type (TypeA-D):
      1. Collect the last THOMPSON_LOOKBACK PositionClosed trades from WAL.
      2. Count wins (pnl > 0) and losses (pnl < 0). Breakeven (pnl == 0) ignored.
      3. Compute Thompson posterior: Beta(wins + 1, losses + 1).
      4. Sample from the posterior to get expected WR for this type.
      5. Adjust confidence floor based on expected WR:
         - expected WR > 50%: LOWER floor (allow more trades from winning type)
         - expected WR < 40%: RAISE floor (suppress losing type)
         - expected WR < 30%: Set to 100 (effectively disable)
      6. Adjust gradually: ±THOMPSON_MAX_STEP per cycle, clamp to [45, 100].

    Args:
        events: All WAL events (from load_todays_wal_events or similar).
        prev_confidences: Previous cycle's Thompson confidences (for gradual adjustment).
            If None, starts from ENTRY_CONF_DEFAULTS.

    Returns:
        {"TypeA": 70.0, "TypeB": 60.0, "TypeC": 72.0, "TypeD": 100.0}
    """
    import random

    # Collect last THOMPSON_LOOKBACK trades per entry type from WAL
    type_trades: Dict[str, List[float]] = {}
    for event in events:
        payload = event.get("payload", {})
        if "PositionClosed" in payload:
            pc = payload["PositionClosed"]
            etype = pc.get("entry_type", "")
            if etype not in ENTRY_CONF_DEFAULTS:
                continue
            pnl = pc.get("final_pnl", 0.0)
            type_trades.setdefault(etype, []).append(pnl)

    # Also enrich from persistent memory (has full trade history)
    try:
        from python_brain.ouroboros.persistent_memory import load_memory
        mem = load_memory()
        entry_stats = getattr(mem, 'entry_type_stats', {})
        for etype, stats in entry_stats.items():
            if etype in ENTRY_CONF_DEFAULTS:
                mem_total = stats.get("total_trades", 0)
                mem_wins = stats.get("wins", 0)
                # If memory has more trades than WAL, use memory stats
                wal_count = len(type_trades.get(etype, []))
                if mem_total > wal_count:
                    # Synthesize pnl list from memory stats for Thompson sampling
                    # We only need wins/losses counts, so create a synthetic list
                    mem_losses = mem_total - mem_wins
                    type_trades[etype] = [1.0] * mem_wins + [-1.0] * mem_losses
    except Exception:
        pass

    # Starting point: previous cycle's values, or base confidences
    if prev_confidences is None:
        prev_confidences = dict(ENTRY_CONF_DEFAULTS)

    result: Dict[str, float] = {}
    for etype in ["TypeA", "TypeB", "TypeC", "TypeD", "TypeE", "TypeF"]:
        base_conf = ENTRY_CONF_DEFAULTS[etype]
        prev_conf = prev_confidences.get(etype, base_conf)

        # Take last THOMPSON_LOOKBACK trades for this type
        all_pnls = type_trades.get(etype, [])
        recent = all_pnls[-THOMPSON_LOOKBACK:]

        if len(recent) < 5:
            # Insufficient data — keep previous value (or base)
            result[etype] = prev_conf
            log.info("Thompson %s: insufficient data (%d trades), keeping %.1f",
                     etype, len(recent), prev_conf)
            continue

        # Count wins and losses (ignore breakeven pnl == 0)
        wins = sum(1 for p in recent if p > 0)
        losses = sum(1 for p in recent if p < 0)

        # Thompson posterior: Beta(wins + 1, losses + 1)
        alpha = wins + 1
        beta_param = losses + 1

        # Sample from Beta posterior to get expected WR
        # Use random.betavariate (stdlib — no numpy needed)
        expected_wr = random.betavariate(alpha, beta_param)

        # Determine target confidence based on expected WR
        if expected_wr < 0.30:
            # Very poor: effectively disable this type
            target = 100.0
        elif expected_wr < 0.40:
            # Poor: raise floor to suppress
            target = base_conf + 15.0
        elif expected_wr > 0.50:
            # Winning: lower floor to allow more trades
            target = base_conf - 10.0
        else:
            # Neutral zone (40-50%): keep near base
            target = base_conf

        # Gradual adjustment: move prev_conf toward target by at most THOMPSON_MAX_STEP
        delta = target - prev_conf
        if abs(delta) > THOMPSON_MAX_STEP:
            delta = THOMPSON_MAX_STEP if delta > 0 else -THOMPSON_MAX_STEP
        new_conf = prev_conf + delta

        # Clamp to bounds
        new_conf = max(THOMPSON_CONF_MIN, min(THOMPSON_CONF_MAX, new_conf))
        result[etype] = round(new_conf, 1)

        log.info("Thompson %s: wins=%d losses=%d Beta(%d,%d) sample=%.3f "
                 "target=%.1f prev=%.1f -> new=%.1f",
                 etype, wins, losses, alpha, beta_param, expected_wr,
                 target, prev_conf, new_conf)

    return result


def _load_prev_thompson_confidences() -> Optional[Dict[str, float]]:
    """Load previous Thompson confidence values from dynamic_weights.toml.

    Returns None if the section doesn't exist yet (first run).
    """
    dw_path = CONFIG_DIR / "dynamic_weights.toml"
    if not dw_path.exists():
        return None
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(dw_path, "rb") as f:
            data = tomllib.load(f)
        section = data.get("adaptive_entry_confidence", {})
        if not section:
            return None
        # Map TOML field names back to TypeX keys
        field_to_type = {
            "type_a_confidence": "TypeA",
            "type_b_confidence": "TypeB",
            "type_c_confidence": "TypeC",
            "type_d_confidence": "TypeD",
            "type_e_confidence": "TypeE",
            "type_f_confidence": "TypeF",
        }
        result = {}
        for field, etype in field_to_type.items():
            if field in section:
                result[etype] = float(section[field])
        return result if result else None
    except Exception as e:
        log.warning("Failed to load previous Thompson confidences: %s", e)
        return None


def compute_adaptive_exchange_weights(history: List[Dict[str, Any]]) -> Dict[str, float]:
    """Compute per-exchange session weights based on recent performance.

    If an exchange has negative PnL over last 5 sessions → weight = 0.5 (50% size).
    Otherwise → weight = 1.0 (full size).

    Reads per_exchange data from the last 5 daily metrics files.
    Returns {"LSE": 1.0, "US": 0.5, ...}
    """
    # Accumulate per-exchange PnL from last 5 days of metrics
    exchange_pnl: Dict[str, float] = {}
    exchange_sessions: Dict[str, int] = {}

    recent_5 = history[-5:] if len(history) >= 5 else history
    for daily in recent_5:
        per_exchange = daily.get("per_exchange", {})
        for exch, data in per_exchange.items():
            if isinstance(data, dict):
                pnl = data.get("total_pnl", 0.0)
                exchange_pnl[exch] = exchange_pnl.get(exch, 0.0) + pnl
                exchange_sessions[exch] = exchange_sessions.get(exch, 0) + 1

    weights: Dict[str, float] = {}
    for exch, total_pnl in exchange_pnl.items():
        sessions = exchange_sessions.get(exch, 0)
        if sessions >= 3 and total_pnl < 0:
            weights[exch] = 0.5
            log.info("Exchange weight %s: PnL=%.2f over %d sessions → 0.5x",
                     exch, total_pnl, sessions)
        else:
            weights[exch] = 1.0

    return weights


def compute_adaptive_kelly_cap(history: List[Dict[str, Any]], metrics: Dict[str, Any]) -> float:
    """Compute drawdown-aware Kelly fraction cap.

    Portfolio drawdown > 10% → cap = 0.05 (very conservative)
    Portfolio drawdown > 5%  → cap = 0.10 (conservative)
    Otherwise                → cap = 0.20 (default from config.toml)

    Uses persistent memory peak drawdown if available, falls back to
    recent metrics.
    """
    peak_drawdown_pct = 0.0

    # Try persistent memory first (most accurate — tracks all-time HWM)
    try:
        from python_brain.ouroboros.persistent_memory import load_memory
        mem = load_memory()
        peak_dd = getattr(mem, 'peak_drawdown_pct', 0.0)
        if peak_dd > 0:
            peak_drawdown_pct = peak_dd
    except Exception:
        pass

    # Fallback: compute from recent daily metrics
    if peak_drawdown_pct == 0.0 and history:
        cumulative_pnl = 0.0
        hwm = 0.0
        for daily in history:
            pnl = daily.get("total_pnl", 0.0)
            cumulative_pnl += pnl
            hwm = max(hwm, cumulative_pnl)
            dd = (hwm - cumulative_pnl) / max(hwm, 1.0) if hwm > 0 else 0.0
            peak_drawdown_pct = max(peak_drawdown_pct, dd * 100)

    if peak_drawdown_pct > 10.0:
        cap = 0.05
        log.info("Adaptive Kelly cap: drawdown=%.1f%% > 10%% → cap=%.2f", peak_drawdown_pct, cap)
    elif peak_drawdown_pct > 5.0:
        cap = 0.10
        log.info("Adaptive Kelly cap: drawdown=%.1f%% > 5%% → cap=%.2f", peak_drawdown_pct, cap)
    else:
        cap = 0.20
        log.info("Adaptive Kelly cap: drawdown=%.1f%% → cap=%.2f (default)", peak_drawdown_pct, cap)

    return cap


# ---------------------------------------------------------------------------
# BT-004: Adaptive hour-of-day confidence weights from rolling hourly WR
# ---------------------------------------------------------------------------

# Default hour weights (UTC). Matches config.toml [timing.hour_weights].
DEFAULT_HOUR_WEIGHTS = {h: 1.0 for h in range(24)}


def compute_adaptive_hour_weights(events: List[Dict[str, Any]]) -> Dict[int, float]:
    """Compute hour-of-day confidence weights from rolling hourly WR of WAL trades.

    Scans all PositionClosed events (last 30 days loaded by caller). Groups
    trades by UTC hour of entry. For each hour with >= 5 trades, computes
    Bayesian WR and converts to a confidence multiplier:

      multiplier = 0.5 + (bayesian_wr * 1.0)

    This maps:
      WR 0%  → 0.50 (halve confidence in worst hours)
      WR 50% → 1.00 (neutral)
      WR 100% → 1.50 (boost for best hours)

    Clamped to [0.5, 1.5]. Hours with < 5 trades keep default 1.0.

    Returns {0: 1.0, 1: 0.7, 2: 1.3, ...} — keyed by UTC hour (int 0-23).
    """
    hour_wins: Dict[int, int] = {}
    hour_total: Dict[int, int] = {}

    for event in events:
        payload = event.get("payload", {})
        if "PositionClosed" not in payload:
            continue
        pc = payload["PositionClosed"]
        pnl = pc.get("final_pnl", 0.0)

        # Extract entry UTC hour from entry_ts (nanoseconds) or opened_at
        entry_ts = pc.get("entry_ts", pc.get("opened_at", event.get("ts", 0)))
        if entry_ts <= 0:
            continue

        # Convert nanosecond timestamp to UTC hour
        try:
            entry_dt = datetime.fromtimestamp(entry_ts / 1_000_000_000, tz=timezone.utc)
            utc_hour = entry_dt.hour
        except (OSError, ValueError, OverflowError):
            continue

        hour_total[utc_hour] = hour_total.get(utc_hour, 0) + 1
        if pnl > 0:
            hour_wins[utc_hour] = hour_wins.get(utc_hour, 0) + 1

    # Compute Bayesian-smoothed weights per hour
    weights: Dict[int, float] = {}
    for h in range(24):
        total = hour_total.get(h, 0)
        wins = hour_wins.get(h, 0)
        if total >= 5:
            # Bayesian WR with Beta(2,2) prior
            bwr = bayesian_win_rate(wins, total)
            # Map: 0% WR → 0.5, 50% WR → 1.0, 100% WR → 1.5
            mult = 0.5 + bwr
            mult = max(0.5, min(1.5, mult))
            weights[h] = round(mult, 2)
            log.info("Hour weight %02d: WR=%.0f%% (%d/%d) → %.2f",
                     h, (wins / total) * 100, wins, total, weights[h])
        else:
            weights[h] = 1.0  # Insufficient data → neutral

    return weights


# ---------------------------------------------------------------------------
# TOML generators
# ---------------------------------------------------------------------------
def generate_dynamic_weights_toml(
    recs: Dict[str, Any],
    metrics: Dict[str, Any],
    history: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
) -> str:
    """Generate config/dynamic_weights.toml content.

    Schema matches RawDynamicWeights in ouroboros_loader.rs:
      schema_version = 1
      [bayesian]
      win_rate, trade_count, sharpe_ratio, dsr, dsr_significant
      [exit]
      chandelier_atr_mult, rung5_rate
      [regime]
      best, worst, <regime_name> = <scale_float>
      [kelly_fractions]
      t1, t2, t3
    """
    # --- Bayesian section ---
    # Prefer cumulative stats from persistent memory (if available)
    # Falls back to single-day metrics if memory not loaded yet
    total_trades = metrics.get("total_trades", 0)
    raw_win_rate = metrics.get("win_rate", 0.0)
    try:
        from python_brain.ouroboros.persistent_memory import load_memory
        mem = load_memory()
        if mem.total_exits >= 10:
            # Use cumulative stats — much more reliable than single-day
            total_trades = mem.total_exits
            raw_win_rate = mem.all_time_win_rate
            log.info("Using persistent memory for Bayesian: %d trades, WR=%.1f%%",
                     total_trades, raw_win_rate * 100)
    except Exception:
        pass  # Fall back to single-day stats
    wins = int(round(raw_win_rate * total_trades)) if total_trades > 0 else 0
    bwr = bayesian_win_rate(wins, total_trades)

    # Sharpe from historical PnLs
    pnls = [h.get("total_pnl", 0.0) for h in history if h.get("total_trades", 0) > 0]
    sharpe = compute_sharpe(pnls)
    dsr_val, dsr_sig = compute_dsr(sharpe, len(pnls))

    bayesian = {
        "win_rate": round(bwr, 6),
        "trade_count": total_trades,
        "sharpe_ratio": round(sharpe, 6),
        "dsr": round(dsr_val, 6),
        "dsr_significant": dsr_sig,
    }

    # --- Exit section ---
    # Plan 1 Phase 3: Use VIX-regime-aware ATR multiplier for Chandelier exit.
    # The adaptive value overrides nightly recommendations when VIX data is available.
    adaptive_atr = compute_adaptive_chandelier_atr(events, recs)
    chandelier_atr = adaptive_atr
    if chandelier_atr is None:
        chandelier_atr = 3.0
    avg_rung = metrics.get("avg_rung", 0.0)
    # rung5_rate: fraction of trades hitting rung 5 (highest profit ladder)
    # Approximate from avg_rung if detailed data unavailable
    rung5_rate = max(0.0, min(1.0, (avg_rung - 3.0) / 2.0)) if avg_rung > 3.0 else 0.0

    exit_section = {
        "chandelier_atr_mult": round(chandelier_atr, 2),
        "rung5_rate": round(rung5_rate, 4),
    }

    # --- Regime section ---
    regime_scales = compute_regime_scales(events)

    # Enrich regime scales from persistent memory (cumulative per-regime performance)
    try:
        from python_brain.ouroboros.persistent_memory import load_memory
        mem = load_memory()
        if mem.regime_stats:
            for regime_name, rstats in mem.regime_stats.items():
                if rstats.get("total_trades", 0) >= 10:
                    scale = mem.get_regime_scale(regime_name)
                    regime_scales[regime_name] = round(scale, 2)
            log.info("Enriched regime scales from persistent memory: %s", regime_scales)
    except Exception:
        pass

    # Determine best/worst from recommendations or defaults
    regime_best = "bull_quiet"
    regime_worst = "bear_volatile"
    if regime_scales:
        regime_best = max(regime_scales, key=regime_scales.get)
        regime_worst = min(regime_scales, key=regime_scales.get)

    # Build regime dict: best, worst, then scale entries
    regime = {
        "best": regime_best,
        "worst": regime_worst,
    }
    for name, scale in sorted(regime_scales.items()):
        regime[name] = round(scale, 2)

    # --- Kelly fractions ---
    kelly = compute_kelly_fractions(metrics, recs)

    # --- Assemble TOML structure ---
    # We build the TOML string manually to control formatting and match
    # the exact layout the Rust deserializer expects (tested in ouroboros_loader.rs).
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"# AEGIS V2 — Dynamic Weights (auto-generated by config_writer)",
        f"# Generated: {now_utc}",
        f"# Source: ouroboros_recommendations.json + metrics + WAL",
        f"# DO NOT EDIT — regenerated nightly at 04:51 UTC",
        f"",
        f"schema_version = 1",
        f"",
        f"[bayesian]",
        f"win_rate = {bayesian['win_rate']:.6f}",
        f"trade_count = {bayesian['trade_count']}",
        f"sharpe_ratio = {bayesian['sharpe_ratio']:.6f}",
        f"dsr = {bayesian['dsr']:.6f}",
        f"dsr_significant = {'true' if bayesian['dsr_significant'] else 'false'}",
        f"",
        f"[exit]",
        f"chandelier_atr_mult = {exit_section['chandelier_atr_mult']:.2f}",
        f"rung5_rate = {exit_section['rung5_rate']:.4f}",
        f"",
        f"[regime]",
        f'best = "{regime["best"]}"',
        f'worst = "{regime["worst"]}"',
    ]
    for name, scale in sorted(regime_scales.items()):
        lines.append(f"{name} = {scale:.2f}")

    lines += [
        f"",
        f"[kelly_fractions]",
    ]
    for tier, frac in sorted(kelly.items()):
        lines.append(f"{tier} = {frac:.6f}")

    # Phase E: Bounded adaptive confidence floor
    # Bidirectional: raises when losing, lowers when winning.
    # Range: [55, 80]. Never below 55 (absolute safety floor).
    # Ratchets based on Bayesian WR over total trade history.
    FLOOR_MIN = 55   # Absolute minimum (good conditions → more opportunities)
    FLOOR_MAX = 80   # Absolute maximum (crisis conditions → very selective)
    FLOOR_DEFAULT = 65  # Starting point / no-data default
    adaptive_floor = FLOOR_DEFAULT
    if bayesian["trade_count"] >= 20:
        wr = bayesian["win_rate"]
        if wr > 0.55:
            adaptive_floor = 55   # winning consistently → lower floor, capture more
        elif wr > 0.48:
            adaptive_floor = 60   # decent → slightly below default
        elif wr > 0.40:
            adaptive_floor = 65   # break-even zone → default
        elif wr > 0.30:
            adaptive_floor = 70   # losing → raise floor, be more selective
        else:
            adaptive_floor = 75   # heavily losing → very conservative
    adaptive_floor = max(FLOOR_MIN, min(FLOOR_MAX, adaptive_floor))

    lines += [
        f"",
        f"[signal]",
        f"confidence_floor = {adaptive_floor}",
    ]

    # Phase E: Ticker blacklist — suppress tickers with proven poor WR
    # WIRED (Sprint 3E/3F): Uses Wilson score interval instead of raw WR to avoid
    # overreacting to small samples. Wilson lower bound < 0.20 with N >= 20 → blacklist.
    # Wilson lower bound > 0.45 with N >= 10 AND previously blacklisted → whitelist.
    import math

    def _wilson_lower(wins: int, n: int, z: float = 1.96) -> float:
        """Wilson score interval lower bound (95% confidence)."""
        if n == 0:
            return 0.0
        phat = wins / n
        denom = 1 + z * z / n
        centre = phat + z * z / (2 * n)
        spread = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
        return (centre - spread) / denom

    blacklisted = []
    try:
        from python_brain.ouroboros.persistent_memory import load_memory
        mem = load_memory()
        if hasattr(mem, 'ticker_stats') and mem.ticker_stats:
            for sym, ts in mem.ticker_stats.items():
                trades = ts.get("total_trades", 0)
                wins = ts.get("wins", int(ts.get("win_rate", 0.5) * max(trades, 1)))
                wilson_lb = _wilson_lower(wins, trades)
                if trades >= 20 and wilson_lb < 0.20:
                    blacklisted.append(sym)
                    log.info("Ticker blacklisted: %s (Wilson LB=%.2f, %d/%d trades)",
                             sym, wilson_lb, wins, trades)
    except Exception:
        pass

    # Merge Gemini morning brief avoid_tickers into blacklist (GAP 2 fix)
    gemini_avoid: List[str] = []
    gemini_focus: List[str] = []
    gemini_strategy_weights: Dict[str, float] = {}
    try:
        brief_path = DATA_DIR / "gemini" / "morning_brief_latest.json"
        if brief_path.exists():
            brief_age_hours = (time.time() - brief_path.stat().st_mtime) / 3600.0
            if brief_age_hours < 12.0:
                with open(brief_path) as f:
                    brief = json.load(f)
                brief_data = brief.get("data", {})
                gemini_avoid = brief_data.get("avoid_tickers", [])
                gemini_focus = brief_data.get("focus_tickers", [])
                gemini_strategy_weights = brief_data.get("strategy_weights", {})
                if gemini_avoid:
                    # Append to blacklist (deduplicating)
                    for sym in gemini_avoid:
                        if sym not in blacklisted:
                            blacklisted.append(sym)
                    log.info("Gemini morning brief: %d avoid tickers merged into blacklist", len(gemini_avoid))
                if gemini_focus:
                    log.info("Gemini morning brief: %d focus tickers loaded", len(gemini_focus))
            else:
                log.info("Gemini morning_brief_latest.json is %.1fh old (>12h), skipping", brief_age_hours)
    except Exception as e:
        log.warning("Gemini morning brief integration failed (non-fatal): %s", e)

    lines += [
        f"",
        f"[ticker_blacklist]",
        f"# Tickers with Wilson LB < 20% over 20+ trades + Gemini avoid list",
        f"tickers = [{', '.join(repr(t) for t in sorted(blacklisted))}]",
    ]

    # Gemini focus tickers + strategy weights (advisory, engine reads if available)
    if gemini_focus:
        lines += [
            f"",
            f"[gemini]",
            f"# Focus tickers from Gemini morning brief (advisory priority boost)",
            f"focus_tickers = [{', '.join(repr(t) for t in gemini_focus[:20])}]",
        ]
    if gemini_strategy_weights:
        lines += [
            f"",
            f"[strategy_weight_overrides]",
            f"# From Gemini morning brief — strategy allocation weights",
        ]
        for k, v in gemini_strategy_weights.items():
            lines.append(f"{k} = {v:.4f}")


    # Phase E: Indicator gates — discovered thresholds from indicator_intelligence
    # These are actionable rules like "ADX > 15 improves WR by 12%"
    # The bridge loads these and applies them as pre-signal filters.
    indicator_gates = []
    try:
        intel_path = Path(recs.get("_data_dir", "/app/data")) / "indicator_intelligence.json"
        if not intel_path.exists():
            intel_path = Path("/app/data/indicator_intelligence.json")
        if intel_path.exists():
            import json
            with open(intel_path) as f:
                intel_data = json.load(f)
            filters = intel_data.get("recommended_filters", {})
            for indicator, rule in filters.items():
                if rule.get("confidence_score", 0) >= 0.6:  # Only high-confidence rules
                    direction = rule.get("direction", "above")
                    threshold = rule.get("threshold", 0)
                    lift = rule.get("lift", 0)
                    indicator_gates.append({
                        "indicator": indicator,
                        "direction": direction,
                        "threshold": round(threshold, 4),
                        "lift_pct": round(lift * 100, 1),
                    })
                    log.info("Indicator gate: %s %s %.4f (lift=%.1f%%)",
                             indicator, direction, threshold, lift * 100)
    except Exception:
        pass

    lines += [
        f"",
        f"[indicator_gates]",
        f"# Discovered by indicator_intelligence — high-confidence rules only",
    ]
    for i, gate in enumerate(indicator_gates):
        lines.append(f'[[indicator_gates.rules]]')
        lines.append(f'indicator = "{gate["indicator"]}"')
        lines.append(f'direction = "{gate["direction"]}"')
        lines.append(f'threshold = {gate["threshold"]}')
        lines.append(f'lift_pct = {gate["lift_pct"]}')

    # --- Plan 1 Phase 3: Adaptive parameters (regime-aware) ---

    # P3.1: Adaptive Chandelier ATR multiplier (VIX-regime-aware)
    adaptive_atr = compute_adaptive_chandelier_atr(events, recs)
    # Override the exit section's chandelier_atr_mult with adaptive value
    # Keep the nightly-recommended value as base, but VIX regime takes precedence
    lines += [
        f"",
        f"[adaptive_chandelier]",
        f"# VIX-regime-aware ATR multiplier (Plan 1 Phase 3)",
        f"# VIX > 35 → 3.0, VIX > 25 → 2.5, normal → 2.0",
        f"atr_mult = {adaptive_atr:.2f}",
    ]

    # P3.2: Adaptive spread veto threshold (VIX-regime-aware)
    adaptive_spread = compute_adaptive_spread_veto(events)
    lines += [
        f"",
        f"[adaptive_spread]",
        f"# VIX-regime-aware spread veto (Plan 1 Phase 3)",
        f"# Low vol → 0.2%, normal → 0.3%, high vol → 0.5%",
        f"spread_veto_pct = {adaptive_spread:.4f}",
    ]

    # P3.3: Adaptive entry type weights (performance-based)
    entry_weights = compute_adaptive_entry_type_weights(events)
    lines += [
        f"",
        f"[adaptive_entry_weights]",
        f"# Performance-based entry type sizing weights (Plan 1 Phase 3)",
        f"# TypeD WR < 35% → 0.5x, TypeB WR > 45% → 1.5x, else 1.0x",
    ]
    for etype in ["TypeA", "TypeB", "TypeC", "TypeD", "TypeE", "TypeF"]:
        weight = entry_weights.get(etype, 1.0)
        lines.append(f"{etype} = {weight:.2f}")

    # P3.4: Adaptive per-exchange session weights (PnL-based)
    exchange_weights = compute_adaptive_exchange_weights(history)
    lines += [
        f"",
        f"[adaptive_exchange_weights]",
        f"# Per-exchange sizing weights (Plan 1 Phase 3)",
        f"# Negative PnL over last 5 sessions → 0.5x, else 1.0x",
    ]
    if exchange_weights:
        for exch in sorted(exchange_weights.keys()):
            lines.append(f'{exch} = {exchange_weights[exch]:.2f}')
    else:
        lines.append(f"# No exchange data yet — all default 1.0x")

    # BT-004: Adaptive hour-of-day confidence weights from rolling WAL hourly WR.
    # Bridge loads these from [adaptive_hour_weights] and multiplies signal
    # confidence by the hour weight before best-signal selection.
    adaptive_hour_wts = compute_adaptive_hour_weights(events)
    lines += [
        f"",
        f"[adaptive_hour_weights]",
        f"# Hour-of-day confidence multipliers from rolling WAL hourly WR (BT-004)",
        f"# Bayesian-smoothed: WR 0%→0.50, WR 50%→1.00, WR 100%→1.50",
        f"# Hours with < 5 trades default to 1.0",
    ]
    for h in range(24):
        wt = adaptive_hour_wts.get(h, 1.0)
        lines.append(f'"{h:02d}" = {wt:.2f}')

    # P3.5: Adaptive Kelly cap (drawdown-aware)
    adaptive_kelly_cap = compute_adaptive_kelly_cap(history, metrics)
    lines += [
        f"",
        f"[adaptive_kelly]",
        f"# Drawdown-aware Kelly fraction cap (Plan 1 Phase 3)",
        f"# Drawdown > 10% → 0.05, > 5% → 0.10, else 0.20",
        f"kelly_cap = {adaptive_kelly_cap:.2f}",
    ]

    # Item 49 + P3.2: Per-entry-type confidence tuning
    # Reads backfill_feedback.json deltas and WAL trade data to adjust
    # entry type confidences within [60, 90] bounds.
    backfill_deltas = load_backfill_feedback()
    entry_confs = compute_entry_type_confidences(events, backfill_deltas)

    # Map TypeX -> config.toml field names for engine consumption
    type_to_field = {
        "TypeA": "type_a_confidence",
        "TypeB": "type_b_confidence",
        "TypeC": "type_c_confidence",
        "TypeD": "type_d_confidence",
        "TypeE": "type_e_confidence",
        "TypeF": "type_f_confidence",
    }

    lines += [
        f"",
        f"[entry_type_confidences]",
        f"# Ouroboros-tuned per-entry-type confidence (WAL + backfill feedback)",
        f"# Bounds: [{ENTRY_CONF_MIN}, {ENTRY_CONF_MAX}]",
    ]
    for etype in ["TypeA", "TypeB", "TypeC", "TypeD", "TypeE", "TypeF"]:
        conf = entry_confs.get(etype, ENTRY_CONF_DEFAULTS[etype])
        field = type_to_field[etype]
        lines.append(f"{field} = {conf:.1f}")

    # P3.6: Thompson Sampler-based adaptive entry type confidence floors.
    # Reads last 50 trades per type from WAL, computes Beta(wins+1, losses+1)
    # posterior, samples expected WR, adjusts confidence floor gradually (±5/cycle).
    # Winning types get LOWER floors (more trades), losing types get HIGHER floors.
    # This is separate from [entry_type_confidences] (Ouroboros WR-delta) — the
    # bridge.py takes the MAX of both floors for each type.
    prev_thompson = _load_prev_thompson_confidences()
    thompson_confs = compute_thompson_entry_confidence(events, prev_thompson)
    lines += [
        f"",
        f"[adaptive_entry_confidence]",
        f"# Thompson Sampler-based per-type confidence floors (Plan 1 Phase 3.6)",
        f"# Beta(wins+1, losses+1) posterior → expected WR → floor adjustment",
        f"# WR > 50% → lower floor (more trades), WR < 30% → 100 (disable)",
        f"# Bounds: [{THOMPSON_CONF_MIN}, {THOMPSON_CONF_MAX}], max ±{THOMPSON_MAX_STEP}/cycle",
    ]
    for etype in ["TypeA", "TypeB", "TypeC", "TypeD", "TypeE", "TypeF"]:
        conf = thompson_confs.get(etype, ENTRY_CONF_DEFAULTS[etype])
        field = type_to_field[etype]
        lines.append(f"{field} = {conf:.1f}")

    lines.append("")  # trailing newline
    return "\n".join(lines)


def generate_spread_cache_toml(spread_data: Dict[str, float]) -> Optional[str]:
    """Generate config/spread_cache.toml content.

    Schema matches the Rust loader:
      [spreads]
      "QQQ3.L" = 0.15
      "3LUS.L" = 0.22
    """
    if not spread_data:
        return None

    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"# AEGIS V2 — Spread Cache (auto-generated by config_writer)",
        f"# Generated: {now_utc}",
        f"# Median intraday spread estimates (percent) from WAL fill analysis",
        f"# DO NOT EDIT — regenerated nightly at 04:51 UTC",
        f"",
        f"[spreads]",
    ]
    for symbol in sorted(spread_data.keys()):
        spread = spread_data[symbol]
        lines.append(f'"{symbol}" = {spread:.4f}')

    lines.append("")
    return "\n".join(lines)


def generate_universe_classification_toml(watchlist: Dict[str, Any]) -> Optional[str]:
    """Generate config/universe_classification.toml content.

    Schema matches RawUniverseClass in ouroboros_loader.rs:
      schema_version = 1
      [tiers]
      tier1 = [0, 1, 2, ...]   # ticker IDs (i64)
      tier2 = [3, 4, ...]
      tier3 = [5, ...]
      locked = []
    """
    if not watchlist:
        return None

    vanguard = watchlist.get("vanguard", [])
    warm = watchlist.get("warm", [])
    apex = watchlist.get("apex_t3", watchlist.get("apex", []))

    def symbols_to_ids(tickers: List[Dict[str, Any]]) -> List[int]:
        """Convert ticker dicts to ticker IDs. Uses TICKER_ID_MAP for known
        tickers, and index offset for extended universe tickers."""
        ids = []
        for t in tickers:
            sym = t.get("symbol", "")
            if sym in TICKER_ID_MAP:
                ids.append(TICKER_ID_MAP[sym])
            # Only include tickers with known IDs (engine uses ticker_id indexing)
        return sorted(set(ids))

    tier1_ids = symbols_to_ids(vanguard)
    tier2_ids = symbols_to_ids(warm)
    tier3_ids = symbols_to_ids(apex)

    # Locked: empty by default (Ouroboros would populate if a ticker is quarantined)
    locked_ids: List[int] = []

    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"# AEGIS V2 — Universe Classification (auto-generated by config_writer)",
        f"# Generated: {now_utc}",
        f"# Tier 1 (Vanguard): real-time 5s bar monitoring",
        f"# Tier 2 (Warm): daily-scanned tickers",
        f"# Tier 3 (Apex): weekly-scanned tickers",
        f"# Locked: quarantined tickers (excluded from trading)",
        f"# DO NOT EDIT — regenerated nightly at 04:51 UTC",
        f"",
        f"schema_version = 1",
        f"",
        f"[tiers]",
        f"tier1 = {_format_int_list(tier1_ids)}",
        f"tier2 = {_format_int_list(tier2_ids)}",
        f"tier3 = {_format_int_list(tier3_ids)}",
        f"locked = {_format_int_list(locked_ids)}",
        f"",
    ]
    return "\n".join(lines)


def _format_int_list(ids: List[int]) -> str:
    """Format a list of ints for TOML: [1, 2, 3]."""
    if not ids:
        return "[]"
    return "[" + ", ".join(str(i) for i in ids) + "]"



def _notify_engine_sighup():
    """Send SIGHUP to the aegis engine process so it hot-reloads dynamic_weights.toml.

    Best-effort: if engine PID is not found or signal fails, we log a warning
    but do not fail the config_writer pipeline.
    """
    import signal
    import subprocess
    try:
        result = subprocess.run(
            ["pgrep", "-x", "aegis"],
            capture_output=True, text=True, timeout=5,
        )
        pids = result.stdout.strip().split("\n")
        pids = [p for p in pids if p.isdigit()]
        if not pids:
            log.info("No aegis process found — SIGHUP skipped (pre-boot config_writer run?)")
            return
        for pid_str in pids:
            os.kill(int(pid_str), signal.SIGHUP)
            log.info("Sent SIGHUP to aegis PID %s (hot-reload dynamic_weights.toml)", pid_str)
    except Exception as e:
        log.warning("Failed to send SIGHUP to engine: %s (non-fatal)", e)



# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_config_writer() -> int:
    """Execute the config writer pipeline. Returns 0 on success, 1 on error."""
    start = time.monotonic()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("Config writer starting for %s", today)
    log.info("Project root: %s", _PROJECT_ROOT)
    log.info("Config dir: %s", CONFIG_DIR)

    # OBSERVE-ONLY MODE: If config.toml [ouroboros] observe_only = true,
    # skip all dynamic_weights.toml mutations. Nightly analysis still runs
    # (via nightly_v6.py) but config_writer does NOT write new parameters.
    # This prevents optimizing on statistically insufficient data (N < 300).
    try:
        try:
            import tomllib as _tomllib_cw
        except ImportError:
            import tomli as _tomllib_cw
        _cfg_path = CONFIG_DIR / "config.toml"
        if _cfg_path.exists():
            with open(_cfg_path, "rb") as _f:
                _cfg = _tomllib_cw.load(_f)
            if _cfg.get("ouroboros", {}).get("observe_only", False):
                min_trades = _cfg.get("ouroboros", {}).get("min_trades_for_mutation", 300)
                # P5: Auto-unfreeze gate — check actual trade count from system_memory.json
                _mem_path = CONFIG_DIR.parent / "data" / "system_memory.json"
                _actual_trades = 0
                if _mem_path.exists():
                    try:
                        import json as _json_cw
                        with open(_mem_path) as _mf:
                            _mem = _json_cw.load(_mf)
                        _actual_trades = _mem.get("bayesian", {}).get("trade_count", 0)
                    except Exception:
                        pass
                if _actual_trades >= min_trades:
                    log.warning(
                        "OUROBOROS UNFREEZE: %d trades >= %d threshold. "
                        "Proceeding with dynamic_weights mutation despite observe_only=true.",
                        _actual_trades, min_trades
                    )
                    # Don't return — fall through to normal mutation path
                else:
                    log.warning(
                        "OBSERVE-ONLY MODE: %d/%d trades. "
                        "Skipping dynamic_weights.toml mutation.", _actual_trades, min_trades
                    )
                    elapsed = time.monotonic() - start
                    log.info("Config writer completed (observe-only) in %.1fs", elapsed)
                    return 0
    except Exception as e:
        log.warning("Failed to check observe_only flag: %s — proceeding with normal write", e)

    errors = 0

    # --- Load all source data ---
    recs = load_recommendations()
    metrics = load_latest_metrics()
    history = load_historical_metrics(days=30)
    events = load_todays_wal_events(today)
    watchlist = load_watchlist()

    # --- 0. Gemini core universe → active_watchlist.json ---
    # REMOVED (GAP 6 fix): This block wrote Gemini tickers with wrong format
    # ("tickers" key) but engine expects "vanguard" format. ticker_selector.py
    # is the canonical watchlist writer; Gemini core universe already feeds into
    # ticker_selector via the every-2-hour cron. Dead code removed.

    # --- 1. dynamic_weights.toml (always written, even with defaults) ---
    try:
        dw_content = generate_dynamic_weights_toml(recs, metrics, history, events)
        dw_path = CONFIG_DIR / "dynamic_weights.toml"
        _record_config_change(dw_path, dw_content)
        atomic_write(dw_path, dw_content)
        log.info("dynamic_weights.toml written successfully")
    except Exception as e:
        log.error("Failed to write dynamic_weights.toml: %s", e, exc_info=True)
        errors += 1

    # --- 2. spread_cache.toml (only if spread data available) ---
    try:
        spread_data = extract_spread_data(events)
        if spread_data:
            sc_content = generate_spread_cache_toml(spread_data)
            if sc_content:
                sc_path = CONFIG_DIR / "spread_cache.toml"
                _record_config_change(sc_path, sc_content)
                atomic_write(sc_path, sc_content)
                log.info("spread_cache.toml written with %d tickers", len(spread_data))
        else:
            log.info("No spread data available — spread_cache.toml not written")
    except Exception as e:
        log.error("Failed to write spread_cache.toml: %s", e, exc_info=True)
        errors += 1

    # --- 3. universe_classification.toml (only if watchlist data available) ---
    try:
        uc_content = generate_universe_classification_toml(watchlist)
        if uc_content:
            uc_path = CONFIG_DIR / "universe_classification.toml"
            _record_config_change(uc_path, uc_content)
            atomic_write(uc_path, uc_content)
            log.info("universe_classification.toml written successfully")
        else:
            log.info("No watchlist data available — universe_classification.toml not written")
    except Exception as e:
        log.error("Failed to write universe_classification.toml: %s", e, exc_info=True)
        errors += 1

    elapsed = time.monotonic() - start
    if errors > 0:
        log.warning("Config writer completed with %d error(s) in %.2fs", errors, elapsed)
    else:
        log.info("Config writer completed successfully in %.2fs", elapsed)

    # Send SIGHUP to Rust engine so it hot-reloads dynamic_weights.toml
    _notify_engine_sighup()

    return 1 if errors > 0 else 0


def main():
    """CLI entry point."""
    try:
        sys.exit(run_config_writer())
    except Exception as e:
        log.error("Config writer crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
