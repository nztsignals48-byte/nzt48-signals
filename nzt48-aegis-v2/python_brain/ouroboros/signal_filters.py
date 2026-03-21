"""Signal Filters — Python-side pre-signal gates for config_writer.

SC-16: CUSUM dynamic mean (EWMA update)
SC-17: VPIN exchange-scoped bucket reset
SC-20: Half-Kelly until 250 trades
SC-21: Meta-labeler minimum sample size gate
T-05:  FAST gate dual-tier confidence (reduced indicator set for gap opens)
T-06b: Regime-dependent ADX thresholds (HMM state → ADX floor)
P2-14: Cooldown after Chandelier stop (30-min re-entry block)

These are SPECIFICATIONS for config_writer to emit into dynamic_weights.toml.
The Rust engine reads these as gate parameters. We don't modify the Rust code.

Usage: python3 -m python_brain.ouroboros.signal_filters
"""

# ---------------------------------------------------------------------------
# SK-03: CONFIDENCE FLOOR HIERARCHY (Unified Documentation)
# ---------------------------------------------------------------------------
#
# There are FOUR layers that enforce confidence floors. A signal must pass
# ALL of them. The effective floor at runtime is the MAX of all applicable
# layers. This comment block is the single source of truth for the hierarchy.
#
# LAYER 1 -- Rust Engine (config.toml [signal].confidence_floor = 65)
#   The hard static floor. Risk Arbiter CHECK 10 rejects any signal with
#   confidence < 65. This is the absolute minimum and cannot be lowered
#   by any Python-side logic. Set by operator in config.toml.
#
# LAYER 2 -- bridge.py (leverage-aware dynamic floor)
#   Computes a floor based on instrument leverage:
#     - Leverage >= 5x:  floor = 80
#     - Leverage >= 3x:  floor = 65
#     - Unleveraged:     floor = 45
#   Then takes max(leverage_floor, adaptive_floor_from_layer_3).
#   Applied in process_tick() before signal emission. Regime and volume
#   gates can further RAISE this floor (e.g. weak Hurst -> 70, flat vol -> 75).
#
# LAYER 3 -- config_writer.py (Ouroboros adaptive floor -> dynamic_weights.toml)
#   Emits [signal].confidence_floor into dynamic_weights.toml each night.
#   Bounded to range [65, 80]. Logic:
#     - WR > 55% and 50+ trades: floor = 70 (be more selective)
#     - WR 30-55%: floor = 65 (maintain baseline)
#     - WR < 30%: floor = 65 (never loosen below static minimum)
#   Q-073 guarantee: adaptive floor NEVER goes below STATIC_CONFIDENCE_FLOOR (65).
#   bridge.py reads this at startup via _load_adaptive_floor().
#
# LAYER 4 -- Python nightly (research_store.py, recommendations)
#   Ouroboros nightly analysis may recommend raising the floor (e.g. to 70%)
#   as a "quality gate" suggestion. This is advisory only -- config_writer
#   reads the recommendation and may incorporate it into Layer 3.
#
# EFFECTIVE FLOOR AT RUNTIME:
#   max(Layer1_static_65, Layer2_leverage_floor, Layer3_adaptive_floor)
#
# INVARIANT: The effective floor is ALWAYS >= 65. No code path can lower
# it below config.toml's static value.
# ---------------------------------------------------------------------------

from __future__ import annotations

import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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
# T-05: FAST Gate Dual-Tier Confidence
# ---------------------------------------------------------------------------
# During gap opens (>1% gap) or volatility spikes (RVOL>3.0), the full
# 12-indicator gate is too slow — entry opportunities expire in <30 seconds.
# FAST mode uses a reduced 4-indicator subset (hurst, rvol, vwap_dist,
# confidence) with a HIGHER confidence floor (75) to compensate for fewer
# checks. Auto-expires after 5 minutes to prevent stale fast-mode windows.

FAST_GATE_DEFAULTS = {
    "fast_gate_enabled": True,
    "fast_gate_gap_threshold_pct": 1.0,
    "fast_gate_rvol_threshold": 3.0,
    "fast_gate_indicators": ["hurst", "rvol", "vwap_dist", "confidence"],
    "fast_gate_confidence_floor": 75,
    "fast_gate_max_duration_sec": 300,
}

