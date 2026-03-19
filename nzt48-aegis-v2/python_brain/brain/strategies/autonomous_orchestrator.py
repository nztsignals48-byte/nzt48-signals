"""Autonomous Orchestrator — Session-aware strategy selector and autonomous signal generator.

Reads the ticker priority ranking, selects the best strategy for the current session,
and generates OrderIntents autonomously. No human decision required.

PURE FUNCTION. No side effects. No I/O. No state mutation. No threading (H07).

The orchestrator is called every scan cycle (5 seconds) and:
1. Determines the current session window
2. Filters strategies eligible for this session + regime
3. Ranks tickers by priority score for this session
4. For each top-ranked ticker, evaluates strategy entry conditions
5. Generates OrderIntents for tickers that pass ALL filters
6. Returns ranked list of intents sorted by (priority × confidence)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ============================================================================
# Session and Regime Classification
# ============================================================================

class SessionWindow(Enum):
    """LSE sub-sessions (London local time)."""
    LSE_AUCTION_OPEN = "07:50-08:00"
    LSE_OPEN_VOLATILITY = "08:00-08:30"
    LSE_MORNING = "08:30-10:30"
    LSE_MIDDAY = "10:30-14:30"
    US_OVERLAP = "14:30-16:00"
    LSE_EOD = "16:00-16:30"
    US_POWER_HOUR = "20:00-21:00"
    DARK = "21:00-23:00"
    ASIAN = "23:00-08:00"


class RegimeType(Enum):
    """Market regime from Hurst + ADX."""
    TRENDING = "trending"
    MEAN_REVERTING = "mean_reverting"
    RANDOM = "random"


class StrategyFamily(Enum):
    """Strategy families."""
    MEAN_REVERSION = "mean_reversion"
    MOMENTUM = "momentum"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class TickerState:
    """Current state of a single ticker — all data needed for strategy evaluation."""
    ticker_id: int
    symbol: str
    last_price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    spread_bps: float = 0.0
    volume: int = 0
    rvol: float = 1.0              # Relative volume vs 20-bar avg
    atr: float = 0.0
    rsi_2: float = 50.0
    ibs: float = 0.5
    vwap: float = 0.0
    vwap_sigma: float = 0.0       # Current price's distance from VWAP in sigma
    vwap_slope: float = 0.0       # VWAP rate of change
    adx: float = 20.0
    hurst: float = 0.50
    sma_200: float = 0.0
    sma_5: float = 0.0
    daily_open: float = 0.0
    prev_close: float = 0.0
    gap_pct: float = 0.0          # Overnight gap percentage
    volume_profile: str = "normal" # "declining", "accelerating", "normal"
    leverage: int = 1
    is_inverse: bool = False
    priority_score: float = 50.0  # From ticker ranker (0-100)


@dataclass
class MarketContext:
    """Global market context for strategy evaluation."""
    london_time_secs: int = 36000  # Seconds from midnight London local
    session: SessionWindow = SessionWindow.LSE_MIDDAY
    regime: RegimeType = RegimeType.RANDOM
    vix: float = 20.0
    spy_first_30min_return: float = 0.0  # S&P 500 first 30 min return
    nq_overnight_change: float = 0.0     # NQ futures overnight change
    broad_market_at_lows: bool = False
    has_news_catalyst: bool = False
    spx_126d_return: float = 0.05        # 126-day SPX return


@dataclass
class StrategyConfig:
    """Configuration for a single strategy — loaded from strategies.toml."""
    name: str
    enabled: bool = True
    priority: int = 3
    family: StrategyFamily = StrategyFamily.MEAN_REVERSION
    base_confidence: float = 65.0
    session_eligible: List[str] = field(default_factory=list)
    session_blocked: List[str] = field(default_factory=list)
    regime_eligible: List[str] = field(default_factory=list)
    regime_blocked: List[str] = field(default_factory=list)
    sizing_mult: float = 1.0
    ticker_whitelist: List[str] = field(default_factory=list)
    ticker_preferred: List[str] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TradeIntent:
    """Autonomous trade intent — ready to become an OrderIntent."""
    ticker_id: int
    symbol: str
    strategy_name: str
    direction: str = "long"       # "long" or "inverse"
    confidence: float = 0.0       # Final confidence (0-100)
    priority_score: float = 0.0   # Ticker priority (0-100)
    combined_score: float = 0.0   # priority × confidence / 100
    sizing_mult: float = 1.0      # Strategy-specific sizing multiplier
    stop_type: str = "atr"        # "atr", "vwap_sigma", "percentage"
    stop_distance: float = 0.0    # Stop distance in the stop_type's units
    target_type: str = "vwap"     # "vwap", "gap_fill", "sma", "atr_trail"
    target_distance: float = 0.0
    time_stop_minutes: int = 90
    features: Dict[str, float] = field(default_factory=dict)


# ============================================================================
# Session Detection
# ============================================================================

def detect_session(london_time_secs: int) -> SessionWindow:
    """Determine the current session window from London local time (seconds from midnight)."""
    h = london_time_secs // 3600
    m = (london_time_secs % 3600) // 60
    t = h * 100 + m  # HHMM format

    if t < 750:
        return SessionWindow.ASIAN
    if t < 800:
        return SessionWindow.LSE_AUCTION_OPEN
    if t < 830:
        return SessionWindow.LSE_OPEN_VOLATILITY
    if t < 1030:
        return SessionWindow.LSE_MORNING
    if t < 1430:
        return SessionWindow.LSE_MIDDAY
    if t < 1600:
        return SessionWindow.US_OVERLAP
    if t < 1630:
        return SessionWindow.LSE_EOD
    if t < 2000:
        # Between LSE close and US power hour — limited activity
        return SessionWindow.US_OVERLAP  # Still US cash hours
    if t < 2100:
        return SessionWindow.US_POWER_HOUR
    if t < 2300:
        return SessionWindow.DARK
    return SessionWindow.ASIAN


def session_aggressiveness(session: SessionWindow) -> float:
    """Default aggressiveness multiplier per session."""
    return {
        SessionWindow.LSE_AUCTION_OPEN: 0.0,    # No trading during auctions
        SessionWindow.LSE_OPEN_VOLATILITY: 0.5,  # Half size
        SessionWindow.LSE_MORNING: 1.0,
        SessionWindow.LSE_MIDDAY: 0.8,
        SessionWindow.US_OVERLAP: 1.2,           # Best liquidity
        SessionWindow.LSE_EOD: 0.3,
        SessionWindow.US_POWER_HOUR: 0.7,
        SessionWindow.DARK: 0.0,                 # No trading
        SessionWindow.ASIAN: 0.3,                # Light monitoring only
    }.get(session, 0.5)


# ============================================================================
# Strategy Eligibility Checks
# ============================================================================

def is_strategy_eligible(
    strategy: StrategyConfig,
    session: SessionWindow,
    regime: RegimeType,
) -> bool:
    """Check if a strategy is eligible for the current session and regime."""
    if not strategy.enabled:
        return False

    # Session check
    session_str = session.value
    if strategy.session_blocked:
        for blocked in strategy.session_blocked:
            if _time_in_range(session_str, blocked):
                return False

    if strategy.session_eligible:
        eligible = False
        for allowed in strategy.session_eligible:
            if _time_in_range(session_str, allowed):
                eligible = True
                break
        if not eligible:
            return False

    # Regime check
    regime_str = regime.value
    if regime_str in strategy.regime_blocked:
        return False
    if strategy.regime_eligible and regime_str not in strategy.regime_eligible:
        return False

    return True


def _time_in_range(session_value: str, range_str: str) -> bool:
    """Check if a session's start time falls within a time range string like '10:30-14:30'."""
    try:
        parts = session_value.split("-")
        session_start = parts[0]
        range_parts = range_str.split("-")
        range_start = range_parts[0]
        range_end = range_parts[1]

        def to_minutes(t: str) -> int:
            h, m = t.split(":")
            return int(h) * 60 + int(m)

        s = to_minutes(session_start)
        rs = to_minutes(range_start)
        re = to_minutes(range_end)
        return rs <= s < re
    except (ValueError, IndexError):
        return False


