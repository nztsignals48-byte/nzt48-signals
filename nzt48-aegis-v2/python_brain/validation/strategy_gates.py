"""Strategy Validation Gates — Books 6, 31, 147, 192.

Anti-overfitting validation infrastructure. Every strategy must pass these
gates before promotion from paper to live trading.

Key metrics:
- Deflated Sharpe Ratio (DSR): Corrects for multiple testing
- Probability of Backtest Overfitting (PBO): Rejects strategies likely overfit
- Walk-Forward validation: Out-of-sample performance vs in-sample
- Minimum sample requirements: At least 30 trades, min backtest length

Usage:
    from python_brain.validation.strategy_gates import (
        deflated_sharpe_ratio, probability_backtest_overfit,
        validate_strategy, ValidationResult,
    )

    result = validate_strategy(trades, n_trials=20)
    if not result.passed:
        reject_strategy(result.reason)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

log = logging.getLogger("strategy_gates")


# ---------------------------------------------------------------------------
# Deflated Sharpe Ratio (Book 31, 192)
# ---------------------------------------------------------------------------
def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_trades: int,
    n_trials: int = 1,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Compute the Deflated Sharpe Ratio (DSR) per Bailey & Lopez de Prado (2014).

    Adjusts the observed Sharpe ratio for:
    1. Multiple testing (n_trials strategies tried)
    2. Non-normal returns (skewness, excess kurtosis)
    3. Small sample size (n_trades)

    Returns: DSR value. Strategy is statistically significant if DSR > 0.
    A higher DSR means more confidence the Sharpe is real, not lucky.

    The key insight: if you test 100 strategies and pick the best Sharpe,
    the expected max Sharpe from pure noise is ~2.3 (for N=252).
    DSR corrects for this selection bias.
    """
    if n_trades < 2 or observed_sharpe <= 0:
        return 0.0

    T = n_trades
    # Expected maximum Sharpe from N independent trials (Euler-Mascheroni)
    euler_gamma = 0.5772156649
    if n_trials > 1:
        e_max_sharpe = (
            (1 - euler_gamma) * _ppf_normal(1 - 1.0 / n_trials)
            + euler_gamma * _ppf_normal(1 - 1.0 / (n_trials * math.e))
        )
    else:
        e_max_sharpe = 0.0

    # Standard error of Sharpe ratio with non-normal returns
    # SE(SR) = sqrt((1 - skew*SR + (kurtosis-1)/4 * SR^2) / (T-1))
    excess_kurt = kurtosis - 3.0
    sr = observed_sharpe
    se_sr = math.sqrt(
        max(1e-10, (1 - skewness * sr + (excess_kurt / 4) * sr * sr))
        / max(T - 1, 1)
    )

    if se_sr <= 0:
        return 0.0

    # DSR = P(SR* > E[max(SR)]) under null
    # Approximated as: (SR* - E[max(SR)]) / SE(SR*)
    dsr = (observed_sharpe - e_max_sharpe) / se_sr

    return dsr


