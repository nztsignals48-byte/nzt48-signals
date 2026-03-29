"""Path Signatures for Trading Features — Book 128.

Universal nonparametric features from price paths. Path signatures
capture the essential shape of a path (trend, curvature, oscillation)
in a mathematically principled way.

Key advantages:
1. Universal approximation: ANY continuous function of a path can be
   approximated by a linear function of its signature
2. Order-invariant: captures ordering effects that simple statistics miss
3. Low memory: truncated at depth 3 = ~45 features for 3D path
4. Fast: O(T × D^K) where T=length, D=dimensions, K=truncation depth

Typical usage: compute signature of (price, volume, time) path
over rolling 20-bar windows. Feed as features to any ML model.

Usage:
    from python_brain.ml.path_signatures import (
        compute_signature, rolling_signatures,
    )

    # 3D path: (log_price, log_volume, normalized_time)
    path = np.column_stack([log_prices, log_volumes, time_index])
    sig = compute_signature(path, depth=3)  # ~45 features

    # Rolling signatures for ML feature matrix
    features = rolling_signatures(path, window=20, depth=3)
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np

log = logging.getLogger("path_signatures")


def compute_signature(path: np.ndarray, depth: int = 3) -> np.ndarray:
    """Compute the truncated signature of a path.

    The signature of a path X: [0,T] → R^d at depth K consists of
    iterated integrals:
        S^{i1,...,ik}(X) = ∫...∫ dX^{i1} ... dX^{ik}

    For depth 1: d features (increments)
    For depth 2: d + d^2 features (+ pairwise interactions)
    For depth 3: d + d^2 + d^3 features (+ triple interactions)

    Args:
        path: T × D array (T time steps, D dimensions)
        depth: Truncation depth (1, 2, or 3)

    Returns:
        1D array of signature features
    """
    if path.ndim != 2 or path.shape[0] < 2:
        return np.array([])

    T, D = path.shape

    # Increments
    dX = np.diff(path, axis=0)  # (T-1) × D

    features = []

    # Depth 1: Simple integrals (sums of increments = total displacement)
    sig1 = np.sum(dX, axis=0)  # D features
    features.extend(sig1.tolist())

    if depth >= 2:
        # Depth 2: Iterated integrals ∫∫ dX^i dX^j
        # S^{ij} = sum_{s<t} dX^i_s * dX^j_t
        cumsum = np.cumsum(dX, axis=0)  # Running sum for efficient computation
        sig2 = np.zeros((D, D))
        for t in range(1, len(dX)):
            sig2 += np.outer(cumsum[t - 1], dX[t])
        features.extend(sig2.flatten().tolist())  # D^2 features

    if depth >= 3:
        # Depth 3: Triple iterated integrals ∫∫∫ dX^i dX^j dX^k
        # Approximation using cumulative sums for efficiency
        sig3 = np.zeros((D, D, D))
        cum2 = np.zeros((D, D))
        for t in range(len(dX)):
            if t > 0:
                for i in range(D):
                    for j in range(D):
                        sig3[i, j, :] += cum2[i, j] * dX[t]
                cum2 += np.outer(cumsum[t - 1], dX[t])
        features.extend(sig3.flatten().tolist())  # D^3 features

    return np.array(features)


def rolling_signatures(
    path: np.ndarray,
    window: int = 20,
    depth: int = 3,
    step: int = 1,
) -> np.ndarray:
    """Compute rolling path signatures for ML feature matrix.

    Args:
        path: T × D array
        window: Rolling window size
        depth: Signature truncation depth
        step: Step size between windows

    Returns:
        N × F array where N = (T-window)//step + 1, F = signature dimension
    """
    T, D = path.shape
    if T < window:
        return np.array([])

    # Compute signature dimension
    sig_dim = D  # depth 1
    if depth >= 2:
        sig_dim += D * D
    if depth >= 3:
        sig_dim += D * D * D

    n_windows = (T - window) // step + 1
    result = np.zeros((n_windows, sig_dim))

    for i in range(n_windows):
        start = i * step
        end = start + window
        sig = compute_signature(path[start:end], depth=depth)
        if len(sig) == sig_dim:
            result[i] = sig

    return result


def build_signature_features(
    close: np.ndarray,
    volume: np.ndarray,
    window: int = 20,
    depth: int = 2,
) -> np.ndarray:
    """Build signature features from price and volume data.

    Constructs a 3D path: (log_close, log_volume, normalized_time)
    and computes rolling signatures.

    For depth=2 with 3 dimensions: 3 + 9 = 12 features per window.
    For depth=3: 3 + 9 + 27 = 39 features per window.

    Args:
        close: Close price array
        volume: Volume array
        window: Rolling window
        depth: Signature depth (2 recommended for speed, 3 for accuracy)

    Returns:
        N × F feature matrix
    """
    n = min(len(close), len(volume))
    if n < window + 1:
        return np.array([])

    # Log transforms for better signature properties
    log_close = np.log(np.maximum(close[:n], 1e-10))
    log_volume = np.log1p(np.maximum(volume[:n], 0))

    # Normalize time to [0, 1] per window
    time_axis = np.linspace(0, 1, n)

    # 3D path
    path = np.column_stack([log_close, log_volume, time_axis])

    return rolling_signatures(path, window=window, depth=depth)


def signature_distance(sig1: np.ndarray, sig2: np.ndarray) -> float:
    """Compute distance between two path signatures.

    Useful for regime detection: if signature of recent path is far
    from signature of "normal" regime, we may be in a different regime.
    """
    if len(sig1) != len(sig2) or len(sig1) == 0:
        return float("inf")
    return float(np.linalg.norm(sig1 - sig2))
