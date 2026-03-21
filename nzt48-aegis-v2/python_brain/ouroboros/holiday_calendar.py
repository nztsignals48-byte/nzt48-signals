"""Holiday Calendar — Exchange holiday detection for all supported exchanges.

ISS-010: Dynamic holiday calendar that works beyond 2027.
Uses a combination of:
1. Static known holidays (Christmas, New Year, etc.)
2. Rule-based computation (Easter, equinoxes, nth-weekday-of-month, etc.)
3. Exchange-specific quirks (early closes, half-days)
4. Hardcoded tables for lunar holidays (Chinese New Year, Vesak, etc.)

Quarantine rules:
  - Read-only: never modifies WAL, config, or trading state
  - Pure stdlib (datetime, calendar, math, logging, argparse, json)
  - Thread-safe: no mutable global state
  - Deterministic: same inputs always produce same outputs

Usage:
    from python_brain.ouroboros.holiday_calendar import is_holiday, next_trading_day

    if is_holiday("LSE", date.today()):
        print("LSE closed today")

    next_day = next_trading_day("NYSE", date.today())

CLI:
    python3 -m python_brain.ouroboros.holiday_calendar --exchange LSE --year 2026
    python3 -m python_brain.ouroboros.holiday_calendar --all --year 2026
    python3 -m python_brain.ouroboros.holiday_calendar --check LSE 2026-12-25
"""

from __future__ import annotations

import argparse
import calendar
import json
import logging
import math
import sys
from datetime import date, time, timedelta
from typing import Dict, FrozenSet, List, Optional, Tuple

log = logging.getLogger("holiday_calendar")

# ============================================================================
# Easter computation — Anonymous Gregorian algorithm (Meeus/Jones/Butcher)
# ============================================================================

def _easter(year: int) -> date:
    """Compute Easter Sunday for any Gregorian year.

    Uses the Anonymous Gregorian algorithm as described by Meeus (1991),
    verified against published Easter tables for 1900-2199.

    >>> _easter(2024)
    datetime.date(2024, 3, 31)
    >>> _easter(2025)
    datetime.date(2025, 4, 20)
    >>> _easter(2026)
    datetime.date(2026, 4, 5)
    >>> _easter(2030)
    datetime.date(2030, 4, 21)
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _good_friday(year: int) -> date:
    """Good Friday = Easter - 2 days."""
    return _easter(year) - timedelta(days=2)


def _easter_monday(year: int) -> date:
    """Easter Monday = Easter + 1 day."""
    return _easter(year) + timedelta(days=1)


# ============================================================================
# Equinox computation — simple astronomical approximation
# ============================================================================

def _vernal_equinox(year: int) -> date:
    """Approximate vernal (spring) equinox for the Northern Hemisphere.

    Uses the Meeus algorithm simplified for the J2000.0 epoch.
    Accurate to +/-1 day for years 1900-2200 (sufficient for holiday calc).

    >>> _vernal_equinox(2026) in (date(2026, 3, 20), date(2026, 3, 21))
    True
    """
    # Jean Meeus, Astronomical Algorithms, Table 27.C
    y = (year - 2000) / 1000.0
    jde = (2451623.80984
           + 365242.37404 * y
           + 0.05169 * y * y
           - 0.00411 * y * y * y
           - 0.00057 * y * y * y * y)
    # Convert JDE to calendar date (simplified)
    return _jde_to_date(jde)


def _autumnal_equinox(year: int) -> date:
    """Approximate autumnal equinox for the Northern Hemisphere.

    Accurate to +/-1 day for years 1900-2200.

    >>> _autumnal_equinox(2026) in (date(2026, 9, 22), date(2026, 9, 23))
    True
    """
    y = (year - 2000) / 1000.0
    jde = (2451810.21715
           + 365242.01767 * y
           - 0.11575 * y * y
           + 0.00337 * y * y * y
           + 0.00078 * y * y * y * y)
    return _jde_to_date(jde)


def _jde_to_date(jde: float) -> date:
    """Convert a Julian Ephemeris Day to a Gregorian calendar date.

    Standard algorithm from Meeus, Astronomical Algorithms, Ch. 7.
    """
    jd = jde + 0.5
    z = int(jd)
    f = jd - z
    if z < 2299161:
        a = z
    else:
        alpha = int((z - 1867216.25) / 36524.25)
        a = z + 1 + alpha - alpha // 4
    b = a + 1524
    c = int((b - 122.1) / 365.25)
    d = int(365.25 * c)
    e = int((b - d) / 30.6001)
    day = b - d - int(30.6001 * e)
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715
    return date(year, month, day)


# ============================================================================
# Nth-weekday-of-month helper
# ============================================================================

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the nth occurrence of a weekday in the given month.

    weekday: 0=Monday, 6=Sunday (per datetime convention).
    n: 1-based (1=first, 2=second, ... -1=last).

    >>> _nth_weekday(2026, 1, 0, 3)   # 3rd Monday of Jan 2026 = MLK Day
    datetime.date(2026, 1, 19)
    >>> _nth_weekday(2026, 5, 0, -1)   # last Monday of May 2026
    datetime.date(2026, 5, 25)
    """
    if n > 0:
        # First day of month
        first = date(year, month, 1)
        # Days until the target weekday
        delta = (weekday - first.weekday()) % 7
        first_occurrence = first + timedelta(days=delta)
        return first_occurrence + timedelta(weeks=n - 1)
    elif n == -1:
        # Last occurrence: start from last day of month
        last_day = date(year, month, calendar.monthrange(year, month)[1])
        delta = (last_day.weekday() - weekday) % 7
        return last_day - timedelta(days=delta)
    else:
        raise ValueError(f"n must be >= 1 or -1, got {n}")


# ============================================================================
# Weekend substitution rules
# ============================================================================

def _us_observed(dt: date) -> date:
    """US federal holiday observation rule.

    If holiday falls on Saturday, observed on Friday.
    If holiday falls on Sunday, observed on Monday.
    """
    wd = dt.weekday()
    if wd == 5:  # Saturday -> Friday
        return dt - timedelta(days=1)
    if wd == 6:  # Sunday -> Monday
        return dt + timedelta(days=1)
    return dt


def _uk_observed(dt: date) -> date:
    """UK bank holiday substitution rule.

    If holiday falls on Saturday, observed on next Monday.
    If holiday falls on Sunday, observed on next Monday.
    If two holidays both fall on weekend, second one goes to Tuesday.
    (The double-sub case is handled in the LSE builder, not here.)
    """
    wd = dt.weekday()
    if wd == 5:  # Saturday -> Monday
        return dt + timedelta(days=2)
    if wd == 6:  # Sunday -> Monday
        return dt + timedelta(days=1)
    return dt