# ============================================================================
# Individual Strategy Evaluators
# ============================================================================

def evaluate_vwap_dip_buy(
    ticker: TickerState,
    ctx: MarketContext,
    cfg: StrategyConfig,
) -> Optional[TradeIntent]:
    """S17: VWAP Dip Buy — buy when price drops N sigma below VWAP."""
    p = cfg.params

    # Entry conditions
    entry_sigma = p.get("entry_vwap_sigma", 2.0)
    if ticker.vwap_sigma > -entry_sigma:
        return None  # Not far enough below VWAP

    # Volume filter: declining volume = noise dip (good), accelerating = breakdown (bad)
    vol_filter = p.get("entry_volume_filter", "declining")
    if vol_filter == "declining" and ticker.volume_profile == "accelerating":
        return None

    # VWAP slope filter: flat VWAP required for MR
    slope_max = p.get("entry_vwap_slope_max", 0.01)
    if abs(ticker.vwap_slope) > slope_max:
        return None

    # ADX filter
    adx_max = p.get("filter_adx_max", 25.0)
    if ticker.adx > adx_max:
        return None

    # Spread filter
    spread_max = p.get("filter_spread_max_bps", 15)
    if ticker.spread_bps > spread_max:
        return None

    # VIX filter
    vix_max = p.get("filter_vix_max", 30.0)
    if ctx.vix > vix_max:
        return None

    # Broad market not at lows
    if p.get("filter_broad_market_not_at_lows", True) and ctx.broad_market_at_lows:
        return None

    # No news catalyst
    if p.get("filter_no_news_catalyst", True) and ctx.has_news_catalyst:
        return None

    # Calculate confidence: deeper dip = higher confidence
    depth_bonus = min(10.0, (abs(ticker.vwap_sigma) - entry_sigma) * 5.0)
    confidence = cfg.base_confidence + depth_bonus

    return TradeIntent(
        ticker_id=ticker.ticker_id,
        symbol=ticker.symbol,
        strategy_name="vwap_dip_buy",
        direction="long",
        confidence=min(confidence, 95.0),
        priority_score=ticker.priority_score,
        combined_score=ticker.priority_score * confidence / 100.0,
        sizing_mult=cfg.sizing_mult,
        stop_type="vwap_sigma",
        stop_distance=p.get("exit_stop_sigma", 3.0),
        target_type="vwap",
        target_distance=0.0,  # Target = VWAP itself
        time_stop_minutes=int(p.get("exit_time_stop_minutes", 90)),
        features={
            "vwap_sigma": ticker.vwap_sigma,
            "vwap_slope": ticker.vwap_slope,
            "adx": ticker.adx,
            "rvol": ticker.rvol,
            "spread_bps": ticker.spread_bps,
        },
    )


