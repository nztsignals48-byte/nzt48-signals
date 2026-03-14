"""
NZT-48 Trading System — Finviz Hot Stock Screener
Section 54: Data feeds for S11 Hot Scanner strategy.

Scrapes Finviz free-tier screener pages for:
  - Hot stocks (high RVOL + gap)
  - Pre-market gappers
  - Ticker fundamentals (catalyst context for S4)
  - Volume spike detection

No API key required. Rate-limited to 1 request per 3 seconds.
Results cached for 15 minutes to reduce scraping load.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://finviz.com/screener.ashx"
_QUOTE_URL = "https://finviz.com/quote.ashx"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_RATE_LIMIT_SECONDS = 3.0
_CACHE_TTL_SECONDS = 15 * 60  # 15 minutes

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    """Single cache entry with expiry timestamp."""
    data: Any
    expires_at: float  # time.monotonic() deadline


class _SimpleCache:
    """In-memory TTL cache. Keys are arbitrary strings."""

    def __init__(self, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        return entry.data

    def set(self, key: str, data: Any) -> None:
        self._store[key] = _CacheEntry(
            data=data,
            expires_at=time.monotonic() + self._ttl,
        )

    def clear(self) -> None:
        self._store.clear()


# ---------------------------------------------------------------------------
# Screener
# ---------------------------------------------------------------------------


class FinvizScreener:
    """Finviz free-tier screener scraper.

    All public methods return plain dicts/lists so consumers don't need
    to know about BeautifulSoup internals.  Every network call is
    rate-limited (min 3 s between requests) and results are cached for
    15 minutes.

    Usage::

        screener = FinvizScreener()
        hot = screener.scan_hot_stocks()
        gappers = screener.scan_gappers(min_gap_pct=2.0)
        fundamentals = screener.get_ticker_fundamentals("AAPL")
        spikes = screener.scan_volume_spikes(["AAPL", "TSLA", "NVDA"])
    """

    def __init__(self, cache_ttl: float = _CACHE_TTL_SECONDS) -> None:
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._cache = _SimpleCache(ttl_seconds=cache_ttl)
        self._last_request_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_hot_stocks(self) -> list[dict[str, Any]]:
        """Scan for hot stocks matching S11 Hot Scanner criteria.

        Filters applied on Finviz:
          - Market Cap > $1 B (``cap_largeover|cap_midover`` isn't granular
            enough, so we use ``cap_midover`` which is > $2 B and add a
            post-filter for the $1-2 B range via ``cap_smallover``).
          - Relative Volume > 2
          - Change (gap) > 3 %

        Returns a list of dicts sorted by RVOL descending::

            {
                "ticker": "SMCI",
                "price": 42.15,
                "change_pct": 8.3,
                "volume": 12_500_000,
                "avg_volume": 3_000_000,
                "rvol": 4.17,
                "gap_pct": 5.2,
                "market_cap": "12.5B",
                "sector": "Technology",
            }
        """
        cache_key = "hot_stocks"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        # Finviz filter tokens:
        #   sh_relvol_o2   = Relative Volume > 2
        #   ta_change_u3   = Change Up > 3%
        #   cap_smallover  = Market Cap > $300M  (we post-filter for > $1B)
        filters = "sh_relvol_o2,ta_change_u3,cap_smallover"
        params = {
            "v": "171",       # custom screener view (performance + volume)
            "f": filters,
            "o": "-relativevolume",  # sort by RVOL descending
        }

        rows = self._fetch_screener_table(params)
        results: list[dict[str, Any]] = []

        for row in rows:
            parsed = self._parse_screener_row(row)
            if parsed is None:
                continue
            # Post-filter: market cap > $1B
            if self._market_cap_to_float(parsed.get("market_cap", "")) < 1_000_000_000:
                continue
            results.append(parsed)

        # Ensure sorted by rvol descending (should already be, but be safe)
        results.sort(key=lambda r: r.get("rvol", 0), reverse=True)

        self._cache.set(cache_key, results)
        logger.info("scan_hot_stocks: found %d hot stocks", len(results))
        return results

    def scan_gappers(self, min_gap_pct: float = 1.5) -> list[dict[str, Any]]:
        """Scan for pre-market / opening gappers.

        Used by Section 6 gap-and-go / gap-and-fade pattern detection.

        Filters:
          - Change (gap) > *min_gap_pct* %
          - Relative Volume > 2

        Returns list of dicts with same schema as ``scan_hot_stocks``,
        sorted by ``gap_pct`` descending.
        """
        cache_key = f"gappers_{min_gap_pct}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        # Map min_gap_pct to closest Finviz filter token
        gap_filter = self._gap_pct_to_filter(min_gap_pct)
        filters = f"sh_relvol_o2,{gap_filter}"
        params = {
            "v": "171",
            "f": filters,
            "o": "-change",  # sort by change % descending
        }

        rows = self._fetch_screener_table(params)
        results: list[dict[str, Any]] = []

        for row in rows:
            parsed = self._parse_screener_row(row)
            if parsed is None:
                continue
            # Post-filter: exact gap_pct >= min_gap_pct
            if parsed.get("change_pct", 0) < min_gap_pct:
                continue
            results.append(parsed)

        results.sort(key=lambda r: r.get("change_pct", 0), reverse=True)

        self._cache.set(cache_key, results)
        logger.info("scan_gappers(%.1f%%): found %d gappers", min_gap_pct, len(results))
        return results

    def get_ticker_fundamentals(self, ticker: str) -> dict[str, Any]:
        """Fetch fundamental data for a single ticker from Finviz quote page.

        Provides context for S4 Catalyst/Narrative strategy.

        Returns::

            {
                "ticker": "AAPL",
                "market_cap": "2.85T",
                "pe_ratio": 28.5,
                "sector": "Technology",
                "industry": "Consumer Electronics",
                "short_float": 0.55,
                "insider_ownership": 0.07,
                "inst_ownership": 60.12,
            }

        Returns a dict with None values on failure.
        """
        cache_key = f"fundamentals_{ticker.upper()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        empty: dict[str, Any] = {
            "ticker": ticker.upper(),
            "market_cap": None,
            "pe_ratio": None,
            "sector": None,
            "industry": None,
            "short_float": None,
            "insider_ownership": None,
            "inst_ownership": None,
        }

        try:
            self._rate_limit()
            url = f"{_QUOTE_URL}?t={ticker.upper()}&ty=c&p=d&b=1"
            resp = self._session.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("get_ticker_fundamentals(%s) request failed: %s", ticker, exc)
            return empty

        try:
            soup = BeautifulSoup(resp.text, "html.parser")
            snapshot = self._parse_snapshot_table(soup)

            result: dict[str, Any] = {
                "ticker": ticker.upper(),
                "market_cap": snapshot.get("Market Cap", None),
                "pe_ratio": self._safe_float(snapshot.get("P/E", "")),
                "sector": snapshot.get("Sector", None),
                "industry": snapshot.get("Industry", None),
                "short_float": self._parse_pct(snapshot.get("Short Float", "")),
                "insider_ownership": self._parse_pct(snapshot.get("Insider Own", "")),
                "inst_ownership": self._parse_pct(snapshot.get("Inst Own", "")),
            }
        except Exception as exc:
            logger.warning("get_ticker_fundamentals(%s) parse failed: %s", ticker, exc)
            return empty

        self._cache.set(cache_key, result)
        return result

    def scan_volume_spikes(self, tickers: list[str]) -> list[dict[str, Any]]:
        """Check which tickers currently have volume > 2x their 20-day average.

        Cross-referenced with NZT RVOL calculation (Section 6).

        Args:
            tickers: List of ticker symbols to check.

        Returns:
            List of dicts for tickers whose current RVOL >= 2.0::

                {
                    "ticker": "TSLA",
                    "rvol": 3.2,
                    "volume": 85_000_000,
                    "avg_volume": 26_500_000,
                }
        """
        if not tickers:
            return []

        cache_key = f"vol_spikes_{'_'.join(sorted(t.upper() for t in tickers))}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        # Finviz screener can filter by specific tickers using the ticker filter
        # We batch them into groups to avoid URL length issues
        results: list[dict[str, Any]] = []
        batch_size = 20
        ticker_batches = [
            tickers[i : i + batch_size]
            for i in range(0, len(tickers), batch_size)
        ]

        for batch in ticker_batches:
            ticker_str = ",".join(t.upper() for t in batch)
            params = {
                "v": "171",
                "t": ticker_str,
                "o": "-relativevolume",
            }

            rows = self._fetch_screener_table(params)
            for row in rows:
                parsed = self._parse_screener_row(row)
                if parsed is None:
                    continue
                if parsed.get("rvol", 0) >= 2.0:
                    results.append({
                        "ticker": parsed["ticker"],
                        "rvol": parsed["rvol"],
                        "volume": parsed["volume"],
                        "avg_volume": parsed["avg_volume"],
                    })

        results.sort(key=lambda r: r.get("rvol", 0), reverse=True)

        self._cache.set(cache_key, results)
        logger.info(
            "scan_volume_spikes: %d/%d tickers have RVOL >= 2.0",
            len(results),
            len(tickers),
        )
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rate_limit(self) -> None:
        """Enforce minimum delay between HTTP requests."""
        now = time.monotonic()
        elapsed = now - self._last_request_at
        if elapsed < _RATE_LIMIT_SECONDS:
            sleep_for = _RATE_LIMIT_SECONDS - elapsed
            logger.debug("Rate limiting: sleeping %.2fs", sleep_for)
            time.sleep(sleep_for)
        self._last_request_at = time.monotonic()

    def _fetch_screener_table(
        self, params: dict[str, str]
    ) -> list[Any]:
        """Fetch a Finviz screener page and return all table rows.

        Paginates through results automatically (Finviz shows 20 per page).
        Returns raw BeautifulSoup <tr> elements.
        """
        all_rows: list[Any] = []
        page_start = 1  # Finviz uses 1-based row offset (&r=1, &r=21, ...)

        while True:
            try:
                self._rate_limit()
                page_params = {**params, "r": str(page_start)}
                resp = self._session.get(
                    _BASE_URL, params=page_params, timeout=15
                )
                resp.raise_for_status()
            except requests.RequestException as exc:
                logger.warning("Finviz screener request failed (r=%d): %s", page_start, exc)
                break

            soup = BeautifulSoup(resp.text, "html.parser")

            # Finviz uses a table with id="screener-views-table" or
            # class="screener_table" depending on version.
            # The data rows are inside the main table body.
            table = soup.find("table", {"id": "screener-views-table"})
            if table is None:
                # Fallback: look for the styled table
                table = soup.find("table", class_="table-light")
            if table is None:
                # Another fallback for different Finviz layouts
                tables = soup.find_all("table")
                # The screener data table is typically one of the larger tables
                for t in tables:
                    rows = t.find_all("tr")
                    if len(rows) > 2:
                        table = t
                        break

            if table is None:
                logger.warning("Could not locate screener table on page r=%d", page_start)
                break

            rows = table.find_all("tr")
            # First row is usually the header
            data_rows = [r for r in rows[1:] if r.find_all("td")]

            if not data_rows:
                break

            all_rows.extend(data_rows)

            # Finviz shows 20 rows per page.  If we got fewer, we've
            # reached the last page.
            if len(data_rows) < 20:
                break

            page_start += 20

            # Safety limit: don't scrape more than 200 stocks
            if page_start > 200:
                break

        return all_rows

    def _parse_screener_row(self, row: Any) -> dict[str, Any] | None:
        """Parse a single <tr> from the Finviz screener table.

        The column layout for view 171 (Performance) is approximately:
          0: No.   1: Ticker   2: Perf Week   3: Perf Month   ...
        But Finviz can vary column positions.  We extract by known
        data patterns rather than fixed column indices.

        Returns None if the row cannot be parsed.
        """
        cells = row.find_all("td")
        if len(cells) < 5:
            return None

        try:
            result: dict[str, Any] = {}

            # Extract ticker — usually in a cell with an <a> link
            # containing the ticker text (1-5 uppercase letters)
            ticker_link = row.find("a", class_="screener-link-primary")
            if ticker_link is None:
                # Fallback: find first link that looks like a ticker
                for a_tag in row.find_all("a"):
                    text = a_tag.get_text(strip=True)
                    if text.isalpha() and text.isupper() and 1 <= len(text) <= 5:
                        ticker_link = a_tag
                        break
            if ticker_link is None:
                return None

            result["ticker"] = ticker_link.get_text(strip=True)

            # Extract all cell texts for pattern matching
            cell_texts = [c.get_text(strip=True) for c in cells]

            # Find numeric values by scanning cells
            result["price"] = 0.0
            result["change_pct"] = 0.0
            result["volume"] = 0
            result["avg_volume"] = 0
            result["rvol"] = 0.0
            result["gap_pct"] = 0.0
            result["market_cap"] = ""
            result["sector"] = ""

            for i, text in enumerate(cell_texts):
                # Market cap: contains B, M, or T suffix (e.g. "12.5B")
                if (
                    text
                    and text[-1] in ("B", "M", "T", "K")
                    and self._safe_float(text[:-1]) is not None
                    and not result["market_cap"]
                ):
                    result["market_cap"] = text

                # Percentage values (change_pct / gap_pct)
                if text.endswith("%"):
                    pct_val = self._safe_float(text.rstrip("%"))
                    if pct_val is not None and result["change_pct"] == 0.0:
                        result["change_pct"] = pct_val
                        result["gap_pct"] = pct_val  # Approximate gap as change

                # Volume: large integers, often with commas
                cleaned = text.replace(",", "")
                int_val = self._safe_int(cleaned)
                if int_val is not None and int_val > 100_000:
                    if result["volume"] == 0:
                        result["volume"] = int_val
                    elif result["avg_volume"] == 0:
                        result["avg_volume"] = int_val

                # Price: float between 0.5 and 50000
                float_val = self._safe_float(text)
                if (
                    float_val is not None
                    and 0.5 <= float_val <= 50_000
                    and result["price"] == 0.0
                    and not text.endswith("%")
                    and text[-1] not in ("B", "M", "T", "K")
                ):
                    result["price"] = float_val

                # Sector: known sector names
                if text in _SECTORS:
                    result["sector"] = text

                # Relative Volume: float typically 0.1 - 100
                if (
                    float_val is not None
                    and 0.1 <= float_val <= 200
                    and text[-1] not in ("B", "M", "T", "K", "%")
                    and result["rvol"] == 0.0
                    and result["price"] != 0.0  # Must come after price
                ):
                    # Heuristic: rvol cells are usually after volume
                    if result["volume"] > 0:
                        result["rvol"] = float_val

            # Compute RVOL from volume/avg_volume if not found directly
            if (
                result["rvol"] == 0.0
                and result["volume"] > 0
                and result["avg_volume"] > 0
            ):
                result["rvol"] = round(
                    result["volume"] / result["avg_volume"], 2
                )

            return result

        except Exception as exc:
            logger.debug("Failed to parse screener row: %s", exc)
            return None

    def _parse_snapshot_table(self, soup: BeautifulSoup) -> dict[str, str]:
        """Parse the Finviz ticker snapshot table into a flat dict.

        The snapshot page has a table of key-value pairs laid out as:
          Label | Value | Label | Value | ...
        across multiple rows.
        """
        snapshot: dict[str, str] = {}

        # The fundamentals table has class "snapshot-table2" or similar
        table = soup.find("table", class_="snapshot-table2")
        if table is None:
            # Fallback: look for table containing known labels
            for t in soup.find_all("table"):
                if t.find(string="Market Cap"):
                    table = t
                    break

        if table is None:
            return snapshot

        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            # Cells alternate: label, value, label, value, ...
            for i in range(0, len(cells) - 1, 2):
                label = cells[i].get_text(strip=True)
                value = cells[i + 1].get_text(strip=True)
                if label:
                    snapshot[label] = value

        return snapshot

    # ------------------------------------------------------------------
    # Value parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_float(text: str) -> float | None:
        """Parse a float from text, returning None on failure."""
        if not text:
            return None
        try:
            return float(text.replace(",", ""))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_int(text: str) -> int | None:
        """Parse an int from text, returning None on failure."""
        if not text:
            return None
        try:
            return int(text.replace(",", ""))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_pct(text: str) -> float | None:
        """Parse a percentage string like '5.50%' -> 5.50."""
        if not text or text == "-":
            return None
        try:
            return float(text.rstrip("%").replace(",", ""))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _market_cap_to_float(cap_str: str) -> float:
        """Convert market cap string like '12.5B' to raw float (12_500_000_000).

        Returns 0.0 if unparsable.
        """
        if not cap_str:
            return 0.0
        cap_str = cap_str.strip()
        multipliers = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
        suffix = cap_str[-1].upper()
        if suffix in multipliers:
            try:
                return float(cap_str[:-1]) * multipliers[suffix]
            except ValueError:
                return 0.0
        # No suffix — try raw number
        try:
            return float(cap_str.replace(",", ""))
        except ValueError:
            return 0.0

    @staticmethod
    def _gap_pct_to_filter(min_gap_pct: float) -> str:
        """Map a minimum gap percentage to the closest Finviz filter token.

        Finviz gap/change filters:
          ta_change_u    = Up
          ta_change_u1   = Up > 1%
          ta_change_u2   = Up > 2%
          ta_change_u3   = Up > 3%
          ta_change_u4   = Up > 4%
          ta_change_u5   = Up > 5%
          ta_change_u10  = Up > 10%
          ta_change_u20  = Up > 20%
        """
        thresholds = [
            (20.0, "ta_change_u20"),
            (10.0, "ta_change_u10"),
            (5.0, "ta_change_u5"),
            (4.0, "ta_change_u4"),
            (3.0, "ta_change_u3"),
            (2.0, "ta_change_u2"),
            (1.0, "ta_change_u1"),
            (0.0, "ta_change_u"),
        ]
        for threshold, token in thresholds:
            if min_gap_pct >= threshold:
                return token
        return "ta_change_u"


# ---------------------------------------------------------------------------
# Known Finviz sectors for pattern matching
# ---------------------------------------------------------------------------

_SECTORS: set[str] = {
    "Technology",
    "Healthcare",
    "Financial",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Industrials",
    "Energy",
    "Basic Materials",
    "Real Estate",
    "Utilities",
    "Communication Services",
}