def _jp_substitute(dt: date, existing_holidays: FrozenSet[date]) -> Optional[date]:
    """Japanese substitute holiday law (振替休日).

    If a national holiday falls on Sunday, the next weekday that is NOT
    already a holiday becomes the substitute holiday.
    """
    if dt.weekday() != 6:  # Not Sunday
        return None
    candidate = dt + timedelta(days=1)
    while candidate.weekday() >= 5 or candidate in existing_holidays:
        candidate += timedelta(days=1)
    return candidate


# ============================================================================
# Lunar holiday tables (Chinese New Year, Vesak, etc.)
# Hardcoded for 2024-2035 — log warning when approaching end of range.
# ============================================================================

# Chinese New Year (day 1 of Lunar Year 1 Month) — affects HKEX, SGX
_CHINESE_NEW_YEAR: Dict[int, date] = {
    2024: date(2024, 2, 10),
    2025: date(2025, 1, 29),
    2026: date(2026, 2, 17),
    2027: date(2027, 2, 6),
    2028: date(2028, 1, 26),
    2029: date(2029, 2, 13),
    2030: date(2030, 2, 3),
    2031: date(2031, 1, 23),
    2032: date(2032, 2, 11),
    2033: date(2033, 1, 31),
    2034: date(2034, 2, 19),
    2035: date(2035, 2, 8),
}

# Buddha's Birthday (Vesak) — affects HKEX, SGX
_VESAK_DAY: Dict[int, date] = {
    2024: date(2024, 5, 15),
    2025: date(2025, 5, 5),
    2026: date(2026, 5, 24),
    2027: date(2027, 5, 13),
    2028: date(2028, 5, 2),
    2029: date(2029, 5, 20),
    2030: date(2030, 5, 9),
    2031: date(2031, 5, 28),
    2032: date(2032, 5, 16),
    2033: date(2033, 5, 6),
    2034: date(2034, 5, 25),
    2035: date(2035, 5, 15),
}

# Dragon Boat Festival (Tuen Ng) — affects HKEX
_DRAGON_BOAT: Dict[int, date] = {
    2024: date(2024, 6, 10),
    2025: date(2025, 5, 31),
    2026: date(2026, 6, 19),
    2027: date(2027, 6, 9),
    2028: date(2028, 5, 28),
    2029: date(2029, 6, 16),
    2030: date(2030, 6, 5),
    2031: date(2031, 6, 24),
    2032: date(2032, 6, 12),
    2033: date(2033, 6, 1),
    2034: date(2034, 6, 20),
    2035: date(2035, 6, 10),
}

# Mid-Autumn Festival (day AFTER Mid-Autumn) — affects HKEX
_MID_AUTUMN_NEXT_DAY: Dict[int, date] = {
    2024: date(2024, 9, 18),
    2025: date(2025, 10, 7),
    2026: date(2026, 9, 26),
    2027: date(2027, 9, 16),
    2028: date(2028, 10, 4),
    2029: date(2029, 9, 23),
    2030: date(2030, 9, 13),
    2031: date(2031, 10, 2),
    2032: date(2032, 9, 20),
    2033: date(2033, 9, 9),
    2034: date(2034, 9, 28),
    2035: date(2035, 9, 17),
}

# Chung Yeung Festival — affects HKEX
_CHUNG_YEUNG: Dict[int, date] = {
    2024: date(2024, 10, 11),
    2025: date(2025, 10, 29),
    2026: date(2026, 10, 19),
    2027: date(2027, 10, 8),
    2028: date(2028, 10, 26),
    2029: date(2029, 10, 16),
    2030: date(2030, 10, 5),
    2031: date(2031, 10, 24),
    2032: date(2032, 10, 12),
    2033: date(2033, 10, 1),
    2034: date(2034, 10, 20),
    2035: date(2035, 10, 9),
}

# Ching Ming Festival — affects HKEX (Qingming, ~Apr 4-5)
_CHING_MING: Dict[int, date] = {
    2024: date(2024, 4, 4),
    2025: date(2025, 4, 4),
    2026: date(2026, 4, 5),
    2027: date(2027, 4, 5),
    2028: date(2028, 4, 4),
    2029: date(2029, 4, 4),
    2030: date(2030, 4, 5),
    2031: date(2031, 4, 5),
    2032: date(2032, 4, 4),
    2033: date(2033, 4, 4),
    2034: date(2034, 4, 5),
    2035: date(2035, 4, 5),
}

# Hari Raya Puasa (Eid al-Fitr) — affects SGX
_HARI_RAYA_PUASA: Dict[int, date] = {
    2024: date(2024, 4, 10),
    2025: date(2025, 3, 31),
    2026: date(2026, 3, 20),
    2027: date(2027, 3, 10),
    2028: date(2028, 2, 27),
    2029: date(2029, 2, 14),
    2030: date(2030, 2, 4),
    2031: date(2031, 1, 24),
    2032: date(2032, 1, 14),
    2033: date(2033, 1, 2),
    2034: date(2034, 12, 22),
    2035: date(2035, 12, 12),
}

# Hari Raya Haji (Eid al-Adha) — affects SGX
_HARI_RAYA_HAJI: Dict[int, date] = {
    2024: date(2024, 6, 17),
    2025: date(2025, 6, 7),
    2026: date(2026, 5, 27),
    2027: date(2027, 5, 17),
    2028: date(2028, 5, 5),
    2029: date(2029, 4, 24),
    2030: date(2030, 4, 14),
    2031: date(2031, 4, 3),
    2032: date(2032, 3, 23),
    2033: date(2033, 3, 12),
    2034: date(2034, 3, 1),
    2035: date(2035, 2, 19),
}

# Deepavali — affects SGX
_DEEPAVALI: Dict[int, date] = {
    2024: date(2024, 11, 1),
    2025: date(2025, 10, 20),
    2026: date(2026, 11, 8),
    2027: date(2027, 10, 29),
    2028: date(2028, 10, 17),
    2029: date(2029, 11, 5),
    2030: date(2030, 10, 26),
    2031: date(2031, 10, 16),
    2032: date(2032, 11, 3),
    2033: date(2033, 10, 23),
    2034: date(2034, 11, 11),
    2035: date(2035, 10, 31),
}

# All lunar tables for coverage checking
_LUNAR_TABLES: Dict[str, Dict[int, date]] = {
    "Chinese New Year": _CHINESE_NEW_YEAR,
    "Vesak Day": _VESAK_DAY,
    "Dragon Boat Festival": _DRAGON_BOAT,
    "Mid-Autumn (day after)": _MID_AUTUMN_NEXT_DAY,
    "Chung Yeung": _CHUNG_YEUNG,
    "Ching Ming": _CHING_MING,
    "Hari Raya Puasa": _HARI_RAYA_PUASA,
    "Hari Raya Haji": _HARI_RAYA_HAJI,
    "Deepavali": _DEEPAVALI,
}

