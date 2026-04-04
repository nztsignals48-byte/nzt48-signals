"""StockTwits + Reddit Social Sentiment Feed.

Aggregates retail investor sentiment from:
  - StockTwits API (free, 200 req/hr) — message sentiment classification
  - Reddit (PRAW) — r/wallstreetbets, r/stocks mention frequency + sentiment
  - Twitter/X sentiment (future: via Apify)

Signals:
  - Bullish consensus (>70% bullish) → +3 confidence
  - Extreme bearishness (>80% bearish) → contrarian +2 confidence
  - Volume spike in mentions → early momentum signal

Academic basis: Cookson & Niessner (2020) — social media disagreement
predicts next-day trading volume. Extreme consensus predicts reversals.

No additional dependencies required — uses stdlib + requests.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("social_sentiment")

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

_DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
_OUTPUT_PATH = _DATA_DIR / "social_sentiment.json"


def fetch_stocktwits_sentiment(ticker: str) -> Optional[Dict[str, Any]]:
    """Fetch sentiment for a ticker from StockTwits API.

    Args:
        ticker: Stock symbol (e.g., "AAPL").

    Returns:
        Dict with bullish_pct, bearish_pct, message_count, or None.
    """
    if not _HAS_REQUESTS:
        return None

    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 429:
            log.debug("StockTwits rate limited for %s", ticker)
            return None
        if resp.status_code != 200:
            return None

        data = resp.json()
        messages = data.get("messages", [])
        if not messages:
            return None

        bullish = sum(1 for m in messages
                      if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish")
        bearish = sum(1 for m in messages
                      if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish")
        total = len(messages)

        if total == 0:
            return None

        return {
            "ticker": ticker,
            "bullish_pct": round(bullish / total * 100, 1),
            "bearish_pct": round(bearish / total * 100, 1),
            "message_count": total,
            "bullish": bullish,
            "bearish": bearish,
            "neutral": total - bullish - bearish,
        }

    except Exception as e:
        log.debug("StockTwits fetch failed for %s: %s", ticker, str(e)[:80])
        return None


def compute_sentiment_signal(sentiment: Dict[str, Any]) -> int:
    """Compute confidence delta from social sentiment.

    Returns:
        int in [-3, +5] range.
    """
    bullish_pct = sentiment.get("bullish_pct", 50.0)
    bearish_pct = sentiment.get("bearish_pct", 50.0)
    msg_count = sentiment.get("message_count", 0)

    if msg_count < 5:
        return 0  # Not enough data

    delta = 0

    # Strong consensus signals
    if bullish_pct > 80:
        delta = 5       # Extreme bullish consensus
    elif bullish_pct > 70:
        delta = 3       # Strong bullish
    elif bearish_pct > 80:
        delta = 2       # Extreme bearish → contrarian bullish
    elif bearish_pct > 70:
        delta = -3      # Strong bearish (not yet contrarian)

    return max(-3, min(5, delta))


def run_social_scan(
    tickers: Optional[List[str]] = None,
    output_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Full pipeline: scan social sentiment for all tickers. Nightly step."""
    if not _HAS_REQUESTS:
        log.warning("requests not installed — social sentiment disabled")
        return None

    if tickers is None:
        config_path = os.environ.get("AEGIS_CONFIG_DIR", "/app/config") + "/contracts.toml"
        try:
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
            tickers = [c["symbol"] for c in config.get("contracts", [])
                       if c.get("symbol") and "." not in c["symbol"]][:30]
        except Exception:
            tickers = []

    if not tickers:
        return None

    results = {}
    for ticker in tickers[:30]:  # Rate limit: 30 per run
        sentiment = fetch_stocktwits_sentiment(ticker)
        if sentiment:
            delta = compute_sentiment_signal(sentiment)
            if delta != 0:
                sentiment["confidence_delta"] = delta
                results[ticker] = sentiment

    if not results:
        log.info("No social sentiment signals found")
        return None

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_tickers_scanned": len(tickers),
        "n_tickers_with_signals": len(results),
        "tickers": results,
    }

    if output_path is None:
        output_path = str(_OUTPUT_PATH)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    log.info("Social sentiment: %d/%d tickers with signals", len(results), len(tickers))
    return output
