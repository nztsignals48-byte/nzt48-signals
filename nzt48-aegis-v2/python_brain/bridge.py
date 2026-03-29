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

# P5: Per-strategy signal counter for validation tracking.
_strategy_signal_counts = defaultdict(int)
_strategy_total_confidence = defaultdict(float)

# ══════════════════════════════════════════════════════════════════════════════
# THE COMPOUNDING MACHINE
# ══════════════════════════════════════════════════════════════════════════════
# This is the core autonomous feedback loop. It does 5 things:
#   1. TRACK:  Record every entry/exit per strategy
#   2. SCORE:  Compute rolling WR, PF, Sharpe per strategy
#   3. SIZE:   Allocate Kelly proportional to proven edge (not equal weight)
#   4. KILL:   Disable strategies with Sharpe < -1.0 over 30+ trades
#   5. COMPOUND: Track geometric growth rate and log CAGR daily
#
# The machine doesn't care WHICH strategy wins. It finds winners and scales them.
# Losers get killed automatically. No human intervention needed.
# ══════════════════════════════════════════════════════════════════════════════

_auto_killed_strategies = {"S6_Catalyst"}  # Killed by 730-day backtest: 13% WR, PF 0.01 over 554K trades
_strategy_pnl_history = defaultdict(list)
_strategy_entry_prices = {}
_cofire_counts = defaultdict(int)
_cofire_total = defaultdict(int)
_COFIRE_WINDOW_NS = 5 * 60 * 1_000_000_000
_recent_strategy_fires = {}

# Edge-proportional allocation weights (updated on every exit)
_strategy_allocation_weights = {}  # strategy → weight [0.0, 1.0]

def _track_strategy_entry(ticker_id, strategy, price):
    """TRACK: Record entry price for P&L computation."""
    _strategy_entry_prices[(ticker_id, strategy)] = price

def _track_strategy_exit(ticker_id, strategy, exit_price):
    """TRACK + SCORE + SIZE + KILL: The compounding machine core loop."""
    key = (ticker_id, strategy)
    entry = _strategy_entry_prices.pop(key, None)
    if entry is None or entry <= 0:
        return

    ret = (exit_price - entry) / entry
    _strategy_pnl_history[strategy].append(ret)
    if len(_strategy_pnl_history[strategy]) > 200:
        _strategy_pnl_history[strategy] = _strategy_pnl_history[strategy][-200:]

    stats = _strategy_live_stats(strategy)

    # KILL: Disable losers
    if stats["n"] >= 30:
        if stats["sharpe"] < -1.0 and strategy not in _auto_killed_strategies:
            _auto_killed_strategies.add(strategy)
            sys.stderr.write(f"COMPOUND_KILL: {strategy} Sharpe={stats['sharpe']} n={stats['n']}\n")
            sys.stderr.flush()
        elif stats["sharpe"] > -0.3 and strategy in _auto_killed_strategies:
            _auto_killed_strategies.discard(strategy)
            sys.stderr.write(f"COMPOUND_REVIVE: {strategy} Sharpe={stats['sharpe']}\n")
            sys.stderr.flush()

    # SIZE: Recompute edge-proportional allocation weights across ALL strategies.
    # Strategies with higher Sharpe get more capital. Negative Sharpe gets zero.
    all_stats = {s: _strategy_live_stats(s) for s in _strategy_pnl_history if _strategy_live_stats(s)["n"] >= 10}
    total_edge = 0.0
    for s, st in all_stats.items():
        edge = max(st["sharpe"], 0.0)  # Only positive edge counts
        all_stats[s]["edge"] = edge
        total_edge += edge

    if total_edge > 0:
        for s, st in all_stats.items():
            _strategy_allocation_weights[s] = st["edge"] / total_edge
    else:
        # No proven edge anywhere — equal weight for data collection
        n_active = len([s for s in all_stats if s not in _auto_killed_strategies])
        if n_active > 0:
            for s in all_stats:
                _strategy_allocation_weights[s] = 1.0 / n_active

    # Log compound state every 10 exits
    total_exits = sum(len(h) for h in _strategy_pnl_history.values())
    if total_exits % 10 == 0:
        sys.stderr.write(f"COMPOUND_STATE: exits={total_exits} killed={list(_auto_killed_strategies)} "
                         f"weights={{{', '.join(f'{s}:{w:.2f}' for s, w in sorted(_strategy_allocation_weights.items()))}}}\n")
        sys.stderr.flush()

def get_strategy_weight(strategy):
    """SIZE: Get current allocation weight for a strategy [0.0, 1.0].
    Used by _kelly_for to scale Kelly proportional to proven edge."""
    if strategy in _auto_killed_strategies:
        return 0.0
    return _strategy_allocation_weights.get(strategy, 0.5)  # Default 50% until proven

def _strategy_live_sharpe(strategy):
    """Compute rolling Sharpe for a strategy (annualized, assuming 252 trading days)."""
    rets = _strategy_pnl_history.get(strategy, [])
    if len(rets) < 10:
        return 0.0
    mean_r = sum(rets) / len(rets)
    var_r = sum((r - mean_r) ** 2 for r in rets) / len(rets)
    std_r = var_r ** 0.5
    if std_r < 1e-9:
        return 0.0
    # Annualize: assume ~3 trades/day, 252 days
    daily_sharpe = mean_r / std_r
    return daily_sharpe * (252 ** 0.5)

# ── COMPOUNDING METRICS: Daily equity tracking + CAGR computation ──
_daily_equity_snapshots = []  # [(date_str, equity)]
_last_equity_date = ""

def _track_daily_equity(equity):
    """Track daily equity for CAGR computation. Call once per new day."""
    global _last_equity_date
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != _last_equity_date and equity > 0:
        _last_equity_date = today
        _daily_equity_snapshots.append((today, equity))
        if len(_daily_equity_snapshots) > 365:
            _daily_equity_snapshots[:] = _daily_equity_snapshots[-365:]
        if len(_daily_equity_snapshots) >= 2:
            first_eq = _daily_equity_snapshots[0][1]
            latest_eq = equity
            n_days = len(_daily_equity_snapshots)
            if first_eq > 0 and n_days > 1:
                total_return = latest_eq / first_eq
                daily_geo = total_return ** (1.0 / n_days) - 1.0
                annualized_cagr = (1 + daily_geo) ** 252 - 1.0
                max_eq = max(eq for _, eq in _daily_equity_snapshots)
                drawdown = (max_eq - latest_eq) / max_eq * 100.0 if max_eq > 0 else 0
                sys.stderr.write(
                    f"COMPOUNDING: days={n_days} equity=£{latest_eq:.0f} "
                    f"total_return={total_return:.4f} daily_geo={daily_geo*100:.4f}% "
                    f"CAGR={annualized_cagr*100:.1f}% max_dd={drawdown:.1f}%\n"
                )
                sys.stderr.flush()

def _strategy_live_stats(strategy):
    """Get live WR, PF, Sharpe for a strategy."""
    rets = _strategy_pnl_history.get(strategy, [])
    n = len(rets)
    if n < 5:
        return {"n": n, "wr": 0, "pf": 0, "sharpe": 0}
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    wr = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 1e-9
    pf = avg_win / avg_loss if avg_loss > 1e-9 else 99.0
    return {"n": n, "wr": round(wr, 3), "pf": round(pf, 3), "sharpe": round(_strategy_live_sharpe(strategy), 2)}

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

# Startup audit log: strategy enforcement status
sys.stderr.write("BRIDGE_STARTUP: Strategy enforcement: TypeA/D=DISABLED, TypeC/E/F=SHADOW, TypeB/VS=LIVE\n")
sys.stderr.write(f"BRIDGE_STARTUP: SIM_MODE={_SIM_MODE}, COOLDOWN_TICKS={SIGNAL_COOLDOWN_TICKS}\n")
sys.stderr.flush()

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
_symbol_raw_exchange_map = {}  # S6: symbol → raw exchange from contracts.toml (e.g. "LSEETF")

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
    global _symbol_exchange_map_loaded, _symbol_exchange_map, _symbol_raw_exchange_map
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
                _symbol_raw_exchange_map[sym] = exch  # S6: preserve raw exchange for gating
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


_blocked_exchanges_loaded = False
_blocked_exchanges = set()


def _load_blocked_exchanges():
    """S6: Load blocked exchanges from config.toml [blacklist].exchanges.

    Blocks all tickers from specific raw exchanges (e.g. LSEETF leveraged ETPs).
    Uses the raw exchange from contracts.toml, not the mapped key.
    """
    global _blocked_exchanges_loaded, _blocked_exchanges
    if _blocked_exchanges_loaded:
        return _blocked_exchanges
    _blocked_exchanges_loaded = True
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        cfg_path = "/app/config/config.toml"
        if os.path.exists(cfg_path):
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
            blocked = cfg.get("blacklist", {}).get("exchanges", [])
            if blocked:
                _blocked_exchanges = set(blocked)
                sys.stderr.write(
                    f"Bridge: blocked exchanges ({len(blocked)}): {', '.join(sorted(blocked))}\n"
                )
                sys.stderr.flush()
    except Exception:
        pass
    return _blocked_exchanges