# Allowed range guardrails for Ouroboros-tuned values
_FAST_GATE_GUARDRAILS = {
    "fast_gate_gap_threshold_pct": (0.3, 3.0),
    "fast_gate_rvol_threshold": (1.5, 6.0),
    "fast_gate_confidence_floor": (60, 95),
    "fast_gate_max_duration_sec": (60, 600),
}


def compute_fast_gate_config() -> dict:
    """Compute FAST gate dual-tier confidence configuration.

    During gap opens or volatility spikes, the full 12-indicator gate
    is too slow (entry windows <30s). FAST mode uses a reduced 4-indicator
    subset with a higher confidence floor to compensate.

    Reads ouroboros_recommendations.json for any Ouroboros-tuned overrides.

    Returns dict with:
        fast_gate_enabled: bool
        fast_gate_gap_threshold_pct: float  (gap % triggering FAST mode)
        fast_gate_rvol_threshold: float     (RVOL triggering FAST mode)
        fast_gate_indicators: list[str]     (reduced indicator set)
        fast_gate_confidence_floor: int     (higher floor for fewer checks)
        fast_gate_max_duration_sec: int     (auto-expire after N seconds)
    """
    config = dict(FAST_GATE_DEFAULTS)

    # Read Ouroboros-tuned overrides
    recs_file = DATA_DIR / "ouroboros_recommendations.json"
    if recs_file.exists():
        try:
            recs = json.loads(recs_file.read_text())
            for key, (lo, hi) in _FAST_GATE_GUARDRAILS.items():
                if key in recs:
                    val = type(FAST_GATE_DEFAULTS[key])(recs[key])
                    config[key] = max(lo, min(hi, val))
            # Allow indicator list override (must be non-empty subset)
            if "fast_gate_indicators" in recs:
                ind = recs["fast_gate_indicators"]
                if isinstance(ind, list) and len(ind) >= 2:
                    config["fast_gate_indicators"] = ind[:6]  # Cap at 6
        except Exception:
            pass

    log.info(
        "T-05 FAST gate config: gap>=%.1f%%, RVOL>=%.1f, %d indicators, "
        "confidence_floor=%d, max_duration=%ds",
        config["fast_gate_gap_threshold_pct"],
        config["fast_gate_rvol_threshold"],
        len(config["fast_gate_indicators"]),
        config["fast_gate_confidence_floor"],
        config["fast_gate_max_duration_sec"],
    )
    return config


# ---------------------------------------------------------------------------
# T-06b: Regime-Dependent ADX Thresholds
# ---------------------------------------------------------------------------
# ADX threshold should vary by volatility regime detected by the HMM
# Student-t model (hmm_student_t.py):
#   - COMPRESSION (HMM state 0, low vol):  ADX >= 15 (lower bar, momentum scarce)
#   - NORMAL      (HMM state 1):           ADX >= 20 (standard)
#   - EXPANSION   (HMM state 2, high vol): ADX >= 25 (higher bar, false signals common)

REGIME_ADX_MAP = {
    0: ("COMPRESSION", 15),
    1: ("NORMAL", 20),
    2: ("EXPANSION", 25),
}

# Fallback label + threshold when regime state is unavailable
_DEFAULT_REGIME = 1  # NORMAL


