"""Python Brain Bridge — long-lived subprocess for signal generation.

Protocol: JSON lines over stdin/stdout.
- Receives: {"type":"tick", "ticker_id":0, "last":10.5, "high":10.6, "low":10.4, "bid":10.49, "ask":10.51, "volume":1000, "timestamp_ns":..., ...context...}
- Responds: {"type":"signal", ...} or {"type":"no_signal", "ticker_id":...}
- Shutdown: {"type":"shutdown"}

Accumulates bar history per ticker. Evaluates Vanguard Sniper on each tick.
Runs Autonomous Orchestrator (S17-S20) in parallel on each tick.
Best signal wins (highest confidence). Runs 12-factor Kelly sizing when generated.
"""

import json
import math
import os
import sys
import time
from collections import defaultdict, deque

# Add python_brain to path so brain.* imports work (strategies use `from brain.config import ...`)
sys.path.insert(0, "/app/python_brain")
sys.path.insert(0, "/app")

from brain.strategies.vanguard_sniper import evaluate as vanguard_evaluate
from brain.strategies.apex_scout import evaluate as apex_evaluate
from brain.strategies.autonomous_orchestrator import (
    MarketContext,
    RegimeType,
    StrategyConfig,
    StrategyFamily,
    TickerState,
    TradeIntent,
    detect_session,
    orchestrate,
)
from brain.sizing.kelly_12factor import kelly_12factor
from brain.indicators.volume_analytics import calculate_rvol, volume_divergence, classify_volume_bvc, calculate_vpin
from brain.indicators.hurst import estimate_hurst, classify_regime
from brain.vwap import VWAPBar, VWAPCalculator
from brain.rsi_ibs import calculate_rsi, calculate_ibs, calculate_sma
from brain.gap_detector import calculate_gap_pct
from python_brain.ouroboros.cost_model import costs as _cost_model
from python_brain.ouroboros.bridge_watchdog import write_heartbeat as _write_heartbeat

MAX_BARS = 500

# Heartbeat: write every 30s so the watchdog knows we're alive
_last_heartbeat_time = 0.0
_HEARTBEAT_INTERVAL = 30.0

bar_history = defaultdict(lambda: deque(maxlen=MAX_BARS))

# Per-ticker VWAP calculators (persist across ticks, reset at session open)
vwap_calculators = defaultdict(VWAPCalculator)

# Sprint 7: Track last trading date per ticker for VWAP session reset.
# When a new date is detected, reset VWAP calculator for that ticker.
_last_vwap_date = {}  # ticker_id → date string "YYYY-MM-DD"

# Ticker ID → symbol mapping (populated from tick messages)
ticker_symbols = {}

# Tick counters for diagnostic logging (per-ticker)
_tick_counts = {}

# Per-ticker signal cooldown: prevents signal spam.
# FIXED (Sprint 5, T-08): Reduced from 300 (25min) to 60 (5min).
# Sprint 6: Now loaded from config.toml [hardening.ticks] if available.
def _load_cooldown_from_config():
    """Load signal cooldown ticks from config.toml, default 60."""
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        cfg_path = "/app/config/config.toml"
        if not os.path.exists(cfg_path):
            return 60
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        # Cooldown in ticks (each tick = 5s). Default 60 = 5 minutes.
        return data.get("hardening", {}).get("ticks", {}).get("signal_cooldown_ticks", 60)
    except Exception:
        return 60

# SIM_MODE: Load cooldown as 0 to completely disable per-ticker signal suppression.
_SIM_MODE = os.environ.get("AEGIS_SIM_MODE", "0") == "1"
SIGNAL_COOLDOWN_TICKS = 0 if _SIM_MODE else _load_cooldown_from_config()
_last_signal_tick = {}  # ticker_id → tick count when last signal was emitted

# ============================================================================
# T-01 (Sprint 5): Exchange cutoff blackout enforcement in Python.
# Prevents wasting compute on signal generation after the per-exchange entry
# cutoff. The Rust risk_arbiter already blocks entries, but generating signals
# we know will be rejected wastes 100ms+ of indicator + strategy evaluation.
# ============================================================================
_exchange_cutoffs_loaded = False
_exchange_cutoffs = {}  # exchange_key → (cutoff_hour, cutoff_minute)

# Map contracts.toml exchange names → config.toml [timing.exchange_cutoffs] keys
_EXCHANGE_KEY_MAP = {
    "LSEETF": "LSE",
    "LSE": "LSE",
    "SMART": "US",
    "HKEX": "HKEX",
    "TSE": "TSE",
    "XETRA": "XETRA",
    "EURONEXT": "EURONEXT",
    "SGX": "SGX",
    "AEB": "EURONEXT",   # Amsterdam → EURONEXT cutoff
    "HEX": "EURONEXT",   # Helsinki → EURONEXT cutoff
    "XMAD": "EURONEXT",  # Madrid → EURONEXT cutoff
}

# Infer exchange from symbol suffix (fallback when contracts.toml unavailable)
_SUFFIX_TO_EXCHANGE = {
    ".L": "LSE",
    ".T": "TSE",
    ".HK": "HKEX",
    ".SI": "SGX",
}

# Symbol → exchange mapping (built lazily from contracts.toml)
_symbol_exchange_map_loaded = False
_symbol_exchange_map = {}  # symbol → exchange key (e.g. "LSE", "US")

# Approximate UTC offsets per exchange (Rust does authoritative DST-aware check).
# This is a compute-saving early return, not a safety gate — fail-open is safe.
_EXCHANGE_UTC_OFFSET = {
    "LSE": 0,        # GMT (simplified; BST is +1 Mar-Oct)
    "US": -5,         # EST (simplified; EDT is -4 Mar-Nov)
    "HKEX": 8,        # HKT (no DST)
    "TSE": 9,         # JST (no DST)
    "XETRA": 1,       # CET (simplified; CEST is +2 Mar-Oct)
    "EURONEXT": 1,     # CET (simplified; CEST is +2 Mar-Oct)
    "SGX": 8,          # SGT (no DST)
}


def _load_exchange_cutoffs():
    """Load per-exchange entry cutoffs from config.toml [timing.exchange_cutoffs]."""
    global _exchange_cutoffs_loaded, _exchange_cutoffs
    if _exchange_cutoffs_loaded:
        return _exchange_cutoffs
    _exchange_cutoffs_loaded = True
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        cfg_path = "/app/config/config.toml"
        if not os.path.exists(cfg_path):
            return _exchange_cutoffs
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        cutoffs = data.get("timing", {}).get("exchange_cutoffs", {})
        for exch, time_str in cutoffs.items():
            parts = time_str.split(":")
            if len(parts) == 2:
                _exchange_cutoffs[exch] = (int(parts[0]), int(parts[1]))
        if _exchange_cutoffs:
            sys.stderr.write(
                "Bridge: loaded exchange cutoffs: {}\n".format(
                    ", ".join(f"{k}={v[0]:02d}:{v[1]:02d}" for k, v in sorted(_exchange_cutoffs.items()))
                )
            )
            sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"Bridge: failed to load exchange cutoffs: {e}\n")
        sys.stderr.flush()
    return _exchange_cutoffs


def _load_symbol_exchange_map():
    """Build symbol -> exchange key mapping from contracts.toml (cached)."""
    global _symbol_exchange_map_loaded, _symbol_exchange_map
    if _symbol_exchange_map_loaded:
        return _symbol_exchange_map
    _symbol_exchange_map_loaded = True
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        contracts_path = "/app/config/contracts.toml"
        if not os.path.exists(contracts_path):
            return _symbol_exchange_map
        with open(contracts_path, "rb") as f:
            data = tomllib.load(f)
        for c in data.get("contracts", []):
            sym = c.get("symbol", "")
            exch = c.get("exchange", "")
            if sym and exch:
                key = _EXCHANGE_KEY_MAP.get(exch, exch)
                _symbol_exchange_map[sym] = key
        if _symbol_exchange_map:
            sys.stderr.write(
                "Bridge: loaded symbol->exchange map ({} symbols)\n".format(len(_symbol_exchange_map))
            )
            sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"Bridge: failed to load symbol->exchange map: {e}\n")
        sys.stderr.flush()
    return _symbol_exchange_map


def _get_exchange_for_symbol(symbol):
    """Determine exchange key for a symbol. Returns None if unknown."""
    # Try contracts.toml mapping first (authoritative)
    exch_map = _load_symbol_exchange_map()
    if symbol in exch_map:
        return exch_map[symbol]
    # Fallback: infer from suffix
    for suffix, exch_key in _SUFFIX_TO_EXCHANGE.items():
        if symbol.endswith(suffix):
            return exch_key
    return None


