"""Covariance-adjusted Kelly sizing — Thorp f* = Σ⁻¹μ with shrinkage.

Nightly updated by Ouroboros; writes per-strategy Kelly caps to learned.toml.
Sig2order consumes via its bandit_kelly dict.

References:
- Thorp (2006) "The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market"
- Zhu & Zhou (2017) "Multivariate Volatility Regulated Kelly Strategy"
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class KellyResult:
    strategies: list
    kelly_full: np.ndarray
    kelly_fractional: np.ndarray
    kelly_capped: np.ndarray
    shrinkage: float
    half_kelly: float
    cap: float


def kelly_adjusted(
    returns_matrix: np.ndarray,     # (n_days, n_strategies)
    strategy_names: list,
    shrinkage: float = 0.3,
    half_kelly: float = 0.5,
    cap_per_strategy: float = 0.05,
) -> KellyResult:
    """Compute fractional-Kelly per strategy with shrinkage + cap.

    Args:
        returns_matrix: daily per-strategy excess returns as fractions
        strategy_names: ordered list matching columns
        shrinkage: covariance shrinkage toward diagonal (0 = raw, 1 = fully diagonal)
        half_kelly: scale factor on full Kelly (0.5 = half-Kelly for safety)
        cap_per_strategy: max fraction per strategy
    """
    if returns_matrix.size == 0 or returns_matrix.shape[0] < 10:
        n = len(strategy_names)
        zeros = np.zeros(n)
        return KellyResult(strategy_names, zeros, zeros, zeros, shrinkage, half_kelly, cap_per_strategy)

    mu = returns_matrix.mean(axis=0)
    Sigma_raw = np.cov(returns_matrix, rowvar=False)
    # Ensure 2D in single-strategy case
    if Sigma_raw.ndim == 0:
        Sigma_raw = np.array([[float(Sigma_raw)]])

    # Shrink toward diagonal
    diag = np.diag(np.diag(Sigma_raw))
    Sigma = (1 - shrinkage) * Sigma_raw + shrinkage * diag
    # Regularize for numerical stability
    Sigma += np.eye(Sigma.shape[0]) * 1e-9

    try:
        f_full = np.linalg.solve(Sigma, mu)
    except np.linalg.LinAlgError:
        f_full = mu / np.maximum(np.diag(Sigma), 1e-9)

    f_fractional = f_full * half_kelly
    f_capped = np.clip(f_fractional, 0.0, cap_per_strategy)

    return KellyResult(
        strategies=list(strategy_names),
        kelly_full=f_full,
        kelly_fractional=f_fractional,
        kelly_capped=f_capped,
        shrinkage=shrinkage,
        half_kelly=half_kelly,
        cap=cap_per_strategy,
    )


def kelly_from_fills(
    fills_by_strategy: dict[str, np.ndarray],
    shrinkage: float = 0.3,
    half_kelly: float = 0.5,
    cap: float = 0.05,
) -> dict[str, float]:
    """Convenience: take dict of strategy -> per-day returns, return Kelly dict."""
    if not fills_by_strategy:
        return {}
    strategies = sorted(fills_by_strategy.keys())
    # Align lengths (shortest wins)
    min_len = min(len(v) for v in fills_by_strategy.values())
    if min_len < 10:
        return {s: 0.005 for s in strategies}  # minimum Kelly
    matrix = np.stack([fills_by_strategy[s][-min_len:] for s in strategies], axis=1)
    result = kelly_adjusted(matrix, strategies, shrinkage, half_kelly, cap)
    return {s: float(k) for s, k in zip(result.strategies, result.kelly_capped)}


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        rng = np.random.default_rng(42)
        # 3 strategies: good, mediocre, bad
        n_days = 60
        good = rng.normal(0.002, 0.01, n_days)
        mid = rng.normal(0.0005, 0.012, n_days)
        bad = rng.normal(-0.001, 0.015, n_days)
        fills = {"good": good, "mid": mid, "bad": bad}
        kellys = kelly_from_fills(fills)
        print(f"Kellys: {kellys}")
        print("OK")
