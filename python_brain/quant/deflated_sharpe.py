"""
Deflated Sharpe Ratio (DSR) — Bailey & López de Prado (2014)

Deflates a strategy's Sharpe ratio for multiple testing bias.
Gate: strategies with DSR > 0 have a real edge beyond chance given the
number of backtest trials run.

Reference:
- Bailey & López de Prado (2014) "The Deflated Sharpe Ratio"
- López de Prado (2018) AFML Chapter 8
"""
from __future__ import annotations

import math
from scipy import stats


def deflated_sharpe_ratio(
    sharpe: float,
    n_observations: int,
    n_trials: int = 1,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """
    Compute Deflated Sharpe Ratio.

    Args:
        sharpe: observed Sharpe ratio
        n_observations: number of return observations
        n_trials: number of backtest trials / strategies tested
        skewness: return skewness (0 = symmetric)
        kurtosis: return kurtosis (3 = normal)

    Returns:
        Probability that true Sharpe > 0 given trial count.
        Positive = has edge; negative = spurious.
    """
    if n_observations < 10:
        return 0.0

    # Expected max Sharpe from n_trials under null hypothesis
    emc = 0.5772156649  # Euler-Mascheroni constant
    if n_trials > 1:
        sharpe_max_expected = (1 - emc) * stats.norm.ppf(1 - 1 / n_trials) + \
                              emc * stats.norm.ppf(1 - 1 / (n_trials * math.e))
    else:
        sharpe_max_expected = 0.0

    # Non-normality adjustment (López de Prado formula)
    non_normal_adj = (
        1
        - skewness * sharpe
        + (kurtosis - 1) / 4 * sharpe ** 2
    )
    non_normal_adj = max(non_normal_adj, 0.01)

    # DSR
    numerator = (sharpe - sharpe_max_expected) * math.sqrt(n_observations - 1)
    denominator = math.sqrt(non_normal_adj)

    dsr_z = numerator / denominator if denominator > 0 else 0
    return float(stats.norm.cdf(dsr_z))


def probabilistic_sharpe_ratio(
    sharpe: float,
    n_observations: int,
    benchmark_sharpe: float = 0.0,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """
    Probabilistic Sharpe Ratio — probability that true Sharpe > benchmark.
    """
    if n_observations < 10:
        return 0.0

    non_normal_adj = (
        1
        - skewness * sharpe
        + (kurtosis - 1) / 4 * sharpe ** 2
    )
    non_normal_adj = max(non_normal_adj, 0.01)

    numerator = (sharpe - benchmark_sharpe) * math.sqrt(n_observations - 1)
    denominator = math.sqrt(non_normal_adj)

    z = numerator / denominator if denominator > 0 else 0
    return float(stats.norm.cdf(z))


def passes_dsr_gate(
    sharpe: float,
    n_observations: int,
    n_trials: int = 1,
    threshold: float = 0.95,
) -> bool:
    """Strategy passes gate if DSR > threshold (default 95% confidence)."""
    dsr = deflated_sharpe_ratio(sharpe, n_observations, n_trials)
    return dsr > threshold


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        # Good strategy: Sharpe 1.5, many obs, few trials
        good = deflated_sharpe_ratio(1.5, 500, 10)
        print(f"Good strategy DSR: {good:.4f}")

        # Bad strategy: same Sharpe but tested 100x (survivorship bias)
        bad = deflated_sharpe_ratio(1.5, 500, 100)
        print(f"Bad strategy (100 trials) DSR: {bad:.4f}")

        psr = probabilistic_sharpe_ratio(1.5, 500, benchmark_sharpe=0.5)
        print(f"PSR vs 0.5 benchmark: {psr:.4f}")
        print("OK")