def evaluate_gap_fade(
    ticker: TickerState,
    ctx: MarketContext,
    cfg: StrategyConfig,
) -> Optional[TradeIntent]:
    """S18: Gap Fade — fade overnight gaps that are liquidity-driven."""
    p = cfg.params

    # Must have a gap
    min_gap = p.get("entry_min_gap_pct", 1.5)
    max_gap = p.get("entry_max_gap_pct", 6.0)
    gap = abs(ticker.gap_pct)
    if gap < min_gap or gap > max_gap:
        return None

    # RVOL filter: information gap vs liquidity gap
    rvol_veto = p.get("filter_rvol_5min_veto", 5.0)
    rvol_max = p.get("filter_rvol_5min_max", 2.0)
    if ticker.rvol > rvol_veto:
        return None  # Information gap — DO NOT FADE
    if ticker.rvol > rvol_max:
        return None  # Uncertain gap — skip

    # No earnings
    if p.get("filter_no_earnings", True) and ctx.has_news_catalyst:
        return None

    # Spread filter
    spread_max = p.get("filter_spread_max_bps", 20)
    if ticker.spread_bps > spread_max:
        return None

    # VIX filter
    vix_max = p.get("filter_vix_max", 35.0)
    if ctx.vix > vix_max:
        return None

    # Day-of-week confidence adjustment
    # (would come from config; simplified here)

    # Direction: fade the gap
    direction = "long" if ticker.gap_pct < 0 else "inverse"

    # Confidence: smaller gaps = more confident they'll fill
    gap_confidence_bonus = max(0, (max_gap - gap) / max_gap * 10.0)
    confidence = cfg.base_confidence + gap_confidence_bonus

    return TradeIntent(
        ticker_id=ticker.ticker_id,
        symbol=ticker.symbol,
        strategy_name="gap_fade",
        direction=direction,
        confidence=min(confidence, 95.0),
        priority_score=ticker.priority_score,
        combined_score=ticker.priority_score * confidence / 100.0,
        sizing_mult=cfg.sizing_mult,
        stop_type="percentage",
        stop_distance=gap * p.get("exit_stop_pct", 1.5),
        target_type="gap_fill",
        target_distance=gap * p.get("exit_target_fill_pct", 0.75),
        time_stop_minutes=int(p.get("exit_time_stop_minutes", 120)),
        features={
            "gap_pct": ticker.gap_pct,
            "rvol_5min": ticker.rvol,
            "spread_bps": ticker.spread_bps,
        },
    )


