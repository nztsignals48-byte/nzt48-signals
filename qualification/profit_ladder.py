"""
NZT-48 Trading System — 7-Rung Profit Ladder
Section 40: Stocks (Bot B) — 7 rungs from entry to gift.
Section 41: Bot A (3x ETPs) — faster ladder for leveraged products.

"NEVER let green turn red." Once in profit, the only question
is HOW MUCH we keep. The profit ladder is a state machine that
mechanically extracts gains at predefined levels.

The ladder runs in the position reconciler loop (every 30s).
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Position, Direction, Bot, LadderRung

logger = logging.getLogger("nzt48.profit_ladder")


@dataclass
class LadderAction:
    """Action produced by the profit ladder state machine."""
    rung: LadderRung
    action: str                  # Human-readable action description
    sell_pct: float = 0.0        # Percentage of position to sell (0-1)
    new_stop: Optional[float] = None  # Updated stop level
    trail_distance: Optional[float] = None  # ATR multiple for trailing
    remaining_pct: float = 1.0   # Position remaining after action


class ProfitLadder:
    """7-rung profit extraction state machine for stocks (Bot B).

    Rung 0: Entry — full position, stop at calculated level
    Rung 1: +0.3R — reduce risk, tighten stop
    Rung 2: +0.5R — BREAKEVEN, trade is now SAFE
    Rung 3: +1.0R — FIRST CASH, sell 40%
    Rung 4: +1.5R — Evaluate (RVOL > 1.2 + regime = hold)
    Rung 5: +2.0R — SECOND CASH, sell to 30%
    Rung 6: +2.5R — RUNNER, trail 1.5x ATR
    Rung 7: +4.0R — GIFT, tighten trail to 0.5x ATR
    """

    def evaluate(
        self,
        position: Position,
        current_price: float,
        atr: float,
        rvol: float = 0.0,
        regime_trending: bool = False,
    ) -> Optional[LadderAction]:
        """Evaluate a position against the profit ladder.

        Calculates current R-multiple and determines if the position
        has reached a new rung. Returns LadderAction if state change needed.

        Args:
            position: The open position to evaluate
            current_price: Current market price
            atr: Current ATR(14) value for stop calculations
            rvol: Current RVOL for rung 4 evaluation
            regime_trending: Whether current regime is TRENDING

        Returns:
            LadderAction if rung advancement needed, None otherwise
        """
        position.current_price = current_price

        # Calculate R-multiple
        risk = abs(position.entry - position.original_stop)
        if risk == 0:
            return None

        if position.direction == Direction.LONG:
            r_multiple = (current_price - position.entry) / risk
        else:
            r_multiple = (position.entry - current_price) / risk

        position.unrealised_r = r_multiple
        position.unrealised_pnl = (
            (current_price - position.entry) * position.shares
            if position.direction == Direction.LONG
            else (position.entry - current_price) * position.shares
        )

        current_rung = position.ladder_rung

        # Check for forced exit: holding losers > -1R for > 5 min
        # (Section 44: emotional firewall — handled separately)

        # Rung 7: +4.0R+ — GIFT. Tighten trail.
        if r_multiple >= 4.0 and current_rung < LadderRung.GIFT:
            new_stop = self._calc_trail_stop(position, current_price, atr, 0.5)
            return LadderAction(
                rung=LadderRung.GIFT,
                action=f"GIFT (+{r_multiple:.1f}R). Tighten trail to 0.5x ATR.",
                sell_pct=0.0,
                new_stop=new_stop,
                trail_distance=0.5,
                remaining_pct=position.remaining_pct,
            )

        # Rung 6: +2.5R+ — RUNNER. Trail 1.5x ATR.
        if r_multiple >= 2.5 and current_rung < LadderRung.RUNNER:
            new_stop = self._calc_trail_stop(position, current_price, atr, 1.5)
            return LadderAction(
                rung=LadderRung.RUNNER,
                action=f"RUNNER (+{r_multiple:.1f}R). Trail 1.5x ATR. Let it run.",
                sell_pct=0.0,
                new_stop=new_stop,
                trail_distance=1.5,
                remaining_pct=position.remaining_pct,
            )

        # Rung 5: +2.0R — SECOND CASH. Sell to 30%.
        if r_multiple >= 2.0 and current_rung < LadderRung.SECOND_CASH:
            sell_pct = max(0, position.remaining_pct - 0.30)
            new_stop = position.entry + (1.0 * risk) if position.direction == Direction.LONG \
                else position.entry - (1.0 * risk)
            return LadderAction(
                rung=LadderRung.SECOND_CASH,
                action=f"SECOND CASH (+{r_multiple:.1f}R). Sell to 30%. Stop at +1.0R.",
                sell_pct=sell_pct,
                new_stop=new_stop,
                remaining_pct=0.30,
            )

        # Rung 4: +1.5R — Evaluate. RVOL > 1.2 + regime = hold.
        if r_multiple >= 1.5 and current_rung < LadderRung.EVALUATE:
            hold = rvol > 1.2 and regime_trending
            new_stop = position.entry + (0.7 * risk) if position.direction == Direction.LONG \
                else position.entry - (0.7 * risk)

            if hold:
                return LadderAction(
                    rung=LadderRung.EVALUATE,
                    action=f"EVALUATE (+{r_multiple:.1f}R). RVOL={rvol:.1f} + TRENDING = HOLD. "
                           f"Stop to +0.7R.",
                    sell_pct=0.0,
                    new_stop=new_stop,
                    remaining_pct=position.remaining_pct,
                )
            else:
                # Sell some if conditions aren't strong
                sell_pct = 0.20 if position.remaining_pct > 0.40 else 0.0
                return LadderAction(
                    rung=LadderRung.EVALUATE,
                    action=f"EVALUATE (+{r_multiple:.1f}R). RVOL={rvol:.1f}, "
                           f"regime={'trending' if regime_trending else 'not trending'}. "
                           f"Partial sell. Stop to +0.7R.",
                    sell_pct=sell_pct,
                    new_stop=new_stop,
                    remaining_pct=position.remaining_pct - sell_pct,
                )

        # Rung 3: +1.0R — FIRST CASH. Sell 40%.
        if r_multiple >= 1.0 and current_rung < LadderRung.FIRST_CASH:
            sell_pct = 0.40
            new_stop = position.entry + (0.3 * risk) if position.direction == Direction.LONG \
                else position.entry - (0.3 * risk)
            return LadderAction(
                rung=LadderRung.FIRST_CASH,
                action=f"FIRST CASH (+{r_multiple:.1f}R). Sell 40%. Stop to +0.3R.",
                sell_pct=sell_pct,
                new_stop=new_stop,
                remaining_pct=0.60,
            )

        # Rung 2: +0.5R — BREAKEVEN. Trade is now SAFE.
        if r_multiple >= 0.5 and current_rung < LadderRung.BREAKEVEN:
            return LadderAction(
                rung=LadderRung.BREAKEVEN,
                action=f"BREAKEVEN (+{r_multiple:.1f}R). Stop moved to breakeven. SAFE.",
                sell_pct=0.0,
                new_stop=position.entry,
                remaining_pct=1.0,
            )

        # Rung 1: +0.3R — Reduce risk.
        if r_multiple >= 0.3 and current_rung < LadderRung.REDUCE_RISK:
            new_stop = position.entry - (0.15 * atr) if position.direction == Direction.LONG \
                else position.entry + (0.15 * atr)
            return LadderAction(
                rung=LadderRung.REDUCE_RISK,
                action=f"REDUCE RISK (+{r_multiple:.1f}R). Stop tightened.",
                sell_pct=0.0,
                new_stop=new_stop,
                remaining_pct=1.0,
            )

        # No rung change
        return None

    def _calc_trail_stop(
        self,
        position: Position,
        current_price: float,
        atr: float,
        atr_multiple: float,
    ) -> float:
        """Calculate trailing stop at N x ATR from current price."""
        trail_distance = atr * atr_multiple
        if position.direction == Direction.LONG:
            new_stop = current_price - trail_distance
            # Never move stop down
            return max(new_stop, position.current_stop)
        else:
            new_stop = current_price + trail_distance
            # Never move stop up for shorts
            return min(new_stop, position.current_stop)


class ETPProfitLadder:
    """Section 41: Bot A Ladder (3x ETPs — FASTER).

    3x ETPs move fast. A 1% underlying move = 3% ETP move.
    All percentages are ETP price moves (not underlying).

    Entry: stop at -3% from entry (= 1R risk)
    +1%: breakeven (0.33R)
    +2.5%: sell 50%, stop to +0.5% (0.83R)
    +5%: sell remaining, CLOSED (1.67R)

    Exception: strong trend (conf > 85 + TRENDING) = hold 50% with 2% trailing
    """

    def evaluate(
        self,
        position: Position,
        current_price: float,
        confidence: float = 0.0,
        regime_trending: bool = False,
    ) -> Optional[LadderAction]:
        """Evaluate an ETP position against the faster Bot A ladder."""
        if position.entry == 0:
            return None

        pct_move = (current_price - position.entry) / position.entry
        if position.direction == Direction.SHORT:
            pct_move = -pct_move  # Invert for shorts

        position.current_price = current_price
        position.unrealised_pnl = (
            (current_price - position.entry) * position.shares
            if position.direction == Direction.LONG
            else (position.entry - current_price) * position.shares
        )

        # Strong trend exception: conf > 85 + TRENDING = hold 50% with 2% trail
        strong_trend = confidence > 85 and regime_trending

        # +5%: Close remaining (or trail if strong trend)
        if pct_move >= 0.05:
            if strong_trend and position.remaining_pct > 0.5:
                trail = current_price * 0.98 if position.direction == Direction.LONG \
                    else current_price * 1.02
                return LadderAction(
                    rung=LadderRung.RUNNER,
                    action=f"ETP +{pct_move*100:.1f}%. STRONG TREND. Hold 50% with 2% trail.",
                    sell_pct=max(0, position.remaining_pct - 0.5),
                    new_stop=trail,
                    trail_distance=0.02,
                    remaining_pct=0.5,
                )
            else:
                return LadderAction(
                    rung=LadderRung.SECOND_CASH,
                    action=f"ETP +{pct_move*100:.1f}%. CLOSED at 1.67R. Full exit.",
                    sell_pct=position.remaining_pct,
                    new_stop=None,
                    remaining_pct=0.0,
                )

        # +2.5%: Sell 50%, stop to +0.5%
        if pct_move >= 0.025 and position.remaining_pct > 0.5:
            new_stop = position.entry * 1.005 if position.direction == Direction.LONG \
                else position.entry * 0.995
            return LadderAction(
                rung=LadderRung.FIRST_CASH,
                action=f"ETP +{pct_move*100:.1f}%. Sell 50%. Stop to +0.5% (0.83R).",
                sell_pct=position.remaining_pct - 0.5,
                new_stop=new_stop,
                remaining_pct=0.5,
            )

        # +1%: Breakeven
        if pct_move >= 0.01 and position.ladder_rung < LadderRung.BREAKEVEN:
            return LadderAction(
                rung=LadderRung.BREAKEVEN,
                action=f"ETP +{pct_move*100:.1f}%. BREAKEVEN (0.33R). Stop to entry.",
                sell_pct=0.0,
                new_stop=position.entry,
                remaining_pct=position.remaining_pct,
            )

        return None
