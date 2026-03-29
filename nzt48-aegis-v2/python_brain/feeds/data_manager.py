"""Unified Data Manager — Coordinates all data feeds for the AEGIS V2 engine.

Single entry point for bridge.py to query:
  - Market data (IBKR primary, Polygon backup)
  - News sentiment (Benzinga, Unusual Whales, EODHD, SEC, Fed, Finviz)
  - Flow data (dark pool, options, Congress)
  - Macro indicators

Architecture:
  DataManager.start() launches all feeds in background threads.
  bridge.py calls DataManager.enrich_signal(signal, ticker) to add
  sentiment, flow, and macro context before sizing.

Books: 8, 53, 72, 117, 198, 216
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

log = logging.getLogger("data_manager")


class DataManager:
    """Unified data feed coordinator.

    Usage from bridge.py:
        dm = DataManager()
        dm.start()
        ...
        context = dm.enrich_signal(signal_dict, ticker_id)
        # context now has sentiment_score, flow_direction, news_count, etc.
    """

    def __init__(self):
        self._polygon = None
        self._news = None
        self._started = False

    def start(self):
        """Start all data feeds."""
        if self._started:
            return

        # Polygon.io market data (optional, requires API key)
        polygon_key = os.environ.get("POLYGON_API_KEY", "")
        if polygon_key:
            try:
                from python_brain.feeds.polygon_feed import PolygonFeed, PolygonConfig
                self._polygon = PolygonFeed(PolygonConfig(api_key=polygon_key))
                redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
                self._polygon.connect_redis(redis_url)
                self._polygon.start()
                log.info("Polygon market data feed: ACTIVE")
            except Exception as e:
                log.warning("Polygon feed failed to start: %s", e)

        # News aggregator (works with any combination of API keys)
        try:
            from python_brain.feeds.news_aggregator import NewsAggregator
            self._news = NewsAggregator()
            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
            self._news.connect_redis(redis_url)
            self._news.start()
            log.info("News aggregator: ACTIVE (sources=%d)", len(self._news._threads))
        except Exception as e:
            log.warning("News aggregator failed to start: %s", e)

        self._started = True
        log.info("DataManager started (polygon=%s, news=%s)",
                 self._polygon is not None, self._news is not None)

    def stop(self):
        """Stop all feeds."""
        if self._polygon:
            self._polygon.stop()
        if self._news:
            self._news.stop()
        self._started = False

    def enrich_signal(self, signal: Dict[str, Any], symbol: str) -> Dict[str, Any]:
        """Enrich a trading signal with news sentiment and flow data.

        Called by bridge.py before signal sizing.
        Returns the signal dict with added fields:
          - news_sentiment_score: -1.0 to +1.0
          - news_sentiment_confidence: 0.0 to 1.0
          - news_count: int
          - news_latest_headline: str
          - has_flow_data: bool
          - flow_direction: bullish/bearish/neutral
          - market_mood: bullish/bearish/neutral
        """
        if self._news:
            try:
                # Per-ticker sentiment
                ticker_sent = self._news.get_ticker_sentiment(symbol)
                signal["news_sentiment_score"] = ticker_sent.get("score", 0.0)
                signal["news_sentiment_confidence"] = ticker_sent.get("confidence", 0.0)
                signal["news_count"] = ticker_sent.get("count", 0)
                signal["news_latest_headline"] = ticker_sent.get("latest_headline", "")
                signal["has_flow_data"] = ticker_sent.get("has_flow", False)
                signal["flow_direction"] = ticker_sent.get("flow_direction", "neutral")
                signal["news_bullish"] = ticker_sent.get("bullish_count", 0)
                signal["news_bearish"] = ticker_sent.get("bearish_count", 0)

                # Market-wide sentiment
                market_sent = self._news.get_market_sentiment()
                signal["market_mood"] = market_sent.get("mood", "neutral")
                signal["market_sentiment_score"] = market_sent.get("score", 0.0)
                signal["market_news_count"] = market_sent.get("count", 0)

                # Confidence adjustment based on sentiment alignment
                # If signal is bullish and news is bearish (or vice versa), reduce confidence
                sig_direction = 1 if signal.get("side", "Long") == "Long" else -1
                news_direction = 1 if ticker_sent.get("score", 0) > 0.1 else -1 if ticker_sent.get("score", 0) < -0.1 else 0

                if news_direction != 0:
                    alignment = sig_direction * news_direction  # +1 if aligned, -1 if opposed
                    news_conf = ticker_sent.get("confidence", 0)
                    if alignment < 0 and news_conf > 0.3:
                        # Opposing sentiment with high confidence — reduce signal confidence
                        penalty = int(min(15, news_conf * 20))
                        signal["confidence"] = max(0, signal.get("confidence", 50) - penalty)
                        signal["news_penalty"] = penalty
                    elif alignment > 0 and news_conf > 0.4:
                        # Aligned sentiment — boost slightly (max +8)
                        boost = int(min(8, news_conf * 10))
                        signal["confidence"] = min(100, signal.get("confidence", 50) + boost)
                        signal["news_boost"] = boost

            except Exception as e:
                log.debug("Signal enrichment error: %s", e)
        else:
            signal["news_sentiment_score"] = 0.0
            signal["news_count"] = 0
            signal["market_mood"] = "neutral"

        return signal

    def get_polygon_tick(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get latest Polygon tick for a symbol (backup data source)."""
        if not self._polygon:
            return None
        tick = self._polygon.get_latest(symbol)
        return tick.to_dict() if tick else None

    def health_dict(self) -> dict:
        """Combined health status."""
        return {
            "started": self._started,
            "polygon": self._polygon.health_dict() if self._polygon else {"active": False},
            "news": self._news.health_dict() if self._news else {"active": False},
        }


# ── Singleton for bridge.py ──

_instance: Optional[DataManager] = None


def get_data_manager() -> DataManager:
    """Get or create the singleton DataManager instance."""
    global _instance
    if _instance is None:
        _instance = DataManager()
        _instance.start()
    return _instance
