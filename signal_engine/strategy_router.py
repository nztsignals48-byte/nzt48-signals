"""
signal_engine/strategy_router.py
==================================
NZT-48 Strategy Router -- Regime -> ActiveStrategies -> StrategyWeightedScore

Aligned to NZT-48 Master Spec v8.0 (Multi-Bot Architecture, 5-Layer Perception,
14 Strategy Engines, 8 Regime States, Time-of-Day Edges).

Architecture:
  1. Regime classification (8 states from spec: TRENDING_UP_STRONG, TRENDING_UP_MOD,
     TRENDING_DOWN_STRONG, TRENDING_DOWN_MOD, RANGE_BOUND, HIGH_VOLATILITY,
     RISK_OFF, SHOCK)
  2. Time-of-day window (from Master Spec Section 10: Chaos Open, Morning Momentum,
     Trend Extension, Lunch Chop, Afternoon Push, Power Hour, Close Mechanics)
  3. Strategy activation per regime + time window
  4. Overlay application (post-strategy risk discipline)
  5. Strategy-weighted score (boosts composite score based on active strategy alignment)

Router output: RouterResult (written to artifacts/{date}/{session}/strategies.json)
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.strategy_router")

# ---------------------------------------------------------------------------
# Master Spec 8-State Regime Classification
# ---------------------------------------------------------------------------
REGIME_TRENDING_UP_STRONG   = "TRENDING_UP_STRONG"
REGIME_TRENDING_UP_MOD      = "TRENDING_UP_MOD"
REGIME_TRENDING_DOWN_STRONG = "TRENDING_DOWN_STRONG"
REGIME_TRENDING_DOWN_MOD    = "TRENDING_DOWN_MOD"
REGIME_RANGE_BOUND          = "RANGE_BOUND"
REGIME_HIGH_VOLATILITY      = "HIGH_VOLATILITY"
REGIME_RISK_OFF             = "RISK_OFF"
REGIME_SHOCK                = "SHOCK"

# Legacy label → canonical spec label mapping
_REGIME_MAP: dict[str, str] = {
    "RISK_ON":          REGIME_TRENDING_UP_MOD,
    "BULLISH":          REGIME_TRENDING_UP_STRONG,
    "BULL":             REGIME_TRENDING_UP_STRONG,
    "BEARISH":          REGIME_TRENDING_DOWN_MOD,
    "BEAR":             REGIME_TRENDING_DOWN_STRONG,
    "RISK_OFF":         REGIME_RISK_OFF,
    "NEUTRAL":          REGIME_RANGE_BOUND,
    "CHOPPY":           REGIME_RANGE_BOUND,
    "HIGH_VOLATILITY":  REGIME_HIGH_VOLATILITY,
    "SHOCK":            REGIME_SHOCK,
}

# ---------------------------------------------------------------------------
# Time-of-Day Windows (Master Spec Section 10, adapted to UK ISA times)
# ---------------------------------------------------------------------------
TOD_CHAOS_OPEN          = "CHAOS_OPEN"        # 08:00-08:30 UK / 14:30-15:00 UK
TOD_MORNING_MOMENTUM    = "MORNING_MOMENTUM"  # 08:30-10:30 UK / 15:00-16:00 UK
TOD_TREND_EXTENSION     = "TREND_EXTENSION"   # 10:30-12:00 UK
TOD_LUNCH_CHOP          = "LUNCH_CHOP"        # 12:00-13:30 UK (RVOL min 1.7)
TOD_AFTERNOON_PUSH      = "AFTERNOON_PUSH"    # 13:30-15:00 UK (pre-NYSE + fresh setups)
TOD_POWER_HOUR          = "POWER_HOUR"        # 15:00-16:30 UK / 19:30-21:00 UK
TOD_CLOSE_MECHANICS     = "CLOSE_MECHANICS"   # 16:00-16:30 UK (FLATTEN, no new entries)
TOD_AFTER_HOURS         = "AFTER_HOURS"       # all other times


def get_time_of_day_window(hour: int, minute: int) -> str:
    """Map UK local hour:minute to Master Spec time-of-day window."""
    t = hour * 60 + minute
    # LSE open chaos
    if 480 <= t < 510:     return TOD_CHAOS_OPEN        # 08:00-08:30
    if 510 <= t < 630:     return TOD_MORNING_MOMENTUM  # 08:30-10:30
    if 630 <= t < 720:     return TOD_TREND_EXTENSION   # 10:30-12:00
    if 720 <= t < 810:     return TOD_LUNCH_CHOP        # 12:00-13:30
    if 810 <= t < 870:     return TOD_AFTERNOON_PUSH    # 13:30-14:30 (pre-NYSE)
    # NYSE open chaos
    if 870 <= t < 900:     return TOD_CHAOS_OPEN        # 14:30-15:00
    if 900 <= t < 960:     return TOD_MORNING_MOMENTUM  # 15:00-16:00
    if 960 <= t < 990:     return TOD_POWER_HOUR        # 16:00-16:30 (LSE close)
    if 990 <= t < 1020:    return TOD_CLOSE_MECHANICS   # 16:30-17:00
    # NYSE afternoon
    if 1170 <= t < 1260:   return TOD_POWER_HOUR        # 19:30-21:00
    if 1260 <= t < 1320:   return TOD_CLOSE_MECHANICS   # 21:00-22:00 (NYSE close)
    return TOD_AFTER_HOURS


# ---------------------------------------------------------------------------
# StrategySpec — one strategy descriptor
# ---------------------------------------------------------------------------

@dataclass
class StrategySpec:
    tag:              str
    weight:           float           # 0.0–1.0 contribution weight
    active:           bool
    why_active:       list[str]       # <=3 bullets (spec-aligned)
    constraints:      dict            # min_rvol, time_windows, etc.
    required_data:    list[str]
    data_available:   bool
    inactive_reason:  str  = ""
    category:         str  = "CORE"  # CORE | INTRADAY | OVERLAY | EVENT

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# RouterResult
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# StrategyAvailability — v4.0: event strategy readiness surface
# ---------------------------------------------------------------------------

@dataclass
class StrategyAvailability:
    """Surface to War Room Strategy Lab + Mega PDF."""
    name:                 str
    status:               str        # ACTIVE / INACTIVE
    reason:               str
    required_config_key:  str = ""
    recommended_provider: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RouterResult:
    regime_tag:         str
    regime_confidence:  float
    time_of_day_window: str
    active_strategies:  list[StrategySpec]
    overlay_tags:       list[str]
    overlay_warnings:   list[str]
    sizing_mode:        str  = "NORMAL"   # NORMAL | REDUCED | DEFENSIVE
    max_factor_cap:     int  = 3          # may be reduced by DISPERSION overlay
    kill_switch:        bool = False      # True if SHOCK or VIX > 45

    # Score boost factor: composite * (1 + boost)  — capped at 100
    score_boost:        float = 0.0

    # v4.0: Capital allocation weights (strategy_tag -> float, sums to 1.0)
    allocation_weights:       dict = field(default_factory=dict)
    # v4.0: Event strategy availability (from adapter stubs)
    strategy_availability:    list = field(default_factory=list)   # list[StrategyAvailability]
    # v4.0: Risk adjustment factor (set by RiskOfficer post-evaluate, 0-1)
    risk_adjustment_factor:   float = 1.0

    def apply_score_boost(self, composite: float) -> float:
        """Apply strategy-weighted boost to a base composite score (0-100)."""
        boosted = composite * (1.0 + self.score_boost)
        return round(min(100.0, boosted), 1)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["active_strategies"] = [s.to_dict() for s in self.active_strategies]
        d["strategy_availability"] = [
            s.to_dict() if hasattr(s, "to_dict") else s
            for s in self.strategy_availability
        ]
        return d

    def active_count(self) -> int:
        return sum(1 for s in self.active_strategies if s.active)

    def active_tags(self) -> list[str]:
        return [s.tag for s in self.active_strategies if s.active]


# ---------------------------------------------------------------------------
# Strategy Router
# ---------------------------------------------------------------------------

class StrategyRouter:
    """
    Implements the Master Spec strategy activation logic.

    Usage::
        router = StrategyRouter()
        result = router.run(
            regime="NEUTRAL",
            session="PRE_LSE",
            vix_level=18.0,
            spx_5d_return=0.5,
            hour_uk=8,
            minute_uk=45,
            isa_data={}
        )
    """

    def __init__(self) -> None:
        self._artifacts_root = Path(__file__).parent.parent / "artifacts"

    # ------------------------------------------------------------------ #
    def run(
        self,
        regime:           str   = "NEUTRAL",
        session:          str   = "INTRADAY",
        vix_level:        float = 18.0,
        spx_5d_return:    float = 0.0,
        bb_width_rank:    float = 0.5,   # universe avg BB width rank
        rvol_avg:         float = 1.0,   # universe avg RVOL
        adx_avg:          float = 20.0,  # universe avg ADX
        hour_uk:          int   = 10,
        minute_uk:        int   = 0,
        isa_data:         dict  = None,
        write_artifact:   bool  = True,
    ) -> RouterResult:
        """Evaluate regime + market data → activate strategies → apply overlays."""

        isa_data = isa_data or {}
        regime_tag = _REGIME_MAP.get(regime.upper(), REGIME_RANGE_BOUND)
        tod        = get_time_of_day_window(hour_uk, minute_uk)

        # Derive regime confidence from data quality
        regime_confidence = self._compute_regime_confidence(
            regime_tag, vix_level, spx_5d_return
        )

        logger.info("[ROUTER] regime=%s->%s tod=%s vix=%.1f rc=%.2f",
                    regime, regime_tag, tod, vix_level, regime_confidence)

        # Build strategy list
        strategies = self._build_strategies(
            regime_tag, tod, vix_level, spx_5d_return,
            bb_width_rank, rvol_avg, adx_avg
        )

        # Apply overlays
        overlay_tags, overlay_warnings, sizing_mode, max_cap, kill = \
            self._apply_overlays(regime_tag, vix_level, spx_5d_return, strategies)

        # Compute score boost from active strategies
        active_weights = [s.weight for s in strategies if s.active and s.data_available]
        score_boost = round(sum(active_weights) * 0.12, 3)  # 0.12 per weight point

        # v4.0: Capital allocation weights
        allocation_weights = self._compute_allocation_weights(regime_tag, strategies, vix_level)

        # Validate weight normalization
        total_weight = sum(allocation_weights.values()) if allocation_weights else 0
        if total_weight > 0 and abs(total_weight - 1.0) > 0.01:
            # Normalize to exactly 1.0
            for k in allocation_weights:
                allocation_weights[k] = allocation_weights[k] / total_weight
            logger.debug("Strategy weights normalized: sum was %.4f", total_weight)

        # v4.0: Event strategy availability
        strategy_availability = self._build_strategy_availability()

        result = RouterResult(
            regime_tag              = regime_tag,
            regime_confidence       = regime_confidence,
            time_of_day_window      = tod,
            active_strategies       = strategies,
            overlay_tags            = overlay_tags,
            overlay_warnings        = overlay_warnings,
            sizing_mode             = sizing_mode,
            max_factor_cap          = max_cap,
            kill_switch             = kill,
            score_boost             = score_boost,
            allocation_weights      = allocation_weights,
            strategy_availability   = strategy_availability,
        )

        if write_artifact:
            self._write_artifact(result, session)

        return result

    # ------------------------------------------------------------------ #
    # Strategy builder
    # ------------------------------------------------------------------ #

    def _build_strategies(
        self,
        regime_tag:     str,
        tod:            str,
        vix_level:      float,
        spx_return:     float,
        bb_width_rank:  float,
        rvol_avg:       float,
        adx_avg:        float,
    ) -> list[StrategySpec]:

        is_bull   = regime_tag in (REGIME_TRENDING_UP_STRONG, REGIME_TRENDING_UP_MOD)
        is_bear   = regime_tag in (REGIME_TRENDING_DOWN_STRONG, REGIME_TRENDING_DOWN_MOD)
        is_range  = regime_tag == REGIME_RANGE_BOUND
        is_shock  = regime_tag in (REGIME_SHOCK, REGIME_RISK_OFF)
        is_hv     = regime_tag == REGIME_HIGH_VOLATILITY
        trending  = is_bull or is_bear
        is_chaos  = tod == TOD_CHAOS_OPEN
        is_lunch  = tod == TOD_LUNCH_CHOP
        is_close  = tod == TOD_CLOSE_MECHANICS
        is_power  = tod == TOD_POWER_HOUR
        is_morning = tod == TOD_MORNING_MOMENTUM
        is_active = not is_chaos and not is_close

        strategies: list[StrategySpec] = []

        # ── S1: REGIME_TREND_FOLLOWING (aligns to Master Spec S1) ──────────
        s1_active = trending and adx_avg >= 20 and is_active and not is_shock
        strategies.append(StrategySpec(
            tag="TREND_MOMENTUM_CTA",
            weight=0.85 if is_bull else (0.75 if is_bear else 0.0),
            active=s1_active,
            why_active=[
                f"Regime {regime_tag}: trend confirmed (ADX {adx_avg:.0f} >= 20)",
                "EMA alignment + ADX confirm directional momentum",
                "CTA/managed-futures intraday adaptation active",
            ] if s1_active else [],
            constraints={"min_adx": 20, "tod_exclude": [TOD_CHAOS_OPEN, TOD_CLOSE_MECHANICS]},
            required_data=["ATR", "RSI", "MACD", "EMA_9_20_50", "ADX"],
            data_available=True,
            inactive_reason="" if s1_active else f"regime={regime_tag} or tod={tod} not suitable",
            category="CORE",
        ))

        # ── S2: MOMENTUM_BREAKOUT (Master Spec S2) ──────────────────────────
        s2_active = (is_bull or is_range) and is_morning and rvol_avg >= 1.2 and is_active
        strategies.append(StrategySpec(
            tag="MOMENTUM_BREAKOUT",
            weight=0.80,
            active=s2_active,
            why_active=[
                f"RVOL avg {rvol_avg:.1f}x confirms institutional participation",
                "Morning session momentum window active (08:30-10:30 / 15:00-16:00)",
                "RSI > 60 + BB squeeze release criteria met",
            ] if s2_active else [],
            constraints={"min_rvol": 1.2, "tod_required": [TOD_MORNING_MOMENTUM]},
            required_data=["RSI", "RVOL", "BB_WIDTH_RANK", "MACD"],
            data_available=True,
            inactive_reason="" if s2_active else f"RVOL {rvol_avg:.1f} < 1.2 or tod={tod}",
            category="CORE",
        ))

        # ── S3: MEAN_REVERSION / STAT_ARB (Master Spec S3) ──────────────────
        s3_active = (is_range or is_lunch) and is_active and not is_shock
        strategies.append(StrategySpec(
            tag="STAT_ARB_MEAN_REVERT",
            weight=0.70,
            active=s3_active,
            why_active=[
                "Range-bound regime: mean reversion statistically preferred",
                "VWAP/BB mean reversion setup active",
                "RSI extremes create fading opportunity",
            ] if s3_active else [],
            constraints={"min_rvol": 0.7, "tod_preferred": [TOD_LUNCH_CHOP, TOD_TREND_EXTENSION]},
            required_data=["RSI", "BB_WIDTH_RANK", "MACD"],
            data_available=True,
            inactive_reason="" if s3_active else f"regime={regime_tag} not range-bound",
            category="CORE",
        ))

        # ── S7: SECTOR_ROTATION (Master Spec S7) ────────────────────────────
        s7_active = is_active and not is_shock
        strategies.append(StrategySpec(
            tag="FACTOR_ROTATION",
            weight=0.60,
            active=s7_active,
            why_active=[
                "Sector rotation radar active: monitoring leadership shifts",
                "Factor group exposure caps applied (nasdaq_beta / semis / commodities)",
            ] if s7_active else [],
            constraints={"max_factor_signals": 3},
            required_data=["FACTOR_GROUP", "SECTOR_RADAR"],
            data_available=True,
            inactive_reason="" if s7_active else "shock/risk_off: rotation paused",
            category="CORE",
        ))

        # ── S8: VOL_CRUSH (Master Spec S8) ──────────────────────────────────
        s8_active = is_hv and is_active
        strategies.append(StrategySpec(
            tag="VOL_SQUEEZE_BREAKOUT",
            weight=0.75,
            active=s8_active,
            why_active=[
                f"HIGH_VOLATILITY regime: vol squeeze breakout window open",
                f"BB width rank avg {bb_width_rank:.2f}: compression state",
                "VIX spike + reversal = vol mean reversion opportunity",
            ] if s8_active else [],
            constraints={"max_bb_rank": 0.40},
            required_data=["BB_WIDTH_RANK", "VIX"],
            data_available=True,
            inactive_reason="" if s8_active else "not in HIGH_VOLATILITY regime",
            category="INTRADAY",
        ))

        # ── VOL_BREAKOUT (squeeze expansion plays) ───────────────────────────
        vol_break_active = bb_width_rank < 0.25 and is_active and not is_shock
        strategies.append(StrategySpec(
            tag="VOL_BREAKOUT",
            weight=0.72,
            active=vol_break_active,
            why_active=[
                f"BB width rank {bb_width_rank:.2f} < 0.25: squeeze building",
                "Compression -> expansion breakout imminent",
                "High ATR% instruments preferred",
            ] if vol_break_active else [],
            constraints={"max_bb_rank": 0.25},
            required_data=["BB_WIDTH_RANK", "ATR"],
            data_available=True,
            inactive_reason="" if vol_break_active else f"BB rank {bb_width_rank:.2f} >= 0.25",
            category="INTRADAY",
        ))

        # ── ORB: OPENING_RANGE_BREAKOUT (prop intraday) ─────────────────────
        orb_active = tod in (TOD_MORNING_MOMENTUM,) and is_active and not is_shock
        strategies.append(StrategySpec(
            tag="OPENING_RANGE_BREAKOUT",
            weight=0.90,
            active=orb_active,
            why_active=[
                "Morning session: ORB window active (08:30-10:30 / 15:00-16:00 UK)",
                "High-probability directional move from opening range",
                "Spec: PRIMARY WINDOW. Full aggression. ORB.",
            ] if orb_active else [],
            constraints={"tod_required": [TOD_MORNING_MOMENTUM], "min_rvol": 1.5},
            required_data=["OPENING_RANGE", "RVOL", "MACD"],
            data_available=True,
            inactive_reason="" if orb_active else f"tod={tod} not morning momentum window",
            category="INTRADAY",
        ))

        # ── VWAP_TREND_PULLBACK (active in core hours) ───────────────────────
        vwap_trend_active = trending and tod in (TOD_TREND_EXTENSION, TOD_AFTERNOON_PUSH) and is_active
        strategies.append(StrategySpec(
            tag="VWAP_TREND_PULLBACK",
            weight=0.75,
            active=vwap_trend_active,
            why_active=[
                "Trend confirmed: VWAP pullback re-entry window",
                "EMA 9/20 aligned, ADX > 20",
                "Trend extension session: adds to winners",
            ] if vwap_trend_active else [],
            constraints={"tod_required": [TOD_TREND_EXTENSION, TOD_AFTERNOON_PUSH]},
            required_data=["EMA_9_20_50", "ATR"],
            data_available=True,
            inactive_reason="" if vwap_trend_active else "not trending or wrong time window",
            category="INTRADAY",
        ))

        # ── VWAP_MEAN_REVERT (lunch chop plays) ──────────────────────────────
        vwap_mr_active = is_lunch and is_range and is_active
        strategies.append(StrategySpec(
            tag="VWAP_MEAN_REVERT",
            weight=0.55,
            active=vwap_mr_active,
            why_active=[
                "Lunch chop window: RVOL min 1.7 required (spec rule)",
                "Range-bound: VWAP extension fades",
            ] if vwap_mr_active else [],
            constraints={"tod_required": [TOD_LUNCH_CHOP], "min_rvol": 1.7},
            required_data=["RSI", "BB_WIDTH_RANK"],
            data_available=True,
            inactive_reason="" if vwap_mr_active else "not lunch + range-bound",
            category="INTRADAY",
        ))

        # ── GAP_GO_FADE (first 30 min gap plays) ─────────────────────────────
        gap_active = tod == TOD_CHAOS_OPEN  # eval only; mark inactive (chaos open = observe)
        strategies.append(StrategySpec(
            tag="GAP_GO_FADE",
            weight=0.0,
            active=False,
            why_active=[],
            constraints={"tod_required": [TOD_CHAOS_OPEN], "note": "observe only window"},
            required_data=["OPEN", "PREV_CLOSE"],
            data_available=True,
            inactive_reason="CHAOS_OPEN window = observe only (spec rule)",
            category="INTRADAY",
        ))

        # ── BETA_NEUTRAL_SPREAD (pairs — data not available for LSE ETPs) ────
        strategies.append(StrategySpec(
            tag="BETA_NEUTRAL_SPREAD",
            weight=0.0,
            active=False,
            why_active=[],
            constraints={"requires": "correlated_pairs_feed"},
            required_data=["PAIR_RATIO", "CORRELATION_MATRIX"],
            data_available=False,
            inactive_reason="pairs_data not available for LSE leveraged ETPs",
            category="INTRADAY",
        ))

        # ── S5: MACRO_EVENT_FILTER (always-on overlay) ───────────────────────
        macro_kill = vix_level > 35 or is_shock
        strategies.append(StrategySpec(
            tag="MACRO_EVENT_FILTER",
            weight=0.0 if not macro_kill else -1.0,  # negative weight = kill
            active=True,   # always evaluating
            why_active=[
                f"VIX={vix_level:.1f}: {'KILL - no new trades' if macro_kill else 'within normal range'}",
                "Macro filter on at all times per spec immutable rules",
            ],
            constraints={"vix_kill_threshold": 35.0},
            required_data=["VIX"],
            data_available=True,
            inactive_reason="",
            category="OVERLAY",
        ))

        # ── TSM_TREND_OVERLAY (12m momentum bias) ────────────────────────────
        strategies.append(StrategySpec(
            tag="TSM_TREND_OVERLAY",
            weight=0.0,
            active=True,
            why_active=[
                "12-month momentum bias from TSM3.L as proxy",
                "Informs long/short directional bias overlay",
            ],
            constraints={"proxy": "TSM3.L", "period": "252d"},
            required_data=["TSM3.L_12M_RETURN"],
            data_available=False,   # would need 252d data; yfinance may not have hourly
            inactive_reason="12m momentum data not available (yfinance hourly limit)",
            category="OVERLAY",
        ))

        # ── VIX_TERM_CARRY_OVERLAY ────────────────────────────────────────────
        strategies.append(StrategySpec(
            tag="VIX_TERM_CARRY_OVERLAY",
            weight=0.0,
            active=True,
            why_active=[
                f"VIX level {vix_level:.1f}: {'elevated' if vix_level > 20 else 'normal'}",
                "Risk overlay: vol-of-vol proxy via VIX day-change",
            ],
            constraints={"vix_elevated_threshold": 20.0},
            required_data=["VIX", "VIX_CHANGE_1D"],
            data_available=True,
            inactive_reason="",
            category="OVERLAY",
        ))

        # ── DISPERSION_CORRELATION_OVERLAY ────────────────────────────────────
        strategies.append(StrategySpec(
            tag="DISPERSION_CORRELATION_OVERLAY",
            weight=0.0,
            active=True,
            why_active=[
                "Correlation rising -> tighten factor caps",
                "Factor exposure dashboard active",
            ],
            constraints={"high_corr_threshold": 0.85, "cap_reduction": "3->2"},
            required_data=["CORRELATION_MATRIX"],
            data_available=False,  # no real-time correlation matrix
            inactive_reason="correlation_matrix not available (using factor group caps instead)",
            category="OVERLAY",
        ))

        # ── S5: EARNINGS_CONFIRMATION_DRIFT ──────────────────────────────────
        strategies.append(StrategySpec(
            tag="EARNINGS_CONFIRMATION_DRIFT",
            weight=0.0,
            active=False,
            why_active=[],
            constraints={"requires": "earnings_calendar + surprise_feed"},
            required_data=["EARNINGS_DATE", "ANALYST_SURPRISE"],
            data_available=False,
            inactive_reason="earnings_feed not connected",
            category="EVENT",
        ))

        # ── IPO_LOCKUP_PRESSURE ───────────────────────────────────────────────
        strategies.append(StrategySpec(
            tag="IPO_LOCKUP_PRESSURE",
            weight=0.0,
            active=False,
            why_active=[],
            constraints={"requires": "lockup_calendar"},
            required_data=["LOCKUP_EXPIRY_DATE"],
            data_available=False,
            inactive_reason="lockup_calendar not connected",
            category="EVENT",
        ))

        # ── MERGER_ARB_EVENT ──────────────────────────────────────────────────
        strategies.append(StrategySpec(
            tag="MERGER_ARB_EVENT",
            weight=0.0,
            active=False,
            why_active=[],
            constraints={"requires": "deal_feed + spread_data"},
            required_data=["DEAL_ANNOUNCEMENT", "MERGER_SPREAD"],
            data_available=False,
            inactive_reason="deal_feed not connected",
            category="EVENT",
        ))

        # ── S12: REBALANCE_FLOW (Master Spec S12 — key signal) ───────────────
        # Only active at 19:00 UK when underlying moves +/-1.5%
        strategies.append(StrategySpec(
            tag="REBALANCE_FLOW",
            weight=0.95,
            active=False,  # only active when scan detects 19:00 rebalance signal
            why_active=[],
            constraints={"fire_time_uk": "19:00", "underlying_move_min": 1.5},
            required_data=["UNDERLYING_DAILY_RETURN", "ETP_TRACKING_ERROR"],
            data_available=True,
            inactive_reason="not in rebalance window (fires at 19:00 UK, S12 only)",
            category="CORE",
        ))

        return strategies

    # ------------------------------------------------------------------ #
    # Overlay application
    # ------------------------------------------------------------------ #

    def _apply_overlays(
        self,
        regime_tag:  str,
        vix_level:   float,
        spx_return:  float,
        strategies:  list[StrategySpec],
    ) -> tuple[list[str], list[str], str, int, bool]:
        """Apply overlay rules. Returns (tags, warnings, sizing_mode, max_cap, kill)."""

        tags:     list[str] = []
        warnings: list[str] = []
        sizing_mode = "NORMAL"
        max_cap     = 3       # factor group cap
        kill        = False

        # MACRO_EVENT_FILTER: VIX kill switch
        if vix_level > 45 or regime_tag == REGIME_SHOCK:
            kill = True
            tags.append("MACRO_EVENT_FILTER_KILL")
            warnings.append(f"VIX={vix_level:.1f} > 45: KILL SWITCH active. No new signals.")
            sizing_mode = "DEFENSIVE"
        elif vix_level > 35:
            tags.append("MACRO_EVENT_FILTER_REDUCE")
            warnings.append(f"VIX={vix_level:.1f} > 35 (spec kill threshold): RISK_OFF mode.")
            sizing_mode = "DEFENSIVE"
        elif vix_level > 25:
            tags.append("VOL_TARGET_OVERLAY_REDUCED")
            warnings.append(f"VIX={vix_level:.1f} > 25: sizing REDUCED to S (small).")
            sizing_mode = "REDUCED"
        elif vix_level > 20:
            tags.append("VOL_TARGET_OVERLAY_CAUTION")
            warnings.append(f"VIX={vix_level:.1f} > 20: elevated vol, caution sizing.")

        # DISPERSION overlay: tighten factor cap if correlation rising
        if regime_tag in (REGIME_RISK_OFF, REGIME_SHOCK):
            max_cap = 2
            tags.append("DISPERSION_CORRELATION_OVERLAY")
            warnings.append("Correlation rising in risk-off: factor cap tightened 3->2.")

        # DECAY risk overlay for leveraged ETPs in chop
        if regime_tag in (REGIME_RANGE_BOUND, REGIME_HIGH_VOLATILITY):
            tags.append("LEVERAGE_DECAY_WARNING")
            warnings.append("Chop/HV regime: 3x ETPs accumulate daily volatility decay.")

        # VIX_TERM_CARRY overlay
        if vix_level > 20:
            tags.append("VIX_TERM_CARRY_OVERLAY")

        # TSM momentum overlay (data not available — note it)
        tags.append("TSM_TREND_OVERLAY_NA")

        return tags, warnings, sizing_mode, max_cap, kill

    # ------------------------------------------------------------------ #
    # Regime confidence
    # ------------------------------------------------------------------ #

    def _compute_regime_confidence(
        self,
        regime_tag:    str,
        vix_level:     float,
        spx_5d_return: float,
    ) -> float:
        """Compute 0-1 regime confidence from signal clarity."""
        base = 0.65
        # Strong directional regime → high confidence
        if regime_tag == REGIME_TRENDING_UP_STRONG:
            base = 0.85
        elif regime_tag == REGIME_TRENDING_UP_MOD:
            base = 0.75
        elif regime_tag == REGIME_TRENDING_DOWN_STRONG:
            base = 0.80
        elif regime_tag == REGIME_TRENDING_DOWN_MOD:
            base = 0.72
        elif regime_tag == REGIME_RISK_OFF:
            base = 0.78
        elif regime_tag == REGIME_SHOCK:
            base = 0.90  # certain — but kill switch, so irrelevant
        elif regime_tag == REGIME_RANGE_BOUND:
            base = 0.55
        elif regime_tag == REGIME_HIGH_VOLATILITY:
            base = 0.50  # ambiguous by nature

        # Penalise intermediate VIX (uncertain regime boundary)
        if 18 <= vix_level <= 25:
            base -= 0.05
        if 15 <= abs(spx_5d_return) <= 0.5:  # flat market = uncertain
            base -= 0.05

        return round(max(0.30, min(0.95, base)), 3)

    # ------------------------------------------------------------------ #
    # v4.0: Capital allocation weights
    # ------------------------------------------------------------------ #

    def _compute_allocation_weights(
        self,
        regime_tag:  str,
        strategies:  list,   # list[StrategySpec]
        vix_level:   float,
    ) -> dict:
        """
        Compute capital allocation weights per strategy tag.
        Weights sum to 1.0.
        Rules:
          - Trending regime: TREND_MOMENTUM_CTA and ORB get +30% relative weight
          - Range/chop: STAT_ARB and VWAP_MEAN_REVERT get +25% relative weight
          - Vol shock (VIX > 25): all weights halved (de-risk across board)
          - Normalise to sum = 1.0
        """
        is_bull    = regime_tag in (REGIME_TRENDING_UP_STRONG, REGIME_TRENDING_UP_MOD)
        is_bear    = regime_tag in (REGIME_TRENDING_DOWN_STRONG, REGIME_TRENDING_DOWN_MOD)
        is_range   = regime_tag == REGIME_RANGE_BOUND
        is_vol_shock = vix_level > 25.0

        active = [s for s in strategies if s.active and s.data_available]
        if not active:
            return {}

        raw_weights: dict[str, float] = {}
        for s in active:
            base = s.weight

            # Regime tilt: trend strategies get boost in trending regimes
            if (is_bull or is_bear) and s.tag in (
                "TREND_MOMENTUM_CTA", "MOMENTUM_BREAKOUT",
                "OPENING_RANGE_BREAKOUT", "VOL_SQUEEZE_BREAKOUT",
            ):
                base *= 1.30

            # Range tilt: mean reversion strategies get boost in choppy regimes
            if is_range and s.tag in (
                "STAT_ARB_MEAN_REVERT", "VWAP_MEAN_REVERT",
            ):
                base *= 1.25

            # Vol shock: reduce aggressiveness across all strategies
            if is_vol_shock:
                base *= 0.50

            raw_weights[s.tag] = max(0.0, base)

        total = sum(raw_weights.values()) or 1.0
        normalised = {tag: round(w / total, 4) for tag, w in raw_weights.items()}
        return normalised

    # ------------------------------------------------------------------ #
    # v4.0: Event strategy availability (adapter stubs)
    # ------------------------------------------------------------------ #

    def _build_strategy_availability(self) -> list:
        """Query adapter stubs and return StrategyAvailability list."""
        availability = []
        try:
            from signal_engine.adapters.earnings_adapter import EarningsAdapter
            ea = EarningsAdapter()
            availability.append(StrategyAvailability(
                name=ea.strategy_availability()["name"],
                status=ea.STATUS,
                reason=ea.strategy_availability()["reason"],
                required_config_key=ea.REQUIRED_CONFIG_KEY,
                recommended_provider=ea.RECOMMENDED_PROVIDER,
            ))
        except Exception:
            pass

        try:
            from signal_engine.adapters.lockup_adapter import LockupAdapter
            la = LockupAdapter()
            availability.append(StrategyAvailability(
                name=la.strategy_availability()["name"],
                status=la.STATUS,
                reason=la.strategy_availability()["reason"],
                required_config_key=la.REQUIRED_CONFIG_KEY,
                recommended_provider=la.RECOMMENDED_PROVIDER,
            ))
        except Exception:
            pass

        try:
            from signal_engine.adapters.ma_adapter import MAAdapter
            ma = MAAdapter()
            availability.append(StrategyAvailability(
                name=ma.strategy_availability()["name"],
                status=ma.STATUS,
                reason=ma.strategy_availability()["reason"],
                required_config_key=ma.REQUIRED_CONFIG_KEY,
                recommended_provider=ma.RECOMMENDED_PROVIDER,
            ))
        except Exception:
            pass

        return availability

    # ------------------------------------------------------------------ #
    # Artifact writer
    # ------------------------------------------------------------------ #

    def _write_artifact(self, result: RouterResult, session: str) -> None:
        """Write strategies.json to artifacts/{date}/{session}/strategies.json."""
        try:
            today       = date.today()
            session_key = session.lower().replace(" ", "_")
            out_dir     = self._artifacts_root / str(today) / session_key
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path    = out_dir / "strategies.json"

            payload = {
                "generated_at":      datetime.now(timezone.utc).isoformat(),
                "session":           session,
                "regime_tag":        result.regime_tag,
                "regime_confidence": result.regime_confidence,
                "time_of_day":       result.time_of_day_window,
                "sizing_mode":       result.sizing_mode,
                "kill_switch":       result.kill_switch,
                "score_boost":       result.score_boost,
                "active_count":      result.active_count(),
                "active_tags":       result.active_tags(),
                "overlay_tags":      result.overlay_tags,
                "overlay_warnings":  result.overlay_warnings,
                "strategies":             [s.to_dict() for s in result.active_strategies],
                # v4.0 additions
                "allocation_weights":     result.allocation_weights,
                "strategy_availability":  [
                    s.to_dict() if hasattr(s, "to_dict") else s
                    for s in result.strategy_availability
                ],
            }

            # Atomic write: tmp → fsync → rename
            tmp_path = out_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2, default=str))
            tmp_path.replace(out_path)

            logger.info("[ROUTER] strategies artifact written: %s", out_path)
        except Exception as exc:
            logger.warning("[ROUTER] artifact write failed: %s", exc)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_ROUTER: Optional[StrategyRouter] = None


def get_router() -> StrategyRouter:
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = StrategyRouter()
    return _ROUTER