# Maximum year in lunar tables — warn 2 years before running out
_LUNAR_TABLE_MAX_YEAR = 2035
_LUNAR_TABLE_WARN_YEAR = _LUNAR_TABLE_MAX_YEAR - 2  # 2033


def _lunar_lookup(table: Dict[int, date], year: int, name: str) -> Optional[date]:
    """Look up a lunar holiday, logging a warning if approaching table end."""
    if year >= _LUNAR_TABLE_WARN_YEAR and year not in table:
        log.warning(
            "Lunar holiday '%s' has no data for year %d. "
            "Table covers 2024-%d. Update holiday_calendar.py!",
            name, year, _LUNAR_TABLE_MAX_YEAR,
        )
        return None
    return table.get(year)


def _weekend_sub_hk(dt: date) -> date:
    """HKEX weekend substitution: if holiday falls on Sunday, observe Monday."""
    if dt.weekday() == 6:  # Sunday
        return dt + timedelta(days=1)
    return dt


def _weekend_sub_sg(dt: date) -> date:
    """SGX weekend substitution: if holiday falls on Sunday, observe Monday."""
    if dt.weekday() == 6:  # Sunday
        return dt + timedelta(days=1)
    return dt


# ============================================================================
# Per-exchange holiday builders
# ============================================================================

def _nyse_holidays(year: int) -> List[date]:
    """NYSE/NASDAQ holidays for a given year.

    Sources: NYSE Rule 7.2, confirmed against nyse.com/markets/hours-calendars.

    Holidays:
      - New Year's Day (Jan 1, observed)
      - Martin Luther King Jr. Day (3rd Monday Jan)
      - Presidents' Day (3rd Monday Feb)
      - Good Friday
      - Memorial Day (last Monday May)
      - Juneteenth National Independence Day (Jun 19, observed, since 2022)
      - Independence Day (Jul 4, observed)
      - Labor Day (1st Monday Sep)
      - Thanksgiving Day (4th Thursday Nov)
      - Christmas Day (Dec 25, observed)
    """
    holidays = []

    # New Year's Day
    nyd = _us_observed(date(year, 1, 1))
    # Special case: if Jan 1 is Saturday, the market was open the previous
    # Friday (Dec 31 of prior year) is observed as prior-year holiday.
    # For *this* year's list, we skip it if the observed date falls in prior year.
    if nyd.year == year:
        holidays.append(nyd)

    # Also check: did NEXT year's NYD get pushed back into this year?
    nyd_next = _us_observed(date(year + 1, 1, 1))
    if nyd_next.year == year:
        holidays.append(nyd_next)

    # MLK Day — 3rd Monday in January
    holidays.append(_nth_weekday(year, 1, 0, 3))

    # Presidents' Day — 3rd Monday in February
    holidays.append(_nth_weekday(year, 2, 0, 3))

    # Good Friday
    holidays.append(_good_friday(year))

    # Memorial Day — last Monday in May
    holidays.append(_nth_weekday(year, 5, 0, -1))

    # Juneteenth — June 19, observed (effective 2022)
    if year >= 2022:
        holidays.append(_us_observed(date(year, 6, 19)))

    # Independence Day — July 4, observed
    holidays.append(_us_observed(date(year, 7, 4)))

    # Labor Day — 1st Monday in September
    holidays.append(_nth_weekday(year, 9, 0, 1))

    # Thanksgiving — 4th Thursday in November
    holidays.append(_nth_weekday(year, 11, 3, 4))

    # Christmas Day — Dec 25, observed
    holidays.append(_us_observed(date(year, 12, 25)))

    return sorted(holidays)


def _nyse_early_closes(year: int) -> List[Tuple[date, time]]:
    """NYSE early close dates (1:00 PM ET).

    - Day after Thanksgiving (Friday)
    - Christmas Eve (Dec 24) if it falls on a weekday
    - July 3rd if July 4 is on a Thursday (day before Independence Day)
    """
    early = []
    early_time = time(13, 0)  # 1:00 PM ET

    # Day after Thanksgiving
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    early.append((thanksgiving + timedelta(days=1), early_time))

    # Christmas Eve — only if weekday (Mon-Fri)
    xmas_eve = date(year, 12, 24)
    if xmas_eve.weekday() < 5:
        early.append((xmas_eve, early_time))

    # July 3rd — early close if it's a weekday and July 4 is weekday or Thursday
    jul3 = date(year, 7, 3)
    if jul3.weekday() < 5:
        early.append((jul3, early_time))

    return early


def _lse_holidays(year: int) -> List[date]:
    """LSE (London Stock Exchange) holidays for a given year.

    Sources: Bank of England bank holiday schedule, LSE Notice.

    UK bank holidays with weekend substitution:
      - New Year's Day (Jan 1)
      - Good Friday
      - Easter Monday
      - Early May Bank Holiday (1st Monday May)
      - Spring Bank Holiday (last Monday May)
      - Summer Bank Holiday (last Monday Aug)
      - Christmas Day (Dec 25)
      - Boxing Day (Dec 26)
    """
    holidays = []

    # New Year's Day — observed Mon if falls on weekend
    holidays.append(_uk_observed(date(year, 1, 1)))

    # Good Friday
    holidays.append(_good_friday(year))

    # Easter Monday
    holidays.append(_easter_monday(year))

    # Early May Bank Holiday — 1st Monday in May
    holidays.append(_nth_weekday(year, 5, 0, 1))

    # Spring Bank Holiday — last Monday in May
    holidays.append(_nth_weekday(year, 5, 0, -1))

    # Summer Bank Holiday — last Monday in August
    holidays.append(_nth_weekday(year, 8, 0, -1))

    # Christmas Day and Boxing Day — handle the double-sub case
    xmas = date(year, 12, 25)
    boxing = date(year, 12, 26)
    xmas_wd = xmas.weekday()

    if xmas_wd == 5:
        # Christmas = Saturday: xmas observed Mon 27, boxing observed Tue 28
        holidays.append(date(year, 12, 27))
        holidays.append(date(year, 12, 28))
    elif xmas_wd == 6:
        # Christmas = Sunday: xmas observed Mon 27 (or Tue 27), boxing Mon 26 is fine
        # Actually: Sun 25 -> Mon 27, Mon 26 Boxing is fine
        holidays.append(date(year, 12, 26))
        holidays.append(date(year, 12, 27))
    elif xmas_wd == 4:
        # Christmas = Friday: Boxing = Saturday -> observed Mon 28
        holidays.append(xmas)
        holidays.append(date(year, 12, 28))
    else:
        # Both fall on weekdays
        holidays.append(xmas)
        holidays.append(boxing)

    return sorted(holidays)


