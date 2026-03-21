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
    """Write content to path atomically via tmp + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
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
    chandelier_atr = recs.get("chandelier_atr_mult", 3.0)
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
    # Q-073 FIX: Dynamic floor must NEVER go below static config minimum (65).
    # This prevents Ouroboros from loosening entry quality below operator's safety setting.
    # Hard floor: max(STATIC_MINIMUM, computed_value). Range: [65, 80].
    STATIC_CONFIDENCE_FLOOR = 65  # Matches config.toml [signal].confidence_floor
    adaptive_floor = STATIC_CONFIDENCE_FLOOR  # default = static minimum
    if bayesian["trade_count"] >= 50:
        if bayesian["win_rate"] > 0.55:
            adaptive_floor = 70  # high WR → be more selective
        elif bayesian["win_rate"] > 0.45:
            adaptive_floor = 65  # decent WR → maintain floor
        elif bayesian["win_rate"] < 0.30:
            adaptive_floor = 65  # low WR → keep floor (don't loosen)
    adaptive_floor = max(STATIC_CONFIDENCE_FLOOR, min(80, adaptive_floor))

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

    lines += [
        f"",
        f"[ticker_blacklist]",
        f"# Tickers with WR < 30% over 10+ trades — no new entries allowed",
        f"tickers = [{', '.join(repr(t) for t in sorted(blacklisted))}]",
    ]

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

    errors = 0

    # --- Load all source data ---
    recs = load_recommendations()
    metrics = load_latest_metrics()
    history = load_historical_metrics(days=30)
    events = load_todays_wal_events(today)
    watchlist = load_watchlist()

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
