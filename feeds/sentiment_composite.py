"""
NZT-48 Trading System — Composite Sentiment Index
Research Enhancement #12: Multi-Source Sentiment Aggregation

Combines multiple sentiment indicators into a single 0-100 composite score
that captures the market's overall fear/greed positioning.

Components (each normalised to 0-100):
  1. Put/Call Ratio  — inverted (high ratio = fear = low score)
  2. VIX Term Structure — VIX / VIX3M ratio
  3. DIX Score        — dark pool buying indicator
  4. GEX Score        — gamma exposure regime
  5. Fear/Greed Proxy — VIX level + breadth approximation

Sentiment regimes:
  EXTREME_FEAR  (< 20)   — contrarian bullish
  FEAR          (20-35)
  NEUTRAL       (35-65)
  GREED         (65-80)
  EXTREME_GREED (> 80)   — contrarian bearish
"""

from __future__ import annotations

import logging
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.sentiment")

# ---------------------------------------------------------------------------
# Constants — mapping boundaries for each component
# ---------------------------------------------------------------------------

# Put/Call Ratio mapping (inverted: high ratio = fear = low score)
PCR_FEAR_THRESHOLD: float = 1.2       # ratio >= 1.2 => score 10
PCR_GREED_THRESHOLD: float = 0.6      # ratio <= 0.6 => score 90

# VIX Term Structure mapping (VIX / VIX3M)
VTS_FEAR_THRESHOLD: float = 1.1       # ratio >= 1.1 (backwardation) => score 10
VTS_GREED_THRESHOLD: float = 0.85     # ratio <= 0.85 (contango) => score 90

# DIX mapping
DIX_BULLISH_THRESHOLD: float = 0.48   # DIX >= 0.48 => score 80
DIX_BEARISH_THRESHOLD: float = 0.40   # DIX <= 0.40 => score 20

# GEX mapping
GEX_POSITIVE_BASE: float = 60.0       # Positive GEX = suppressed vol
GEX_NEGATIVE_BASE: float = 40.0       # Negative GEX = amplified vol
GEX_SCALE_FACTOR: float = 10.0        # Scale magnitude effect

# Fear/Greed Proxy (VIX level)
VIX_LOW_THRESHOLD: float = 15.0       # VIX < 15 => score 80
VIX_HIGH_THRESHOLD: float = 30.0      # VIX > 30 => score 20

# Default component weights (must sum to 1.0)
DEFAULT_WEIGHTS: dict[str, float] = {
    "put_call_ratio": 0.20,
    "vix_term_structure": 0.20,
    "dix_score": 0.25,
    "gex_score": 0.15,
    "fear_greed_proxy": 0.20,
}

# Sentiment regime boundaries
EXTREME_FEAR_UPPER: float = 20.0
FEAR_UPPER: float = 35.0
NEUTRAL_UPPER: float = 65.0
GREED_UPPER: float = 80.0

