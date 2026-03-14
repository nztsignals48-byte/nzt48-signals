"""
VPIN -- Volume-Synchronized Probability of Informed Trading.
Easley, Lopez de Prado, O'Hara (2012).
"""
from __future__ import annotations
import numpy as np
from scipy.stats import norm


def calculate_vpin(price_changes: np.ndarray, volumes: np.ndarray,
                   bucket_size: float = 50000.0) -> float:
    """
    VPIN > 0.75 = toxic flow. Step aside.
    Uses Bulk Volume Classification (BVC) per Easley et al. (2012).
    """
    if len(price_changes) < 2 or np.sum(volumes) < bucket_size:
        return 0.5

    sigma = np.std(price_changes, ddof=1)
    if sigma == 0:
        return 0.0

    buy_pct = norm.cdf(price_changes / sigma)
    buy_vol = volumes * buy_pct
    sell_vol = volumes * (1 - buy_pct)
    imbalances = np.abs(buy_vol - sell_vol)

    return float(np.sum(imbalances) / np.sum(volumes))
