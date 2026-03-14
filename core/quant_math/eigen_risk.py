"""
Dynamic PCA portfolio concentration monitor.
Replaces static correlation groups when sufficient data is available.
"""
from __future__ import annotations
import numpy as np
import logging

logger = logging.getLogger("nzt48.eigen_risk")


def calculate_portfolio_heat_pca(returns_matrix: np.ndarray) -> float:
    """
    returns_matrix: shape (N_observations, N_open_positions)
    Returns PC1 variance explained ratio.

    If PC1 > 0.85, portfolio is essentially one bet.
    March 2020: cross-asset correlation spiked to 0.92 in 48 hours.
    Static groups would not have caught this.
    """
    if returns_matrix.ndim != 2 or returns_matrix.shape[1] < 2:
        return 0.0

    if returns_matrix.shape[0] < 5:
        return 0.0

    try:
        standardized = returns_matrix - np.mean(returns_matrix, axis=0)
        std = np.std(returns_matrix, axis=0)
        std[std == 0] = 1.0
        standardized = standardized / std

        cov_matrix = np.cov(standardized, rowvar=False)
        eigenvalues = np.linalg.eigvalsh(cov_matrix)
        eigenvalues = eigenvalues[::-1]  # Descending

        total_var = np.sum(eigenvalues)
        if total_var == 0:
            return 0.0
        return float(eigenvalues[0] / total_var)
    except Exception as e:
        logger.debug("PCA heat calculation failed: %s", e)
        return 0.0
