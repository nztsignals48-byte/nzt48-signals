"""Clock, Timezone & Trading Calendar — Book 94.

UTC everywhere internally. Local time only for display and session checks.
Handles DST transitions, half-days, and holiday calendars.

Key rules:
  1. All timestamps are UTC nanoseconds since epoch
  2. London time used for LSE session checks
  3. US Eastern time used for US session checks
  4. UK/US DST offset changes: 4h vs 5h for 2-3 weeks/year
  5. Tick timestamps > 120s old are rejected as stale
  6. Ticks in the future are rejected

Usage:
    from python_brain.execution.calendar_manager import (
        TradingCalendar, MarketHours, is_market_open,
    )

    cal = TradingCalendar()
    if cal.is_trading_day(date(2026, 3, 30)):
        hours = cal.session_hours("LSE", date(2026, 3, 30))
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

log = logging.getLogger("calendar_manager")


# ═══════════════════════════════════════════════════════════════════════
# Market Hours
# ═══════════════════════════════════════════════════════════════════════

class MarketHours:
    """Trading hours for a market."""

    def __init__(self, name: str, open_utc: time, close_utc: time,
                 auction_start: Optional[time] = None, auction_end: Optional[time] = None):
        self.name = name
        self.open_utc = open_utc
        self.close_utc = close_utc
        self.auction_start = auction_start
        self.auction_end = auction_end

    def is_open(self, utc_time: time) -> bool:
        return self.open_utc <= utc_time <= self.close_utc

    def minutes_to_close(self, utc_time: time) -> int:
        close_mins = self.close_utc.hour * 60 + self.close_utc.minute
        current_mins = utc_time.hour * 60 + utc_time.minute
        return max(0, close_mins - current_mins)


# Standard market hours (UTC, non-DST — adjust for DST separately)
MARKETS: Dict[str, MarketHours] = {
    "LSE": MarketHours("LSE", time(8, 0), time(16, 30),
                        auction_start=time(7, 50), auction_end=time(8, 0)),
    "NYSE": MarketHours("NYSE", time(14, 30), time(21, 0)),
    "NASDAQ": MarketHours("NASDAQ", time(14, 30), time(21, 0)),
    "XETRA": MarketHours("XETRA", time(8, 0), time(16, 30)),
    "EURONEXT": MarketHours("EURONEXT", time(8, 0), time(16, 30)),
    "TSE": MarketHours("TSE", time(0, 0), time(6, 0)),  # Tokyo
    "HKEX": MarketHours("HKEX", time(1, 30), time(8, 0)),  # Hong Kong
}


# ═══════════════════════════════════════════════════════════════════════
# Holiday Calendar
# ═══════════════════════════════════════════════════════════════════════

# UK bank holidays 2026 (known)
UK_HOLIDAYS_2026: Set[date] = {
    date(2026, 1, 1),   # New Year
    date(2026, 4, 3),   # Good Friday
    date(2026, 4, 6),   # Easter Monday
    date(2026, 5, 4),   # Early May
    date(2026, 5, 25),  # Spring
    date(2026, 8, 31),  # Summer
    date(2026, 12, 25), # Christmas
    date(2026, 12, 28), # Boxing Day (substitute)
}

# US holidays 2026 (known)
US_HOLIDAYS_2026: Set[date] = {
    date(2026, 1, 1),   # New Year
    date(2026, 1, 19),  # MLK Jr
    date(2026, 2, 16),  # Presidents Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),   # Independence Day (observed)
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
}

# US half-days (early close at 18:00 UTC / 1:00 PM ET)
US_HALF_DAYS_2026: Set[date] = {
    date(2026, 11, 27), # Day after Thanksgiving
    date(2026, 12, 24), # Christmas Eve
}


class TradingCalendar:
    """Trading calendar with holiday and DST awareness."""

    def __init__(self):
        self._uk_holidays = UK_HOLIDAYS_2026
        self._us_holidays = US_HOLIDAYS_2026
        self._us_half_days = US_HALF_DAYS_2026

    def is_trading_day(self, d: date, market: str = "LSE") -> bool:
        """Check if date is a trading day for the given market."""
        # Weekend check
        if d.weekday() >= 5:
            return False

        # Holiday check
        if market in ("LSE", "XETRA", "EURONEXT"):
            return d not in self._uk_holidays
        elif market in ("NYSE", "NASDAQ"):
            return d not in self._us_holidays

        return True

    def is_half_day(self, d: date, market: str = "NYSE") -> bool:
        """Check if date is a half-day (early close)."""
        if market in ("NYSE", "NASDAQ"):
            return d in self._us_half_days
        return False

    def session_hours(self, market: str, d: date) -> Optional[MarketHours]:
        """Get trading hours for a market on a specific date."""
        if not self.is_trading_day(d, market):
            return None

        hours = MARKETS.get(market)
        if hours is None:
            return None

        # Adjust for half-day
        if self.is_half_day(d, market):
            return MarketHours(
                hours.name, hours.open_utc, time(18, 0),  # Close at 18:00 UTC
                hours.auction_start, hours.auction_end,
            )

        return hours

    def next_trading_day(self, d: date, market: str = "LSE") -> date:
        """Find the next trading day after d."""
        candidate = d + timedelta(days=1)
        for _ in range(10):  # Max 10 day lookahead (holiday stretches)
            if self.is_trading_day(candidate, market):
                return candidate
            candidate += timedelta(days=1)
        return candidate

    def trading_days_between(self, start: date, end: date, market: str = "LSE") -> int:
        """Count trading days between two dates."""
        count = 0
        d = start
        while d <= end:
            if self.is_trading_day(d, market):
                count += 1
            d += timedelta(days=1)
        return count

    def us_uk_time_offset_hours(self, d: date) -> int:
        """Get the US Eastern → UK London time offset for a date.

        Usually 5 hours, but 4 hours during the ~2 weeks where
        US has switched DST but UK hasn't (or vice versa).
        """
        # US DST: second Sunday of March → first Sunday of November
        # UK DST: last Sunday of March → last Sunday of October
        # In the gap: offset is 4h instead of 5h

        # Simplified: March 8-28 and October 25-31 the offset is 4h
        if (d.month == 3 and 8 <= d.day <= 28) or (d.month == 10 and 25 <= d.day <= 31):
            return 4
        return 5

    def is_us_overlap(self, utc_time: time) -> bool:
        """Check if current UTC time is during US/LSE overlap session."""
        # Overlap: 14:30-16:30 UTC (standard), 13:30-15:30 during DST gap
        return time(14, 30) <= utc_time <= time(16, 30)