def compute_regime_adx_config(
    wal_dir: Path = WAL_DIR,
) -> dict:
    """Compute regime-dependent ADX threshold configuration.

    Reads the latest HMM regime state from:
      1. data/hmm_regime_state.json (dedicated state file, preferred)
      2. Most recent data/regime_reports/hmm_regime_report_*.json (fallback)

    Maps HMM state index → (regime_label, adx_threshold):
      0 → COMPRESSION, ADX >= 15
      1 → NORMAL,      ADX >= 20
      2 → EXPANSION,   ADX >= 25

    Returns dict with:
        adx_regime_enabled: bool
        adx_threshold_compression: int (15)
        adx_threshold_normal: int (20)
        adx_threshold_expansion: int (25)
        adx_current_regime: str  (e.g. "NORMAL")
        adx_current_threshold: int (active threshold)
    """
    current_state = _DEFAULT_REGIME

    # Strategy 1: dedicated state file (written by hmm_student_t.py or cron)
    state_file = DATA_DIR / "hmm_regime_state.json"
    if state_file.exists():
        try:
            state_data = json.loads(state_file.read_text())
            # Accept either {"current_regime": {"index": N}} or {"regime_index": N}
            if "current_regime" in state_data:
                cr = state_data["current_regime"]
                idx = cr.get("index", _DEFAULT_REGIME) if isinstance(cr, dict) else int(cr)
                if idx in REGIME_ADX_MAP:
                    current_state = idx
            elif "regime_index" in state_data:
                idx = int(state_data["regime_index"])
                if idx in REGIME_ADX_MAP:
                    current_state = idx
        except Exception:
            pass

    # Strategy 2: fallback to most recent regime report JSON
    if current_state == _DEFAULT_REGIME and not state_file.exists():
        report_dir = DATA_DIR / "regime_reports"
        if report_dir.exists():
            reports = sorted(report_dir.glob("hmm_regime_report_*.json"), reverse=True)
            for rpath in reports[:1]:  # Only check the latest
                try:
                    report = json.loads(rpath.read_text())
                    cr = report.get("current_regime", {})
                    idx = cr.get("index", _DEFAULT_REGIME) if isinstance(cr, dict) else _DEFAULT_REGIME
                    if idx in REGIME_ADX_MAP:
                        current_state = idx
                except Exception:
                    pass

    regime_label, threshold = REGIME_ADX_MAP[current_state]

    config = {
        "adx_regime_enabled": True,
        "adx_threshold_compression": REGIME_ADX_MAP[0][1],
        "adx_threshold_normal": REGIME_ADX_MAP[1][1],
        "adx_threshold_expansion": REGIME_ADX_MAP[2][1],
        "adx_current_regime": regime_label,
        "adx_current_threshold": threshold,
    }
    log.info(
        "T-06b Regime ADX config: regime=%s (HMM state %d), "
        "active_threshold=%d (compression=%d, normal=%d, expansion=%d)",
        regime_label, current_state, threshold,
        config["adx_threshold_compression"],
        config["adx_threshold_normal"],
        config["adx_threshold_expansion"],
    )
    return config


# ---------------------------------------------------------------------------
# P2-14: Cooldown After Chandelier Stop
# ---------------------------------------------------------------------------
# After a Chandelier exit fires on a ticker, block re-entry for 30 minutes.
# This prevents chasing the same ticker immediately after a stop-out, which
# historically leads to revenge trades with poor R:R.
#
# Detection: scan WAL for ExitSignal events where reason contains
# "ChandelierStop" OR PositionClosed events where the preceding ExitSignal
# was a Chandelier stop. We use a 30-minute lookback from now.

POST_STOP_COOLDOWN_MINUTES = 30