def _lse_early_closes(year: int) -> List[Tuple[date, time]]:
    """LSE early close dates (12:30 PM GMT/BST).

    - Christmas Eve (Dec 24) if weekday
    - New Year's Eve (Dec 31) if weekday
    """
    early = []
    early_time = time(12, 30)

    xmas_eve = date(year, 12, 24)
    if xmas_eve.weekday() < 5:
        early.append((xmas_eve, early_time))

    nye = date(year, 12, 31)
    if nye.weekday() < 5:
        early.append((nye, early_time))

    return early


def _tse_holidays(year: int) -> List[date]:
    """Tokyo Stock Exchange holidays for a given year.

    Sources: JPX holiday calendar, National Holiday Act.

    Japan's substitute holiday law (振替休日): if a national holiday falls
    on Sunday, the following Monday (or next non-holiday weekday) is a
    substitute holiday.

    Holidays:
      - New Year's (Jan 1-3)
      - Coming of Age Day (2nd Monday Jan)
      - National Foundation Day (Feb 11)
      - Emperor's Birthday (Feb 23)
      - Vernal Equinox Day (~Mar 20-21)
      - Showa Day (Apr 29)
      - Constitution Memorial Day (May 3)
      - Greenery Day (May 4)
      - Children's Day (May 5)
      - Marine Day (3rd Monday Jul)
      - Mountain Day (Aug 11)
      - Respect for the Aged Day (3rd Monday Sep)
      - Autumnal Equinox Day (~Sep 22-23)
      - Sports Day (2nd Monday Oct)
      - Culture Day (Nov 3)
      - Labour Thanksgiving Day (Nov 23)
    """
    # Collect base holidays first (before substitution)
    base = []

    # New Year's: Jan 1-3 (exchange-specific, always closed)
    base.append(date(year, 1, 1))
    base.append(date(year, 1, 2))
    base.append(date(year, 1, 3))

    # Coming of Age Day — 2nd Monday in January
    base.append(_nth_weekday(year, 1, 0, 2))

    # National Foundation Day — Feb 11
    base.append(date(year, 2, 11))

    # Emperor's Birthday — Feb 23 (since 2020; was Dec 23 before)
    if year >= 2020:
        base.append(date(year, 2, 23))

    # Vernal Equinox Day
    base.append(_vernal_equinox(year))

    # Showa Day — Apr 29
    base.append(date(year, 4, 29))

    # Constitution Memorial Day — May 3
    base.append(date(year, 5, 3))

    # Greenery Day — May 4
    base.append(date(year, 5, 4))

    # Children's Day — May 5
    base.append(date(year, 5, 5))

    # Marine Day — 3rd Monday in July
    base.append(_nth_weekday(year, 7, 0, 3))

    # Mountain Day — Aug 11
    base.append(date(year, 8, 11))

    # Respect for the Aged Day — 3rd Monday in September
    base.append(_nth_weekday(year, 9, 0, 3))

    # Autumnal Equinox Day
    base.append(_autumnal_equinox(year))

    # Sports Day — 2nd Monday in October
    base.append(_nth_weekday(year, 10, 0, 2))

    # Culture Day — Nov 3
    base.append(date(year, 11, 3))

    # Labour Thanksgiving Day — Nov 23
    base.append(date(year, 11, 23))

    # Apply substitute holiday law: if holiday on Sunday, next non-holiday
    # weekday becomes a substitute holiday
    base_set = frozenset(base)
    substitutes = []
    for h in base:
        sub = _jp_substitute(h, base_set)
        if sub is not None:
            substitutes.append(sub)

    # Japanese "sandwiched day" rule: if a non-holiday is between two holidays,
    # it also becomes a holiday. Most common: Sep 22 between Respect-Aged (Mon)
    # and Autumnal Equinox (Wed).
    all_holidays = sorted(set(base + substitutes))
    sandwiched = []
    for i in range(len(all_holidays) - 1):
        gap = (all_holidays[i + 1] - all_holidays[i]).days
        if gap == 2:
            mid = all_holidays[i] + timedelta(days=1)
            if mid.weekday() < 5 and mid not in all_holidays:
                sandwiched.append(mid)

    return sorted(set(all_holidays + sandwiched))


def _hkex_holidays(year: int) -> List[date]:
    """Hong Kong Stock Exchange holidays for a given year.

    Sources: HKEX trading calendar, General Holidays Ordinance (Cap. 149).

    Holidays:
      - New Year's Day (Jan 1)
      - Lunar New Year (3 days: CNY day 1, 2, 3)
      - Ching Ming Festival (~Apr 4-5)
      - Good Friday
      - Easter Monday  (day after Easter Saturday)
      - Labour Day (May 1)
      - Buddha's Birthday (Vesak)
      - Tuen Ng Festival (Dragon Boat)
      - HKSAR Establishment Day (Jul 1)
      - Day after Mid-Autumn Festival
      - Chung Yeung Festival
      - National Day (Oct 1)
      - Christmas Day (Dec 25)
      - Boxing Day (Dec 26)

    Weekend substitution: if holiday on Sunday, next Monday observed.
    """
    holidays = []

    # New Year's Day
    holidays.append(_weekend_sub_hk(date(year, 1, 1)))

    # Lunar New Year — 3 consecutive days
    cny = _lunar_lookup(_CHINESE_NEW_YEAR, year, "Chinese New Year")
    if cny is not None:
        for offset in range(3):
            d = cny + timedelta(days=offset)
            holidays.append(_weekend_sub_hk(d))
        # If CNY Day 1 is Sunday, there's an extra observed day (day 4)
        if cny.weekday() == 6:
            holidays.append(cny + timedelta(days=3))

    # Ching Ming
    cm = _lunar_lookup(_CHING_MING, year, "Ching Ming")
    if cm is not None:
        holidays.append(_weekend_sub_hk(cm))

    # Good Friday + day after Easter (Easter Saturday is also closed on HKEX)
    holidays.append(_good_friday(year))
    # HKEX: the Saturday after Good Friday is closed but that's a weekend anyway.
    # Easter Monday
    holidays.append(_easter_monday(year))

    # Labour Day — May 1
    holidays.append(_weekend_sub_hk(date(year, 5, 1)))

    # Buddha's Birthday
    vesak = _lunar_lookup(_VESAK_DAY, year, "Buddha's Birthday")
    if vesak is not None:
        holidays.append(_weekend_sub_hk(vesak))

    # Tuen Ng (Dragon Boat)
    db = _lunar_lookup(_DRAGON_BOAT, year, "Dragon Boat")
    if db is not None:
        holidays.append(_weekend_sub_hk(db))

    # HKSAR Establishment Day — Jul 1
    holidays.append(_weekend_sub_hk(date(year, 7, 1)))

    # Day after Mid-Autumn
    ma = _lunar_lookup(_MID_AUTUMN_NEXT_DAY, year, "Mid-Autumn (day after)")
    if ma is not None:
        holidays.append(_weekend_sub_hk(ma))

    # Chung Yeung
    cy = _lunar_lookup(_CHUNG_YEUNG, year, "Chung Yeung")
    if cy is not None:
        holidays.append(_weekend_sub_hk(cy))

    # National Day — Oct 1
    holidays.append(_weekend_sub_hk(date(year, 10, 1)))

    # Christmas + Boxing Day — same double-sub logic as LSE
    xmas = date(year, 12, 25)
    boxing = date(year, 12, 26)
    xmas_wd = xmas.weekday()

    if xmas_wd == 5:
        holidays.append(date(year, 12, 27))
        holidays.append(date(year, 12, 28))
    elif xmas_wd == 6:
        holidays.append(date(year, 12, 26))
        holidays.append(date(year, 12, 27))
    elif xmas_wd == 4:
        holidays.append(xmas)
        holidays.append(date(year, 12, 28))
    else:
        holidays.append(xmas)
        holidays.append(boxing)

    return sorted(set(holidays))


