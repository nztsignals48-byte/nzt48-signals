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
from brain.indicators.volume_analytics import calculate_rvol, volume_divergence
from brain.indicators.hurst import estimate_hurst, classify_regime
from brain.vwap import VWAPBar, VWAPCalculator
from brain.rsi_ibs import calculate_rsi, calculate_ibs, calculate_sma
from brain.gap_detector import calculate_gap_pct
from python_brain.ouroboros.cost_model import costs as _cost_model

MAX_BARS = 500

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

SIGNAL_COOLDOWN_TICKS = _load_cooldown_from_config()
_last_signal_tick = {}  # ticker_id → tick count when last signal was emitted

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
    """Load ticker blacklist from dynamic_weights.toml.

    Ouroboros generates this list from persistent_memory: tickers with
    WR < 30% over 10+ trades are blacklisted. Signals for blacklisted
    tickers are suppressed before any gate evaluation.

    BUILD NOW item N1c from IMPLEMENTATION_MASTER_PLAN v6.0.
    """
    global _ticker_blacklist_loaded, _ticker_blacklist
    if _ticker_blacklist_loaded:
        return _ticker_blacklist
    _ticker_blacklist_loaded = True
    dw_path = "/app/config/dynamic_weights.toml"
    if not os.path.exists(dw_path):
        return _ticker_blacklist
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(dw_path, "rb") as f:
            data = tomllib.load(f)
        bl = data.get("ticker_blacklist", {}).get("tickers", [])
        if bl:
            _ticker_blacklist = set(bl)
            sys.stderr.write(
                "Bridge: loaded ticker blacklist ({} tickers): {}\n".format(
                    len(bl), ", ".join(bl[:10])
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

    # Compute RSI(2) from close prices
    prices = [t["last"] for t in ticks]
    rsi_2 = calculate_rsi(prices, period=2)
    if rsi_2 is None:
        rsi_2 = 50.0

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


def process_tick(msg):
    """Process a tick message, return a response dict.

    Evaluates BOTH VanguardSniper (momentum) and the Autonomous Orchestrator
    (S17-S20: VWAP dip buy, gap fade, RSI/IBS, cross-market momentum).
    Returns the signal with the highest confidence. If neither fires, returns
    no_signal.
    """
    ticker_id = msg["ticker_id"]

    # Track ticker_id → symbol mapping from msg (if provided by Rust side)
    if "symbol" in msg and msg["symbol"]:
        ticker_symbols[ticker_id] = msg["symbol"]

    bar_history[ticker_id].append({
        "last": msg["last"],
        "bid": msg.get("bid", msg["last"]),
        "ask": msg.get("ask", msg["last"]),
        "high": msg.get("high", msg["last"]),
        "low": msg.get("low", msg["last"]),
        "volume": msg.get("volume", 0),
        "timestamp_ns": msg.get("timestamp_ns", 0),
    })

    ticks = list(bar_history[ticker_id])

    # Sprint 7: VWAP session reset — detect date change and reset VWAP calculator.
    ts_ns = msg.get("timestamp_ns", 0)
    if ts_ns > 0:
        from datetime import datetime, timezone
        _tick_dt = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        _tick_date = _tick_dt.strftime("%Y-%m-%d")
        _prev_date = _last_vwap_date.get(ticker_id)
        if _prev_date is not None and _tick_date != _prev_date:
            vwap_calculators[ticker_id].reset()
        _last_vwap_date[ticker_id] = _tick_date

    # =========================================================================
    # FIX 1: Aggregate 5-second bars into 5-MINUTE bars for indicator computation.
    # Raw 5-second bars produce meaningless indicators (ADX=99, Hurst=1.0).
    # 5-minute bars give proper trend/regime readings.
    # =========================================================================
    BARS_PER_5MIN = 60  # 60 × 5s = 5 minutes
    n_5min_bars = len(ticks) // BARS_PER_5MIN

    # Aggregate into 5-minute OHLCV bars (cached — only recompute when new bar forms)
    cached = _bar_cache.get(ticker_id)
    if cached and cached[0] == n_5min_bars:
        bars_5m = cached[1]
    else:
        bars_5m = []
        for i in range(n_5min_bars):
            chunk = ticks[i * BARS_PER_5MIN : (i + 1) * BARS_PER_5MIN]
            bar = {
                "open": chunk[0]["last"],
                "high": max(t["last"] for t in chunk),
                "low": min(t["last"] for t in chunk),
                "close": chunk[-1]["last"],
                "volume": sum(t["volume"] for t in chunk),
                "last": chunk[-1]["last"],
            }
            bars_5m.append(bar)
        _bar_cache[ticker_id] = (n_5min_bars, bars_5m)

    # Compute indicators on 5-MINUTE bars (not 5-second)
    if bars_5m:
        prices_5m = [b["close"] for b in bars_5m]
        volumes_5m = [b["volume"] for b in bars_5m]
        rvol = calculate_rvol(volumes_5m, window=20) if len(volumes_5m) >= 20 else 1.0
        hurst = estimate_hurst(prices_5m, max_lag=min(20, len(prices_5m) - 1)) if len(prices_5m) >= 5 else 0.5
        hurst_regime = classify_regime(hurst)
        vol_div = volume_divergence(prices_5m, volumes_5m, window=10) if len(prices_5m) >= 10 else 0.0
        adx = _compute_adx([{"last": b["close"], "high": b["high"], "low": b["low"],
                             "volume": b["volume"]} for b in bars_5m])
    else:
        # Not enough data for 5-min bars yet — use raw but mark as unreliable
        volumes = [t["volume"] for t in ticks]
        prices = [t["last"] for t in ticks]
        rvol = calculate_rvol(volumes, window=20)
        hurst = estimate_hurst(prices, max_lag=20)
        hurst_regime = classify_regime(hurst)
        vol_div = volume_divergence(prices, volumes, window=10)
        adx = _compute_adx(ticks)

    # =========================================================================
    # FIX 7: Volume trend slope (replaces static RVOL threshold)
    # Rising volume = real momentum building. Flat/falling volume = noise.
    # =========================================================================
    vol_slope = 0.0
    if len(bars_5m) >= 5:
        recent_vols = [b["volume"] for b in bars_5m[-10:]]
        if len(recent_vols) >= 3:
            # Simple linear regression slope of volume
            n = len(recent_vols)
            x_mean = (n - 1) / 2.0
            y_mean = sum(recent_vols) / n
            num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(recent_vols))
            den = sum((i - x_mean) ** 2 for i in range(n))
            vol_slope = num / den if den > 0 else 0.0

    no_signal_base = {
        "type": "no_signal",
        "ticker_id": ticker_id,
        "rvol": rvol,
        "hurst": hurst,
        "hurst_regime": hurst_regime,
        "volume_divergence": vol_div,
    }

    # Common indicator dict for gate veto logging — captures EVERYTHING an LLM needs
    bid = msg.get("bid", 0)
    ask = msg.get("ask", 0)
    spread_pct_raw = ((ask - bid) / ((ask + bid) / 2) * 100) if bid > 0 and ask > 0 else 0
    vwap_dist_raw = 0.0
    _vc = vwap_calculators.get(ticker_id)
    if _vc:
        _vh = _vc.get_history()
        if _vh:
            _lv = _vh[-1]
            if _lv > 0:
                vwap_dist_raw = (msg["last"] - _lv) / _lv * 100
    _ind = {
        "hurst": hurst, "adx": adx, "rvol": rvol, "vol_slope": vol_slope,
        "n_5min_bars": n_5min_bars, "n_ticks": len(ticks), "hurst_regime": hurst_regime,
        "spread_pct": spread_pct_raw, "vwap_dist_pct": vwap_dist_raw,
        "bid": bid, "ask": ask, "leverage": msg.get("leverage", 1),
        "session_mode": msg.get("session_mode", "?"),
        "volume": msg.get("volume", 0),
    }

    # =========================================================================
    # N1c: Ticker blacklist enforcement (from Ouroboros learning)
    # Tickers with WR < 30% over 10+ trades are blacklisted in dynamic_weights.toml.
    # Suppress ALL signals before wasting compute on indicators.
    # BUILD NOW item N1c from IMPLEMENTATION_MASTER_PLAN v6.0.
    # =========================================================================
    blacklist = _load_ticker_blacklist()
    sym = ticker_symbols.get(ticker_id, "")
    if sym and sym in blacklist:
        # Don't log every tick (too noisy) — just first occurrence per day
        global _blacklist_warned, _blacklist_warned_date
        import datetime as _dt
        _today = _dt.date.today().isoformat()
        if _blacklist_warned_date != _today:
            _blacklist_warned = set()
            _blacklist_warned_date = _today
        if sym not in _blacklist_warned:
            sys.stderr.write(f"BLACKLIST_VETO: {sym} (tid={ticker_id}) suppressed by Ouroboros blacklist\n")
            sys.stderr.flush()
            _blacklist_warned.add(sym)
        return no_signal_base

    # =========================================================================
    # FIX 2: Raise warm-up to 200 bars (= 16 min of 5-second data = 3+ 5-min bars)
    # Need at least 3 five-minute bars for any meaningful indicator reading.
    # =========================================================================
    MIN_WARMUP_BARS = 200  # Was 50. Now 200 = 16 min = 3+ five-minute bars
    if len(ticks) < MIN_WARMUP_BARS:
        # Don't log warm-up vetoes (too noisy — every tick for first 16 min)
        return no_signal_base

    # ---- Diagnostic logging (every 500th tick per ticker) ----
    _tick_counts[ticker_id] = _tick_counts.get(ticker_id, 0) + 1
    if _tick_counts[ticker_id] % 500 == 1:
        sys.stderr.write(
            f"BRIDGE_DIAG: tid={ticker_id} bars={len(ticks)} "
            f"hurst={hurst:.3f}({hurst_regime}) adx={adx:.1f} rvol={rvol:.2f} "
            f"price={msg['last']:.4f} vol={msg.get('volume', 0)}\n"
        )
        sys.stderr.flush()

    # ---- Phase E: Indicator gates from Ouroboros indicator_intelligence ----
    # Apply discovered threshold rules that improve WR. Each gate is a
    # pre-signal filter: if the indicator violates the gate, suppress signal.
    gates = _load_indicator_gates()
    indicator_values = {"adx": adx, "hurst": hurst, "rvol": rvol}
    for gate in gates:
        ind = gate.get("indicator", "")
        direction = gate.get("direction", "above")
        threshold = gate.get("threshold", 0)
        val = indicator_values.get(ind)
        if val is not None:
            if direction == "above" and val < threshold:
                _log_gate_veto(ticker_id, "indicator_gate", msg["last"], _ind,
                               "{} {:.2f} < {:.2f} required".format(ind, val, threshold))
                return no_signal_base
            elif direction == "below" and val > threshold:
                _log_gate_veto(ticker_id, "indicator_gate", msg["last"], _ind,
                               "{} {:.2f} > {:.2f} limit".format(ind, val, threshold))
                return no_signal_base

    # =========================================================================
    # N3a: Structural Tradability Score (0-100)
    # Pre-entry quality score assessing market microstructure conditions.
    # Score < 30 = suppress signal. Score > 70 = confidence boost.
    # Components: spread quality, regime clarity, volume, MTF alignment, ADX.
    # BUILD NOW item N3a from IMPLEMENTATION_MASTER_PLAN v6.0.
    # =========================================================================
    sts_components = {}

    # Component 1: Spread quality (0-25 pts)
    # Tighter spread → higher score. Leveraged ETPs have structural wider spreads.
    _bid = msg.get("bid", 0)
    _ask = msg.get("ask", 0)
    if _bid > 0 and _ask > 0:
        _sprd = (_ask - _bid) / ((_ask + _bid) / 2) * 100
        # 0% spread = 25pts, 1% = 15pts, 2% = 5pts, >3% = 0pts
        sts_components["spread"] = max(0, min(25, int(25 - _sprd * 8.3)))
    else:
        sts_components["spread"] = 10  # No quote data → middling score

    # Component 2: Regime clarity (0-25 pts)
    # Clear trending or clear mean-reverting = high clarity.
    # Random walk = low clarity → low score.
    if hurst > 0.01:
        # |H - 0.5| = deviation from random walk. 0.0 = pure random, 0.5 = pure trending/reverting
        clarity = abs(hurst - 0.5) / 0.5  # 0.0 to 1.0
        sts_components["regime_clarity"] = min(25, int(clarity * 25))
    else:
        sts_components["regime_clarity"] = 0

    # Component 3: Volume quality (0-20 pts)
    # Rising volume + decent RVOL = good. Flat volume + low RVOL = bad.
    # FIXED (Sprint 5, T-05): Lowered RVOL thresholds — LSE ETPs average RVOL 0.8-1.2.
    # Old threshold (1.5) killed 40%+ of valid signals.
    # TODO(Sprint 6): Make configurable, per-session baselines from Ouroboros.
    vol_score = 0
    if rvol > 1.0:
        vol_score += 10
    elif rvol > 0.7:
        vol_score += 5
    if vol_slope > 0:
        vol_score += 10
    elif vol_slope == 0:
        vol_score += 3
    sts_components["volume"] = min(20, vol_score)

    # Component 4: ADX trend strength (0-15 pts)
    # FIXED (Sprint 5, T-04): Lowered thresholds — LSE ETPs average ADX 18-22.
    # Old thresholds (25/35) were killing 30%+ of valid signals.
    # New: ADX > 20 = strong, ADX > 30 = very strong, ADX > 12 = some trend
    # TODO(Sprint 6): Make configurable from config.toml, per-regime adaptive.
    if adx >= 30:
        sts_components["adx_strength"] = 15
    elif adx >= 20:
        sts_components["adx_strength"] = 10
    elif adx >= 12:
        sts_components["adx_strength"] = 5
    else:
        sts_components["adx_strength"] = 0

    # Component 5: Data quality (0-15 pts)
    # More bars = more reliable indicators. >500 bars = full confidence.
    data_bars = len(ticks)
    if data_bars >= 500:
        sts_components["data_quality"] = 15
    elif data_bars >= 300:
        sts_components["data_quality"] = 10
    elif data_bars >= 200:
        sts_components["data_quality"] = 5
    else:
        sts_components["data_quality"] = 2

    structural_score = sum(sts_components.values())

    # Gate: suppress if structural score too low (< 30 = poor microstructure)
    if structural_score < 30:
        _log_gate_veto(ticker_id, "structural_tradability", msg["last"],
                       {**_ind, "structural_score": structural_score, **sts_components},
                       "STS={}/100 < 30 minimum".format(structural_score))
        return no_signal_base

    # Add structural score to indicator dict for downstream logging
    _ind["structural_score"] = structural_score

    # =========================================================================
    # FIX 3: Leverage-aware confidence floor (65 for 3x, 80 for 5x)
    # Low-confidence trades on leveraged products are noise, not edge.
    # =========================================================================
    leverage = msg.get("leverage", 1)
    if leverage >= 5:
        leverage_conf_floor = 80
    elif leverage >= 3:
        leverage_conf_floor = 65
    else:
        leverage_conf_floor = 45  # Unleveraged: lower bar OK

    # =========================================================================
    # FIX 4: VWAP pullback check — reject if buying extension
    # If price is >1.5% above VWAP, we're chasing. Wait for pullback.
    # =========================================================================
    vwap_calc = vwap_calculators.get(ticker_id)
    _vwap_hist = vwap_calc.get_history() if vwap_calc else []
    if _vwap_hist and len(ticks) > 60:
        last_vwap = _vwap_hist[-1]
        if last_vwap > 0:
            vwap_distance_pct = (msg["last"] - last_vwap) / last_vwap * 100
            # For LONG entries: reject if price too far ABOVE VWAP (chasing)
            if vwap_distance_pct > 1.5:
                _log_gate_veto(ticker_id, "vwap_extension", msg["last"],
                               {**_ind, "vwap": last_vwap, "vwap_dist_pct": vwap_distance_pct},
                               "price {:.1f}% above VWAP (max 1.5%)".format(vwap_distance_pct))
                return no_signal_base
            # For pullback buy: ideal entry is price near VWAP (within ±0.5%)
            # Boost confidence if price is pulling back to VWAP from above
            is_vwap_pullback = 0.0 <= vwap_distance_pct <= 0.5

    # =========================================================================
    # FIX 6: Regime gate on 5-minute Hurst — block momentum on mean-reverting
    # Hurst < 0.45 on 5-min bars = mean-reverting. Don't run momentum.
    # Hurst > 0.55 on 5-min bars = trending. Momentum OK.
    # Hurst 0.45-0.55 = random. Reduce confidence.
    # =========================================================================
    if n_5min_bars >= 5 and hurst > 0.01:  # hurst=0.0 means insufficient data, not mean-reverting
        if hurst < 0.40:
            # Strongly mean-reverting on 5-min timeframe — suppress momentum signals
            _log_gate_veto(ticker_id, "hurst_mean_reverting", msg["last"], _ind,
                           "hurst={:.3f} < 0.40 (mean-reverting regime)".format(hurst))
            return no_signal_base
        elif hurst < 0.50:
            # Weakly mean-reverting / random — reduce confidence by 15
            leverage_conf_floor = max(leverage_conf_floor, 70)

    # =========================================================================
    # FIX 9: Volume trend gate — require rising volume for momentum entry
    # Flat/falling volume = noise move. Rising volume = real flow.
    # =========================================================================
    # Only gate on volume slope when we actually have volume data
    has_volume = any(b.get("volume", 0) > 0 for b in bars_5m[-5:]) if bars_5m else False
    if n_5min_bars >= 5 and has_volume and vol_slope <= 0:
        # Volume not rising — suppress momentum signal
        # Only allow if very high confidence from other factors
        leverage_conf_floor = max(leverage_conf_floor, 75)

    # ---- Evaluate VanguardSniper (momentum + any non-reverting regime) ----
    vanguard_signal = None

    # Phase E: Apply adaptive confidence floor — use the HIGHER of adaptive and leverage floors
    # Passed as parameter to vanguard_evaluate() — no global config mutation.
    adaptive_floor = _load_adaptive_floor()
    effective_floor = leverage_conf_floor
    if adaptive_floor is not None:
        effective_floor = max(leverage_conf_floor, adaptive_floor)

    # =========================================================================
    # FIX 10: Multi-timeframe confirmation before VanguardSniper
    # Require 5-second EMA, 1-minute EMA, and 5-minute EMA all trending same direction.
    # This prevents whipsaw entries where fast timeframe disagrees with slow.
    # =========================================================================
    mtf_aligned = True  # Default pass if not enough data
    if len(ticks) >= 60 and len(bars_5m) >= 3:
        # 5-second EMA (last 20 ticks)
        ema_5s = sum(t["last"] for t in ticks[-20:]) / 20
        ema_5s_prev = sum(t["last"] for t in ticks[-40:-20]) / 20 if len(ticks) >= 40 else ema_5s
        trend_5s = 1 if ema_5s > ema_5s_prev else -1

        # 1-minute EMA (last 12 five-second bars)
        last_12 = ticks[-12:] if len(ticks) >= 12 else ticks
        prev_12 = ticks[-24:-12] if len(ticks) >= 24 else last_12
        ema_1m = sum(t["last"] for t in last_12) / len(last_12)
        ema_1m_prev = sum(t["last"] for t in prev_12) / len(prev_12)
        trend_1m = 1 if ema_1m > ema_1m_prev else -1

        # 5-minute EMA (last 3 five-minute bars)
        ema_5m = sum(b["close"] for b in bars_5m[-3:]) / min(3, len(bars_5m))
        ema_5m_prev = sum(b["close"] for b in bars_5m[-6:-3]) / min(3, len(bars_5m)) if len(bars_5m) >= 6 else ema_5m
        trend_5m = 1 if ema_5m > ema_5m_prev else -1

        # All 3 must agree for momentum entry
        mtf_aligned = (trend_5s == trend_1m == trend_5m)

    if not mtf_aligned:
        _log_gate_veto(ticker_id, "mtf_misaligned", msg["last"],
                       {**_ind, "trend_5s": trend_5s, "trend_1m": trend_1m, "trend_5m": trend_5m},
                       "5s={} 1m={} 5m={} (need all same)".format(
                           "up" if trend_5s > 0 else "down",
                           "up" if trend_1m > 0 else "down",
                           "up" if trend_5m > 0 else "down"))
        return no_signal_base

    # Hurst regime gating on 5-MINUTE bars (already computed above).
    # The earlier regime gate (FIX 6) already blocks hurst < 0.40.
    # This gate is a softer check: require trending or random for VanguardSniper.
    if hurst >= 0.40 or hurst_regime in ("trending", "random"):
        # FIX 1: Pass 5-minute bars to VanguardSniper if available, else raw ticks
        eval_ticks = [{"last": b["close"], "high": b["high"], "low": b["low"],
                       "bid": b["close"], "ask": b["close"],
                       "volume": b["volume"]} for b in bars_5m] if bars_5m else ticks
        result = vanguard_evaluate(eval_ticks, confidence_floor=effective_floor)
        if result is not None:
            # Run 12-factor Kelly sizing.
            total_trades = msg.get("total_trades", 0)
            kelly = kelly_12factor(
                win_rate_raw=msg.get("win_rate", 0.5),
                total_trades=total_trades,
                avg_win=msg.get("avg_win", 0.02),
                avg_loss=msg.get("avg_loss", 0.02),
                leverage_factor=msg.get("leverage", 3),
                realized_vol_annual=msg.get("realized_vol", 0.30),
                correlation_to_portfolio=msg.get("correlation", 0.0),
                current_drawdown_pct=msg.get("drawdown_pct", 0.0),
                amihud_illiq=msg.get("amihud", 0.0),
                regime=hurst_regime if hurst_regime != "random" else msg.get("regime", "normal"),
                spread_pct=msg.get("spread_pct", 0.1),
                time_of_day_fraction=msg.get("time_fraction", 0.5),
                confidence=result["confidence"],
                portfolio_heat_pct=msg.get("heat_pct", 0.0),
                equity=msg.get("equity", 10000.0),
                price=msg["last"],
            )

            # Paper bootstrap floor
            if total_trades < 50:
                preliminary_kelly = result["kelly_fraction"]
                if kelly["kelly_fraction"] < preliminary_kelly:
                    equity = msg.get("equity", 10000.0)
                    price = max(msg["last"], 1e-9)
                    kelly["kelly_fraction"] = preliminary_kelly
                    kelly["shares"] = max(int(preliminary_kelly * equity / price), 1)

            vanguard_signal = {
                "type": "signal",
                "ticker_id": ticker_id,
                "direction": "Long",
                "confidence": result["confidence"],
                "kelly_fraction": kelly["kelly_fraction"],
                "shares": kelly["shares"],
                "strategy": "VanguardSniper",
                "rvol": rvol,
                "hurst": hurst,
                "hurst_regime": hurst_regime,
                "volume_divergence": vol_div,
                "adx": adx,
                "vol_slope": vol_slope,
                "vwap_dist_pct": vwap_dist_raw,
                "structural_score": structural_score,
            }

    # ---- Evaluate Autonomous Orchestrator (S17-S20, all regimes) ----
    orchestrator_signal = None

    # Orchestrator needs at least a few bars of history to be meaningful
    if len(ticks) >= 5:
        try:
            intent = _evaluate_orchestrator(msg, ticks, rvol, hurst, hurst_regime, adx)
            if intent is not None:
                # Convert TradeIntent → signal dict (same format as VanguardSniper)
                # Direction mapping: "long" → "Long", "inverse" → "Short"
                direction = "Long" if intent.direction == "long" else "Short"

                # Kelly fraction from orchestrator: use sizing_mult * confidence / 1000
                # (preliminary sizing, same approach as VanguardSniper)
                orch_kelly = min(intent.confidence * intent.sizing_mult / 1000.0, 0.20)

                # Compute shares from Kelly fraction
                equity = msg.get("equity", 10000.0)
                price = max(msg["last"], 1e-9)
                orch_shares = max(int(orch_kelly * equity / price), 1) if orch_kelly > 0 else 0

                orchestrator_signal = {
                    "type": "signal",
                    "ticker_id": ticker_id,
                    "direction": direction,
                    "confidence": intent.confidence,
                    "kelly_fraction": orch_kelly,
                    "shares": orch_shares,
                    "strategy": f"Orchestrator_{intent.strategy_name}",
                    "rvol": rvol,
                    "hurst": hurst,
                    "hurst_regime": hurst_regime,
                    "volume_divergence": vol_div,
                    "adx": adx,
                    "vol_slope": vol_slope,
                    "vwap_dist_pct": vwap_dist_raw,
                    "structural_score": structural_score,
                }
        except Exception as e:
            # Orchestrator failure must never block VanguardSniper.
            # Log and continue — VanguardSniper result (if any) is still valid.
            sys.stderr.write(f"Bridge: orchestrator error (non-fatal): {e}\n")
            sys.stderr.flush()

    # ---- LSE Leveraged ETP Boost during LSE hours ----
    # During LSE hours (08:00-16:30 London), boost LSE leveraged ETPs by +20 confidence
    # so they are preferred over raw US equities (NVD3.L preferred over NVDA etc.)
    from python_brain.ouroboros.contract_loader import load_lse_symbols
    lse_symbols = set(load_lse_symbols())
    LSE_LEVERAGED_TICKERS = set(range(len(lse_symbols)))  # Ticker IDs for LSE ETPs
    symbol = ticker_symbols.get(ticker_id, "")
    is_lse_leveraged = ticker_id in LSE_LEVERAGED_TICKERS or symbol in lse_symbols

    if is_lse_leveraged:
        london_secs = msg.get("london_time_secs", 0)
        lse_open = 8 * 3600    # 08:00
        lse_close = 16 * 3600 + 30 * 60  # 16:30
        if lse_open <= london_secs < lse_close:
            # Boost confidence for LSE leveraged ETPs during LSE hours
            if vanguard_signal:
                vanguard_signal["confidence"] = min(vanguard_signal["confidence"] + 20, 100)
            if orchestrator_signal:
                orchestrator_signal["confidence"] = min(orchestrator_signal["confidence"] + 20, 100)

    # ---- Phase G: Pre-emission quality filters ----
    # These filters run AFTER signal generation but BEFORE emission.
    # They suppress low-quality contexts that produce immediate stop-outs.

    # G1: Spread quality gate — reject if bid/ask spread too wide (Q-051: uses CostModel)
    bid = msg.get("bid", 0)
    ask = msg.get("ask", 0)
    if bid > 0 and ask > 0:
        spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
        # Leverage-aware spread limits derived from CostModel.spread_veto_pct
        # Leveraged ETPs (3x+) get ~6.7x the base spread gate (structural wider spreads)
        _base_spread_gate = _cost_model.spread_veto_pct * 100  # 0.3% -> 0.3
        spread_limit = _base_spread_gate * 6.67 if leverage >= 3 else _base_spread_gate * 1.67
        if spread_pct > spread_limit:
            _log_gate_veto(ticker_id, "spread_too_wide", msg["last"],
                           {**_ind, "spread_pct": spread_pct, "bid": bid, "ask": ask},
                           "spread={:.2f}% > {:.1f}%".format(spread_pct, spread_limit))
            return no_signal_base

    # G2: Extension filter — reject if price moved >3% from session VWAP
    # (buying extension = immediate adverse excursion)
    vwap_calc = vwap_calculators.get(ticker_id)
    _g2_vwap_hist = vwap_calc.get_history() if vwap_calc else []
    if _g2_vwap_hist and len(ticks) > 30:
        last_vwap = _g2_vwap_hist[-1]
        if last_vwap > 0:
            extension = abs(msg["last"] - last_vwap) / last_vwap * 100
            if extension > 3.0:
                _log_gate_veto(ticker_id, "vwap_extension_3pct", msg["last"],
                               {**_ind, "extension_pct": extension, "vwap": last_vwap},
                               "extension={:.1f}% from VWAP (max 3%)".format(extension))
                return no_signal_base

    # ---- Per-ticker signal cooldown (prevent NVD3.L-style spam) ----
    # After emitting a signal for a ticker, suppress for COOLDOWN_TICKS ticks.
    tick_count = _tick_counts.get(ticker_id, 0)
    last_sig = _last_signal_tick.get(ticker_id, -SIGNAL_COOLDOWN_TICKS - 1)
    if tick_count - last_sig < SIGNAL_COOLDOWN_TICKS:
        remaining = SIGNAL_COOLDOWN_TICKS - (tick_count - last_sig)
        _log_gate_veto(ticker_id, "cooldown", msg["last"], _ind,
                       "{}s remaining".format(remaining * 5))
        return no_signal_base

    # ---- Select best signal (highest confidence wins) ----
    best = None
    if vanguard_signal and orchestrator_signal:
        best = orchestrator_signal if orchestrator_signal["confidence"] > vanguard_signal["confidence"] else vanguard_signal
    elif vanguard_signal:
        best = vanguard_signal
    elif orchestrator_signal:
        best = orchestrator_signal

    if best:
        # N3a: Structural tradability score — FIXED (Sprint 5, SK-03).
        # Preserve raw strategy_confidence for risk gate CHECK 10 (unmodified).
        # Adjusted confidence is for logging/telemetry only.
        best["strategy_confidence"] = best["confidence"]  # Raw, unmodified
        # Score > 70: boost adjusted confidence by (score - 70) / 5 (max +6)
        # Score 50-70: no adjustment
        # Score 30-50: penalize adjusted confidence by (50 - score) / 5 (max -4)
        if structural_score > 70:
            sts_boost = min(6, (structural_score - 70) // 5)
            best["confidence"] = min(100, best["confidence"] + sts_boost)
        elif structural_score < 50:
            sts_penalty = min(4, (50 - structural_score) // 5)
            best["confidence"] = max(0, best["confidence"] - sts_penalty)
        best["structural_score"] = structural_score

        # Record cooldown timestamp
        _last_signal_tick[ticker_id] = tick_count
        return best
    else:
        return no_signal_base


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
    sys.stderr.write("Python Brain Bridge: started\n")
    sys.stderr.flush()

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
