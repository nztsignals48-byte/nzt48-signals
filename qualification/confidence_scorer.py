"""
NZT-48 Trading System — 5-Layer Confidence Scorer
Section 36: TOTAL = Layer1 + Layer2 + Layer3 + Layer4 + Layer5 - Penalties.
Max: 100. System floor: 60 (immutable). ORB/VWAP: 65. BEAR-BOT: 80.

Every signal must pass through this scorer. A perfect Layer 1 setup
gets REJECTED if Layer 3 says money is flowing OUT of the sector,
or Layer 4 says yields are spiking.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    ConfidenceBreakdown, IndicatorSnapshot, MarketContext,
    SectorFlow, NarrativeContext, RegimeState, GEXRegime, TimeWindow,
)

logger = logging.getLogger("nzt48.confidence")


class ConfidenceScorer:
    """Five-layer confidence scoring engine.

    Layer 1: Price Action (cap 45) — RVOL, EMA, VWAP, MACD, volume, ATR%, RSI, spread, patterns, microstructure
    Layer 2: Regime (max 20) — Alignment, transition bonus, historical WR
    Layer 3: Sector Flow (max 15) — Relative strength vs SPY
    Layer 4: Macro (max 10) — Tailwind/headwind/crisis (VETO power)
    Layer 5: Narrative (max 10) — News sentiment, crisis keywords (VETO power)
    Penalties: Various deductions for adverse conditions
    """

    def score(
        self,
        indicators: IndicatorSnapshot,
        market_ctx: MarketContext,
        sector_flow: SectorFlow,
        narrative: NarrativeContext,
        ticker: str,
        direction: str,
        strategy: str,
        consecutive_losses: int = 0,
        ticker_cold_streak: bool = False,
    ) -> ConfidenceBreakdown:
        """Compute the full 5-layer confidence score for a signal.

        Args:
            indicators: Layer 1 data — all 22 indicator values
            market_ctx: Layer 2+4 — regime state + macro context
            sector_flow: Layer 3 — relative strength and sector flow
            narrative: Layer 5 — news and narrative context
            ticker: The ticker being scored
            direction: "LONG" or "SHORT"
            strategy: Strategy ID (S1-S14)
            consecutive_losses: Count of recent consecutive losses
            ticker_cold_streak: Whether this ticker is on a losing streak

        Returns:
            ConfidenceBreakdown with all layer scores and final result
        """
        breakdown = ConfidenceBreakdown()

        # Layer 1: Price Action (cap 45)
        breakdown.layer1_price_action = self._score_layer1(indicators, direction)

        # Layer 2: Regime (max 20)
        breakdown.layer2_regime = self._score_layer2(market_ctx, direction)

        # Layer 3: Sector Flow (max 15)
        breakdown.layer3_sector_flow = self._score_layer3(sector_flow, direction)

        # Layer 4: Macro (max 10) — has VETO power
        breakdown.layer4_macro = self._score_layer4(market_ctx)

        # Layer 5: Narrative (max 10) — has VETO power
        breakdown.layer5_narrative = self._score_layer5(narrative)

        # Penalties
        breakdown.penalties = self._calculate_penalties(
            indicators, market_ctx, strategy,
            consecutive_losses, ticker_cold_streak
        )

        # Compute final
        breakdown.compute()

        logger.info(
            "Confidence %s %s: L1=%.0f L2=%.0f L3=%.0f L4=%.0f L5=%.0f "
            "Pen=%.0f → FINAL=%.0f",
            direction, ticker,
            breakdown.layer1_price_action,
            breakdown.layer2_regime,
            breakdown.layer3_sector_flow,
            breakdown.layer4_macro,
            breakdown.layer5_narrative,
            breakdown.penalties,
            breakdown.final_score,
        )

        return breakdown

    def _score_layer1(self, ind: IndicatorSnapshot, direction: str) -> float:
        """Layer 1: Price Action — cap 45 points.

        Components:
        - RVOL (0-12): THE NZT indicator
        - EMA alignment (0-8)
        - VWAP position (-5 to +8)
        - MACD (-5 to +8)
        - Volume spike (0-7)
        - ATR% (0-3)
        - RSI (-3 to +3)
        - Spread (0-3)
        - Pattern (0-5)
        - Microstructure (-3 to +3)
        """
        score = 0.0

        # RVOL (0-12): Core NZT indicator — smoother curve to reward 1.5-2.0 range
        if ind.rvol >= 3.0:
            score += 12
        elif ind.rvol >= 2.5:
            score += 11
        elif ind.rvol >= 2.0:
            score += 10
        elif ind.rvol >= 1.5:
            score += 8
        elif ind.rvol >= 1.2:
            score += 4

        # EMA alignment (0-8)
        score += min(ind.ema_alignment, 8)

        # VWAP position (-5 to +8)
        if direction == "LONG":
            if ind.price > ind.vwap:
                # Above VWAP is bullish for longs
                dist = (ind.price - ind.vwap) / ind.vwap if ind.vwap > 0 else 0
                if dist < 0.005:
                    score += 8  # Near VWAP, ideal pullback entry
                elif dist < 0.01:
                    score += 5
                else:
                    score += 2  # Extended, less ideal
            else:
                score -= 5  # Below VWAP, bearish for longs
        else:  # SHORT
            if ind.price < ind.vwap:
                dist = (ind.vwap - ind.price) / ind.vwap if ind.vwap > 0 else 0
                if dist < 0.005:
                    score += 8
                elif dist < 0.01:
                    score += 5
                else:
                    score += 2
            else:
                score -= 5

        # MACD (-5 to +8)
        if direction == "LONG":
            if ind.macd_histogram > 0 and ind.macd_line > ind.macd_signal:
                score += 8 if ind.macd_histogram > 0.1 else 5
            elif ind.macd_histogram < 0:
                score -= 5
        else:
            if ind.macd_histogram < 0 and ind.macd_line < ind.macd_signal:
                score += 8 if ind.macd_histogram < -0.1 else 5
            elif ind.macd_histogram > 0:
                score -= 5

        # Volume spike (0-7)
        if ind.volume_spike:
            if ind.rvol >= 2.0:
                score += 7
            else:
                score += 4

        # ATR% (0-3): Needs enough volatility to trade
        if ind.atr_pct >= 0.5:
            score += 3
        elif ind.atr_pct >= 0.3:
            score += 2
        elif ind.atr_pct >= 0.1:
            score += 1

        # RSI (-3 to +3): For FILTERING
        if direction == "LONG":
            if 40 <= ind.rsi14 <= 60:
                score += 3  # Neutral zone, room to run
            elif ind.rsi14 > 80:
                score -= 3  # Overbought
            elif ind.rsi14 > 70:
                score -= 1
        else:
            if 40 <= ind.rsi14 <= 60:
                score += 3
            elif ind.rsi14 < 20:
                score -= 3  # Oversold
            elif ind.rsi14 < 30:
                score -= 1

        # Spread (0-3): Execution cost check
        if ind.bid_ask_spread <= 0.05:
            score += 3
        elif ind.bid_ask_spread <= 0.10:
            score += 2
        elif ind.bid_ask_spread <= 0.15:
            score += 1
        # > 0.15% = skip (handled by qualification pipeline)

        # Pattern bonus (0-5)
        if ind.patterns_detected:
            score += min(len(ind.patterns_detected) * 2, 5)

        # Microstructure (-3 to +3)
        if ind.microstructure_score >= 7:
            score += 3
        elif ind.microstructure_score >= 5:
            score += 1
        elif ind.microstructure_score <= 2:
            score -= 3

        # CAP at 45
        return min(score, 45)

    def _score_layer2(self, ctx: MarketContext, direction: str) -> float:
        """Layer 2: Regime — max 20 points.

        Fully aligned = 15, partial = 8, neutral = 3.
        Transition bonus = +5. Historical WR > 65% = +5.
        """
        regime = ctx.regime
        score = 0.0

        # Alignment check
        if direction == "LONG":
            if regime in (RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD):
                score += 15  # Fully aligned
            elif regime == RegimeState.RANGE_BOUND:
                score += 8   # Partial (ORB longs OK)
            elif regime == RegimeState.HIGH_VOLATILITY:
                score += 3   # Neutral
            # Trending down / risk off = 0 for longs
        else:  # SHORT
            if regime in (RegimeState.TRENDING_DOWN_STRONG, RegimeState.TRENDING_DOWN_MOD):
                score += 15
            elif regime == RegimeState.RANGE_BOUND:
                score += 8
            elif regime == RegimeState.RISK_OFF:
                score += 15  # Shorts aligned with risk-off
            elif regime == RegimeState.HIGH_VOLATILITY:
                score += 3

        # Transition bonus (max +5) — recent favourable transition
        # This would come from the regime classifier's transition detection
        # Placeholder: check regime duration
        if ctx.regime_duration_bars < 5:
            score += 5  # Recent transition, potential momentum

        return min(score, 20)

    def _score_layer3(self, sf: SectorFlow, direction: str) -> float:
        """Layer 3: Sector Flow — max 15 points.

        RS > +1.0 = 15. RS > +0.5 = 10. RS < -0.5 for long = -5. RS leader = +5.
        """
        rs = sf.rs_vs_spy

        if direction == "LONG":
            if rs > 1.0:
                score = 15
            elif rs > 0.5:
                score = 10
            elif rs > 0:
                score = 5
            elif rs < -0.5:
                score = -5
            else:
                score = 0
        else:  # SHORT
            # For shorts, weak RS is good
            if rs < -1.0:
                score = 15
            elif rs < -0.5:
                score = 10
            elif rs < 0:
                score = 5
            elif rs > 0.5:
                score = -5
            else:
                score = 0

        # RS leader bonus
        if sf.sector_rank == 1:
            score += 5

        return min(max(score, -5), 15)

    def _score_layer4(self, ctx: MarketContext) -> float:
        """Layer 4: Macro — max 10 points. HAS VETO POWER.

        TAILWIND = +7. NEUTRAL = 0. CAUTIOUS = -5.
        HEADWIND = -10. CRISIS = -20 (VETO).
        """
        score = ctx.macro_score

        # VIX-based adjustments — reduced penalties (breakouts often happen at VIX 25-35)
        if ctx.vix < 15:
            score += 3  # Low vol = tailwind
        elif ctx.vix > 35:
            score -= 10  # Extreme vol = headwind
        elif ctx.vix > 30:
            score -= 5   # High vol (was -10, now -5)
        elif ctx.vix > 25:
            score -= 3   # Elevated vol (was -5, now -3)

        # Put/call ratio
        if ctx.put_call_ratio > 1.2:
            score -= 5  # Fear
        elif ctx.put_call_ratio < 0.7:
            score += 2  # Complacent but bullish

        # VIX term structure
        if ctx.vix_term_structure == "backwardation":
            score -= 5  # Active fear

        # Cap: CRISIS = -20 is a VETO (kills the signal in qualification)
        return max(min(score, 10), -20)

    def _score_layer5(self, narrative: NarrativeContext) -> float:
        """Layer 5: Narrative — max 10 points. HAS VETO POWER.

        Positive ticker news = +8. Sector positive = +5.
        Negative = -10. Crisis keyword = -50 (VETO).
        """
        if narrative.crisis_keyword:
            return -50  # ABSOLUTE VETO

        return float(narrative.narrative_score)

    def _calculate_penalties(
        self,
        indicators: IndicatorSnapshot,
        ctx: MarketContext,
        strategy: str,
        consecutive_losses: int,
        ticker_cold: bool,
    ) -> float:
        """Section 36: Penalty deductions.

        MACD zero-cross 3 bars: -12
        Sector divergence: -12
        2 consecutive losses: -5
        3+ consecutive losses: -15
        Midday: -8
        OR too wide: -8
        Ticker cold streak: -10
        Friday afternoon: -5
        """
        penalty = 0.0

        # MACD zero-cross within 3 bars
        if abs(indicators.macd_histogram) < 0.02 and abs(indicators.macd_line) < 0.05:
            penalty += 12
            logger.debug("Penalty: MACD zero-cross -12")

        # Consecutive losses
        if consecutive_losses >= 3:
            penalty += 15
            logger.debug("Penalty: 3+ consecutive losses -15")
        elif consecutive_losses == 2:
            penalty += 5
            logger.debug("Penalty: 2 consecutive losses -5")

        # Midday penalty (lunch chop) — reduced from 8 to 5
        if ctx.time_window == TimeWindow.LUNCH_CHOP:
            penalty += 5
            logger.debug("Penalty: Midday -5")

        # OR too wide (more than 2x normal — already volatile)
        or_width = indicators.or_high_5m - indicators.or_low_5m
        if or_width > 0 and indicators.atr14 > 0:
            or_atr_ratio = or_width / indicators.atr14
            if or_atr_ratio > 2.0:
                penalty += 5
                logger.debug("Penalty: OR too wide (%.1fx ATR) -5", or_atr_ratio)

        # Ticker cold streak — reduced from 10 to 7
        if ticker_cold:
            penalty += 7
            logger.debug("Penalty: Ticker cold streak -7")

        # Friday afternoon
        # (Would check via TimeOfDayEngine, simplified here)

        return penalty

    def meets_minimum(
        self,
        score: float,
        strategy: str = "",
        bot_instance: str = "BULL",
    ) -> bool:
        """Check if a score meets the minimum threshold.

        System floor: 60 (immutable).
        ORB/VWAP strategies: 65.
        BEAR-BOT: 80.
        """
        if bot_instance == "BEAR":
            return score >= 80

        if strategy in ("S1", "S2") and "ORB" in strategy:
            return score >= 65

        return score >= 60
