"""
NZT-48 Trading System — Core Data Models
All dataclasses representing the system's core concepts:
signals, trades, positions, regime states, and bot configurations.
Every field maps to a specific section of the Master Spec.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _utcnow() -> datetime:
    """Return current UTC time with timezone info."""
    return datetime.now(timezone.utc)


# === Enums ===

class Direction(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Bot(str, enum.Enum):
    A = "A"   # UK ISA — Leveraged ETPs
    B = "B"   # US Equities — IBKR


class BotInstance(str, enum.Enum):
    """Section 64: 5 specialist bot instances."""
    BULL = "BULL"
    RANGE = "RANGE"
    BEAR = "BEAR"
    EARNINGS = "EARNINGS"
    SECTOR_ROTATION = "SECTOR_ROTATION"


class RegimeState(str, enum.Enum):
    """Section 7: 8-state regime classifier + REGIME_FLAPPING (G-09)."""
    TRENDING_UP_STRONG = "TRENDING_UP_STRONG"
    TRENDING_UP_MOD = "TRENDING_UP_MOD"
    TRENDING_DOWN_STRONG = "TRENDING_DOWN_STRONG"
    TRENDING_DOWN_MOD = "TRENDING_DOWN_MOD"
    RANGE_BOUND = "RANGE_BOUND"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    RISK_OFF = "RISK_OFF"
    SHOCK = "SHOCK"
    REGIME_FLAPPING = "REGIME_FLAPPING"  # G-09: 3+ changes in 10 min


class SignalStatus(str, enum.Enum):
    PENDING = "PENDING"
    TAKEN = "TAKEN"
    SKIPPED = "SKIPPED"


class TimeWindow(str, enum.Enum):
    """Section 10: 7 time-of-day windows (ET)."""
    CHAOS_OPEN = "CHAOS_OPEN"           # 09:30-09:35
    MORNING_MOMENTUM = "MORNING_MOMENTUM"  # 09:35-10:30
    TREND_EXTENSION = "TREND_EXTENSION"    # 10:30-11:30
    LUNCH_CHOP = "LUNCH_CHOP"              # 11:30-14:00
    AFTERNOON_PUSH = "AFTERNOON_PUSH"      # 14:00-15:00
    POWER_HOUR = "POWER_HOUR"              # 15:00-15:30
    CLOSE_MECHANICS = "CLOSE_MECHANICS"    # 15:30-16:00


class GEXRegime(str, enum.Enum):
    """Section 8: Gamma Exposure states."""
    POSITIVE = "POSITIVE"       # Market makers dampen moves
    NEGATIVE = "NEGATIVE"       # Market makers amplify moves
    FLIPPING = "FLIPPING"       # Regime change imminent


class EmotionalPattern(str, enum.Enum):
    """Section 44: 14 emotional firewall blocks."""
    REVENGE = "REVENGE"
    OVERTRADING = "OVERTRADING"
    SIZE_INFLATION = "SIZE_INFLATION"
    HOLDING_LOSERS = "HOLDING_LOSERS"
    MOVING_STOPS = "MOVING_STOPS"
    FOMO = "FOMO"
    AVERAGING_DOWN = "AVERAGING_DOWN"
    REFUSING_PROFITS = "REFUSING_PROFITS"
    FRIDAY_ANXIETY = "FRIDAY_ANXIETY"
    ONE_MORE_TRADE = "ONE_MORE_TRADE"
    HOPE = "HOPE"
    ANCHORING = "ANCHORING"
    CHASING = "CHASING"
    REVENGE_SIZING = "REVENGE_SIZING"


class DrawdownLevel(str, enum.Enum):
    """Section 60: Drawdown recovery protocol levels."""
    GREEN = "GREEN"        # Normal
    YELLOW = "YELLOW"      # -3% to -5%: Caution
    ORANGE = "ORANGE"      # -5% to -8%: Defensive
    RED = "RED"            # -8% to -10%: Recovery mode
    CRITICAL = "CRITICAL"  # -10% to -12%: Full stop
    EMERGENCY = "EMERGENCY"  # > -12%: Reset


class PDTMode(str, enum.Enum):
    """Section X, point 8: Pattern Day Trader modes."""
    SELECTIVE = "SELECTIVE"         # 3 trades/week, best signals only
    CONSERVATIVE = "CONSERVATIVE"  # 2/week, conf > 80
    RESERVE = "RESERVE"            # 1/week, conf > 85
    SWING = "SWING"                # Hold overnight, no PDT count


class Strategy(str, enum.Enum):
    """Part IV: 14 automated strategies."""
    S1_REGIME_TREND = "S1"
    S2_MOMENTUM_BREAKOUT = "S2"
    S3_MEAN_REVERSION = "S3"
    S4_CATALYST_NARRATIVE = "S4"
    S5_PEAD_EARNINGS = "S5"
    S6_MACRO_REGIME = "S6"
    S7_SECTOR_ROTATION = "S7"
    S8_VOL_CRUSH = "S8"
    S9_PAIRS_TRADE = "S9"
    S10_AI_THEMATIC = "S10"
    S11_HOT_SCANNER = "S11"
    S12_REBALANCE_FLOW = "S12"
    S13_TREND_COMPOUND = "S13"
    S14_GAMMA_SQUEEZE = "S14"


class LadderRung(int, enum.Enum):
    """Section 40: 7-rung profit ladder positions."""
    ENTRY = 0
    REDUCE_RISK = 1
    BREAKEVEN = 2
    FIRST_CASH = 3
    EVALUATE = 4
    SECOND_CASH = 5
    RUNNER = 6
    GIFT = 7


class RestrictionType(str, enum.Enum):
    """Overseer restriction types."""
    TICKER = "TICKER"
    SECTOR = "SECTOR"
    DIRECTION = "DIRECTION"
    HALT = "HALT"


# === Core Dataclasses ===

@dataclass
class IndicatorSnapshot:
    """Complete indicator state for a ticker at a point in time.
    Section 6: All 22 core indicators."""
    timestamp: datetime
    ticker: str

    # Price structure
    price: float = 0.0
    vwap: float = 0.0
    vwap_upper_1s: float = 0.0
    vwap_lower_1s: float = 0.0
    vwap_upper_2s: float = 0.0
    vwap_lower_2s: float = 0.0

    # EMAs
    ema9: float = 0.0
    ema20: float = 0.0
    ema50: float = 0.0
    ema10w: float = 0.0  # 10-week EMA

    # Momentum
    rsi14: float = 50.0
    macd_line: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    stochastic_rsi: float = 50.0

    # Volatility
    atr14: float = 0.0
    atr_pct: float = 0.0
    bb_upper: float = 0.0
    bb_lower: float = 0.0
    bb_middle: float = 0.0
    keltner_upper: float = 0.0
    keltner_lower: float = 0.0
    adx14: float = 0.0

    # Volume
    rvol: Optional[float] = None
    volume_spike: bool = False
    dollar_volume: float = 0.0
    obv: float = 0.0
    mfi14: float = 50.0
    cumulative_delta: float = 0.0
    speed_of_tape: float = 0.0

    # Opening Range
    or_high_5m: float = 0.0
    or_low_5m: float = 0.0
    or_high_15m: float = 0.0
    or_low_15m: float = 0.0

    # Microstructure
    bid_ask_spread: float = 0.0
    microstructure_score: float = 0.0

    # Fundamentals
    market_cap: float = 0.0

    # EMA alignment score (0-8)
    ema_alignment: int = 0

    # Detected patterns
    patterns_detected: list[str] = field(default_factory=list)

    # CVD divergence detection (research enhancement)
    cvd_bearish_div: bool = False          # Price higher high + CVD lower high
    cvd_bullish_div: bool = False          # Price lower low + CVD higher low
    absorption_detected: bool = False      # High volume + small price range

    # Initial Balance — Auction Market Theory (research enhancement)
    ib_high: float = 0.0                   # First 60-min high
    ib_low: float = 0.0                    # First 60-min low
    ib_range: float = 0.0                  # IB high - IB low
    ib_extension_pct: float = 0.0          # How far price moved beyond IB as % of IB range

    # Cross-sectional momentum (research enhancement)
    cross_momentum_rank: float = 0.0      # Rank within sector (0-1, 1 = strongest)
    capital_gains_overhang: float = 0.0    # Disposition effect proxy
    attention_score: float = 0.0           # RVOL + news-based attention
    intraday_momentum_pct: float = 0.0     # First-hour price change %
    sentiment_composite: float = 50.0      # 0-100 composite sentiment

    # Phase Q1 — Indicator Enhancements (+1.3 Sharpe)
    macd_bearish_div: bool = False         # MACD bearish divergence (fade signal)
    macd_bullish_div: bool = False         # MACD bullish divergence (entry signal)
    macd_div_strength: float = 0.0         # 0-100 divergence strength
    vol_ma50: float = 0.0                  # 50-bar volume MA (longer trend)
    vol_acceleration: bool = False         # vol_ma20 > vol_ma50 (bullish volume)
    price_action_bullish: bool = False     # close > open (recovery confirmation)
    bb_dynamic_upper: float = 0.0          # Regime-adaptive BB upper
    bb_dynamic_middle: float = 0.0         # Regime-adaptive BB middle
    bb_dynamic_lower: float = 0.0          # Regime-adaptive BB lower

    # Sprint 1 — T-05/T-06/T-07: FAST/SLOW tier indicators
    roc_30: Optional[float] = None          # T-05: 30-bar Rate of Change (%) — 30 min on 1-min bars
    adx_delta: Optional[float] = None       # T-06: ADX change per bar (trend acceleration)
    rvol_trajectory: Optional[float] = None  # T-07: RVOL acceleration (current / mean last 3)


@dataclass
class MarketContext:
    """Sections 7-9: Complete market context snapshot.
    Regime state + GEX/DIX + market internals + time window."""
    timestamp: datetime

    # Regime (Section 7)
    regime: RegimeState = RegimeState.RANGE_BOUND
    regime_confidence: float = 0.0
    regime_duration_bars: int = 0

    # QQQ/SPY context
    qqq_vs_vwap: float = 0.0  # % above/below VWAP
    spy_vs_vwap: float = 0.0
    ema_alignment: str = ""   # "bullish" / "bearish" / "flat"

    # Market Structure (Section 8)
    gex_regime: GEXRegime = GEXRegime.POSITIVE
    gex_value: float = 0.0
    dix_value: float = 0.0
    dix_signal: str = ""  # "accumulation" / "distribution" / "neutral"

    # DIX/GEX combined regime (research-backed)
    dix_gex_regime: str = "NEUTRAL"  # SETUP_BULLISH / SETUP_BEARISH / MOMENTUM_AMPLIFIED / MEAN_REVERSION / NEUTRAL
    dix_trend: str = "NEUTRAL"       # ACCUMULATING / DISTRIBUTING / NEUTRAL (3d vs 10d MA)

    # Market Internals (Section 9)
    tick: float = 0.0
    trin: float = 0.0
    add: float = 0.0
    vold: float = 0.0
    internals_composite: int = 0  # 0-4
    internals_confidence_adj: int = 0  # +5 if score 3-4, -5 if score 0-1

    # VIX
    vix: float = 0.0
    vix3m: float = 0.0
    vix_term_structure: str = ""  # "contango" / "backwardation"

    # Macro (Layer 4)
    dxy: float = 0.0
    ten_year_yield: float = 0.0
    put_call_ratio: float = 0.0
    macro_score: int = 0  # -20 to +7

    # Time (Section 10)
    time_window: TimeWindow = TimeWindow.MORNING_MOMENTUM

    # Calendar
    fomc_today: bool = False
    earnings_tonight: list[str] = field(default_factory=list)
    cpi_nfp_today: bool = False
    calendar_risk: str = "CLEAR"  # CLEAR / HIGH_RISK / BLOCK

    # Pre-market intelligence (attached by intelligence scan)
    premarket_brief: Optional[object] = None  # PreMarketBrief from intelligence engine


@dataclass
class ConstituentAlert:
    """Pre-market alert for a single constituent stock."""
    ticker: str
    name: str = ""
    overnight_change_pct: float = 0.0
    premarket_price: float = 0.0
    premarket_volume: float = 0.0
    volume_spike: bool = False
    news_headlines: list[str] = field(default_factory=list)
    sentiment: str = "neutral"
    catalyst_type: str = ""
    affected_etps: list[tuple] = field(default_factory=list)


@dataclass
class ETPBrief:
    """Pre-market brief for a single ETP/ETF."""
    etp_ticker: str
    index_name: str = ""
    leverage: int = 1
    etp_overnight_change_pct: float = 0.0
    top_movers: list[ConstituentAlert] = field(default_factory=list)
    weighted_constituent_change: float = 0.0
    expected_etp_move_pct: float = 0.0
    gap_setup: str = "flat"  # gap_and_go / gap_and_fade / flat
    news_summary: str = ""


@dataclass
class PreMarketBrief:
    """Complete pre-market intelligence brief."""
    timestamp: datetime
    scan_window: str = ""
    market_bias: str = "NEUTRAL"  # BULLISH / BEARISH / NEUTRAL / MIXED
    bias_confidence: float = 0.0
    sp500_futures_pct: float = 0.0
    nasdaq_futures_pct: float = 0.0
    vix_level: float = 0.0
    asia_summary: str = ""
    europe_summary: str = ""
    etp_briefs: list[ETPBrief] = field(default_factory=list)
    stock_alerts: list[ConstituentAlert] = field(default_factory=list)
    sector_leaders: list[str] = field(default_factory=list)
    sector_laggards: list[str] = field(default_factory=list)
    high_conviction_setups: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)

    def to_telegram(self) -> str:
        """Format the brief for Telegram delivery."""
        lines = [
            f"PRE-MARKET BRIEF -- {self.scan_window}",
            "=" * 36,
            "",
            f"Market Bias: {self.market_bias} ({self.bias_confidence:.0f}/100)",
            f"Futures: S&P {self.sp500_futures_pct:+.1f}% | Nasdaq {self.nasdaq_futures_pct:+.1f}% | VIX {self.vix_level:.1f}",
        ]
        if self.asia_summary:
            lines.append(f"Asia: {self.asia_summary}")
        if self.europe_summary:
            lines.append(f"Europe: {self.europe_summary}")

        # ETP impact
        if self.etp_briefs:
            lines.append("")
            lines.append("-- ETP IMPACT --")
            for eb in self.etp_briefs:
                lines.append(f"{eb.etp_ticker} ({eb.leverage}x {eb.index_name}): Expected {eb.expected_etp_move_pct:+.1f}%")
                if eb.top_movers:
                    movers = ", ".join(
                        f"{m.ticker} {m.overnight_change_pct:+.1f}%"
                        for m in eb.top_movers[:3]
                    )
                    lines.append(f"  Top movers: {movers}")
                if eb.gap_setup != "flat":
                    lines.append(f"  Setup: {eb.gap_setup.upper()}")

        # Bot B stocks
        if self.stock_alerts:
            lines.append("")
            lines.append("-- BOT B STOCKS --")
            for sa in self.stock_alerts:
                parts = [f"{sa.ticker}: {sa.overnight_change_pct:+.1f}%"]
                if sa.volume_spike:
                    parts.append("Vol spike")
                if sa.catalyst_type:
                    parts.append(sa.catalyst_type)
                lines.append(" | ".join(parts))

        # Sector rotation
        if self.sector_leaders:
            lines.append("")
            lines.append(f"Sector Leaders: {', '.join(self.sector_leaders)}")
            lines.append(f"Sector Laggards: {', '.join(self.sector_laggards)}")

        # Risk flags
        if self.risk_flags:
            lines.append("")
            lines.append("-- RISK FLAGS --")
            for flag in self.risk_flags:
                lines.append(f"  {flag}")

        # High conviction
        if self.high_conviction_setups:
            lines.append("")
            lines.append(f"HIGH CONVICTION: {', '.join(self.high_conviction_setups)}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize for database storage."""
        from dataclasses import asdict
        return asdict(self)