# ---------------------------------------------------------------------------
# S8: Regime + Session enforcement from strategy_registry.json
# ---------------------------------------------------------------------------
_strategy_registry_loaded = False
_strategy_registry = {}  # strategy_id → {"regime_allowed", "regime_blocked", "session_allowed", "session_blocked"}


def _load_strategy_registry():
    """Load regime/session metadata from strategy_registry.json (cached)."""
    global _strategy_registry_loaded, _strategy_registry
    if _strategy_registry_loaded:
        return _strategy_registry
    _strategy_registry_loaded = True
    reg_path = "/app/config/strategy_registry.json"
    if not os.path.exists(reg_path):
        return _strategy_registry
    try:
        with open(reg_path) as f:
            data = json.load(f)
        for key, entry in data.get("strategies", {}).items():
            sid = entry.get("id", key)
            _strategy_registry[sid] = {
                "regime_allowed": set(entry.get("regime_allowed", [])),
                "regime_blocked": set(entry.get("regime_blocked", [])),
                "session_allowed": set(entry.get("session_allowed", [])),
                "session_blocked": set(entry.get("session_blocked", [])),
                "status": entry.get("status", "live"),
            }
        sys.stderr.write(f"Bridge: loaded strategy registry ({len(_strategy_registry)} strategies)\n")
        sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"Bridge: failed to load strategy registry: {e}\n")
        sys.stderr.flush()
    return _strategy_registry


def _classify_market_regime(hurst, rvol, adx):
    """Map indicator values to registry regime name.

    Uses the definitions from strategy_registry.json:
      trend_up: Hurst > 0.55, ADX > 20, price above 20-SMA (simplified to Hurst+ADX)
      trend_down: Hurst > 0.55, ADX > 20
      high_vol_trend: Hurst > 0.55, RVOL > 2.0
      high_vol_chop: Hurst < 0.45, RVOL > 2.0
      low_vol_compression: 0.45 <= Hurst <= 0.55, RVOL < 1.0
    """
    if rvol > 2.0 and hurst > 0.55:
        return "high_vol_trend"
    if rvol > 2.0 and hurst < 0.45:
        return "high_vol_chop"
    if hurst > 0.55 and adx > 20:
        return "trend_up"  # Can't distinguish up/down without price vs SMA
    if 0.45 <= hurst <= 0.55 and rvol < 1.0:
        return "low_vol_compression"
    if hurst > 0.55:
        return "trend_up"
    return "low_vol_compression"  # Default: benign regime


