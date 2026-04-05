"""Signal Outlier Detection — catch anomalous signals before execution.

Uses statistical methods to detect when a signal's features are
abnormal compared to recent history. Flags potential data errors,
stale quotes, or regime transitions before they cause losses.

Approach: Mahalanobis distance on feature vector with rolling stats.
No external dependency — pure numpy implementation avoids PyOD's heavy deps.

License dependency: numpy (BSD-3).
"""

import json
import os
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple


class RollingStats:
    """Online computation of rolling mean and covariance."""
    __slots__ = ('n', 'mean', 'M2', 'dim')

    def __init__(self, dim: int):
        self.dim = dim
        self.n = 0
        self.mean = [0.0] * dim
        self.M2 = [[0.0] * dim for _ in range(dim)]

    def update(self, x: List[float]) -> None:
        """Welford's online algorithm for mean + covariance."""
        if len(x) != self.dim:
            return
        self.n += 1
        delta = [xi - mi for xi, mi in zip(x, self.mean)]
        self.mean = [mi + di / self.n for mi, di in zip(self.mean, delta)]
        delta2 = [xi - mi for xi, mi in zip(x, self.mean)]
        for i in range(self.dim):
            for j in range(self.dim):
                self.M2[i][j] += delta[i] * delta2[j]

    def covariance(self) -> Optional[List[List[float]]]:
        if self.n < 10:
            return None
        return [[self.M2[i][j] / (self.n - 1) for j in range(self.dim)]
                for i in range(self.dim)]

    def mahalanobis(self, x: List[float]) -> float:
        """Compute Mahalanobis distance of x from running distribution."""
        if self.n < 10 or len(x) != self.dim:
            return 0.0

        cov = self.covariance()
        if cov is None:
            return 0.0

        # Regularize diagonal
        for i in range(self.dim):
            cov[i][i] += 1e-6

        # Simple 2x2 or diagonal inverse for speed
        # For small dim (<8), use explicit inversion
        diff = [xi - mi for xi, mi in zip(x, self.mean)]

        if self.dim <= 3:
            # Diagonal approximation (fast, good enough for outlier detection)
            dist_sq = sum(d * d / max(cov[i][i], 1e-10)
                          for i, d in enumerate(diff))
        else:
            # Full Mahalanobis with Cholesky (for larger feature vectors)
            dist_sq = self._full_mahalanobis(diff, cov)

        return dist_sq ** 0.5

    def _full_mahalanobis(self, diff: List[float], cov: List[List[float]]) -> float:
        """Full Mahalanobis via Cholesky decomposition."""
        n = len(diff)
        # Cholesky: cov = L * L^T
        L = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1):
                s = sum(L[i][k] * L[j][k] for k in range(j))
                if i == j:
                    val = cov[i][i] - s
                    L[i][j] = val ** 0.5 if val > 0 else 1e-6
                else:
                    L[i][j] = (cov[i][j] - s) / max(L[j][j], 1e-10)

        # Solve L * y = diff
        y = [0.0] * n
        for i in range(n):
            y[i] = (diff[i] - sum(L[i][k] * y[k] for k in range(i))) / max(L[i][i], 1e-10)

        return sum(yi * yi for yi in y)