def evaluate_rsi_ibs(
    ticker: TickerState,
    ctx: MarketContext,
    cfg: StrategyConfig,
) -> Optional[TradeIntent]:
    """S19: RSI(2)/IBS Mean Reversion — daily oversold bounce."""
    p = cfg.params

    # RSI threshold (stricter for 3x products)
    if ticker.leverage >= 3:
        rsi_thresh = p.get("entry_rsi_threshold_3x", 2.5)
        ibs_thresh = p.get("entry_ibs_threshold_3x", 0.10)
    else:
        rsi_thresh = p.get("entry_rsi_threshold", 5.0)
        ibs_thresh = p.get("entry_ibs_threshold", 0.20)

    if ticker.rsi_2 > rsi_thresh:
        return None
    if ticker.ibs > ibs_thresh:
        return None

    # Trend filter: must be above 200-day SMA
    if p.get("entry_above_sma200", True) and ticker.last_price < ticker.sma_200:
        return None

    # Max distance above SMA-200
    if ticker.sma_200 > 0:
        pct_above = (ticker.last_price - ticker.sma_200) / ticker.sma_200
        max_above = p.get("entry_max_above_sma200_pct", 5.0) / 100.0
        if pct_above > max_above:
            return None

    # Macro filter
    if p.get("entry_macro_filter", True) and ctx.spx_126d_return < 0:
        return None

    # Spread filter
    spread_max = p.get("filter_spread_max_bps", 20)
    if ticker.spread_bps > spread_max:
        return None

    # Confidence: lower RSI = deeper oversold = higher confidence
    rsi_bonus = max(0, (rsi_thresh - ticker.rsi_2) / rsi_thresh * 10.0)
    confidence = cfg.base_confidence + rsi_bonus

    # Sizing penalty for 3x products (decay risk on multi-day hold)
    sizing = cfg.sizing_mult
    if ticker.leverage >= 3:
        sizing *= p.get("sizing_3x_penalty", 0.5)

    return TradeIntent(
        ticker_id=ticker.ticker_id,
        symbol=ticker.symbol,
        strategy_name="rsi_ibs",
        direction="long",
        confidence=min(confidence, 95.0),
        priority_score=ticker.priority_score,
        combined_score=ticker.priority_score * confidence / 100.0,
        sizing_mult=sizing,
        stop_type="percentage",
        stop_distance=p.get("exit_stop_pct", 5.0),
        target_type="sma",
        target_distance=0.0,  # Exit when close > 5-day SMA
        time_stop_minutes=int(p.get("exit_max_hold_days", 10)) * 390,  # Trading minutes
        features={
            "rsi_2": ticker.rsi_2,
            "ibs": ticker.ibs,
            "sma_200_dist": (ticker.last_price - ticker.sma_200) / max(ticker.sma_200, 1e-9),
        },
    )


def evaluate_cross_market_momentum(
    ticker: TickerState,
    ctx: MarketContext,
    cfg: StrategyConfig,
) -> Optional[TradeIntent]:
    """S20: Cross-Market Momentum — US direction predicts LSE continuation."""
    p = cfg.params

    # US market must have moved enough in first 15 minutes
    min_move = p.get("entry_spy_min_move_pct", 0.3) / 100.0
    if abs(ctx.spy_first_30min_return) < min_move:
        return None

    # ADX filter: need some trend
    adx_min = p.get("filter_adx_min", 20.0)
    if ticker.adx < adx_min:
        return None

    # RVOL filter
    rvol_min = p.get("filter_rvol_min", 1.2)
    if ticker.rvol < rvol_min:
        return None

    # Hurst filter
    hurst_min = p.get("filter_hurst_min", 0.50)
    if ticker.hurst < hurst_min:
        return None

    # Spread filter
    spread_max = p.get("filter_spread_max_bps", 15)
    if ticker.spread_bps > spread_max:
        return None

    # Direction: same as US market
    if ctx.spy_first_30min_return > 0:
        direction = "long"
    else:
        direction = "inverse"

    confidence = cfg.base_confidence + abs(ctx.spy_first_30min_return) * 1000  # Bigger move = more confident
    confidence = min(confidence, 95.0)

    return TradeIntent(
        ticker_id=ticker.ticker_id,
        symbol=ticker.symbol,
        strategy_name="cross_market_momentum",
        direction=direction,
        confidence=confidence,
        priority_score=ticker.priority_score,
        combined_score=ticker.priority_score * confidence / 100.0,
        sizing_mult=cfg.sizing_mult,
        stop_type="atr",
        stop_distance=p.get("exit_trail_atr_mult", 1.5),
        target_type="atr_trail",
        target_distance=p.get("exit_trail_atr_mult", 1.5),
        time_stop_minutes=int(p.get("exit_time_stop_minutes", 90)),
        features={
            "spy_first_30min": ctx.spy_first_30min_return,
            "adx": ticker.adx,
            "hurst": ticker.hurst,
            "rvol": ticker.rvol,
        },
    )


