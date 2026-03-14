"""
NZT-48 Trading System — News Feed
Section 54 and Layer 5 (Narrative): News/catalyst detection.

Provides real-time news retrieval, catalyst classification, crisis-keyword
detection, and a narrative confidence score for the 5-layer scoring model.

Environment variables:
    NEWSAPI_API_KEY  — newsapi.org API key (free: 100 req/day)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

try:
    import lxml  # noqa: F401 — required by BeautifulSoup "xml" parser
except ImportError:
    logging.getLogger(__name__).warning("lxml not installed — BS4 XML parser may fail")

logger = logging.getLogger("nzt48.feeds.news")

# ---------------------------------------------------------------------------
# Cache layer — simple in-memory dict with TTL
# ---------------------------------------------------------------------------
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL_SECONDS = 1800  # 30 minutes


def _cache_get(key: str) -> Any | None:
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
# 429 backoff state — module-level so all NewsFeed instances share it
# ---------------------------------------------------------------------------
_last_429_time: float = 0.0
_BACKOFF_429_SECONDS: int = 60  # back off for 60s after a 429

# Per-call rate limiting — minimum gap between NewsAPI requests
_last_newsapi_call_time: float = 0.0
_MIN_CALL_INTERVAL: float = 5.0  # seconds between requests


# ---------------------------------------------------------------------------
# Keyword / classification constants
# ---------------------------------------------------------------------------

CRISIS_KEYWORDS: list[str] = [
    "crash",
    "collapse",
    "bankruptcy",
    "fraud",
    "sec investigation",
    "halt",
    "delisted",
    "margin call",
    "liquidation",
]

_CATALYST_PATTERNS: dict[str, list[str]] = {
    "earnings_beat": [
        r"earnings\s+beat", r"beats\s+estimates", r"tops\s+expectations",
        r"profit\s+surges", r"revenue\s+beats", r"blowout\s+quarter",
        r"record\s+earnings",
    ],
    "earnings_miss": [
        r"earnings\s+miss", r"misses\s+estimates", r"below\s+expectations",
        r"profit\s+drops", r"revenue\s+misses", r"disappointing\s+quarter",
        r"earnings\s+decline",
    ],
    "upgrade": [
        r"upgrade[ds]?", r"price\s+target\s+raise", r"raises?\s+price\s+target",
        r"overweight", r"outperform",
    ],
    "downgrade": [
        r"downgrade[ds]?", r"price\s+target\s+cut", r"cuts?\s+price\s+target",
        r"underweight", r"underperform",
    ],
    "product_launch": [
        r"launches?", r"new\s+product", r"unveils?", r"announces?\s+new",
        r"release[ds]?", r"rollout",
    ],
    "regulatory": [
        r"fda\s+approv", r"sec\s+filing", r"regulatory\s+approv",
        r"investigation", r"lawsuit", r"settlement", r"antitrust",
        r"compliance",
    ],
    "macro": [
        r"fed\s+rate", r"inflation", r"interest\s+rate", r"tariff",
        r"recession", r"stimulus", r"gdp\s+growth", r"employment\s+data",
    ],
    "sector": [
        r"sector\s+rotation", r"industry\s+trend", r"peers?\s+rally",
        r"sector\s+sell-?off", r"chip\s+shortage", r"supply\s+chain",
    ],
}

# Pre-compile patterns for speed
_COMPILED_CATALYST_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    cat: [re.compile(p, re.IGNORECASE) for p in patterns]
    for cat, patterns in _CATALYST_PATTERNS.items()
}

_COMPILED_CRISIS: list[re.Pattern[str]] = [
    re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE) for kw in CRISIS_KEYWORDS
]

# Simple headline sentiment keywords
_POSITIVE_WORDS: set[str] = {
    "beats", "surges", "soars", "rallies", "jumps", "gains", "upgrade",
    "outperform", "record", "breakthrough", "strong", "bullish", "boost",
    "raises", "approval", "positive", "growth", "momentum", "optimism",
    "recovery",
}
_NEGATIVE_WORDS: set[str] = {
    "misses", "drops", "falls", "plunges", "sinks", "decline", "downgrade",
    "underperform", "weak", "bearish", "cuts", "warning", "concern",
    "disappointing", "slump", "loss", "fears", "risk", "negative",
    "selloff", "sell-off",
}


def _headline_sentiment(title: str) -> str:
    """Quick keyword-based sentiment: positive / negative / neutral."""
    words = set(title.lower().split())
    pos = len(words & _POSITIVE_WORDS)
    neg = len(words & _NEGATIVE_WORDS)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


# ---------------------------------------------------------------------------
# RSS helpers
# ---------------------------------------------------------------------------

def _parse_rss_items(xml_text: str, ticker: str, hours: int) -> list[dict[str, Any]]:
    """Parse an RSS XML feed and return articles mentioning *ticker*."""
    results: list[dict[str, Any]] = []
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    soup = BeautifulSoup(xml_text, "xml")

    for item in soup.find_all("item"):
        try:
            title = item.find("title")
            title_text = title.get_text(strip=True) if title else ""
            # Only keep items that mention the ticker
            if ticker.upper() not in title_text.upper():
                description = item.find("description")
                desc_text = description.get_text(strip=True) if description else ""
                if ticker.upper() not in desc_text.upper():
                    continue

            pub_date_el = item.find("pubDate")
            pub_str = pub_date_el.get_text(strip=True) if pub_date_el else ""
            pub_date = _parse_rss_date(pub_str)
            if pub_date and pub_date < cutoff:
                continue

            link = item.find("link")
            link_text = link.get_text(strip=True) if link else ""
            source_el = item.find("source")
            source_text = source_el.get_text(strip=True) if source_el else "RSS"

            results.append({
                "title": title_text,
                "source": source_text,
                "published": pub_str,
                "url": link_text,
                "sentiment": _headline_sentiment(title_text),
            })
        except Exception as exc:
            logger.debug("RSS item parse error: %s", exc)
            continue

    return results


def _parse_rss_date(date_str: str) -> datetime | None:
    """Best-effort parse of RSS pubDate (RFC 822 and ISO 8601)."""
    if not date_str:
        return None
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# NewsFeed
# ---------------------------------------------------------------------------

class NewsFeed:
    """News retrieval and catalyst / sentiment analysis.

    Layer 5 of the 5-layer confidence model.  Combines newsapi.org
    headlines with Yahoo Finance and Seeking Alpha RSS fallbacks.
    """

    NEWSAPI_BASE = "https://newsapi.org/v2"

    # Public RSS feeds for fallback
    _RSS_FEEDS: dict[str, str] = {
        "yahoo": "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
        "seeking_alpha": "https://seekingalpha.com/api/sa/combined/{ticker}.xml",
    }

    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }

    def __init__(self, newsapi_key: str | None = None) -> None:
        self._newsapi_key = newsapi_key or os.environ.get("NEWSAPI_API_KEY", "")
        if not self._newsapi_key:
            logger.warning("NEWSAPI_API_KEY not set — will rely on RSS fallback")

    # ------------------------------------------------------------------
    # 1. Ticker news retrieval
    # ------------------------------------------------------------------

    def get_ticker_news(
        self,
        ticker: str,
        hours: int = 24,
    ) -> list[dict[str, Any]]:
        """Fetch recent news articles for *ticker*.

        Uses newsapi.org ``/everything`` as the primary source.
        Falls back to Yahoo Finance and Seeking Alpha RSS feeds.

        Args:
            ticker: Equity symbol (e.g. ``"AAPL"``).
            hours: Look-back window in hours.  Default 24.

        Returns:
            List of dicts: {title, source, published, url, sentiment}.
        """
        cache_key = f"news:{ticker}:{hours}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        articles = self._news_from_newsapi(ticker, hours)

        # Fallback to RSS if newsapi returned nothing
        if not articles:
            articles = self._news_from_rss(ticker, hours)

        _cache_set(cache_key, articles)
        return articles

    def _news_from_newsapi(self, ticker: str, hours: int) -> list[dict[str, Any]]:
        """Query newsapi.org /everything for ticker headlines."""
        global _last_429_time, _last_newsapi_call_time

        if not self._newsapi_key:
            return []

        # Check 429 backoff — don't even try if we're in cooldown
        if time.time() - _last_429_time < _BACKOFF_429_SECONDS:
            logger.debug(
                "NewsAPI 429 backoff active — skipping %s (%.0fs remaining)",
                ticker,
                _BACKOFF_429_SECONDS - (time.time() - _last_429_time),
            )
            return []

        # Per-call rate limiting — wait until minimum interval has passed
        elapsed = time.time() - _last_newsapi_call_time
        if elapsed < _MIN_CALL_INTERVAL:
            sleep_time = _MIN_CALL_INTERVAL - elapsed
            logger.debug("Rate limiting: sleeping %.1fs before NewsAPI call for %s", sleep_time, ticker)
            # Rate limiting sleep — sync context, blocking is intentional.
            # Previous run_in_executor without await was a no-op (fire-and-forget).
            time.sleep(sleep_time)

        from_dt = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
        results: list[dict[str, Any]] = []

        try:
            _last_newsapi_call_time = time.time()
            resp = requests.get(
                f"{self.NEWSAPI_BASE}/everything",
                params={
                    "q": f'"{ticker}" OR "{ticker} stock"',
                    "from": from_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "sortBy": "publishedAt",
                    "language": "en",
                    "pageSize": 20,
                    "apiKey": self._newsapi_key,
                },
                timeout=15,
            )

            # Explicit 429 handling — don't raise, just back off
            if resp.status_code == 429:
                _last_429_time = time.time()
                logger.warning(
                    "NewsAPI rate limited (429) on ticker %s. "
                    "Backing off for %ds. Remaining calls will use RSS fallback.",
                    ticker,
                    _BACKOFF_429_SECONDS,
                )
                return []

            resp.raise_for_status()
            data = resp.json()

            for article in data.get("articles", []):
                title = article.get("title", "")
                results.append({
                    "title": title,
                    "source": article.get("source", {}).get("name", ""),
                    "published": article.get("publishedAt", ""),
                    "url": article.get("url", ""),
                    "sentiment": _headline_sentiment(title),
                })
        except requests.RequestException as exc:
            logger.warning("newsapi.org call failed for %s: %s", ticker, exc)
        except (KeyError, ValueError) as exc:
            logger.warning("newsapi.org parse error for %s: %s", ticker, exc)

        return results

    def get_ticker_news_batch(
        self,
        tickers: list[str],
        hours: int = 24,
        batch_size: int = 5,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch news for multiple tickers efficiently using batched queries.

        Instead of one API call per ticker, groups tickers into batches and
        uses NewsAPI's OR query syntax to fetch several at once.  Results are
        split back to per-ticker lists and cached individually.

        Args:
            tickers: List of equity symbols.
            hours: Look-back window in hours.
            batch_size: Number of tickers per API call (default 5).

        Returns:
            Dict mapping ticker -> list of article dicts.
        """
        global _last_429_time, _last_newsapi_call_time

        result: dict[str, list[dict[str, Any]]] = {}

        # First, collect tickers that already have cached results
        uncached: list[str] = []
        for ticker in tickers:
            cache_key = f"news:{ticker}:{hours}"
            cached = _cache_get(cache_key)
            if cached is not None:
                result[ticker] = cached
            else:
                uncached.append(ticker)

        if not uncached:
            return result

        # Batch the uncached tickers into groups
        batches = [uncached[i:i + batch_size] for i in range(0, len(uncached), batch_size)]

        for batch in batches:
            # Check 429 backoff
            if time.time() - _last_429_time < _BACKOFF_429_SECONDS:
                logger.debug("NewsAPI 429 backoff active — falling back to RSS for batch %s", batch)
                for ticker in batch:
                    articles = self._news_from_rss(ticker, hours)
                    _cache_set(f"news:{ticker}:{hours}", articles)
                    result[ticker] = articles
                continue

            if not self._newsapi_key:
                # No API key — go straight to RSS
                for ticker in batch:
                    articles = self._news_from_rss(ticker, hours)
                    _cache_set(f"news:{ticker}:{hours}", articles)
                    result[ticker] = articles
                continue

            # Rate limiting
            elapsed = time.time() - _last_newsapi_call_time
            if elapsed < _MIN_CALL_INTERVAL:
                sleep_time = _MIN_CALL_INTERVAL - elapsed
                logger.debug("Rate limiting: sleeping %.1fs before batched NewsAPI call", sleep_time)
                # C-18 fix: avoid blocking the event loop in async context
                try:
                    loop = asyncio.get_running_loop()
                    loop.run_in_executor(None, time.sleep, sleep_time)
                except RuntimeError:
                    time.sleep(sleep_time)

            # Build combined query: "AAPL" OR "TSLA" OR "MSFT" ...
            query_parts = [f'"{t}"' for t in batch]
            combined_query = " OR ".join(query_parts)
            from_dt = datetime.now(tz=timezone.utc) - timedelta(hours=hours)

            try:
                _last_newsapi_call_time = time.time()
                resp = requests.get(
                    f"{self.NEWSAPI_BASE}/everything",
                    params={
                        "q": combined_query,
                        "from": from_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                        "sortBy": "publishedAt",
                        "language": "en",
                        "pageSize": 100,
                        "apiKey": self._newsapi_key,
                    },
                    timeout=15,
                )

                if resp.status_code == 429:
                    _last_429_time = time.time()
                    logger.warning(
                        "NewsAPI rate limited (429) on batch %s. Backing off %ds.",
                        batch, _BACKOFF_429_SECONDS,
                    )
                    # Fall back to RSS for this batch
                    for ticker in batch:
                        articles = self._news_from_rss(ticker, hours)
                        _cache_set(f"news:{ticker}:{hours}", articles)
                        result[ticker] = articles
                    continue

                resp.raise_for_status()
                data = resp.json()

                # Distribute articles to their respective tickers
                per_ticker: dict[str, list[dict[str, Any]]] = {t: [] for t in batch}
                for article in data.get("articles", []):
                    title = (article.get("title") or "").upper()
                    description = (article.get("description") or "").upper()
                    for ticker in batch:
                        t_upper = ticker.upper().rstrip(".L")  # Strip .L suffix for matching
                        if t_upper in title or t_upper in description:
                            per_ticker[ticker].append({
                                "title": article.get("title", ""),
                                "source": article.get("source", {}).get("name", ""),
                                "published": article.get("publishedAt", ""),
                                "url": article.get("url", ""),
                                "sentiment": _headline_sentiment(article.get("title", "")),
                            })

                # Cache and store results; fall back to RSS for tickers with no hits
                for ticker in batch:
                    articles = per_ticker[ticker]
                    if not articles:
                        articles = self._news_from_rss(ticker, hours)
                    _cache_set(f"news:{ticker}:{hours}", articles)
                    result[ticker] = articles

            except requests.RequestException as exc:
                logger.warning("NewsAPI batch call failed: %s — falling back to RSS", exc)
                for ticker in batch:
                    articles = self._news_from_rss(ticker, hours)
                    _cache_set(f"news:{ticker}:{hours}", articles)
                    result[ticker] = articles

        return result

    def _news_from_rss(self, ticker: str, hours: int) -> list[dict[str, Any]]:
        """Fallback: fetch from Yahoo Finance and Seeking Alpha RSS."""
        all_articles: list[dict[str, Any]] = []

        for feed_name, url_template in self._RSS_FEEDS.items():
            url = url_template.format(ticker=ticker)
            try:
                resp = requests.get(url, headers=self._HEADERS, timeout=15)
                resp.raise_for_status()
                articles = _parse_rss_items(resp.text, ticker, hours)
                all_articles.extend(articles)
            except requests.RequestException as exc:
                logger.debug("RSS feed %s for %s failed: %s", feed_name, ticker, exc)

        return all_articles

    # ------------------------------------------------------------------
    # 2. Catalyst detection
    # ------------------------------------------------------------------

    def detect_catalyst(self, ticker: str) -> dict[str, Any]:
        """Detect whether a material catalyst exists for *ticker*.

        Runs keyword/pattern matching on recent headlines to classify
        the catalyst type.

        Returns:
            Dict: {detected: bool, type: str, headline: str, sentiment: str}.
        """
        articles = self.get_ticker_news(ticker, hours=24)
        if not articles:
            return {
                "detected": False,
                "type": "",
                "headline": "",
                "sentiment": "neutral",
            }

        # Check each headline against catalyst patterns (priority order)
        for article in articles:
            title = article.get("title", "")
            for cat_type, patterns in _COMPILED_CATALYST_PATTERNS.items():
                for pattern in patterns:
                    if pattern.search(title):
                        return {
                            "detected": True,
                            "type": cat_type,
                            "headline": title,
                            "sentiment": article.get("sentiment", "neutral"),
                        }

        # No specific catalyst pattern matched — check if there is
        # volume of news (>= 5 articles in 24h = something is happening)
        if len(articles) >= 5:
            # Determine aggregate sentiment
            sentiments = [a.get("sentiment", "neutral") for a in articles]
            pos = sentiments.count("positive")
            neg = sentiments.count("negative")
            agg_sentiment = "positive" if pos > neg else ("negative" if neg > pos else "neutral")
            return {
                "detected": True,
                "type": "sector",  # general buzz
                "headline": articles[0].get("title", ""),
                "sentiment": agg_sentiment,
            }

        return {
            "detected": False,
            "type": "",
            "headline": "",
            "sentiment": "neutral",
        }

    # ------------------------------------------------------------------
    # 3. Crisis keyword detection
    # ------------------------------------------------------------------

    def check_crisis_keywords(self, headlines: list[str]) -> bool:
        """Return True if any headline contains a crisis keyword.

        Crisis keywords: crash, collapse, bankruptcy, fraud,
        SEC investigation, halt, delisted, margin call, liquidation.

        When True, the confidence model applies a -50 VETO.
        """
        for headline in headlines:
            for pattern in _COMPILED_CRISIS:
                if pattern.search(headline):
                    logger.warning(
                        "Crisis keyword detected in headline: %s (pattern: %s)",
                        headline,
                        pattern.pattern,
                    )
                    return True
        return False

    # ------------------------------------------------------------------
    # 4. Narrative score (Layer 5)
    # ------------------------------------------------------------------

    def get_narrative_score(self, ticker: str) -> int:
        """Compute the Layer-5 narrative confidence score for *ticker*.

        Scoring rules:
            +8  — Positive ticker-specific news
            +5  — Positive sector/macro news (no ticker-specific)
           -10  — Negative news
           -50  — Crisis keyword detected (VETO)

        The score is capped to the range [-50, +8].

        Args:
            ticker: Equity symbol.

        Returns:
            Integer score in [-50, +8].
        """
        articles = self.get_ticker_news(ticker, hours=24)
        if not articles:
            return 0  # No news = neutral

        headlines = [a.get("title", "") for a in articles]

        # Crisis check first — immediate VETO
        if self.check_crisis_keywords(headlines):
            logger.info("Narrative score for %s: -50 (CRISIS VETO)", ticker)
            return -50

        # Classify sentiment across all articles
        sentiments = [a.get("sentiment", "neutral") for a in articles]
        pos_count = sentiments.count("positive")
        neg_count = sentiments.count("negative")

        # Detect if the news is ticker-specific or sector-level
        catalyst = self.detect_catalyst(ticker)
        is_ticker_specific = catalyst["detected"] and catalyst["type"] in (
            "earnings_beat", "earnings_miss", "upgrade", "downgrade",
            "product_launch", "regulatory",
        )

        # Negative news dominates
        if neg_count > pos_count:
            score = -10
            logger.debug("Narrative score for %s: -10 (negative news)", ticker)
            return score

        # Positive ticker-specific news
        if pos_count > neg_count and is_ticker_specific:
            score = 8
            logger.debug("Narrative score for %s: +8 (positive ticker news)", ticker)
            return score

        # Positive but sector-level only
        if pos_count > neg_count:
            score = 5
            logger.debug("Narrative score for %s: +5 (positive sector news)", ticker)
            return score

        # Balanced or no clear signal
        return 0
