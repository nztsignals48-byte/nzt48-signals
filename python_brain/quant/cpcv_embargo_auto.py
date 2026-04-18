"""Autocorrelation-aware CPCV embargo sizer.

Computes embargo length as a function of label autocorrelation decay time.
For a strategy with label horizon h and residual autocorrelation ρ(k), pick
embargo = max(1.5 * h, lag at which |ρ(k)| < 0.1).

Referenced by ouroboros_v3_nightly + cpcv_harness.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class EmbargoEstimate:
    embargo_bars: int
    autocorr_decay_lag: int
    label_horizon_bars: int
    method: str


def autocorr(series: np.ndarray, max_lag: int = 50) -> np.ndarray:
    """Compute sample autocorrelation up to max_lag."""
    x = series - series.mean()
    var = x.var() or 1e-12
    n = len(x)
    out = np.zeros(max_lag + 1)
    for k in range(max_lag + 1):
        if k == 0:
            out[k] = 1.0
        else:
            out[k] = (x[:n - k] * x[k:]).mean() / var
    return out


def autocorr_decay_lag(series: np.ndarray, threshold: float = 0.1,
                      max_lag: int = 50) -> int:
    """Smallest lag k at which |autocorr(k)| < threshold."""
    ac = autocorr(series, max_lag)
    for k in range(1, len(ac)):
        if abs(ac[k]) < threshold:
            return k
    return max_lag


def embargo_horizon_aware(
    returns_or_residuals: np.ndarray,
    label_horizon_bars: int,
    min_embargo_bars: int = 5,
    max_embargo_bars: int = 500,
    ac_threshold: float = 0.1,
) -> EmbargoEstimate:
    """Pick CPCV embargo as max(1.5 * label_horizon, autocorr decay)."""
    if len(returns_or_residuals) < 20:
        return EmbargoEstimate(
            embargo_bars=max(min_embargo_bars, int(1.5 * label_horizon_bars)),
            autocorr_decay_lag=0,
            label_horizon_bars=label_horizon_bars,
            method="insufficient_data",
        )
    decay_lag = autocorr_decay_lag(returns_or_residuals, ac_threshold)
    horizon_based = max(min_embargo_bars, int(1.5 * label_horizon_bars))
    emb = max(horizon_based, decay_lag)
    emb = min(emb, max_embargo_bars)
    return EmbargoEstimate(
        embargo_bars=emb,
        autocorr_decay_lag=decay_lag,
        label_horizon_bars=label_horizon_bars,
        method="autocorr+horizon",
    )


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        rng = np.random.default_rng(42)
        # AR(1) with ρ=0.5
        series = np.zeros(500)
        for i in range(1, 500):
            series[i] = 0.5 * series[i - 1] + rng.normal(0, 0.01)
        est = embargo_horizon_aware(series, label_horizon_bars=10)
        print(f"AR(1) ρ=0.5, horizon=10:")
        print(f"  autocorr decay lag: {est.autocorr_decay_lag}")
        print(f"  embargo: {est.embargo_bars}")
        print(f"  method: {est.method}")
        print("OK")
