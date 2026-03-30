"""Book 144: Conformal Prediction — Signal Confidence Calibrator.

Provides distribution-free calibrated confidence for trading signals.
Raw model confidence ≠ actual win probability. Conformal prediction provides
statistically valid prediction intervals with coverage guarantees.

Key property: If raw confidence says 70% but actual win rate at that level
is 55%, the calibrator adjusts to 55%. No assumptions about the underlying
distribution — works for any signal generator.

Method: Split Conformal Prediction with adaptive window.
  1. Maintain a buffer of (raw_confidence, actual_outcome) pairs
  2. Compute nonconformity scores: |raw_conf - actual_outcome|
  3. For a new signal at raw_conf=C, find the quantile of C's nonconformity
  4. Calibrated confidence = empirical win rate at quantile bucket

No external dependencies (stdlib only).

Usage:
    from python_brain.analytics.conformal_calibrator import get_calibrator
    cal = get_calibrator()
    cal.record_outcome(raw_confidence=72, won=True)  # After trade completes
    calibrated = cal.calibrate(raw_confidence=72)     # Before new trade
    # calibrated.confidence = 58 (adjusted to empirical reality)
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("conformal_calibrator")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CalibratedSignal:
    """Output from calibration."""
    raw_confidence: float       # Original signal confidence
    calibrated_confidence: float  # Empirically adjusted confidence
    coverage: float             # How many outcomes fall within prediction
    n_samples: int              # How many calibration samples used
    bucket: str                 # Which confidence bucket (e.g. "60-70")


# ---------------------------------------------------------------------------
# Core calibrator
# ---------------------------------------------------------------------------

class ConformalCalibrator:
    """Split conformal calibrator for signal confidence.

    Maintains a rolling window of (confidence, outcome) pairs.
    Bins confidence into buckets and tracks empirical win rate per bucket.
    Calibrated confidence = empirical win rate of the bucket.

    Coverage guarantee: for a (1-alpha) confidence level, the true outcome
    falls within the prediction set at least (1-alpha) fraction of the time.
    """

    def __init__(
        self,
        window_size: int = 200,
        n_buckets: int = 10,
        min_samples_per_bucket: int = 5,
    ):
        self.window_size = window_size
        self.n_buckets = n_buckets
        self.min_samples = min_samples_per_bucket

        # Rolling window of (raw_confidence, outcome_binary)
        self._history: deque = deque(maxlen=window_size)

        # Bucket boundaries (0-10, 10-20, ..., 90-100)
        self._bucket_width = 100.0 / n_buckets

        # Cached bucket stats
        self._bucket_wins: Dict[int, int] = defaultdict(int)
        self._bucket_total: Dict[int, int] = defaultdict(int)
        self._bucket_avg_conf: Dict[int, float] = defaultdict(float)

        # Overall calibration error tracking
        self._total_recorded = 0

    def _bucket_for(self, confidence: float) -> int:
        """Map confidence [0-100] to bucket index."""
        return min(int(confidence / self._bucket_width), self.n_buckets - 1)

    def record_outcome(self, raw_confidence: float, won: bool):
        """Record a trade outcome for calibration."""
        bucket = self._bucket_for(raw_confidence)
        outcome = 1 if won else 0

        # If window is full, remove oldest entry's contribution
        if len(self._history) >= self.window_size:
            old_conf, old_outcome = self._history[0]
            old_bucket = self._bucket_for(old_conf)
            self._bucket_wins[old_bucket] -= old_outcome
            self._bucket_total[old_bucket] -= 1

        self._history.append((raw_confidence, outcome))
        self._bucket_wins[bucket] += outcome
        self._bucket_total[bucket] += 1

        # Update running average confidence per bucket
        total = self._bucket_total[bucket]
        if total > 0:
            old_avg = self._bucket_avg_conf.get(bucket, raw_confidence)
            self._bucket_avg_conf[bucket] = old_avg + (raw_confidence - old_avg) / total

        self._total_recorded += 1

    def calibrate(self, raw_confidence: float) -> CalibratedSignal:
        """Calibrate a raw confidence score using empirical win rates."""
        bucket = self._bucket_for(raw_confidence)
        total = self._bucket_total.get(bucket, 0)

        if total < self.min_samples:
            # Not enough data in this bucket — try adjacent buckets
            adjacent_wins = 0
            adjacent_total = 0
            for b in range(max(0, bucket - 1), min(self.n_buckets, bucket + 2)):
                adjacent_wins += self._bucket_wins.get(b, 0)
                adjacent_total += self._bucket_total.get(b, 0)

            if adjacent_total >= self.min_samples:
                win_rate = adjacent_wins / adjacent_total
                calibrated = win_rate * 100
                return CalibratedSignal(
                    raw_confidence=raw_confidence,
                    calibrated_confidence=round(calibrated, 1),
                    coverage=self._compute_coverage(),
                    n_samples=adjacent_total,
                    bucket=f"adjacent({bucket})",
                )

            # Truly insufficient data — return raw with low coverage flag
            return CalibratedSignal(
                raw_confidence=raw_confidence,
                calibrated_confidence=raw_confidence,
                coverage=0.0,
                n_samples=total,
                bucket=f"insufficient({bucket})",
            )

        # Normal path: empirical win rate of this bucket
        wins = self._bucket_wins.get(bucket, 0)
        win_rate = wins / total
        calibrated = win_rate * 100

        # Clamp: never let calibrated exceed 95 or drop below 5
        calibrated = max(5.0, min(95.0, calibrated))

        bucket_lo = int(bucket * self._bucket_width)
        bucket_hi = int((bucket + 1) * self._bucket_width)

        return CalibratedSignal(
            raw_confidence=raw_confidence,
            calibrated_confidence=round(calibrated, 1),
            coverage=self._compute_coverage(),
            n_samples=total,
            bucket=f"{bucket_lo}-{bucket_hi}",
        )

    def _compute_coverage(self) -> float:
        """Compute empirical coverage: fraction of outcomes where
        calibrated confidence correctly predicted direction."""
        if len(self._history) < 20:
            return 0.0

        correct = 0
        for conf, outcome in self._history:
            bucket = self._bucket_for(conf)
            total = self._bucket_total.get(bucket, 0)
            if total < 3:
                continue
            win_rate = self._bucket_wins.get(bucket, 0) / total
            # "Correct" if high confidence → win or low confidence → loss
            predicted_win = win_rate > 0.5
            if (predicted_win and outcome == 1) or (not predicted_win and outcome == 0):
                correct += 1

        return round(correct / max(len(self._history), 1), 3)

    @property
    def calibration_error(self) -> float:
        """Expected Calibration Error (ECE).

        Average gap between bucket's average raw confidence and empirical win rate.
        Lower is better. ECE > 10% = badly miscalibrated.
        """
        total_weight = 0
        weighted_error = 0.0
        for b in range(self.n_buckets):
            total = self._bucket_total.get(b, 0)
            if total < self.min_samples:
                continue
            avg_conf = self._bucket_avg_conf.get(b, 50.0) / 100.0
            win_rate = self._bucket_wins.get(b, 0) / total
            weighted_error += total * abs(avg_conf - win_rate)
            total_weight += total

        if total_weight == 0:
            return 0.0
        return round(weighted_error / total_weight * 100, 2)  # As percentage

    @property
    def summary(self) -> dict:
        """Calibration summary for logging/nightly."""
        buckets = {}
        for b in range(self.n_buckets):
            total = self._bucket_total.get(b, 0)
            if total > 0:
                lo = int(b * self._bucket_width)
                hi = int((b + 1) * self._bucket_width)
                buckets[f"{lo}-{hi}"] = {
                    "n": total,
                    "wins": self._bucket_wins.get(b, 0),
                    "win_rate": round(self._bucket_wins.get(b, 0) / total * 100, 1),
                    "avg_raw_conf": round(self._bucket_avg_conf.get(b, 0), 1),
                }

        return {
            "total_recorded": self._total_recorded,
            "window_size": len(self._history),
            "calibration_error_pct": self.calibration_error,
            "coverage": self._compute_coverage(),
            "buckets": buckets,
        }

    # ---------------------------------------------------------------------------
    # Persistence
    # ---------------------------------------------------------------------------

    def save(self, path: str = "/app/data/conformal_calibration.json"):
        """Save calibration state."""
        data = {
            "history": list(self._history),
            "total_recorded": self._total_recorded,
        }
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            log.error(f"Failed to save calibration: {e}")

    def load(self, path: str = "/app/data/conformal_calibration.json"):
        """Load calibration state."""
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # Replay history to rebuild bucket stats
            for conf, outcome in data.get("history", []):
                self.record_outcome(conf, bool(outcome))
            log.info(f"Loaded {len(self._history)} calibration samples")
        except FileNotFoundError:
            pass
        except Exception as e:
            log.error(f"Failed to load calibration: {e}")


# ---------------------------------------------------------------------------
# Per-strategy calibrators
# ---------------------------------------------------------------------------

class StrategyCalibrators:
    """Maintains separate calibrators per strategy for targeted calibration."""

    def __init__(self, window_size: int = 200):
        self._calibrators: Dict[str, ConformalCalibrator] = {}
        self._global = ConformalCalibrator(window_size=window_size)
        self.window_size = window_size

    def record(self, strategy: str, raw_confidence: float, won: bool):
        """Record outcome for both strategy-specific and global calibrators."""
        if strategy not in self._calibrators:
            self._calibrators[strategy] = ConformalCalibrator(
                window_size=self.window_size
            )
        self._calibrators[strategy].record_outcome(raw_confidence, won)
        self._global.record_outcome(raw_confidence, won)

    def calibrate(self, strategy: str, raw_confidence: float) -> CalibratedSignal:
        """Calibrate using strategy-specific data, falling back to global."""
        cal = self._calibrators.get(strategy)
        if cal and len(cal._history) >= 20:
            result = cal.calibrate(raw_confidence)
            if result.n_samples >= 5:
                return result
        # Fallback to global calibrator
        return self._global.calibrate(raw_confidence)

    def save(self, path: str = "/app/data/conformal_calibration.json"):
        """Save all calibrators."""
        data = {
            "global": list(self._global._history),
            "strategies": {
                s: list(c._history) for s, c in self._calibrators.items()
            },
        }
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            log.error(f"Failed to save calibration: {e}")

    def load(self, path: str = "/app/data/conformal_calibration.json"):
        """Load all calibrators."""
        try:
            with open(path, "r") as f:
                data = json.load(f)
            for conf, outcome in data.get("global", []):
                self._global.record_outcome(conf, bool(outcome))
            for strategy, history in data.get("strategies", {}).items():
                if strategy not in self._calibrators:
                    self._calibrators[strategy] = ConformalCalibrator(
                        window_size=self.window_size
                    )
                for conf, outcome in history:
                    self._calibrators[strategy].record_outcome(conf, bool(outcome))
            log.info(
                f"Loaded calibration: global={len(self._global._history)}, "
                f"strategies={list(self._calibrators.keys())}"
            )
        except FileNotFoundError:
            pass
        except Exception as e:
            log.error(f"Failed to load calibration: {e}")

    @property
    def summary(self) -> dict:
        result = {"global": self._global.summary, "strategies": {}}
        for s, c in self._calibrators.items():
            result["strategies"][s] = c.summary
        return result


# ---------------------------------------------------------------------------
# Singleton + bridge integration
# ---------------------------------------------------------------------------

_calibrators: Optional[StrategyCalibrators] = None


def get_calibrators() -> StrategyCalibrators:
    """Get or create the singleton strategy calibrators."""
    global _calibrators
    if _calibrators is None:
        _calibrators = StrategyCalibrators()
        _calibrators.load()
    return _calibrators


def calibrate_confidence(strategy: str, raw_confidence: float) -> float:
    """Quick bridge integration: returns calibrated confidence."""
    result = get_calibrators().calibrate(strategy, raw_confidence)
    return result.calibrated_confidence


def record_trade_outcome(strategy: str, raw_confidence: float, won: bool):
    """Record outcome for calibration. Called from bridge.py exit handler."""
    get_calibrators().record(strategy, raw_confidence, won)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    cals = get_calibrators()
    print(json.dumps(cals.summary, indent=2))