# History buffer size
MAX_HISTORY: int = 252  # ~1 trading year


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SentimentSnapshot:
    """A single composite sentiment reading with component scores."""
    composite: float = 50.0
    put_call_score: float = 50.0
    vts_score: float = 50.0
    dix_score: float = 50.0
    gex_score: float = 50.0
    fear_greed_score: float = 50.0
    regime: str = "NEUTRAL"
    contrarian_bias: float = 0.0


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class SentimentComposite:
    """Combines multiple sentiment indicators into a single 0-100 composite.

    Accepts raw indicator values, normalises each to 0-100, then computes
    a weighted average.  Tracks history for momentum/trend detection.

    Usage:
        engine = SentimentComposite()
        components = {
            "put_call_ratio": 0.95,
            "vix": 18.5,
            "vix3m": 22.0,
            "dix": 0.45,
            "gex": 500_000_000,
        }
        composite = engine.compute_composite(components)
        regime = engine.get_sentiment_regime(composite)
    """

    def __init__(
        self,
        weights: Optional[dict[str, float]] = None,
    ) -> None:
        self.logger = logging.getLogger("nzt48.sentiment")
        self.weights = weights if weights is not None else dict(DEFAULT_WEIGHTS)
        self._history: deque[float] = deque(maxlen=MAX_HISTORY)
        self._last_snapshot: Optional[SentimentSnapshot] = None

    # ------------------------------------------------------------------
    # Component normalisation (each raw value -> 0-100)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_put_call_ratio(ratio: float) -> float:
        """Normalise Put/Call ratio to 0-100 (inverted).

        High P/C ratio = fear = LOW score.
        ratio >= 1.2  -> 10
        ratio <= 0.6  -> 90
        Linear interpolation between.

        Args:
            ratio: Raw put/call ratio.

        Returns:
            Normalised score 0-100.
        """
        if ratio >= PCR_FEAR_THRESHOLD:
            return 10.0
        if ratio <= PCR_GREED_THRESHOLD:
            return 90.0
        # Linear interpolation: as ratio goes UP from 0.6 to 1.2, score goes DOWN from 90 to 10
        t = (ratio - PCR_GREED_THRESHOLD) / (PCR_FEAR_THRESHOLD - PCR_GREED_THRESHOLD)
        return 90.0 - t * 80.0

    @staticmethod
    def _normalise_vix_term_structure(vix: float, vix3m: float) -> float:
        """Normalise VIX term structure ratio to 0-100.

        VIX / VIX3M >= 1.1 (backwardation / fear) -> 10
        VIX / VIX3M <= 0.85 (contango / complacency) -> 90
        Linear interpolation between.

        Args:
            vix: Current VIX level.
            vix3m: Current VIX3M (3-month VIX) level.

        Returns:
            Normalised score 0-100.
        """
        if vix3m <= 0:
            return 50.0  # Cannot compute — return neutral
        ratio = vix / vix3m

        if ratio >= VTS_FEAR_THRESHOLD:
            return 10.0
        if ratio <= VTS_GREED_THRESHOLD:
            return 90.0
        t = (ratio - VTS_GREED_THRESHOLD) / (VTS_FEAR_THRESHOLD - VTS_GREED_THRESHOLD)
        return 90.0 - t * 80.0

    @staticmethod
    def _normalise_dix(dix: float) -> float:
        """Normalise DIX (Dark Index) to 0-100.

        DIX >= 0.48 -> 80 (bullish dark-pool buying)
        DIX <= 0.40 -> 20 (bearish)
        Linear interpolation between.

        Args:
            dix: Raw DIX value (typically 0.35-0.55).

        Returns:
            Normalised score 0-100.
        """
        if dix >= DIX_BULLISH_THRESHOLD:
            return 80.0
        if dix <= DIX_BEARISH_THRESHOLD:
            return 20.0
        t = (dix - DIX_BEARISH_THRESHOLD) / (DIX_BULLISH_THRESHOLD - DIX_BEARISH_THRESHOLD)
        return 20.0 + t * 60.0

    @staticmethod
    def _normalise_gex(gex: float) -> float:
        """Normalise Gamma Exposure (GEX) to 0-100.

        Positive GEX (dealer hedging suppresses vol) -> base 60, scale by magnitude.
        Negative GEX (dealer hedging amplifies vol)  -> base 40, scale by magnitude.

        Args:
            gex: Raw GEX value (can be any magnitude).

        Returns:
            Normalised score 0-100.
        """
        if gex >= 0:
            base = GEX_POSITIVE_BASE
            # Larger positive GEX = more suppressed vol = slightly higher score
            magnitude_adj = min(abs(gex) / 1e9, 1.0) * GEX_SCALE_FACTOR
            return min(100.0, base + magnitude_adj)
        else:
            base = GEX_NEGATIVE_BASE
            # More negative GEX = more amplified vol = lower score
            magnitude_adj = min(abs(gex) / 1e9, 1.0) * GEX_SCALE_FACTOR
            return max(0.0, base - magnitude_adj)

    @staticmethod
    def _normalise_fear_greed_proxy(vix: float) -> float:
        """Normalise VIX level as a fear/greed proxy to 0-100.

        VIX < 15 -> 80 (greed/complacency)
        VIX > 30 -> 20 (fear)
        Linear interpolation between.

        Args:
            vix: Current VIX level.

        Returns:
            Normalised score 0-100.
        """
        if vix <= VIX_LOW_THRESHOLD:
            return 80.0
        if vix >= VIX_HIGH_THRESHOLD:
            return 20.0
        t = (vix - VIX_LOW_THRESHOLD) / (VIX_HIGH_THRESHOLD - VIX_LOW_THRESHOLD)
        return 80.0 - t * 60.0

    # ------------------------------------------------------------------
    # Composite computation
    # ------------------------------------------------------------------

    def compute_composite(self, components: dict) -> float:
        """Compute the weighted composite sentiment score.

        Accepts a dict of raw indicator values.  Only components that are
        present are included; their weights are re-normalised to sum to 1.0.

        Recognised keys:
            put_call_ratio (float): Raw put/call ratio
            vix (float): VIX level
            vix3m (float): VIX3M level
            dix (float): DIX value
            gex (float): GEX value

        Args:
            components: Dict of raw indicator values.

        Returns:
            Composite score 0-100.
        """
        scores: dict[str, float] = {}

        # --- Normalise each available component ---
        if "put_call_ratio" in components:
            try:
                scores["put_call_ratio"] = self._normalise_put_call_ratio(
                    float(components["put_call_ratio"]),
                )
            except (ValueError, TypeError):
                self.logger.warning("Invalid put_call_ratio value: %s", components["put_call_ratio"])

        if "vix" in components and "vix3m" in components:
            try:
                scores["vix_term_structure"] = self._normalise_vix_term_structure(
                    float(components["vix"]),
                    float(components["vix3m"]),
                )
            except (ValueError, TypeError):
                self.logger.warning("Invalid VIX term structure values.")

        if "dix" in components:
            try:
                scores["dix_score"] = self._normalise_dix(float(components["dix"]))
            except (ValueError, TypeError):
                self.logger.warning("Invalid DIX value: %s", components["dix"])

        if "gex" in components:
            try:
                scores["gex_score"] = self._normalise_gex(float(components["gex"]))
            except (ValueError, TypeError):
                self.logger.warning("Invalid GEX value: %s", components["gex"])

        if "vix" in components:
            try:
                scores["fear_greed_proxy"] = self._normalise_fear_greed_proxy(
                    float(components["vix"]),
                )
            except (ValueError, TypeError):
                self.logger.warning("Invalid VIX value for fear/greed proxy.")

        if not scores:
            self.logger.warning("No valid sentiment components provided. Returning 50.0.")
            return 50.0

        # --- Re-normalise weights for available components ---
        active_weights: dict[str, float] = {}
        for key in scores:
            weight_key = key  # score keys match weight keys
            if weight_key in self.weights:
                active_weights[key] = self.weights[weight_key]

        total_weight = sum(active_weights.values())
        if total_weight <= 0:
            # Equal weight fallback
            n = len(scores)
            active_weights = {k: 1.0 / n for k in scores}
            total_weight = 1.0

        # --- Weighted average ---
        composite = 0.0
        for key, score in scores.items():
            w = active_weights.get(key, 0.0) / total_weight
            composite += score * w

        composite = max(0.0, min(100.0, composite))

        # --- Record history and snapshot ---
        self._history.append(composite)

        self._last_snapshot = SentimentSnapshot(
            composite=composite,
            put_call_score=scores.get("put_call_ratio", 50.0),
            vts_score=scores.get("vix_term_structure", 50.0),
            dix_score=scores.get("dix_score", 50.0),
            gex_score=scores.get("gex_score", 50.0),
            fear_greed_score=scores.get("fear_greed_proxy", 50.0),
            regime=self.get_sentiment_regime(composite),
            contrarian_bias=self.get_contrarian_bias(composite),
        )

        self.logger.debug(
            "Sentiment composite: %.1f (%s) | PCR=%.0f VTS=%.0f DIX=%.0f GEX=%.0f FG=%.0f",
            composite,
            self._last_snapshot.regime,
            self._last_snapshot.put_call_score,
            self._last_snapshot.vts_score,
            self._last_snapshot.dix_score,
            self._last_snapshot.gex_score,
            self._last_snapshot.fear_greed_score,
        )

        return composite

    # ------------------------------------------------------------------
    # Regime classification
    # ------------------------------------------------------------------

    @staticmethod
    def get_sentiment_regime(composite: float) -> str:
        """Classify composite score into a sentiment regime.

        Args:
            composite: Composite sentiment score 0-100.

        Returns:
            One of: EXTREME_FEAR, FEAR, NEUTRAL, GREED, EXTREME_GREED.
        """
        if composite < EXTREME_FEAR_UPPER:
            return "EXTREME_FEAR"
        if composite < FEAR_UPPER:
            return "FEAR"
        if composite < NEUTRAL_UPPER:
            return "NEUTRAL"
        if composite < GREED_UPPER:
            return "GREED"
        return "EXTREME_GREED"

    @staticmethod
    def get_contrarian_bias(composite: float) -> float:
        """Compute a contrarian bias signal from the composite score.

        Extreme fear  => positive (contrarian bullish): +0.5 to +1.0
        Extreme greed => negative (contrarian bearish): -0.5 to -1.0
        Neutral zone  => 0.0

        Args:
            composite: Composite sentiment score 0-100.

        Returns:
            Contrarian bias from -1.0 (bearish) to +1.0 (bullish).
        """
        if composite < EXTREME_FEAR_UPPER:
            # 0 -> +1.0,  20 -> +0.5  (linear)
            t = 1.0 - (composite / EXTREME_FEAR_UPPER)
            return 0.5 + t * 0.5

        if composite < FEAR_UPPER:
            # 20 -> +0.5,  35 -> 0.0  (linear fade)
            t = (composite - EXTREME_FEAR_UPPER) / (FEAR_UPPER - EXTREME_FEAR_UPPER)
            return 0.5 * (1.0 - t)

        if composite <= NEUTRAL_UPPER:
            # Neutral zone — no contrarian signal
            return 0.0

        if composite <= GREED_UPPER:
            # 65 -> 0.0,  80 -> -0.5  (linear)
            t = (composite - NEUTRAL_UPPER) / (GREED_UPPER - NEUTRAL_UPPER)
            return -0.5 * t

        # Extreme greed: 80 -> -0.5,  100 -> -1.0
        t = (composite - GREED_UPPER) / (100.0 - GREED_UPPER)
        return -(0.5 + t * 0.5)

    # ------------------------------------------------------------------
    # Momentum / trend detection
    # ------------------------------------------------------------------

    def get_sentiment_momentum(self, lookback: int = 5) -> float:
        """Compute rate of change in the composite score over lookback days.

        Positive momentum = sentiment improving (fear -> greed).
        Negative momentum = sentiment deteriorating (greed -> fear).

        Args:
            lookback: Number of historical readings to look back.

        Returns:
            Rate of change (current - prior) or 0.0 if insufficient data.
        """
        if len(self._history) < lookback + 1:
            self.logger.debug(
                "Insufficient sentiment history (%d) for lookback=%d.",
                len(self._history),
                lookback,
            )
            return 0.0

        history_list = list(self._history)
        current = history_list[-1]
        prior = history_list[-(lookback + 1)]
        momentum = current - prior

        self.logger.debug(
            "Sentiment momentum (lookback=%d): %.2f (current=%.1f, prior=%.1f)",
            lookback,
            momentum,
            current,
            prior,
        )
        return momentum

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def last_snapshot(self) -> Optional[SentimentSnapshot]:
        """Return the most recent SentimentSnapshot, or None if no data."""
        return self._last_snapshot

    @property
    def history(self) -> list[float]:
        """Return the full composite history as a list."""
        return list(self._history)
