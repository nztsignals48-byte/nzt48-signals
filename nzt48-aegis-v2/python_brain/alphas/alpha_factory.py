"""WorldQuant Alpha Factory — Book 121, 168.

Formulaic alpha evaluation framework. Each alpha is a mathematical
expression over price/volume data that produces a directional signal.

The power of this approach: combine many WEAK alphas (IC 0.05-0.12)
into one STRONG ensemble signal via diversification.

Operators:
  rank(x):        Cross-sectional rank (0-1)
  ts_rank(x, d):  Time-series rank over d bars
  ts_delta(x, d): x[t] - x[t-d]
  ts_corr(x,y,d): Rolling correlation
  ts_stddev(x,d): Rolling standard deviation
  ts_min/max(x,d): Rolling min/max
  decay_linear(x,d): Linearly decaying weighted sum

Usage:
    from python_brain.alphas.alpha_factory import AlphaFactory, AlphaResult

    factory = AlphaFactory()
    factory.register_alpha("momentum_12", alpha_momentum_12)
    results = factory.evaluate_all(ohlcv_data)
    ensemble_signal = factory.ensemble(results)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("alpha_factory")


@dataclass
class AlphaResult:
    """Result of evaluating a single alpha."""
    name: str
    value: float = 0.0       # Current alpha value (-1 to +1 typical)
    ic: float = 0.0          # Information Coefficient (rolling)
    turnover: float = 0.0    # Daily turnover rate
    ic_adjusted: float = 0.0  # IC adjusted for turnover: IC * sqrt(252/turnover)
    n_observations: int = 0


# ---------------------------------------------------------------------------
# Alpha Operator Library
# ---------------------------------------------------------------------------
def ts_rank(x: np.ndarray, d: int) -> np.ndarray:
    """Time-series rank: where current value sits in last d values (0-1)."""
    n = len(x)
    result = np.full(n, 0.5)
    for i in range(d - 1, n):
        window = x[i - d + 1:i + 1]
        result[i] = np.sum(window <= x[i]) / d
    return result


def ts_delta(x: np.ndarray, d: int) -> np.ndarray:
    """x[t] - x[t-d]."""
    result = np.zeros(len(x))
    result[d:] = x[d:] - x[:-d]
    return result


def ts_corr(x: np.ndarray, y: np.ndarray, d: int) -> np.ndarray:
    """Rolling correlation between x and y over d bars."""
    n = min(len(x), len(y))
    result = np.zeros(n)
    for i in range(d - 1, n):
        wx = x[i - d + 1:i + 1]
        wy = y[i - d + 1:i + 1]
        if np.std(wx) > 0 and np.std(wy) > 0:
            result[i] = np.corrcoef(wx, wy)[0, 1]
    return result


def ts_stddev(x: np.ndarray, d: int) -> np.ndarray:
    """Rolling standard deviation over d bars."""
    n = len(x)
    result = np.zeros(n)
    for i in range(d - 1, n):
        result[i] = np.std(x[i - d + 1:i + 1], ddof=1)
    return result


def decay_linear(x: np.ndarray, d: int) -> np.ndarray:
    """Linearly decaying weighted sum over d bars."""
    weights = np.arange(1, d + 1, dtype=float)
    weights /= weights.sum()
    return np.convolve(x, weights[::-1], mode="same")


def rank(x: np.ndarray) -> np.ndarray:
    """Cross-sectional rank (normalized 0-1 for single series)."""
    from scipy.stats import rankdata
    return rankdata(x) / len(x)


# ---------------------------------------------------------------------------
# Sample Alpha Definitions
# ---------------------------------------------------------------------------
def alpha_momentum_12(close: np.ndarray, volume: np.ndarray, **kw) -> float:
    """12-bar momentum: (close[-1] - close[-13]) / close[-13]."""
    if len(close) < 13:
        return 0.0
    return (close[-1] - close[-13]) / max(abs(close[-13]), 1e-10)


def alpha_reversion_5(close: np.ndarray, volume: np.ndarray, **kw) -> float:
    """5-bar mean reversion: negative of ts_rank of close over 5 bars."""
    if len(close) < 5:
        return 0.0
    r = ts_rank(close, 5)
    return -(r[-1] - 0.5) * 2  # Normalize to [-1, +1]


def alpha_volume_price_div(close: np.ndarray, volume: np.ndarray, **kw) -> float:
    """Volume-price divergence: correlation(close, volume) over 10 bars."""
    if len(close) < 10 or len(volume) < 10:
        return 0.0
    corr = ts_corr(close, volume.astype(float), 10)
    return -corr[-1]  # Negative correlation = divergence = signal


def alpha_vwap_reversion(close: np.ndarray, volume: np.ndarray, **kw) -> float:
    """Distance from VWAP over 20 bars, ranked."""
    n = min(len(close), len(volume))
    if n < 20:
        return 0.0
    c = close[-20:]
    v = volume[-20:].astype(float)
    vwap = np.sum(c * v) / max(np.sum(v), 1e-10)
    dist = (close[-1] - vwap) / max(abs(vwap), 1e-10)
    return -dist  # Far above VWAP → bearish, far below → bullish


def alpha_triple_rank(close: np.ndarray, volume: np.ndarray, **kw) -> float:
    """Triple rank reversion (Alpha #17 from WorldQuant).

    -1 * rank(ts_rank(close, 10)) * rank(acceleration) * rank(volume_rank)
    """
    if len(close) < 12 or len(volume) < 12:
        return 0.0
    r1 = ts_rank(close, 10)[-1]
    delta1 = ts_delta(close, 1)
    delta2 = ts_delta(close, 2)
    accel = delta1[-1] - delta2[-1] / 2 if len(delta2) > 0 else 0
    vol_r = ts_rank(volume.astype(float), 10)[-1]
    return -r1 * (0.5 + np.sign(accel) * 0.5) * vol_r


def alpha_overnight_return(close: np.ndarray, open_: np.ndarray = None, **kw) -> float:
    """Overnight return reversal: gap tends to fill."""
    if open_ is None or len(close) < 2 or len(open_) < 1:
        return 0.0
    gap = (open_[-1] - close[-2]) / max(abs(close[-2]), 1e-10)
    return -gap  # Fade the gap


# ---------------------------------------------------------------------------
# Alpha Factory
# ---------------------------------------------------------------------------
class AlphaFactory:
    """Register and evaluate multiple formulaic alphas."""

    def __init__(self):
        self._alphas: Dict[str, Callable] = {}
        self._ic_history: Dict[str, List[float]] = {}
        # Register built-in alphas
        self.register_alpha("momentum_12", alpha_momentum_12)
        self.register_alpha("reversion_5", alpha_reversion_5)
        self.register_alpha("vol_price_div", alpha_volume_price_div)
        self.register_alpha("vwap_reversion", alpha_vwap_reversion)
        self.register_alpha("triple_rank", alpha_triple_rank)
        self.register_alpha("overnight_return", alpha_overnight_return)

    def register_alpha(self, name: str, func: Callable):
        self._alphas[name] = func
        self._ic_history.setdefault(name, [])

    def evaluate_all(
        self,
        close: np.ndarray,
        volume: np.ndarray,
        open_: Optional[np.ndarray] = None,
        high: Optional[np.ndarray] = None,
        low: Optional[np.ndarray] = None,
    ) -> List[AlphaResult]:
        """Evaluate all registered alphas on current data."""
        results = []
        kwargs = {"open_": open_, "high": high, "low": low}

        for name, func in self._alphas.items():
            try:
                value = func(close=close, volume=volume, **kwargs)
                result = AlphaResult(
                    name=name,
                    value=round(value, 6),
                    n_observations=len(close),
                )
                results.append(result)
            except Exception as e:
                log.warning("Alpha %s failed: %s", name, e)

        return results

    def ensemble(
        self,
        results: List[AlphaResult],
        method: str = "equal_weight",
    ) -> float:
        """Combine alpha values into ensemble signal.

        Methods:
        - equal_weight: simple average
        - ic_weighted: weight by recent IC (when available)
        """
        if not results:
            return 0.0

        values = [r.value for r in results if not math.isnan(r.value)]
        if not values:
            return 0.0

        if method == "equal_weight":
            return sum(values) / len(values)

        # IC-weighted (fallback to equal if no IC data)
        weighted_sum = 0.0
        weight_total = 0.0
        for r in results:
            if math.isnan(r.value):
                continue
            ic_history = self._ic_history.get(r.name, [])
            w = abs(np.mean(ic_history[-50:])) if len(ic_history) >= 10 else 1.0
            weighted_sum += r.value * w
            weight_total += w

        return weighted_sum / max(weight_total, 1e-10)

    def update_ic(self, name: str, predicted: float, actual_return: float):
        """Update IC tracking for an alpha after observing actual return."""
        self._ic_history.setdefault(name, []).append(
            1.0 if (predicted > 0 and actual_return > 0) or (predicted < 0 and actual_return < 0) else -1.0
        )
        # Keep bounded
        if len(self._ic_history[name]) > 500:
            self._ic_history[name] = self._ic_history[name][-500:]