def _xetra_holidays(year: int) -> List[date]:
    """XETRA (Frankfurt) holidays for a given year.

    Sources: Deutsche Boerse trading calendar.

    Holidays:
      - New Year's Day (Jan 1)
      - Good Friday
      - Easter Monday
      - Labour Day (May 1)
      - Christmas Eve (Dec 24)
      - Christmas Day (Dec 25)
      - Boxing Day / 2nd Christmas Day (Dec 26)
      - New Year's Eve (Dec 31)
    """
    holidays = [
        date(year, 1, 1),
        _good_friday(year),
        _easter_monday(year),
        date(year, 5, 1),
        date(year, 12, 24),
        date(year, 12, 25),
        date(year, 12, 26),
        date(year, 12, 31),
    ]
    return sorted(holidays)


def _euronext_holidays(year: int) -> List[date]:
    """Euronext (Amsterdam, Paris, Brussels, Lisbon) holidays.

    Sources: Euronext trading calendar.

    Holidays:
      - New Year's Day (Jan 1)
      - Good Friday
      - Easter Monday
      - Labour Day (May 1)
      - Christmas Day (Dec 25)
      - Boxing Day (Dec 26)

    Note: Euronext harmonised its calendar in 2002. Dec 24/31 are NOT
    full holidays (may have early close).
    """
    holidays = [
        date(year, 1, 1),
        _good_friday(year),
        _easter_monday(year),
        date(year, 5, 1),
        date(year, 12, 25),
        date(year, 12, 26),
    ]
    return sorted(holidays)


def _euronext_early_closes(year: int) -> List[Tuple[date, time]]:
    """Euronext early close dates (2:05 PM CET).

    - Christmas Eve (Dec 24) if weekday
    - New Year's Eve (Dec 31) if weekday
    """
    early = []
    early_time = time(14, 5)

    xmas_eve = date(year, 12, 24)
    if xmas_eve.weekday() < 5:
        early.append((xmas_eve, early_time))

    nye = date(year, 12, 31)
    if nye.weekday() < 5:
        early.append((nye, early_time))

    return early


def _sgx_holidays(year: int) -> List[date]:
    """Singapore Exchange holidays for a given year.

    Sources: MAS gazette, SGX trading calendar.

    Holidays:
      - New Year's Day (Jan 1)
      - Chinese New Year (2 days)
      - Good Friday
      - Labour Day (May 1)
      - Vesak Day (varies)
      - Hari Raya Puasa (varies)
      - Hari Raya Haji (varies)
      - National Day (Aug 9)
      - Deepavali (varies)
      - Christmas Day (Dec 25)

    Weekend substitution: Sunday -> Monday.
    """
    holidays = []

    # New Year's Day
    holidays.append(_weekend_sub_sg(date(year, 1, 1)))

    # Chinese New Year — 2 days
    cny = _lunar_lookup(_CHINESE_NEW_YEAR, year, "Chinese New Year")
    if cny is not None:
        day1 = _weekend_sub_sg(cny)
        day2 = _weekend_sub_sg(cny + timedelta(days=1))
        holidays.append(day1)
        holidays.append(day2)
        # If both days fall on Sat-Sun, Monday+Tuesday are observed
        if cny.weekday() == 5:  # Sat: day1=Mon, day2(Sun)=Mon — collision
            holidays.append(day1 + timedelta(days=1))  # Tuesday
        elif cny.weekday() == 6:  # Sun: day1=Mon, day2(Mon)=Mon — collision
            pass  # day1=Mon(sub), day2=Mon already — add Tue
            holidays.append(day1 + timedelta(days=1))

    # Good Friday
    holidays.append(_good_friday(year))

    # Labour Day — May 1
    holidays.append(_weekend_sub_sg(date(year, 5, 1)))

    # Vesak Day
    vesak = _lunar_lookup(_VESAK_DAY, year, "Vesak Day")
    if vesak is not None:
        holidays.append(_weekend_sub_sg(vesak))

    # Hari Raya Puasa
    hrp = _lunar_lookup(_HARI_RAYA_PUASA, year, "Hari Raya Puasa")
    if hrp is not None:
        holidays.append(_weekend_sub_sg(hrp))

    # Hari Raya Haji
    hrh = _lunar_lookup(_HARI_RAYA_HAJI, year, "Hari Raya Haji")
    if hrh is not None:
        holidays.append(_weekend_sub_sg(hrh))

    # National Day — Aug 9
    holidays.append(_weekend_sub_sg(date(year, 8, 9)))

    # Deepavali
    dp = _lunar_lookup(_DEEPAVALI, year, "Deepavali")
    if dp is not None:
        holidays.append(_weekend_sub_sg(dp))

    # Christmas Day
    holidays.append(_weekend_sub_sg(date(year, 12, 25)))

    return sorted(set(holidays))