@dataclass
class SectorFlow:
    """Layer 3: Sector flow / relative strength data."""
    timestamp: datetime
    ticker: str
    sector: str = ""
    rs_vs_spy: float = 0.0          # 20-day relative strength
    sector_etf_rs: float = 0.0
    money_flow_direction: str = ""  # "inflow" / "outflow" / "neutral"
    sector_rank: int = 0            # 1-6


@dataclass
class NarrativeContext:
    """Layer 5: News/narrative context for a ticker."""
    timestamp: datetime
    ticker: str
    sentiment: str = "neutral"  # "positive" / "negative" / "neutral"
    catalyst_detected: bool = False
    catalyst_type: str = ""     # "earnings" / "upgrade" / "news" / "macro"
    headline: str = ""
    crisis_keyword: bool = False
    narrative_score: int = 0    # -50 to +8


@dataclass
class ConfidenceBreakdown:
    """Section 36: Five-layer confidence scoring breakdown."""
    layer1_price_action: float = 0.0   # Cap 45
    layer2_regime: float = 0.0         # Max 20
    layer3_sector_flow: float = 0.0    # Max 15
    layer4_macro: float = 0.0          # Max 10
    layer5_narrative: float = 0.0      # Max 10
    penalties: float = 0.0
    raw_total: float = 0.0
    final_score: float = 0.0           # After capping + floor check

    def compute(self) -> float:
        """Compute final confidence score with caps and penalties."""
        l1 = min(self.layer1_price_action, 45)
        l2 = min(self.layer2_regime, 20)
        l3 = min(self.layer3_sector_flow, 15)
        l4 = min(self.layer4_macro, 10)
        l5 = min(self.layer5_narrative, 10)
        self.raw_total = l1 + l2 + l3 + l4 + l5
        self.final_score = max(0, min(100, self.raw_total - abs(self.penalties)))
        return self.final_score


