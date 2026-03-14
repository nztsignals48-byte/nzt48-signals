"""
Deflated Sharpe Ratio -- Bailey & Lopez de Prado (2014).
Accounts for multiple testing, skewness, kurtosis.
"""
from __future__ import annotations
import numpy as np
from scipy.stats import norm, skew, kurtosis
import math


def calculate_deflated_sharpe(returns: np.ndarray, num_trials: int) -> float:
    """
    Probability that estimated Sharpe is real, not data mining.
    Returns p-value in [0, 1]. If p < 0.95, edge is indistinguishable from noise.
    """
    n = len(returns)
    if n < 30 or num_trials < 2:
        return 0.0

    std = np.std(returns, ddof=1)
    if std == 0:
        return 0.0

    sr = (np.mean(returns) / std) * math.sqrt(252)
    sk = skew(returns)
    ku = kurtosis(returns, fisher=True)

    emc = 0.5772156649  # Euler-Mascheroni constant
    log_trials = math.log(num_trials)
    sr0 = math.sqrt(2 * log_trials) + \
          (emc - math.log(log_trials)) / math.sqrt(2 * log_trials)

    var_sr = (1 - sk * sr + ((ku - 1) / 4) * sr**2) / (n - 1)
    if var_sr <= 0:
        return 0.0

    z_dsr = (sr - sr0) / math.sqrt(var_sr)
    return float(norm.cdf(z_dsr))
