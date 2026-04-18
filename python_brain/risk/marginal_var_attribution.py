"""Marginal VaR / component VaR attribution per position.

Tells us in real time which position is driving portfolio VaR.
Consumed by Grafana "top VaR contributors" panel.

Reference: Jorion (2007) VaR, Ch 7.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class MarginalVaR:
    symbol: str
    weight: float
    marginal_var: float       # dVaR/dweight
    component_var: float      # weight * marginal_var
    pct_contribution: float   # component / total VaR


def portfolio_var(weights: np.ndarray, cov: np.ndarray, z: float = 1.6449) -> float:
    """Parametric VaR = z * sqrt(w^T Σ w)."""
    var = float(weights @ cov @ weights)
    return z * np.sqrt(max(var, 0.0))


def marginal_var_vector(weights: np.ndarray, cov: np.ndarray, z: float = 1.6449) -> np.ndarray:
    """d(VaR)/d(w_i) = z * (Σ w)_i / sqrt(w^T Σ w)."""
    sigma_p = np.sqrt(max(float(weights @ cov @ weights), 1e-18))
    return z * (cov @ weights) / sigma_p


def attribute(
    symbols: list[str],
    weights: np.ndarray,       # fraction of portfolio value
    cov: np.ndarray,           # daily return covariance
    confidence: float = 0.95,
) -> tuple[float, list[MarginalVaR]]:
    """Return (portfolio_var_usd_per_unit, list of per-symbol contributions)."""
    z = 1.6449 if confidence == 0.95 else (2.3263 if confidence == 0.99 else 1.2816)
    total_var = portfolio_var(weights, cov, z)
    if total_var <= 0 or weights.sum() == 0:
        return total_var, []

    mvar = marginal_var_vector(weights, cov, z)
    components = weights * mvar

    out = []
    for i, sym in enumerate(symbols):
        pct = (components[i] / total_var * 100) if total_var > 0 else 0.0
        out.append(MarginalVaR(
            symbol=sym,
            weight=float(weights[i]),
            marginal_var=float(mvar[i]),
            component_var=float(components[i]),
            pct_contribution=float(pct),
        ))
    out.sort(key=lambda x: -x.pct_contribution)
    return total_var, out


def top_contributors(attribution: list[MarginalVaR], n: int = 10) -> list[MarginalVaR]:
    return attribution[:n]


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        rng = np.random.default_rng(42)
        symbols = ["AAPL", "MSFT", "NVDA", "XOM", "GLD"]
        # Synthesize return history
        returns = np.stack([
            rng.normal(0.001, 0.015, 100),
            rng.normal(0.001, 0.012, 100),
            rng.normal(0.001, 0.025, 100),
            rng.normal(0.0005, 0.02, 100),
            rng.normal(0.0002, 0.008, 100),
        ], axis=1)
        cov = np.cov(returns, rowvar=False)
        weights = np.array([0.3, 0.25, 0.2, 0.15, 0.1])

        total, contribs = attribute(symbols, weights, cov)
        print(f"Portfolio VaR: {total:.4f}")
        for c in contribs:
            print(f"  {c.symbol}: w={c.weight:.2f} marginal={c.marginal_var:.4f} "
                  f"component={c.component_var:.4f} pct={c.pct_contribution:.1f}%")
        print("OK")