def _classify_current_session():
    """Determine current session from UTC time.

    Returns session name matching strategy_registry.json definitions.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    h = now.hour

    if 1 <= h < 8:
        return "asia_main"
    if 8 <= h < 12:
        return "lse_main"
    if 12 <= h < 14:
        return "us_premarket"
    if h == 14 and now.minute < 30:
        return "us_premarket"
    if (h == 14 and now.minute >= 30) or (h == 15 and now.minute < 30):
        return "us_open"
    if 15 <= h < 19:
        return "us_midday"
    if 19 <= h < 21:
        return "us_power_hour"
    if 21 <= h < 23:
        return "us_after_hours"
    return "lse_main"  # Default: benign session


def _check_regime_session_gate(strategy_id, hurst, rvol, adx):
    """S8: Check if strategy is allowed in current regime and session.

    Returns (allowed, reason) tuple.
    """
    registry = _load_strategy_registry()
    entry = registry.get(strategy_id)
    if not entry:
        return True, ""  # Unknown strategy: fail-open

    # Regime check
    regime = _classify_market_regime(hurst, rvol, adx)
    if "ALL" in entry["regime_blocked"]:
        return False, f"regime_blocked=ALL (regime={regime})"
    if regime in entry["regime_blocked"]:
        return False, f"regime_blocked={regime}"
    if entry["regime_allowed"] and regime not in entry["regime_allowed"]:
        return False, f"regime={regime} not in allowed={entry['regime_allowed']}"

    # Session check
    session = _classify_current_session()
    if "ALL" in entry["session_blocked"]:
        return False, f"session_blocked=ALL (session={session})"
    if session in entry["session_blocked"]:
        return False, f"session_blocked={session}"
    # ALL_EXCEPT_* pattern (used by ORB)
    for blocked in entry["session_blocked"]:
        if blocked.startswith("ALL_EXCEPT_"):
            allowed_session = blocked.replace("ALL_EXCEPT_", "").lower()
            if session != allowed_session:
                return False, f"session={session} not in {allowed_session}"

    return True, ""


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

    # TypeB (EarlyRunner): RVOL rising + RSI in [20, 80]
    # S4B: Loosened from 3-bar strictly rising to 2-bar rising. 3-bar was unreachable on 5s bars.
    rvol_hist = list(_rvol_history[ticker_id])
    type_b_rising = (len(rvol_hist) >= 2
                     and rvol_hist[-1] > rvol_hist[-2])
    type_b_rsi_ok = (rsi_14 is not None
                     and cfg["type_b_rsi_low"] <= rsi_14 <= cfg["type_b_rsi_high"])
    if type_b_rising and type_b_rsi_ok:
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

    # Strongly mean-reverting → raise floor (tightened from H<0.50 to H<0.30 for paper validation)
    # H<0.50 was blocking ~80% of signals since random walk (0.45-0.55) was included.
    # Only truly mean-reverting regimes (H<0.30) should raise the floor.
    if not _SIM_MODE and ind["n_5min_bars"] >= 5 and 0.01 < ind["hurst"] < 0.30:
        floor = max(floor, 60)

    # Falling volume → moderate floor raise (tightened from 75 to 60 for paper validation)
    has_volume = any(b.get("volume", 0) > 0 for b in ind["bars_5m"][-5:]) if ind["bars_5m"] else False
    if not _SIM_MODE and ind["n_5min_bars"] >= 5 and has_volume and ind["vol_slope"] < -0.5:
        floor = max(floor, 60)

    return floor


def _system1_microstructure(ticker_id, msg, ticks, ind, conf_floor, kelly_fn, common_fields):
    """System 1: Microstructure Momentum — order flow proxy + intraday momentum.

    6 indicators (Easley-LdP-O'Hara 2012, Gao-Ritter 2010, Chordia-Roll-Subrahmanyam 2002):
    1. TMR: Trade-to-mid ratio — buy/sell pressure
    2. VPIN: Volume-sync informed trading probability
    3. Spread compression: narrowing = competition for liquidity
    4. Tick momentum: net up-ticks vs down-ticks
    5. Volume-weighted price momentum: VWAP slope direction
    6. Amihud illiquidity drop: improving liquidity = institutional interest

    Entry: 4+ of 6 bullish + ADX > 15 + hurst != mean_reverting.
    """
    bid, ask, last = msg["bid"], msg["ask"], msg["last"]
    spread = ask - bid
    if spread <= 0 or bid <= 0:
        return None
    mid = (bid + ask) / 2.0

    # 1. TMR — where trades execute in the spread
    tmr = (last - mid) / spread
    tmr_bullish = tmr > 0.25

    # 2. VPIN — informed trading detection
    vpin = ind.get("vpin", 0.5)
    vpin_bullish = vpin > 0.55

    # 3. Spread compression — current vs rolling average
    spreads = []
    for t in ticks[-30:]:
        tb = t.get("bid", 0) if isinstance(t, dict) else getattr(t, "bid", 0)
        ta = t.get("ask", 0) if isinstance(t, dict) else getattr(t, "ask", 0)
        if tb > 0 and ta > tb:
            spreads.append((ta - tb) / tb)
    if len(spreads) >= 10:
        avg_s = sum(spreads) / len(spreads)
        cur_s = spread / bid
        spread_compressed = cur_s < avg_s * 0.75
    else:
        spread_compressed = False

    # 4. Tick momentum — Lee-Ready (1991) tick test
    prices = []
    for t in ticks[-31:]:
        prices.append(t.get("last", t.get("close", 0)) if isinstance(t, dict) else getattr(t, "last", 0))
    if len(prices) < 10:
        return None
    up = sum(1 for i in range(1, len(prices)) if prices[i] > prices[i-1])
    dn = sum(1 for i in range(1, len(prices)) if prices[i] < prices[i-1])
    total = up + dn
    tick_ratio = up / total if total > 0 else 0.5
    tick_bullish = tick_ratio > 0.58

    # 5. VWAP slope — Gao-Ritter (2010) intraday momentum
    vwap_slope = ind.get("vwap_slope", 0) if "vwap_slope" not in common_fields else common_fields.get("vwap_slope", 0)
    # Compute from raw data if not available
    if vwap_slope == 0 and len(prices) >= 10:
        # Simple price-volume weighted slope over last 10 ticks
        n = min(10, len(prices) - 1)
        recent = prices[-n-1:]
        rets = [(recent[i+1] - recent[i]) / recent[i] for i in range(n) if recent[i] > 0]
        vwap_slope = sum(rets) / len(rets) if rets else 0
    vwap_bullish = vwap_slope > 0.0005  # 0.05% positive slope

    # 6. Amihud illiquidity drop — decreasing illiquidity = institutional inflow
    amihud = msg.get("amihud", 0)
    amihud_bullish = amihud < 0.01 and ind.get("rvol", 1.0) > 1.0

    # Count aligned indicators
    signals = [tmr_bullish, vpin_bullish, spread_compressed, tick_bullish, vwap_bullish, amihud_bullish]
    bullish_count = sum(signals)

    # Entry gate: 4+ of 6 + ADX > 15 + not mean-reverting regime
    adx = ind.get("adx", 0)
    hurst_regime = ind.get("hurst_regime", "random")
    if bullish_count < 4 or adx < 15.0 or hurst_regime == "mean_reverting":
        return None

    # Graduated confidence with regime conditioning
    conf = 52.0
    conf += (bullish_count - 4) * 4.0
    if adx > 25.0:
        conf += 8.0
    if adx > 35.0:
        conf += 4.0
    if ind.get("rvol", 1.0) > 2.0:
        conf += 5.0
    if tmr > 0.5:
        conf += 3.0
    if hurst_regime == "trending":
        conf += 5.0  # Momentum works best in trending regime
    # Book 21: D-VPIN bonus — if informed buying detected, boost confidence
    d_vpin_val = common_fields.get("d_vpin", 0)
    if d_vpin_val > 0.30:
        conf += 4.0  # Informed buyers on our side
    # Book 22: Keltner squeeze release — 67% WR breakout signal
    if common_fields.get("squeeze_release", False):
        conf += 8.0  # Volatility expanding after compression = strong breakout
    # Penalize if structural score is low (poor tradability)
    ss = ind.get("structural_score", 50)
    if ss < 40:
        conf -= 5.0
    conf = max(0, min(conf, 95.0))

    if conf < conf_floor:
        return None

    # Live stats for adaptive sizing
    stats = _strategy_live_stats("S1_Microstructure")
    # If we have enough data and Sharpe is negative, reduce confidence
    if stats["n"] >= 30 and stats["sharpe"] < -0.5:
        conf *= 0.7

    kelly = kelly_fn(conf)
    _track_strategy_entry(ticker_id, "S1_Microstructure", last)
    return {
        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
        "confidence": conf,
        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
        "strategy": "S1_Microstructure",
        "s1_tmr": round(tmr, 3), "s1_vpin": round(vpin, 3),
        "s1_spread_compressed": spread_compressed, "s1_tick_ratio": round(tick_ratio, 3),
        "s1_bullish_count": bullish_count, "s1_vwap_slope": round(vwap_slope, 6),
        "s1_live_sharpe": stats["sharpe"],
        **common_fields,
    }


def _system2_reversion(ticker_id, msg, bars_5m, ind, conf_floor, kelly_fn, common_fields):
    """System 2: Statistical Reversion — multi-factor oversold detection.

    Academic basis: Connors & Alvarez (2008), Bollinger (2001), Jegadeesh (1990).
    5 factors: BB z-score, RSI(2), IBS, volume capitulation, mean-reversion speed.
    Regime gate: mean_reverting or random only (never trending — that's S3's job).
    """
    if len(bars_5m) < 20:
        return None

    hurst_regime = ind.get("hurst_regime", "random")
    if hurst_regime == "trending":
        return None

    closes = [b["close"] for b in bars_5m[-20:]]
    current = closes[-1]

    # 1. BB z-score (20-bar)
    sma20 = sum(closes) / len(closes)
    variance = sum((c - sma20) ** 2 for c in closes) / len(closes)
    std20 = variance ** 0.5
    if std20 < 1e-9:
        return None
    z_score = (current - sma20) / std20

    # 2. RSI(2) — Connors short-term oversold
    rsi2 = calculate_rsi(closes, period=2)

    # 3. IBS (Internal Bar Strength)
    ibs_val = ind.get("ibs", 0.5)

    # 4. Volume capitulation — RVOL spike on down move = panic selling exhaustion
    rvol = ind.get("rvol", 1.0)
    last_3_down = sum(1 for b in bars_5m[-3:] if b["close"] < b["open"])
    vol_capitulation = rvol > 2.0 and last_3_down >= 2

    # 5. Mean-reversion speed — how fast did price deviate? Fast = liquidity gap = fills faster
    if len(closes) >= 5:
        dev_speed = abs(closes[-1] - closes[-5]) / (std20 * 5) if std20 > 0 else 0
    else:
        dev_speed = 0

    # Scoring: each factor adds conviction
    score = 0
    if z_score < -1.5: score += 1
    if z_score < -2.0: score += 1
    if z_score < -2.5: score += 1
    if rsi2 is not None and rsi2 < 15: score += 1
    if rsi2 is not None and rsi2 < 5: score += 1
    if ibs_val < 0.25: score += 1
    if ibs_val < 0.10: score += 1
    if vol_capitulation: score += 2  # Strong signal — double weight
    if dev_speed > 0.5: score += 1  # Fast deviation = faster fill

    # Need score >= 4 (out of 10 possible)
    if score < 4:
        return None

    # Confidence maps score to conviction
    conf = 48.0 + score * 4.0  # 48 + 4*4=64 minimum, up to 48+40=88
    if hurst_regime == "mean_reverting":
        conf += 5.0  # Bonus: confirmed MR regime
    conf = max(0, min(conf, 90.0))

    if conf < conf_floor:
        return None

    # Adaptive: reduce if strategy has negative live Sharpe
    stats = _strategy_live_stats("S2_Reversion")
    if stats["n"] >= 30 and stats["sharpe"] < -0.5:
        conf *= 0.7

    kelly = kelly_fn(conf)
    _track_strategy_entry(ticker_id, "S2_Reversion", current)
    return {
        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
        "confidence": conf,
        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
        "strategy": "S2_Reversion",
        "s2_zscore": round(z_score, 3), "s2_rsi2": round(rsi2, 2) if rsi2 else 0,
        "s2_ibs": round(ibs_val, 4), "s2_score": score,
        "s2_vol_capitulation": vol_capitulation, "s2_dev_speed": round(dev_speed, 3),
        "s2_live_sharpe": stats["sharpe"],
        **common_fields,
    }


def _system3_macro_trend(ticker_id, msg, bars_5m, ind, conf_floor, kelly_fn, common_fields):
    """System 3: Macro Trend Following — multi-timeframe momentum.

    Academic basis: Moskowitz-Ooi-Pedersen (2012), Faber (2007), Asness-Moskowitz-Pedersen (2013).
    5 factors: dual MA crossover, 12-bar momentum, ADX trend strength, volume trend, Hurst confirmation.
    Thrives in trending + stress. ISA long-only: only long momentum.
    """
    if len(bars_5m) < 20:
        return None

    closes = [b["close"] for b in bars_5m]
    current = closes[-1]

    # 1. Dual MA crossover (fast/slow)
    sma5 = sum(closes[-5:]) / 5
    sma20 = sum(closes[-20:]) / 20
    ma_bullish = sma5 > sma20 and current > sma5

    # 2. 12-bar momentum (Moskowitz-style time-series momentum)
    if len(closes) >= 12:
        mom_12 = (current - closes[-12]) / closes[-12] if closes[-12] > 0 else 0
    else:
        mom_12 = 0
    mom_bullish = mom_12 > 0.005  # >0.5% gain over 12 bars (1 hour)

    # 3. ADX trend strength
    adx = ind.get("adx", 0)
    adx_strong = adx > 20.0

    # 4. Volume trend — increasing volume confirms trend
    vol_slope = ind.get("vol_slope", 0)
    vol_confirming = vol_slope > 0

    # 5. Hurst regime — trending confirmed
    hurst = ind.get("hurst", 0.5)
    hurst_regime = ind.get("hurst_regime", "random")
    hurst_trending = hurst_regime == "trending" or hurst > 0.55

    # Regime gate: reject in mean_reverting (choppy kills trend following)
    if hurst_regime == "mean_reverting":
        return None

    # Score: need 4+ of 5
    factors = [ma_bullish, mom_bullish, adx_strong, vol_confirming, hurst_trending]
    score = sum(factors)
    if score < 4:
        return None

    # Crossover strength for confidence scaling
    crossover_pct = (sma5 - sma20) / sma20 * 100.0 if sma20 > 0 else 0

    conf = 50.0
    conf += score * 4.0
    if crossover_pct > 0.3:
        conf += 5.0
    if adx > 30.0:
        conf += 5.0
    if mom_12 > 0.01:
        conf += 3.0  # Strong 1-hour momentum
    if hurst_trending:
        conf += 4.0
    conf = max(0, min(conf, 90.0))

    if conf < conf_floor:
        return None

    stats = _strategy_live_stats("S3_MacroTrend")
    if stats["n"] >= 30 and stats["sharpe"] < -0.5:
        conf *= 0.7

    kelly = kelly_fn(conf)
    _track_strategy_entry(ticker_id, "S3_MacroTrend", current)
    return {
        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
        "confidence": conf,
        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
        "strategy": "S3_MacroTrend",
        "s3_sma5": round(sma5, 4), "s3_sma20": round(sma20, 4),
        "s3_crossover_pct": round(crossover_pct, 3), "s3_mom12": round(mom_12, 5),
        "s3_score": score, "s3_live_sharpe": stats["sharpe"],
        **common_fields,
    }


def _system4_volatility(ticker_id, msg, ind, conf_floor, kelly_fn, common_fields):
    """System 4: Volatility Premium — VIX-driven inverse ETP trading.

    Low VIX (< 18): long inverse 3x ETPs (short vol premium).
    High VIX (> 25): long regular 3x ETPs (long vol rebound).
    ISA constraint: long-only, so we buy inverse ETPs to express short-vol view.
    Habitat: Index inverse pairs (3USS, QQQS). US session primarily.
    """
    vix = msg.get("vix", 0)
    if vix <= 0:
        return None

    symbol = ticker_symbols.get(ticker_id, "")
    is_inverse = symbol.startswith("3S") or symbol.startswith("QQQ S") or symbol in (
        "QQQS.L", "3USS.L", "3STS.L", "3SNV.L", "3SAP.L", "3SMS.L", "3SEM.L",
    )

    # Low VIX regime: buy inverse ETPs (expressing short vol)
    if vix < 18.0 and is_inverse:
        conf = 57.0
        if vix < 14.0:
            conf += 8.0  # Very low VIX = strong vol premium
        if ind.get("adx", 0) < 15.0:
            conf += 5.0  # Low trend = range-bound = vol selling works
        conf = min(conf, 78.0)
        if conf < conf_floor:
            return None
        kelly = kelly_fn(conf)
        return {
            "type": "signal", "ticker_id": ticker_id, "direction": "Long",
            "confidence": conf,
            "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
            "strategy": "S4_VolPremium", "s4_vix": round(vix, 1), "s4_mode": "short_vol",
            **common_fields,
        }

    # High VIX regime: buy regular (non-inverse) 3x ETPs (long vol rebound)
    if vix > 30.0 and not is_inverse:
        conf = 55.0
        if vix > 40.0:
            conf += 10.0  # Extreme fear = strong rebound potential
        conf = min(conf, 75.0)
        if conf < conf_floor:
            return None
        kelly = kelly_fn(conf)
        return {
            "type": "signal", "ticker_id": ticker_id, "direction": "Long",
            "confidence": conf,
            "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
            "strategy": "S4_VolPremium", "s4_vix": round(vix, 1), "s4_mode": "long_rebound",
            **common_fields,
        }

    return None


def _system5_overnight(ticker_id, msg, ind, conf_floor, kelly_fn, common_fields):
    """System 5: Overnight Carry — buy at close, sell at open.

    Academic basis: Cliff, Cooper, Gulen (2008) — overnight drift premium.
    Leveraged ETPs amplify the overnight premium (3x drift = 3x carry).
    Entry: 30 minutes before market close. Exit: handled by exit_engine at open.
    Habitat: Tier 1 liquid ETPs. Regime: Normal, Caution.
    """
    # Only fire near market close (last 30 min of session)
    london_secs = msg.get("london_time_secs", 0)
    # LSE close window: 16:00-16:25 London (57600-59100 secs)
    # US close window: 20:30-20:55 London (73800-75300 secs)
    in_lse_close = 57600 <= london_secs <= 59100
    in_us_close = 73800 <= london_secs <= 75300
    if not (in_lse_close or in_us_close):
        return None

    # Regime gate: only Normal/Caution (overnight in Stress/Crisis is too risky)
    hurst_regime = ind.get("hurst_regime", "random")
    if hurst_regime in ("mean_reverting",):
        return None  # Choppy = bad for carry

    # Day-of-week seasonal filter (S&P 500 overnight premium research):
    # Overnight returns are strongest Mon-Thu, weakest Fri (weekend risk).
    from datetime import datetime, timezone
    ts_ns = msg.get("timestamp_ns", 0)
    if ts_ns > 0:
        day_of_week = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).weekday()
        if day_of_week == 4:  # Friday
            return None  # Skip Friday overnight — weekend gap risk

    # Book 40: Leverage-specific overnight limit — 5x ETPs = ZERO overnight
    leverage = msg.get("leverage", 3)
    if leverage >= 5:
        return None  # 5x ETPs: intraday only, never overnight

    # Book 186: Day-of-week carry premium adjustment
    # Mon-Wed: +2pp confidence (positive carry). Thu: neutral. Fri: blocked above.
    day_conf_adj = 0.0
    if ts_ns > 0:
        if day_of_week <= 2:  # Mon-Wed
            day_conf_adj = 2.0
        elif day_of_week == 3:  # Thursday
            day_conf_adj = -2.0  # Slightly reduce (pre-weekend positioning)

    # Need positive momentum going into close (don't carry a falling knife)
    bars_5m = ind.get("bars_5m", [])
    if len(bars_5m) < 6:
        return None
    recent_3 = bars_5m[-3:]
    up_count = sum(1 for b in recent_3 if b["close"] > b["open"])
    if up_count < 2:
        return None  # Need 2/3 recent bars up

    conf = 56.0 + day_conf_adj
    if up_count == 3:
        conf += 5.0
    if ind.get("rvol", 1.0) > 1.2:
        conf += 5.0  # Volume supports direction
    if ind.get("adx", 0) > 20.0:
        conf += 5.0  # Trending into close
    conf = min(conf, 78.0)

    if conf < conf_floor:
        return None

    kelly = kelly_fn(conf)
    return {
        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
        "confidence": conf,
        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
        "strategy": "S5_OvernightCarry",
        "s5_window": "lse_close" if in_lse_close else "us_close",
        "s5_up_bars": up_count,
        **common_fields,
    }


def _system6_catalyst(ticker_id, msg, ind, conf_floor, kelly_fn, common_fields):
    """System 6: Catalyst Rotation — event-driven trading.

    Fires around major macro events (FOMC, NFP, CPI) + gap fades.
    Post-event drift: markets tend to continue in the direction of the initial move
    30-60 minutes after the event. ISA long-only: only trade bullish catalysts.
    Habitat: All ETPs during events. All regimes.
    """
    # Check for gap (already computed by engine)
    gap_pct = msg.get("gap_pct", 0.0)

    # Post-gap continuation: if gap > 1.5% up with high volume, ride the momentum
    if gap_pct > 1.5 and ind.get("rvol", 1.0) > 2.0:
        conf = 58.0
        if gap_pct > 3.0:
            conf += 8.0  # Large gap = strong catalyst
        if ind.get("adx", 0) > 20.0:
            conf += 7.0  # Trending after gap = continuation likely
        bars_5m = ind.get("bars_5m", [])
        if len(bars_5m) >= 3:
            recent_up = sum(1 for b in bars_5m[-3:] if b["close"] > b["open"])
            if recent_up >= 2:
                conf += 5.0  # Price confirming after gap
        conf = min(conf, 82.0)
        if conf >= conf_floor:
            kelly = kelly_fn(conf)
            return {
                "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                "confidence": conf,
                "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                "strategy": "S6_Catalyst", "s6_gap_pct": round(gap_pct, 3),
                "s6_trigger": "gap_continuation",
                **common_fields,
            }

    return None


def _system7_tail_hedge(ticker_id, msg, ind, conf_floor, kelly_fn, common_fields):
    """System 7: Tail Hedge — long inverse positions during crisis.

    When VIX > 25 AND hurst regime is trending AND market is falling,
    buy inverse 3x ETPs to profit from the crash and hedge the portfolio.
    Habitat: Inverse ETPs (-3x). Regime: Stress, Crisis only.
    """
    vix = msg.get("vix", 0)
    if vix < 25.0:
        return None

    symbol = ticker_symbols.get(ticker_id, "")
    is_inverse = symbol.startswith("3S") or symbol in (
        "QQQS.L", "3USS.L", "3STS.L", "3SNV.L", "3SAP.L", "3SMS.L", "3SEM.L",
    )
    if not is_inverse:
        return None

    # Need trending regime (crisis = strong trends)
    hurst_regime = ind.get("hurst_regime", "random")
    if hurst_regime not in ("trending",):
        return None

    # Confirm downward momentum (inverse ETPs go UP when market goes DOWN)
    bars_5m = ind.get("bars_5m", [])
    if len(bars_5m) < 5:
        return None
    # For inverse ETPs, "up" bars mean the underlying is falling
    recent_up = sum(1 for b in bars_5m[-5:] if b["close"] > b["open"])
    if recent_up < 3:
        return None  # Inverse not trending up = market not crashing

    conf = 60.0
    if vix > 35.0:
        conf += 10.0  # Extreme fear
    if vix > 45.0:
        conf += 5.0   # Panic
    if ind.get("rvol", 1.0) > 3.0:
        conf += 5.0   # Extreme volume = capitulation
    conf = min(conf, 88.0)

    if conf < conf_floor:
        return None

    kelly = kelly_fn(conf)
    return {
        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
        "confidence": conf,
        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
        "strategy": "S7_TailHedge", "s7_vix": round(vix, 1),
        "s7_inverse_momentum": recent_up,
        **common_fields,
    }


def _generate_signals(ticker_id, msg, ticks, ind, conf_floor):
    """Stage 3: Generate signals from all 13 generators with Book-derived pre-gates."""
    hurst, hurst_regime = ind["hurst"], ind["hurst_regime"]
    bars_5m = ind["bars_5m"]

    # ── BOOK 162: VPIN TOXICITY GATE ──
    # Block entries when informed flow is toxic. Calibrated thresholds from Book 162.
    # > 0.60: elevated → raise confidence floor by 10 (harder to enter)
    # > 0.80: extreme → block all entries (informed traders dominating)
    vpin = ind.get("vpin", 0.5)
    if vpin > 0.80:
        return []  # Block all signals — toxic informed flow
    vpin_penalty = 10 if vpin > 0.60 else 0
    effective_floor = conf_floor + vpin_penalty

    # ── BOOK 21: DIRECTIONAL VPIN (D-VPIN) ──
    # Signed version: positive = informed buying, negative = informed selling
    d_vpin = 0.0
    if bars_5m and len(bars_5m) >= 10:
        buy_v = sum(b["volume"] for b in bars_5m[-10:] if b["close"] > b["open"])
        sell_v = sum(b["volume"] for b in bars_5m[-10:] if b["close"] <= b["open"])
        total_v = buy_v + sell_v
        if total_v > 0:
            d_vpin = (buy_v - sell_v) / total_v  # -1 to +1

    # ── BOOK 22: KELTNER SQUEEZE DETECTION ──
    # When Bollinger Bands contract inside Keltner Channel = volatility squeeze.
    # First bar after squeeze releases → 67% WR breakout signal.
    squeeze_on = False
    squeeze_release = False
    if bars_5m and len(bars_5m) >= 20:
        closes_20 = [b["close"] for b in bars_5m[-20:]]
        sma20 = sum(closes_20) / 20
        std20 = (sum((c - sma20)**2 for c in closes_20) / 20) ** 0.5

        # ATR(20) from bars
        atr_sum = 0.0
        for i in range(1, min(20, len(bars_5m))):
            h, l, pc = bars_5m[-i]["high"], bars_5m[-i]["low"], bars_5m[-i-1]["close"]
            atr_sum += max(h - l, abs(h - pc), abs(l - pc))
        atr20 = atr_sum / min(19, len(bars_5m) - 1) if len(bars_5m) > 1 else 0

        if std20 > 0 and atr20 > 0:
            bb_upper = sma20 + 2.0 * std20
            keltner_upper = sma20 + 1.5 * atr20
            squeeze_on = bb_upper < keltner_upper  # BB inside Keltner = squeeze
            # Check if squeeze just released (was on, now off)
            if not squeeze_on and len(bars_5m) >= 21:
                prev_closes = [b["close"] for b in bars_5m[-21:-1]]
                prev_sma = sum(prev_closes) / 20
                prev_std = (sum((c - prev_sma)**2 for c in prev_closes) / 20) ** 0.5
                prev_bb_upper = prev_sma + 2.0 * prev_std
                prev_keltner_upper = prev_sma + 1.5 * atr20
                if prev_bb_upper < prev_keltner_upper:
                    squeeze_release = True  # Just released — 67% WR breakout

    # ── BOOK 118: STUDENT-T KELLY ADJUSTMENT ──
    # ETP returns have fat tails (ν ≈ 4-6). Standard Kelly overestimates optimal fraction.
    # Adjustment: multiply Kelly by 1/(1 + 3/ν). For ν=5: factor = 1/1.6 = 0.625.
    leverage = msg.get("leverage", 3)
    nu = 5.0 if leverage >= 3 else 7.0  # Fatter tails for higher leverage
    student_t_factor = 1.0 / (1.0 + 3.0 / nu)  # 0.625 for 3x ETPs

    common_fields = {
        "rvol": ind["rvol"], "hurst": hurst, "hurst_regime": hurst_regime,
        "volume_divergence": ind["vol_div"], "adx": ind["adx"],
        "vol_slope": ind["vol_slope"], "vwap_dist_pct": ind["vwap_dist_pct"],
        "structural_score": ind["structural_score"],
        "d_vpin": round(d_vpin, 3), "squeeze_on": squeeze_on,
        "squeeze_release": squeeze_release, "vpin_toxic": vpin > 0.60,
    }

    # Thompson per-type floors
    # Use VPIN-adjusted floor for all strategies
    vanguard_floor, orchestrator_floor = effective_floor, effective_floor
    if _adaptive_entry_confidence:
        ta = _adaptive_entry_confidence.get("TypeA")
        if ta is not None:
            vanguard_floor = max(conf_floor, ta)
        tb = _adaptive_entry_confidence.get("TypeB")
        if tb is not None:
            orchestrator_floor = max(conf_floor, tb)

    # Helper: compute Kelly for a given confidence
    def _kelly_for(confidence):
        total_trades = msg.get("total_trades", 0)
        k = kelly_12factor(
            win_rate_raw=msg.get("win_rate", 0.5), total_trades=total_trades,
            avg_win=msg.get("avg_win", 0.02), avg_loss=msg.get("avg_loss", 0.02),
            leverage_factor=msg.get("leverage", 3), realized_vol_annual=msg.get("realized_vol", 0.30),
            correlation_to_portfolio=msg.get("correlation", 0.0),
            current_drawdown_pct=msg.get("drawdown_pct", 0.0),
            amihud_illiq=msg.get("amihud", 0.0),
            regime=hurst_regime if hurst_regime != "random" else msg.get("regime", "normal"),
            spread_pct=msg.get("spread_pct", 0.1), time_of_day_fraction=msg.get("time_fraction", 0.5),
            confidence=confidence, portfolio_heat_pct=msg.get("heat_pct", 0.0),
            equity=msg.get("equity", 10000.0), price=msg["last"],
        )
        # Early ramp: use preliminary Kelly if we have few trades
        if total_trades < 50:
            pk = min(confidence / 1000.0, 0.05)
            if k["kelly_fraction"] < pk:
                eq = msg.get("equity", 10000.0)
                k["kelly_fraction"] = pk
                k["shares"] = max(int(pk * eq / max(msg["last"], 1e-9)), 1)

        # CONSOLIDATED KELLY SCALING (was 5 layers → now 1 with drawdown governor):
        # Previous chain: regime(0.6) * half_kelly(0.5) * paper_buffer(0.75) = 0.225x → KILLED ALL TRADES.
        # Fix: half-Kelly with drawdown governor is the ONLY scaling layer.
        # Regime is already a factor in kelly_12factor(). Paper-to-live gap is covered by slippage model.
        # LETF decay in MR regime is a genuine additional factor (arxiv 2504.20116).
        dd = msg.get("drawdown_pct", 0.0)
        if dd > 10.0:
            kelly_scale = 0.25   # quarter-Kelly — survival mode
        elif dd > 5.0:
            kelly_scale = 0.40   # reduced Kelly — defensive
        else:
            kelly_scale = 0.50   # half-Kelly — standard (Chan 2010)

        # LETF mean-reversion penalty (the one genuine additional factor)
        leverage = msg.get("leverage", 3)
        if leverage >= 3 and hurst_regime == "mean_reverting":
            kelly_scale *= 0.7  # 30% reduction for 3x LETF in MR regime

        # Book 118: Student-t Kelly adjustment for fat-tailed ETP returns.
        # ν=5 for 3x ETPs → factor = 1/(1+3/5) = 0.625. Prevents Gaussian overestimation.
        combined = kelly_scale * student_t_factor
        k["kelly_fraction"] *= combined
        k["shares"] = max(1, int(k["shares"] * combined))

        return k

    # VanguardSniper (momentum, non-mean-reverting regimes)
    vanguard_signal = None
    if hurst < 0.01 or hurst >= 0.20 or hurst_regime in ("trending", "random"):
        eval_ticks = [{"last": b["close"], "high": b["high"], "low": b["low"],
                       "bid": b["close"], "ask": b["close"], "volume": b["volume"]}
                      for b in bars_5m] if bars_5m else ticks
        result = vanguard_evaluate(eval_ticks, confidence_floor=vanguard_floor)
        if result is not None:
            kelly = _kelly_for(result["confidence"])
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

    # ── NEW STRATEGIES (Sprint C, 2026-03-23) ──

    # Strategy: IBS Mean Reversion (Connors RSI-2 / IBS combo)
    # Academic basis: Connors & Alvarez (2008). IBS < 0.2 + RSI(2) < 10 → ~57% WR.
    # Fires in ALL regimes (mean-reverting is strongest but don't gate on regime).
    # LOOSENED from original: removed RVOL > 0.7 gate, widened RSI2 to < 25, IBS to < 0.30.
    ibs_signal = None
    if len(bars_5m) >= 5:
        ibs_val = ind["ibs"]
        prices_for_rsi = [b["close"] for b in bars_5m]
        rsi2 = calculate_rsi(prices_for_rsi, period=2)
        if ibs_val is not None and rsi2 is not None and ibs_val < 0.30 and rsi2 < 25.0:
            # Graduated confidence: lower IBS + lower RSI = higher conviction
            ibs_conf = 55.0
            if ibs_val < 0.10:
                ibs_conf += 15.0
            if rsi2 < 5.0:
                ibs_conf += 10.0
            if ind["rvol"] > 1.5:
                ibs_conf += 5.0
            ibs_conf = min(ibs_conf, 95.0)
            if ibs_conf >= conf_floor:
                kelly = _kelly_for(ibs_conf)
                ibs_signal = {
                    "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                    "confidence": ibs_conf,
                    "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                    "strategy": "IBS_MeanReversion", "ibs_entry": round(ibs_val, 4),
                    "rsi2_entry": round(rsi2, 2), **common_fields,
                }

    # Strategy: Volume Expansion Continuation
    # Entry: RVOL > 2.0 AND ADX > 20 AND 3+ consecutive up bars.
    # Differentiation from VanguardSniper: requires RVOL > 2.0 (vs 1.5) and consecutive bars.
    # VanguardSniper is ADX-based scoring; VolExpansion is structure-based confirmation.
    volexp_signal = None
    if ind["rvol"] > 2.0 and ind["adx"] > 20.0 and len(bars_5m) >= 5:
        recent = bars_5m[-4:]
        up_count = sum(1 for b in recent if b["close"] > b["open"])
        if up_count >= 3:
            # Graduated confidence based on RVOL strength + trend strength
            ve_conf = 60.0
            if ind["rvol"] > 3.0:
                ve_conf += 10.0
            if ind["adx"] > 30.0:
                ve_conf += 10.0
            if ind["vol_slope"] > 0:
                ve_conf += 5.0
            # Differentiation bonus: if VanguardSniper ALSO fired, this is confirmation
            if vanguard_signal is not None:
                ve_conf += 5.0  # Cross-strategy confirmation
            ve_conf = min(ve_conf, 95.0)
            if ve_conf >= conf_floor:
                kelly = _kelly_for(ve_conf)
                volexp_signal = {
                    "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                    "confidence": ve_conf,
                    "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                    "strategy": "VolExpansion", **common_fields,
                }

    # Strategy: Opening Range Breakout (ORB) — US session only
    # Entry: Price breaks the first-30-min high/low with volume confirmation.
    # Time window: first 60 bars (5s each = 5min) after US cash open.
    # For now, we detect via time_fraction (0.0=LSE open, higher=later in day).
    orb_signal = None
    ts_ns = msg.get("timestamp_ns", 0)
    if ts_ns > 0 and len(bars_5m) >= 6:
        from datetime import datetime, timezone
        utc_dt = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        utc_hour = utc_dt.hour
        utc_min = utc_dt.minute
        # US cash opens at 14:30 UTC. ORB formation: 14:30-14:45 UTC (first 15 min).
        # ORB breakout window: 14:45-15:30 UTC (trade the breakout for up to 45 min).
        if 14 <= utc_hour <= 15 and (utc_hour == 14 and utc_min >= 45 or utc_hour == 15 and utc_min <= 30):
            # Find the opening range: first 3 five-minute bars of this session
            # (bars_5m index 0 may not be session open, but we approximate with recent 6 bars)
            exchange = _get_exchange_for_symbol(ticker_symbols.get(ticker_id, ""))
            if exchange == "US":
                range_bars = bars_5m[:min(3, len(bars_5m))]
                orb_high = max(b["high"] for b in range_bars)
                orb_low = min(b["low"] for b in range_bars)
                current = bars_5m[-1]["close"]
                # Breakout above ORB high with volume
                if current > orb_high and ind["rvol"] > 1.5:
                    orb_conf = 60.0
                    if ind["rvol"] > 2.5:
                        orb_conf += 10.0
                    if ind["adx"] > 15.0:
                        orb_conf += 10.0
                    orb_conf = min(orb_conf, 90.0)
                    if orb_conf >= conf_floor:
                        kelly = _kelly_for(orb_conf)
                        orb_signal = {
                            "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                            "confidence": orb_conf,
                            "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                            "strategy": "ORB_Breakout", "orb_high": round(orb_high, 4),
                            "orb_low": round(orb_low, 4), **common_fields,
                        }

    # Strategy: Gap Fade — liquidity gaps tend to fill
    # Academic basis: Gaps >1% in large-cap stocks fill ~65% of the time within the session.
    # Only fade LIQUIDITY gaps (RVOL < 2.0 at open = low institutional participation).
    # Information gaps (RVOL > 5.0) are news-driven and tend to continue — don't fade those.
    gap_signal = None
    gap_pct = msg.get("gap_pct", 0.0)
    if abs(gap_pct) > 1.0 and ind["rvol"] < 2.0 and len(bars_5m) >= 3:
        from brain.gap_detector import classify_gap
        gap_type = classify_gap(ind["rvol"])
        if gap_type == "liquidity":
            # Fade the gap: if gap up, go short (but we're ISA long-only, so skip gap-up fades)
            # If gap down > 1%, go long (buying the dip on a liquidity gap)
            if gap_pct < -1.0:
                gf_conf = 58.0
                if gap_pct < -2.0:
                    gf_conf += 10.0  # Larger gap = higher fill probability
                if ind["ibs"] < 0.3:
                    gf_conf += 7.0   # Close near low confirms gap-down exhaustion
                gf_conf = min(gf_conf, 90.0)
                if gf_conf >= conf_floor:
                    kelly = _kelly_for(gf_conf)
                    gap_signal = {
                        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                        "confidence": gf_conf,
                        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                        "strategy": "GapFade", "gap_pct": round(gap_pct, 3),
                        "gap_type": gap_type, **common_fields,
                    }

    # ── SYSTEM 1: Microstructure Momentum (Phase 4) ──
    s1_signal = None
    if len(ticks) >= 20 and msg.get("bid", 0) > 0 and msg.get("ask", 0) > 0:
        s1_signal = _system1_microstructure(ticker_id, msg, ticks, ind, conf_floor, _kelly_for, common_fields)

    # ── SYSTEM 2: Statistical Reversion (Phase 6) ──
    s2_signal = None
    if len(bars_5m) >= 20:
        s2_signal = _system2_reversion(ticker_id, msg, bars_5m, ind, conf_floor, _kelly_for, common_fields)

    # ── SYSTEM 3: Macro Trend Following (Phase 6) ──
    s3_signal = None
    if len(bars_5m) >= 20:
        s3_signal = _system3_macro_trend(ticker_id, msg, bars_5m, ind, conf_floor, _kelly_for, common_fields)

    # ── SYSTEM 4: Volatility Premium (Phase 8) ──
    s4_signal = _system4_volatility(ticker_id, msg, ind, conf_floor, _kelly_for, common_fields)

    # ── SYSTEM 5: Overnight Carry (Phase 8) ──
    s5_signal = _system5_overnight(ticker_id, msg, ind, conf_floor, _kelly_for, common_fields)

    # ── SYSTEM 6: Catalyst Rotation (Phase 9) ──
    s6_signal = _system6_catalyst(ticker_id, msg, ind, conf_floor, _kelly_for, common_fields)

    # ── SYSTEM 7: Tail Hedge (Phase 9) ──
    s7_signal = _system7_tail_hedge(ticker_id, msg, ind, conf_floor, _kelly_for, common_fields)

    # Return ALL signals sorted by confidence — no artificial "Best 2" bottleneck.
    # Stage 4 selects the best after applying adjustments to every signal.
    all_signals = [s for s in [
        vanguard_signal, orchestrator_signal, ibs_signal, volexp_signal, orb_signal, gap_signal,
        s1_signal, s2_signal, s3_signal, s4_signal, s5_signal, s6_signal, s7_signal,
    ] if s]

    # AUTONOMY: Filter out auto-killed strategies (live Sharpe < -1.0 over 30+ trades)
    if _auto_killed_strategies:
        all_signals = [s for s in all_signals if s.get("strategy", "") not in _auto_killed_strategies]

    all_signals.sort(key=lambda s: s["confidence"], reverse=True)
    return all_signals


def _apply_adjustments(ticker_id, msg, ind, all_signals):
    """Stage 4: Apply confidence adjustments to ALL signals, select best, classify, size."""
    if not all_signals:
        return None
    hurst_regime = ind["hurst_regime"]

    # P2-#7: LSE confidence boost DELETED — was +20 blanket boost that inflated
    # marginal signals (conf 45-49) above floor, causing false entries.

    symbol = ticker_symbols.get(ticker_id, "")

    # Drawdown regime filter — applied to ALL signals
    dd = msg.get("drawdown_pct", 0.0)
    is_inverse = (symbol.startswith("3S") or symbol.startswith("5S") or
                  (symbol.endswith("S.L") and len(symbol) <= 7) or
                  symbol in ("QQQS.L", "3USS.L", "3STS.L", "3SAM.L", "3SNV.L", "3SAP.L", "3SMS.L", "3SEM.L"))
    if dd > 0.02:
        penalty = min(int(dd * 500), 20)
        boost = min(int(dd * 300), 15) if is_inverse else 0
        for sig in all_signals:
            if is_inverse:
                sig["confidence"] = min(sig["confidence"] + boost, 100)
            else:
                sig["confidence"] = max(sig["confidence"] - penalty, 0)

    # Hour-of-day weights — applied to ALL signals
    hw = _load_hour_weights()
    if hw:
        ts_ns = msg.get("timestamp_ns", 0)
        if ts_ns > 0:
            from datetime import datetime as _dt_hw, timezone as _tz_hw
            utc_hour = _dt_hw.fromtimestamp(ts_ns / 1_000_000_000, tz=_tz_hw.utc).hour
            w = hw.get(utc_hour, 1.0)
            if w != 1.0:
                for sig in all_signals:
                    sig["confidence"] = max(0, min(100, int(sig["confidence"] * w)))

    # Simulated commission + slippage deduction (paper mode reality check)
    # Deducts estimated round-trip cost from Kelly sizing to prevent false positive edges.
    # IBKR tiered: £1.70 entry + £1.70 exit = £3.40 per round trip.
    # Slippage: 0.5% of position value (config.toml [risk] slippage_assumption_pct).
    for sig in all_signals:
        eq = msg.get("equity", 10000.0)
        notional = sig["kelly_fraction"] * eq
        sim_commission = 3.40  # £1.70 × 2 (IBKR tiered minimum)
        sim_slippage = notional * 0.005  # 0.5% slippage assumption
        total_cost = sim_commission + sim_slippage
        # Attach cost estimate to signal for Ouroboros forensics
        sig["sim_commission_gbp"] = round(sim_commission, 2)
        sig["sim_slippage_gbp"] = round(sim_slippage, 2)
        sig["sim_total_cost_gbp"] = round(total_cost, 2)
        # Reduce shares by cost fraction (so Kelly reflects post-cost reality)
        if notional > 0:
            cost_frac = total_cost / notional
            sig["kelly_fraction"] = max(0.001, sig["kelly_fraction"] * (1 - cost_frac))
            sig["shares"] = max(1, int(sig["shares"] * (1 - cost_frac)))

    # COMPOUNDING: Cost-aware edge filter — reject if cost > 50% of expected edge.
    # Expected edge ≈ kelly_fraction * equity * (WR - 0.5) * 2 (simplified edge proxy).
    # If the cost of the trade exceeds half the expected profit, it's not worth taking.
    wr = msg.get("win_rate", 0.5)
    edge_proxy = max(wr - 0.45, 0) * 2  # edge above 45% WR breakeven (after costs)
    all_signals = [
        sig for sig in all_signals
        if sig["sim_total_cost_gbp"] <= 0 or edge_proxy <= 0 or
        sig["sim_total_cost_gbp"] < (sig["kelly_fraction"] * msg.get("equity", 10000) * edge_proxy * 0.5)
    ]
    if not all_signals:
        return None

    # STRATEGY-REGIME MATRIX (Book 15, 113, 124)
    # Disable or scale strategies based on current market regime.
    try:
        from python_brain.regime.strategy_regime_matrix import (
            RegimeState, apply_regime_adjustments,
        )
        vix_val = msg.get("vix", 21.0)
        hurst_val = msg.get("hurst", 0.50)
        hmm_st = msg.get("hmm_state", 1)
        regime_state = RegimeState.from_indicators(
            vix=vix_val, hurst=hurst_val, hmm_state=hmm_st,
        )
        regime_filtered = []
        for sig in all_signals:
            strat = sig.get("strategy", "")
            adj_conf, adj_kelly = apply_regime_adjustments(
                strat, sig["confidence"], sig["kelly_fraction"], regime_state,
            )
            if adj_conf > 0:
                sig["confidence"] = adj_conf
                sig["kelly_fraction"] = adj_kelly
                regime_filtered.append(sig)
        all_signals = regime_filtered
        if not all_signals:
            return None
    except Exception:
        pass  # Non-fatal: if regime matrix fails, proceed without it

    # OVERNIGHT GAP RISK FILTER (Book 40, 148, 186)
    # Adjust confidence and Kelly for late-session entries approaching overnight.
    # Blocks entries that would create overnight exposure exceeding tier/regime limits.
    try:
        from python_brain.overnight.risk import check_overnight_risk
        vix_val = msg.get("vix", 21.0)
        london_secs = msg.get("london_time_secs", 0)
        is_fri = msg.get("day_of_week", 0) == 4  # 0=Mon, 4=Fri
        filtered_signals = []
        for sig in all_signals:
            ticker = sig.get("ticker", "")
            lev = sig.get("leverage", 3)
            adj_conf, adj_kelly = check_overnight_risk(
                ticker=ticker,
                confidence=sig["confidence"],
                kelly_fraction=sig["kelly_fraction"],
                vix=vix_val,
                london_time_secs=london_secs,
                leverage=lev,
                is_friday=is_fri,
            )
            if adj_conf > 0:
                sig["confidence"] = adj_conf
                sig["kelly_fraction"] = adj_kelly
                filtered_signals.append(sig)
        all_signals = filtered_signals
        if not all_signals:
            return None
    except Exception:
        pass  # Non-fatal: if overnight risk module fails, proceed without it

    # DRAWDOWN RECOVERY SIZING (Book 42)
    # Scale Kelly based on current drawdown phase. Deeper drawdown = smaller size.
    try:
        from python_brain.risk.drawdown_recovery import DrawdownMonitor
        dd_monitor = DrawdownMonitor(initial_equity=msg.get("initial_equity", 10000.0))
        dd_monitor.update(msg.get("equity", 10000.0))
        dd_scale = dd_monitor.kelly_scale()
        dd_min_conf = dd_monitor.min_confidence()
        if dd_scale < 1.0:
            for sig in all_signals:
                sig["kelly_fraction"] *= dd_scale
                sig["shares"] = max(1, int(sig["shares"] * dd_scale))
        # Filter by drawdown-adjusted confidence floor
        all_signals = [s for s in all_signals if s["confidence"] >= dd_min_conf]
        if not all_signals:
            return None
    except Exception:
        pass

    # CORRELATION POSITION SIZING (Book 41)
    # Reduce size when portfolio correlation is elevated.
    try:
        from python_brain.risk.correlation import CorrelationTracker
        corr_tracker = getattr(msg, "_corr_tracker", None)
        if corr_tracker is not None:
            corr_mult = corr_tracker.position_size_multiplier()
            if corr_mult < 1.0:
                for sig in all_signals:
                    sig["kelly_fraction"] *= corr_mult
                    sig["shares"] = max(1, int(sig["shares"] * corr_mult))
            if corr_tracker.should_block_long_entry():
                all_signals = [s for s in all_signals
                               if s.get("ticker", "").endswith("S.L")]  # Only inverse
                if not all_signals:
                    return None
    except Exception:
        pass

    # VOL-TARGETING (Book 80)
    # Scale Kelly inversely to realized volatility for constant dollar risk.
    try:
        from python_brain.sizing.vol_targeting import vol_adjusted_kelly, student_t_correction
        for sig in all_signals:
            rv = msg.get("realized_vol", 0.02)
            lev = sig.get("leverage", 3)
            # Vol-target: shrink in high vol, expand in low vol
            sig["kelly_fraction"] = vol_adjusted_kelly(
                sig["kelly_fraction"], rv, target_vol=0.02,
            )
            # Student-t correction for fat-tailed ETP returns
            sig["kelly_fraction"] = student_t_correction(
                sig["kelly_fraction"], nu=5.0, leverage=lev,
            )
            sig["shares"] = max(1, int(sig["kelly_fraction"] * msg.get("equity", 10000) / max(sig.get("price", 1), 0.01)))
    except Exception:
        pass

    # Select best signal after all adjustments
    all_signals.sort(key=lambda s: s["confidence"], reverse=True)
    best = all_signals[0]

    # COMPOUNDING MACHINE: Scale Kelly by edge-proportional allocation weight.
    # Strategies with proven higher Sharpe get more capital. Unproven get 50%.
    strat_name = best.get("strategy", "")
    alloc_weight = get_strategy_weight(strat_name)
    if alloc_weight < 1.0:
        best["kelly_fraction"] *= max(alloc_weight, 0.1)  # Floor 10% to keep data flowing
        best["shares"] = max(1, int(best["shares"] * max(alloc_weight, 0.1)))
    best["allocation_weight"] = round(alloc_weight, 3)

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

    # P5: System 1+ signals bypass legacy TypeA-F classification entirely.
    # They have their own strategy identity and validation path.
    _SYSTEM_STRATEGIES = {
        "S1_Microstructure", "S2_Reversion", "S3_MacroTrend",
        "S4_VolPremium", "S5_OvernightCarry", "S6_Catalyst", "S7_TailHedge",
    }
    is_system_signal = best.get("strategy", "") in _SYSTEM_STRATEGIES

    if not is_system_signal:
        # TypeA-F classification (all variables local — no scope bugs)
        cls_prices = [t["last"] for t in list(bar_history[ticker_id])]
        cls_volumes = [t.get("volume", 0) for t in list(bar_history[ticker_id])]
        cls_rsi = calculate_rsi(cls_prices, period=14) if len(cls_prices) >= 14 else None
        entry_type = classify_entry_type(
            rsi_14=cls_rsi, ibs=ind["ibs"], rvol=ind["rvol"], ticker_id=ticker_id,
            prices=cls_prices, volumes=cls_volumes, vol_div=ind["vol_div"],
        )
        best["entry_type"] = entry_type

        # STRATEGY REGISTRY (updated by 730-day backtest 2026-03-29):
        # TypeA/D RE-ENABLED — 730-day backtest shows 44%/43% WR, PF 1.22/1.28.
        # Previous disable was based on broken system with fantasy weights.
        # TypeC disabled — 39% WR, PF 0.81 over 62K trades.
        _DISABLED_TYPES = {"TypeC"}
        _SHADOW_TYPES = set()
        if entry_type in _DISABLED_TYPES:
            return None
        if entry_type in _SHADOW_TYPES:
            sys.stderr.write(f"SHADOW_SIGNAL: {entry_type} tid={ticker_id} conf={best['confidence']} (logged, not emitted)\n")
            sys.stderr.flush()
            return None
    else:
        # System signals keep their own strategy name and entry_type
        best["entry_type"] = best["strategy"]

    # S8: Regime + session enforcement from strategy_registry.json
    if not _SIM_MODE:
        strategy_id = best.get("strategy", entry_type)
        allowed, reason = _check_regime_session_gate(
            strategy_id, ind["hurst"], ind["rvol"], ind["adx"],
        )
        if not allowed:
            sys.stderr.write(
                f"REGIME_SESSION_VETO: {strategy_id} tid={ticker_id} — {reason}\n"
            )
            sys.stderr.flush()
            return None

    if not is_system_signal:
        if entry_type != "Unclassified":
            best["strategy"] = entry_type
        cls_rsi_val = cls_rsi if cls_rsi is not None else 0.0
    else:
        cls_rsi_val = 0.0
        entry_type = best["entry_type"]
    best["rsi"] = cls_rsi_val
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
                # P9: Claude rejection is now a SOFT gate — reduce confidence by 15 points.
                # If confidence drops below floor, the signal will be vetoed by CHECK 10.
                # This makes Claude advisory meaningful without giving it hard-veto power.
                best["confidence"] = max(0, best["confidence"] - 15)
                best["kelly_fraction"] = best["kelly_fraction"] * 0.5  # Halve sizing on reject
                best["shares"] = max(1, best["shares"] // 2)
                sys.stderr.write(
                    f"CLAUDE_SOFT_GATE: tid={ticker_id} conf_reduced_by=15 "
                    f"reason={cr.get('reasoning', '')[:80]}\n"
                )
                sys.stderr.flush()
            elif cr.get("adjusted_confidence"):
                best["claude_adjusted_confidence"] = cr["adjusted_confidence"]
                # P9: Apply Claude's adjusted confidence if it's lower (conservative gate)
                adj = cr["adjusted_confidence"]
                if adj < best["confidence"]:
                    best["confidence"] = adj
            best["claude_verdict"] = cr.get("claude_verdict", "no_response")
        except Exception as e:
            best["claude_error"] = str(e)[:200]

    # P5: Per-strategy signal tracking for validation metrics
    strat = best.get("strategy", "unknown")
    _strategy_signal_counts[strat] += 1
    _strategy_total_confidence[strat] += best.get("confidence", 0)
    n = _strategy_signal_counts[strat]
    if n % 25 == 0 or n <= 3:
        avg_conf = _strategy_total_confidence[strat] / n if n > 0 else 0
        sys.stderr.write(
            f"STRATEGY_TRACKER: {strat} signals={n} avg_conf={avg_conf:.1f} "
            f"tid={ticker_id} conf={best.get('confidence', 0)}\n"
        )
        sys.stderr.flush()

    # P7: Cannibalization detection — track co-fires within 5-min window per ticker
    ts_ns = msg.get("timestamp_ns", 0)
    if ts_ns > 0 and strat.startswith("S"):
        _cofire_total[strat] = _cofire_total.get(strat, 0) + 1
        recent = _recent_strategy_fires.get(ticker_id, [])
        # Check for co-fires with other strategies on same ticker
        cutoff = ts_ns - _COFIRE_WINDOW_NS
        for prev_strat, prev_ts in recent:
            if prev_ts >= cutoff and prev_strat != strat:
                pair = tuple(sorted([strat, prev_strat]))
                _cofire_counts[pair] = _cofire_counts.get(pair, 0) + 1
                ct = _cofire_counts[pair]
                if ct % 10 == 0:
                    denom = min(_cofire_total.get(pair[0], 1), _cofire_total.get(pair[1], 1))
                    rho_est = ct / max(denom, 1)
                    severity = "CRITICAL" if rho_est > 0.70 else "WARNING" if rho_est > 0.50 else "INFO"
                    sys.stderr.write(
                        f"CANNIBALIZATION_{severity}: {pair[0]}↔{pair[1]} "
                        f"co-fires={ct} rho_est={rho_est:.2f}\n"
                    )
                    sys.stderr.flush()
        # Record this fire
        recent = [(s, t) for s, t in recent if t >= cutoff]
        recent.append((strat, ts_ns))
        _recent_strategy_fires[ticker_id] = recent[-10:]  # Keep last 10

    # Record cooldown
    _last_signal_tick[ticker_id] = tick_count
    return best


def process_tick(msg):
    """Process a tick message through 5 stages: ingest → indicators → gates → signals → output."""
    ticker_id = msg["ticker_id"]
    _load_adaptive_params()

    # COMPOUNDING: Track daily equity for CAGR computation
    equity = msg.get("equity", 0)
    if equity > 0:
        _track_daily_equity(equity)

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

    # S6: Early exit: blocked exchanges (e.g. LSEETF leveraged ETPs — 0% WR, -£30)
    if not _SIM_MODE and sym:
        _load_symbol_exchange_map()  # ensure maps are loaded
        raw_exch = _symbol_raw_exchange_map.get(sym, "")
        blocked_exchanges = _load_blocked_exchanges()
        if raw_exch and raw_exch in blocked_exchanges:
            if sym not in _blacklist_warned:
                sys.stderr.write(f"EXCHANGE_VETO: {sym} (tid={ticker_id}) blocked — {raw_exch} in blocked_exchanges\n")
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

    # Stage 3: Generate signals (returns ALL signals, no bottleneck)
    conf_floor = _compute_confidence_floor(msg, ind)
    all_signals = _generate_signals(ticker_id, msg, ticks, ind, conf_floor)

    # Stage 4: Adjust ALL signals, select best, classify, size
    best = _apply_adjustments(ticker_id, msg, ind, all_signals)

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

        elif msg_type == "exit":
            # COMPOUNDING FIX: Engine notifies bridge when a position is closed.
            # Updates live Sharpe tracking for the strategy that opened the position.
            try:
                exit_tid = msg.get("ticker_id", -1)
                exit_price = msg.get("exit_price", 0)
                exit_pnl = msg.get("pnl", 0)
                exit_strategy = msg.get("strategy", "")
                if exit_price > 0 and exit_strategy:
                    _track_strategy_exit(exit_tid, exit_strategy, exit_price)
                    stats = _strategy_live_stats(exit_strategy)
                    sys.stderr.write(
                        f"EXIT_TRACKED: {exit_strategy} tid={exit_tid} pnl={exit_pnl:.4f} "
                        f"live_wr={stats['wr']} live_pf={stats['pf']} live_sharpe={stats['sharpe']}\n"
                    )
                    sys.stderr.flush()
            except Exception as e:
                sys.stderr.write(f"Bridge: exit tracking error: {e}\n")
                sys.stderr.flush()
            response = {"type": "ack", "ticker_id": msg.get("ticker_id", -1)}

        elif msg_type == "shutdown":
            sys.stderr.write("Python Brain Bridge: shutting down\n")
            sys.stderr.flush()
            break

        else:
            response = {"type": "error", "message": f"unknown type: {msg_type}"}
            print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
