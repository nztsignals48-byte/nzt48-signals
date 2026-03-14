"""
S6 — Macro Regime Shift

Detects macro rotations by monitoring DXY, yields, gold, and oil crossing
key levels. Primarily a BEAR-BOT strategy that generates signals for
inverse ETPs when macro conditions shift decisively.

Monitored instruments:
  - DXY (dollar index via UUP)
  - 10Y yields (via TLT inverse relationship)
  - Gold (GLD)
  - Oil (via XLE)

Signal logic:
  - LONG (risk-on): Falling yields + weak dollar + semis leading
  - SHORT (risk-off): Rising yields + strong dollar + gold surging + equities weak
  - Only fires when macro_score delta > 5 points (significant shift)

Maps to:
  - Bot A: QQQ3/QQQS leveraged ETPs
  - Informs Bot B stock selection bias
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.base import StrategyBase
from models import (
    Signal,
    IndicatorSnapshot,
    MarketContext,
    SectorFlow,
    NarrativeContext,
    Direction,
    Bot,
    BotInstance,
    RegimeState,
    GEXRegime,
    TimeWindow,
    ConfidenceBreakdown,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MACRO_DELTA_THRESHOLD: int = 5       # Minimum macro score change to fire
RISK_PER_TRADE: float = 0.0075      # 0.75% default risk

# Macro instrument tickers we monitor
MACRO_INSTRUMENTS: dict[str, str] = {
    "dollar": "UUP",
    "bonds": "TLT",
    "gold": "GLD",
    "oil": "XLE",
    "semis": "SMH",
}

# ISA ETP mappings for macro trades
ISA_LONG_RISK_ON: str = "QQQ3.L"     # 3x QQQ long
ISA_SHORT_RISK_OFF: str = "QQQS.L"   # 3x QQQ short

# Thresholds for macro component scoring
DXY_WEAK_THRESHOLD: float = -0.5     # DXY RS vs SPY below this = weak dollar
DXY_STRONG_THRESHOLD: float = 0.5    # DXY RS vs SPY above this = strong dollar
TLT_RISING_RS: float = 0.3           # TLT outperforming = yields falling
TLT_FALLING_RS: float = -0.3         # TLT underperforming = yields rising
GOLD_SURGE_RS: float = 0.5           # Gold outperforming strongly
SEMIS_LEAD_RS: float = 0.3           # Semis leading market

# VIX levels for regime classification
VIX_ELEVATED: float = 20.0
VIX_HIGH: float = 25.0


class MacroRegimeShift(StrategyBase):
    """S6 Macro Regime Shift detector.

    Monitors cross-asset macro relationships to detect significant regime
    rotations. Generates signals for leveraged ETPs (Bot A) and informs
    directional bias for Bot B stock selection.
    """

    def __init__(self) -> None:
        super().__init__(
            name="Macro Regime Shift",
            strategy_id="S6",
        )
        self._prev_macro_score: Optional[int] = None
        self._prev_regime_bias: Optional[str] = None  # "RISK_ON" / "RISK_OFF" / None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(
        self,
        tickers: list[str],
        indicators: dict[str, IndicatorSnapshot],
        market_ctx: MarketContext,
        sector_flows: dict[str, SectorFlow],
        narratives: dict[str, NarrativeContext],
    ) -> list[Signal]:
        """Scan macro instruments for regime shift signals.

        Unlike single-stock strategies, this scans the macro context itself
        and generates ETP-level signals rather than individual stock signals.
        """
        if not self.enabled:
            return []

        signals: list[Signal] = []

        # --- Compute macro score delta ---
        current_score = market_ctx.macro_score
        if self._prev_macro_score is not None:
            delta = current_score - self._prev_macro_score
        else:
            # First scan — no delta available, record baseline
            self._prev_macro_score = current_score
            self.logger.info("Macro baseline recorded: score=%d", current_score)
            return signals

        # Gate: only fire on significant delta
        if abs(delta) < MACRO_DELTA_THRESHOLD:
            self._prev_macro_score = current_score
            return signals

        # --- Build macro component scores ---
        components = self._score_macro_components(indicators, sector_flows, market_ctx)
        regime_bias = self._classify_regime_bias(components, market_ctx)

        # Skip if bias hasn't actually changed
        if regime_bias == self._prev_regime_bias:
            self._prev_macro_score = current_score
            return signals

        self.logger.info(
            "Macro regime shift detected: %s -> %s (delta=%+d, score=%d)",
            self._prev_regime_bias or "NONE",
            regime_bias,
            delta,
            current_score,
        )

        # --- Generate signal ---
        signal = self._build_macro_signal(
            regime_bias, components, indicators, market_ctx
        )
        if signal is not None:
            signals.append(signal)

        # Update state
        self._prev_macro_score = current_score
        self._prev_regime_bias = regime_bias

        return signals

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _score_macro_components(
        self,
        indicators: dict[str, IndicatorSnapshot],
        sector_flows: dict[str, SectorFlow],
        market_ctx: MarketContext,
    ) -> dict[str, float]:
        """Score each macro component on a -1 to +1 scale.

        Positive = risk-on leaning.  Negative = risk-off leaning.
        """
        scores: dict[str, float] = {
            "dollar": 0.0,
            "yields": 0.0,
            "gold": 0.0,
            "oil": 0.0,
            "semis": 0.0,
            "vix": 0.0,
            "internals": 0.0,
        }

        # Dollar (UUP) — weak dollar is risk-on
        uup_flow = sector_flows.get("UUP")
        if uup_flow is not None:
            if uup_flow.rs_vs_spy < DXY_WEAK_THRESHOLD:
                scores["dollar"] = 1.0   # Weak dollar = risk-on
            elif uup_flow.rs_vs_spy > DXY_STRONG_THRESHOLD:
                scores["dollar"] = -1.0  # Strong dollar = risk-off
            else:
                scores["dollar"] = 0.0

        # Yields (TLT) — rising TLT = falling yields = risk-on
        tlt_flow = sector_flows.get("TLT")
        if tlt_flow is not None:
            if tlt_flow.rs_vs_spy > TLT_RISING_RS:
                scores["yields"] = 1.0   # Falling yields
            elif tlt_flow.rs_vs_spy < TLT_FALLING_RS:
                scores["yields"] = -1.0  # Rising yields
            else:
                scores["yields"] = 0.0

        # Gold (GLD) — surging gold often signals risk-off
        gld_flow = sector_flows.get("GLD")
        if gld_flow is not None:
            if gld_flow.rs_vs_spy > GOLD_SURGE_RS:
                scores["gold"] = -1.0    # Gold surging = risk-off
            elif gld_flow.rs_vs_spy < 0.0:
                scores["gold"] = 0.5     # Gold weak = mild risk-on
            else:
                scores["gold"] = 0.0

        # Oil / Energy (XLE)
        xle_flow = sector_flows.get("XLE")
        if xle_flow is not None:
            if xle_flow.money_flow_direction == "inflow":
                scores["oil"] = 0.5
            elif xle_flow.money_flow_direction == "outflow":
                scores["oil"] = -0.5

        # Semis (SMH) — semis leading = risk-on
        smh_flow = sector_flows.get("SMH")
        if smh_flow is not None:
            if smh_flow.rs_vs_spy > SEMIS_LEAD_RS:
                scores["semis"] = 1.0
            elif smh_flow.rs_vs_spy < -SEMIS_LEAD_RS:
                scores["semis"] = -1.0

        # VIX
        if market_ctx.vix > VIX_HIGH:
            scores["vix"] = -1.0
        elif market_ctx.vix > VIX_ELEVATED:
            scores["vix"] = -0.5
        elif market_ctx.vix < 15.0:
            scores["vix"] = 1.0
        else:
            scores["vix"] = 0.0

        # Market internals composite (0-4 scale, map to -1..+1)
        internals = market_ctx.internals_composite
        scores["internals"] = (internals - 2) / 2.0  # 0->-1, 2->0, 4->1

        return scores

    @staticmethod
    def _classify_regime_bias(
        components: dict[str, float],
        market_ctx: MarketContext,
    ) -> str:
        """Classify overall macro bias as RISK_ON, RISK_OFF, or NEUTRAL.

        Requires alignment across multiple components to fire.
        """
        if not components:
            return "NEUTRAL"

        total = sum(components.values())
        component_count = len(components)

        # Normalise to -1..+1 range
        avg_score = total / component_count if component_count > 0 else 0.0

        # Count how many components agree on direction
        bullish_count = sum(1 for v in components.values() if v > 0)
        bearish_count = sum(1 for v in components.values() if v < 0)

        # Need at least 4 of 7 components aligned for a regime call
        if avg_score > 0.25 and bullish_count >= 4:
            return "RISK_ON"
        elif avg_score < -0.25 and bearish_count >= 4:
            return "RISK_OFF"
        else:
            return "NEUTRAL"

    def _build_macro_signal(
        self,
        regime_bias: str,
        components: dict[str, float],
        indicators: dict[str, IndicatorSnapshot],
        market_ctx: MarketContext,
    ) -> Optional[Signal]:
        """Build a Signal for the detected macro shift.

        Maps to ISA ETPs for Bot A and sets bias for Bot B.
        """
        if regime_bias == "NEUTRAL":
            return None

        # Determine signal direction and ISA instrument
        if regime_bias == "RISK_ON":
            direction = Direction.LONG
            isa_ticker = ISA_LONG_RISK_ON
            target_ticker = "QQQ"
            bot_instance = BotInstance.BULL
        else:  # RISK_OFF
            direction = Direction.SHORT
            isa_ticker = ISA_SHORT_RISK_OFF
            target_ticker = "QQQ"
            bot_instance = BotInstance.BEAR

        # Get QQQ indicators for entry/stop calculation
        qqq_snap = indicators.get("QQQ") or indicators.get("SPY")
        if qqq_snap is None:
            self.logger.warning("No QQQ or SPY indicator data — cannot build macro signal.")
            return None

        entry = qqq_snap.price
        atr = qqq_snap.atr14

        if atr <= 0 or entry <= 0:
            self.logger.warning("Invalid ATR or price for macro signal.")
            return None

        # Stop based on the macro level that triggered the signal
        # Use a wider stop (2.5x ATR) since this is a macro-level trade
        stop_distance = 2.5 * atr
        if direction == Direction.LONG:
            stop = entry - stop_distance
            target_1r = entry + (2.0 * stop_distance)
            target_2r = entry + (3.0 * stop_distance)
        else:
            stop = entry + stop_distance
            target_1r = entry - (2.0 * stop_distance)
            target_2r = entry - (3.0 * stop_distance)

        signal = self._create_signal(
            ticker=target_ticker,
            direction=direction.value,
            entry=entry,
            stop=stop,
            indicators=qqq_snap,
            market_ctx=market_ctx,
        )

        # Populate macro-specific fields
        signal.risk_pct = RISK_PER_TRADE
        signal.target_1r = round(target_1r, 4)
        signal.target_2r = round(target_2r, 4)
        signal.bot = Bot.A
        signal.bot_instance = bot_instance
        signal.isa_ticker = isa_ticker
        signal.isa_leverage = "3x"
        signal.isa_underlying = "QQQ"
        signal.timeframe_layer = "SWING"

        # Build confidence from macro components
        confidence = self._compute_macro_confidence(components, market_ctx)
        signal.confidence = confidence.final_score
        signal.confidence_breakdown = confidence

        # Log component breakdown
        component_str = " | ".join(f"{k}={v:+.1f}" for k, v in components.items())
        signal.qualification_log.append(f"MACRO_SHIFT: bias={regime_bias}")
        signal.qualification_log.append(f"Components: {component_str}")
        signal.qualification_log.append(f"macro_score={market_ctx.macro_score}")

        return signal

    @staticmethod
    def _compute_macro_confidence(
        components: dict[str, float],
        market_ctx: MarketContext,
    ) -> ConfidenceBreakdown:
        """Compute the 5-layer confidence score for a macro signal.

        Macro signals weight layer 4 (macro) much more heavily than typical.
        """
        cb = ConfidenceBreakdown()

        # Layer 1: Price action (capped at 45, but macro signals use less)
        # Use regime confidence as a proxy for price-action alignment
        cb.layer1_price_action = min(20.0, market_ctx.regime_confidence * 20.0)

        # Layer 2: Regime alignment
        regime = market_ctx.regime
        if regime in (RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD):
            cb.layer2_regime = 15.0
        elif regime == RegimeState.RANGE_BOUND:
            cb.layer2_regime = 8.0
        elif regime in (RegimeState.TRENDING_DOWN_STRONG, RegimeState.TRENDING_DOWN_MOD):
            cb.layer2_regime = 12.0  # Good for SHORT macro signals
        else:
            cb.layer2_regime = 5.0

        # Layer 3: Sector flow agreement
        aligned = sum(1 for v in components.values() if abs(v) >= 0.5)
        cb.layer3_sector_flow = min(15.0, aligned * 2.5)

        # Layer 4: Macro — this is the primary driver for S6
        total_strength = sum(abs(v) for v in components.values())
        cb.layer4_macro = min(10.0, total_strength * 1.5)

        # Layer 5: Narrative (term structure, VIX shape)
        if market_ctx.vix_term_structure == "backwardation":
            cb.layer5_narrative = 8.0  # Fear confirmed
        elif market_ctx.vix_term_structure == "contango":
            cb.layer5_narrative = 5.0  # Normal/complacent
        else:
            cb.layer5_narrative = 3.0

        # Penalties
        if market_ctx.fomc_today or market_ctx.cpi_nfp_today:
            cb.penalties = 10.0  # Macro signals unreliable on event days

        cb.compute()
        return cb