# ============================================================================
# Strategy Evaluator Registry
# ============================================================================

STRATEGY_EVALUATORS: Dict[str, Callable] = {
    "vwap_dip_buy": evaluate_vwap_dip_buy,
    "gap_fade": evaluate_gap_fade,
    "rsi_ibs": evaluate_rsi_ibs,
    "cross_market_momentum": evaluate_cross_market_momentum,
}


# ============================================================================
# Main Orchestrator
# ============================================================================

def orchestrate(
    tickers: List[TickerState],
    ctx: MarketContext,
    strategies: List[StrategyConfig],
    max_intents: int = 6,
    log_fn: Optional[Callable[[str, str], None]] = None,
) -> List[TradeIntent]:
    """Autonomous orchestrator — the main entry point.

    Called every scan cycle (5 seconds). Returns a ranked list of trade intents
    ready to be converted to OrderIntents and sent to the RiskArbiter.

    Args:
        tickers: Current state of all tracked tickers (pre-sorted by priority_score)
        ctx: Global market context
        strategies: List of strategy configurations (loaded from strategies.toml)
        max_intents: Maximum number of intents to return per cycle
        log_fn: Optional logging callback (level, message)

    Returns:
        List of TradeIntent sorted by combined_score (descending).
        Empty list if no trading conditions are met.
    """
    # 1. Determine current session
    session = detect_session(ctx.london_time_secs)
    ctx.session = session
    aggression = session_aggressiveness(session)

    # No trading during dark/auction periods
    if aggression == 0.0:
        return []

    # 2. Filter strategies eligible for this session + regime
    eligible_strategies = [
        s for s in strategies
        if is_strategy_eligible(s, session, ctx.regime)
    ]

    if not eligible_strategies:
        return []

    # Sort by priority (lower number = higher priority)
    eligible_strategies.sort(key=lambda s: s.priority)

    # 3. Sort tickers by priority score (highest first)
    ranked_tickers = sorted(tickers, key=lambda t: t.priority_score, reverse=True)

    # 4. Evaluate each strategy against each top ticker
    all_intents: List[TradeIntent] = []

    for strategy in eligible_strategies:
        evaluator = STRATEGY_EVALUATORS.get(strategy.name)
        if evaluator is None:
            continue

        # Filter tickers by whitelist/preferred
        candidates = ranked_tickers
        if strategy.ticker_whitelist:
            candidates = [t for t in candidates if t.symbol in strategy.ticker_whitelist]

        # Evaluate top candidates (don't evaluate all 100 — just top 20 for performance)
        for ticker in candidates[:20]:
            intent = evaluator(ticker, ctx, strategy)
            if intent is not None:
                # Apply session aggressiveness to sizing
                intent.sizing_mult *= aggression
                # Recalculate combined score
                intent.combined_score = intent.priority_score * intent.confidence / 100.0
                all_intents.append(intent)

    # 5. Sort by combined score and return top N
    all_intents.sort(key=lambda i: i.combined_score, reverse=True)

    # Deduplicate: only one intent per ticker (highest score wins)
    seen_tickers = set()
    deduped = []
    for intent in all_intents:
        if intent.ticker_id not in seen_tickers:
            seen_tickers.add(intent.ticker_id)
            deduped.append(intent)

    result = deduped[:max_intents]

    if log_fn and result:
        log_fn("DEBUG", f"Orchestrator: {len(result)} intents generated "
               f"(session={session.value}, regime={ctx.regime.value}, "
               f"strategies={[s.name for s in eligible_strategies]})")

    return result


