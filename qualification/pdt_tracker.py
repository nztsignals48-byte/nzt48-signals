"""
NZT-48 Trading System — Pattern Day Trader (PDT) Tracker
Section X, Point 8: 4-mode system for Bot B accounts under $25k.

US accounts < $25k face the PDT rule: max 3 day trades per 5 rolling days.
This tracker manages 4 modes that automatically adjust trading frequency:

SELECTIVE → CONSERVATIVE → RESERVE → SWING

PDT does NOT apply to Bot A (ISA/UK platform).
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import PDTMode

logger = logging.getLogger("nzt48.pdt")


class PDTTracker:
    """Pattern Day Trader compliance tracker for Bot B.

    Manages day trade count over rolling 5-day windows.
    Automatically switches between 4 modes based on remaining
    day trade capacity. PDT does NOT apply to Bot A (UK ISA).
    """

    PDT_THRESHOLD = 25_000  # USD account value below which PDT applies
    MAX_DAY_TRADES_5DAY = 3

    def __init__(self, account_value: float = 20_000.0) -> None:
        self.account_value = account_value
        self._day_trades: list[datetime] = []  # Timestamps of day trades
        self._current_mode: PDTMode = PDTMode.SELECTIVE

    @property
    def pdt_applies(self) -> bool:
        """PDT rule only applies to accounts under $25k."""
        return self.account_value < self.PDT_THRESHOLD

    @property
    def current_mode(self) -> PDTMode:
        return self._current_mode

    @property
    def remaining_day_trades(self) -> int:
        """How many day trades are available in the current 5-day window."""
        if not self.pdt_applies:
            return 999  # No limit

        self._cleanup_old_trades()
        used = len(self._day_trades)
        return max(0, self.MAX_DAY_TRADES_5DAY - used)

    def record_day_trade(self, timestamp: Optional[datetime] = None) -> None:
        """Record a day trade (bought and sold same day)."""
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        self._day_trades.append(timestamp)
        self._update_mode()
        logger.info(
            "Day trade recorded. Used: %d/%d. Mode: %s",
            len(self._day_trades), self.MAX_DAY_TRADES_5DAY,
            self._current_mode.value,
        )

    def can_day_trade(self, confidence: float = 0.0) -> bool:
        """Check if a day trade is allowed given current PDT state.

        Different modes have different confidence requirements:
        SELECTIVE: 3 trades/week, best signals only
        CONSERVATIVE: 2/week, conf > 80
        RESERVE: 1/week, conf > 85
        SWING: Hold overnight (no PDT count), no conf restriction
        """
        if not self.pdt_applies:
            return True

        remaining = self.remaining_day_trades

        if remaining <= 0:
            # Must use SWING mode (hold overnight)
            return False

        if self._current_mode == PDTMode.SELECTIVE:
            return remaining > 0

        elif self._current_mode == PDTMode.CONSERVATIVE:
            return remaining > 0 and confidence >= 80

        elif self._current_mode == PDTMode.RESERVE:
            return remaining > 0 and confidence >= 85

        elif self._current_mode == PDTMode.SWING:
            return False  # SWING mode = hold overnight, don't use day trade

        return False

    def should_swing(self) -> bool:
        """Whether the system should hold overnight to avoid using a day trade."""
        if not self.pdt_applies:
            return False
        return self.remaining_day_trades <= 0 or self._current_mode == PDTMode.SWING

    def _update_mode(self) -> None:
        """Auto-switch PDT mode based on remaining capacity."""
        remaining = self.remaining_day_trades

        if remaining >= 2:
            self._current_mode = PDTMode.SELECTIVE
        elif remaining == 1:
            self._current_mode = PDTMode.CONSERVATIVE
        elif remaining == 0:
            self._current_mode = PDTMode.SWING
        else:
            self._current_mode = PDTMode.SWING

        logger.debug("PDT mode: %s (remaining: %d)",
                     self._current_mode.value, remaining)

    def _cleanup_old_trades(self) -> None:
        """Remove day trades older than 5 rolling business days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)  # ~5 business days
        self._day_trades = [t for t in self._day_trades if t > cutoff]

    def update_account_value(self, value: float) -> None:
        """Update account value — PDT may no longer apply if over $25k."""
        old_applies = self.pdt_applies
        self.account_value = value
        if old_applies and not self.pdt_applies:
            logger.info("Account above $25k — PDT restrictions LIFTED")
        elif not old_applies and self.pdt_applies:
            logger.warning("Account below $25k — PDT restrictions ACTIVE")

    def get_status(self) -> dict:
        """Get full PDT status for display."""
        return {
            "pdt_applies": self.pdt_applies,
            "account_value": self.account_value,
            "mode": self._current_mode.value,
            "remaining_day_trades": self.remaining_day_trades,
            "used_day_trades": len(self._day_trades),
            "max_day_trades": self.MAX_DAY_TRADES_5DAY,
        }
