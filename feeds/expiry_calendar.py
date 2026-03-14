"""
NZT-48 — Options Expiry Calendar + Pinning Detection
Ni, Pearson & Poteshman (2005): stock prices cluster toward max-OI strikes near expiry.
"""
from __future__ import annotations
import json, os, logging, datetime
from typing import Optional

logger = logging.getLogger(__name__)
_DATA_PATH = "data/expiry_state.json"


def _next_third_friday(from_date=None):
    d = from_date or datetime.date.today()
    first = d.replace(day=1)
    first_friday = first + datetime.timedelta(days=(4 - first.weekday()) % 7)
    third_friday = first_friday + datetime.timedelta(weeks=2)
    if third_friday <= d:
        if d.month == 12:
            first = datetime.date(d.year + 1, 1, 1)
        else:
            first = datetime.date(d.year, d.month + 1, 1)
        first_friday = first + datetime.timedelta(days=(4 - first.weekday()) % 7)
        third_friday = first_friday + datetime.timedelta(weeks=2)
    return third_friday


def _next_weekly_friday(from_date=None):
    d = from_date or datetime.date.today()
    days_until = (4 - d.weekday()) % 7
    if days_until == 0:
        days_until = 7
    return d + datetime.timedelta(days=days_until)


class ExpiryCalendar:
    """Tracks options expiry dates and detects pin risk."""
    HEAVY_OPTIONS_TICKERS = ["NVDA", "TSLA", "AMD", "MRVL", "AVGO", "ARM", "SMCI"]

    def __init__(self):
        self._state: dict = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(_DATA_PATH):
                with open(_DATA_PATH) as f:
                    self._state = json.load(f)
        except Exception:
            self._state = {}

    def _save(self):
        os.makedirs("data", exist_ok=True)
        with open(_DATA_PATH, "w") as f:
            json.dump(self._state, f, indent=2)

    def update(self):
        """Refresh expiry dates and OI data."""
        today = datetime.date.today()
        monthly = _next_third_friday(today)
        weekly = _next_weekly_friday(today)
        days_to_monthly = (monthly - today).days
        days_to_weekly = (weekly - today).days

        for ticker in self.HEAVY_OPTIONS_TICKERS:
            try:
                import yfinance as yf
                tk = yf.Ticker(ticker)
                info = tk.info or {}
                current_price = float(info.get("regularMarketPrice") or info.get("currentPrice") or 0)
                pin_risk = False
                top_strike = None
                max_oi = 0
                try:
                    exps = tk.options
                    if exps:
                        chain = tk.option_chain(exps[0])
                        calls_oi = chain.calls[["strike", "openInterest"]].copy()
                        puts_oi = chain.puts[["strike", "openInterest"]].copy()
                        combined = calls_oi.copy()
                        combined["openInterest"] = (
                            combined["openInterest"].fillna(0) +
                            puts_oi["openInterest"].fillna(0)
                        )
                        if len(combined) > 0 and current_price > 0:
                            top_row = combined.nlargest(1, "openInterest").iloc[0]
                            top_strike = float(top_row["strike"])
                            max_oi = int(top_row["openInterest"])
                            distance_pct = abs(current_price - top_strike) / current_price
                            pin_risk = distance_pct < 0.01 and (days_to_monthly <= 2 or days_to_weekly <= 2)
                except Exception:
                    pass

                self._state[ticker] = {
                    "days_to_monthly": days_to_monthly,
                    "days_to_weekly": days_to_weekly,
                    "pin_risk": pin_risk,
                    "top_strike": top_strike,
                    "max_oi": max_oi,
                    "current_price": current_price,
                }
            except Exception as e:
                self._state[ticker] = {"pin_risk": False, "error": str(e)}

        self._save()

    def is_pin_risk(self, ticker: str) -> bool:
        return self._state.get(ticker, {}).get("pin_risk", False)

    def get_confidence_adj(self, ticker: str) -> int:
        state = self._state.get(ticker, {})
        if state.get("pin_risk"):
            return -8
        days_to_monthly = state.get("days_to_monthly", 99)
        if days_to_monthly >= 28:
            return 5
        return 0
