"""
S7 — Sector Rotation

Weekly sector rotation strategy based on relative strength (RS) rank changes
and 10-week EMA crosses for sector ETFs.

Monitored sectors (ETFs):
  - SMH  (Semiconductors)
  - QQQ  (Tech / Nasdaq)
  - XLE  (Energy)
  - XLF  (Financials)
  - XLV  (Healthcare)
  - ITA  (Aerospace & Defense)

Signal logic:
  - Calculate 20-day RS of each sector ETF vs SPY
  - Rank sectors 1-6 by RS
  - LONG: RS crosses above 1.0 AND price above 10-week EMA
  - SHORT (inverse): RS drops below 0.90 (severe underperformer)

ISA mapping:
  - Semis strong  -> 3SEM.L (3x Semis long)
  - Tech strong   -> QQQ3.L (3x QQQ long)
  - Semis weak    -> SC3S.L (3x Semis short)

Rebalancing: Weekly (Sunday scan).
Feeds the Sector Rotation Meta-Bot in Phase 2.
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
    ConfidenceBreakdown,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Sector ETFs to monitor, in no particular order
SECTOR_ETFS: list[str] = ["SMH", "QQQ", "XLE", "XLF", "XLV", "ITA"]

# RS thresholds
RS_LONG_THRESHOLD: float = 1.0     # RS above this = strong outperformer
RS_SHORT_THRESHOLD: float = 0.90   # RS below this = severe underperformer

# ISA ETP mapping for sector-level trades
ISA_MAPPING: dict[str, dict[str, str]] = {
    "SMH": {
        "long": "3SEM.L",   # 3x Semiconductors long
        "short": "SC3S.L",  # 3x Semiconductors short
        "leverage": "3x",
    },
    "QQQ": {
        "long": "QQQ3.L",   # 3x QQQ long
        "short": "QQQS.L",  # 3x QQQ short
        "leverage": "3x",
    },
}

# Stop: Based on 10-week EMA distance
STOP_EMA_BUFFER_PCT: float = 0.02   # 2% below 10w EMA for longs

RISK_PER_TRADE: float = 0.0075      # 0.75% per sector position


class SectorRotation(StrategyBase):
    """S7 Sector Rotation strategy.

    Weekly scan that ranks sectors by relative strength vs SPY and generates
    long signals for strong outperformers and short signals for severe
    underperformers. Maps to ISA leveraged ETPs where available.
    """

    def __init__(self) -> None:
        super().__init__(
            name="Sector Rotation",
            strategy_id="S7",
        )
        self._prev_rankings: dict[str, int] = {}   # Previous week's rankings
        self._prev_rs_scores: dict[str, float] = {}  # Previous RS values

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
        """Weekly sector rotation scan.

        This should be called once per week (Sunday scan). It ranks all
        monitored sector ETFs by 20-day RS vs SPY and generates signals
        for sectors crossing key thresholds.

        Note: The caller is responsible for ensuring this is only invoked
        on the weekly schedule. The strategy itself does not enforce timing.
        """
        if not self.enabled:
            return []

        signals: list[Signal] = []

        # --- Step 1: Collect RS scores for each sector ETF ---
        current_rs: dict[str, float] = {}
        for etf in SECTOR_ETFS:
            flow = sector_flows.get(etf)
            if flow is not None:
                current_rs[etf] = flow.rs_vs_spy
            else:
                self.logger.debug("No sector flow data for %s — skipping.", etf)

        if len(current_rs) < 2:
            self.logger.warning(
                "Insufficient sector data (%d of %d). Skipping rotation scan.",
                len(current_rs),
                len(SECTOR_ETFS),
            )
            return signals

        # --- Step 2: Rank sectors by RS ---
        ranked = sorted(current_rs.items(), key=lambda x: x[1], reverse=True)
        current_rankings: dict[str, int] = {
            etf: rank + 1 for rank, (etf, _) in enumerate(ranked)
        }

        self.logger.info(
            "Sector RS rankings: %s",
            " | ".join(f"#{r} {etf} RS={current_rs[etf]:.3f}" for etf, r in
                       sorted(current_rankings.items(), key=lambda x: x[1])),
        )

        # --- Step 3: Evaluate each sector for signals ---
        for etf in SECTOR_ETFS:
            if etf not in current_rs:
                continue

            rs = current_rs[etf]
            rank = current_rankings[etf]
            snap = indicators.get(etf)

            if snap is None:
                continue

            signal = self._evaluate_sector(
                etf, rs, rank, snap, market_ctx, sector_flows.get(etf)
            )
            if signal is not None:
                signals.append(signal)

        # --- Step 4: Update state for next week ---
        self._prev_rankings = current_rankings
        self._prev_rs_scores = current_rs

        return signals

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _evaluate_sector(
        self,
        etf: str,
        rs: float,
        rank: int,
        snap: IndicatorSnapshot,
        market_ctx: MarketContext,
        flow: Optional[SectorFlow],
    ) -> Optional[Signal]:
        """Evaluate a single sector ETF for rotation signal.

        LONG: RS > 1.0 AND price above 10-week EMA
        SHORT: RS < 0.90 (severe underperformance)
        """
        prev_rs = self._prev_rs_scores.get(etf)
        prev_rank = self._prev_rankings.get(etf)

        # --- LONG setup: RS crossing above threshold ---
        if rs >= RS_LONG_THRESHOLD and snap.price > snap.ema10w and snap.ema10w > 0:
            # Check for fresh cross (was below threshold previously)
            is_fresh_cross = prev_rs is not None and prev_rs < RS_LONG_THRESHOLD
            # Also accept if this is the first scan (no previous data)
            is_first_scan = prev_rs is None

            if is_fresh_cross or is_first_scan:
                return self._build_sector_signal(
                    etf=etf,
                    direction=Direction.LONG,
                    rs=rs,
                    rank=rank,
                    snap=snap,
                    market_ctx=market_ctx,
                    flow=flow,
                    reason=f"RS crossed above {RS_LONG_THRESHOLD:.2f} (rs={rs:.3f}), price above 10w EMA",
                )

        # --- SHORT setup: RS dropped below severe threshold ---
        if rs < RS_SHORT_THRESHOLD:
            is_fresh_drop = prev_rs is not None and prev_rs >= RS_SHORT_THRESHOLD
            is_first_scan = prev_rs is None

            if is_fresh_drop or (is_first_scan and rs < RS_SHORT_THRESHOLD - 0.05):
                return self._build_sector_signal(
                    etf=etf,
                    direction=Direction.SHORT,
                    rs=rs,
                    rank=rank,
                    snap=snap,
                    market_ctx=market_ctx,
                    flow=flow,
                    reason=f"RS dropped below {RS_SHORT_THRESHOLD:.2f} (rs={rs:.3f}), severe underperformance",
                )

        return None

    def _build_sector_signal(
        self,
        etf: str,
        direction: Direction,
        rs: float,
        rank: int,
        snap: IndicatorSnapshot,
        market_ctx: MarketContext,
        flow: Optional[SectorFlow],
        reason: str,
    ) -> Optional[Signal]:
        """Build a Signal for a sector rotation trade."""
        entry = snap.price
        atr = snap.atr14 if snap.atr14 > 0 else entry * 0.02  # Fallback: 2% of price

        if entry <= 0 or atr <= 0:
            self.logger.warning("Zero entry (%.4f) or ATR (%.4f) for %s — skipping.", entry, atr, etf)
            return None

        # Stop: below 10-week EMA for longs, above for shorts
        if direction == Direction.LONG:
            if snap.ema10w > 0:
                stop = snap.ema10w * (1 - STOP_EMA_BUFFER_PCT)
            else:
                stop = entry - (2.0 * atr)
            risk = entry - stop
            target_1r = entry + (2.0 * risk)
            target_2r = entry + (3.0 * risk)
        else:
            if snap.ema10w > 0:
                stop = snap.ema10w * (1 + STOP_EMA_BUFFER_PCT)
            else:
                stop = entry + (2.0 * atr)
            risk = stop - entry
            target_1r = entry - (2.0 * risk)
            target_2r = entry - (3.0 * risk)

        signal = self._create_signal(
            ticker=etf,
            direction=direction.value,
            entry=entry,
            stop=stop,
            indicators=snap,
            market_ctx=market_ctx,
        )

        signal.risk_pct = RISK_PER_TRADE
        signal.target_1r = round(target_1r, 4)
        signal.target_2r = round(target_2r, 4)
        signal.bot_instance = BotInstance.SECTOR_ROTATION
        signal.timeframe_layer = "SWING"

        # ISA mapping if available
        isa_info = ISA_MAPPING.get(etf)
        if isa_info is not None:
            dir_key = "long" if direction == Direction.LONG else "short"
            signal.bot = Bot.A
            signal.isa_ticker = isa_info[dir_key]
            signal.isa_leverage = isa_info["leverage"]
            signal.isa_underlying = etf
        else:
            signal.bot = Bot.B  # Trade the ETF directly on IBKR

        # Confidence scoring
        confidence = self._compute_rotation_confidence(
            rs, rank, snap, market_ctx, flow, direction
        )
        signal.confidence = confidence.final_score
        signal.confidence_breakdown = confidence

        # Qualification log
        signal.qualification_log.append(f"SECTOR_ROTATION: {reason}")
        signal.qualification_log.append(f"rank={rank}/{len(SECTOR_ETFS)} rs={rs:.3f}")
        if flow is not None:
            signal.qualification_log.append(f"money_flow={flow.money_flow_direction}")

        self.logger.info(
            "Sector rotation signal: %s %s | RS=%.3f rank=#%d | entry=%.2f stop=%.2f",
            direction.value,
            etf,
            rs,
            rank,
            entry,
            stop,
        )

        return signal

    @staticmethod
    def _compute_rotation_confidence(
        rs: float,
        rank: int,
        snap: IndicatorSnapshot,
        market_ctx: MarketContext,
        flow: Optional[SectorFlow],
        direction: Direction,
    ) -> ConfidenceBreakdown:
        """Compute confidence breakdown for a sector rotation signal.

        Layer 3 (sector flow) is weighted most heavily here since this
        is fundamentally a sector-level strategy.
        """
        cb = ConfidenceBreakdown()

        # Layer 1: Price action — EMA alignment and trend
        if snap.ema_alignment >= 6:
            cb.layer1_price_action = 30.0
        elif snap.ema_alignment >= 4:
            cb.layer1_price_action = 20.0
        else:
            cb.layer1_price_action = 10.0

        # Bonus for price above VWAP
        if snap.price > snap.vwap and snap.vwap > 0:
            cb.layer1_price_action = min(45.0, cb.layer1_price_action + 5.0)

        # Layer 2: Regime alignment
        if direction == Direction.LONG and market_ctx.regime in (
            RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD
        ):
            cb.layer2_regime = 18.0
        elif direction == Direction.SHORT and market_ctx.regime in (
            RegimeState.TRENDING_DOWN_STRONG, RegimeState.TRENDING_DOWN_MOD
        ):
            cb.layer2_regime = 18.0
        elif market_ctx.regime == RegimeState.RANGE_BOUND:
            cb.layer2_regime = 10.0
        else:
            cb.layer2_regime = 5.0

        # Layer 3: Sector flow — primary driver for S7
        rs_strength = abs(rs - 1.0)  # Distance from neutral
        cb.layer3_sector_flow = min(15.0, rs_strength * 30.0)

        # Bonus for top-ranked sectors or bottom-ranked for shorts
        if direction == Direction.LONG and rank <= 2:
            cb.layer3_sector_flow = min(15.0, cb.layer3_sector_flow + 3.0)
        elif direction == Direction.SHORT and rank >= len(SECTOR_ETFS) - 1:
            cb.layer3_sector_flow = min(15.0, cb.layer3_sector_flow + 3.0)

        # Layer 4: Macro
        if market_ctx.macro_score > 3:
            cb.layer4_macro = 8.0
        elif market_ctx.macro_score > 0:
            cb.layer4_macro = 5.0
        else:
            cb.layer4_macro = 2.0

        # Layer 5: Narrative — money flow confirmation
        if flow is not None:
            if direction == Direction.LONG and flow.money_flow_direction == "inflow":
                cb.layer5_narrative = 8.0
            elif direction == Direction.SHORT and flow.money_flow_direction == "outflow":
                cb.layer5_narrative = 8.0
            else:
                cb.layer5_narrative = 3.0
        else:
            cb.layer5_narrative = 2.0

        # Penalties: high volatility environments reduce rotation confidence
        if market_ctx.regime in (RegimeState.HIGH_VOLATILITY, RegimeState.SHOCK):
            cb.penalties = 8.0
        elif market_ctx.vix > 25:
            cb.penalties = 5.0

        cb.compute()
        return cb
