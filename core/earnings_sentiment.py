"""
NLP Earnings Sentiment -- Stage 4 Intelligence
===============================================
Loughran & McDonald (2011) -- NLP-scored financial text predicts
3-day post-announcement drift direction with 67% accuracy.

Uses Gemini API to score earnings call headlines/summaries.
Gemini API key: read from env var GEMINI_API_KEY.

Confidence adjustment: sentiment score maps to +-10 confidence points.
Cache: data/earnings_sentiment.json -- one entry per ticker per quarter.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_EARNINGS_KEYWORDS = {"earnings", "revenue", "EPS", "beat", "miss"}
_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"


def _current_quarter_label() -> str:
    """Return a string like 2026Q1 for cache keying."""
    now = datetime.now(tz=timezone.utc)
    quarter = (now.month - 1) // 3 + 1
    return f"{now.year}Q{quarter}"


class EarningsSentimentScorer:
    """
    Stage 4 intelligence: uses Gemini API to NLP-score earnings headlines.
    Maps sentiment to a confidence adjustment of +-10 points.
    All results are cached per ticker per quarter.
    Degrades gracefully: returns 0 adjustment if Gemini unavailable.
    """

    def __init__(self, data_path: str = "data/earnings_sentiment.json") -> None:
        self.data_path = Path(data_path)
        self._api_key: str = ""
        # Try python-dotenv first, then fall back to raw os.environ
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        self._api_key = os.environ.get("GEMINI_API_KEY", "")
        self.gemini_available: bool = bool(self._api_key)
        if not self.gemini_available:
            logger.warning("EarningsSentimentScorer: GEMINI_API_KEY not set -- running in unavailable mode")
        self._cache: dict[str, Any] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        """Load existing sentiment cache from disk."""
        if not self.data_path.exists():
            return
        try:
            with open(self.data_path, "r", encoding="utf-8") as fh:
                self._cache = json.load(fh)
            logger.debug("EarningsSentimentScorer: loaded cache (%d entries)", len(self._cache))
        except Exception as exc:
            logger.warning("EarningsSentimentScorer: could not load cache -- %s", exc)
            self._cache = {}

    def save_cache(self) -> None:
        """Persist sentiment cache to disk."""
        try:
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.data_path, "w", encoding="utf-8") as fh:
                json.dump(self._cache, fh, indent=2)
            logger.debug("EarningsSentimentScorer: cache saved to %s", self.data_path)
        except Exception as exc:
            logger.warning("EarningsSentimentScorer: could not save cache -- %s", exc)

    def _get_earnings_headlines(self, ticker: str) -> list[str]:
        """Fetch recent earnings-related headlines via yfinance. Returns up to 10 strings."""
        try:
            import yfinance as yf
            news_items = yf.Ticker(ticker).news or []
        except Exception as exc:
            logger.debug("EarningsSentimentScorer: yfinance news failed for %s -- %s", ticker, exc)
            return []
        now_ts = time.time()
        seven_days_ago = now_ts - 7 * 86400
        headlines: list[str] = []
        for item in news_items:
            if not isinstance(item, dict):
                continue
            pub_ts = item.get("providerPublishTime", 0) or 0
            if pub_ts < seven_days_ago:
                continue
            title = item.get("title", "")
            if any(kw.lower() in title.lower() for kw in _EARNINGS_KEYWORDS):
                headlines.append(title)
            if len(headlines) >= 10:
                break
        logger.debug("EarningsSentimentScorer: %d earnings headlines for %s", len(headlines), ticker)
        return headlines

    def _call_gemini(self, prompt: str) -> str:
        """Call Gemini API; returns raw text response. Raises on failure."""
        # Try google.generativeai SDK first
        try:
            import google.generativeai as genai
            genai.configure(api_key=self._api_key)
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(prompt)
            return response.text
        except ImportError:
            pass  # Fall through to HTTP
        # Fallback: direct HTTP POST via requests
        import requests
        url = f"{_GEMINI_ENDPOINT}?key={self._api_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        resp = requests.post(url, json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    def score_ticker(self, ticker: str, force_refresh: bool = False) -> dict:
        """Score ticker earnings sentiment via Gemini. Returns cached result when possible."""
        quarter = _current_quarter_label()
        cache_key = f"{ticker}_{quarter}"
        if not force_refresh and cache_key in self._cache:
            logger.debug("EarningsSentimentScorer: cache hit for %s", cache_key)
            return self._cache[cache_key]
        _unavailable = {"sentiment": 0.0, "confidence": 0.0, "source": "unavailable"}
        headlines = self._get_earnings_headlines(ticker)
        if not headlines or not self.gemini_available:
            reason = "no_headlines" if not headlines else "no_api_key"
            logger.debug("EarningsSentimentScorer: unavailable for %s (%s)", ticker, reason)
            return _unavailable
        headlines_text = "\n".join(f"- {h}" for h in headlines)
        prompt = (
            f"You are analyzing earnings sentiment for {ticker}.\n"
            f"Headlines:\n{headlines_text}\n\n"
            "Return ONLY valid JSON (no markdown):\n"
            "{\"sentiment\": <-1.0 to 1.0>, \"guidance_tone\": <-1.0 to 1.0>, "
            "\"beat_quality\": <0.0 to 1.0>, \"drift_prediction\": \"UP\" or \"DOWN\" or \"NEUTRAL\", "
            "\"confidence\": <0.0 to 1.0>, \"key_reason\": \"<10 words max>\"}"
        )
        try:
            raw_text = self._call_gemini(prompt)
            # Strip markdown code fences if present
            clean = raw_text.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            parsed: dict = json.loads(clean)
            required_keys = {"sentiment", "guidance_tone", "beat_quality", "drift_prediction", "confidence", "key_reason"}
            for k in required_keys:
                if k not in parsed:
                    parsed[k] = 0.0 if k != "drift_prediction" and k != "key_reason" else ("NEUTRAL" if k == "drift_prediction" else "")
            parsed["source"] = "gemini"
            parsed["ticker"] = ticker
            parsed["quarter"] = quarter
            parsed["cached_at"] = datetime.now(tz=timezone.utc).isoformat()
            parsed["headline_count"] = len(headlines)
        except Exception as exc:
            logger.warning("EarningsSentimentScorer: Gemini call/parse failed for %s -- %s", ticker, exc)
            return _unavailable
        self._cache[cache_key] = parsed
        self.save_cache()
        logger.info("EarningsSentimentScorer: scored %s sentiment=%.2f drift=%s", ticker, parsed.get("sentiment", 0), parsed.get("drift_prediction", "?"))
        return parsed

    def get_confidence_adjustment(self, ticker: str) -> int:
        """Return confidence delta (-10 to +10) based on earnings sentiment."""
        score = self.score_ticker(ticker)
        if score.get("source") == "unavailable":
            return 0
        sentiment = float(score.get("sentiment", 0.0))
        drift = str(score.get("drift_prediction", "NEUTRAL")).upper()
        if sentiment > 0.5 and drift == "UP":
            return 10
        if sentiment > 0.2:
            return 5
        if sentiment < -0.5 and drift == "DOWN":
            return -10
        if sentiment < -0.2:
            return -5
        return 0

    def get_telegram_note(self, ticker: str) -> str:
        """Return a short Telegram note string for signal alerts, or empty string."""
        quarter = _current_quarter_label()
        cache_key = f"{ticker}_{quarter}"
        score = self._cache.get(cache_key, {})
        if not score or score.get("source") == "unavailable":
            return ""
        sentiment = float(score.get("sentiment", 0.0))
        drift = str(score.get("drift_prediction", "NEUTRAL")).upper()
        adj = self.get_confidence_adjustment(ticker)
        if sentiment > 0.3:
            tone = "BULLISH"
        elif sentiment < -0.3:
            tone = "BEARISH"
        else:
            tone = "NEUTRAL"
        adj_str = f"+{adj}" if adj > 0 else str(adj)
        if adj == 0:
            return ""
        return f"Sentiment: {tone} ({sentiment:.2f}) -- conf {adj_str}"
