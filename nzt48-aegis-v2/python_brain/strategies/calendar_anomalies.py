"""Calendar Anomalies & Temporal Alpha — Book 171.

Exploits well-documented calendar effects in equity markets:
1. Turn-of-Month (TOM): Days -1 to +3 around month-end (+50-80bps)
2. Holiday effect: Day before market holidays (+20-40bps)
3. Day-of-week: Monday weakness, Tuesday-Wednesday strength
4. Intraday patterns: First 30min momentum, last 30min rebalancing
5. Month-end rebalancing: Pension fund flows T-3 to T-1
6. Triple/Quad witching: Options expiry volatility

These are NOT alpha in isolation — they're confidence MODIFIERS that
boost or penalize existing signals based on temporal context.

Usage:
    from python_brain.strategies.calendar_anomalies import (
        CalendarModifier, get_calendar_adjustment,
    )

    adj = get_calendar_adjustment(
        year=2026, month=3, day=29, weekday=6,  # Saturday
        hour=14, minute=30,
    )
    adjusted_confidence = base_confidence + adj.confidence_delta
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set

log = logging.getLogger("calendar_anomalies")


@dataclass
class CalendarAdjustment:
    """Calendar-based signal adjustment."""
    confidence_delta: int = 0      # Additive confidence adjustment
    kelly_multiplier: float = 1.0  # Multiplicative Kelly adjustment
    reason: str = ""
    effects: List[str] = None      # Active calendar effects

    def __post_init__(self):
        if self.effects is None:
            self.effects = []


# ---------------------------------------------------------------------------
# Calendar Effect Definitions
# ---------------------------------------------------------------------------

# Day-of-week effects (0=Monday, 4=Friday)
DOW_CONFIDENCE_DELTA: Dict[int, int] = {
    0: -3,   # Monday: historically weak
    1: +2,   # Tuesday: reversal from Monday
    2: +2,   # Wednesday: mid-week strength
    3: +1,   # Thursday: neutral-positive
    4: -2,   # Friday: pre-weekend caution
}

# Turn-of-month window (trading days from month-end)
# Days -1, 0, +1, +2, +3 are statistically positive
TOM_BOOST = 5  # Confidence boost during TOM window

# Holiday pre-market boost
HOLIDAY_PRE_BOOST = 4

# Known UK/US holidays (month, day) — extend as needed
UK_HOLIDAYS: Set[tuple] = {
    (1, 1), (12, 25), (12, 26),  # New Year, Christmas, Boxing Day
    # Easter and bank holidays vary by year — simplified
}

US_HOLIDAYS: Set[tuple] = {
    (1, 1), (1, 20), (2, 17), (5, 26), (6, 19),
    (7, 4), (9, 1), (11, 27), (12, 25),
}


# Intraday patterns (LSE time in seconds from midnight)
def _intraday_adjustment(london_time_secs: int) -> int:
    """Intraday confidence adjustment based on time of day."""
    # First 30min after open (08:00-08:30): momentum continuation
    if 28800 <= london_time_secs <= 30600:
        return +3

    # US open (14:30-15:00): high volatility, mixed signal
    if 52200 <= london_time_secs <= 54000:
        return -2

    # Last 30min (16:00-16:30): rebalancing flow
    if 57600 <= london_time_secs <= 59400:
        return +2

    # Lunch doldrums (12:00-13:00): low volume, noise
    if 43200 <= london_time_secs <= 46800:
        return -3

    return 0


def _is_turn_of_month(d: date) -> bool:
    """Check if date is within the turn-of-month window (-1 to +3 trading days)."""
    # Last day of month
    if d.month == 12:
        next_month_1st = date(d.year + 1, 1, 1)
    else:
        next_month_1st = date(d.year, d.month + 1, 1)
    last_day = next_month_1st - timedelta(days=1)

    # Check if within -1 to +3 of month boundary
    days_to_end = (last_day - d).days
    if days_to_end <= 1:  # Last day or day before
        return True

    # First 3 days of month
    if d.day <= 3:
        return True

    return False


def _is_pre_holiday(d: date) -> bool:
    """Check if tomorrow is a market holiday."""
    tomorrow = d + timedelta(days=1)
    md = (tomorrow.month, tomorrow.day)
    return md in UK_HOLIDAYS or md in US_HOLIDAYS


def _is_options_expiry(d: date) -> bool:
    """Check if today is monthly options expiry (3rd Friday of month)."""
    if d.weekday() != 4:  # Not Friday
        return False
    # 3rd Friday: day 15-21
    return 15 <= d.day <= 21


def get_calendar_adjustment(
    year: int,
    month: int,
    day: int,
    weekday: int,
    hour: int = 12,
    minute: int = 0,
    london_time_secs: int = 0,
) -> CalendarAdjustment:
    """Compute calendar-based adjustment for current date/time.

    Args:
        year, month, day: Date components
        weekday: 0=Monday, 6=Sunday
        hour, minute: Time components (UTC)
        london_time_secs: Seconds from midnight London time (preferred)

    Returns: CalendarAdjustment with confidence delta and effects list.
    """
    if weekday >= 5:  # Weekend — no trading
        return CalendarAdjustment(confidence_delta=-100, reason="weekend")

    d = date(year, month, day)
    effects: List[str] = []
    total_delta = 0
    kelly_mult = 1.0

    # Day-of-week
    dow_delta = DOW_CONFIDENCE_DELTA.get(weekday, 0)
    if dow_delta != 0:
        total_delta += dow_delta
        effects.append(f"dow_{['mon','tue','wed','thu','fri'][weekday]}({dow_delta:+d})")

    # Turn-of-month
    if _is_turn_of_month(d):
        total_delta += TOM_BOOST
        effects.append(f"turn_of_month(+{TOM_BOOST})")

    # Pre-holiday
    if _is_pre_holiday(d):
        total_delta += HOLIDAY_PRE_BOOST
        effects.append(f"pre_holiday(+{HOLIDAY_PRE_BOOST})")

    # Options expiry
    if _is_options_expiry(d):
        total_delta -= 2  # Higher vol, less predictable
        kelly_mult *= 0.85
        effects.append("options_expiry(-2, kelly*0.85)")

    # Intraday
    if london_time_secs > 0:
        intra_delta = _intraday_adjustment(london_time_secs)
        if intra_delta != 0:
            total_delta += intra_delta
            effects.append(f"intraday({intra_delta:+d})")

    return CalendarAdjustment(
        confidence_delta=total_delta,
        kelly_multiplier=kelly_mult,
        reason=", ".join(effects) if effects else "no_calendar_effects",
        effects=effects,
    )
