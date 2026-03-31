"""Conformal Prediction for Calibrated Trade Signal Intervals — Book 144.

Distribution-free prediction intervals for trading signals using
conformal prediction. Unlike point predictions, conformal prediction
provides statistically valid coverage guarantees without assumptions
about the underlying distribution.

Key classes:
  NonconformityScorer: Computes nonconformity scores |y - y_hat| / sigma_hat
  ConformalSignalCalibrator: Batch calibration with coverage testing
  OnlineConformalTracker: Streaming adaptive calibration

The narrower the prediction interval, the more confident the signal.
Coverage guarantee: with probability >= 1-alpha, the true outcome
falls within the predicted interval.

State persisted to /app/data/conformal_signals/.

Usage:
    from python_brain.ml.conformal_signals import (
        ConformalSignalCalibrator, OnlineConformalTracker, NonconformityScorer,
    )
    scorer = NonconformityScorer()
    scores = scorer.score(predictions, actuals)

    cal = ConformalSignalCalibrator()
    threshold = cal.calibrate(predictions, actuals, alpha=0.10)
    lo, hi = cal.predict_interval(new_pred, threshold)
    coverage = cal.coverage_test(intervals, actuals)

    tracker = OnlineConformalTracker(alpha=0.10)
    tracker.update(prediction, actual)
    width = tracker.get_current_width()
    conf = tracker.confidence_from_width(width)
"""

from __future__ import annotations

import json
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("conformal_signals")

__all__ = [
    "NonconformityScorer",
    "ConformalSignalCalibrator",
    "OnlineConformalTracker",
]

# ── Constants ──────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/conformal_signals")
DEFAULT_ALPHA = 0.10           # 90% coverage
DEFAULT_WINDOW = 500           # Online tracker window
MIN_CALIBRATION_SAMPLES = 20   # Minimum samples for calibration
SIGMA_FLOOR = 1e-8             # Prevent division by zero


# ── Nonconformity Scorer ──────────────────────────────────────────────

class NonconformityScorer:
    """Compute nonconformity scores for conformal prediction.

    The nonconformity score measures how "strange" an observation is
    relative to the prediction. Standard score: |y - y_hat| / sigma_hat.
    Higher scores indicate worse predictions.
    """

    def __init__(self, normalize: bool = True):
        """Initialize scorer.

        Args:
            normalize: If True, divide by predicted uncertainty (sigma_hat).
                       If False, use raw absolute error.
        """
        self.normalize = normalize
        self._n_scored: int = 0
        log.info("NonconformityScorer initialized: normalize=%s", normalize)

    def score(self, predictions: np.ndarray, actuals: np.ndarray,
              sigma_hat: Optional[np.ndarray] = None) -> np.ndarray:
        """Compute nonconformity scores.

        Args:
            predictions: Predicted values, shape (n,).
            actuals: Actual observed values, shape (n,).
            sigma_hat: Predicted uncertainties, shape (n,).
                       If None and normalize=True, uses MAD of residuals.

        Returns:
            Nonconformity scores, shape (n,).
        """
        predictions = np.asarray(predictions, dtype=np.float64).ravel()
        actuals = np.asarray(actuals, dtype=np.float64).ravel()

        if predictions.shape[0] != actuals.shape[0]:
            raise ValueError(
                f"Shape mismatch: predictions={predictions.shape}, "
                f"actuals={actuals.shape}"
            )

        residuals = np.abs(actuals - predictions)

        if self.normalize:
            if sigma_hat is not None:
                sigma = np.asarray(sigma_hat, dtype=np.float64).ravel()
                sigma = np.maximum(sigma, SIGMA_FLOOR)
            else:
                # Use MAD (median absolute deviation) as robust sigma estimate
                mad = np.median(residuals)
                sigma = np.full_like(residuals, max(mad * 1.4826, SIGMA_FLOOR))
            scores = residuals / sigma
        else:
            scores = residuals

        self._n_scored += len(scores)
        return scores

    def score_single(self, prediction: float, actual: float,
                     sigma_hat: float = 1.0) -> float:
        """Score a single observation.

        Args:
            prediction: Single predicted value.
            actual: Single actual value.
            sigma_hat: Predicted uncertainty.

        Returns:
            Nonconformity score.
        """
        residual = abs(actual - prediction)
        if self.normalize:
            return residual / max(sigma_hat, SIGMA_FLOOR)
        return residual


# ── Conformal Signal Calibrator ───────────────────────────────────────