class SignalOutlierDetector:
    """Detect anomalous signals using feature-space outlier detection.

    Features tracked per signal:
      [confidence, rvol, spread_bps, kelly_fraction, atr_pct, volume_ratio]
    """
    FEATURE_NAMES = ['confidence', 'rvol', 'spread_bps', 'kelly_frac', 'atr_pct', 'vol_ratio']
    FEATURE_DIM = len(FEATURE_NAMES)

    # Mahalanobis distance thresholds
    WARN_THRESHOLD = 3.0   # Flag but allow
    BLOCK_THRESHOLD = 5.0  # Reject signal

    def __init__(self):
        self._global_stats = RollingStats(self.FEATURE_DIM)
        self._per_strategy: Dict[str, RollingStats] = defaultdict(
            lambda: RollingStats(self.FEATURE_DIM)
        )
        self._history: List[Dict] = []

    def check_signal(self, features: Dict[str, float],
                     strategy: str = "") -> Tuple[str, float, str]:
        """Check a signal for anomalies.

        Args:
            features: dict with keys matching FEATURE_NAMES
            strategy: strategy name for per-strategy tracking

        Returns:
            (verdict, distance, reason) where:
              verdict: "OK", "WARN", or "BLOCK"
              distance: Mahalanobis distance
              reason: human-readable explanation
        """
        vec = [features.get(k, 0.0) for k in self.FEATURE_NAMES]

        # Update running stats
        self._global_stats.update(vec)
        if strategy:
            self._per_strategy[strategy].update(vec)

        # Not enough data yet — pass through
        if self._global_stats.n < 30:
            return ("OK", 0.0, "warming_up")

        # Compute distance
        dist = self._global_stats.mahalanobis(vec)

        # Also check per-strategy if enough data
        strat_dist = 0.0
        if strategy and self._per_strategy[strategy].n >= 30:
            strat_dist = self._per_strategy[strategy].mahalanobis(vec)

        max_dist = max(dist, strat_dist)

        # Determine verdict
        if max_dist >= self.BLOCK_THRESHOLD:
            # Find which feature is most anomalous
            anomalous = self._find_anomalous_feature(vec)
            reason = f"extreme_outlier:{anomalous} (d={max_dist:.1f})"
            self._record(features, strategy, "BLOCK", max_dist, reason)
            return ("BLOCK", max_dist, reason)
        elif max_dist >= self.WARN_THRESHOLD:
            anomalous = self._find_anomalous_feature(vec)
            reason = f"mild_outlier:{anomalous} (d={max_dist:.1f})"
            self._record(features, strategy, "WARN", max_dist, reason)
            return ("WARN", max_dist, reason)
        else:
            return ("OK", max_dist, "normal")

    def _find_anomalous_feature(self, vec: List[float]) -> str:
        """Find which feature contributes most to the anomaly."""
        if self._global_stats.n < 10:
            return "unknown"
        cov = self._global_stats.covariance()
        if cov is None:
            return "unknown"

        max_contrib = 0.0
        max_name = "unknown"
        for i, name in enumerate(self.FEATURE_NAMES):
            var = cov[i][i]
            if var < 1e-10:
                continue
            z = abs(vec[i] - self._global_stats.mean[i]) / (var ** 0.5)
            if z > max_contrib:
                max_contrib = z
                max_name = name
        return max_name

    def _record(self, features, strategy, verdict, dist, reason):
        self._history.append({
            "ts": time.time(),
            "strategy": strategy,
            "verdict": verdict,
            "distance": round(dist, 2),
            "reason": reason,
        })
        if len(self._history) > 500:
            self._history = self._history[-500:]

    def confidence_penalty(self, features: Dict[str, float],
                           strategy: str = "") -> float:
        """Return confidence penalty for outlier signals.

        Returns 0 for normal signals, -3 to -10 for outliers.
        """
        verdict, dist, _ = self.check_signal(features, strategy)
        if verdict == "BLOCK":
            return -10.0
        elif verdict == "WARN":
            return -3.0
        return 0.0

    def stats_summary(self) -> Dict:
        return {
            "global_obs": self._global_stats.n,
            "strategies_tracked": len(self._per_strategy),
            "recent_blocks": sum(1 for h in self._history[-100:]
                                 if h["verdict"] == "BLOCK"),
            "recent_warns": sum(1 for h in self._history[-100:]
                                if h["verdict"] == "WARN"),
        }


# Alias for backward compatibility — some callers use OutlierDetector
OutlierDetector = SignalOutlierDetector

# Module singleton
_detector: Optional[SignalOutlierDetector] = None


def get_detector() -> SignalOutlierDetector:
    global _detector
    if _detector is None:
        _detector = SignalOutlierDetector()
    return _detector
