"""Bayesian win rate estimation and Deflated Sharpe Ratio (DSR).

References:
  - Laplace smoothing for small-sample WR
  - Bailey & López de Prado (2014) "The Deflated Sharpe Ratio"
  - DSR = Φ((SR* - SR₀) / σ_SR₀)
    where σ_SR₀ = √((1 - γ₃·SR₀ + ((γ₄-1)/4)·SR₀²) / (T-1))
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

from .config import (
    BENCHMARK_SHARPE,
    LAPLACE_PRIOR_TOTAL,
    LAPLACE_PRIOR_WINS,
    MIN_TRADES_FOR_DSR,
)


@dataclass(frozen=True)
class BayesianResult:
    """Output of Bayesian win rate estimation."""
    raw_win_rate: float
    bayesian_win_rate: float
    trade_count: int
    total_pnl: float
    avg_win: float
    avg_loss: float


@dataclass(frozen=True)
class DSRResult:
    """Output of Deflated Sharpe Ratio calculation."""
    sharpe_ratio: float
    dsr: float
    dsr_pvalue: float
    skewness: float
    kurtosis: float
    is_significant: bool


def bayesian_win_rate(pnls: List[float]) -> BayesianResult:
    """Compute Bayesian-adjusted win rate with Laplace smoothing.

    With small samples, shrinks toward 50% (uninformative prior).
    As N grows, converges to raw win rate.
    """
    n = len(pnls)
    if n == 0:
        return BayesianResult(
            raw_win_rate=0.5,
            bayesian_win_rate=0.5,
            trade_count=0,
            total_pnl=0.0,
            avg_win=0.0,
            avg_loss=0.0,
        )

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    raw_wr = len(wins) / n if n > 0 else 0.5

    # Laplace smoothing: (wins + α) / (total + α + β)
    bayesian_wr = (len(wins) + LAPLACE_PRIOR_WINS) / (n + LAPLACE_PRIOR_TOTAL)

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0

    return BayesianResult(
        raw_win_rate=raw_wr,
        bayesian_win_rate=bayesian_wr,
        trade_count=n,
        total_pnl=sum(pnls),
        avg_win=avg_win,
        avg_loss=avg_loss,
    )


def deflated_sharpe_ratio(
    returns: List[float],
    sr_benchmark: float = BENCHMARK_SHARPE,
) -> DSRResult:
    """Compute Deflated Sharpe Ratio per Bailey & López de Prado (2014).

    DSR accounts for skewness and kurtosis of returns, penalizing
    strategies that achieve high Sharpe through tail risk.

    Args:
        returns: List of per-trade returns (not PnL, but % returns).
        sr_benchmark: Null hypothesis Sharpe ratio (default 0.0).

    Returns:
        DSRResult with Sharpe, DSR, p-value, and significance flag.
    """
    n = len(returns)
    if n < MIN_TRADES_FOR_DSR:
        return DSRResult(
            sharpe_ratio=0.0,
            dsr=0.0,
            dsr_pvalue=1.0,
            skewness=0.0,
            kurtosis=3.0,
            is_significant=False,
        )

    mean_r = sum(returns) / n
    variance = sum((r - mean_r) ** 2 for r in returns) / max(n - 1, 1)
    std_r = math.sqrt(variance) if variance > 0 else 1e-10

    sharpe = mean_r / std_r

    # Skewness (γ₃)
    m3 = sum((r - mean_r) ** 3 for r in returns) / n
    skewness = m3 / (std_r ** 3) if std_r > 1e-10 else 0.0

    # Excess kurtosis (γ₄)
    m4 = sum((r - mean_r) ** 4 for r in returns) / n
    kurtosis = (m4 / (std_r ** 4)) if std_r > 1e-10 else 3.0

    # DSR formula: σ_SR₀ = √((1 - γ₃·SR₀ + ((γ₄-1)/4)·SR₀²) / (T-1))
    sr0 = sr_benchmark
    numerator = 1.0 - skewness * sr0 + ((kurtosis - 1.0) / 4.0) * sr0 ** 2
    sigma_sr = math.sqrt(max(numerator, 1e-10) / max(n - 1, 1))

    # DSR = Φ((SR* - SR₀) / σ_SR₀)
    z_score = (sharpe - sr0) / sigma_sr if sigma_sr > 1e-10 else 0.0
    dsr = _normal_cdf(z_score)
    pvalue = 1.0 - dsr

    return DSRResult(
        sharpe_ratio=sharpe,
        dsr=dsr,
        dsr_pvalue=pvalue,
        skewness=skewness,
        kurtosis=kurtosis,
        is_significant=dsr > 0.95,
    )


def _normal_cdf(x: float) -> float:
    """Standard normal CDF using error function (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
