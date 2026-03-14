"""
L-08: Fractional Differentiation on ML Features (Phase Q3-Q4 skeleton).
Per-feature walk-forward d-selection over [0.10, 0.90].
ADF test + correlation preservation. Typical d ~ 0.35-0.55.
"""
import logging
import numpy as np
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def get_weights_ffd(d: float, threshold: float = 1e-5) -> np.ndarray:
    """Fixed-width window fractional differentiation weights.
    De Prado (2018) Advances in Financial Machine Learning, Ch. 5.
    """
    w = [1.0]
    k = 1
    while abs(w[-1]) >= threshold:
        w.append(-w[-1] * (d - k + 1) / k)
        k += 1
    return np.array(w[::-1]).reshape(-1, 1)


def fracdiff(series: np.ndarray, d: float, threshold: float = 1e-5) -> np.ndarray:
    """Apply fractional differentiation of order d to a series."""
    weights = get_weights_ffd(d, threshold)
    width = len(weights)
    output = np.full(len(series), np.nan)
    for i in range(width - 1, len(series)):
        window = series[i - width + 1:i + 1]
        output[i] = np.dot(weights.flatten(), window)
    return output


def find_optimal_d(series: np.ndarray, d_range: Tuple[float, float] = (0.1, 0.9),
                   step: float = 0.05, adf_threshold: float = 0.05,
                   min_correlation: float = 0.90) -> float:
    """Find minimum d that achieves stationarity while preserving memory.
    TODO: Implement ADF test (requires statsmodels).
    """
    # Skeleton -- needs statsmodels for ADF test
    logger.info("fracdiff.find_optimal_d: skeleton (Q3-Q4)")
    return 0.4  # Typical starting value