def compute_cooldown_config(
    wal_dir: Path = WAL_DIR,
) -> dict:
    """Compute post-Chandelier-stop cooldown configuration.

    Scans WAL (current + archive) for recent Chandelier exit events
    within the last POST_STOP_COOLDOWN_MINUTES. Returns the set of
    tickers currently in cooldown (re-entry blocked).

    Detection logic:
      1. ExitSignal events with reason containing "ChandelierStop"
      2. ExitSignal events with priority containing "ChandelierStop"
    Both are checked since the Rust engine may serialize either field.

    Returns dict with:
        post_stop_cooldown_enabled: bool
        post_stop_cooldown_minutes: int (30)
        post_stop_active_tickers: list[str]  (tickers currently in cooldown)
        post_stop_cooldown_count: int
    """
    import time as _time

    cooldown_ns = POST_STOP_COOLDOWN_MINUTES * 60 * 1_000_000_000
    now_ns = int(_time.time() * 1_000_000_000)
    cutoff_ns = now_ns - cooldown_ns

    # ticker_id → symbol mapping from RoutedOrder events (best effort)
    ticker_symbols: dict[int, str] = {}
    # ticker_ids that had a Chandelier stop within the cooldown window
    stopped_ticker_ids: set[int] = set()
    stopped_symbols: set[str] = set()

    wal_files = _collect_wal_files(wal_dir)

    for wf in wal_files:
        try:
            with open(wf) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    payload = event.get("payload", {})
                    ts = event.get("event_time_ns", 0)

                    # Build ticker_id → symbol map from RoutedOrder and PositionClosed
                    if "RoutedOrder" in payload:
                        ro = payload["RoutedOrder"]
                        sym = ro.get("symbol", "")
                        tid = ro.get("ticker_id")
                        if sym and tid is not None:
                            ticker_symbols[tid] = sym
                    elif "PositionClosed" in payload:
                        pc = payload["PositionClosed"]
                        sym = pc.get("symbol", "")
                        tid = pc.get("ticker_id")
                        if sym and tid is not None:
                            ticker_symbols[tid] = sym

                    # Detect Chandelier stops within cooldown window
                    if ts >= cutoff_ns and "ExitSignal" in payload:
                        es = payload["ExitSignal"]
                        reason = es.get("reason", "")
                        priority = es.get("priority", "")
                        if "ChandelierStop" in reason or "ChandelierStop" in priority:
                            tid = es.get("ticker_id")
                            if tid is not None:
                                stopped_ticker_ids.add(tid)
        except OSError:
            continue

    # Resolve ticker_ids to symbols
    for tid in stopped_ticker_ids:
        sym = ticker_symbols.get(tid, f"TID_{tid}")
        stopped_symbols.add(sym)

    active_tickers = sorted(stopped_symbols)

    config = {
        "post_stop_cooldown_enabled": True,
        "post_stop_cooldown_minutes": POST_STOP_COOLDOWN_MINUTES,
        "post_stop_active_tickers": active_tickers,
        "post_stop_cooldown_count": len(active_tickers),
    }
    log.info(
        "P2-14 Post-stop cooldown: %d tickers in cooldown (%s), window=%d min",
        len(active_tickers),
        ", ".join(active_tickers) if active_tickers else "none",
        POST_STOP_COOLDOWN_MINUTES,
    )
    return config


def _collect_wal_files(wal_dir: Path) -> list[Path]:
    """Collect all WAL .ndjson files (current + archive), sorted by name."""
    wal_files: list[Path] = []
    if wal_dir.exists():
        wal_files.extend(sorted(wal_dir.glob("*.ndjson")))
    archive_dir = wal_dir / "archive"
    if archive_dir.exists():
        wal_files.extend(sorted(archive_dir.glob("*.ndjson")))
    return wal_files


# ---------------------------------------------------------------------------
# P2-15: Adaptive EWA Learning Rate (Changepoint-Driven)
# ---------------------------------------------------------------------------
# When PELT detects a structural changepoint (regime shift), EWA learning
# rate (eta) should increase to adapt faster. When the market is stable
# (no changepoints for 5+ sessions), use a slower learning rate.
#
# Regime: changepoint within last 5 sessions -> eta=0.10 (fast adaptation)
#         no changepoint for 5+ sessions     -> eta=0.05 (stable)
#
# Reads the last PELT changepoint date from ouroboros_recommendations.json
# (written by nightly_v6 via indicator_intelligence or config_writer).

RECS_FILE_SF = DATA_DIR / "ouroboros_recommendations.json"

EWA_FAST_RATE = 0.10   # Fast adaptation after changepoint
EWA_SLOW_RATE = 0.05   # Stable regime, slow adaptation
EWA_CHANGEPOINT_WINDOW_SESSIONS = 5  # Sessions to consider "recent"