@dataclass
class Signal:
    """Core signal object produced by every strategy's scan() method.
    This is the fundamental unit that flows through the entire system."""
    id: str = ""
    timestamp: datetime = field(default_factory=_utcnow)
    ticker: str = ""
    direction: Direction = Direction.LONG
    strategy: str = ""               # S1-S14
    bot: Bot = Bot.B

    # Entry/exit levels
    entry: float = 0.0
    stop: float = 0.0
    target_1r: float = 0.0
    target_2r: float = 0.0
    trail: float = 0.0

    # Risk sizing (Section 37, stage 6)
    risk_dollars: float = 0.0
    risk_pct: float = 0.0075        # Default 0.75%
    shares: int = 0
    position_pct_equity: float = 0.0

    # Confidence (Section 36)
    confidence: float = 0.0
    confidence_breakdown: ConfidenceBreakdown = field(default_factory=ConfidenceBreakdown)

    # Context
    regime: RegimeState = RegimeState.RANGE_BOUND
    gex_regime: GEXRegime = GEXRegime.POSITIVE
    rvol: Optional[float] = None
    time_window: TimeWindow = TimeWindow.MORNING_MOMENTUM
    patterns_detected: list[str] = field(default_factory=list)
    internals_composite: int = 0

    # ISA mapping (Section 37, stage 7)
    isa_ticker: str = ""
    isa_leverage: str = ""
    isa_underlying: str = ""

    # Bot instance
    bot_instance: BotInstance = BotInstance.BULL

    # Qualification
    status: SignalStatus = SignalStatus.PENDING
    qualification_log: list[str] = field(default_factory=list)
    rejection_reason: str = ""

    # Overseer
    overseer_status: str = "CLEAR"
    portfolio_heat: float = 0.0

    # Timeframe layer
    timeframe_layer: str = ""  # "SCALP" / "SWING" / ""

    # Smart routing
    predicted_slippage: float = 0.0  # Predicted slippage from SmartRouter

    # Strategy metadata (S15 tier, indicator decomposition, etc.)
    metadata: dict = field(default_factory=dict)
    seasonality_tag: str = ""  # e.g. "POWER_HOUR", "GAP_SCAN", "NORMAL"

    @property
    def stop_pct(self) -> float:
        """Stop loss as percentage from entry."""
        if self.entry == 0:
            return 0
        return abs(self.stop - self.entry) / self.entry

    @property
    def reward_risk(self) -> float:
        """Reward-to-risk ratio (1R = stop distance)."""
        risk = abs(self.entry - self.stop)
        if risk == 0:
            return 0
        reward = abs(self.target_1r - self.entry)
        return reward / risk


