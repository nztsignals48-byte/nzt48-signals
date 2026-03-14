"""
Earnings Calendar — NZT-48 V8.0 Auto-Integration
W6 Bug fix: auto-fetches upcoming earnings from yfinance and records them
so the SUE/PEAD scorer can fire without manual input.

Previously: record_earnings() required operator manual call.
Now: auto-checks all ISA tickers daily at 04:00 UTC, records any
     announcement that occurred in the past 24 hours.
"""

import logging
import json
import os
from datetime import datetime, date, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "data/earnings_calendar.json"

_LSE_TO_UNDERLYING = {
    "QQQ3.L": None, "3LUS.L": None, "QQQ5.L": None, "QQQS.L": None,
    "SP5L.L": None, "3USS.L": None, "3SEM.L": None,  # Index ETFs — no earnings
    "GPT3.L": "MSFT",
    "NVD3.L": "NVDA",
    "TSL3.L": "TSLA",
    "TSM3.L": "TSM",
    "MU2.L": "MU",
}


class EarningsCalendar:
    """
    Auto-fetches and tracks earnings announcement dates.
    Feeds into SUE/PEAD scorer and IV crush monitor.
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
        return {"upcoming": {}, "recent": {}, "last_check": None}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self.state_file) if os.path.dirname(self.state_file) else ".", exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning("EarningsCalendar: save failed: %s", e)

    def check_upcoming(self, lse_tickers: list = None) -> dict:
        """
        Checks upcoming earnings for all ISA underlying stocks.
        Returns {ticker: {date, days_away, confirmed}}.
        """
        if lse_tickers is None:
            lse_tickers = list(_LSE_TO_UNDERLYING.keys())

        upcoming = {}
        today = date.today()

        for lse_ticker in lse_tickers:
            underlying = _LSE_TO_UNDERLYING.get(lse_ticker)
            if not underlying:
                continue

            try:
                import yfinance as yf
                cal = yf.Ticker(underlying).calendar
                if cal is None:
                    continue

                # Calendar can be dict or DataFrame
                earnings_date = None
                if isinstance(cal, dict):
                    ed = cal.get("Earnings Date")
                    if ed:
                        if isinstance(ed, list) and len(ed) > 0:
                            ed = ed[0]
                        if hasattr(ed, "date"):
                            earnings_date = ed.date()
                        elif isinstance(ed, str):
                            try:
                                earnings_date = datetime.fromisoformat(ed[:10]).date()
                            except Exception:
                                pass

                if earnings_date:
                    days_away = (earnings_date - today).days
                    if -1 <= days_away <= 30:  # Past 1 day or next 30 days
                        upcoming[underlying] = {
                            "date": earnings_date.isoformat(),
                            "days_away": days_away,
                            "lse_ticker": lse_ticker,
                        }
                        logger.info(
                            "Earnings calendar: %s earnings in %d days (%s)",
                            underlying, days_away, earnings_date,
                        )

                        # Auto-record recent announcements (0-1 days ago)
                        if -1 <= days_away <= 0:
                            self.state["recent"][underlying] = {
                                "date": earnings_date.isoformat(),
                                "auto_recorded_at": datetime.now(timezone.utc).isoformat(),
                            }

            except Exception as e:
                logger.debug("EarningsCalendar: failed for %s: %s", underlying, e)

        self.state["upcoming"] = upcoming
        self.state["last_check"] = datetime.now(timezone.utc).isoformat()
        self._save_state()
        return upcoming

    def is_earnings_in_next_days(self, ticker: str, days: int = 5) -> bool:
        """True if earnings announcement within `days` trading days."""
        underlying = _LSE_TO_UNDERLYING.get(ticker, ticker.replace(".L", ""))
        info = self.state["upcoming"].get(underlying)
        if not info:
            return False
        return 0 <= info.get("days_away", 999) <= days

    def days_to_earnings(self, ticker: str) -> Optional[int]:
        """Returns days until next earnings, or None if not found."""
        underlying = _LSE_TO_UNDERLYING.get(ticker, ticker.replace(".L", ""))
        info = self.state["upcoming"].get(underlying)
        if not info:
            return None
        return info.get("days_away")

    def get_recent_announcements(self, within_days: int = 2) -> list:
        """Returns list of tickers that announced earnings within N days."""
        today = date.today()
        result = []
        for ticker, info in self.state["recent"].items():
            try:
                ann_date = date.fromisoformat(info["date"])
                if (today - ann_date).days <= within_days:
                    result.append({"ticker": ticker, "date": info["date"]})
            except Exception:
                continue
        return result

    def get_telegram_summary(self) -> str:
        upcoming = self.state.get("upcoming", {})
        if not upcoming:
            return "📅 Earnings Calendar: no upcoming events in next 30 days"
        lines = ["📅 Earnings Calendar:"]
        for ticker, info in sorted(upcoming.items(), key=lambda x: x[1].get("days_away", 99)):
            days = info["days_away"]
            d = info["date"]
            prefix = "🔴 TODAY" if days == 0 else f"  in {days}d"
            lines.append(f"  {prefix}: {ticker} ({d})")
        return "\n".join(lines)
