"""
NZT-48 Trading System — Calendar Feed
Sections 37 (Stage 3) and 54: Earnings calendar + economic events calendar.

Provides earnings dates for the Bot B ticker universe via Finnhub (with
yfinance fallback) and high-impact USD economic events scraped from
ForexFactory.  Results are cached for 1 hour.

Environment variables:
    FINNHUB_API_KEY  — Free tier: 60 calls/min
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("nzt48.feeds.calendar")

# ---------------------------------------------------------------------------
# Cache layer — simple in-memory dict with TTL
# ---------------------------------------------------------------------------
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL_SECONDS = 3600  # 1 hour


def _cache_get(key: str) -> Any | None:
    """Return cached value if it exists and has not expired."""
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


# ---------------------------------------------------------------------------
# CalendarFeed
# ---------------------------------------------------------------------------

class CalendarFeed:
    """Earnings calendar and economic events calendar.

    Combines Finnhub API data, yfinance fallback, and ForexFactory scraping
    to provide a unified calendar risk view for the signal pipeline.
    """

    FINNHUB_BASE = "https://finnhub.io/api/v1"
    FOREXFACTORY_URL = "https://www.forexfactory.com/calendar"

    # ForexFactory user-agent (avoid bot blocks)
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Crisis-level macro events
    _FOMC_KEYWORDS = {"fomc", "federal funds rate", "fed interest rate decision"}
    _CPI_NFP_KEYWORDS = {
        "cpi", "consumer price index",
        "nonfarm payrolls", "non-farm payrolls", "nfp",
        "employment situation",
    }

    def __init__(self, finnhub_api_key: str | None = None) -> None:
        self._finnhub_key = finnhub_api_key or os.environ.get("FINNHUB_API_KEY", "")
        if not self._finnhub_key:
            logger.warning("FINNHUB_API_KEY not set — will rely on yfinance fallback")

    # ------------------------------------------------------------------
    # 1. Earnings calendar
    # ------------------------------------------------------------------

    def get_earnings_calendar(
        self,
        tickers: list[str],
        weeks_ahead: int = 2,
    ) -> list[dict[str, Any]]:
        """Fetch upcoming earnings dates for the given tickers.

        Uses Finnhub ``/calendar/earnings`` as the primary source.  If
        Finnhub fails (rate limit, missing key, network), falls back to
        ``yfinance.Ticker.calendar``.

        Args:
            tickers: List of equity symbols (e.g. the 12 Bot B tickers).
            weeks_ahead: How many weeks ahead to look. Default 2.

        Returns:
            List of dicts with keys: ticker, date, time (BMO/AMC/--),
            eps_estimate, eps_actual, revenue_estimate.
        """
        cache_key = f"earnings:{'|'.join(sorted(tickers))}:{weeks_ahead}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        from_date = date.today()
        to_date = from_date + timedelta(weeks=weeks_ahead)

        results = self._earnings_from_finnhub(tickers, from_date, to_date)

        # Fallback: fill any missing tickers from yfinance
        found_tickers = {r["ticker"] for r in results}
        missing = [t for t in tickers if t not in found_tickers]
        if missing:
            yf_results = self._earnings_from_yfinance(missing, from_date, to_date)
            results.extend(yf_results)

        # Sort by date
        results.sort(key=lambda r: r.get("date", ""))
        _cache_set(cache_key, results)
        return results

    def _earnings_from_finnhub(
        self,
        tickers: list[str],
        from_date: date,
        to_date: date,
    ) -> list[dict[str, Any]]:
        """Query Finnhub /calendar/earnings for a date range."""
        if not self._finnhub_key:
            return []

        results: list[dict[str, Any]] = []
        ticker_set = {t.upper() for t in tickers}

        try:
            resp = requests.get(
                f"{self.FINNHUB_BASE}/calendar/earnings",
                params={
                    "from": from_date.isoformat(),
                    "to": to_date.isoformat(),
                    "token": self._finnhub_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("earningsCalendar", []):
                symbol = item.get("symbol", "").upper()
                if symbol not in ticker_set:
                    continue
                results.append({
                    "ticker": symbol,
                    "date": item.get("date", ""),
                    "time": item.get("hour", "--"),  # BMO / AMC / --
                    "eps_estimate": item.get("epsEstimate"),
                    "eps_actual": item.get("epsActual"),
                    "revenue_estimate": item.get("revenueEstimate"),
                })
        except requests.RequestException as exc:
            logger.warning("Finnhub earnings call failed: %s", exc)
        except (KeyError, ValueError) as exc:
            logger.warning("Finnhub earnings parse error: %s", exc)

        return results

    def _earnings_from_yfinance(
        self,
        tickers: list[str],
        from_date: date,
        to_date: date,
    ) -> list[dict[str, Any]]:
        """Fallback: pull earnings dates from yfinance.Ticker.calendar."""
        results: list[dict[str, Any]] = []
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance not installed — earnings fallback unavailable")
            return results

        for ticker in tickers:
            try:
                t = yf.Ticker(ticker)
                cal = t.calendar
                if cal is None or (cal.empty if hasattr(cal, "empty") else not cal):
                    continue

                # yfinance returns a DataFrame or dict depending on version
                if hasattr(cal, "to_dict"):
                    cal_dict = cal.to_dict()
                    # May have columns like "Earnings Date", "EPS Estimate", etc.
                    earnings_dates = cal_dict.get("Earnings Date", {})
                    for _idx, ed in earnings_dates.items():
                        if hasattr(ed, "date"):
                            ed = ed.date()
                        if isinstance(ed, str):
                            ed = datetime.strptime(ed[:10], "%Y-%m-%d").date()
                        if from_date <= ed <= to_date:
                            results.append({
                                "ticker": ticker.upper(),
                                "date": ed.isoformat(),
                                "time": "--",
                                "eps_estimate": cal_dict.get("EPS Estimate", {}).get(_idx),
                                "eps_actual": None,
                                "revenue_estimate": cal_dict.get("Revenue Estimate", {}).get(_idx),
                            })
                elif isinstance(cal, dict):
                    ed = cal.get("Earnings Date")
                    if ed and hasattr(ed, "__iter__"):
                        for d in ed:
                            if hasattr(d, "date"):
                                d = d.date()
                            if isinstance(d, str):
                                d = datetime.strptime(d[:10], "%Y-%m-%d").date()
                            if from_date <= d <= to_date:
                                results.append({
                                    "ticker": ticker.upper(),
                                    "date": d.isoformat(),
                                    "time": "--",
                                    "eps_estimate": cal.get("EPS Estimate"),
                                    "eps_actual": None,
                                    "revenue_estimate": cal.get("Revenue Estimate"),
                                })
            except Exception as exc:
                logger.debug("yfinance calendar for %s failed: %s", ticker, exc)

        return results

    # ------------------------------------------------------------------
    # 2. Economic events (ForexFactory)
    # ------------------------------------------------------------------

    def get_economic_events(self, days_ahead: int = 7) -> list[dict[str, Any]]:
        """Scrape ForexFactory calendar for USD high-impact events.

        Parses the ForexFactory HTML table and returns FOMC, CPI, NFP,
        and other high-impact USD events within the look-ahead window.

        Args:
            days_ahead: Number of days to look forward. Default 7.

        Returns:
            List of dicts: {date, time, event, impact, currency}.
        """
        cache_key = f"econ_events:{days_ahead}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        events = self._scrape_forexfactory(days_ahead)
        _cache_set(cache_key, events)
        return events

    def _scrape_forexfactory(self, days_ahead: int) -> list[dict[str, Any]]:
        """Parse ForexFactory /calendar HTML for USD high-impact rows."""
        events: list[dict[str, Any]] = []
        today = date.today()
        cutoff = today + timedelta(days=days_ahead)

        try:
            resp = requests.get(
                self.FOREXFACTORY_URL,
                headers=self._HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("ForexFactory scrape failed: %s", exc)
            return events

        soup = BeautifulSoup(resp.text, "html.parser")

        # ForexFactory calendar rows use class="calendar__row"
        rows = soup.select("tr.calendar__row")
        current_date_str = ""

        for row in rows:
            try:
                # Date cell — may span multiple rows; only present on first
                date_cell = row.select_one("td.calendar__date span")
                if date_cell and date_cell.get_text(strip=True):
                    current_date_str = date_cell.get_text(strip=True)

                # Currency filter — USD only
                currency_cell = row.select_one("td.calendar__currency")
                if not currency_cell:
                    continue
                currency = currency_cell.get_text(strip=True).upper()
                if currency != "USD":
                    continue

                # Impact — look for high-impact icon class
                impact_cell = row.select_one("td.calendar__impact span")
                if not impact_cell:
                    continue
                impact_classes = impact_cell.get("class", [])
                if any("high" in c.lower() for c in impact_classes):
                    impact = "HIGH"
                elif any("medium" in c.lower() or "medi" in c.lower() for c in impact_classes):
                    impact = "MEDIUM"
                else:
                    impact = "LOW"

                # Only keep HIGH impact (FOMC, CPI, NFP, etc.)
                if impact != "HIGH":
                    continue

                # Event name
                event_cell = row.select_one("td.calendar__event span")
                if not event_cell:
                    continue
                event_name = event_cell.get_text(strip=True)

                # Time
                time_cell = row.select_one("td.calendar__time")
                event_time = time_cell.get_text(strip=True) if time_cell else ""

                # Parse the date string
                event_date = self._parse_ff_date(current_date_str)
                if event_date is None:
                    continue
                if event_date > cutoff:
                    continue

                events.append({
                    "date": event_date.isoformat(),
                    "time": event_time,
                    "event": event_name,
                    "impact": impact,
                    "currency": currency,
                })
            except Exception as exc:
                logger.debug("ForexFactory row parse error: %s", exc)
                continue

        return events

    @staticmethod
    def _parse_ff_date(raw: str) -> date | None:
        """Best-effort parse of ForexFactory date strings.

        ForexFactory uses formats like ``Mon Jan 29`` or ``Jan 29``.
        We assume the current year (or next year if the date has passed).
        """
        if not raw:
            return None

        # Strip leading day-of-week if present (e.g. "Mon Jan 29")
        parts = raw.split()
        if len(parts) == 3:
            # "Mon Jan 29"
            month_str, day_str = parts[1], parts[2]
        elif len(parts) == 2:
            month_str, day_str = parts[0], parts[1]
        else:
            return None

        try:
            year = date.today().year
            parsed = datetime.strptime(f"{month_str} {day_str} {year}", "%b %d %Y").date()
            # If the date is more than 30 days in the past, assume next year
            if parsed < date.today() - timedelta(days=30):
                parsed = parsed.replace(year=year + 1)
            return parsed
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # 3-5. Convenience checks
    # ------------------------------------------------------------------

    def is_fomc_day(self, check_date: date | None = None) -> bool:
        """Return True if *check_date* (default today) has an FOMC event."""
        check_date = check_date or date.today()
        events = self.get_economic_events(days_ahead=14)
        for ev in events:
            if ev["date"] == check_date.isoformat():
                if any(kw in ev["event"].lower() for kw in self._FOMC_KEYWORDS):
                    return True
        return False

    def is_cpi_nfp_day(self, check_date: date | None = None) -> bool:
        """Return True if *check_date* (default today) has a CPI or NFP release."""
        check_date = check_date or date.today()
        events = self.get_economic_events(days_ahead=14)
        for ev in events:
            if ev["date"] == check_date.isoformat():
                if any(kw in ev["event"].lower() for kw in self._CPI_NFP_KEYWORDS):
                    return True
        return False

    def has_earnings_tonight(self, ticker: str) -> bool:
        """Return True if *ticker* reports earnings after market close today.

        Also returns True for before-market-open (BMO) the next trading day
        since the position would be held overnight into the announcement.
        """
        today = date.today()
        tomorrow = today + timedelta(days=1)
        # Skip weekends for "tomorrow"
        if tomorrow.weekday() == 5:  # Saturday
            tomorrow += timedelta(days=2)
        elif tomorrow.weekday() == 6:  # Sunday
            tomorrow += timedelta(days=1)

        earnings = self.get_earnings_calendar([ticker], weeks_ahead=1)
        for e in earnings:
            if e["ticker"].upper() != ticker.upper():
                continue
            edate = e["date"]
            etime = e.get("time", "--").upper()
            # AMC today
            if edate == today.isoformat() and etime in ("AMC", "AFTER MARKET CLOSE"):
                return True
            # BMO tomorrow
            if edate == tomorrow.isoformat() and etime in ("BMO", "BEFORE MARKET OPEN"):
                return True
            # Unknown time but date is today — assume risk
            if edate == today.isoformat() and etime == "--":
                return True
        return False

    # ------------------------------------------------------------------
    # 6. Calendar risk assessment
    # ------------------------------------------------------------------

    def get_calendar_risk(self, tickers: list[str] | None = None) -> str:
        """Compute aggregate calendar risk level.

        Section 37, Stage 3 calendar risk:
            BLOCK     — FOMC day (block all 5x ETPs)
            HIGH_RISK — CPI/NFP day (half position size)
            CLEAR     — No material events

        Individual ticker earnings blocking is handled separately via
        ``has_earnings_tonight``.

        Args:
            tickers: Optional list of tickers to check for tonight's
                     earnings.  Not used for the aggregate level but
                     included for informational completeness.

        Returns:
            One of ``"CLEAR"``, ``"HIGH_RISK"``, or ``"BLOCK"``.
        """
        today = date.today()

        # FOMC day => BLOCK all 5x ETPs
        if self.is_fomc_day(today):
            logger.info("Calendar risk: BLOCK (FOMC day)")
            return "BLOCK"

        # CPI / NFP day => HIGH_RISK (half size)
        if self.is_cpi_nfp_day(today):
            logger.info("Calendar risk: HIGH_RISK (CPI/NFP day)")
            return "HIGH_RISK"

        logger.debug("Calendar risk: CLEAR")
        return "CLEAR"

    # ------------------------------------------------------------------
    # 7. Earnings IV rank (Section 66)
    # ------------------------------------------------------------------

    def get_earnings_iv_rank(self, ticker: str) -> float:
        """Return the implied-volatility percentile rank (0-100) for *ticker*.

        Section 66: If IV rank > 80th percentile pre-announcement, do NOT
        trade.  Uses Finnhub's ``/stock/metric`` endpoint for current IV
        and 52-week range to compute the percentile rank.

        Falls back to 50.0 (neutral) if data is unavailable.
        """
        cache_key = f"iv_rank:{ticker}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        iv_rank = self._iv_rank_from_finnhub(ticker)
        _cache_set(cache_key, iv_rank)
        return iv_rank

    def _iv_rank_from_finnhub(self, ticker: str) -> float:
        """Compute IV rank via Finnhub /stock/metric basic financials."""
        if not self._finnhub_key:
            return 50.0

        try:
            resp = requests.get(
                f"{self.FINNHUB_BASE}/stock/metric",
                params={
                    "symbol": ticker.upper(),
                    "metric": "all",
                    "token": self._finnhub_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            metrics = data.get("metric", {})

            # Finnhub provides:
            #   "52WeekHighDate", "52WeekLowDate",
            #   "currentEv/freeCashFlowAnnual" etc.
            # For IV we look for specific fields
            current_iv = metrics.get("currentIV")  # not always present
            iv_52w_high = metrics.get("52WeekIVHigh")
            iv_52w_low = metrics.get("52WeekIVLow")

            # If direct IV fields are missing, try the Finnhub beta/metric fields
            if current_iv is None:
                # Alternative: use "10DayAverageTradingVolume" as proxy indicator
                # but this is not IV.  Return neutral.
                logger.debug("IV data unavailable for %s on Finnhub", ticker)
                return self._iv_rank_from_yfinance(ticker)

            if iv_52w_high is not None and iv_52w_low is not None:
                iv_range = iv_52w_high - iv_52w_low
                if iv_range > 0:
                    rank = ((current_iv - iv_52w_low) / iv_range) * 100.0
                    return max(0.0, min(100.0, rank))

            return 50.0

        except requests.RequestException as exc:
            logger.warning("Finnhub IV rank call failed for %s: %s", ticker, exc)
            return self._iv_rank_from_yfinance(ticker)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Finnhub IV rank parse error for %s: %s", ticker, exc)
            return 50.0

    @staticmethod
    def _iv_rank_from_yfinance(ticker: str) -> float:
        """Fallback IV rank using yfinance implied volatility data."""
        try:
            import yfinance as yf

            t = yf.Ticker(ticker)
            # yfinance options chain gives current IV per strike
            # We use the ATM IV as a proxy
            expirations = t.options
            if not expirations:
                return 50.0

            # Use nearest expiry
            chain = t.option_chain(expirations[0])
            calls = chain.calls
            if calls.empty:
                return 50.0

            # ATM call: closest strike to current price
            info = t.info
            current_price = info.get("currentPrice") or info.get("regularMarketPrice", 0)
            if not current_price:
                return 50.0

            calls = calls.copy()
            calls["dist"] = abs(calls["strike"] - current_price)
            atm = calls.loc[calls["dist"].idxmin()]
            current_iv = atm.get("impliedVolatility", 0.5)

            # Without historical IV range from yfinance, estimate rank
            # Typical equity IV range: 0.15 - 0.80
            iv_floor = 0.15
            iv_ceil = 0.80
            rank = ((current_iv - iv_floor) / (iv_ceil - iv_floor)) * 100.0
            return max(0.0, min(100.0, rank))

        except Exception as exc:
            logger.debug("yfinance IV rank fallback failed for %s: %s", ticker, exc)
            return 50.0
