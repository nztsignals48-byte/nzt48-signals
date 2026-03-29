"""Conformal Prediction for Trade Signal Calibration — Books 105, 144, 99B.

Distribution-free prediction intervals with guaranteed coverage.
Unlike Bayesian methods, conformal prediction makes NO distributional
assumptions — it works for any model, any data distribution.

Key insight: narrow interval = high confidence = larger position.
Wide interval = uncertain = smaller position or skip.

Three modes:
1. Split conformal: Simple, uses calibration holdout set
2. Adaptive conformal (ACI): Online updates for non-exchangeable time series
3. Regime-conditional: Separate calibration per regime

Coverage guarantee: If target α=0.10, actual coverage will be ≥90%
regardless of the underlying distribution (assuming exchangeability
or ACI adaptation for time series).

Usage:
    from python_brain.calibration.conformal import (
        ConformalPredictor, AdaptiveConformalPredictor,
    )

    cp = ConformalPredictor(alpha=0.10)
    cp.calibrate(calibration_residuals)
    interval = cp.predict_interval(point_prediction)
    size_mult = cp.sizing_multiplier(interval)
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional, Tuple

import numpy as np

log = logging.getLogger("conformal")


@dataclass
class PredictionInterval:
    """A conformal prediction interval."""
    lower: float
    upper: float
    point: float
    width: float
    confidence: float  # 1 - alpha

    @property
    def is_narrow(self) -> bool:
        """Interval width < 1% of point prediction (high confidence)."""
        if abs(self.point) < 1e-10:
            return self.width < 0.01
        return self.width / abs(self.point) < 0.01

    @property
    def relative_width(self) -> float:
        """Width as fraction of point prediction."""
        if abs(self.point) < 1e-10:
            return float("inf")
        return self.width / abs(self.point)


class ConformalPredictor:
    """Split conformal prediction for signal calibration.

    Calibrate on historical residuals, then produce prediction intervals
    for new predictions with guaranteed coverage.
    """

    def __init__(self, alpha: float = 0.10):
        """
        Args:
            alpha: Miscoverage rate. alpha=0.10 → 90% coverage.
        """
        self.alpha = alpha
        self._quantile: float = 0.0
        self._residuals: np.ndarray = np.array([])
        self._calibrated = False

    def calibrate(self, residuals: np.ndarray) -> float:
        """Calibrate from historical prediction residuals.

        Args:
            residuals: |y_true - y_pred| for calibration set (absolute residuals)

        Returns: The conformal quantile threshold.
        """
        if len(residuals) < 10:
            log.warning("Conformal: insufficient calibration data (%d < 10)", len(residuals))
            self._quantile = float(np.max(np.abs(residuals))) if len(residuals) > 0 else 1.0
            self._calibrated = False
            return self._quantile

        self._residuals = np.abs(residuals)
        n = len(self._residuals)

        # Conformal quantile: ceil((1-alpha)(n+1))/n-th quantile
        level = math.ceil((1 - self.alpha) * (n + 1)) / n
        level = min(level, 1.0)
        self._quantile = float(np.quantile(self._residuals, level))
        self._calibrated = True

        log.info(
            "Conformal calibrated: alpha=%.2f, n=%d, quantile=%.4f",
            self.alpha, n, self._quantile,
        )
        return self._quantile

    def predict_interval(self, point_prediction: float) -> PredictionInterval:
        """Generate prediction interval around a point prediction."""
        q = self._quantile if self._calibrated else point_prediction * 0.05
        return PredictionInterval(
            lower=point_prediction - q,
            upper=point_prediction + q,
            point=point_prediction,
            width=2 * q,
            confidence=1 - self.alpha,
        )

    def sizing_multiplier(self, interval: PredictionInterval, max_width: float = 0.03) -> float:
        """Convert interval width to position sizing multiplier.

        Narrow interval → multiplier close to 1.0 (full size)
        Wide interval → multiplier close to 0.0 (reduce/skip)

        Args:
            interval: Prediction interval from predict_interval()
            max_width: Width above which multiplier = 0.0

        Returns: Multiplier in [0.0, 1.0]
        """
        rw = interval.relative_width
        if rw <= 0 or max_width <= 0:
            return 1.0
        return max(0.0, 1.0 - rw / max_width)


class AdaptiveConformalPredictor:
    """Adaptive Conformal Inference (ACI) for non-exchangeable time series.

    Standard conformal prediction assumes exchangeability (iid-like).
    Financial time series are NOT exchangeable (autocorrelation, regime changes).

    ACI adapts the conformal quantile online based on recent coverage:
    - If actual coverage > target: WIDEN intervals (too many misses)
    - If actual coverage < target: NARROW intervals (too conservative)

    Update rule: alpha_t = alpha_{t-1} + gamma * (err_t - alpha)
    where err_t = 1 if y_t outside interval, 0 otherwise.
    """

    def __init__(self, alpha: float = 0.10, gamma: float = 0.01, window: int = 200):
        self.target_alpha = alpha
        self.gamma = gamma  # Learning rate for alpha adaptation
        self._alpha_t = alpha  # Current adaptive alpha
        self._residuals: Deque[float] = deque(maxlen=window)
        self._coverage_history: Deque[bool] = deque(maxlen=window)
        self._quantile: float = 0.0

    @property
    def current_alpha(self) -> float:
        return self._alpha_t

    @property
    def empirical_coverage(self) -> float:
        if not self._coverage_history:
            return 1.0
        return sum(self._coverage_history) / len(self._coverage_history)

    def update(self, residual: float, was_covered: bool) -> float:
        """Online update with new observation.

        Args:
            residual: |y_true - y_pred| for this observation
            was_covered: Whether y_true was inside the prediction interval

        Returns: Updated conformal quantile
        """
        self._residuals.append(abs(residual))
        self._coverage_history.append(was_covered)

        # ACI alpha update
        err_t = 0.0 if was_covered else 1.0
        self._alpha_t = self._alpha_t + self.gamma * (err_t - self.target_alpha)
        self._alpha_t = max(0.01, min(0.50, self._alpha_t))  # Clamp

        # Recompute quantile
        if len(self._residuals) >= 10:
            arr = np.array(list(self._residuals))
            level = min(1.0, 1 - self._alpha_t)
            self._quantile = float(np.quantile(arr, level))
        elif self._residuals:
            self._quantile = float(max(self._residuals))

        return self._quantile

    def predict_interval(self, point_prediction: float) -> PredictionInterval:
        q = self._quantile if self._quantile > 0 else abs(point_prediction) * 0.05
        return PredictionInterval(
            lower=point_prediction - q,
            upper=point_prediction + q,
            point=point_prediction,
            width=2 * q,
            confidence=1 - self._alpha_t,
        )

    def to_dict(self) -> dict:
        return {
            "target_alpha": self.target_alpha,
            "current_alpha": round(self._alpha_t, 4),
            "empirical_coverage": round(self.empirical_coverage, 3),
            "quantile": round(self._quantile, 6),
            "n_observations": len(self._residuals),
        }