def _is_in_blackout(ticker_id, timestamp_ns):
    """Check if the current time is past the exchange entry cutoff for this ticker.

    Returns True if in blackout (signal should be suppressed), False otherwise.
    Fail-open: returns False if exchange or cutoff cannot be determined.
    """
    symbol = ticker_symbols.get(ticker_id, "")
    if not symbol:
        return False  # No symbol -> can't determine exchange -> fail-open

    exchange = _get_exchange_for_symbol(symbol)
    if not exchange:
        return False  # Unknown exchange -> fail-open

    cutoffs = _load_exchange_cutoffs()
    cutoff = cutoffs.get(exchange)
    if not cutoff:
        return False  # No cutoff configured -> fail-open

    cutoff_hour, cutoff_minute = cutoff

    if timestamp_ns <= 0:
        return False  # No timestamp -> can't check -> fail-open

    from datetime import datetime, timezone, timedelta
    utc_dt = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc)

    offset_hours = _EXCHANGE_UTC_OFFSET.get(exchange, 0)
    local_dt = utc_dt + timedelta(hours=offset_hours)

    # Past cutoff = blackout
    if local_dt.hour > cutoff_hour or (local_dt.hour == cutoff_hour and local_dt.minute >= cutoff_minute):
        return True

    return False

# Blackout veto logging dedup — avoid logging every tick (once per ticker per day)
_blackout_warned: set = set()
_blackout_warned_date: str = ""

# 5-minute bar aggregation cache — avoid recomputing OHLCV bars on every tick
# when no new complete 5-min bar has formed.
# ticker_id → (last_n_5min_bars, cached_bars_5m_list)
_bar_cache: dict = {}

# Blacklist logging dedup — tracks which symbols have already been logged
# as blacklisted (reset daily to catch config changes across sessions).
_blacklist_warned: set = set()
_blacklist_warned_date: str = ""

# ============================================================================
# Gate veto logging — tracks WHY signals were suppressed and what WOULD have happened
# Logged to stderr as GATE_VETO lines, also written to /app/data/gate_vetoes.ndjson
# for Ouroboros missed-winner analysis.
# ============================================================================
_gate_veto_log_path = "/app/data/gate_vetoes.ndjson"
_gate_veto_counts = {}  # (ticker_id, gate_name) → count (rate limit logging)

def _log_gate_veto(ticker_id, gate_name, price, indicators, reason_detail=""):
    """Log a gate veto with full indicator context for missed-winner analysis."""
    key = (ticker_id, gate_name)
    _gate_veto_counts[key] = _gate_veto_counts.get(key, 0) + 1
    count = _gate_veto_counts[key]

    record = {
        "ts": time.time(),
        "ticker_id": ticker_id,
        "symbol": ticker_symbols.get(ticker_id, "?"),
        "gate": gate_name,
        "price": round(price, 4),
        "detail": reason_detail,
        "indicators": {k: round(v, 4) if isinstance(v, float) else v for k, v in indicators.items()},
    }

    try:
        with open(_gate_veto_log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except (ValueError, KeyError, TypeError, IOError, json.JSONDecodeError, OSError) as e:
        # Log file write failure is non-fatal — default stance: VETO still applies.
        # Only log the first error to avoid spam.
        if not getattr(_log_gate_veto, '_err_logged', False):
            sys.stderr.write(f"Bridge: gate veto log write failed: {e}\n")
            sys.stderr.flush()
            _log_gate_veto._err_logged = True

    if count <= 3 or count % 100 == 0:
        sym = ticker_symbols.get(ticker_id, str(ticker_id))
        sys.stderr.write(
            "GATE_VETO: {} gate={} price={:.4f} {} "
            "hurst={} adx={} rvol={} vol_slope={} "
            "(#{})\n".format(
                sym, gate_name, price, reason_detail,
                indicators.get("hurst", "?"), indicators.get("adx", "?"),
                indicators.get("rvol", "?"), indicators.get("vol_slope", "?"),
                count))
        sys.stderr.flush()

# ============================================================================
# Phase E: Adaptive confidence floor from dynamic_weights.toml
# ============================================================================

_adaptive_floor_loaded = False
_adaptive_confidence_floor = None
_indicator_gates_loaded = False
_indicator_gates = []  # list of {"indicator", "direction", "threshold"}
_ticker_blacklist_loaded = False
_ticker_blacklist = set()  # Set of symbols to reject signals for


def _load_ticker_blacklist():
    """Load ticker blacklist from BOTH config.toml [blacklist] AND dynamic_weights.toml.

    Two sources merged:
      1. config.toml [blacklist].tickers — static, operator-curated from backtest evidence
      2. dynamic_weights.toml [ticker_blacklist].tickers — adaptive, Ouroboros-generated
    """
    global _ticker_blacklist_loaded, _ticker_blacklist
    if _ticker_blacklist_loaded:
        return _ticker_blacklist
    _ticker_blacklist_loaded = True
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

        combined = set()

        # Source 1: Static blacklist from config.toml
        cfg_path = "/app/config/config.toml"
        if os.path.exists(cfg_path):
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
            static_bl = cfg.get("blacklist", {}).get("tickers", [])
            combined.update(static_bl)

        # Source 2: Adaptive blacklist from dynamic_weights.toml
        dw_path = "/app/config/dynamic_weights.toml"
        if os.path.exists(dw_path):
            with open(dw_path, "rb") as f:
                dw = tomllib.load(f)
            adaptive_bl = dw.get("ticker_blacklist", {}).get("tickers", [])
            combined.update(adaptive_bl)

        if combined:
            _ticker_blacklist = combined
            sys.stderr.write(
                "Bridge: loaded ticker blacklist ({} tickers): {}\n".format(
                    len(combined), ", ".join(sorted(combined)[:10])
                )
            )
            sys.stderr.flush()
        return _ticker_blacklist
    except Exception:
        return _ticker_blacklist


def _load_indicator_gates():
    """Load indicator gates from dynamic_weights.toml if available."""
    global _indicator_gates_loaded, _indicator_gates
    if _indicator_gates_loaded:
        return _indicator_gates
    _indicator_gates_loaded = True
    dw_path = "/app/config/dynamic_weights.toml"
    if not os.path.exists(dw_path):
        return []
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(dw_path, "rb") as f:
            data = tomllib.load(f)
        gates_section = data.get("indicator_gates", {})
        rules = gates_section.get("rules", [])
        if rules:
            _indicator_gates = rules
            sys.stderr.write(f"Bridge: loaded {len(rules)} indicator gates from dynamic_weights.toml\n")
            sys.stderr.flush()
        return _indicator_gates
    except Exception:
        return []


def _load_adaptive_floor():
    """Load confidence floor from dynamic_weights.toml if available."""
    global _adaptive_floor_loaded, _adaptive_confidence_floor
    if _adaptive_floor_loaded:
        return _adaptive_confidence_floor
    _adaptive_floor_loaded = True
    dw_path = "/app/config/dynamic_weights.toml"
    if not os.path.exists(dw_path):
        return None
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(dw_path, "rb") as f:
            data = tomllib.load(f)
        floor = data.get("signal", {}).get("confidence_floor")
        if floor is not None:
            _adaptive_confidence_floor = float(floor)
            sys.stderr.write(f"Bridge: loaded adaptive confidence_floor={floor} from dynamic_weights.toml\n")
            sys.stderr.flush()
        return _adaptive_confidence_floor
    except Exception:
        return None


# ============================================================================
# Plan 1 Phase 3: Adaptive parameter loaders from dynamic_weights.toml
# ============================================================================

_adaptive_params_loaded = False
_adaptive_chandelier_atr = None
_adaptive_spread_veto = None
_adaptive_entry_weights = {}
_adaptive_exchange_weights = {}
_adaptive_kelly_cap = None
_adaptive_entry_confidence = {}  # Thompson Sampler per-type confidence floors


def _load_adaptive_params():
    """Load all Plan 1 Phase 3 adaptive parameters from dynamic_weights.toml.

    Loaded once at first tick and cached for process lifetime.
    Sections: [adaptive_chandelier], [adaptive_spread], [adaptive_entry_weights],
              [adaptive_exchange_weights], [adaptive_kelly].
    """
    global _adaptive_params_loaded
    global _adaptive_chandelier_atr, _adaptive_spread_veto
    global _adaptive_entry_weights, _adaptive_exchange_weights, _adaptive_kelly_cap
    global _adaptive_entry_confidence
    if _adaptive_params_loaded:
        return
    _adaptive_params_loaded = True

    dw_path = "/app/config/dynamic_weights.toml"
    if not os.path.exists(dw_path):
        return

    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(dw_path, "rb") as f:
            data = tomllib.load(f)

        # Adaptive Chandelier ATR multiplier
        ac = data.get("adaptive_chandelier", {})
        if "atr_mult" in ac:
            _adaptive_chandelier_atr = float(ac["atr_mult"])

        # Adaptive spread veto threshold
        asp = data.get("adaptive_spread", {})
        if "spread_veto_pct" in asp:
            _adaptive_spread_veto = float(asp["spread_veto_pct"])

        # Adaptive entry type weights
        aew = data.get("adaptive_entry_weights", {})
        for etype in ["TypeA", "TypeB", "TypeC", "TypeD"]:
            if etype in aew:
                _adaptive_entry_weights[etype] = float(aew[etype])

        # Adaptive per-exchange session weights
        axw = data.get("adaptive_exchange_weights", {})
        for exch, weight in axw.items():
            _adaptive_exchange_weights[exch] = float(weight)

        # BT-005: Fallback — if dynamic_weights.toml has no adaptive exchange
        # weights (first boot, or nightly had no per-exchange data), load the
        # static baseline from config.toml [position.exchange_sizing_weights].
        if not _adaptive_exchange_weights:
            cfg_path = "/app/config/config.toml"
            if os.path.exists(cfg_path):
                with open(cfg_path, "rb") as f:
                    cfg = tomllib.load(f)
                static_ew = cfg.get("position", {}).get("exchange_sizing_weights", {})
                for exch, weight in static_ew.items():
                    _adaptive_exchange_weights[exch] = float(weight)
                if _adaptive_exchange_weights:
                    sys.stderr.write(
                        "Bridge: exchange weights fallback from config.toml "
                        "[position.exchange_sizing_weights]: {}\n".format(_adaptive_exchange_weights)
                    )
                    sys.stderr.flush()

        # Adaptive Kelly cap
        ak = data.get("adaptive_kelly", {})
        if "kelly_cap" in ak:
            _adaptive_kelly_cap = float(ak["kelly_cap"])

        # Thompson Sampler per-type confidence floors
        aec = data.get("adaptive_entry_confidence", {})
        _type_field_map = {
            "type_a_confidence": "TypeA",
            "type_b_confidence": "TypeB",
            "type_c_confidence": "TypeC",
            "type_d_confidence": "TypeD",
        }
        for field, etype in _type_field_map.items():
            if field in aec:
                _adaptive_entry_confidence[etype] = float(aec[field])

        parts = []
        if _adaptive_chandelier_atr is not None:
            parts.append(f"chandelier_atr={_adaptive_chandelier_atr:.2f}")
        if _adaptive_spread_veto is not None:
            parts.append(f"spread_veto={_adaptive_spread_veto*100:.1f}%")
        if _adaptive_entry_weights:
            parts.append(f"entry_weights={_adaptive_entry_weights}")
        if _adaptive_exchange_weights:
            parts.append(f"exchange_weights={_adaptive_exchange_weights}")
        if _adaptive_kelly_cap is not None:
            parts.append(f"kelly_cap={_adaptive_kelly_cap:.2f}")
        if _adaptive_entry_confidence:
            parts.append(f"entry_confidence={_adaptive_entry_confidence}")
        if parts:
            sys.stderr.write(f"Bridge: loaded adaptive params: {', '.join(parts)}\n")
            sys.stderr.flush()

    except Exception as e:
        sys.stderr.write(f"Bridge: failed to load adaptive params: {e}\n")
        sys.stderr.flush()


# ============================================================================
# BT-004: Hour-of-day confidence weight loader from config.toml
# Lazy-loaded, cached for process lifetime (same pattern as _load_adaptive_floor).
# Reads [timing.hour_weights] → {"00": 1.0, "01": 0.7, ...}
# At runtime also checks [adaptive_hour_weights] in dynamic_weights.toml;
# adaptive values (from nightly rolling WR) override static if present.
# ============================================================================
_hour_weights_loaded = False
_hour_weights = {}  # UTC hour (int 0-23) → confidence multiplier (float)

# Per-ticker RVOL history for TypeB 3-bar rising detection
_rvol_history = {}  # ticker_id → deque(maxlen=3) of RVOL values

# Entry type config thresholds (loaded once from config.toml)
_entry_type_cfg_loaded = False
_entry_type_cfg = {}


def _load_entry_type_cfg():
    """Load TypeA-F thresholds from config.toml [entry_types]."""
    global _entry_type_cfg_loaded, _entry_type_cfg
    if _entry_type_cfg_loaded:
        return _entry_type_cfg
    _entry_type_cfg_loaded = True
    defaults = {
        "type_a_rsi_oversold": 30.0, "type_a_volume_spike_mult": 2.5, "type_a_drop_atr_mult": 2.5,
        "type_b_rsi_low": 30.0, "type_b_rsi_high": 70.0, "type_b_momentum_bars": 3,
        "type_c_rsi_overbought": 80.0,
        "type_d_price_proximity_pct": 0.5, "type_d_rsi_low": 25.0, "type_d_rsi_high": 35.0,
        "type_e_ibs_threshold": 0.10, "type_e_rvol_threshold": 1.0,
        "type_f_obv_rsi_threshold": 30.0, "type_f_rvol_threshold": 0.7,
    }
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        cfg_path = "/app/config/config.toml"
        if not os.path.exists(cfg_path):
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "config.toml")
        if os.path.exists(cfg_path):
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
            et = cfg.get("entry_types", {})
            for k in defaults:
                if k in et:
                    defaults[k] = float(et[k])
    except Exception:
        pass
    _entry_type_cfg = defaults
    return defaults


