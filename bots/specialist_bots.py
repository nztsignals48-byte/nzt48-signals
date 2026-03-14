"""
NZT-48 Multi-Bot Architecture — Specialist Bot Implementations
Section 64: BULL-BOT, RANGE-BOT, BEAR-BOT
"""
from __future__ import annotations
import copy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import BotInstance, RegimeState, Signal, Direction, DrawdownLevel
from bots.bot_base import BotBase


class BullBot(BotBase):
    """BULL-BOT: Aggressive trend follower.

    Active in: TRENDING_UP_STRONG, TRENDING_UP_MOD
    Strategies: S1, S2, S5, S10, S11, S13, S14
    Personality: Rides trends. Wider stops. Trails aggressively.
    Capital: 50%
    """

    def __init__(self):
        super().__init__(BotInstance.BULL, "bull_bot")

    def get_active_regimes(self) -> list[RegimeState]:
        return [
            RegimeState.TRENDING_UP_STRONG,
            RegimeState.TRENDING_UP_MOD,
        ]

    def apply_personality(self, signal: Signal, atr: float) -> Signal:
        """Bull-bot: Wider stops, aggressive trailing, prefer long."""
        signal = super().apply_personality(signal, atr)

        # Bull-bot prefers long direction
        if signal.direction == Direction.SHORT:
            # Short signals in uptrend require higher confidence
            signal.confidence *= 0.85

        # Trail trigger parsing — "+1.0R" → trail at 1.0R profit
        trail_r = float(self.trail_trigger.replace("+", "").replace("R", ""))
        risk = abs(signal.entry - signal.stop)
        if signal.direction == Direction.LONG:
            signal.trail = signal.entry + (risk * trail_r)
        else:
            signal.trail = signal.entry - (risk * trail_r)

        return signal


class RangeBot(BotBase):
    """RANGE-BOT: Patient mean-reversion trader.

    Active in: RANGE_BOUND, HIGH_VOLATILITY (carefully)
    Strategies: S3, S4, S8, S9, S12
    Personality: Fades extremes. Quick partials. Intraday only.
    Capital: 30%
    """

    def __init__(self):
        super().__init__(BotInstance.RANGE, "range_bot")
        # Store original values so we can restore them when regime changes
        self._orig_min_confidence = self.min_confidence
        self._orig_max_positions = self.max_positions

    def get_active_regimes(self) -> list[RegimeState]:
        return [
            RegimeState.RANGE_BOUND,
            RegimeState.HIGH_VOLATILITY,
        ]

    def apply_personality(self, signal: Signal, atr: float) -> Signal:
        """Range-bot: Tight stops, quick partials, no overnight."""
        signal = super().apply_personality(signal, atr)

        # Range-bot: force intraday
        if not self.overnight_allowed:
            signal.qualification_log.append("RANGE-BOT: Intraday only, must close by session end")

        # Quick partial: 50% off at first target
        trail_r = float(self.trail_trigger.replace("+", "").replace("R", ""))
        risk = abs(signal.entry - signal.stop)
        if signal.direction == Direction.LONG:
            signal.trail = signal.entry + (risk * trail_r)
        else:
            signal.trail = signal.entry - (risk * trail_r)

        return signal

    def should_activate(self, regime: RegimeState) -> bool:
        """Range-bot also partially activates in HIGH_VOL but with extra caution."""
        if regime == RegimeState.HIGH_VOLATILITY:
            # In high vol, range-bot operates but with tighter params
            self.min_confidence = max(self._orig_min_confidence, 75)
            self.max_positions = min(self._orig_max_positions, 2)
            return True
        # Restore original params when regime is not HIGH_VOLATILITY
        self.min_confidence = self._orig_min_confidence
        self.max_positions = self._orig_max_positions
        return regime in self.get_active_regimes()


