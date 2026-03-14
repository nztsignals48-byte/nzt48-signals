"""
Day-of-Week Filter — NZT-48 Microstructure Module
French (1980): Monday Effect — stocks show negative/lower returns on Monday
due to bad news released over weekends and lower institutional activity.
Friday is strongest session for momentum continuation.

Applies confidence adjustments and hard veto logic per day.
"""

import json
import os
import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "data/dow_performance.json"

# Research priors: win rates by day (0=Mon, 1=Tue, ... 4=Fri)
# French (1980) + internal backtesting priors
RESEARCH_WIN_RATE_PRIOR = {
    0: 0.35,  # Monday — weakest
    1: 0.42,  # Tuesday
    2: 0.44,  # Wednesday
    3: 0.41,  # Thursday — expiry caution
    4: 0.47,  # Friday — strongest momentum continuation
}

# Confidence adjustments by day
DOW_CONFIDENCE_ADJUSTMENTS = {
    0: -5,  # Monday penalty
    1: 0,
    2: 0,
    3: 0,
    4: +3,  # Friday bonus
}

# Size multipliers by day
DOW_SIZE_MULTIPLIERS = {
    0: 0.80,  # Monday — smaller size
    1: 1.00,
    2: 1.00,
    3: 1.00,
    4: 1.00,
}

# Hard veto on Monday if RVOL < this threshold
MONDAY_HARD_VETO_RVOL = 1.5
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class DayOfWeekFilter:
    """
    Applies day-of-week effects to signal confidence and sizing.
    Tracks actual win rates per day to improve on research priors over time.
    """

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"trades_by_day": {str(i): {"wins": 0, "total": 0} for i in range(5)}}

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning(f"DayOfWeekFilter: save failed: {e}")

    def get_confidence_adjustment(self, check_date: Optional[date] = None) -> int:
        """Returns confidence delta for today's day of week."""
        d = check_date or date.today()
        dow = d.weekday()
        return DOW_CONFIDENCE_ADJUSTMENTS.get(dow, 0)

    def is_hard_veto(self, check_date: Optional[date] = None, rvol: Optional[float] = None) -> bool:
        """
        Hard veto on Monday if RVOL < 1.5.
        Logic: on the weakest day of week, only trade if there's confirmed
        institutional participation (high relative volume).
        """
        d = check_date or date.today()
        if d.weekday() != 0:  # Not Monday
            return False
        if rvol is None:
            return True  # Conservative: veto if no RVOL data on Monday
        return rvol < MONDAY_HARD_VETO_RVOL

    def get_size_multiplier(self, check_date: Optional[date] = None) -> float:
        """Returns position size multiplier for today's day of week."""
        d = check_date or date.today()
        return DOW_SIZE_MULTIPLIERS.get(d.weekday(), 1.0)

    def record_trade(self, win: bool, trade_date: Optional[date] = None):
        """Record a trade outcome to track actual win rates per day."""
        d = trade_date or date.today()
        dow = str(d.weekday())
        if dow not in self.state["trades_by_day"]:
            self.state["trades_by_day"][dow] = {"wins": 0, "total": 0}
        self.state["trades_by_day"][dow]["total"] += 1
        if win:
            self.state["trades_by_day"][dow]["wins"] += 1
        self._save_state()

    def get_win_rate_by_day(self) -> dict:
        """
        Returns actual win rates by day. Falls back to research prior
        if fewer than 10 trades on that day.
        """
        result = {}
        for dow in range(5):
            data = self.state["trades_by_day"].get(str(dow), {"wins": 0, "total": 0})
            total = data["total"]
            if total >= 10:
                result[DAY_NAMES[dow]] = round(data["wins"] / total, 3)
            else:
                result[DAY_NAMES[dow]] = RESEARCH_WIN_RATE_PRIOR[dow]
        return result

    def get_telegram_note(self, check_date: Optional[date] = None, rvol: Optional[float] = None) -> str:
        d = check_date or date.today()
        dow = d.weekday()
        day_name = DAY_NAMES[dow]
        adj = self.get_confidence_adjustment(d)
        size = self.get_size_multiplier(d)
        veto = self.is_hard_veto(d, rvol)
        wr = self.get_win_rate_by_day()

        lines = [f"📅 Day-of-Week Filter — {day_name}"]
        lines.append(f"  Conf adjustment: {adj:+d}  |  Size multiplier: {size:.2f}x")
        lines.append(f"  Win rate prior: {wr.get(day_name, 0)*100:.0f}%")
        if veto:
            lines.append(f"  🚫 HARD VETO — Monday RVOL {'N/A' if rvol is None else f'{rvol:.2f}'} < {MONDAY_HARD_VETO_RVOL}")
        return "\n".join(lines)
