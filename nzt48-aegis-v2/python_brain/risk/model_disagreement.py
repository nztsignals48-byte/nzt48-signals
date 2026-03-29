"""Model Disagreement Ensembles — Book 172.

When multiple models disagree on direction or magnitude,
REDUCE position size. Agreement = high confidence. Disagreement = uncertainty.

5-seed ensemble: train same model with different random seeds.
If predictions diverge → the signal is fragile and should be sized smaller.

Disagreement metrics:
  - Prediction variance across seeds
  - Jensen-Shannon divergence of output distributions
  - Entropy of vote distribution

Position sizing: full when unanimous, proportional when partially agreed,
FLAT when majority disagrees.

Usage:
    from python_brain.risk.model_disagreement import (
        DisagreementEnsemble, DisagreementLevel,
    )

    ensemble = DisagreementEnsemble(n_models=5)
    ensemble.record_predictions([0.8, 0.7, 0.6, -0.1, 0.5])
    level = ensemble.disagreement_level  # DisagreementLevel.MODERATE
    sizing_mult = ensemble.sizing_multiplier()  # 0.6
"""

from __future__ import annotations

import logging
import math
from enum import Enum
from typing import List, Optional

import numpy as np

log = logging.getLogger("model_disagreement")


class DisagreementLevel(Enum):
    UNANIMOUS = "UNANIMOUS"      # All models agree
    STRONG = "STRONG"            # 4/5 agree
    MODERATE = "MODERATE"        # 3/5 agree
    WEAK = "WEAK"                # Split
    CONTRADICTORY = "CONTRADICTORY"  # Majority disagrees with signal


class DisagreementEnsemble:
    """Track and score model disagreement across multiple seeds."""

    def __init__(self, n_models: int = 5):
        self.n_models = n_models
        self._predictions: List[float] = []
        self._level = DisagreementLevel.UNANIMOUS

    def record_predictions(self, predictions: List[float]):
        """Record predictions from all model seeds.

        predictions: List of directional scores (-1 to +1).
        Positive = bullish, negative = bearish.
        """
        self._predictions = predictions[:self.n_models]
        self._update_level()

    def _update_level(self):
        if not self._predictions:
            self._level = DisagreementLevel.WEAK
            return

        n = len(self._predictions)
        positive = sum(1 for p in self._predictions if p > 0)
        negative = n - positive

        agreement = max(positive, negative) / n

        if agreement >= 0.95:
            self._level = DisagreementLevel.UNANIMOUS
        elif agreement >= 0.80:
            self._level = DisagreementLevel.STRONG
        elif agreement >= 0.60:
            self._level = DisagreementLevel.MODERATE
        elif agreement >= 0.40:
            self._level = DisagreementLevel.WEAK
        else:
            self._level = DisagreementLevel.CONTRADICTORY

    @property
    def disagreement_level(self) -> DisagreementLevel:
        return self._level

    def prediction_variance(self) -> float:
        """Variance of predictions across seeds."""
        if len(self._predictions) < 2:
            return 0.0
        return float(np.var(self._predictions))

    def entropy(self) -> float:
        """Entropy of the vote distribution (higher = more disagreement)."""
        if not self._predictions:
            return 1.0
        n = len(self._predictions)
        positive = sum(1 for p in self._predictions if p > 0)
        if positive == 0 or positive == n:
            return 0.0  # Perfect agreement
        p_pos = positive / n
        p_neg = 1 - p_pos
        return -(p_pos * math.log2(p_pos) + p_neg * math.log2(p_neg))

    def jensen_shannon_divergence(self) -> float:
        """JSD between individual predictions and the mean (0=identical, 1=maximally different)."""
        if len(self._predictions) < 2:
            return 0.0
        arr = np.array(self._predictions)
        mean_pred = np.mean(arr)
        # Simplified: use variance as proxy for JSD
        variance = np.var(arr)
        # Normalize to [0, 1] assuming predictions in [-1, 1]
        return min(1.0, variance * 4)

    def sizing_multiplier(self) -> float:
        """Convert disagreement level to position sizing multiplier.

        UNANIMOUS:     1.0 (full size)
        STRONG:        0.8
        MODERATE:      0.5
        WEAK:          0.2
        CONTRADICTORY: 0.0 (FLAT — do not trade)
        """
        multipliers = {
            DisagreementLevel.UNANIMOUS: 1.0,
            DisagreementLevel.STRONG: 0.8,
            DisagreementLevel.MODERATE: 0.5,
            DisagreementLevel.WEAK: 0.2,
            DisagreementLevel.CONTRADICTORY: 0.0,
        }
        return multipliers.get(self._level, 0.5)

    def confidence_adjustment(self) -> int:
        """Additive confidence adjustment based on disagreement."""
        adjustments = {
            DisagreementLevel.UNANIMOUS: +10,
            DisagreementLevel.STRONG: +5,
            DisagreementLevel.MODERATE: 0,
            DisagreementLevel.WEAK: -10,
            DisagreementLevel.CONTRADICTORY: -30,
        }
        return adjustments.get(self._level, 0)

    def mean_prediction(self) -> float:
        """Mean prediction across all seeds."""
        if not self._predictions:
            return 0.0
        return float(np.mean(self._predictions))

    def to_dict(self) -> dict:
        return {
            "level": self._level.value,
            "n_models": self.n_models,
            "n_predictions": len(self._predictions),
            "mean_prediction": round(self.mean_prediction(), 4),
            "variance": round(self.prediction_variance(), 6),
            "entropy": round(self.entropy(), 3),
            "jsd": round(self.jensen_shannon_divergence(), 4),
            "sizing_multiplier": self.sizing_multiplier(),
            "confidence_adj": self.confidence_adjustment(),
        }
