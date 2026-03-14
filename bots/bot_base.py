"""
NZT-48 Multi-Bot Architecture — Base Bot Class
Section 64: Each specialist bot operates as a focused personality.
"""
from __future__ import annotations
import logging
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    Signal, Direction, Bot, BotInstance, RegimeState,
    MarketContext, IndicatorSnapshot, SectorFlow, NarrativeContext,
    SignalStatus, Position, DrawdownLevel,
)
import config as cfg


class BotBase(ABC):
    """Base class for specialist trading bots.

    Each bot has:
    - A personality that defines trading style
    - Active regimes where it's allowed to trade
    - Assigned strategies from the 14-strategy pool
    - Capital allocation percentage
    - Independent risk parameters
    - Daily loss limit
    - Max positions
    """

    def __init__(self, instance: BotInstance, config_key: str):
        self.instance = instance
        self.config_key = config_key
        self.logger = logging.getLogger(f"nzt48.bot.{instance.value}")

        # Load config
        bot_cfg = cfg.get(f"bots.{config_key}", {})
        self.strategies = bot_cfg.get("strategies", [])
        self.max_risk_per_trade = bot_cfg.get("max_risk_per_trade", 0.0075)
        # Canonical confidence floor — see ThresholdRegistry (E-01)
        self.min_confidence = bot_cfg.get("min_confidence", 65)
        self.max_positions = bot_cfg.get("max_positions", 5)
        self.personality = bot_cfg.get("personality", "")
        self.profit_targets = bot_cfg.get("profit_target_1r", [2.0, 3.0])
        self.stop_width = bot_cfg.get("stop_width", "1.5x ATR")
        self.partial_profit = bot_cfg.get("partial_profit", [30, 30, 40])
        self.trail_trigger = bot_cfg.get("trail_trigger", "+1.0R")
        self.max_hold_days = bot_cfg.get("max_hold_days", 5)
        self.overnight_allowed = bot_cfg.get("overnight", False)
        self.daily_loss_limit = bot_cfg.get("daily_loss_limit", -0.02)
        self.capital_allocation = bot_cfg.get("capital_allocation", 0.33)

        # Runtime state
        self._lock = threading.Lock()
        self.active = False
        self.daily_pnl = 0.0
        self.open_positions: list[Position] = []
        self.drawdown_level = DrawdownLevel.GREEN
        self._halted = False
        self._halted_reason = ""

    @abstractmethod
    def get_active_regimes(self) -> list[RegimeState]:
        """Return the list of regimes where this bot is active."""
        ...

    def should_activate(self, regime: RegimeState) -> bool:
        """Check if this bot should be active in the current regime."""
        return regime in self.get_active_regimes()

    def activate(self, regime: RegimeState) -> None:
        """Activate the bot for trading."""
        with self._lock:
            if self.should_activate(regime):
                self.active = True
                self.logger.info("ACTIVATED — regime %s matches", regime.value)
            else:
                self.active = False
                self.logger.info("DORMANT — regime %s not in active set", regime.value)

    def halt(self, reason: str) -> None:
        """Halt the bot (from Overseer or daily loss)."""
        self._halted = True
        self._halted_reason = reason
        self.active = False
        self.logger.warning("HALTED: %s", reason)

    def resume(self) -> None:
        """Resume trading after halt."""
        with self._lock:
            self._halted = False
            self._halted_reason = ""
            self.active = True
        self.logger.info("RESUMED from halt")

    def is_halted(self) -> bool:
        return self._halted

    def can_take_signal(self, signal: Signal) -> tuple[bool, str]:
        """Check if this bot can accept a signal.
        Returns (can_take, reason)."""
        with self._lock:
            if not self.active:
                return False, "Bot not active"
            if self._halted:
                return False, f"Halted: {self._halted_reason}"
            if signal.confidence < self.min_confidence:
                return False, f"Confidence {signal.confidence} < min {self.min_confidence}"
            if len(self.open_positions) >= self.max_positions:
                return False, f"Max positions ({self.max_positions}) reached"
            # SCALP and SWING signals come from timeframe stacking layers,
            # not the S1-S14 strategy pool — allow them through any active bot
            if signal.strategy not in ("SCALP", "SWING") and signal.strategy not in self.strategies:
                return False, f"Strategy {signal.strategy} not assigned to this bot"
            if self.daily_pnl <= self.daily_loss_limit:
                return False, f"Daily loss limit hit ({self.daily_pnl:.2%})"
            return True, "OK"

    def apply_personality(self, signal: Signal, atr: float) -> Signal:
        """Apply bot personality to signal (stop width, targets, sizing).
        Each bot adjusts the signal to its trading style."""
        # Parse stop width multiplier
        try:
            mult = float(self.stop_width.replace("x ATR", "").replace("x", ""))
        except ValueError:
            mult = 1.5

        # Adjust stop based on personality
        if signal.direction == Direction.LONG:
            signal.stop = signal.entry - (atr * mult)
            signal.target_1r = signal.entry + abs(signal.entry - signal.stop) * self.profit_targets[0]
            if len(self.profit_targets) > 1:
                signal.target_2r = signal.entry + abs(signal.entry - signal.stop) * self.profit_targets[1]
        else:
            signal.stop = signal.entry + (atr * mult)
            signal.target_1r = signal.entry - abs(signal.entry - signal.stop) * self.profit_targets[0]
            if len(self.profit_targets) > 1:
                signal.target_2r = signal.entry - abs(signal.entry - signal.stop) * self.profit_targets[1]

        # Cap risk to bot's max
        signal.risk_pct = min(signal.risk_pct, self.max_risk_per_trade)
        signal.bot_instance = self.instance

        return signal

    def update_daily_pnl(self, pnl_pct: float) -> None:
        """Update daily P&L (accumulate) and check limits."""
        with self._lock:
            self.daily_pnl += pnl_pct
            if self.daily_pnl <= self.daily_loss_limit:
                self.halt(f"Daily loss limit hit: {self.daily_pnl:.2%}")

    def get_capital(self, total_equity: float) -> float:
        """Get allocated capital for this bot."""
        return total_equity * self.capital_allocation

    def get_status(self) -> dict:
        """Get current bot status for Telegram /bots command."""
        return {
            "instance": self.instance.value,
            "active": self.active,
            "halted": self._halted,
            "halted_reason": self._halted_reason,
            "positions": len(self.open_positions),
            "max_positions": self.max_positions,
            "daily_pnl": self.daily_pnl,
            "daily_loss_limit": self.daily_loss_limit,
            "strategies": self.strategies,
            "capital_allocation": self.capital_allocation,
            "drawdown_level": self.drawdown_level.value,
        }

    def reset_daily(self) -> None:
        """Reset daily state at start of session."""
        with self._lock:
            self.daily_pnl = 0.0
            if self._halted and "Daily" in self._halted_reason:
                self.resume()
