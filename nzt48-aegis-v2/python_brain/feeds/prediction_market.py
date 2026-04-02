"""Prediction Market Signals — Book 155/194.

Ingests prediction market data (Polymarket/Kalshi-style) and converts
high-probability macro events into trading signal overlays.

Prediction markets aggregate information from many participants, often
producing more accurate probability estimates than expert forecasts.
This module:
  1. Fetches/parses prediction events (API placeholder)
  2. Converts high-probability events into directional signals
  3. Aggregates event probabilities into macro regime estimates
  4. Provides confidence adjustment overlays for existing signals

Key insight: When prediction markets price recession probability at 70%+,
bearish strategies should get a confidence boost; when rate-hike probability
is low, momentum-friendly regimes are likely.

State persisted to /app/data/prediction_markets/.

Usage:
    from python_brain.feeds.prediction_market import (
        PredictionMarketFeed, MacroProbabilityOverlay, PredictionEvent,
    )
    feed = PredictionMarketFeed()
    events = feed.fetch_events(category='economics')
    overlay = MacroProbabilityOverlay()
    regime = overlay.compute_regime_probability(events)
    adjusted_conf = overlay.confidence_adjustment(base_conf=72, macro_probs=regime)
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("prediction_market")

__all__ = [
    "PredictionEvent",
    "PredictionMarketFeed",
    "MacroProbabilityOverlay",
]

# ── Constants ──────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/prediction_markets")
EVENTS_CACHE_PATH = STATE_DIR / "events_cache.json"
MAX_CACHE_AGE_SECONDS = 3600  # 1 hour

# Event categories relevant to trading
MACRO_CATEGORIES = {"economics", "fed", "rates", "inflation", "recession",
                    "gdp", "employment", "geopolitics", "trade_war"}

# Recession-indicative keywords
RECESSION_KEYWORDS = {"recession", "contraction", "gdp decline", "negative growth",
                      "economic downturn", "bear market"}
RATE_HIKE_KEYWORDS = {"rate hike", "rate increase", "fed raise", "hawkish",
                      "tightening", "rate cut", "rate decrease", "dovish", "easing"}


# ── Data Classes ──────────────────────────────────────────────────────

@dataclass
class PredictionEvent:
    """A single prediction market event.

    Attributes:
        event_id: Unique identifier for the event.
        question: The market question (e.g., "Will the Fed raise rates in March?").
        probability: Current market probability [0, 1].
        volume: Total trading volume in USD.
        last_updated: ISO timestamp of last price change.
        category: Event category (economics, rates, etc.).
        source: Data source identifier.
    """
    event_id: str
    question: str
    probability: float
    volume: float
    last_updated: str
    category: str = "economics"
    source: str = "polymarket"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "event_id": self.event_id,
            "question": self.question,
            "probability": self.probability,
            "volume": self.volume,
            "last_updated": self.last_updated,
            "category": self.category,
            "source": self.source,
        }


# ── Prediction Market Feed ────────────────────────────────────────────

class PredictionMarketFeed:
    """Fetches and processes prediction market events.

    Provides a standardized interface for multiple prediction market
    data sources. Currently a placeholder API; can be extended to
    fetch from Polymarket, Kalshi, or PredictIt.
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        """Initialize the prediction market feed.

        Args:
            cache_dir: Directory for caching fetched events.
        """
        self._cache_dir = cache_dir or STATE_DIR
        self._events_cache: List[PredictionEvent] = []
        self._last_fetch: float = 0.0
        log.info("PredictionMarketFeed initialized (cache_dir=%s)", self._cache_dir)

    def fetch_events(self, category: str = "economics") -> List[PredictionEvent]:
        """Fetch prediction events for a category.

        Currently returns cached/placeholder data. In production, this would
        call the Polymarket/Kalshi API.

        Args:
            category: Event category to filter.

        Returns:
            List of PredictionEvent matching the category.
        """
        # Check cache freshness
        if (time.time() - self._last_fetch) < MAX_CACHE_AGE_SECONDS and self._events_cache:
            return [e for e in self._events_cache if e.category == category or category == "all"]

        # Try loading from file cache
        events = self._load_cache()
        if events:
            self._events_cache = events
            self._last_fetch = time.time()
            return [e for e in events if e.category == category or category == "all"]

        # Placeholder: return synthetic events for development
        log.info("No prediction market data available — using placeholder events")
        events = self._generate_placeholder_events()
        self._events_cache = events
        self._last_fetch = time.time()
        self._save_cache(events)

        return [e for e in events if e.category == category or category == "all"]

    def probability_to_signal(self, event: PredictionEvent,
                              threshold: float = 0.70) -> Dict[str, Any]:
        """Convert a high-probability prediction event into a trading signal.

        Events with probability >= threshold generate directional signals.
        The signal confidence scales with both probability and volume.

        Args:
            event: Prediction event to evaluate.
            threshold: Minimum probability to generate a signal.

        Returns:
            Dict with signal info, or empty dict if below threshold.
        """
        if event.probability < threshold:
            return {}

        # Base confidence from probability
        prob_excess = event.probability - threshold
        base_confidence = 50 + int(prob_excess * 100)  # 50-80 range
        base_confidence = min(base_confidence, 80)

        # Volume weighting (higher volume = more reliable)
        if event.volume > 1_000_000:
            vol_boost = 5
        elif event.volume > 100_000:
            vol_boost = 2
        else:
            vol_boost = 0

        confidence = min(base_confidence + vol_boost, 85)

        # Determine direction from event content
        direction = self._infer_direction(event)

        signal = {
            "event_id": event.event_id,
            "question": event.question,
            "probability": round(event.probability, 3),
            "confidence": confidence,
            "direction": direction,
            "volume": event.volume,
            "source": f"prediction_market_{event.source}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        log.info("Signal from prediction market: %s prob=%.2f conf=%d dir=%s",
                 event.event_id, event.probability, confidence, direction)
        return signal

    def _infer_direction(self, event: PredictionEvent) -> str:
        """Infer trading direction from event question content."""
        q = event.question.lower()

        # Bearish events
        for kw in RECESSION_KEYWORDS:
            if kw in q:
                return "bearish" if event.probability > 0.5 else "bullish"

        # Rate events
        for kw in RATE_HIKE_KEYWORDS:
            if kw in q:
                if "cut" in kw or "decrease" in kw or "dovish" in kw or "easing" in kw:
                    return "bullish" if event.probability > 0.5 else "bearish"
                return "bearish" if event.probability > 0.5 else "bullish"

        return "neutral"

    def _generate_placeholder_events(self) -> List[PredictionEvent]:
        """Generate placeholder events for development."""
        now = datetime.now(timezone.utc).isoformat()
        return [
            PredictionEvent("fed_rate_hold_q2", "Will the Fed hold rates in Q2?",
                            0.72, 2_500_000.0, now, "rates"),
            PredictionEvent("us_recession_2026", "US recession by end of 2026?",
                            0.25, 5_000_000.0, now, "recession"),
            PredictionEvent("cpi_above_3pct", "US CPI above 3% in next report?",
                            0.35, 800_000.0, now, "inflation"),
            PredictionEvent("sp500_ath_q2", "S&P 500 new ATH in Q2 2026?",
                            0.55, 1_200_000.0, now, "economics"),
            PredictionEvent("fed_cut_h2", "Fed rate cut in H2 2026?",
                            0.48, 3_000_000.0, now, "rates"),
        ]

    def _load_cache(self) -> List[PredictionEvent]:
        """Load events from file cache."""
        try:
            if EVENTS_CACHE_PATH.exists():
                with open(str(EVENTS_CACHE_PATH), "r") as f:
                    data = json.load(f)
                events = []
                for d in data.get("events", []):
                    events.append(PredictionEvent(**d))
                return events
        except Exception as e:
            log.warning("Failed to load prediction market cache: %s", e)
        return []

    def _save_cache(self, events: List[PredictionEvent]) -> None:
        """Save events to file cache."""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "events": [e.to_dict() for e in events],
            }
            with open(str(EVENTS_CACHE_PATH), "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.warning("Failed to save prediction market cache: %s", e)


# ── Macro Probability Overlay ─────────────────────────────────────────

class MacroProbabilityOverlay:
    """Aggregates prediction market events into macro regime probabilities.

    Converts individual event probabilities into composite regime
    estimates (recession probability, rate change probability, etc.)
    and provides confidence adjustments for trading signals.
    """

    def __init__(self):
        """Initialize macro probability overlay."""
        self._last_regime: Optional[Dict] = None
        log.info("MacroProbabilityOverlay initialized")

    def compute_regime_probability(self, events: List[PredictionEvent]) -> Dict[str, float]:
        """Aggregate event probabilities into regime estimates.

        Args:
            events: List of prediction events.

        Returns:
            Dict mapping regime names to probabilities.
        """
        if not events:
            return {
                "recession": 0.15,
                "rate_hike": 0.20,
                "rate_cut": 0.30,
                "bull_market": 0.50,
                "high_volatility": 0.25,
            }

        recession_probs: List[Tuple[float, float]] = []   # (prob, volume_weight)
        rate_hike_probs: List[Tuple[float, float]] = []
        rate_cut_probs: List[Tuple[float, float]] = []
        bull_probs: List[Tuple[float, float]] = []

        for event in events:
            q = event.question.lower()
            vol_weight = math.log1p(event.volume + 1)

            for kw in RECESSION_KEYWORDS:
                if kw in q:
                    recession_probs.append((event.probability, vol_weight))
                    break

            if any(kw in q for kw in ["rate hike", "rate increase", "hawkish", "tightening"]):
                rate_hike_probs.append((event.probability, vol_weight))
            elif any(kw in q for kw in ["rate cut", "rate decrease", "dovish", "easing"]):
                rate_cut_probs.append((event.probability, vol_weight))

            if any(kw in q for kw in ["ath", "all-time high", "new high", "bull"]):
                bull_probs.append((event.probability, vol_weight))

        regime = {
            "recession": self._weighted_mean(recession_probs, default=0.15),
            "rate_hike": self._weighted_mean(rate_hike_probs, default=0.20),
            "rate_cut": self._weighted_mean(rate_cut_probs, default=0.30),
            "bull_market": self._weighted_mean(bull_probs, default=0.50),
            "high_volatility": 0.25,  # Default; would need VIX futures market
        }

        # High volatility estimate from recession + rate uncertainty
        rate_uncertainty = 1.0 - abs(regime["rate_hike"] - regime["rate_cut"])
        regime["high_volatility"] = min(
            0.3 * regime["recession"] + 0.4 * rate_uncertainty + 0.1,
            0.90,
        )

        self._last_regime = regime
        log.info("Regime probabilities: %s",
                 {k: f"{v:.2f}" for k, v in regime.items()})
        return regime

    def recession_probability(self, events: List[PredictionEvent]) -> float:
        """Compute aggregate recession probability.

        Args:
            events: List of prediction events.

        Returns:
            Recession probability [0, 1].
        """
        recession_events = []
        for event in events:
            q = event.question.lower()
            for kw in RECESSION_KEYWORDS:
                if kw in q:
                    recession_events.append((event.probability,
                                             math.log1p(event.volume + 1)))
                    break

        return self._weighted_mean(recession_events, default=0.15)

    def rate_hike_probability(self, events: List[PredictionEvent]) -> float:
        """Compute aggregate rate hike probability.

        Args:
            events: List of prediction events.

        Returns:
            Rate hike probability [0, 1].
        """
        rate_events = []
        for event in events:
            q = event.question.lower()
            if any(kw in q for kw in ["rate hike", "rate increase", "hawkish", "tightening"]):
                rate_events.append((event.probability,
                                    math.log1p(event.volume + 1)))
            elif any(kw in q for kw in ["hold rates", "rate hold"]):
                # P(hike) = 1 - P(hold), weighted less heavily
                rate_events.append((1.0 - event.probability,
                                    math.log1p(event.volume + 1) * 0.5))

        return self._weighted_mean(rate_events, default=0.20)

    def confidence_adjustment(self, base_conf: float,
                              macro_probs: Dict[str, float]) -> float:
        """Adjust signal confidence based on macro regime probabilities.

        Bearish macro environments reduce confidence in long signals.
        Bullish environments boost confidence (modestly).

        Args:
            base_conf: Base confidence score [0, 100].
            macro_probs: Regime probability dict from compute_regime_probability.

        Returns:
            Adjusted confidence [0, 100].
        """
        recession_p = macro_probs.get("recession", 0.15)
        bull_p = macro_probs.get("bull_market", 0.50)
        vol_p = macro_probs.get("high_volatility", 0.25)

        # Bearish adjustment: reduce confidence when recession probability is high
        # Max penalty: -15 points at recession_p = 1.0
        bearish_penalty = recession_p * 15.0

        # Bullish boost: small boost when bull market probability is high
        # Max boost: +5 points at bull_p = 1.0
        bullish_boost = bull_p * 5.0

        # Volatility penalty: reduce confidence in high-vol environments
        # Max penalty: -8 points at vol_p = 1.0
        vol_penalty = vol_p * 8.0

        adjusted = base_conf - bearish_penalty + bullish_boost - vol_penalty
        adjusted = float(np.clip(adjusted, 10.0, 95.0))

        if abs(adjusted - base_conf) > 3.0:
            log.info("Confidence adjusted: %.0f -> %.0f (recession=%.2f, "
                     "bull=%.2f, vol=%.2f)",
                     base_conf, adjusted, recession_p, bull_p, vol_p)

        return round(adjusted, 1)

    @staticmethod
    def _weighted_mean(items: List[Tuple[float, float]],
                       default: float = 0.5) -> float:
        """Volume-weighted mean probability.

        Args:
            items: List of (probability, weight) tuples.
            default: Default value if no items.

        Returns:
            Weighted mean probability.
        """
        if not items:
            return default
        total_weight = sum(w for _, w in items)
        if total_weight < 1e-10:
            return default
        weighted_sum = sum(p * w for p, w in items)
        return float(np.clip(weighted_sum / total_weight, 0.0, 1.0))

    def crowd_vs_model_signal(self, events: List[PredictionEvent],
                              model_signal: str = "neutral",
                              model_confidence: float = 0.5) -> Dict[str, Any]:
        """Generate signal from disagreement between crowd and model (Book 155).

        Prediction market consensus vs historical model: when they diverge,
        the crowd's conviction (high volume) suggests real information.

        Args:
            events: List of prediction events.
            model_signal: Model's directional assessment (bullish/bearish/neutral).
            model_confidence: Model's confidence [0, 1].

        Returns:
            Dict with crowd-vs-model signal and confidence boost.
        """
        if not events:
            return {
                "signal": "PREDMKT_CROWD_MODEL",
                "agreement": "INSUFFICIENT_DATA",
                "confidence_adjustment": 0,
            }

        # Compute crowd's aggregate direction
        crowd_bullish = 0.0
        crowd_bearish = 0.0
        total_volume = 0.0

        for event in events:
            vol_weight = math.log1p(event.volume + 1)
            total_volume += vol_weight

            if "bull" in event.question.lower() or "ath" in event.question.lower():
                crowd_bullish += event.probability * vol_weight
            elif any(kw in event.question.lower() for kw in RECESSION_KEYWORDS):
                crowd_bearish += event.probability * vol_weight

        if total_volume < 1e-6:
            return {
                "signal": "PREDMKT_CROWD_MODEL",
                "agreement": "NO_VOLUME",
                "confidence_adjustment": 0,
            }

        crowd_bullish /= total_volume
        crowd_bearish /= total_volume

        # Determine crowd consensus
        if crowd_bullish > 0.6:
            crowd_signal = "bullish"
        elif crowd_bearish > 0.6:
            crowd_signal = "bearish"
        else:
            crowd_signal = "neutral"

        # Compare to model
        if crowd_signal == model_signal:
            agreement = "AGREEMENT"
            adjustment = min(10, int(20 * max(crowd_bullish, crowd_bearish)))
        else:
            agreement = "DISAGREEMENT"
            adjustment = -max(5, int(10 * max(crowd_bullish, crowd_bearish)))

        return {
            "signal": "PREDMKT_CROWD_MODEL",
            "crowd_consensus": crowd_signal,
            "model_assessment": model_signal,
            "agreement": agreement,
            "crowd_bullish_prob": round(crowd_bullish, 3),
            "crowd_bearish_prob": round(crowd_bearish, 3),
            "confidence_adjustment": int(adjustment),
            "total_volume_weight": round(total_volume, 2),
        }
