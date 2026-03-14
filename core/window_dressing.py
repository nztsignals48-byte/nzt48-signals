"""
Window Dressing Monitor — NZT-48 Microstructure Module
Lakonishok, Shleifer, Thaler & Vishny (1994):
Fund managers buy recent winners in last 5 days of quarter to
improve reported holdings. Creates predictable quarter-end momentum,
followed by Q+1 reversal as positions unwind.
"""

import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

WINDOW_DRESSING_DAYS = 5
UNWIND_DAYS = 3
WD_CONFIDENCE_BOOST = 5
UNWIND_CONFIDENCE_PENALTY = -5
QUARTER_END_MONTHS = {3, 6, 9, 12}


class WindowDressingMonitor:
    def get_quarter_end_dates(self, year=None):
        y = year or date.today().year
        return [date(y,3,31), date(y,6,30), date(y,9,30), date(y,12,31)]

    def get_quarter_start_dates(self, year=None):
        y = year or date.today().year
        return [date(y,1,1), date(y,4,1), date(y,7,1), date(y,10,1)]

    def _trading_days_to_end_of_quarter(self, d):
        ends = self.get_quarter_end_dates(d.year) + [date(d.year+1,3,31)]
        for qend in sorted(ends):
            if qend >= d:
                td = 0
                cur = d
                while cur <= qend:
                    if cur.weekday() < 5: td += 1
                    cur += timedelta(days=1)
                return td - 1
        return None

    def _trading_days_from_quarter_start(self, d):
        starts = self.get_quarter_start_dates(d.year) + self.get_quarter_start_dates(d.year-1)
        for qs in sorted(starts, reverse=True):
            if qs <= d:
                if (d - qs).days > 10: return None
                td = 0
                cur = qs
                while cur < d:
                    if cur.weekday() < 5: td += 1
                    cur += timedelta(days=1)
                return td
        return None

    def is_window_dressing_window(self, check_date=None):
        d = check_date or date.today()
        days = self._trading_days_to_end_of_quarter(d)
        return days is not None and 0 <= days <= WINDOW_DRESSING_DAYS

    def is_new_quarter_unwind(self, check_date=None):
        d = check_date or date.today()
        days = self._trading_days_from_quarter_start(d)
        return days is not None and 0 <= days < UNWIND_DAYS

    def get_active_window(self, check_date=None):
        d = check_date or date.today()
        if self.is_new_quarter_unwind(d): return "UNWIND"
        if self.is_window_dressing_window(d): return "WINDOW_DRESSING"
        return "NORMAL"

    def get_confidence_adjustment(self, ticker, check_date=None, is_ytd_winner=False):
        window = self.get_active_window(check_date)
        if window == "WINDOW_DRESSING" and is_ytd_winner:
            return WD_CONFIDENCE_BOOST
        if window == "UNWIND":
            return UNWIND_CONFIDENCE_PENALTY
        return 0

    def get_telegram_note(self, check_date=None):
        d = check_date or date.today()
        window = self.get_active_window(d)
        ends = sorted(self.get_quarter_end_dates(d.year))
        next_qend = next((e for e in ends if e >= d), ends[0])
        days_to_end = self._trading_days_to_end_of_quarter(d)
        lines = ["Window Dressing Monitor:"]
        qend_str = next_qend.strftime('%d %b %Y')
        lines.append(f"  Next quarter-end: {qend_str}")
        if window == "WINDOW_DRESSING":
            lines.append(f"  WINDOW DRESSING ACTIVE — {days_to_end} trading days to quarter-end")
            lines.append(f"  Effect: +{WD_CONFIDENCE_BOOST} conf on YTD winners")
        elif window == "UNWIND":
            lines.append(f"  NEW QUARTER UNWIND — {UNWIND_CONFIDENCE_PENALTY} conf")
        else:
            lines.append(f"  Normal period — {days_to_end} trading days to quarter-end")
        return chr(10).join(lines)