class BearBot(BotBase):
    """BEAR-BOT: Defensive capital preserver.

    Active in: RISK_OFF, TRENDING_DOWN_STRONG, TRENDING_DOWN_MOD, SHOCK
    Strategies: S1 (short bias), S6, S7
    Personality: Highest conviction only. Small size. Preserve capital.
    Capital: 20%
    """

    def __init__(self):
        super().__init__(BotInstance.BEAR, "bear_bot")
        # Store original values so we can restore them when regime changes
        self._orig_min_confidence = self.min_confidence
        self._orig_max_positions = self.max_positions
        self._orig_max_risk_per_trade = self.max_risk_per_trade

    def get_active_regimes(self) -> list[RegimeState]:
        return [
            RegimeState.RISK_OFF,
            RegimeState.TRENDING_DOWN_STRONG,
            RegimeState.TRENDING_DOWN_MOD,
            RegimeState.SHOCK,
        ]

    def apply_personality(self, signal: Signal, atr: float) -> Signal:
        """Bear-bot: Tight stops, defensive, prefer shorts and hedges."""
        signal = super().apply_personality(signal, atr)

        # In bear mode, long signals get severe confidence penalty
        if signal.direction == Direction.LONG:
            signal.confidence *= 0.7
            signal.qualification_log.append("BEAR-BOT: Long signal penalized in downtrend")

        # Tightest trail
        trail_r = float(self.trail_trigger.replace("+", "").replace("R", ""))
        risk = abs(signal.entry - signal.stop)
        if signal.direction == Direction.LONG:
            signal.trail = signal.entry + (risk * trail_r)
        else:
            signal.trail = signal.entry - (risk * trail_r)

        return signal

    def should_activate(self, regime: RegimeState) -> bool:
        """Bear-bot activates defensively. In SHOCK, go to max-conservative."""
        if regime == RegimeState.SHOCK:
            self.min_confidence = 90
            self.max_positions = 1
            self.max_risk_per_trade = 0.002
            self.logger.warning("SHOCK MODE: Ultra-defensive parameters")
            return True
        # Restore original params when regime is not SHOCK
        self.min_confidence = self._orig_min_confidence
        self.max_positions = self._orig_max_positions
        self.max_risk_per_trade = self._orig_max_risk_per_trade
        return regime in self.get_active_regimes()


class BotRouter:
    """Routes signals to the appropriate specialist bot based on regime."""

    def __init__(self):
        self.bull = BullBot()
        self.range = RangeBot()
        self.bear = BearBot()
        self._all_bots = [self.bull, self.range, self.bear]

    def update_regime(self, regime: RegimeState) -> None:
        """Update regime for all bots, activating/deactivating as needed."""
        for bot in self._all_bots:
            bot.activate(regime)

    def route_signal(self, signal: Signal, atr: float) -> tuple[BotBase | None, Signal]:
        """Route a signal to the correct bot. Returns (bot, adjusted_signal) or (None, signal)."""
        # Try each bot in priority order
        for bot in self._all_bots:
            can_take, reason = bot.can_take_signal(signal)
            if can_take:
                # Deep-copy to avoid mutating the original signal
                signal_copy = copy.deepcopy(signal)
                adjusted = bot.apply_personality(signal_copy, atr)
                adjusted.bot_instance = bot.instance
                return bot, adjusted
        return None, signal

    def get_active_bot(self) -> BotBase | None:
        """Get the most appropriate active bot.

        Returns the active, non-halted bot with the most remaining capacity
        (fewest open positions relative to its max). This avoids always
        returning the first bot in list order.
        """
        candidates = [bot for bot in self._all_bots if bot.active and not bot.is_halted()]
        if not candidates:
            return None
        # Pick the bot with the most remaining position capacity
        return max(candidates, key=lambda b: b.max_positions - len(b.open_positions))

    def get_all_status(self) -> list[dict]:
        """Get status of all bots."""
        return [bot.get_status() for bot in self._all_bots]

    def reset_daily(self) -> None:
        """Reset all bots for new session."""
        for bot in self._all_bots:
            bot.reset_daily()

    def halt_all(self, reason: str) -> None:
        """Halt all bots (Overseer emergency)."""
        for bot in self._all_bots:
            bot.halt(reason)
