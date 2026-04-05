"""Hurst Exponent — Rescaled Range (R/S) analysis for regime classification.

H > 0.55 = trending (momentum strategies have edge)
H < 0.45 = mean-reverting (contrarian strategies have edge)
0.45 <= H <= 0.55 = random walk (no directional edge)
"""

from __future__ import annotations
import math
from typing import List


def estimate_hurst(prices: List[float], max_lag: int = 20) -> float:
    """Estimate Hurst exponent via rescaled range (R/S) analysis.

    Args:
        prices: Price series (minimum *max_lag* + 1 values required).
        max_lag: Maximum lag period for R/S computation.

    Returns:
        Hurst exponent clamped to [0.0, 1.0].
        Returns 0.5 (random walk) if data is insufficient or invalid.
    """
    if len(prices) < max_lag + 1 or max_lag < 2:
        return 0.5

    # Log returns
    returns = []
    for i in range(1, len(prices)):
        if prices[i - 1] <= 0.0 or prices[i] <= 0.0:
            return 0.5
        returns.append(math.log(prices[i] / prices[i - 1]))

    mean_ret = sum(returns) / len(returns)

    log_lags = []
    log_rs = []

    for lag in range(2, min(max_lag + 1, len(returns) + 1)):
        # Cumulative deviations
        deviation = 0.0
        max_dev = 0.0
        min_dev = 0.0
        for j in range(lag):
            deviation += returns[j] - mean_ret
            max_dev = max(max_dev, deviation)
            min_dev = min(min_dev, deviation)

        r = max_dev - min_dev

        # Standard deviation
        ss = sum((returns[j] - mean_ret) ** 2 for j in range(lag))
        s = math.sqrt(ss / lag) if lag > 0 else 1e-10
        if s < 1e-10:
            s = 1e-10

        rs = r / s
        if rs < 1e-10:
            rs = 1e-10

        log_lags.append(math.log(lag))
        log_rs.append(math.log(rs))

    if len(log_lags) < 2:
        return 0.5

    # OLS slope of log(R/S) vs log(lag)
    n = len(log_lags)
    sx = sum(log_lags)
    sy = sum(log_rs)
    sxy = sum(x * y for x, y in zip(log_lags, log_rs))
    sx2 = sum(x * x for x in log_lags)

    denom = n * sx2 - sx * sx
    if abs(denom) < 1e-10:
        return 0.5

    slope = (n * sxy - sx * sy) / denom
    return max(0.0, min(1.0, slope))


def classify_regime(hurst: float) -> str:
    """Classify market regime from Hurst exponent.

    Returns:
        "trending" if H > 0.55
        "mean_reverting" if H < 0.45
        "random" otherwise
    """
    if hurst > 0.55:
        return "trending"
    if hurst < 0.45:
        return "mean_reverting"
    return "random"


# Alias for backward compatibility — some callers use compute_hurst
compute_hurst = estimate_hurst