def classify_entry_type(rsi_14, ibs, rvol, ticker_id, prices, volumes, vol_div):
    """Classify signal into TypeA-F matching Rust entry_engine.rs logic.

    Uses indicators already computed by bridge.py. Returns the entry type string.
    Priority order: F, E, B, C, A, D (highest-WR first from backtest).
    """
    from collections import deque
    cfg = _load_entry_type_cfg()

    # Track RVOL history for TypeB (3-bar rising detection)
    if ticker_id not in _rvol_history:
        _rvol_history[ticker_id] = deque(maxlen=3)
    _rvol_history[ticker_id].append(rvol)

    # TypeF (OBVDivergence): OBV-RSI(5) < 30 + RVOL > 0.7
    # Simplified: use volume_divergence as proxy for OBV-RSI declining
    if vol_div < -0.5 and rvol > cfg["type_f_rvol_threshold"]:
        return "TypeF"

    # TypeE (IBSMeanReversion): IBS < 0.10 + RVOL > 1.0
    if ibs < cfg["type_e_ibs_threshold"] and rvol > cfg["type_e_rvol_threshold"]:
        return "TypeE"

    # TypeB (EarlyRunner): RVOL rising for 3 consecutive bars + RSI in [30, 70]
    rvol_hist = list(_rvol_history[ticker_id])
    if (len(rvol_hist) >= 3
            and rvol_hist[-3] < rvol_hist[-2] < rvol_hist[-1]
            and rsi_14 is not None
            and cfg["type_b_rsi_low"] <= rsi_14 <= cfg["type_b_rsi_high"]):
        return "TypeB"

    # TypeC (OverboughtFade): RSI > 80 + price rising + volume declining
    if (rsi_14 is not None and rsi_14 > cfg["type_c_rsi_overbought"]
            and len(prices) >= 2 and prices[-1] > prices[-2]
            and len(volumes) >= 2 and volumes[-1] < volumes[-2]):
        return "TypeC"

    # TypeA (DipRecovery): RSI < 30 + RVOL > vol_ma20 * 2.5
    # Simplified: use RVOL > spike_mult as proxy (vol_ma20 normalization already in RVOL)
    if rsi_14 is not None and rsi_14 < cfg["type_a_rsi_oversold"] and rvol > cfg["type_a_volume_spike_mult"]:
        return "TypeA"

    # TypeD (SupportBounce): price within 0.5% of daily low + RSI 25-35
    if (rsi_14 is not None
            and cfg["type_d_rsi_low"] <= rsi_14 <= cfg["type_d_rsi_high"]
            and len(prices) >= 10):
        daily_low = min(prices[-min(len(prices), 100):])  # Approximate daily low from recent prices
        if daily_low > 0:
            pct_above = ((prices[-1] - daily_low) / daily_low) * 100
            if pct_above <= cfg["type_d_price_proximity_pct"]:
                return "TypeD"

    return "Unclassified"


