"""Bootstrap confidence intervals for Sharpe / PF / DSR.

Addresses James Okonkwo persona — no point estimate without uncertainty bounds.

Consumed by ouroboros_v2_nightly.py in per-strategy reports.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class CIResult:
    estimate: float
    ci_low: float
    ci_high: float
    std_err: float
    n_bootstrap: int
    confidence: float


def bootstrap_sharpe(
    returns: np.ndarray,
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    annualize_factor: float = 252,
    seed: int = 42,
) -> CIResult:
    """Bootstrap CI for annualized Sharpe ratio."""
    if len(returns) < 10:
        return CIResult(0.0, 0.0, 0.0, 0.0, 0, confidence)
    rng = np.random.default_rng(seed)
    n = len(returns)
    sharpes = np.empty(n_bootstrap, dtype=float)
    for i in range(n_bootstrap):
        idx = rng.integers(0, n, n)
        sample = returns[idx]
        std = sample.std()
        sharpes[i] = sample.mean() / std * math.sqrt(annualize_factor) if std > 0 else 0.0

    alpha = 1 - confidence
    low = float(np.quantile(sharpes, alpha / 2))
    high = float(np.quantile(sharpes, 1 - alpha / 2))
    est = float(returns.mean() / returns.std() * math.sqrt(annualize_factor)) if returns.std() > 0 else 0.0
    return CIResult(est, low, high, float(sharpes.std()), n_bootstrap, confidence)


def bootstrap_profit_factor(
    returns: np.ndarray,
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    seed: int = 42,
) -> CIResult:
    """Bootstrap CI for Profit Factor (sum wins / sum |losses|)."""
    if len(returns) < 10:
        return CIResult(0.0, 0.0, 0.0, 0.0, 0, confidence)
    rng = np.random.default_rng(seed)
    n = len(returns)
    pfs = np.empty(n_bootstrap, dtype=float)
    for i in range(n_bootstrap):
        idx = rng.integers(0, n, n)
        sample = returns[idx]
        wins = sample[sample > 0].sum()
        losses = -sample[sample < 0].sum()
        pfs[i] = wins / losses if losses > 0 else (float("inf") if wins > 0 else 1.0)
    # Clip infinities for quantile
    finite_mask = np.isfinite(pfs)
    finite = pfs[finite_mask]
    if len(finite) == 0:
        return CIResult(float("inf"), 0.0, float("inf"), 0.0, 0, confidence)
    alpha = 1 - confidence
    low = float(np.quantile(finite, alpha / 2))
    high = float(np.quantile(finite, 1 - alpha / 2))
    wins = returns[returns > 0].sum()
    losses = -returns[returns < 0].sum()
    est = float(wins / losses) if losses > 0 else float("inf")
    return CIResult(est, low, high, float(finite.std()), n_bootstrap, confidence)


def bootstrap_max_drawdown(
    returns: np.ndarray,
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    seed: int = 42,
) -> CIResult:
    """Bootstrap CI for max drawdown (assuming IID — approximate)."""
    if len(returns) < 10:
        return CIResult(0.0, 0.0, 0.0, 0.0, 0, confidence)
    rng = np.random.default_rng(seed)
    n = len(returns)
    dds = np.empty(n_bootstrap, dtype=float)
    for i in range(n_bootstrap):
        idx = rng.integers(0, n, n)
        sample = returns[idx]
        cum = np.cumprod(1 + sample)
        run_max = np.maximum.accumulate(cum)
        dds[i] = float(((run_max - cum) / np.maximum(run_max, 1e-9)).max())
    alpha = 1 - confidence
    low = float(np.quantile(dds, alpha / 2))
    high = float(np.quantile(dds, 1 - alpha / 2))
    # Observed
    cum_obs = np.cumprod(1 + returns)
    rm_obs = np.maximum.accumulate(cum_obs)
    est = float(((rm_obs - cum_obs) / np.maximum(rm_obs, 1e-9)).max())
    return CIResult(est, low, high, float(dds.std()), n_bootstrap, confidence)


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        rng = np.random.default_rng(42)
        returns = rng.normal(0.0005, 0.01, 300)
        s = bootstrap_sharpe(returns)
        print(f"Sharpe: {s.estimate:.3f} [{s.ci_low:.3f}, {s.ci_high:.3f}]")
        p = bootstrap_profit_factor(returns)
        print(f"PF: {p.estimate:.3f} [{p.ci_low:.3f}, {p.ci_high:.3f}]")
        d = bootstrap_max_drawdown(returns)
        print(f"MaxDD: {d.estimate:.3%} [{d.ci_low:.3%}, {d.ci_high:.3%}]")
        print("OK")
