"""
Options Expiry Pinning Monitor — NZT-48 Microstructure Module
Ni, Pearson & Poteshman (2005): Stock price clustering at strike prices
near options expiry due to dealer delta-hedging creates pinning effects.

Pre-expiry: stocks gravitate toward high-OI strikes → reduced momentum.
Post-monthly-expiry: options removed, pinning ends → momentum resumes.
"""

import json
import os
import logging
from datetime import datetime, date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

OPTIONS_UNIVERSE = {"NVDA", "TSLA", "AMD", "TSM", "MU", "ARM", "AAPL", "MSFT", "META", "GOOGL"}


class ExpiryPinningMonitor:
    """
    Tracks options expiry calendar and adjusts confidence:
    - Pre-monthly-expiry window (Mon–Thu before 3rd Friday): -8 confidence
    - Post-monthly-expiry window (Mon–Wed after 3rd Friday): +5 confidence
    - Weekly expiry Thursday: minor caution -3

    LSE ETPs have no options → no adjustment applied.
    """

    def __init__(self):
        self._oi_cache: dict = {}  # ticker → {strike: oi}
        self._cache_date: Optional[date] = None

    # ─────────────────────────────────────────────────────────
    # Expiry calendar
    # ─────────────────────────────────────────────────────────

    def get_next_monthly_expiry(self, from_date: Optional[date] = None) -> date:
        """
        Returns the next monthly expiry — 3rd Friday of the current or next month.
        """
        d = from_date or date.today()

        for month_offset in range(0, 3):
            year = d.year + (d.month + month_offset - 1) // 12
            month = (d.month + month_offset - 1) % 12 + 1
            # Find 3rd Friday
            first_day = date(year, month, 1)
            # Day of week: Monday=0, Friday=4
            first_friday = first_day + timedelta(days=(4 - first_day.weekday()) % 7)
            third_friday = first_friday + timedelta(weeks=2)
            if third_friday >= d:
                return third_friday

        # Fallback
        return d + timedelta(days=21)

    def get_next_weekly_expiry(self, from_date: Optional[date] = None) -> date:
        """Returns the next Friday (weekly expiry)."""
        d = from_date or date.today()
        days_ahead = (4 - d.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return d + timedelta(days=days_ahead)

    def is_monthly_expiry_week(self, check_date: Optional[date] = None) -> bool:
        """True if we're in the Mon–Thu window before 3rd Friday."""
        d = check_date or date.today()
        expiry = self.get_next_monthly_expiry(d)
        days_to_expiry = (expiry - d).days
        return 1 <= days_to_expiry <= 4 and d.weekday() < 4  # Mon–Thu

    def is_pre_expiry_window(self, check_date: Optional[date] = None) -> bool:
        """Alias for is_monthly_expiry_week."""
        return self.is_monthly_expiry_week(check_date)

    def is_post_monthly_expiry_window(self, check_date: Optional[date] = None) -> bool:
        """
        True in Mon–Wed of the week AFTER 3rd Friday.
        Pinning is removed, momentum can reassert.
        """
        d = check_date or date.today()
        # Check the most recent 3rd Friday
        # Look back up to 7 days to find the last 3rd Friday
        for days_back in range(1, 8):
            candidate = d - timedelta(days=days_back)
            if candidate.weekday() == 4:  # Friday
                # Is it the 3rd Friday of its month?
                year, month = candidate.year, candidate.month
                first_day = date(year, month, 1)
                first_friday = first_day + timedelta(days=(4 - first_day.weekday()) % 7)
                third_friday = first_friday + timedelta(weeks=2)
                if candidate == third_friday:
                    days_since = (d - candidate).days
                    return 1 <= days_since <= 3 and d.weekday() <= 2  # Mon–Wed
        return False

    def is_weekly_expiry_thursday(self, check_date: Optional[date] = None) -> bool:
        """True on Thursday — weekly expiry tomorrow."""
        d = check_date or date.today()
        return d.weekday() == 3  # Thursday

    # ─────────────────────────────────────────────────────────
    # OI strike data (optional enhancement)
    # ─────────────────────────────────────────────────────────

    def get_top_oi_strikes(self, ticker: str) -> list:
        """
        Returns top 3 open-interest strikes for nearest expiry.
        Used to identify pin targets. Returns [] for LSE ETPs.
        """
        base = ticker.replace(".L", "")
        if base not in OPTIONS_UNIVERSE:
            return []

        today = date.today()
        if self._cache_date == today and base in self._oi_cache:
            return self._oi_cache[base]

        try:
            import yfinance as yf
            stock = yf.Ticker(base)
            expirations = stock.options
            if not expirations:
                return []

            chain = stock.option_chain(expirations[0])
            calls = chain.calls
            puts = chain.puts

            # Combine OI by strike
            oi_by_strike = {}
            for df in [calls, puts]:
                for _, row in df.iterrows():
                    s = row["strike"]
                    oi_by_strike[s] = oi_by_strike.get(s, 0) + row.get("openInterest", 0)

            top = sorted(oi_by_strike.items(), key=lambda x: x[1], reverse=True)[:3]
            result = [s for s, _ in top]

            self._oi_cache[base] = result
            self._cache_date = today
            return result

        except Exception as e:
            logger.debug(f"ExpiryPinningMonitor.get_top_oi_strikes({ticker}): {e}")
            return []

    def is_pin_risk(self, ticker: str, current_price: Optional[float] = None) -> bool:
        """
        True if current price is within 1% of a high-OI strike AND we're in pre-expiry window.
        """
        if not self.is_pre_expiry_window():
            return False

        strikes = self.get_top_oi_strikes(ticker)
        if not strikes or current_price is None:
            return self.is_pre_expiry_window()  # Conservative: flag window even without strike data

        for strike in strikes:
            if abs(current_price - strike) / strike < 0.01:
                return True
        return False

    # ─────────────────────────────────────────────────────────
    # Confidence adjustments
    # ─────────────────────────────────────────────────────────

    def get_confidence_adjustment(self, ticker: str, current_price: Optional[float] = None) -> int:
        """
        Pre-monthly-expiry window: -8 (pinning suppresses momentum)
        Post-monthly-expiry window: +5 (momentum can reassert)
        Weekly expiry Thursday: -3 (minor caution)
        LSE ETPs: 0 (no options)
        """
        base = ticker.replace(".L", "")
        if base not in OPTIONS_UNIVERSE:
            return 0  # LSE ETP — not affected

        today = date.today()
        if self.is_post_monthly_expiry_window(today):
            return 5
        if self.is_monthly_expiry_week(today):
            return -8
        if self.is_weekly_expiry_thursday(today):
            return -3
        return 0

    # ─────────────────────────────────────────────────────────
    # Telegram note
    # ─────────────────────────────────────────────────────────

    def get_telegram_note(self) -> str:
        today = date.today()
        monthly_expiry = self.get_next_monthly_expiry(today)
        weekly_expiry = self.get_next_weekly_expiry(today)

        lines = ["📅 Options Expiry Monitor:"]
        lines.append(f"  Monthly expiry: {monthly_expiry.strftime('%d %b %Y')} ({(monthly_expiry - today).days}d)")
        lines.append(f"  Weekly expiry:  {weekly_expiry.strftime('%d %b %Y')} ({(weekly_expiry - today).days}d)")

        if self.is_post_monthly_expiry_window():
            lines.append("  ✅ POST-MONTHLY EXPIRY — pinning removed, momentum boost +5")
        elif self.is_monthly_expiry_week():
            lines.append("  ⚠️  PRE-EXPIRY WEEK — pinning risk, confidence -8 for options tickers")
        elif self.is_weekly_expiry_thursday():
            lines.append("  ⚠️  WEEKLY EXPIRY THURSDAY — minor caution -3")
        else:
            lines.append("  ✅ Normal window — no expiry adjustment")

        return "\n".join(lines)
