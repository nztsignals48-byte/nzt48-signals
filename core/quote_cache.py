"""
Quote Caching Layer
===================

Q2-5: In-memory 1-minute cache for quote data to reduce API costs.

Problem: API calls for same ticker multiple times per minute (waste 40% of calls)
Solution: In-memory cache with 60-second TTL and stale-data fallback

Benefits:
- 40% reduction in API costs
- Lower latency (cached quotes served instantly)
- Fallback to stale quotes if feed fails (graceful degradation)

Cache invalidation:
- TTL-based: 60 seconds (aligned with scan interval)
- Manual: clear_cache() for specific ticker or all
- Auto-eviction: LRU when max_size exceeded

Thread safety: Uses threading.Lock for concurrent access
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("nzt48.core.quote_cache")

UTC = ZoneInfo("UTC")


@dataclass
class CachedQuote:
    """Cached quote data."""
    ticker: str
    price: float
    bid: float
    ask: float
    volume: int
    timestamp: datetime
    cache_time: float  # time.time() when cached

    def is_stale(self, ttl_seconds: float = 60.0) -> bool:
        """Check if quote is stale (older than TTL)."""
        age = time.time() - self.cache_time
        return age > ttl_seconds

    def age_seconds(self) -> float:
        """Get age of cached quote in seconds."""
        return time.time() - self.cache_time


class QuoteCache:
    """
    Q2-5: In-memory quote cache with TTL and LRU eviction.

    Thread-safe cache for real-time quote data. Reduces API calls by 40%.

    Usage:
        cache = QuoteCache(ttl_seconds=60, max_size=100)

        # Get quote (returns cached if fresh, None if stale/missing)
        quote = cache.get("AAPL")

        # Set quote
        cache.set("AAPL", price=150.0, bid=149.95, ask=150.05, volume=1000000)

        # Get stale quote as fallback
        quote = cache.get_stale("AAPL")  # Returns even if stale

        # Clear cache
        cache.clear()  # Clear all
        cache.clear_ticker("AAPL")  # Clear specific ticker
    """

    def __init__(self, ttl_seconds: float = 60.0, max_size: int = 200):
        """
        Initialize quote cache.

        Args:
            ttl_seconds: Time-to-live for cached quotes (default 60s)
            max_size: Maximum cache size (LRU eviction when exceeded)
        """
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size

        # Cache storage: ticker → CachedQuote
        self._cache: Dict[str, CachedQuote] = {}

        # Access tracking for LRU (ticker → last_access_time)
        self._access_times: Dict[str, float] = {}

        # Thread safety
        self._lock = threading.Lock()

        # Stats
        self._stats = {
            "hits": 0,
            "misses": 0,
            "stale_hits": 0,
            "evictions": 0,
            "sets": 0,
        }

        logger.info(f"Q2-5 QUOTE_CACHE: Initialized (ttl={ttl_seconds}s, max_size={max_size})")

    def get(self, ticker: str) -> Optional[CachedQuote]:
        """
        Get cached quote if fresh (within TTL).

        Args:
            ticker: Stock ticker

        Returns:
            CachedQuote if fresh, None if stale or missing
        """
        with self._lock:
            if ticker not in self._cache:
                self._stats["misses"] += 1
                logger.debug(f"Q2-5 CACHE_MISS: {ticker}")
                return None

            quote = self._cache[ticker]

            if quote.is_stale(self.ttl_seconds):
                self._stats["misses"] += 1
                logger.debug(
                    f"Q2-5 CACHE_STALE: {ticker} (age={quote.age_seconds():.1f}s > TTL={self.ttl_seconds}s)"
                )
                return None

            # Cache hit
            self._stats["hits"] += 1
            self._access_times[ticker] = time.time()
            logger.debug(
                f"Q2-5 CACHE_HIT: {ticker} (age={quote.age_seconds():.1f}s, price={quote.price:.2f})"
            )
            return quote

    def get_stale(self, ticker: str) -> Optional[CachedQuote]:
        """
        Get cached quote even if stale (fallback for feed failures).

        Args:
            ticker: Stock ticker

        Returns:
            CachedQuote (even if stale) or None if not in cache
        """
        with self._lock:
            if ticker not in self._cache:
                logger.debug(f"Q2-5 CACHE_MISS_STALE: {ticker}")
                return None

            quote = self._cache[ticker]
            self._stats["stale_hits"] += 1
            self._access_times[ticker] = time.time()

            logger.warning(
                f"Q2-5 CACHE_STALE_FALLBACK: {ticker} (age={quote.age_seconds():.1f}s, "
                f"price={quote.price:.2f}) — using stale quote as fallback"
            )
            return quote

    def set(
        self,
        ticker: str,
        price: float,
        bid: float = None,
        ask: float = None,
        volume: int = 0,
        timestamp: datetime = None,
    ) -> None:
        """
        Cache a quote.

        Args:
            ticker: Stock ticker
            price: Last price
            bid: Bid price (optional)
            ask: Ask price (optional)
            volume: Volume
            timestamp: Quote timestamp (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now(UTC)

        # Default bid/ask if not provided
        if bid is None:
            bid = price * 0.9999  # Estimate
        if ask is None:
            ask = price * 1.0001  # Estimate

        with self._lock:
            # Evict LRU if cache full
            if len(self._cache) >= self.max_size and ticker not in self._cache:
                self._evict_lru()

            # Cache quote
            self._cache[ticker] = CachedQuote(
                ticker=ticker,
                price=price,
                bid=bid,
                ask=ask,
                volume=volume,
                timestamp=timestamp,
                cache_time=time.time(),
            )
            self._access_times[ticker] = time.time()
            self._stats["sets"] += 1

            logger.debug(
                f"Q2-5 CACHE_SET: {ticker} (price={price:.2f}, size={len(self._cache)}/{self.max_size})"
            )

    def clear(self) -> None:
        """Clear entire cache."""
        with self._lock:
            size = len(self._cache)
            self._cache.clear()
            self._access_times.clear()
            logger.info(f"Q2-5 CACHE_CLEAR: Cleared {size} quotes")

    def clear_ticker(self, ticker: str) -> None:
        """
        Clear specific ticker from cache.

        Args:
            ticker: Stock ticker
        """
        with self._lock:
            if ticker in self._cache:
                del self._cache[ticker]
                del self._access_times[ticker]
                logger.debug(f"Q2-5 CACHE_CLEAR_TICKER: {ticker}")

    def get_stats(self) -> dict:
        """
        Get cache statistics.

        Returns:
            Dict with keys: hits, misses, stale_hits, evictions, sets,
            hit_rate, cache_size, max_size
        """
        with self._lock:
            total_requests = self._stats["hits"] + self._stats["misses"]
            hit_rate = (
                self._stats["hits"] / total_requests if total_requests > 0 else 0.0
            )

            return {
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "stale_hits": self._stats["stale_hits"],
                "evictions": self._stats["evictions"],
                "sets": self._stats["sets"],
                "hit_rate": hit_rate,
                "cache_size": len(self._cache),
                "max_size": self.max_size,
            }

    def log_stats(self) -> None:
        """Log cache statistics."""
        stats = self.get_stats()
        logger.info(
            f"Q2-5 CACHE_STATS: hits={stats['hits']} misses={stats['misses']} "
            f"stale_hits={stats['stale_hits']} hit_rate={stats['hit_rate']:.1%} "
            f"size={stats['cache_size']}/{stats['max_size']} evictions={stats['evictions']}"
        )

    def _evict_lru(self) -> None:
        """Evict least-recently-used quote (called when cache full)."""
        if not self._access_times:
            return

        # Find LRU ticker
        lru_ticker = min(self._access_times, key=self._access_times.get)

        # Evict
        del self._cache[lru_ticker]
        del self._access_times[lru_ticker]
        self._stats["evictions"] += 1

        logger.debug(f"Q2-5 CACHE_EVICT_LRU: {lru_ticker}")

    def purge_stale(self) -> int:
        """
        Manually purge all stale quotes from cache.

        Returns:
            Number of quotes purged
        """
        with self._lock:
            stale_tickers = [
                ticker
                for ticker, quote in self._cache.items()
                if quote.is_stale(self.ttl_seconds)
            ]

            for ticker in stale_tickers:
                del self._cache[ticker]
                if ticker in self._access_times:
                    del self._access_times[ticker]

            logger.info(f"Q2-5 CACHE_PURGE_STALE: Purged {len(stale_tickers)} stale quotes")
            return len(stale_tickers)