def _ppf_normal(p: float) -> float:
    """Approximate inverse normal CDF (probit function).

    Rational approximation by Abramowitz and Stegun.
    Accurate to ~4.5e-4 for 0.5 < p < 1.0.
    """
    if p <= 0:
        return -6.0
    if p >= 1:
        return 6.0
    if p < 0.5:
        return -_ppf_normal(1 - p)

    t = math.sqrt(-2 * math.log(1 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t * t * t)


# ---------------------------------------------------------------------------
# Probability of Backtest Overfitting (Book 31)
# ---------------------------------------------------------------------------
def probability_backtest_overfit(
    is_returns: np.ndarray,
    oos_returns: np.ndarray,
    n_splits: int = 10,
) -> float:
    """Estimate Probability of Backtest Overfitting (PBO).

    PBO = fraction of CPCV folds where the best in-sample configuration
    ranks below median out-of-sample.

    Simplified version: compare IS vs OOS Sharpe degradation.
    If OOS Sharpe < IS Sharpe * 0.5, the strategy is likely overfit.

    Returns: PBO estimate (0.0 = no overfitting, 1.0 = certain overfit).
    Reject if PBO > 0.40.
    """
    if len(is_returns) < 20 or len(oos_returns) < 10:
        return 1.0  # Insufficient data = assume overfit

    is_sharpe = _compute_sharpe(is_returns)
    oos_sharpe = _compute_sharpe(oos_returns)

    if is_sharpe <= 0:
        return 1.0  # Negative IS Sharpe = no edge even in-sample

    degradation = oos_sharpe / is_sharpe if is_sharpe > 0 else 0.0

    # PBO approximation: map degradation to probability
    # degradation < 0.3 → PBO ~0.8 (severe overfit)
    # degradation 0.3-0.6 → PBO ~0.5 (moderate overfit)
    # degradation 0.6-0.9 → PBO ~0.2 (mild overfit)
    # degradation > 0.9 → PBO ~0.05 (robust)
    if degradation < 0.0:
        return 0.95
    elif degradation < 0.3:
        return 0.80
    elif degradation < 0.6:
        return 0.50 - (degradation - 0.3) * (0.30 / 0.3)
    elif degradation < 0.9:
        return 0.20 - (degradation - 0.6) * (0.15 / 0.3)
    else:
        return max(0.05, 0.05 * (2.0 - degradation))


def _compute_sharpe(returns: np.ndarray, annual_factor: float = 252.0) -> float:
    """Compute annualized Sharpe ratio from daily returns."""
    if len(returns) < 2:
        return 0.0
    mean_r = np.mean(returns)
    std_r = np.std(returns, ddof=1)
    if std_r < 1e-10:
        return 0.0
    return float(mean_r / std_r * math.sqrt(annual_factor))


# ---------------------------------------------------------------------------
# Minimum Backtest Length (Book 192)
# ---------------------------------------------------------------------------
def minimum_backtest_length(
    observed_sharpe: float,
    n_trials: int = 1,
    significance: float = 0.05,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> int:
    """Compute minimum number of trades needed for statistical significance.

    Per Bailey & Lopez de Prado (2012): MinBTL formula.
    Returns the minimum T (number of observations) such that
    P(SR > 0 | H0: SR = 0) < significance.
    """
    if observed_sharpe <= 0:
        return 999999  # Infinite length needed for zero/negative Sharpe

    z = _ppf_normal(1 - significance)
    excess_kurt = kurtosis - 3.0

    # MinBTL ≈ (z / SR)^2 * (1 + skew*SR/3 + excess_kurt*SR^2/12)
    sr = observed_sharpe / math.sqrt(252)  # Daily Sharpe
    if sr <= 0:
        return 999999

    correction = 1 + skewness * sr / 3 + excess_kurt * sr * sr / 12
    min_t = (z / sr) ** 2 * correction

    return max(30, int(math.ceil(min_t)))  # Floor at 30 trades


# ---------------------------------------------------------------------------
# Walk-Forward Validation Gate (Book 128)
# ---------------------------------------------------------------------------
def walk_forward_test(
    returns: np.ndarray,
    n_folds: int = 5,
    train_ratio: float = 0.6,
) -> Tuple[float, float, bool]:
    """Walk-forward validation: compare IS vs OOS Sharpe across folds.

    Returns: (avg_is_sharpe, avg_oos_sharpe, passes_gate)
    Gate passes if avg OOS Sharpe > 0.5 * avg IS Sharpe AND avg OOS > 0.
    """
    n = len(returns)
    fold_size = n // n_folds
    if fold_size < 20:
        return 0.0, 0.0, False

    is_sharpes: List[float] = []
    oos_sharpes: List[float] = []

    for i in range(n_folds):
        start = i * fold_size
        end = start + fold_size
        if end > n:
            break

        split = start + int(fold_size * train_ratio)
        is_ret = returns[start:split]
        oos_ret = returns[split:end]

        if len(is_ret) < 10 or len(oos_ret) < 5:
            continue

        is_sharpes.append(_compute_sharpe(is_ret))
        oos_sharpes.append(_compute_sharpe(oos_ret))

    if not is_sharpes:
        return 0.0, 0.0, False

    avg_is = sum(is_sharpes) / len(is_sharpes)
    avg_oos = sum(oos_sharpes) / len(oos_sharpes)
    passes = avg_oos > 0 and (avg_oos > 0.5 * avg_is or avg_is <= 0)

    return avg_is, avg_oos, passes


# ---------------------------------------------------------------------------
# Composite Validation Gate
# ---------------------------------------------------------------------------
@dataclass
class ValidationResult:
    """Result of strategy validation through all gates."""
    passed: bool = False
    reason: str = ""
    # Individual gate results
    n_trades: int = 0
    sharpe: float = 0.0
    dsr: float = 0.0
    pbo: float = 0.0
    max_drawdown_pct: float = 0.0
    walk_forward_oos_sharpe: float = 0.0
    min_backtest_length: int = 0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "n_trades": self.n_trades,
            "sharpe": round(self.sharpe, 3),
            "dsr": round(self.dsr, 3),
            "pbo": round(self.pbo, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "walk_forward_oos_sharpe": round(self.walk_forward_oos_sharpe, 3),
            "min_backtest_length": self.min_backtest_length,
        }


def validate_strategy(
    returns: np.ndarray,
    n_trials: int = 1,
    min_trades: int = 30,
    min_sharpe: float = 0.5,
    max_drawdown: float = 15.0,
    max_pbo: float = 0.40,
) -> ValidationResult:
    """Run all validation gates on a strategy's return series.

    Gates (sequential — first failure stops):
    1. Minimum sample size (default 30 trades)
    2. Sharpe ratio > min_sharpe (default 0.5)
    3. Max drawdown < max_drawdown (default 15%)
    4. Deflated Sharpe Ratio > 0 (accounts for multiple testing)
    5. PBO < max_pbo (default 0.40)
    6. Walk-forward OOS Sharpe > 0
    """
    result = ValidationResult()
    result.n_trades = len(returns)

    # Gate 1: Minimum sample
    if len(returns) < min_trades:
        result.reason = f"insufficient_trades: {len(returns)} < {min_trades}"
        return result

    # Gate 2: Sharpe ratio
    result.sharpe = _compute_sharpe(returns)
    if result.sharpe < min_sharpe:
        result.reason = f"sharpe_too_low: {result.sharpe:.3f} < {min_sharpe}"
        return result

    # Gate 3: Max drawdown
    cum = np.cumsum(returns)
    running_max = np.maximum.accumulate(cum)
    drawdowns = running_max - cum
    result.max_drawdown_pct = float(np.max(drawdowns)) * 100 if len(drawdowns) > 0 else 0
    if result.max_drawdown_pct > max_drawdown:
        result.reason = f"drawdown_too_high: {result.max_drawdown_pct:.1f}% > {max_drawdown}%"
        return result

    # Gate 4: Deflated Sharpe Ratio
    skew = float(np.mean((returns - np.mean(returns)) ** 3) / (np.std(returns, ddof=1) ** 3 + 1e-10)) if len(returns) > 2 else 0.0
    kurt = float(np.mean((returns - np.mean(returns)) ** 4) / (np.std(returns, ddof=1) ** 4 + 1e-10)) if len(returns) > 3 else 3.0
    result.dsr = deflated_sharpe_ratio(result.sharpe, len(returns), n_trials, skew, kurt)
    if result.dsr <= 0:
        result.reason = f"dsr_negative: {result.dsr:.3f} (adjusting for {n_trials} trials)"
        return result

    # Gate 5: PBO
    split = len(returns) // 2
    is_ret = returns[:split]
    oos_ret = returns[split:]
    result.pbo = probability_backtest_overfit(is_ret, oos_ret)
    if result.pbo > max_pbo:
        result.reason = f"pbo_too_high: {result.pbo:.2f} > {max_pbo}"
        return result

    # Gate 6: Walk-forward
    _, oos_sharpe, wf_passed = walk_forward_test(returns)
    result.walk_forward_oos_sharpe = oos_sharpe
    if not wf_passed:
        result.reason = f"walk_forward_failed: OOS Sharpe={oos_sharpe:.3f}"
        return result

    # Gate 7: Minimum backtest length
    result.min_backtest_length = minimum_backtest_length(result.sharpe, n_trials, skewness=skew, kurtosis=kurt)
    if len(returns) < result.min_backtest_length:
        result.reason = f"below_min_backtest_length: {len(returns)} < {result.min_backtest_length}"
        return result

    # All gates passed
    result.passed = True
    result.reason = "all_gates_passed"
    log.info(
        "Strategy VALIDATED: Sharpe=%.2f, DSR=%.2f, PBO=%.2f, DD=%.1f%%, WF_OOS=%.2f, n=%d",
        result.sharpe, result.dsr, result.pbo, result.max_drawdown_pct,
        result.walk_forward_oos_sharpe, result.n_trades,
    )
    return result