class ConformalSignalCalibrator:
    """Batch conformal prediction calibrator for trade signals.

    Uses split conformal prediction: calibrate on a calibration set,
    then apply the learned quantile threshold to new predictions.
    """

    def __init__(self, scorer: Optional[NonconformityScorer] = None):
        """Initialize calibrator.

        Args:
            scorer: NonconformityScorer instance. Creates default if None.
        """
        self._scorer = scorer or NonconformityScorer()
        self._calibration_scores: Optional[np.ndarray] = None
        self._threshold: Optional[float] = None
        self._alpha: float = DEFAULT_ALPHA
        self._n_calibrated: int = 0
        log.info("ConformalSignalCalibrator initialized")

    def calibrate(self, predictions: np.ndarray, actuals: np.ndarray,
                  alpha: float = DEFAULT_ALPHA,
                  sigma_hat: Optional[np.ndarray] = None) -> float:
        """Calibrate using a calibration dataset.

        Computes the (1-alpha) quantile of nonconformity scores.
        This threshold guarantees >= (1-alpha) coverage on exchangeable data.

        Args:
            predictions: Calibration predictions, shape (n,).
            actuals: Calibration actuals, shape (n,).
            alpha: Miscoverage rate (e.g., 0.10 for 90% coverage).
            sigma_hat: Optional predicted uncertainties.

        Returns:
            Calibrated threshold (quantile of nonconformity scores).
        """
        n = len(predictions)
        if n < MIN_CALIBRATION_SAMPLES:
            log.warning("Insufficient calibration samples: %d < %d",
                        n, MIN_CALIBRATION_SAMPLES)
            # Return a conservative high threshold
            return float(np.std(actuals) * 3.0) if len(actuals) > 0 else 1.0

        self._alpha = alpha
        self._calibration_scores = self._scorer.score(predictions, actuals, sigma_hat)

        # Conformal quantile: ceil((n+1)*(1-alpha)) / n percentile
        quantile_level = math.ceil((n + 1) * (1.0 - alpha)) / n
        quantile_level = min(quantile_level, 1.0)

        self._threshold = float(np.quantile(self._calibration_scores, quantile_level))
        self._n_calibrated = n

        log.info("Calibrated: alpha=%.2f, n=%d, threshold=%.4f, "
                 "mean_score=%.4f, max_score=%.4f",
                 alpha, n, self._threshold,
                 float(np.mean(self._calibration_scores)),
                 float(np.max(self._calibration_scores)))

        return self._threshold

    def predict_interval(self, prediction: float,
                         threshold: Optional[float] = None,
                         sigma_hat: float = 1.0) -> Tuple[float, float]:
        """Construct a prediction interval for a new prediction.

        Args:
            prediction: Point prediction.
            threshold: Nonconformity threshold from calibrate().
                       Uses stored threshold if None.
            sigma_hat: Predicted uncertainty for this point.

        Returns:
            Tuple (lower, upper) bounds of the prediction interval.
        """
        t = threshold if threshold is not None else self._threshold
        if t is None:
            raise RuntimeError("Must call calibrate() before predict_interval()")

        # Interval width depends on whether scores are normalized
        if self._scorer.normalize:
            half_width = t * max(sigma_hat, SIGMA_FLOOR)
        else:
            half_width = t

        lower = prediction - half_width
        upper = prediction + half_width
        return (float(lower), float(upper))

    def predict_intervals_batch(self, predictions: np.ndarray,
                                threshold: Optional[float] = None,
                                sigma_hat: Optional[np.ndarray] = None
                                ) -> Tuple[np.ndarray, np.ndarray]:
        """Construct prediction intervals for a batch of predictions.

        Args:
            predictions: Point predictions, shape (n,).
            threshold: Nonconformity threshold. Uses stored if None.
            sigma_hat: Per-prediction uncertainties. Uses 1.0 if None.

        Returns:
            Tuple (lower_bounds, upper_bounds), each shape (n,).
        """
        t = threshold if threshold is not None else self._threshold
        if t is None:
            raise RuntimeError("Must call calibrate() before predict_intervals_batch()")

        predictions = np.asarray(predictions, dtype=np.float64).ravel()

        if self._scorer.normalize:
            if sigma_hat is not None:
                sigma = np.maximum(np.asarray(sigma_hat, dtype=np.float64).ravel(),
                                   SIGMA_FLOOR)
            else:
                sigma = np.ones_like(predictions)
            half_widths = t * sigma
        else:
            half_widths = np.full_like(predictions, t)

        return predictions - half_widths, predictions + half_widths

    def coverage_test(self, intervals: List[Tuple[float, float]],
                      actuals: np.ndarray) -> Dict[str, Any]:
        """Test empirical coverage of prediction intervals.

        Args:
            intervals: List of (lower, upper) tuples.
            actuals: Actual observed values.

        Returns:
            Dict with coverage rate, average width, and pass/fail status.
        """
        actuals = np.asarray(actuals, dtype=np.float64).ravel()
        n = len(actuals)

        if n == 0 or len(intervals) == 0:
            return {"coverage": 0.0, "n": 0, "status": "no_data"}

        n_covered = 0
        widths = []

        for i, (lo, hi) in enumerate(intervals):
            if i >= n:
                break
            if lo <= actuals[i] <= hi:
                n_covered += 1
            widths.append(hi - lo)

        effective_n = min(n, len(intervals))
        coverage = n_covered / effective_n if effective_n > 0 else 0.0
        target_coverage = 1.0 - self._alpha

        return {
            "coverage": round(coverage, 4),
            "target_coverage": round(target_coverage, 4),
            "n_samples": effective_n,
            "n_covered": n_covered,
            "avg_width": round(float(np.mean(widths)), 4) if widths else 0.0,
            "median_width": round(float(np.median(widths)), 4) if widths else 0.0,
            "max_width": round(float(np.max(widths)), 4) if widths else 0.0,
            "pass": coverage >= target_coverage - 0.02,  # 2% tolerance
            "calibration_gap": round(coverage - target_coverage, 4),
        }

    @property
    def summary(self) -> Dict[str, Any]:
        """Calibration summary."""
        result: Dict[str, Any] = {
            "n_calibrated": self._n_calibrated,
            "alpha": self._alpha,
            "threshold": round(self._threshold, 6) if self._threshold else None,
        }
        if self._calibration_scores is not None and len(self._calibration_scores) > 0:
            result["score_stats"] = {
                "mean": round(float(np.mean(self._calibration_scores)), 4),
                "std": round(float(np.std(self._calibration_scores)), 4),
                "median": round(float(np.median(self._calibration_scores)), 4),
                "p95": round(float(np.percentile(self._calibration_scores, 95)), 4),
            }
        return result


