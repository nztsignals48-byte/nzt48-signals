"""
delivery/dst_anchor.py
=======================
DST-aware scheduler anchor for NZT-48.

Derives UK (Europe/London) fire times from US market anchor times so that
the system handles DST desync weeks correctly — the 1-2 weeks per year when
the US and UK switch on different dates, shifting the effective offset from
UTC+0/+1 (UK) + UTC-5/-4 (US) by an extra hour.

Usage:
    from delivery.dst_anchor import get_uk_market_times, log_dst_state

    times = get_uk_market_times()
    # times["nyse_open_uk"]  -> e.g. "14:30" or "15:30" during desync week
    # times["nyse_close_uk"] -> e.g. "21:00" or "22:00" during desync week
    # times["lse_open_uk"]   -> always "08:00" (LSE is always UK local)
    # times["pdf_pre_lse"]   -> "07:00" (30 min before LSE open)
    # times["pdf_pre_nyse"]  -> 30 min before NYSE open in UK time
    # times["pdf_eod"]       -> 30 min after NYSE close in UK time
    # times["pdf_mega"]      -> 60 min after NYSE close in UK time

The anchor approach: NYSE opens at 09:30 ET and closes at 16:00 ET.
Convert those to UTC, then to Europe/London — handles DST automatically
via zoneinfo without any manual offset arithmetic.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger("nzt48.dst_anchor")

from core.clock import ET_TZ as _ET, UK_TZ as _UK
_UTC = timezone.utc

# Fixed LSE times (always UK local regardless of US DST)
_LSE_OPEN_UK  = time(8,  0)
_LSE_CLOSE_UK = time(16, 30)

# NYSE anchor times (ET — these never change)
_NYSE_OPEN_ET  = time(9,  30)
_NYSE_CLOSE_ET = time(16,  0)


def _et_to_uk(t: time, ref_date: date = None) -> time:
    """Convert an ET time to UK local time using today's DST state."""
    d = ref_date or date.today()
    dt_et = datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=_ET)
    dt_uk = dt_et.astimezone(_UK)
    return dt_uk.time().replace(second=0, microsecond=0)


def get_uk_market_times(ref_date: date = None) -> dict[str, str]:
    """
    Return a dict of key UK-local fire times for today (or ref_date).

    All values are HH:MM strings for use with APScheduler cron triggers.
    """
    d = ref_date or date.today()

    nyse_open_uk  = _et_to_uk(_NYSE_OPEN_ET,  d)
    nyse_close_uk = _et_to_uk(_NYSE_CLOSE_ET, d)

    # PDF fire times
    # PRE_LSE: 07:00 UK (fixed — 1h before LSE open)
    # PRE_NYSE: 30 min before NYSE open UK time
    pre_nyse_h = nyse_open_uk.hour
    pre_nyse_m = nyse_open_uk.minute - 30
    if pre_nyse_m < 0:
        pre_nyse_h -= 1
        pre_nyse_m += 60

    # EOD: 30 min after NYSE close
    eod_h = nyse_close_uk.hour
    eod_m = nyse_close_uk.minute + 30
    if eod_m >= 60:
        eod_h += 1
        eod_m -= 60

    # MEGA: 60 min after NYSE close
    mega_h = nyse_close_uk.hour
    mega_m = nyse_close_uk.minute + 60
    if mega_m >= 60:
        mega_h += 1
        mega_m -= 60

    result = {
        "lse_open_uk":   f"{_LSE_OPEN_UK.hour:02d}:{_LSE_OPEN_UK.minute:02d}",
        "lse_close_uk":  f"{_LSE_CLOSE_UK.hour:02d}:{_LSE_CLOSE_UK.minute:02d}",
        "nyse_open_uk":  f"{nyse_open_uk.hour:02d}:{nyse_open_uk.minute:02d}",
        "nyse_close_uk": f"{nyse_close_uk.hour:02d}:{nyse_close_uk.minute:02d}",
        "pdf_pre_lse":   "07:00",
        "pdf_pre_nyse":  f"{pre_nyse_h:02d}:{pre_nyse_m:02d}",
        "pdf_eod":       f"{eod_h:02d}:{eod_m:02d}",
        "pdf_mega":      f"{mega_h:02d}:{mega_m:02d}",
    }
    return result


def log_dst_state(ref_date: date = None) -> None:
    """Log current UK/ET offsets and computed fire times. Call at startup."""
    d = ref_date or date.today()
    times = get_uk_market_times(d)

    # Compute current offsets for logging
    midnight = datetime(d.year, d.month, d.day, tzinfo=_UTC)
    uk_off   = midnight.astimezone(_UK).utcoffset()
    et_off   = midnight.astimezone(_ET).utcoffset()

    uk_h = int(uk_off.total_seconds() // 3600)
    et_h = int(et_off.total_seconds() // 3600)
    diff  = uk_h - et_h

    logger.info(
        "[DST_ANCHOR] date=%s  UK=UTC%+d  ET=UTC%+d  diff=%dh  "
        "NYSE_open_UK=%s  NYSE_close_UK=%s  "
        "PDF_pre_lse=%s  PDF_pre_nyse=%s  PDF_eod=%s  PDF_mega=%s",
        d, uk_h, et_h, diff,
        times["nyse_open_uk"],  times["nyse_close_uk"],
        times["pdf_pre_lse"],   times["pdf_pre_nyse"],
        times["pdf_eod"],       times["pdf_mega"],
    )

    # Warn if we're in a DST desync week (diff != 5)
    if diff != 5:
        logger.warning(
            "[DST_ANCHOR] DST DESYNC WEEK DETECTED: UK-ET gap = %dh (normally 5h). "
            "PDF fire times have been adjusted automatically.", diff
        )


def get_scheduler_hours_minutes(key: str, ref_date: date = None) -> tuple[int, int]:
    """Return (hour, minute) for APScheduler from a key like 'pdf_pre_nyse'."""
    times = get_uk_market_times(ref_date)
    hm = times.get(key, "00:00")
    h, m = hm.split(":")
    return int(h), int(m)