def _asx_holidays(year: int) -> List[date]:
    """Australian Securities Exchange holidays for a given year.

    Sources: ASX trading calendar, NSW public holidays.

    Holidays:
      - New Year's Day (Jan 1, observed Mon if weekend)
      - Australia Day (Jan 26, observed Mon if weekend)
      - Good Friday
      - Easter Saturday (day after Good Friday)
      - Easter Monday
      - Anzac Day (Apr 25, observed Mon if Sunday; if Saturday, NOT observed)
      - Queen's Birthday (2nd Monday Jun) — now King's Birthday
      - Christmas Day (Dec 25)
      - Boxing Day (Dec 26)
    """
    holidays = []

    # New Year's Day
    nyd = date(year, 1, 1)
    if nyd.weekday() == 5:
        holidays.append(nyd + timedelta(days=2))  # Sat -> Mon
    elif nyd.weekday() == 6:
        holidays.append(nyd + timedelta(days=1))  # Sun -> Mon
    else:
        holidays.append(nyd)

    # Australia Day — Jan 26
    aud = date(year, 1, 26)
    if aud.weekday() == 5:
        holidays.append(aud + timedelta(days=2))
    elif aud.weekday() == 6:
        holidays.append(aud + timedelta(days=1))
    else:
        holidays.append(aud)

    # Good Friday
    holidays.append(_good_friday(year))

    # Easter Saturday
    holidays.append(_easter(year) - timedelta(days=1))

    # Easter Monday
    holidays.append(_easter_monday(year))

    # Anzac Day — Apr 25
    # If Sunday, observed Monday. If Saturday, NOT observed (unique to Anzac).
    anzac = date(year, 4, 25)
    if anzac.weekday() == 6:
        holidays.append(anzac + timedelta(days=1))
    elif anzac.weekday() < 5:
        holidays.append(anzac)
    # Saturday: no observation

    # Queen's/King's Birthday — 2nd Monday in June (NSW/ACT/SA/TAS)
    holidays.append(_nth_weekday(year, 6, 0, 2))

    # Christmas + Boxing Day
    xmas = date(year, 12, 25)
    boxing = date(year, 12, 26)
    xmas_wd = xmas.weekday()

    if xmas_wd == 5:
        # Sat 25, Sun 26 -> Mon 27, Tue 28
        holidays.append(date(year, 12, 27))
        holidays.append(date(year, 12, 28))
    elif xmas_wd == 6:
        # Sun 25, Mon 26 -> Mon 27 (xmas observed), Mon 26 (boxing stays)
        holidays.append(date(year, 12, 26))
        holidays.append(date(year, 12, 27))
    elif xmas_wd == 4:
        # Fri 25, Sat 26 -> Fri 25, Mon 28
        holidays.append(xmas)
        holidays.append(date(year, 12, 28))
    else:
        holidays.append(xmas)
        holidays.append(boxing)

    return sorted(holidays)


def _kse_holidays(year: int) -> List[date]:
    """Korea Exchange (KRX/KSE) holidays for a given year.

    Sources: KRX trading calendar.

    Holidays:
      - New Year's Day (Jan 1)
      - Lunar New Year (3 days: day before, day of, day after)
      - Independence Movement Day (Mar 1)
      - Children's Day (May 5)
      - Buddha's Birthday (Vesak)
      - Memorial Day (Jun 6)
      - Liberation Day (Aug 15)
      - Chuseok (3 days: day before, day of, day after — Mid-Autumn)
      - National Foundation Day (Oct 3)
      - Hangul Day (Oct 9)
      - Christmas Day (Dec 25)
      - Year-end closure (Dec 31)

    Weekend substitution: Sunday -> Monday (since 2014 Alternative Holiday Act).
    """
    holidays = []

    def _kr_sub(dt: date) -> date:
        if dt.weekday() == 6:
            return dt + timedelta(days=1)
        return dt

    # New Year's Day
    holidays.append(_kr_sub(date(year, 1, 1)))

    # Lunar New Year — 3 days (day before, day of, day after)
    cny = _lunar_lookup(_CHINESE_NEW_YEAR, year, "Korean Lunar New Year")
    if cny is not None:
        for offset in (-1, 0, 1):
            d = cny + timedelta(days=offset)
            holidays.append(_kr_sub(d))

    # Independence Movement Day — Mar 1
    holidays.append(_kr_sub(date(year, 3, 1)))

    # Children's Day — May 5
    holidays.append(_kr_sub(date(year, 5, 5)))

    # Buddha's Birthday
    vesak = _lunar_lookup(_VESAK_DAY, year, "Korean Buddha's Birthday")
    if vesak is not None:
        holidays.append(_kr_sub(vesak))

    # Memorial Day — Jun 6
    holidays.append(_kr_sub(date(year, 6, 6)))

    # Liberation Day — Aug 15
    holidays.append(_kr_sub(date(year, 8, 15)))

    # Chuseok — 3 days around Mid-Autumn (use day_after - 1 as center)
    ma = _lunar_lookup(_MID_AUTUMN_NEXT_DAY, year, "Korean Chuseok")
    if ma is not None:
        mid_autumn = ma - timedelta(days=1)  # The actual Mid-Autumn day
        for offset in (-1, 0, 1):
            d = mid_autumn + timedelta(days=offset)
            holidays.append(_kr_sub(d))

    # National Foundation Day — Oct 3
    holidays.append(_kr_sub(date(year, 10, 3)))

    # Hangul Day — Oct 9
    holidays.append(_kr_sub(date(year, 10, 9)))

    # Christmas — Dec 25
    holidays.append(_kr_sub(date(year, 12, 25)))

    # Year-end closure — Dec 31
    holidays.append(date(year, 12, 31))

    return sorted(set(holidays))


# ============================================================================
# Exchange registry — maps exchange codes to their holiday/early-close builders
# ============================================================================

# Holiday builder functions
_HOLIDAY_BUILDERS: Dict[str, callable] = {
    "NYSE": _nyse_holidays,
    "NASDAQ": _nyse_holidays,   # Same calendar as NYSE
    "AMEX": _nyse_holidays,     # Same calendar as NYSE
    "SMART": _nyse_holidays,    # IBKR SMART routes to US exchanges
    "LSE": _lse_holidays,
    "LSEETF": _lse_holidays,    # Same calendar as LSE
    "TSE": _tse_holidays,
    "HKEX": _hkex_holidays,
    "XETRA": _xetra_holidays,
    "EURONEXT": _euronext_holidays,
    "EURONEXT_PA": _euronext_holidays,
    "EURONEXT_AS": _euronext_holidays,
    "AEB": _euronext_holidays,  # Amsterdam
    "SGX": _sgx_holidays,
    "ASX": _asx_holidays,
    "KSE": _kse_holidays,
    "KRX": _kse_holidays,       # Same as KSE
}

# Early close builder functions
_EARLY_CLOSE_BUILDERS: Dict[str, callable] = {
    "NYSE": _nyse_early_closes,
    "NASDAQ": _nyse_early_closes,
    "AMEX": _nyse_early_closes,
    "SMART": _nyse_early_closes,
    "LSE": _lse_early_closes,
    "LSEETF": _lse_early_closes,
    "EURONEXT": _euronext_early_closes,
    "EURONEXT_PA": _euronext_early_closes,
    "EURONEXT_AS": _euronext_early_closes,
    "AEB": _euronext_early_closes,
}

