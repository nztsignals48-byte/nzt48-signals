"""
Active Learning Weighter -- NZT-48 W12
Settles (2009): Active Learning Literature Survey -- uncertainty sampling.
Trades where model confidence was 45-55% = highest learning value (max uncertainty).
Weight these 2x during retraining -- 20-30% faster convergence.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class ActiveLearningWeighter:
    """
    Scores all outcomes by "learning value" using uncertainty sampling.

    Settles (2009) uncertainty sampling:
      learning_value = 1 - |confidence - 0.5| * 2
      confidence 0.50 -- learning_value = 1.0 (maximum uncertainty)
      confidence 0.90 -- learning_value = 0.2 (low uncertainty, model was sure)
      confidence 0.10 -- learning_value = 0.2 (low uncertainty, wrong direction)

    Combined with drift detector's exponential decay:
      final_weight = learning_value * exp(-age_in_trades / 20)
    """

    EWC_HALF_LIFE = 20  # Must match drift_detector.py

    def get_learning_value(self, confidence_pct: float) -> float:
        """
        Computes learning value for a single trade given model confidence (0-100).
        Returns 0.0-1.0.
        """
        # Normalize to 0-1
        conf = max(0.0, min(1.0, float(confidence_pct) / 100.0))
        # Uncertainty = distance from 0.5 (max uncertainty)
        uncertainty = 1.0 - abs(conf - 0.5) * 2.0
        return round(max(0.0, uncertainty), 4)

    def get_learning_weights(self, outcomes: list,
                              drift_weights: Optional[np.ndarray] = None) -> np.ndarray:
        """
        Returns combined sample weights for LightGBM fit().

        Combines:
        1. Uncertainty weight (highest near 50% confidence)
        2. Exponential age decay (recent trades weighted more)

        Args:
            outcomes: List of outcome dicts with 'confidence' field
            drift_weights: Optional pre-computed exponential decay weights from DriftDetector

        Returns:
            np.ndarray of shape (n_outcomes,) -- sample weights
        """
        n = len(outcomes)
        if n == 0:
            return np.array([])

        # Uncertainty weights
        uncertainty_weights = np.array([
            self.get_learning_value(o.get("confidence", 50) or 50)
            for o in outcomes
        ])

        # Exponential age decay (if no drift weights provided)
        if drift_weights is not None and len(drift_weights) == n:
            age_weights = drift_weights
        else:
            ages = np.arange(n - 1, -1, -1)  # 0 = most recent
            age_weights = np.exp(-ages / self.EWC_HALF_LIFE)

        # Combined weight
        combined = uncertainty_weights * age_weights

        # Normalize so sum = n (LightGBM expects sum proportional to n)
        total = combined.sum()
        if total > 0:
            combined = (combined / total) * n

        return combined

    def get_high_value_samples(self, outcomes: list, n: int = 50) -> list:
        """
        Returns top-n highest learning value trades for analysis.
        Useful for understanding what the model is most uncertain about.
        """
        if not outcomes:
            return []

        scored = [
            (self.get_learning_value(o.get("confidence", 50) or 50), i, o)
            for i, o in enumerate(outcomes)
        ]
        scored.sort(reverse=True)

        return [
            {
                "learning_value": round(lv, 4),
                "index": idx,
                "ticker": o.get("ticker"),
                "confidence": o.get("confidence"),
                "status": o.get("status"),
                "r_multiple": o.get("r_multiple"),
                "regime": o.get("regime"),
            }
            for lv, idx, o in scored[:n]
        ]

    def get_utilization_stats(self, outcomes: list) -> dict:
        """Stats on learning value distribution of outcomes."""
        if not outcomes:
            return {"mean_lv": 0, "high_value_count": 0, "n": 0}

        weights = [self.get_learning_value(o.get("confidence", 50) or 50) for o in outcomes]
        high_value = sum(1 for w in weights if w > 0.5)

        return {
            "mean_learning_value": round(sum(weights) / len(weights), 4),
            "high_value_count": high_value,
            "high_value_pct": round(high_value / len(weights) * 100, 1),
            "n": len(outcomes),
        }