def _load_hour_weights():
    """Load hour-of-day confidence weights from config.toml [timing.hour_weights].

    Falls back to dynamic_weights.toml [adaptive_hour_weights] if present
    (adaptive overrides static). Loaded once and cached for process lifetime.
    """
    global _hour_weights_loaded, _hour_weights
    if _hour_weights_loaded:
        return _hour_weights
    _hour_weights_loaded = True

    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

        # Source 1: Static hour weights from config.toml
        cfg_path = "/app/config/config.toml"
        if os.path.exists(cfg_path):
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
            hw = cfg.get("timing", {}).get("hour_weights", {})
            for hour_str, weight in hw.items():
                try:
                    _hour_weights[int(hour_str)] = float(weight)
                except (ValueError, TypeError):
                    continue

        # Source 2: Adaptive hour weights from dynamic_weights.toml (override static)
        dw_path = "/app/config/dynamic_weights.toml"
        if os.path.exists(dw_path):
            with open(dw_path, "rb") as f:
                dw = tomllib.load(f)
            ahw = dw.get("adaptive_hour_weights", {})
            for hour_str, weight in ahw.items():
                try:
                    _hour_weights[int(hour_str)] = float(weight)
                except (ValueError, TypeError):
                    continue

        if _hour_weights:
            sys.stderr.write(
                "Bridge: loaded hour weights ({} hours, range {:.2f}-{:.2f})\n".format(
                    len(_hour_weights),
                    min(_hour_weights.values()),
                    max(_hour_weights.values()),
                )
            )
            sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"Bridge: failed to load hour weights: {e}\n")
        sys.stderr.flush()

    return _hour_weights


# ============================================================================
# Strategies.toml loader (lazy, once at first tick)
# ============================================================================

_strategies_config_cache = None
_strategies_config_loaded = False


def _load_strategies_config():
    """Load strategy configs from /app/config/strategies.toml.

    Returns a list of StrategyConfig. Loaded once and cached for the process lifetime.
    Falls back to empty list on error (orchestrator simply produces no intents).
    """
    global _strategies_config_cache, _strategies_config_loaded
    if _strategies_config_loaded:
        return _strategies_config_cache or []

    _strategies_config_loaded = True

    strategies_path = "/app/config/strategies.toml"
    if not os.path.exists(strategies_path):
        sys.stderr.write(f"Bridge: strategies.toml not found at {strategies_path}\n")
        sys.stderr.flush()
        _strategies_config_cache = []
        return []

    try:
        # Python 3.11+ has tomllib built-in; 3.10 and below need tomli
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

        with open(strategies_path, "rb") as f:
            data = tomllib.load(f)

        configs = []
        strategy_section = data.get("strategy", {})

        for name, section in strategy_section.items():
            if not isinstance(section, dict):
                continue

            # Skip adaptive_ranges sub-tables
            if name.endswith("adaptive_ranges"):
                continue

            family_str = section.get("family", "mean_reversion")
            family = (
                StrategyFamily.MOMENTUM
                if family_str == "momentum"
                else StrategyFamily.MEAN_REVERSION
            )

            # Collect all non-structural keys as params
            structural_keys = {
                "enabled", "priority", "family", "base_confidence",
                "session_eligible", "session_blocked", "regime_eligible",
                "regime_blocked", "sizing_mult", "ticker_whitelist",
                "ticker_preferred", "adaptive_ranges",
            }
            params = {
                k: v for k, v in section.items()
                if k not in structural_keys and not isinstance(v, dict)
            }

            configs.append(StrategyConfig(
                name=name,
                enabled=section.get("enabled", True),
                priority=section.get("priority", 5),
                family=family,
                base_confidence=section.get("base_confidence", 65.0),
                session_eligible=section.get("session_eligible", []),
                session_blocked=section.get("session_blocked", []),
                regime_eligible=section.get("regime_eligible", []),
                regime_blocked=section.get("regime_blocked", []),
                sizing_mult=section.get("sizing_mult", 1.0),
                ticker_whitelist=section.get("ticker_whitelist", []),
                ticker_preferred=section.get("ticker_preferred", []),
                params=params,
            ))

        _strategies_config_cache = configs
        sys.stderr.write(
            f"Bridge: loaded {len(configs)} strategy configs from strategies.toml "
            f"({[c.name for c in configs]})\n"
        )
        sys.stderr.flush()
        return configs

    except Exception as e:
        sys.stderr.write(f"Bridge: failed to load strategies.toml: {e}\n")
        sys.stderr.flush()
        _strategies_config_cache = []
        return []


# ============================================================================
# Ticker ranking loader (from strategies.toml [ticker_ranking.current])
# ============================================================================

_ticker_rankings = None


def _load_ticker_rankings():
    """Load ticker priority scores from strategies.toml [ticker_ranking.current].

    Returns dict of symbol -> score. Cached for process lifetime (Ouroboros
    refreshes every 2 hours; bridge restarts pick up new rankings).
    """
    global _ticker_rankings
    if _ticker_rankings is not None:
        return _ticker_rankings

    strategies_path = "/app/config/strategies.toml"
    if not os.path.exists(strategies_path):
        _ticker_rankings = {}
        return _ticker_rankings

    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

        with open(strategies_path, "rb") as f:
            data = tomllib.load(f)

        rankings = data.get("ticker_ranking", {}).get("current", {})
        _ticker_rankings = {str(k): float(v) for k, v in rankings.items()}
        return _ticker_rankings

    except Exception:
        _ticker_rankings = {}
        return _ticker_rankings


# ============================================================================
# Helper: compute ADX from bar history (reuse VanguardSniper's logic)
# ============================================================================

def _compute_adx(ticks, period=14):
    """Compute current ADX from tick history. Returns float or 20.0 on error."""
    import numpy as np
    n = len(ticks)
    if n < period + 2:
        return 20.0

    closes = np.array([t["last"] for t in ticks], dtype=np.float64)
    highs = np.array(
        [max(t["last"], t.get("high", t["last"])) for t in ticks], dtype=np.float64
    )
    lows = np.array(
        [min(t["last"], t.get("low", t["last"])) for t in ticks], dtype=np.float64
    )

    # Same ADX implementation as vanguard_sniper._adx
    high_low = highs[1:] - lows[1:]
    high_close = np.abs(highs[1:] - closes[:-1])
    low_close = np.abs(lows[1:] - closes[:-1])
    tr = np.maximum(high_low, np.maximum(high_close, low_close))

    up_move = highs[1:] - highs[:-1]
    down_move = lows[:-1] - lows[1:]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr = np.empty(len(tr), dtype=np.float64)
    smooth_plus = np.empty(len(tr), dtype=np.float64)
    smooth_minus = np.empty(len(tr), dtype=np.float64)
    atr[0] = tr[0]
    smooth_plus[0] = plus_dm[0]
    smooth_minus[0] = minus_dm[0]
    for i in range(1, len(tr)):
        atr[i] = atr[i - 1] - atr[i - 1] / period + tr[i]
        smooth_plus[i] = smooth_plus[i - 1] - smooth_plus[i - 1] / period + plus_dm[i]
        smooth_minus[i] = smooth_minus[i - 1] - smooth_minus[i - 1] / period + minus_dm[i]

    safe_atr = np.where(atr == 0, 1e-9, atr)
    plus_di = 100.0 * smooth_plus / safe_atr
    minus_di = 100.0 * smooth_minus / safe_atr
    di_sum = plus_di + minus_di
    safe_di_sum = np.where(di_sum == 0, 1e-9, di_sum)
    dx = 100.0 * np.abs(plus_di - minus_di) / safe_di_sum

    adx_arr = np.empty(len(dx), dtype=np.float64)
    adx_arr[0] = dx[0]
    for i in range(1, len(dx)):
        adx_arr[i] = adx_arr[i - 1] + (dx[i] - adx_arr[i - 1]) / period

    val = float(adx_arr[-1])
    if math.isnan(val):
        return 20.0
    return val


# ============================================================================
# Orchestrator evaluation on each tick
# ============================================================================