@dataclass
class Position:
    """Open position tracked across all bots.
    Section 40-41: Profit ladder state machine."""
    id: str = ""
    trade_id: str = ""
    signal_id: str = ""
    bot: Bot = Bot.B
    bot_instance: BotInstance = BotInstance.BULL
    ticker: str = ""
    direction: Direction = Direction.LONG

    # Position details
    entry: float = 0.0
    shares: int = 0
    current_stop: float = 0.0
    original_stop: float = 0.0

    # Profit ladder state
    ladder_rung: LadderRung = LadderRung.ENTRY
    remaining_pct: float = 1.0       # 100% at entry

    # Live P&L
    current_price: float = 0.0
    unrealised_pnl: float = 0.0
    unrealised_r: float = 0.0

    # Timing
    entry_time: datetime = field(default_factory=_utcnow)
    last_update: datetime = field(default_factory=_utcnow)

    # Risk
    risk_dollars: float = 0.0

    @property
    def r_multiple(self) -> float:
        """Current R-multiple: how many R units of profit/loss."""
        risk = abs(self.entry - self.original_stop)
        if risk == 0:
            return 0
        if self.direction == Direction.LONG:
            return (self.current_price - self.entry) / risk
        else:
            return (self.entry - self.current_price) / risk


@dataclass
class Trade:
    """Completed trade with full journal fields.
    Section 63: Trade journal template."""
    id: str = ""
    signal_id: str = ""
    bot: Bot = Bot.B
    bot_instance: BotInstance = BotInstance.BULL

    # Setup
    ticker: str = ""
    direction: Direction = Direction.LONG
    strategy: str = ""

    # Prices
    entry_price: float = 0.0
    exit_price: float = 0.0
    stop_price: float = 0.0
    target_1r: float = 0.0
    target_2r: float = 0.0

    # Sizing
    shares: int = 0
    risk_dollars: float = 0.0
    risk_percent: float = 0.0
    position_pct_equity: float = 0.0

    # Result
    pnl_dollars: float = 0.0
    pnl_r_multiple: float = 0.0
    gross_pnl: float = 0.0
    commissions: float = 0.0
    net_pnl: float = 0.0

    # Execution quality
    expected_entry: float = 0.0
    actual_entry: float = 0.0
    fill_quality: float = 0.0          # slippage measure
    entry_quality: float = 0.0         # MAE-based 0-100
    exit_quality: float = 0.0          # MFE-based 0-100
    timing_quality: float = 0.0

    # 5-Layer Context
    confidence_score: float = 0.0
    regime_state: str = ""
    sector_rs: float = 0.0
    macro_score: float = 0.0
    narrative_sentiment: str = ""

    # Market structure
    gex_regime: str = ""
    dix_reading: float = 0.0
    internals_composite: int = 0
    vix_level: float = 0.0
    calendar_risk: str = ""

    # Patterns
    patterns_detected: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    invalidation_reason: str = ""

    # Psychology (Section 44)
    emotional_state: str = "CALM"   # CALM/ANXIOUS/EAGER/FOMO/REVENGE
    firewall_triggers: list[str] = field(default_factory=list)

    # Review
    what_worked: str = ""
    what_failed: str = ""
    improvement_note: str = ""
    would_take_again: bool = True

    # Timing
    time_entered: datetime = field(default_factory=_utcnow)
    time_exited: Optional[datetime] = None
    duration_minutes: float = 0.0


