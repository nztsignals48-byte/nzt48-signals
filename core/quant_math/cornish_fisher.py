"""
Cornish-Fisher fat-tail adjusted Kelly sizing.
Replaces naive variance with CF-expanded variance for leveraged ETPs.
Cornish & Fisher (1938), adapted per Magdon-Ismail (2004) + Feller (1968).
"""
from __future__ import annotations
import numpy as np
from scipy.stats import skew, kurtosis


def get_cf_adjusted_variance(returns: np.ndarray, confidence_level: float = 0.99) -> float:
    """Adjusts variance using Cornish-Fisher expansion for fat tails."""
    if len(returns) < 10:
        return float(np.var(returns, ddof=1)) if len(returns) > 1 else 1.0

    z_c = 2.3263  # 99% confidence

    s = skew(returns)
    k = kurtosis(returns, fisher=True)

    z_cf = (z_c +
            (1/6) * (z_c**2 - 1) * s +
            (1/24) * (z_c**3 - 3*z_c) * k -
            (1/36) * (2*z_c**3 - 5*z_c) * (s**2))

    raw_variance = np.var(returns, ddof=1)
    adjustment_multiplier = max(1.0, (z_cf / z_c) ** 2)
    return float(raw_variance * adjustment_multiplier)


def cornish_fisher_kelly(mu: float, returns: np.ndarray, leverage: float = 1.0) -> float:
    """Merton Kelly with Cornish-Fisher fat-tail adjustment + leverage scaling."""
    cf_var = get_cf_adjusted_variance(returns)
    if cf_var <= 0 or mu <= 0:
        return 0.0

    f_star_unlev = mu / cf_var
    f_star_lev = f_star_unlev / max(leverage, 1.0)

    # Leverage-dependent safety (Magdon-Ismail 2004 + Feller 1968):
    if leverage >= 5.0:
        return f_star_lev * 0.20   # Fifth Kelly for 5x
    elif leverage >= 3.0:
        return f_star_lev * 0.25   # Quarter Kelly for 3x
    elif leverage >= 2.0:
        return f_star_lev * 0.33   # Third Kelly for 2x
    else:
        return f_star_lev * 0.50   # Half Kelly for 1x