def _evaluate_orchestrator(msg, ticks, rvol, hurst, hurst_regime, adx):
    """Run the autonomous orchestrator on the current tick data.

    Returns the best TradeIntent, or None if no opportunities found.
    """
    ticker_id = msg["ticker_id"]
    symbol = ticker_symbols.get(ticker_id, f"TICKER_{ticker_id}")

    # Load strategy configs (cached after first call)
    strategies = _load_strategies_config()
    if not strategies:
        return None

    # Load ticker rankings (cached)
    rankings = _load_ticker_rankings()
    priority_score = rankings.get(symbol, 50.0)

    # Compute spread in basis points
    last_price = msg["last"]
    bid = msg.get("bid", last_price)
    ask = msg.get("ask", last_price)
    spread_bps = 0.0
    if last_price > 0:
        spread_bps = ((ask - bid) / last_price) * 10000.0

    # Compute RSI(2) and RSI(14) from close prices
    prices = [t["last"] for t in ticks]
    rsi_2 = calculate_rsi(prices, period=2)
    if rsi_2 is None:
        rsi_2 = 50.0
    rsi_14 = calculate_rsi(prices, period=14)  # For TypeA-F classification

    # Compute IBS from latest bar
    latest = ticks[-1]
    high = latest.get("high", latest["last"])
    low = latest.get("low", latest["last"])
    ibs = calculate_ibs(high, low, latest["last"])
    if ibs is None:
        ibs = 0.5

    # VWAP: update calculator and get sigma position + slope + volume profile
    vwap_calc = vwap_calculators[ticker_id]
    vwap_bar = VWAPBar(
        high=high,
        low=low,
        close=latest["last"],
        volume=float(latest.get("volume", 0)),
    )
    vwap_result = vwap_calc.update(vwap_bar)
    vwap_sigma = 0.0
    vwap_slope = 0.0
    volume_profile = "normal"
    vwap_price = latest["last"]
    if vwap_result is not None:
        vwap_sigma = vwap_result.sigma_position
        vwap_slope = vwap_result.slope
        volume_profile = vwap_result.volume_profile
        vwap_price = vwap_result.vwap

    # SMA-200 and SMA-5 (from available bar history, not daily bars)
    sma_200 = calculate_sma(prices, 200) or 0.0
    sma_5 = calculate_sma(prices, 5) or 0.0

    # Gap detection: use first bar in history as "daily open" proxy
    daily_open = ticks[0]["last"] if ticks else last_price
    prev_close = daily_open  # No true prev_close in intraday; use daily_open as proxy
    gap_pct = msg.get("gap_pct", 0.0)

    # ATR from recent bars (simple approximation)
    if len(ticks) >= 2:
        ranges = [
            max(t.get("high", t["last"]), t["last"]) - min(t.get("low", t["last"]), t["last"])
            for t in ticks[-min(14, len(ticks)):]
        ]
        atr = sum(ranges) / len(ranges) if ranges else 0.0
    else:
        atr = 0.0

    # Build TickerState
    ticker_state = TickerState(
        ticker_id=ticker_id,
        symbol=symbol,
        last_price=last_price,
        bid=bid,
        ask=ask,
        spread_bps=spread_bps,
        volume=msg.get("volume", 0),
        rvol=rvol,
        atr=atr,
        rsi_2=rsi_2,
        ibs=ibs,
        vwap=vwap_price,
        vwap_sigma=vwap_sigma,
        vwap_slope=vwap_slope,
        adx=adx,
        hurst=hurst,
        sma_200=sma_200,
        sma_5=sma_5,
        daily_open=daily_open,
        prev_close=prev_close,
        gap_pct=gap_pct,
        volume_profile=volume_profile,
        leverage=msg.get("leverage", 3),
        is_inverse=False,
        priority_score=priority_score,
    )

    # Build MarketContext
    # London time from time_fraction (fraction of trading day 0.0-1.0)
    # time_fraction=0.0 → 08:00 London, time_fraction=1.0 → 16:30 London
    # Convert: london_time_secs = 8*3600 + time_fraction * 8.5 * 3600
    time_fraction = msg.get("time_fraction", 0.5)
    london_time_secs = int(8 * 3600 + time_fraction * 8.5 * 3600)

    # Map hurst_regime string to RegimeType enum
    if hurst_regime == "trending":
        regime = RegimeType.TRENDING
    elif hurst_regime == "mean_reverting":
        regime = RegimeType.MEAN_REVERTING
    else:
        regime = RegimeType.RANDOM

    market_ctx = MarketContext(
        london_time_secs=london_time_secs,
        regime=regime,
        vix=msg.get("vix", 20.0),
        spy_first_30min_return=msg.get("spy_first_30min_return", 0.0),
        nq_overnight_change=msg.get("nq_overnight_change", 0.0),
        broad_market_at_lows=msg.get("broad_market_at_lows", False),
        has_news_catalyst=msg.get("has_news_catalyst", False),
        spx_126d_return=msg.get("spx_126d_return", 0.05),
    )

    # Run orchestrator
    intents = orchestrate(
        tickers=[ticker_state],
        ctx=market_ctx,
        strategies=strategies,
        max_intents=3,
    )

    if not intents:
        return None

    # Return the best intent (highest combined_score)
    return intents[0]


def _compute_indicators(ticker_id, ticks, msg):
    """Stage 1: Compute all indicators from tick data. Returns a dict."""
    # Aggregate into 5-minute OHLCV bars (cached)
    BARS_PER_5MIN = 60
    n_5min_bars = len(ticks) // BARS_PER_5MIN
    cached = _bar_cache.get(ticker_id)
    if cached and cached[0] == n_5min_bars:
        bars_5m = cached[1]
    else:
        bars_5m = []
        for i in range(n_5min_bars):
            chunk = ticks[i * BARS_PER_5MIN : (i + 1) * BARS_PER_5MIN]
            bars_5m.append({
                "open": chunk[0]["last"], "high": max(t["last"] for t in chunk),
                "low": min(t["last"] for t in chunk), "close": chunk[-1]["last"],
                "volume": sum(t["volume"] for t in chunk), "last": chunk[-1]["last"],
            })
        _bar_cache[ticker_id] = (n_5min_bars, bars_5m)

    # Compute indicators on 5-MINUTE bars (preferred) or raw ticks (fallback)
    if bars_5m:
        prices_5m = [b["close"] for b in bars_5m]
        volumes_5m = [b["volume"] for b in bars_5m]
        rvol = calculate_rvol(volumes_5m, window=20) if len(volumes_5m) >= 20 else 1.0
        hurst = estimate_hurst(prices_5m, max_lag=min(20, len(prices_5m) - 1)) if len(prices_5m) >= 5 else 0.5
        vol_div = volume_divergence(prices_5m, volumes_5m, window=10) if len(prices_5m) >= 10 else 0.0
        adx = _compute_adx([{"last": b["close"], "high": b["high"], "low": b["low"], "volume": b["volume"]} for b in bars_5m])
    else:
        volumes = [t["volume"] for t in ticks]
        prices = [t["last"] for t in ticks]
        rvol = calculate_rvol(volumes, window=20)
        hurst = estimate_hurst(prices, max_lag=20)
        vol_div = volume_divergence(prices, volumes, window=10)
        adx = _compute_adx(ticks)
    hurst_regime = classify_regime(hurst)

    # Volume trend slope
    vol_slope = 0.0
    if len(bars_5m) >= 5:
        recent_vols = [b["volume"] for b in bars_5m[-10:]]
        if len(recent_vols) >= 3:
            n = len(recent_vols)
            x_mean = (n - 1) / 2.0
            y_mean = sum(recent_vols) / n
            num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(recent_vols))
            den = sum((i - x_mean) ** 2 for i in range(n))
            vol_slope = num / den if den > 0 else 0.0

    # VPIN (shadow — never gates)
    vpin = 0.0
    if bars_5m and len(bars_5m) >= 20:
        try:
            bc = [b.get("close", b.get("last", 0)) for b in bars_5m[-50:]]
            bv = [b.get("volume", 0) for b in bars_5m[-50:]]
            buy_v, sell_v = classify_volume_bvc(bc, bv)
            vpin = calculate_vpin(buy_v, sell_v, n_buckets=min(20, len(bc)))
        except Exception:
            pass

    # IBS from latest tick
    latest = ticks[-1]
    high = latest.get("high", latest["last"])
    low = latest.get("low", latest["last"])
    ibs = calculate_ibs(high, low, latest["last"])
    if ibs is None:
        ibs = 0.5

    # VWAP update
    vwap_calc = vwap_calculators[ticker_id]
    vwap_bar = VWAPBar(high=high, low=low, close=latest["last"], volume=float(latest.get("volume", 0)))
    vwap_result = vwap_calc.update(vwap_bar)
    vwap_sigma, vwap_slope, vwap_price = 0.0, 0.0, latest["last"]
    if vwap_result is not None:
        vwap_sigma = vwap_result.sigma_position
        vwap_slope = vwap_result.slope
        vwap_price = vwap_result.vwap

    # VWAP distance
    bid = msg.get("bid", 0)
    ask = msg.get("ask", 0)
    spread_pct = ((ask - bid) / ((ask + bid) / 2) * 100) if bid > 0 and ask > 0 else 0
    vwap_dist_pct = 0.0
    vc = vwap_calculators.get(ticker_id)
    if vc:
        vh = vc.get_history()
        if vh and vh[-1] > 0:
            vwap_dist_pct = (msg["last"] - vh[-1]) / vh[-1] * 100

    # Structural Tradability Score (0-100)
    sts = {}
    if bid > 0 and ask > 0:
        sts["spread"] = max(0, min(25, int(25 - spread_pct * 8.3)))
    else:
        sts["spread"] = 10
    sts["regime_clarity"] = min(25, int(abs(hurst - 0.5) / 0.5 * 25)) if hurst > 0.01 else 0
    vs = (10 if rvol > 1.0 else 5 if rvol > 0.7 else 0) + (10 if vol_slope > 0 else 3 if vol_slope == 0 else 0)
    sts["volume"] = min(20, vs)
    sts["adx_strength"] = 15 if adx >= 30 else 10 if adx >= 20 else 5 if adx >= 12 else 0
    nb = len(ticks)
    sts["data_quality"] = 15 if nb >= 500 else 10 if nb >= 300 else 5 if nb >= 200 else 2
    structural_score = sum(sts.values())

    return {
        "bars_5m": bars_5m, "n_5min_bars": n_5min_bars,
        "rvol": rvol, "hurst": hurst, "hurst_regime": hurst_regime,
        "vol_div": vol_div, "adx": adx, "vol_slope": vol_slope,
        "vpin": vpin, "ibs": ibs,
        "vwap_price": vwap_price, "vwap_sigma": vwap_sigma, "vwap_slope": vwap_slope,
        "vwap_dist_pct": vwap_dist_pct,
        "bid": bid, "ask": ask, "spread_pct": spread_pct,
        "structural_score": structural_score, "sts_components": sts,
    }


