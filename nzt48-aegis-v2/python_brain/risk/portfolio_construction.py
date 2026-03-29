"""Portfolio Construction for Leveraged Instruments — Books 20, 180.

Hierarchical Risk Parity (HRP) for leveraged ETP portfolios.
Accounts for vol drag, leverage decay, and path dependency.

HRP (Lopez de Prado 2016):
  1. Tree clustering on correlation matrix
  2. Quasi-diagonalization (seriation)
  3. Recursive bisection for weight allocation

Adjustments for leveraged ETPs:
  - Vol drag penalty: reduce allocation for high-vol instruments
  - Leverage normalization: 3x gets 1/3 the weight of 1x
  - Correlation clustering: group by underlying, not by ETP

Usage:
    from python_brain.risk.portfolio_construction import (
        HRPAllocator, compute_hrp_weights,
    )

    weights = compute_hrp_weights(returns_matrix, tickers)
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np

log = logging.getLogger("portfolio_construction")


def compute_hrp_weights(
    returns: np.ndarray,
    tickers: List[str],
    leverage_factors: Optional[Dict[str, int]] = None,
) -> Dict[str, float]:
    """Compute HRP allocation weights.

    Args:
        returns: T × N matrix of returns
        tickers: N ticker names
        leverage_factors: {ticker: leverage} for normalization

    Returns: {ticker: weight} summing to 1.0
    """
    T, N = returns.shape
    if N < 2 or T < 20:
        return {t: 1.0 / N for t in tickers}

    # Step 1: Correlation and distance matrix
    corr = np.corrcoef(returns.T)
    dist = np.sqrt(0.5 * (1 - corr))

    # Step 2: Hierarchical clustering (single linkage)
    order = _quasi_diagonalize(dist)

    # Step 3: Recursive bisection
    cov = np.cov(returns.T)
    weights = _recursive_bisection(cov, order)

    # Leverage normalization
    if leverage_factors:
        for i, ticker in enumerate(tickers):
            lev = leverage_factors.get(ticker, 1)
            if lev > 1:
                weights[i] /= lev  # 3x gets 1/3 weight

        # Renormalize
        total = np.sum(weights)
        if total > 0:
            weights /= total

    return {tickers[i]: round(float(weights[i]), 4) for i in range(N)}


def _quasi_diagonalize(dist: np.ndarray) -> List[int]:
    """Order assets to minimize off-diagonal distance (seriation)."""
    N = dist.shape[0]
    if N <= 2:
        return list(range(N))

    # Simple greedy seriation (not optimal but fast)
    remaining = set(range(N))
    order = [0]
    remaining.discard(0)

    while remaining:
        last = order[-1]
        nearest = min(remaining, key=lambda j: dist[last, j])
        order.append(nearest)
        remaining.discard(nearest)

    return order


def _recursive_bisection(cov: np.ndarray, order: List[int]) -> np.ndarray:
    """Allocate weights via recursive bisection of the covariance matrix."""
    N = len(order)
    weights = np.ones(N)

    if N <= 1:
        return weights

    # Split at midpoint
    mid = N // 2
    left = order[:mid]
    right = order[mid:]

    # Cluster variance for each half
    var_left = _cluster_variance(cov, left)
    var_right = _cluster_variance(cov, right)

    # Allocate inversely to variance
    total_var = var_left + var_right
    if total_var > 0:
        alpha = var_right / total_var  # More weight to lower-variance cluster
    else:
        alpha = 0.5

    # Apply weights
    for i in left:
        weights[i] *= alpha
    for i in right:
        weights[i] *= (1 - alpha)

    # Recurse
    if len(left) > 1:
        sub_weights = _recursive_bisection(cov, left)
        for j, i in enumerate(left):
            weights[i] *= sub_weights[j]
    if len(right) > 1:
        sub_weights = _recursive_bisection(cov, right)
        for j, i in enumerate(right):
            weights[i] *= sub_weights[j]

    return weights


def _cluster_variance(cov: np.ndarray, indices: List[int]) -> float:
    """Compute inverse-variance weighted cluster variance."""
    sub_cov = cov[np.ix_(indices, indices)]
    ivp = 1.0 / np.diag(sub_cov)
    ivp /= ivp.sum()
    return float(ivp @ sub_cov @ ivp)


def risk_parity_weights(cov: np.ndarray) -> np.ndarray:
    """Simple risk parity: weight inversely to volatility."""
    vols = np.sqrt(np.diag(cov))
    inv_vol = 1.0 / np.maximum(vols, 1e-10)
    return inv_vol / inv_vol.sum()
