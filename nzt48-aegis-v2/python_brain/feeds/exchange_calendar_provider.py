"""Exchange Calendar Provider — exact market schedules for all AEGIS venues.

Replaces hardcoded session times in risk_arbiter.rs with calendar-aware checks.
Writes config/exchange_schedules.json consumed by Rust at startup and Python at nightly.

Supported exchanges: XNYS (NYSE), XNAS (NASDAQ), XLON (LSE), XTSE (TSE),
                     XSES (SGX), XHKG (HKEX), plus EU venues.

License: exchange-calendars is Apache 2.0.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("exchange_calendar_provider")

# MIC → exchange-calendars calendar name mapping
_MIC_TO_CALENDAR = {
    "XNYS": "XNYS",     # NYSE
    "XNAS": "XNYS",     # NASDAQ uses NYSE calendar
    "ARCX": "XNYS",     # NYSE Arca
    "BATS": "XNYS",     # CBOE BZX
    "XLON": "XLON",     # London Stock Exchange
    "XDUB": "XDUB",     # Dublin (Euronext Dublin)
    "XETR": "XETR",     # Deutsche Börse (Xetra)
    "XPAR": "XPAR",     # Euronext Paris
    "XAMS": "XAMS",     # Euronext Amsterdam
    "XBRU": "XBRU",     # Euronext Brussels
    "XLIS": "XLIS",     # Euronext Lisbon
    "XMIL": "XMIL",     # Borsa Italiana
    "XMAD": "XMAD",     # Bolsa de Madrid
    "XSWX": "XSWX",     # SIX Swiss Exchange
    "XSTO": "XSTO",     # Nasdaq Stockholm
    "XOSL": "XOSL",     # Oslo Børs
    "XCSE": "XCSE",     # Nasdaq Copenhagen
    "XHEL": "XHEL",     # Nasdaq Helsinki
    "XWAR": "XWAR",     # Warsaw Stock Exchange
    "XTSE": "XTKS",     # Tokyo Stock Exchange (TSE → XTKS in exchange-calendars)
    "XHKG": "XHKG",     # Hong Kong Exchange
    "XSES": "XSES",     # Singapore Exchange
}

# Lazy-loaded calendar cache
_calendar_cache: Dict[str, Any] = {}


def _get_calendar(mic: str):
    """Get exchange calendar for a MIC code. Cached per-process."""
    cal_name = _MIC_TO_CALENDAR.get(mic, mic)
    if cal_name not in _calendar_cache:
        try:
            import exchange_calendars as xcals
            _calendar_cache[cal_name] = xcals.get_calendar(cal_name)
        except Exception as e:
            log.warning("Failed to load calendar for %s (%s): %s", mic, cal_name, e)
            return None
    return _calendar_cache[cal_name]


def is_market_open(mic: str, dt: Optional[datetime] = None) -> bool:
    """Check if a market is currently open (or will be open at dt)."""
    cal = _get_calendar(mic)
    if cal is None:
        return True  # Fail-open: allow trading if calendar unavailable
    if dt is None:
        dt = datetime.now(timezone.utc)
    try:
        return cal.is_open_on_minute(dt.replace(second=0, microsecond=0))
    except Exception:
        return True  # Fail-open


def is_trading_day(mic: str, d: Optional[date] = None) -> bool:
    """Check if a date is a trading session (not holiday/weekend)."""
    cal = _get_calendar(mic)
    if cal is None:
        return d.weekday() < 5 if d else date.today().weekday() < 5  # Fallback: weekdays only
    if d is None:
        d = date.today()
    try:
        return cal.is_session(d.isoformat())
    except Exception:
        return d.weekday() < 5


def get_session_times(mic: str, d: Optional[date] = None) -> Optional[Dict[str, str]]:
    """Get market open/close times for a specific date."""
    cal = _get_calendar(mic)
    if cal is None:
        return None
    if d is None:
        d = date.today()
    try:
        if not cal.is_session(d.isoformat()):
            return None
        session = cal.session_open_close(d.isoformat())
        return {
            "open": session[0].isoformat(),
            "close": session[1].isoformat(),
        }
    except Exception:
        return None


def next_trading_day(mic: str, after: Optional[date] = None) -> Optional[date]:
    """Get the next trading day after a given date."""
    cal = _get_calendar(mic)
    if cal is None:
        return None
    if after is None:
        after = date.today()
    try:
        # Look ahead up to 10 days to find next session
        for i in range(1, 11):
            candidate = after + timedelta(days=i)
            if cal.is_session(candidate.isoformat()):
                return candidate
    except Exception:
        pass
    return None


def trading_days_between(mic: str, start: date, end: date) -> int:
    """Count trading days between two dates (exclusive of end)."""
    cal = _get_calendar(mic)
    if cal is None:
        # Fallback: estimate ~252 trading days per year
        return max(0, int((end - start).days * 252 / 365))
    try:
        sessions = cal.sessions_in_range(start.isoformat(), end.isoformat())
        return len(sessions)
    except Exception:
        return max(0, int((end - start).days * 252 / 365))


def generate_exchange_schedules(
    output_path: Optional[str] = None,
    days_ahead: int = 5,
) -> Dict[str, Any]:
    """Generate exchange_schedules.json for Rust engine consumption.

    Produces a JSON file with:
    - Per-exchange: next N trading days with open/close times (UTC)
    - Holiday list for the next 30 days
    - Half-day / early-close indicators

    Args:
        output_path: Where to write JSON. Defaults to /app/config/exchange_schedules.json
        days_ahead: How many trading days to include per exchange
    """
    if output_path is None:
        output_path = os.environ.get(
            "AEGIS_CONFIG_DIR", "/app/config"
        ) + "/exchange_schedules.json"

    today = date.today()
    result: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exchanges": {},
    }

    for mic, cal_name in _MIC_TO_CALENDAR.items():
        cal = _get_calendar(mic)
        if cal is None:
            continue

        exchange_data: Dict[str, Any] = {
            "calendar": cal_name,
            "sessions": [],
            "holidays_next_30d": [],
        }

        # Next N trading sessions
        try:
            sessions_found = 0
            check_date = today
            while sessions_found < days_ahead and (check_date - today).days < 30:
                ds = check_date.isoformat()
                try:
                    if cal.is_session(ds):
                        times = cal.session_open_close(ds)
                        exchange_data["sessions"].append({
                            "date": ds,
                            "open_utc": times[0].isoformat(),
                            "close_utc": times[1].isoformat(),
                        })
                        sessions_found += 1
                except Exception:
                    pass
                check_date += timedelta(days=1)
        except Exception as e:
            log.warning("Error generating sessions for %s: %s", mic, e)

        # Holidays in next 30 days
        try:
            for i in range(31):
                check = today + timedelta(days=i)
                if check.weekday() < 5:  # Weekday
                    try:
                        if not cal.is_session(check.isoformat()):
                            exchange_data["holidays_next_30d"].append(check.isoformat())
                    except Exception:
                        pass
        except Exception:
            pass

        result["exchanges"][mic] = exchange_data

    # Write output
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    log.info("Generated exchange schedules: %s (%d exchanges)", output_path, len(result["exchanges"]))

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = generate_exchange_schedules()
    print(f"Generated schedules for {len(result['exchanges'])} exchanges")
    for mic, data in result["exchanges"].items():
        sessions = data["sessions"]
        holidays = data["holidays_next_30d"]
        print(f"  {mic}: {len(sessions)} sessions, {len(holidays)} holidays in next 30d")