# ============================================================================
# Unit Tests
# ============================================================================

def _make_test_ticker(symbol: str = "QQQ3.L", **kwargs) -> TickerState:
    """Helper to create a test ticker with sensible defaults."""
    defaults = {
        "ticker_id": 1, "symbol": symbol, "last_price": 50.0,
        "bid": 49.98, "ask": 50.02, "spread_bps": 8.0,
        "volume": 100000, "rvol": 1.5, "atr": 1.0,
        "rsi_2": 50.0, "ibs": 0.5, "vwap": 50.5,
        "vwap_sigma": -0.5, "vwap_slope": 0.002, "adx": 20.0,
        "hurst": 0.48, "sma_200": 48.0, "sma_5": 49.5,
        "daily_open": 50.5, "prev_close": 51.0, "gap_pct": -1.0,
        "volume_profile": "declining", "leverage": 3,
        "is_inverse": False, "priority_score": 90.0,
    }
    defaults.update(kwargs)
    return TickerState(**defaults)


def _make_test_context(**kwargs) -> MarketContext:
    """Helper to create a test market context."""
    defaults = {
        "london_time_secs": 12 * 3600,  # Noon
        "regime": RegimeType.MEAN_REVERTING,
        "vix": 18.0,
        "spy_first_30min_return": 0.005,
        "nq_overnight_change": 0.008,
        "broad_market_at_lows": False,
        "has_news_catalyst": False,
        "spx_126d_return": 0.05,
    }
    defaults.update(kwargs)
    ctx = MarketContext(**defaults)
    ctx.session = detect_session(ctx.london_time_secs)
    return ctx


def _make_vwap_strategy() -> StrategyConfig:
    return StrategyConfig(
        name="vwap_dip_buy", enabled=True, priority=2,
        family=StrategyFamily.MEAN_REVERSION, base_confidence=70.0,
        session_eligible=["10:30-14:30", "14:30-16:00"],
        regime_eligible=["mean_reverting", "random"],
        params={
            "entry_vwap_sigma": 2.0, "entry_volume_filter": "declining",
            "entry_vwap_slope_max": 0.01, "filter_adx_max": 25.0,
            "filter_spread_max_bps": 15, "filter_vix_max": 30.0,
            "filter_broad_market_not_at_lows": True,
            "filter_no_news_catalyst": True,
            "exit_stop_sigma": 3.0, "exit_time_stop_minutes": 90,
        },
    )


def _make_gap_strategy() -> StrategyConfig:
    return StrategyConfig(
        name="gap_fade", enabled=True, priority=1,
        family=StrategyFamily.MEAN_REVERSION, base_confidence=72.0,
        session_eligible=["08:15-10:00"],
        regime_eligible=["mean_reverting", "random", "trending"],
        sizing_mult=0.8,
        params={
            "entry_min_gap_pct": 1.5, "entry_max_gap_pct": 6.0,
            "filter_rvol_5min_max": 2.0, "filter_rvol_5min_veto": 5.0,
            "filter_no_earnings": True, "filter_spread_max_bps": 20,
            "filter_vix_max": 35.0,
            "exit_target_fill_pct": 0.75, "exit_stop_pct": 1.5,
            "exit_time_stop_minutes": 120,
        },
    )


def test_session_detection():
    """Test session window detection from London time."""
    assert detect_session(8 * 3600 + 15 * 60) == SessionWindow.LSE_OPEN_VOLATILITY
    assert detect_session(12 * 3600) == SessionWindow.LSE_MIDDAY
    assert detect_session(15 * 3600) == SessionWindow.US_OVERLAP
    assert detect_session(16 * 3600 + 15 * 60) == SessionWindow.LSE_EOD
    assert detect_session(21 * 3600 + 30 * 60) == SessionWindow.DARK
    assert detect_session(23 * 3600 + 30 * 60) == SessionWindow.ASIAN


