"""Fractional Differentiation — Book 135.

Preserves memory while achieving stationarity. Standard differencing (d=1)
throws away ALL memory. Fractional differencing (d=0.35) retains ~65%
of the original series' memory while being stationary enough for ML.

The key: financial time series need SOME memory (trends, mean reversion)
but ALSO need stationarity (no unit root). d ∈ (0, 1) gives both.

Algorithm: Fixed-width window fractional differentiation (FFD)
  w_0 = 1
  w_k = -w_{k-1} * (d - k + 1) / k  for k >= 1
  FFD(x_t) = sum(w_k * x_{t-k}) for k = 0, ..., window-1

Usage:
    from python_brain.ml.ffd import ffd_transform, find_min_d

    transformed = ffd_transform(prices, d=0.35, window=500)
    min_d = find_min_d(prices, pvalue_threshold=0.05)
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional, Tuple

import numpy as np

log = logging.getLogger("ffd")


def ffd_weights(d: float, window: int, threshold: float = 1e-5) -> np.ndarray:
    """Compute FFD weights using Grunwald-Letnikov formula.

    w_0 = 1
    w_k = -w_{k-1} * (d - k + 1) / k

    Weights decay but never reach zero (unlike standard differencing).
    The `threshold` parameter truncates weights below a minimum magnitude.
    """
    weights = [1.0]
    for k in range(1, window):
        w = -weights[-1] * (d - k + 1) / k
        if abs(w) < threshold:
            break
        weights.append(w)
    return np.array(weights[::-1])  # Reverse so oldest weight is first


def ffd_transform(
    series: np.ndarray,
    d: float = 0.35,
    window: int = 500,
    threshold: float = 1e-5,
) -> np.ndarray:
    """Apply Fixed-Width Window Fractional Differentiation.

    Args:
        series: Price or return series (1D array)
        d: Differentiation order (0 < d < 1). Lower = more memory retained.
            d=0.35 is typical for financial time series.
        window: Lookback window for weight computation
        threshold: Minimum weight magnitude before truncation

    Returns:
        Fractionally differentiated series (shorter by len(weights)-1)
    """
    weights = ffd_weights(d, window, threshold)
    w_len = len(weights)

    if len(series) <= w_len:
        return np.array([])

    # Apply convolution
    result = np.convolve(series, weights[::-1], mode="valid")
    return result


def find_min_d(
    series: np.ndarray,
    pvalue_threshold: float = 0.05,
    d_range: Tuple[float, float] = (0.0, 1.0),
    d_step: float = 0.05,
    window: int = 500,
) -> float:
    """Find minimum d that makes the series stationary.

    Searches from d=0.0 upward until the ADF test rejects the null
    hypothesis of a unit root at the given p-value threshold.

    Returns: Minimum d for stationarity (or 1.0 if not found)
    """
    d_values = np.arange(d_range[0], d_range[1] + d_step, d_step)

    for d in d_values:
        if d == 0:
            continue

        transformed = ffd_transform(series, d=d, window=window)
        if len(transformed) < 30:
            continue

        adf_p = _adf_pvalue(transformed)
        if adf_p < pvalue_threshold:
            log.info("Min d found: %.2f (ADF p=%.4f, threshold=%.2f)", d, adf_p, pvalue_threshold)
            return d

    return 1.0  # Fallback to full differencing


def _adf_pvalue(series: np.ndarray) -> float:
    """Simplified ADF test (Dickey-Fuller regression)."""
    n = len(series)
    if n < 20:
        return 1.0

    dy = np.diff(series)
    y_lag = series[:-1]

    X = np.column_stack([np.ones(len(y_lag)), y_lag])
    try:
        beta = np.linalg.lstsq(X, dy, rcond=None)[0]
        residuals = dy - X @ beta
        se = np.sqrt(np.sum(residuals ** 2) / (len(dy) - 2))
        se_gamma = se / np.sqrt(np.sum((y_lag - np.mean(y_lag)) ** 2))
        if se_gamma <= 0:
            return 1.0
        t_stat = beta[1] / se_gamma

        # Approximate p-value from MacKinnon critical values
        if t_stat < -3.51:
            return 0.005
        elif t_stat < -2.89:
            return 0.03
        elif t_stat < -2.58:
            return 0.08
        else:
            return 0.50
    except (np.linalg.LinAlgError, ValueError):
        return 1.0


def ffd_feature_set(
    ohlcv: dict,
    d: float = 0.35,
    window: int = 200,
) -> dict:
    """Generate FFD-transformed features from OHLCV data.

    Args:
        ohlcv: {"open": arr, "high": arr, "low": arr, "close": arr, "volume": arr}
        d: Fractional differentiation order
        window: FFD window

    Returns: dict of feature_name → transformed array
    """
    features = {}
    for col in ("open", "high", "low", "close"):
        if col in ohlcv and len(ohlcv[col]) > window:
            features[f"ffd_{col}"] = ffd_transform(ohlcv[col], d=d, window=window)

    # Log volume is often better for FFD
    if "volume" in ohlcv and len(ohlcv["volume"]) > window:
        log_vol = np.log1p(np.maximum(ohlcv["volume"], 0))
        features["ffd_log_volume"] = ffd_transform(log_vol, d=d, window=window)

    return features