# ── Online Conformal Tracker ──────────────────────────────────────────

class OnlineConformalTracker:
    """Streaming conformal prediction tracker.

    Maintains a sliding window of nonconformity scores and adaptively
    updates the prediction interval width. Suitable for non-stationary
    trading signals where the calibration set must evolve over time.
    """

    def __init__(self, alpha: float = DEFAULT_ALPHA,
                 window_size: int = DEFAULT_WINDOW,
                 scorer: Optional[NonconformityScorer] = None):
        """Initialize online tracker.

        Args:
            alpha: Target miscoverage rate.
            window_size: Number of recent scores to maintain.
            scorer: NonconformityScorer. Creates default if None.
        """
        self._alpha = alpha
        self._window_size = window_size
        self._scorer = scorer or NonconformityScorer()

        self._scores: deque = deque(maxlen=window_size)
        self._widths: deque = deque(maxlen=window_size)
        self._n_updates: int = 0
        self._n_covered: int = 0
        self._current_threshold: float = 1.0

        # Adaptive alpha (tightens/loosens based on recent coverage)
        self._adaptive_alpha = alpha
        self._coverage_history: deque = deque(maxlen=100)

        log.info("OnlineConformalTracker initialized: alpha=%.2f, window=%d",
                 alpha, window_size)

    def update(self, prediction: float, actual: float,
               sigma_hat: float = 1.0) -> None:
        """Process a new (prediction, actual) pair.

        Updates the nonconformity score window and recomputes the threshold.

        Args:
            prediction: Model's point prediction.
            actual: Realized value.
            sigma_hat: Predicted uncertainty.
        """
        score = self._scorer.score_single(prediction, actual, sigma_hat)
        self._scores.append(score)
        self._n_updates += 1

        # Check if previous interval covered this actual
        lo, hi = self._make_interval(prediction, sigma_hat)
        covered = lo <= actual <= hi
        self._coverage_history.append(1.0 if covered else 0.0)
        if covered:
            self._n_covered += 1

        # Recompute threshold from current window
        if len(self._scores) >= MIN_CALIBRATION_SAMPLES:
            scores_arr = np.array(self._scores)
            n = len(scores_arr)
            q_level = min(math.ceil((n + 1) * (1.0 - self._adaptive_alpha)) / n, 1.0)
            self._current_threshold = float(np.quantile(scores_arr, q_level))

            # Adaptive alpha: adjust if coverage is drifting
            if len(self._coverage_history) >= 50:
                recent_coverage = float(np.mean(list(self._coverage_history)[-50:]))
                target = 1.0 - self._alpha
                gap = recent_coverage - target

                # If over-covering (too wide), increase alpha slightly
                # If under-covering (too narrow), decrease alpha
                adjustment = 0.005 * np.sign(gap) * min(abs(gap), 0.05)
                self._adaptive_alpha = float(np.clip(
                    self._adaptive_alpha + adjustment,
                    self._alpha * 0.5,
                    min(self._alpha * 2.0, 0.50),
                ))

        width = self._current_width_for(sigma_hat)
        self._widths.append(width)

    def _make_interval(self, prediction: float,
                       sigma_hat: float) -> Tuple[float, float]:
        """Construct interval using current threshold."""
        if self._scorer.normalize:
            hw = self._current_threshold * max(sigma_hat, SIGMA_FLOOR)
        else:
            hw = self._current_threshold
        return (prediction - hw, prediction + hw)

    def _current_width_for(self, sigma_hat: float = 1.0) -> float:
        """Compute current interval width."""
        if self._scorer.normalize:
            return 2.0 * self._current_threshold * max(sigma_hat, SIGMA_FLOOR)
        return 2.0 * self._current_threshold

    def get_current_width(self) -> float:
        """Get the current prediction interval width.

        Returns:
            Width of the prediction interval at sigma_hat=1.0.
        """
        return self._current_width_for(1.0)

    def get_interval(self, prediction: float,
                     sigma_hat: float = 1.0) -> Tuple[float, float]:
        """Get prediction interval for a new prediction.

        Args:
            prediction: New point prediction.
            sigma_hat: Predicted uncertainty.

        Returns:
            (lower, upper) bounds.
        """
        return self._make_interval(prediction, sigma_hat)

    def confidence_from_width(self, width: float) -> float:
        """Convert interval width to a confidence score.

        Narrower intervals indicate higher confidence.
        Uses the historical width distribution to compute a percentile.

        Args:
            width: Prediction interval width.

        Returns:
            Confidence in [0, 1] where 1 = very narrow (high confidence).
        """
        if len(self._widths) < 10:
            # Not enough history — return moderate confidence
            return 0.5

        widths_arr = np.array(self._widths)
        median_w = float(np.median(widths_arr))

        if median_w < SIGMA_FLOOR:
            return 0.5

        # Ratio: width relative to median
        ratio = width / median_w

        # Sigmoid-like mapping: ratio < 1 → high confidence
        # ratio = 0.5 → ~0.8 confidence
        # ratio = 1.0 → ~0.5 confidence
        # ratio = 2.0 → ~0.2 confidence
        confidence = 1.0 / (1.0 + math.exp(2.0 * (ratio - 1.0)))
        return float(np.clip(confidence, 0.01, 0.99))

    @property
    def empirical_coverage(self) -> float:
        """Current empirical coverage rate."""
        if self._n_updates == 0:
            return 0.0
        return self._n_covered / self._n_updates

    @property
    def summary(self) -> Dict[str, Any]:
        """Tracker summary."""
        result: Dict[str, Any] = {
            "n_updates": self._n_updates,
            "alpha": self._alpha,
            "adaptive_alpha": round(self._adaptive_alpha, 4),
            "threshold": round(self._current_threshold, 6),
            "current_width": round(self.get_current_width(), 4),
            "empirical_coverage": round(self.empirical_coverage, 4),
            "target_coverage": round(1.0 - self._alpha, 4),
            "window_fill": f"{len(self._scores)}/{self._window_size}",
        }
        if len(self._widths) > 0:
            widths_arr = np.array(self._widths)
            result["width_stats"] = {
                "mean": round(float(np.mean(widths_arr)), 4),
                "std": round(float(np.std(widths_arr)), 4),
                "min": round(float(np.min(widths_arr)), 4),
                "max": round(float(np.max(widths_arr)), 4),
            }
        return result

    def save(self, path: str = "/app/data/conformal_signals/tracker.json") -> None:
        """Persist tracker state."""
        save_path = Path(path)
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "alpha": self._alpha,
                "adaptive_alpha": self._adaptive_alpha,
                "threshold": self._current_threshold,
                "scores": list(self._scores),
                "n_updates": self._n_updates,
                "n_covered": self._n_covered,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with open(str(save_path), "w") as f:
                json.dump(state, f, indent=2)
            log.info("OnlineConformalTracker saved to %s", path)
        except Exception as e:
            log.error("Failed to save tracker to %s: %s", path, e)

    def load(self, path: str = "/app/data/conformal_signals/tracker.json") -> None:
        """Load tracker state."""
        try:
            with open(path, "r") as f:
                state = json.load(f)
            self._alpha = state.get("alpha", self._alpha)
            self._adaptive_alpha = state.get("adaptive_alpha", self._alpha)
            self._current_threshold = state.get("threshold", 1.0)
            self._n_updates = state.get("n_updates", 0)
            self._n_covered = state.get("n_covered", 0)
            for s in state.get("scores", []):
                self._scores.append(s)
            log.info("OnlineConformalTracker loaded: %d scores, threshold=%.4f",
                     len(self._scores), self._current_threshold)
        except FileNotFoundError:
            log.info("No saved tracker at %s — starting fresh", path)
        except Exception as e:
            log.error("Failed to load tracker from %s: %s", path, e)
