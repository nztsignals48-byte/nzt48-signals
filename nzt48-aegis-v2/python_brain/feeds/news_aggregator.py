"""Institutional News & Sentiment Aggregator — Same-time-as-hedge-funds pipeline.

Multi-source news ingestion with real-time NLP sentiment scoring.
Feeds into bridge.py signal generation as a confidence modifier.

Sources (priority order):
  1. Benzinga Pro API — real-time financial news (institutional-grade)
  2. Unusual Whales — dark pool flow, options flow, Congress trades
  3. EODHD — news + pre-scored sentiment
  4. SEC EDGAR RSS — insider trades, 8-K filings
  5. Federal Reserve RSS — FOMC statements, Beige Book
  6. Finviz RSS — market headlines (free, fast)

Architecture:
  - Each source runs in its own thread
  - News items normalized to NewsItem dataclass
  - Gemini 2.5 Flash scores sentiment in batch (0.2s/item, ~$0/month)
  - Scored items pushed to Redis pubsub + written to disk
  - bridge.py reads latest sentiment for each ticker before signal generation

Config: env vars BENZINGA_API_KEY, UW_API_KEY, EODHD_API_KEY, GEMINI_API_KEY
Books: 72 (AI as filter), 198 (LLM bullish bias), 216 (regime awareness)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

log = logging.getLogger("news_aggregator")

# Deduplication window (seconds) — same headline within this window is dropped
DEDUP_WINDOW_S = 600  # 10 minutes


@dataclass
class NewsItem:
    """Normalized news item from any source."""
    headline: str
    source: str                     # benzinga, uw, eodhd, sec, fed, finviz
    timestamp: float                # Unix timestamp
    symbols: List[str] = field(default_factory=list)
    url: str = ""
    body: str = ""                  # First 500 chars of article body
    category: str = ""              # earnings, fda, macro, insider, darkpool, flow
    # Sentiment (scored by Gemini or pre-scored)
    sentiment_score: float = 0.0    # -1.0 (bearish) to +1.0 (bullish)
    sentiment_confidence: float = 0.0  # 0.0 to 1.0
    sentiment_model: str = ""       # gemini-2.5-flash, eodhd, manual
    # Flow data (Unusual Whales specific)
    flow_type: str = ""             # darkpool, options, congress
    flow_size_usd: float = 0.0
    flow_direction: str = ""        # bullish, bearish, neutral
    # Metadata
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "headline": self.headline,
            "source": self.source,
            "timestamp": self.timestamp,
            "symbols": self.symbols,
            "url": self.url,
            "category": self.category,
            "sentiment_score": round(self.sentiment_score, 3),
            "sentiment_confidence": round(self.sentiment_confidence, 3),
            "sentiment_model": self.sentiment_model,
            "flow_type": self.flow_type,
            "flow_size_usd": self.flow_size_usd,
            "flow_direction": self.flow_direction,
        }

    @property
    def dedup_key(self) -> str:
        """Hash for deduplication."""
        content = f"{self.headline}:{','.join(sorted(self.symbols))}:{self.source}"
        return hashlib.md5(content.encode()).hexdigest()


class NewsAggregator:
    """Multi-source news aggregator with real-time sentiment scoring.

    Usage:
        agg = NewsAggregator()
        agg.on_news = my_callback
        agg.start()  # starts all source threads

        # From bridge.py:
        sentiment = agg.get_ticker_sentiment("AAPL")
    """

    def __init__(self, data_dir: str = "/app/data/news"):
        self._data_dir = Path(data_dir)
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._running = False
        self._threads: List[threading.Thread] = []
        # Per-ticker sentiment cache: symbol -> deque of recent NewsItems
        self._ticker_news: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        # Global news feed (most recent 500)
        self._global_feed: deque = deque(maxlen=500)
        # Dedup set
        self._seen: Dict[str, float] = {}  # dedup_key -> timestamp
        self._lock = threading.Lock()
        # Stats
        self._stats = defaultdict(int)
        # Callbacks
        self.on_news: Optional[Callable[[NewsItem], None]] = None
        # Redis
        self._redis = None
        # Gemini scorer
        self._gemini_enabled = bool(os.environ.get("GEMINI_API_KEY", ""))
        self._sentiment_queue: deque = deque(maxlen=100)
        self._sentiment_thread: Optional[threading.Thread] = None

    def start(self):
        """Start all source threads."""
        self._running = True

        sources = [
            ("benzinga", self._poll_benzinga, 15),   # 15s poll interval
            ("uw-flow", self._poll_unusual_whales_flow, 30),
            ("uw-congress", self._poll_unusual_whales_congress, 3600),
            ("eodhd", self._poll_eodhd, 60),
            ("sec-edgar", self._poll_sec_edgar, 120),
            ("fed-rss", self._poll_fed_rss, 300),
            ("finviz", self._poll_finviz, 30),
        ]

        for name, fn, interval in sources:
            t = threading.Thread(target=self._source_loop, args=(name, fn, interval), daemon=True, name=f"news-{name}")
            t.start()
            self._threads.append(t)

        # Gemini batch sentiment scorer
        if self._gemini_enabled:
            self._sentiment_thread = threading.Thread(target=self._sentiment_scorer_loop, daemon=True, name="news-sentiment")
            self._sentiment_thread.start()

        # Dedup cleaner
        t = threading.Thread(target=self._dedup_cleaner, daemon=True, name="news-dedup")
        t.start()
        self._threads.append(t)

        log.info("News aggregator started (%d sources, gemini=%s)", len(sources), self._gemini_enabled)

    def stop(self):
        """Stop all source threads."""
        self._running = False
        for t in self._threads:
            t.join(timeout=3)
        log.info("News aggregator stopped. Stats: %s", dict(self._stats))

    def get_ticker_sentiment(self, symbol: str, window_s: float = 3600) -> Dict[str, Any]:
        """Get aggregated sentiment for a ticker over recent window.

        Returns dict with:
          - score: weighted average sentiment (-1 to +1)
          - confidence: average confidence
          - count: number of news items
          - latest_headline: most recent headline
          - bullish_count, bearish_count, neutral_count
          - has_flow: whether dark pool/options flow data exists
          - flow_direction: net flow direction
        """
        now = time.time()
        with self._lock:
            items = [n for n in self._ticker_news.get(symbol.upper(), []) if now - n.timestamp < window_s]

        if not items:
            return {"score": 0.0, "confidence": 0.0, "count": 0, "latest_headline": "", "has_flow": False}

        # Time-weighted sentiment (recent items weighted more)
        total_weight = 0.0
        weighted_score = 0.0
        bullish = bearish = neutral = 0
        has_flow = False
        flow_bullish = flow_bearish = 0

        for item in items:
            age = max(1, now - item.timestamp)
            weight = 1.0 / (1 + age / 300)  # Exponential decay over 5 min
            # Flow data weighted 2x (institutional signal)
            if item.flow_type:
                weight *= 2.0
                has_flow = True
                if item.flow_direction == "bullish":
                    flow_bullish += 1
                elif item.flow_direction == "bearish":
                    flow_bearish += 1

            weighted_score += item.sentiment_score * weight * item.sentiment_confidence
            total_weight += weight * item.sentiment_confidence

            if item.sentiment_score > 0.1:
                bullish += 1
            elif item.sentiment_score < -0.1:
                bearish += 1
            else:
                neutral += 1

        score = weighted_score / total_weight if total_weight > 0 else 0.0
        avg_conf = sum(i.sentiment_confidence for i in items) / len(items)
        latest = max(items, key=lambda x: x.timestamp)

        flow_dir = "neutral"
        if flow_bullish > flow_bearish * 1.5:
            flow_dir = "bullish"
        elif flow_bearish > flow_bullish * 1.5:
            flow_dir = "bearish"

        return {
            "score": round(score, 3),
            "confidence": round(avg_conf, 3),
            "count": len(items),
            "latest_headline": latest.headline[:100],
            "bullish_count": bullish,
            "bearish_count": bearish,
            "neutral_count": neutral,
            "has_flow": has_flow,
            "flow_direction": flow_dir,
        }

    def get_market_sentiment(self, window_s: float = 1800) -> Dict[str, Any]:
        """Get overall market sentiment from all sources."""
        now = time.time()
        with self._lock:
            items = [n for n in self._global_feed if now - n.timestamp < window_s]
        if not items:
            return {"score": 0.0, "count": 0, "mood": "neutral"}
        avg = sum(i.sentiment_score for i in items) / len(items)
        mood = "bullish" if avg > 0.15 else "bearish" if avg < -0.15 else "neutral"
        return {
            "score": round(avg, 3),
            "count": len(items),
            "mood": mood,
            "sources": dict(self._stats),
        }

    def health_dict(self) -> dict:
        """Health status for monitoring."""
        return {
            "running": self._running,
            "sources": dict(self._stats),
            "global_feed_size": len(self._global_feed),
            "tracked_tickers": len(self._ticker_news),
            "dedup_cache_size": len(self._seen),
            "gemini_enabled": self._gemini_enabled,
            "sentiment_queue_size": len(self._sentiment_queue),
        }

    # ── Ingest ──

    def _ingest(self, item: NewsItem):
        """Deduplicate, score, cache, and emit a news item."""
        # Dedup
        key = item.dedup_key
        with self._lock:
            if key in self._seen:
                return
            self._seen[key] = time.time()

        self._stats[item.source] += 1
        self._stats["total"] += 1

        # Queue for sentiment scoring if no pre-score
        if item.sentiment_score == 0.0 and self._gemini_enabled:
            self._sentiment_queue.append(item)
        else:
            self._finalize(item)

    def _finalize(self, item: NewsItem):
        """Cache and emit a scored news item."""
        with self._lock:
            self._global_feed.append(item)
            for sym in item.symbols:
                self._ticker_news[sym.upper()].append(item)

        # Persist to disk (NDJSON)
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = self._data_dir / f"news_{today}.ndjson"
            with open(path, "a") as f:
                f.write(json.dumps(item.to_dict()) + "\n")
        except Exception as e:
            log.debug("News persist error: %s", e)

        # Redis publish
        if self._redis:
            try:
                self._redis.publish("news:items", json.dumps(item.to_dict()))
            except Exception:
                pass

        # Callback
        if self.on_news:
            try:
                self.on_news(item)
            except Exception as e:
                log.error("News callback error: %s", e)

    # ── Source Polling ──

    def _source_loop(self, name: str, fn: Callable, interval_s: float):
        """Generic polling loop for a news source."""
        log.info("News source '%s' starting (interval=%ds)", name, interval_s)
        while self._running:
            try:
                fn()
            except Exception as e:
                log.warning("News source '%s' error: %s", name, e)
                self._stats[f"{name}_errors"] += 1
            time.sleep(interval_s)

    def _poll_benzinga(self):
        """Poll Benzinga Pro News API for real-time financial news."""
        api_key = os.environ.get("BENZINGA_API_KEY", "")
        if not api_key:
            return

        import requests
        url = "https://api.benzinga.com/api/v2/news"
        params = {
            "token": api_key,
            "pageSize": 20,
            "displayOutput": "full",
            "sort": "created:desc",
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        articles = resp.json()

        for article in articles:
            symbols = [s.get("name", "") for s in article.get("stocks", [])]
            self._ingest(NewsItem(
                headline=article.get("title", ""),
                source="benzinga",
                timestamp=_parse_timestamp(article.get("created", "")),
                symbols=symbols,
                url=article.get("url", ""),
                body=article.get("body", "")[:500],
                category=_classify_category(article.get("title", ""), article.get("channels", [])),
            ))

    def _poll_unusual_whales_flow(self):
        """Poll Unusual Whales for dark pool + options flow."""
        api_key = os.environ.get("UW_API_KEY", "")
        if not api_key:
            return

        import requests
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

        # Dark pool flow
        try:
            resp = requests.get("https://api.unusualwhales.com/api/darkpool/recent", headers=headers, timeout=10)
            if resp.status_code == 200:
                for trade in resp.json().get("data", [])[:20]:
                    size = float(trade.get("volume", 0)) * float(trade.get("price", 0))
                    direction = "bullish" if trade.get("type") == "buy" else "bearish" if trade.get("type") == "sell" else "neutral"
                    self._ingest(NewsItem(
                        headline=f"Dark pool: {trade.get('ticker', '?')} ${size:,.0f} {direction}",
                        source="uw",
                        timestamp=time.time(),
                        symbols=[trade.get("ticker", "")],
                        category="darkpool",
                        flow_type="darkpool",
                        flow_size_usd=size,
                        flow_direction=direction,
                        sentiment_score=0.3 if direction == "bullish" else -0.3 if direction == "bearish" else 0.0,
                        sentiment_confidence=min(0.8, size / 1_000_000),  # Higher confidence for bigger trades
                        sentiment_model="flow-heuristic",
                    ))
        except Exception as e:
            log.debug("UW darkpool error: %s", e)

        # Options flow
        try:
            resp = requests.get("https://api.unusualwhales.com/api/stock/flow/recent", headers=headers, timeout=10)
            if resp.status_code == 200:
                for flow in resp.json().get("data", [])[:20]:
                    premium = float(flow.get("premium", 0))
                    is_call = flow.get("option_type", "").lower() == "call"
                    direction = "bullish" if is_call else "bearish"
                    self._ingest(NewsItem(
                        headline=f"Options flow: {flow.get('ticker', '?')} {'call' if is_call else 'put'} ${premium:,.0f}",
                        source="uw",
                        timestamp=time.time(),
                        symbols=[flow.get("ticker", "")],
                        category="flow",
                        flow_type="options",
                        flow_size_usd=premium,
                        flow_direction=direction,
                        sentiment_score=0.25 if direction == "bullish" else -0.25,
                        sentiment_confidence=min(0.7, premium / 500_000),
                        sentiment_model="flow-heuristic",
                    ))
        except Exception as e:
            log.debug("UW options flow error: %s", e)

    def _poll_unusual_whales_congress(self):
        """Poll Unusual Whales for Congressional trading data."""
        api_key = os.environ.get("UW_API_KEY", "")
        if not api_key:
            return

        import requests
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        try:
            resp = requests.get("https://api.unusualwhales.com/api/congress/recent", headers=headers, timeout=10)
            if resp.status_code == 200:
                for trade in resp.json().get("data", [])[:10]:
                    is_buy = trade.get("transaction_type", "").lower() in ("purchase", "buy")
                    self._ingest(NewsItem(
                        headline=f"Congress: {trade.get('representative', '?')} {'bought' if is_buy else 'sold'} {trade.get('ticker', '?')}",
                        source="uw",
                        timestamp=time.time(),
                        symbols=[trade.get("ticker", "")],
                        category="insider",
                        flow_type="congress",
                        flow_direction="bullish" if is_buy else "bearish",
                        sentiment_score=0.2 if is_buy else -0.15,
                        sentiment_confidence=0.4,
                        sentiment_model="flow-heuristic",
                    ))
        except Exception as e:
            log.debug("UW congress error: %s", e)

    def _poll_eodhd(self):
        """Poll EODHD for pre-scored news with sentiment."""
        api_key = os.environ.get("EODHD_API_KEY", "")
        if not api_key:
            return

        import requests
        url = f"https://eodhd.com/api/news?api_token={api_key}&limit=20&fmt=json"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()

        for article in resp.json():
            sentiment = article.get("sentiment", {})
            score = float(sentiment.get("polarity", 0))
            self._ingest(NewsItem(
                headline=article.get("title", ""),
                source="eodhd",
                timestamp=_parse_timestamp(article.get("date", "")),
                symbols=article.get("symbols", []) or [],
                url=article.get("link", ""),
                category=_classify_category(article.get("title", ""), []),
                sentiment_score=score,
                sentiment_confidence=0.5,  # EODHD pre-scored, moderate confidence
                sentiment_model="eodhd",
            ))

    def _poll_sec_edgar(self):
        """Poll SEC EDGAR RSS for insider trades and 8-K filings."""
        import requests
        import xml.etree.ElementTree as ET

        feeds = [
            ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&dateb=&owner=include&count=20&search_text=&action=getcurrent&output=atom", "insider"),
            ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&dateb=&owner=include&count=20&search_text=&action=getcurrent&output=atom", "8k"),
        ]
        for url, cat in feeds:
            try:
                resp = requests.get(url, timeout=15, headers={"User-Agent": "AEGIS-V2/1.0 research@example.com"})
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.content)
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                for entry in root.findall(".//atom:entry", ns)[:10]:
                    title = entry.findtext("atom:title", "", ns)
                    link = entry.findtext("atom:link[@rel='alternate']", "", ns) or ""
                    updated = entry.findtext("atom:updated", "", ns)
                    # Extract ticker from title (format: "FORM 4 - AAPL - John Doe")
                    symbols = _extract_symbols_from_title(title)
                    self._ingest(NewsItem(
                        headline=title,
                        source="sec",
                        timestamp=_parse_timestamp(updated),
                        symbols=symbols,
                        url=link,
                        category=cat,
                    ))
            except Exception as e:
                log.debug("SEC EDGAR error: %s", e)

    def _poll_fed_rss(self):
        """Poll Federal Reserve RSS for FOMC statements and speeches."""
        import requests
        import xml.etree.ElementTree as ET

        url = "https://www.federalreserve.gov/feeds/press_all.xml"
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                return
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:5]:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")
                self._ingest(NewsItem(
                    headline=title,
                    source="fed",
                    timestamp=_parse_timestamp(pub_date),
                    symbols=["SPY", "QQQ", "IWM"],  # Fed affects all indices
                    url=link,
                    category="macro",
                ))
        except Exception as e:
            log.debug("Fed RSS error: %s", e)

    def _poll_finviz(self):
        """Poll Finviz RSS for market headlines (fast, free)."""
        import requests
        import xml.etree.ElementTree as ET

        url = "https://finviz.com/news_export.ashx?v=2"
        try:
            resp = requests.get(url, timeout=10, headers={"User-Agent": "AEGIS-V2/1.0"})
            if resp.status_code != 200:
                return
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:15]:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")
                symbols = _extract_symbols_from_title(title)
                self._ingest(NewsItem(
                    headline=title,
                    source="finviz",
                    timestamp=_parse_timestamp(pub_date),
                    symbols=symbols,
                    url=link,
                    category=_classify_category(title, []),
                ))
        except Exception as e:
            log.debug("Finviz RSS error: %s", e)

    # ── Sentiment Scoring ──

    def _sentiment_scorer_loop(self):
        """Batch sentiment scoring with Gemini 2.5 Flash."""
        log.info("Gemini sentiment scorer started")
        while self._running:
            # Collect batch
            batch = []
            while self._sentiment_queue and len(batch) < 10:
                batch.append(self._sentiment_queue.popleft())

            if batch:
                self._score_batch(batch)
            else:
                time.sleep(2)

    def _score_batch(self, items: List[NewsItem]):
        """Score a batch of news items using Gemini 2.5 Flash."""
        try:
            import google.generativeai as genai
            api_key = os.environ.get("GEMINI_API_KEY", "")
            if not api_key:
                for item in items:
                    self._finalize(item)
                return

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-2.5-flash")

            # Build batch prompt
            headlines = []
            for i, item in enumerate(items):
                headlines.append(f"{i}: {item.headline}")

            prompt = (
                "Score the sentiment of each financial news headline below.\n"
                "Return ONLY a JSON array of objects with keys: index, score (-1.0 to +1.0), confidence (0.0 to 1.0).\n"
                "IMPORTANT: You have a documented BULLISH BIAS. Correct by scoring neutral headlines as 0.0, not positive.\n"
                "Headlines:\n" + "\n".join(headlines)
            )

            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=500,
                    temperature=0.1,
                ),
            )

            raw = response.text.strip()
            # Parse JSON response
            clean = raw
            if "```json" in clean:
                clean = clean.split("```json")[1].split("```")[0].strip()
            elif "```" in clean:
                clean = clean.split("```")[1].split("```")[0].strip()

            scores = json.loads(clean)
            for entry in scores:
                idx = entry.get("index", -1)
                if 0 <= idx < len(items):
                    items[idx].sentiment_score = float(entry.get("score", 0.0))
                    items[idx].sentiment_confidence = float(entry.get("confidence", 0.5))
                    items[idx].sentiment_model = "gemini-2.5-flash"

        except Exception as e:
            log.warning("Gemini sentiment scoring failed: %s", e)

        # Finalize all items (scored or not)
        for item in items:
            self._finalize(item)

    # ── Maintenance ──

    def _dedup_cleaner(self):
        """Periodically clean dedup cache."""
        while self._running:
            time.sleep(300)
            cutoff = time.time() - DEDUP_WINDOW_S
            with self._lock:
                expired = [k for k, v in self._seen.items() if v < cutoff]
                for k in expired:
                    del self._seen[k]

    def connect_redis(self, redis_url: str = "redis://localhost:6379"):
        """Connect to Redis for cross-process news distribution."""
        try:
            import redis
            self._redis = redis.Redis.from_url(redis_url)
            self._redis.ping()
            log.info("News aggregator: Redis connected (%s)", redis_url)
        except Exception as e:
            log.warning("News aggregator: Redis connection failed: %s", e)
            self._redis = None


# ── Utilities ──

def _parse_timestamp(ts_str: str) -> float:
    """Parse various timestamp formats to Unix timestamp."""
    if not ts_str:
        return time.time()
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
    ]:
        try:
            dt = datetime.strptime(ts_str, fmt)
            return dt.timestamp()
        except ValueError:
            continue
    return time.time()


def _classify_category(headline: str, channels: list) -> str:
    """Classify news category from headline and channel tags."""
    h = headline.lower()
    if any(w in h for w in ["earnings", "revenue", "eps", "guidance", "quarterly"]):
        return "earnings"
    if any(w in h for w in ["fda", "approval", "clinical", "trial"]):
        return "fda"
    if any(w in h for w in ["fed", "fomc", "rate", "inflation", "cpi", "gdp", "jobs"]):
        return "macro"
    if any(w in h for w in ["insider", "form 4", "10-k", "sec", "filing"]):
        return "insider"
    if any(w in h for w in ["upgrade", "downgrade", "target", "analyst"]):
        return "analyst"
    if any(w in h for w in ["merger", "acquisition", "buyout", "takeover"]):
        return "ma"
    return "general"


def _extract_symbols_from_title(title: str) -> List[str]:
    """Extract stock symbols from news title (heuristic)."""
    import re
    # Match patterns like $AAPL, (AAPL), AAPL:
    patterns = [
        r'\$([A-Z]{1,5})',           # $AAPL
        r'\(([A-Z]{1,5})\)',         # (AAPL)
        r'(?:^|\s)([A-Z]{2,5})(?:\s|$|:)',  # AAPL: or standalone
    ]
    symbols = set()
    for pattern in patterns:
        for match in re.findall(pattern, title):
            if match not in {"A", "I", "CEO", "FDA", "SEC", "IPO", "ETF", "NYSE", "GDP", "CPI", "THE", "FOR", "AND", "NOT"}:
                symbols.add(match)
    return list(symbols)


def get_cached_sentiment(symbol: str) -> Optional[float]:
    """Get cached sentiment score for a symbol.

    Called by bridge.py as a news sentiment overlay on signals.
    Reads from /app/data/sentiment_cache.json if it exists.

    Args:
        symbol: Ticker symbol (e.g. "AAPL", "3USL.L")

    Returns:
        Float from -1.0 (bearish) to +1.0 (bullish), or None if no data.
    """
    cache_path = Path("/app/data/sentiment_cache.json")
    try:
        if not cache_path.exists():
            return None
        with open(cache_path) as f:
            cache = json.load(f)
        val = cache.get(symbol.upper())
        if val is None:
            # Try without exchange suffix (e.g. "3USL" for "3USL.L")
            base = symbol.split(".")[0].upper()
            val = cache.get(base)
        if val is not None:
            return max(-1.0, min(1.0, float(val)))
        return None
    except Exception:
        return None