def compute_adaptive_ewa_config(
    wal_dir: Path = WAL_DIR,
) -> dict:
    """P2-15: Compute adaptive EWA learning rate based on PELT changepoint recency.

    Reads data/ouroboros_recommendations.json for the last PELT changepoint date.
    If a changepoint was detected within the last 5 trading sessions, uses a
    fast learning rate (eta=0.10). Otherwise, uses a slow rate (eta=0.05).

    Args:
        wal_dir: WAL directory (unused currently, kept for interface consistency)

    Returns dict:
        ewa_adaptive_enabled: bool (always True)
        ewa_learning_rate: float (0.05 or 0.10)
        ewa_last_changepoint: str (ISO date or "none")
        ewa_sessions_since_changepoint: int (-1 if unknown)
    """
    config = {
        "ewa_adaptive_enabled": True,
        "ewa_learning_rate": EWA_SLOW_RATE,
        "ewa_last_changepoint": "none",
        "ewa_sessions_since_changepoint": -1,
    }

    # Load recommendations to find last PELT changepoint
    if not RECS_FILE_SF.exists():
        log.info("P2-15 EWA: No recommendations file found — using slow rate %.2f",
                 EWA_SLOW_RATE)
        return config

    try:
        recs = json.loads(RECS_FILE_SF.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("P2-15 EWA: Failed to read recommendations: %s", e)
        return config

    # Look for PELT changepoint date in multiple possible locations.
    # The nightly_v6 or indicator_intelligence may store it in different keys.
    changepoint_date_str = None

    # Check top-level keys
    for key in ("last_pelt_changepoint", "pelt_changepoint_date",
                "last_changepoint_date", "changepoint_date"):
        if key in recs and recs[key]:
            changepoint_date_str = str(recs[key])
            break

    # Check nested indicator_filters
    if changepoint_date_str is None:
        indicator_filters = recs.get("indicator_filters", {})
        if isinstance(indicator_filters, dict):
            for key in ("last_changepoint", "pelt_changepoint_date"):
                if key in indicator_filters and indicator_filters[key]:
                    changepoint_date_str = str(indicator_filters[key])
                    break

    # Check nested analytics_pack
    if changepoint_date_str is None:
        analytics = recs.get("analytics_pack", {})
        if isinstance(analytics, dict):
            for key in ("last_changepoint", "changepoint_date"):
                if key in analytics and analytics[key]:
                    changepoint_date_str = str(analytics[key])
                    break

    # Count trading sessions since changepoint
    if changepoint_date_str and changepoint_date_str != "none":
        try:
            cp_date = datetime.strptime(changepoint_date_str[:10], "%Y-%m-%d").date()
            today = datetime.now(timezone.utc).date()
            # Count weekdays (trading sessions) between changepoint and today
            sessions_since = 0
            current = cp_date
            while current < today:
                current += timedelta(days=1)
                if current.weekday() < 5:  # Mon-Fri
                    sessions_since += 1

            config["ewa_last_changepoint"] = changepoint_date_str[:10]
            config["ewa_sessions_since_changepoint"] = sessions_since

            if sessions_since <= EWA_CHANGEPOINT_WINDOW_SESSIONS:
                config["ewa_learning_rate"] = EWA_FAST_RATE
                log.info(
                    "P2-15 EWA: Changepoint detected %s (%d sessions ago) — "
                    "FAST rate eta=%.2f",
                    changepoint_date_str[:10], sessions_since, EWA_FAST_RATE,
                )
            else:
                config["ewa_learning_rate"] = EWA_SLOW_RATE
                log.info(
                    "P2-15 EWA: Last changepoint %s (%d sessions ago, >%d) — "
                    "SLOW rate eta=%.2f",
                    changepoint_date_str[:10], sessions_since,
                    EWA_CHANGEPOINT_WINDOW_SESSIONS, EWA_SLOW_RATE,
                )
        except (ValueError, TypeError) as e:
            log.warning("P2-15 EWA: Failed to parse changepoint date '%s': %s",
                        changepoint_date_str, e)
    else:
        log.info("P2-15 EWA: No PELT changepoint recorded — using slow rate %.2f",
                 EWA_SLOW_RATE)

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
    configs.update(compute_fast_gate_config())
    configs.update(compute_regime_adx_config(wal_dir))
    configs.update(compute_cooldown_config(wal_dir))
    configs.update(compute_adaptive_ewa_config(wal_dir))
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