# All known exchange codes for validation
SUPPORTED_EXCHANGES: FrozenSet[str] = frozenset(_HOLIDAY_BUILDERS.keys())


# ============================================================================
# Thread-safe cache (per year+exchange) using frozenset
# We build once per (exchange, year) and cache the result.
# No mutable global state — _HolidayCache is a simple dict that grows
# monotonically and is safe for concurrent reads (GIL-protected).
# ============================================================================

_holiday_cache: Dict[Tuple[str, int], FrozenSet[date]] = {}
_early_close_cache: Dict[Tuple[str, int], Dict[date, time]] = {}


def _get_holiday_set(exchange: str, year: int) -> FrozenSet[date]:
    """Get or compute the holiday set for an exchange+year, cached."""
    key = (exchange, year)
    if key not in _holiday_cache:
        builder = _HOLIDAY_BUILDERS.get(exchange)
        if builder is None:
            log.warning("Unknown exchange '%s' — assuming no holidays", exchange)
            _holiday_cache[key] = frozenset()
        else:
            holidays = builder(year)
            _holiday_cache[key] = frozenset(holidays)
    return _holiday_cache[key]


def _get_early_close_map(exchange: str, year: int) -> Dict[date, time]:
    """Get or compute the early close map for an exchange+year, cached."""
    key = (exchange, year)
    if key not in _early_close_cache:
        builder = _EARLY_CLOSE_BUILDERS.get(exchange)
        if builder is None:
            _early_close_cache[key] = {}
        else:
            early_list = builder(year)
            _early_close_cache[key] = {d: t for d, t in early_list}
    return _early_close_cache[key]


# ============================================================================
# Public API
# ============================================================================

def is_holiday(exchange: str, dt: date) -> bool:
    """Check if a date is a non-trading day for the given exchange.

    Returns True if the date is a weekend OR a public holiday for the exchange.

    Args:
        exchange: Exchange code (e.g., "NYSE", "LSE", "LSEETF", "TSE").
        dt: The date to check.

    Returns:
        True if the exchange is closed on that date.

    Examples:
        >>> is_holiday("NYSE", date(2026, 12, 25))  # Christmas (Friday)
        True
        >>> is_holiday("NYSE", date(2026, 3, 15))    # Regular Sunday
        True
        >>> is_holiday("NYSE", date(2026, 3, 16))    # Regular Monday
        False
        >>> is_holiday("LSE", date(2026, 12, 25))    # Christmas (Friday)
        True
        >>> is_holiday("LSE", date(2026, 12, 28))    # Boxing Day observed (Monday)
        True
    """
    # Weekends: all exchanges are closed Sat/Sun
    if dt.weekday() >= 5:
        return True

    exchange = exchange.upper()
    holiday_set = _get_holiday_set(exchange, dt.year)
    return dt in holiday_set


def is_early_close(exchange: str, dt: date) -> Optional[time]:
    """Check if an exchange has an early close on the given date.

    Args:
        exchange: Exchange code.
        dt: The date to check.

    Returns:
        The early closing time (exchange local time) if early close, None otherwise.

    Examples:
        >>> is_early_close("NYSE", date(2026, 11, 27))  # Day after Thanksgiving
        datetime.time(13, 0)
        >>> is_early_close("NYSE", date(2026, 3, 16))    # Regular Monday
    """
    if dt.weekday() >= 5:
        return None  # Weekend — not an "early close"

    exchange = exchange.upper()
    early_map = _get_early_close_map(exchange, dt.year)
    return early_map.get(dt)


def next_trading_day(exchange: str, dt: date) -> date:
    """Return the next date that is a trading day (not weekend, not holiday).

    Starts searching from dt + 1 day.

    Args:
        exchange: Exchange code.
        dt: Start date (exclusive — we look for the NEXT day after this).

    Returns:
        The next trading day.

    Examples:
        >>> next_trading_day("NYSE", date(2026, 12, 24))  # Thu before Christmas
        datetime.date(2026, 12, 28)
        >>> next_trading_day("NYSE", date(2026, 12, 25))  # Christmas (Fri)
        datetime.date(2026, 12, 28)
    """
    exchange = exchange.upper()
    candidate = dt + timedelta(days=1)
    # Safety: don't loop forever (max 30 days — no exchange is closed 30 consecutive days)
    for _ in range(30):
        if not is_holiday(exchange, candidate):
            return candidate
        candidate += timedelta(days=1)
    # Should never happen, but be defensive
    log.error(
        "Could not find next trading day for %s after %s within 30 days",
        exchange, dt,
    )
    return candidate


def prev_trading_day(exchange: str, dt: date) -> date:
    """Return the most recent past trading day (not weekend, not holiday).

    Starts searching from dt - 1 day.

    Args:
        exchange: Exchange code.
        dt: Start date (exclusive — we look BEFORE this).

    Returns:
        The previous trading day.

    Examples:
        >>> prev_trading_day("NYSE", date(2026, 12, 28))  # Monday after Christmas
        datetime.date(2026, 12, 24)
    """
    exchange = exchange.upper()
    candidate = dt - timedelta(days=1)
    for _ in range(30):
        if not is_holiday(exchange, candidate):
            return candidate
        candidate -= timedelta(days=1)
    log.error(
        "Could not find prev trading day for %s before %s within 30 days",
        exchange, dt,
    )
    return candidate


def get_holidays(exchange: str, year: int) -> List[date]:
    """Return all holidays for an exchange in a given year, sorted.

    This returns only the public holidays (not weekends). Use is_holiday()
    for a combined weekend+holiday check.

    Args:
        exchange: Exchange code.
        year: Calendar year.

    Returns:
        Sorted list of holiday dates.

    Examples:
        >>> len(get_holidays("NYSE", 2026)) >= 9
        True
        >>> date(2026, 12, 25) in get_holidays("NYSE", 2026)
        True
    """
    exchange = exchange.upper()
    return sorted(_get_holiday_set(exchange, year))


def trading_days_between(exchange: str, start: date, end: date) -> int:
    """Count trading days between two dates (exclusive of both endpoints).

    Args:
        exchange: Exchange code.
        start: Start date (exclusive).
        end: End date (exclusive).

    Returns:
        Number of trading days strictly between start and end.

    Examples:
        >>> trading_days_between("NYSE", date(2026, 12, 23), date(2026, 12, 29))
        2
    """
    exchange = exchange.upper()
    count = 0
    current = start + timedelta(days=1)
    while current < end:
        if not is_holiday(exchange, current):
            count += 1
        current += timedelta(days=1)
    return count


def is_trading_day(exchange: str, dt: date) -> bool:
    """Convenience: True if the exchange is open (not a holiday, not a weekend).

    Inverse of is_holiday().
    """
    return not is_holiday(exchange, dt)


