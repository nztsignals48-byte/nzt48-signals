"""Dynamic Correlation & Contagion Detection — Book 41.

Tracks real-time EWMA correlation across open positions.
Detects correlation regime changes and contagion spikes.

3 Correlation Regimes:
  LOW:      avg_corr < 0.30 — normal diversification
  ELEVATED: avg_corr 0.30-0.60 — reduce size 30%, block correlated entries
  CRISIS:   avg_corr > 0.60 — block all long entries, allow only inverse

The correlation spike exit (Book 39 Section 23):
  avg_corr < 0.3 → trail multiplier 1.0
  avg_corr = 0.5 → trail multiplier 0.85
  avg_corr > 0.7 → trail multiplier 0.60

Usage:
    from python_brain.risk.correlation import (
        CorrelationTracker, CorrelationRegime,
    )

    tracker = CorrelationTracker()
    tracker.update(returns_matrix)  # N instruments × T bars
    regime = tracker.regime
    trail_mult = tracker.exit_trail_multiplier()
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("correlation_risk")


class CorrelationRegime(Enum):
    LOW = "LOW"           # avg_corr < 0.30
    ELEVATED = "ELEVATED"  # avg_corr 0.30-0.60
    CRISIS = "CRISIS"     # avg_corr > 0.60


@dataclass
class ContagionAlert:
    """Alert when historically uncorrelated pairs suddenly correlate."""
    pair: Tuple[str, str]
    baseline_corr: float
    current_corr: float
    delta: float
    timestamp_ns: int = 0


class CorrelationTracker:
    """EWMA correlation matrix tracker across instruments.

    Uses exponentially weighted moving average (lambda=0.94, per RiskMetrics)
    for responsive correlation estimation.
    """

    def __init__(self, decay: float = 0.94, min_history: int = 20):
        self.decay = decay
        self.min_history = min_history
        self._corr_matrix: Optional[np.ndarray] = None
        self._tickers: List[str] = []
        self._returns_buffer: List[np.ndarray] = []
        self._regime = CorrelationRegime.LOW
        self._avg_corr: float = 0.0
        self._max_pair_corr: float = 0.0
        self._contagion_alerts: List[ContagionAlert] = []
        # Hysteresis: regime entry/exit thresholds differ
        self._entry_thresholds = {
            CorrelationRegime.ELEVATED: 0.30,
            CorrelationRegime.CRISIS: 0.60,
        }
        self._exit_thresholds = {
            CorrelationRegime.ELEVATED: 0.22,  # Must drop below 0.22 to return to LOW
            CorrelationRegime.CRISIS: 0.50,    # Must drop below 0.50 to return to ELEVATED
        }

    @property
    def regime(self) -> CorrelationRegime:
        return self._regime

    @property
    def avg_correlation(self) -> float:
        return self._avg_corr

    @property
    def max_pair_correlation(self) -> float:
        return self._max_pair_corr

    def update(self, returns: Dict[str, float]) -> CorrelationRegime:
        """Update with a new bar of returns per ticker.

        Args:
            returns: {ticker: bar_return} for current bar
        """
        if not returns:
            return self._regime

        # Build consistent ticker ordering
        if not self._tickers:
            self._tickers = sorted(returns.keys())

        # Create return vector in ticker order
        vec = np.array([returns.get(t, 0.0) for t in self._tickers])
        self._returns_buffer.append(vec)

        # Keep bounded buffer
        max_buffer = 500
        if len(self._returns_buffer) > max_buffer:
            self._returns_buffer = self._returns_buffer[-max_buffer:]

        if len(self._returns_buffer) < self.min_history:
            return self._regime

        # Compute EWMA correlation matrix
        self._compute_ewma_correlation()
        self._update_regime()
        self._check_contagion()

        return self._regime

    def _compute_ewma_correlation(self):
        """Compute EWMA correlation matrix from returns buffer."""
        data = np.array(self._returns_buffer)
        n_assets = data.shape[1]
        T = data.shape[0]

        if n_assets < 2 or T < self.min_history:
            return

        # EWMA weights
        weights = np.array([(1 - self.decay) * self.decay ** i for i in range(T - 1, -1, -1)])
        weights /= weights.sum()

        # Weighted means
        means = np.average(data, axis=0, weights=weights)
        centered = data - means

        # Weighted covariance
        cov = np.zeros((n_assets, n_assets))
        for t in range(T):
            cov += weights[t] * np.outer(centered[t], centered[t])

        # Covariance to correlation
        stds = np.sqrt(np.diag(cov))
        stds = np.maximum(stds, 1e-10)
        self._corr_matrix = cov / np.outer(stds, stds)
        np.fill_diagonal(self._corr_matrix, 1.0)

        # Average pairwise correlation (upper triangle, excluding diagonal)
        n = n_assets
        if n > 1:
            upper = self._corr_matrix[np.triu_indices(n, k=1)]
            self._avg_corr = float(np.mean(np.abs(upper)))
            self._max_pair_corr = float(np.max(np.abs(upper)))
        else:
            self._avg_corr = 0.0
            self._max_pair_corr = 0.0

    def _update_regime(self):
        """Update correlation regime with hysteresis."""
        old = self._regime

        if self._regime == CorrelationRegime.LOW:
            if self._avg_corr >= self._entry_thresholds[CorrelationRegime.CRISIS]:
                self._regime = CorrelationRegime.CRISIS
            elif self._avg_corr >= self._entry_thresholds[CorrelationRegime.ELEVATED]:
                self._regime = CorrelationRegime.ELEVATED

        elif self._regime == CorrelationRegime.ELEVATED:
            if self._avg_corr >= self._entry_thresholds[CorrelationRegime.CRISIS]:
                self._regime = CorrelationRegime.CRISIS
            elif self._avg_corr < self._exit_thresholds[CorrelationRegime.ELEVATED]:
                self._regime = CorrelationRegime.LOW

        elif self._regime == CorrelationRegime.CRISIS:
            if self._avg_corr < self._exit_thresholds[CorrelationRegime.CRISIS]:
                self._regime = CorrelationRegime.ELEVATED

        if self._regime != old:
            log.warning(
                "CORRELATION_REGIME: %s → %s (avg_corr=%.3f, max_pair=%.3f)",
                old.value, self._regime.value, self._avg_corr, self._max_pair_corr,
            )

    def _check_contagion(self):
        """Detect sudden correlation spikes between historically uncorrelated pairs."""
        self._contagion_alerts.clear()
        if self._corr_matrix is None or len(self._tickers) < 2:
            return

        n = len(self._tickers)
        for i in range(n):
            for j in range(i + 1, n):
                current = abs(self._corr_matrix[i, j])
                # Flag if current correlation > 0.50 for pairs that are
                # typically < 0.20 (use first 50% of history as baseline)
                half = len(self._returns_buffer) // 2
                if half >= self.min_history:
                    baseline_data = np.array(self._returns_buffer[:half])
                    if baseline_data.shape[0] >= 10:
                        baseline_corr = abs(np.corrcoef(baseline_data[:, i], baseline_data[:, j])[0, 1])
                        delta = current - baseline_corr
                        if baseline_corr < 0.20 and current > 0.50 and delta > 0.30:
                            alert = ContagionAlert(
                                pair=(self._tickers[i], self._tickers[j]),
                                baseline_corr=round(baseline_corr, 3),
                                current_corr=round(current, 3),
                                delta=round(delta, 3),
                            )
                            self._contagion_alerts.append(alert)
                            log.warning(
                                "CONTAGION: %s-%s corr %.2f→%.2f (Δ=%.2f)",
                                self._tickers[i], self._tickers[j],
                                baseline_corr, current, delta,
                            )

    def exit_trail_multiplier(self) -> float:
        """Compute trail tightening multiplier for exit engine (Book 39 Section 23)."""
        if self._avg_corr < 0.3:
            return 1.0
        elif self._avg_corr < 0.5:
            # Linear interpolation: 0.3→1.0, 0.5→0.85
            return 1.0 - (self._avg_corr - 0.3) / 0.2 * 0.15
        elif self._avg_corr < 0.7:
            # Linear interpolation: 0.5→0.85, 0.7→0.60
            return 0.85 - (self._avg_corr - 0.5) / 0.2 * 0.25
        else:
            return 0.60

    def position_size_multiplier(self) -> float:
        """Position size scaling based on correlation regime."""
        if self._regime == CorrelationRegime.LOW:
            return 1.0
        elif self._regime == CorrelationRegime.ELEVATED:
            return 0.70  # 30% reduction
        else:  # CRISIS
            return 0.30  # 70% reduction for longs

    def should_block_long_entry(self) -> bool:
        """In CRISIS correlation regime, block all new long entries."""
        return self._regime == CorrelationRegime.CRISIS

    def absorption_ratio(self) -> float:
        """Compute absorption ratio (eigenvalue concentration).

        High absorption ratio (>0.80) = market driven by few factors = fragile.
        """
        if self._corr_matrix is None or self._corr_matrix.shape[0] < 3:
            return 0.0

        eigenvalues = np.linalg.eigvalsh(self._corr_matrix)
        eigenvalues = np.sort(eigenvalues)[::-1]  # Descending
        total = np.sum(eigenvalues)
        if total <= 0:
            return 0.0

        # Top 3 eigenvalues as fraction of total
        top3 = np.sum(eigenvalues[:3])
        return float(top3 / total)

    def to_dict(self) -> dict:
        return {
            "regime": self._regime.value,
            "avg_correlation": round(self._avg_corr, 4),
            "max_pair_correlation": round(self._max_pair_corr, 4),
            "exit_trail_multiplier": round(self.exit_trail_multiplier(), 3),
            "position_size_multiplier": round(self.position_size_multiplier(), 3),
            "absorption_ratio": round(self.absorption_ratio(), 3),
            "contagion_alerts": len(self._contagion_alerts),
            "n_tickers": len(self._tickers),
            "buffer_size": len(self._returns_buffer),
        }