def _check_quality_gates(ticker_id, msg, ticks, ind):
    """Stage 2: Quality gates. Returns (pass, reason) tuple."""
    leverage = msg.get("leverage", 1)

    # G1: Spread gate (skip in SIM_MODE)
    if not _SIM_MODE and ind["bid"] > 0 and ind["ask"] > 0:
        base_raw = _adaptive_spread_veto if _adaptive_spread_veto is not None else _cost_model.spread_veto_pct
        base_gate = base_raw * 100
        spread_limit = base_gate * 15.0 if leverage >= 3 else base_gate * 5.0
        if ind["spread_pct"] > spread_limit:
            return False, "spread_too_wide", "spread={:.2f}% > {:.1f}%".format(ind["spread_pct"], spread_limit)

    # G2: VWAP extension (absolute, both directions)
    vc = vwap_calculators.get(ticker_id)
    vh = vc.get_history() if vc else []
    if not _SIM_MODE and vh and len(ticks) > 30:
        lv = vh[-1]
        if lv > 0:
            ext = abs(msg["last"] - lv) / lv * 100
            if ext > 15.0:
                return False, "vwap_extension_5pct", "extension={:.1f}% from VWAP (max 15%)".format(ext)

    # G3: VWAP directional extension (long-only chasing check)
    if not _SIM_MODE and vh and len(ticks) > 60:
        lv = vh[-1]
        if lv > 0:
            vd = (msg["last"] - lv) / lv * 100
            if vd > 10.0:
                return False, "vwap_extension", "price {:.1f}% above VWAP (max 10.0%)".format(vd)

    # G4: Structural tradability minimum
    if not _SIM_MODE and ind["structural_score"] < 15:
        return False, "structural_tradability", "STS={}/100 < 15 minimum".format(ind["structural_score"])

    # G5: Hurst extreme mean-reversion
    if not _SIM_MODE and ind["n_5min_bars"] >= 5 and ind["hurst"] > 0.01 and ind["hurst"] < 0.10:
        return False, "hurst_mean_reverting", "hurst={:.3f} < 0.10".format(ind["hurst"])

    # G6: Ouroboros indicator gates
    gates = [] if _SIM_MODE else _load_indicator_gates()
    indicator_values = {"adx": ind["adx"], "hurst": ind["hurst"], "rvol": ind["rvol"]}
    for gate in gates:
        g_ind = gate.get("indicator", "")
        direction = gate.get("direction", "above")
        threshold = gate.get("threshold", 0)
        val = indicator_values.get(g_ind)
        if val is not None:
            if direction == "above" and val < threshold:
                return False, "indicator_gate", "{} {:.2f} < {:.2f} required".format(g_ind, val, threshold)
            elif direction == "below" and val > threshold:
                return False, "indicator_gate", "{} {:.2f} > {:.2f} limit".format(g_ind, val, threshold)

    return True, None, None


def _compute_confidence_floor(msg, ind):
    """Compute effective confidence floor from leverage, adaptive params, and regime."""
    leverage = msg.get("leverage", 1)
    if leverage >= 5:
        floor = 70
    elif leverage >= 3:
        floor = 50
    else:
        floor = 40

    adaptive_floor = _load_adaptive_floor()
    if adaptive_floor is not None:
        floor = max(floor, adaptive_floor)

    # Weakly mean-reverting → raise floor
    if not _SIM_MODE and ind["n_5min_bars"] >= 5 and 0.01 < ind["hurst"] < 0.50:
        floor = max(floor, 70)

    # Falling volume → raise floor
    has_volume = any(b.get("volume", 0) > 0 for b in ind["bars_5m"][-5:]) if ind["bars_5m"] else False
    if not _SIM_MODE and ind["n_5min_bars"] >= 5 and has_volume and ind["vol_slope"] <= 0:
        floor = max(floor, 75)

    return floor


def _generate_signals(ticker_id, msg, ticks, ind, conf_floor):
    """Stage 3: Generate signals from VanguardSniper + Orchestrator. Returns (vanguard, orchestrator)."""
    hurst, hurst_regime = ind["hurst"], ind["hurst_regime"]
    bars_5m = ind["bars_5m"]
    common_fields = {
        "rvol": ind["rvol"], "hurst": hurst, "hurst_regime": hurst_regime,
        "volume_divergence": ind["vol_div"], "adx": ind["adx"],
        "vol_slope": ind["vol_slope"], "vwap_dist_pct": ind["vwap_dist_pct"],
        "structural_score": ind["structural_score"],
    }

    # Thompson per-type floors
    vanguard_floor, orchestrator_floor = conf_floor, conf_floor
    if _adaptive_entry_confidence:
        ta = _adaptive_entry_confidence.get("TypeA")
        if ta is not None:
            vanguard_floor = max(conf_floor, ta)
        tb = _adaptive_entry_confidence.get("TypeB")
        if tb is not None:
            orchestrator_floor = max(conf_floor, tb)

    # VanguardSniper (momentum, non-mean-reverting regimes)
    vanguard_signal = None
    if hurst < 0.01 or hurst >= 0.20 or hurst_regime in ("trending", "random"):
        eval_ticks = [{"last": b["close"], "high": b["high"], "low": b["low"],
                       "bid": b["close"], "ask": b["close"], "volume": b["volume"]}
                      for b in bars_5m] if bars_5m else ticks
        result = vanguard_evaluate(eval_ticks, confidence_floor=vanguard_floor)
        if result is not None:
            total_trades = msg.get("total_trades", 0)
            kelly = kelly_12factor(
                win_rate_raw=msg.get("win_rate", 0.5), total_trades=total_trades,
                avg_win=msg.get("avg_win", 0.02), avg_loss=msg.get("avg_loss", 0.02),
                leverage_factor=msg.get("leverage", 3), realized_vol_annual=msg.get("realized_vol", 0.30),
                correlation_to_portfolio=msg.get("correlation", 0.0),
                current_drawdown_pct=msg.get("drawdown_pct", 0.0),
                amihud_illiq=msg.get("amihud", 0.0),
                regime=hurst_regime if hurst_regime != "random" else msg.get("regime", "normal"),
                spread_pct=msg.get("spread_pct", 0.1), time_of_day_fraction=msg.get("time_fraction", 0.5),
                confidence=result["confidence"], portfolio_heat_pct=msg.get("heat_pct", 0.0),
                equity=msg.get("equity", 10000.0), price=msg["last"],
            )
            if total_trades < 50:
                pk = result["kelly_fraction"]
                if kelly["kelly_fraction"] < pk:
                    eq = msg.get("equity", 10000.0)
                    kelly["kelly_fraction"] = pk
                    kelly["shares"] = max(int(pk * eq / max(msg["last"], 1e-9)), 1)
            vanguard_signal = {
                "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                "confidence": result["confidence"],
                "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                "strategy": "Momentum", **common_fields,
            }

    # Orchestrator (all regimes)
    orchestrator_signal = None
    if len(ticks) >= 5:
        try:
            intent = _evaluate_orchestrator(msg, ticks, ind["rvol"], hurst, hurst_regime, ind["adx"])
            if intent is not None and intent.confidence < orchestrator_floor:
                intent = None
            if intent is not None:
                direction = "Long" if intent.direction == "long" else "Short"
                ok = min(intent.confidence * intent.sizing_mult / 1000.0, 0.05)
                eq = msg.get("equity", 10000.0)
                pr = max(msg["last"], 1e-9)
                orchestrator_signal = {
                    "type": "signal", "ticker_id": ticker_id, "direction": direction,
                    "confidence": intent.confidence,
                    "kelly_fraction": ok, "shares": max(int(ok * eq / pr), 1) if ok > 0 else 0,
                    "strategy": f"Orchestrator_{intent.strategy_name}", **common_fields,
                }
        except Exception as e:
            sys.stderr.write(f"Bridge: orchestrator error (non-fatal): {e}\n")
            sys.stderr.flush()

    return vanguard_signal, orchestrator_signal