@dataclass
class DailySummary:
    """Section 63: Daily summary fields for journal and tracking."""
    date: str = ""
    bot: str = ""
    trades_taken: int = 0
    signals_received: int = 0
    signals_skipped: int = 0
    daily_pnl_dollars: float = 0.0
    daily_pnl_percent: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    avg_r_multiple: float = 0.0
    largest_winner_r: float = 0.0
    largest_loser_r: float = 0.0
    regime_today: str = ""
    emotional_grade: str = "A"  # A (perfect) to F
    one_sentence_lesson: str = ""
    tomorrow_plan: str = ""


@dataclass
class Restriction:
    """Overseer-generated restriction entry (Section 67)."""
    id: str = ""
    bot_instance: str = ""
    restriction_type: RestrictionType = RestrictionType.TICKER
    value: str = ""           # ticker, sector, or direction being restricted
    reason: str = ""
    created_at: datetime = field(default_factory=_utcnow)
    expires_at: Optional[datetime] = None


@dataclass
class TickerProfile:
    """Section 49-52: Per-ticker performance profile (learning engine)."""
    ticker: str = ""
    rolling_60d_wr: float = 0.0
    best_strategy: str = ""
    best_direction: Direction = Direction.LONG
    false_breakout_rate: float = 0.0
    optimal_rvol: Optional[float] = None
    optimal_stop_mult: float = 0.0
    priority_score: float = 0.0


@dataclass
class BotAllocation:
    """Multi-bot capital allocation tracking (Section 64)."""
    date: str = ""
    bot_instance: str = ""
    target_pct: float = 0.0
    actual_pct: float = 0.0
    capital_allocated: float = 0.0


@dataclass
class RegimeMemoryCell:
    """Section 49-52: One cell in the regime memory matrix.
    RegimePerformanceMatrix[regime][strategy][direction]."""
    regime: str = ""
    strategy: str = ""
    direction: str = ""
    trades: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    expectancy: float = 0.0