def test_vwap_dip_buy_triggers():
    """Test VWAP dip buy generates intent when conditions met."""
    ticker = _make_test_ticker(vwap_sigma=-2.5, adx=18.0, vwap_slope=0.003)
    ctx = _make_test_context()
    strategy = _make_vwap_strategy()
    intent = evaluate_vwap_dip_buy(ticker, ctx, strategy)
    assert intent is not None
    assert intent.strategy_name == "vwap_dip_buy"
    assert intent.confidence >= 70.0


def test_vwap_dip_buy_blocked_by_adx():
    """Test VWAP dip buy blocked when ADX too high (trending market)."""
    ticker = _make_test_ticker(vwap_sigma=-2.5, adx=30.0)
    ctx = _make_test_context()
    strategy = _make_vwap_strategy()
    intent = evaluate_vwap_dip_buy(ticker, ctx, strategy)
    assert intent is None


def test_vwap_dip_buy_blocked_by_sigma():
    """Test VWAP dip buy blocked when not enough deviation."""
    ticker = _make_test_ticker(vwap_sigma=-1.0)
    ctx = _make_test_context()
    strategy = _make_vwap_strategy()
    intent = evaluate_vwap_dip_buy(ticker, ctx, strategy)
    assert intent is None


def test_gap_fade_triggers():
    """Test gap fade generates intent for valid liquidity gap."""
    ticker = _make_test_ticker(gap_pct=-2.0, rvol=1.5, spread_bps=12.0)
    ctx = _make_test_context(london_time_secs=9 * 3600)  # 09:00
    strategy = _make_gap_strategy()
    intent = evaluate_gap_fade(ticker, ctx, strategy)
    assert intent is not None
    assert intent.strategy_name == "gap_fade"
    assert intent.direction == "long"  # Fading a gap-down


def test_gap_fade_blocked_by_rvol():
    """Test gap fade blocked when RVOL too high (information gap)."""
    ticker = _make_test_ticker(gap_pct=-2.0, rvol=6.0)
    ctx = _make_test_context(london_time_secs=9 * 3600)
    strategy = _make_gap_strategy()
    intent = evaluate_gap_fade(ticker, ctx, strategy)
    assert intent is None


def test_orchestrator_full_cycle():
    """Test full orchestrator cycle with multiple strategies and tickers."""
    tickers = [
        _make_test_ticker("QQQ3.L", ticker_id=1, vwap_sigma=-2.5, adx=18.0,
                          vwap_slope=0.003, priority_score=95.0),
        _make_test_ticker("3LUS.L", ticker_id=2, vwap_sigma=-1.0, adx=22.0,
                          priority_score=85.0),
        _make_test_ticker("NVD3.L", ticker_id=3, vwap_sigma=-3.0, adx=15.0,
                          vwap_slope=0.001, priority_score=80.0),
    ]
    ctx = _make_test_context()
    strategies = [_make_vwap_strategy()]
    intents = orchestrate(tickers, ctx, strategies)
    # Should get intents for QQQ3.L and NVD3.L (not 3LUS.L — sigma too shallow)
    assert len(intents) >= 1
    assert all(i.combined_score > 0 for i in intents)
    # Should be sorted by combined_score descending
    for i in range(len(intents) - 1):
        assert intents[i].combined_score >= intents[i + 1].combined_score


def test_orchestrator_no_intents_during_dark():
    """Test orchestrator returns empty during dark hours."""
    tickers = [_make_test_ticker()]
    ctx = _make_test_context(london_time_secs=22 * 3600)  # 22:00 = DARK
    strategies = [_make_vwap_strategy()]
    intents = orchestrate(tickers, ctx, strategies)
    assert len(intents) == 0


def test_strategy_eligibility_regime_block():
    """Test strategy blocked when regime doesn't match."""
    strategy = _make_vwap_strategy()
    assert not is_strategy_eligible(strategy, SessionWindow.LSE_MIDDAY, RegimeType.TRENDING)
    assert is_strategy_eligible(strategy, SessionWindow.LSE_MIDDAY, RegimeType.MEAN_REVERTING)


if __name__ == "__main__":
    test_session_detection()
    test_vwap_dip_buy_triggers()
    test_vwap_dip_buy_blocked_by_adx()
    test_vwap_dip_buy_blocked_by_sigma()
    test_gap_fade_triggers()
    test_gap_fade_blocked_by_rvol()
    test_orchestrator_full_cycle()
    test_orchestrator_no_intents_during_dark()
    test_strategy_eligibility_regime_block()
    print("All autonomous orchestrator tests passed (9/9)")