def _apply_adjustments(ticker_id, msg, ind, vanguard_signal, orchestrator_signal):
    """Stage 4: Apply confidence adjustments, select best, classify, size."""
    hurst_regime = ind["hurst_regime"]

    # LSE boost during LSE hours
    from python_brain.ouroboros.contract_loader import load_lse_symbols
    lse_syms = set(load_lse_symbols())
    symbol = ticker_symbols.get(ticker_id, "")
    is_lse = ticker_id < len(lse_syms) or symbol in lse_syms
    if is_lse:
        ls = msg.get("london_time_secs", 0)
        if 28800 <= ls < 59400:  # 08:00-16:30
            if vanguard_signal:
                vanguard_signal["confidence"] = min(vanguard_signal["confidence"] + 20, 100)
            if orchestrator_signal:
                orchestrator_signal["confidence"] = min(orchestrator_signal["confidence"] + 20, 100)

    # Drawdown regime filter
    dd = msg.get("drawdown_pct", 0.0)
    is_inverse = (symbol.startswith("3S") or symbol.startswith("5S") or
                  (symbol.endswith("S.L") and len(symbol) <= 7) or
                  symbol in ("QQQS.L", "3USS.L", "3STS.L", "3SAM.L", "3SNV.L", "3SAP.L", "3SMS.L", "3SEM.L"))
    if dd > 0.02:
        penalty = min(int(dd * 500), 20)
        boost = min(int(dd * 300), 15) if is_inverse else 0
        for sig in (vanguard_signal, orchestrator_signal):
            if sig:
                if is_inverse:
                    sig["confidence"] = min(sig["confidence"] + boost, 100)
                else:
                    sig["confidence"] = max(sig["confidence"] - penalty, 0)

    # Hour-of-day weights
    hw = _load_hour_weights()
    if hw:
        ts_ns = msg.get("timestamp_ns", 0)
        if ts_ns > 0:
            from datetime import datetime as _dt_hw, timezone as _tz_hw
            utc_hour = _dt_hw.fromtimestamp(ts_ns / 1_000_000_000, tz=_tz_hw.utc).hour
            w = hw.get(utc_hour, 1.0)
            if w != 1.0:
                for sig in (vanguard_signal, orchestrator_signal):
                    if sig:
                        sig["confidence"] = max(0, min(100, int(sig["confidence"] * w)))

    # Select best signal
    if vanguard_signal and orchestrator_signal:
        best = orchestrator_signal if orchestrator_signal["confidence"] > vanguard_signal["confidence"] else vanguard_signal
    elif vanguard_signal:
        best = vanguard_signal
    elif orchestrator_signal:
        best = orchestrator_signal
    else:
        return None

    # Per-ticker cooldown
    tick_count = _tick_counts.get(ticker_id, 0)
    if not _SIM_MODE:
        last_sig = _last_signal_tick.get(ticker_id, -SIGNAL_COOLDOWN_TICKS - 1)
        if tick_count - last_sig < SIGNAL_COOLDOWN_TICKS:
            return None  # Cooldown active

    # STS confidence adjustment
    best["strategy_confidence"] = best["confidence"]
    ss = ind["structural_score"]
    if ss > 70:
        best["confidence"] = min(100, best["confidence"] + min(6, (ss - 70) // 5))
    elif ss < 50:
        best["confidence"] = max(0, best["confidence"] - min(4, (50 - ss) // 5))
    best["structural_score"] = ss

    # TypeA-F classification (all variables local — no scope bugs)
    cls_prices = [t["last"] for t in list(bar_history[ticker_id])]
    cls_volumes = [t.get("volume", 0) for t in list(bar_history[ticker_id])]
    cls_rsi = calculate_rsi(cls_prices, period=14) if len(cls_prices) >= 14 else None
    entry_type = classify_entry_type(
        rsi_14=cls_rsi, ibs=ind["ibs"], rvol=ind["rvol"], ticker_id=ticker_id,
        prices=cls_prices, volumes=cls_volumes, vol_div=ind["vol_div"],
    )
    best["entry_type"] = entry_type
    if entry_type != "Unclassified":
        best["strategy"] = entry_type
    best["rsi"] = cls_rsi if cls_rsi is not None else 0.0
    best["ibs"] = ind["ibs"]

    # Adaptive entry type weight → Kelly sizing
    if _adaptive_entry_weights:
        if entry_type in _adaptive_entry_weights:
            ew = _adaptive_entry_weights[entry_type]
            if ew < 1.0:
                best["kelly_fraction"] = best["kelly_fraction"] * ew
                best["shares"] = max(1, int(best["shares"] * ew))
        best["adaptive_entry_weights"] = _adaptive_entry_weights

    if _adaptive_entry_confidence:
        best["adaptive_entry_confidence"] = _adaptive_entry_confidence

    # Adaptive exchange weight → Kelly sizing
    if _adaptive_exchange_weights:
        exchange = msg.get("exchange", "")
        if exchange and exchange in _adaptive_exchange_weights:
            exw = _adaptive_exchange_weights[exchange]
            if exw < 1.0:
                best["kelly_fraction"] = best["kelly_fraction"] * exw
                best["shares"] = max(1, int(best["shares"] * exw))
        best["adaptive_exchange_weights"] = _adaptive_exchange_weights

    # Adaptive Kelly cap (drawdown-aware)
    if _adaptive_kelly_cap is not None and _adaptive_kelly_cap < 0.05:
        if best["kelly_fraction"] > _adaptive_kelly_cap:
            best["kelly_fraction"] = _adaptive_kelly_cap
            eq = msg.get("equity", 10000.0)
            pr = max(msg["last"], 1e-9)
            best["shares"] = max(1, int(_adaptive_kelly_cap * eq / pr))
        best["adaptive_kelly_cap"] = _adaptive_kelly_cap

    # VPIN shadow fields
    best["vpin"] = round(ind["vpin"], 4)
    best["vpin_would_block"] = bool(ind["vpin"] < 0.3 and ind["rvol"] > 2.5)

    # Claude curator (shadow mode, non-blocking)
    if best.get("confidence", 0) >= 55 and not _SIM_MODE:
        try:
            from python_brain.ouroboros.claude_curator import evaluate_signal
            cr = evaluate_signal(
                signal_dict=best,
                market_context={
                    "regime": hurst_regime, "drawdown_pct": msg.get("drawdown_pct", 0),
                    "vix": msg.get("vix", 20), "equity": msg.get("equity", 10000),
                    "exchange": msg.get("exchange", ""), "open_positions": msg.get("open_positions", 0),
                    "trades_today": msg.get("trades_today", 0),
                }
            )
            if cr.get("claude_verdict") == "reject":
                best["claude_rejected"] = True
                best["claude_reasoning"] = cr.get("reasoning", "")[:200]
            elif cr.get("adjusted_confidence"):
                best["claude_adjusted_confidence"] = cr["adjusted_confidence"]
            best["claude_verdict"] = cr.get("claude_verdict", "no_response")
        except Exception as e:
            best["claude_error"] = str(e)[:200]

    # Record cooldown
    _last_signal_tick[ticker_id] = tick_count
    return best


def process_tick(msg):
    """Process a tick message through 5 stages: ingest → indicators → gates → signals → output."""
    ticker_id = msg["ticker_id"]
    _load_adaptive_params()

    # Track symbol mapping
    if "symbol" in msg and msg["symbol"]:
        ticker_symbols[ticker_id] = msg["symbol"]

    # Ingest tick
    bar_history[ticker_id].append({
        "last": msg["last"], "bid": msg.get("bid", msg["last"]),
        "ask": msg.get("ask", msg["last"]), "high": msg.get("high", msg["last"]),
        "low": msg.get("low", msg["last"]), "volume": msg.get("volume", 0),
        "timestamp_ns": msg.get("timestamp_ns", 0),
    })
    ticks = list(bar_history[ticker_id])

    # VWAP session reset on date change
    ts_ns = msg.get("timestamp_ns", 0)
    if ts_ns > 0:
        from datetime import datetime, timezone
        td = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).strftime("%Y-%m-%d")
        prev = _last_vwap_date.get(ticker_id)
        if prev is not None and td != prev:
            vwap_calculators[ticker_id].reset()
        _last_vwap_date[ticker_id] = td

    no_signal = {"type": "no_signal", "ticker_id": ticker_id}

    # Early exit: blackout period
    if not _SIM_MODE:
        if _is_in_blackout(ticker_id, msg.get("timestamp_ns", 0)):
            global _blackout_warned, _blackout_warned_date
            import datetime as _dt_bo
            today = _dt_bo.date.today().isoformat()
            if _blackout_warned_date != today:
                _blackout_warned = set()
                _blackout_warned_date = today
            sym = ticker_symbols.get(ticker_id, str(ticker_id))
            if sym not in _blackout_warned:
                exch = _get_exchange_for_symbol(sym) or "?"
                sys.stderr.write(f"BLACKOUT_VETO: {sym} (tid={ticker_id}) past {exch} entry cutoff\n")
                sys.stderr.flush()
                _blackout_warned.add(sym)
            return no_signal

    # Early exit: blacklist
    blacklist = set() if _SIM_MODE else _load_ticker_blacklist()
    sym = ticker_symbols.get(ticker_id, "")
    if sym and sym in blacklist:
        global _blacklist_warned, _blacklist_warned_date
        import datetime as _dt
        today = _dt.date.today().isoformat()
        if _blacklist_warned_date != today:
            _blacklist_warned = set()
            _blacklist_warned_date = today
        if sym not in _blacklist_warned:
            sys.stderr.write(f"BLACKLIST_VETO: {sym} (tid={ticker_id}) suppressed by Ouroboros blacklist\n")
            sys.stderr.flush()
            _blacklist_warned.add(sym)
        return no_signal

    # Early exit: warmup
    MIN_WARMUP = 10 if _SIM_MODE else 50
    if len(ticks) < MIN_WARMUP:
        return no_signal

    # Diagnostic logging (every 500th tick)
    _tick_counts[ticker_id] = _tick_counts.get(ticker_id, 0) + 1
    if _tick_counts[ticker_id] % 500 == 1:
        sys.stderr.write(f"BRIDGE_DIAG: tid={ticker_id} bars={len(ticks)} price={msg['last']:.4f} vol={msg.get('volume', 0)}\n")
        sys.stderr.flush()

    # Stage 1: Compute indicators
    ind = _compute_indicators(ticker_id, ticks, msg)

    # Build indicator dict for gate veto logging
    _ind = {
        "hurst": ind["hurst"], "adx": ind["adx"], "rvol": ind["rvol"],
        "vol_slope": ind["vol_slope"], "n_5min_bars": ind["n_5min_bars"],
        "n_ticks": len(ticks), "hurst_regime": ind["hurst_regime"],
        "spread_pct": ind["spread_pct"], "vwap_dist_pct": ind["vwap_dist_pct"],
        "bid": ind["bid"], "ask": ind["ask"], "leverage": msg.get("leverage", 1),
        "session_mode": msg.get("session_mode", "?"), "volume": msg.get("volume", 0),
        "vpin": ind["vpin"], "structural_score": ind["structural_score"],
    }

    # Enrich no_signal with indicators for Rust telemetry
    no_signal.update({"rvol": ind["rvol"], "hurst": ind["hurst"],
                      "hurst_regime": ind["hurst_regime"], "volume_divergence": ind["vol_div"]})

    # Stage 2: Quality gates
    gate_pass, gate_name, gate_detail = _check_quality_gates(ticker_id, msg, ticks, ind)
    if not gate_pass:
        _log_gate_veto(ticker_id, gate_name, msg["last"], _ind, gate_detail)
        return no_signal

    # Stage 3: Generate signals
    conf_floor = _compute_confidence_floor(msg, ind)
    vanguard_signal, orchestrator_signal = _generate_signals(ticker_id, msg, ticks, ind, conf_floor)

    # Stage 4: Adjust, select, classify, size
    best = _apply_adjustments(ticker_id, msg, ind, vanguard_signal, orchestrator_signal)

    # Stage 5: Output
    if best:
        return best
    return no_signal


def process_apex_snapshot(msg):
    """Process an Apex snapshot message via ApexScout, return a response dict."""
    ticker_id = msg["ticker_id"]
    snapshots = msg.get("snapshots", [])

    if not snapshots:
        return {"type": "no_signal", "ticker_id": ticker_id}

    result = apex_evaluate(snapshots)

    if result is None:
        return {"type": "no_signal", "ticker_id": ticker_id}

    # Apex signals use preliminary Kelly from the scout (full 12-factor in future).
    return {
        "type": "signal",
        "ticker_id": ticker_id,
        "direction": "Long",
        "confidence": result["confidence"],
        "kelly_fraction": result["kelly_fraction"],
        "shares": 0,  # Apex sizing done by Rust side based on kelly_fraction
        "strategy": "ApexScout",
    }


def main():
    """Main loop: read JSON lines from stdin, write responses to stdout."""
    global _last_heartbeat_time
    sys.stderr.write("Python Brain Bridge: started\n")
    sys.stderr.flush()

    # Write initial heartbeat immediately on startup
    _last_heartbeat_time = time.time()
    try:
        _write_heartbeat({"ticks_processed": 0})
    except Exception:
        pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"Bridge: JSON decode error: {e}\n")
            sys.stderr.flush()
            response = {"type": "error", "message": str(e)}
            print(json.dumps(response), flush=True)
            continue

        msg_type = msg.get("type", "")

        # Periodic heartbeat for watchdog (every 30s)
        now = time.time()
        if now - _last_heartbeat_time >= _HEARTBEAT_INTERVAL:
            _last_heartbeat_time = now
            try:
                _write_heartbeat({"ticks_processed": sum(_tick_counts.values())})
            except Exception:
                pass

        if msg_type == "tick":
            try:
                response = process_tick(msg)
            except Exception as e:
                # FIX 2026-03-11: Return "error" type (not "no_signal") so Rust
                # can distinguish "no trade setup" from "strategy is broken".
                # This prevents silent V1-style rot where a broken strategy
                # looks identical to a quiet market.
                import traceback
                tb = traceback.format_exc()
                sys.stderr.write(f"Bridge: tick processing error: {e}\n{tb}\n")
                sys.stderr.flush()
                response = {
                    "type": "error",
                    "ticker_id": msg.get("ticker_id", -1),
                    "error": f"{type(e).__name__}: {e}",
                }
            print(json.dumps(response), flush=True)

        elif msg_type == "apex_snapshot":
            # P6-I: Apex Scout evaluation for Apex-class tickers (60s OHLCV snapshots).
            try:
                response = process_apex_snapshot(msg)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                sys.stderr.write(f"Bridge: apex processing error: {e}\n{tb}\n")
                sys.stderr.flush()
                response = {
                    "type": "error",
                    "ticker_id": msg.get("ticker_id", -1),
                    "error": f"{type(e).__name__}: {e}",
                }
            print(json.dumps(response), flush=True)

        elif msg_type == "shutdown":
            sys.stderr.write("Python Brain Bridge: shutting down\n")
            sys.stderr.flush()
            break

        else:
            response = {"type": "error", "message": f"unknown type: {msg_type}"}
            print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