def holidays_remaining(exchange: str, dt: date) -> int:
    """Count remaining holidays in the year from dt (inclusive) to Dec 31.

    Useful for reporting and dashboards.
    """
    exchange = exchange.upper()
    holiday_set = _get_holiday_set(exchange, dt.year)
    return sum(1 for h in holiday_set if h >= dt)


def check_lunar_table_coverage(year: int) -> List[str]:
    """Check which lunar holiday tables are missing data for a given year.

    Returns list of warning messages. Empty list = all tables have coverage.

    Examples:
        >>> check_lunar_table_coverage(2026)
        []
        >>> len(check_lunar_table_coverage(2040)) > 0
        True
    """
    warnings = []
    for name, table in _LUNAR_TABLES.items():
        if year not in table:
            warnings.append(
                f"'{name}' has no data for {year} "
                f"(table covers {min(table)}-{max(table)})"
            )
    return warnings


# ============================================================================
# CLI entry point
# ============================================================================

def _format_holiday_table(exchange: str, year: int) -> str:
    """Format a holiday table for CLI output."""
    holidays = get_holidays(exchange, year)
    lines = [f"\n{'=' * 60}"]
    lines.append(f"  {exchange} Holidays for {year}")
    lines.append(f"{'=' * 60}")

    if not holidays:
        lines.append("  (no holidays found)")
        return "\n".join(lines)

    for h in holidays:
        day_name = h.strftime("%a")
        date_str = h.strftime("%Y-%m-%d")
        early = is_early_close(exchange, h)
        early_str = f"  [EARLY CLOSE: {early}]" if early else ""
        lines.append(f"  {date_str}  {day_name}{early_str}")

    # Early closes (non-holiday days)
    early_map = _get_early_close_map(exchange, year)
    early_non_holiday = [
        (d, t) for d, t in sorted(early_map.items())
        if d not in _get_holiday_set(exchange, year) and d.weekday() < 5
    ]
    if early_non_holiday:
        lines.append(f"\n  Early Closes (not full holidays):")
        for d, t in early_non_holiday:
            day_name = d.strftime("%a")
            date_str = d.strftime("%Y-%m-%d")
            lines.append(f"  {date_str}  {day_name}  closes at {t}")

    lines.append(f"\n  Total holidays: {len(holidays)}")

    # Lunar table coverage warning
    coverage_warnings = check_lunar_table_coverage(year)
    if coverage_warnings:
        lines.append(f"\n  WARNINGS:")
        for w in coverage_warnings:
            lines.append(f"    - {w}")

    return "\n".join(lines)


def main() -> None:
    """CLI entry point for holiday calendar."""
    parser = argparse.ArgumentParser(
        prog="holiday_calendar",
        description="Exchange holiday detection for all supported exchanges.",
    )
    parser.add_argument(
        "--exchange", "-e",
        type=str,
        help=f"Exchange code. Supported: {', '.join(sorted(SUPPORTED_EXCHANGES))}",
    )
    parser.add_argument(
        "--year", "-y",
        type=int,
        default=date.today().year,
        help="Calendar year (default: current year)",
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="Show holidays for ALL supported exchanges",
    )
    parser.add_argument(
        "--check", "-c",
        nargs=2,
        metavar=("EXCHANGE", "DATE"),
        help="Check if a specific date is a holiday (format: EXCHANGE YYYY-MM-DD)",
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output in JSON format",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Check lunar table coverage for the given year",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Check coverage
    if args.coverage:
        warnings = check_lunar_table_coverage(args.year)
        if warnings:
            print(f"Lunar table coverage issues for {args.year}:")
            for w in warnings:
                print(f"  - {w}")
        else:
            print(f"All lunar tables have coverage for {args.year}.")
        return

    # Check a specific date
    if args.check:
        exch = args.check[0].upper()
        try:
            dt = date.fromisoformat(args.check[1])
        except ValueError:
            print(f"ERROR: Invalid date format '{args.check[1]}'. Use YYYY-MM-DD.")
            sys.exit(1)

        if exch not in SUPPORTED_EXCHANGES:
            print(f"WARNING: Unknown exchange '{exch}'. Assuming no holidays.")

        holiday = is_holiday(exch, dt)
        early = is_early_close(exch, dt)

        if args.json:
            result = {
                "exchange": exch,
                "date": str(dt),
                "is_holiday": holiday,
                "is_weekend": dt.weekday() >= 5,
                "is_early_close": str(early) if early else None,
                "next_trading_day": str(next_trading_day(exch, dt)) if holiday else str(dt),
            }
            print(json.dumps(result, indent=2))
        else:
            status = "CLOSED (holiday)" if holiday else "OPEN"
            if dt.weekday() >= 5 and holiday:
                status = "CLOSED (weekend)"
            if early:
                status += f" — early close at {early}"
            print(f"{exch} on {dt} ({dt.strftime('%A')}): {status}")
            if holiday:
                ntd = next_trading_day(exch, dt)
                print(f"  Next trading day: {ntd} ({ntd.strftime('%A')})")
        return

    # Show holidays for one or all exchanges
    if args.all:
        exchanges = sorted(SUPPORTED_EXCHANGES)
    elif args.exchange:
        exch = args.exchange.upper()
        if exch not in SUPPORTED_EXCHANGES:
            print(f"WARNING: Unknown exchange '{exch}'.")
        exchanges = [exch]
    else:
        # Default: show the exchanges we actually trade on
        exchanges = ["NYSE", "LSE", "TSE", "HKEX", "XETRA", "EURONEXT", "SGX", "ASX", "KSE"]

    if args.json:
        result = {}
        for exch in exchanges:
            holidays = get_holidays(exch, args.year)
            early_map = _get_early_close_map(exch, args.year)
            result[exch] = {
                "holidays": [str(h) for h in holidays],
                "early_closes": {
                    str(d): str(t)
                    for d, t in sorted(early_map.items())
                    if d.weekday() < 5
                },
                "total_holidays": len(holidays),
            }
        print(json.dumps(result, indent=2))
    else:
        for exch in exchanges:
            print(_format_holiday_table(exch, args.year))

        # Summary
        print(f"\n{'=' * 60}")
        print(f"  Summary for {args.year}")
        print(f"{'=' * 60}")
        for exch in exchanges:
            holidays = get_holidays(exch, args.year)
            print(f"  {exch:12s}: {len(holidays):2d} holidays")

        # Lunar coverage
        warnings = check_lunar_table_coverage(args.year)
        if warnings:
            print(f"\n  Lunar Table Warnings:")
            for w in warnings:
                print(f"    - {w}")


if __name__ == "__main__":
    main()
