"""Monte Carlo Simulation Engine — Book 17.

10,000-path equity simulation for:
1. Ruin probability at current Sharpe/leverage
2. Drawdown distribution (P(DD > X%))
3. Confidence intervals for Sharpe, Kelly, max drawdown
4. Bootstrap from actual WAL trade P&L (non-parametric)

Two modes:
  Parametric: GBM with regime transitions
  Non-parametric: Block bootstrap from actual trade returns

Usage:
    from python_brain.validation.monte_carlo import (
        run_monte_carlo, MCResult,
    )

    result = run_monte_carlo(trade_returns, n_paths=10000, horizon_days=252)
    print(f"Ruin probability: {result.ruin_probability:.1%}")
    print(f"Sharpe 95% CI: [{result.sharpe_ci_low:.2f}, {result.sharpe_ci_high:.2f}]")
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

log = logging.getLogger("monte_carlo")


@dataclass
class MCResult:
    """Results from Monte Carlo simulation."""
    n_paths: int = 0
    horizon_days: int = 0
    # Ruin analysis
    ruin_probability: float = 0.0  # P(equity < ruin_threshold)
    ruin_threshold_pct: float = 8.0  # Default: 8% sacred limit
    # Drawdown distribution
    median_max_drawdown_pct: float = 0.0
    p5_max_drawdown_pct: float = 0.0   # 5th percentile (worst case)
    p95_max_drawdown_pct: float = 0.0  # 95th percentile (best case)
    # Terminal equity
    median_terminal_equity: float = 0.0
    p5_terminal_equity: float = 0.0
    p95_terminal_equity: float = 0.0
    # Sharpe CI
    sharpe_estimate: float = 0.0
    sharpe_ci_low: float = 0.0   # 5th percentile
    sharpe_ci_high: float = 0.0  # 95th percentile
    # Kelly CI
    kelly_estimate: float = 0.0
    kelly_ci_low: float = 0.0
    kelly_ci_high: float = 0.0
    # Probability of positive return
    prob_positive: float = 0.0

    def to_dict(self) -> dict:
        return {
            "n_paths": self.n_paths,
            "horizon_days": self.horizon_days,
            "ruin_probability": round(self.ruin_probability, 4),
            "median_max_dd_pct": round(self.median_max_drawdown_pct, 2),
            "p5_max_dd_pct": round(self.p5_max_drawdown_pct, 2),
            "median_terminal": round(self.median_terminal_equity, 2),
            "p5_terminal": round(self.p5_terminal_equity, 2),
            "sharpe_estimate": round(self.sharpe_estimate, 3),
            "sharpe_ci": [round(self.sharpe_ci_low, 3), round(self.sharpe_ci_high, 3)],
            "kelly_ci": [round(self.kelly_ci_low, 4), round(self.kelly_ci_high, 4)],
            "prob_positive": round(self.prob_positive, 3),
        }


def run_monte_carlo(
    trade_returns: np.ndarray,
    n_paths: int = 10000,
    horizon_days: int = 252,
    initial_equity: float = 10000.0,
    ruin_threshold_pct: float = 8.0,
    block_size: int = 5,
    seed: int = 42,
) -> MCResult:
    """Run non-parametric block bootstrap Monte Carlo simulation.

    Uses actual trade returns (from WAL) resampled in blocks to preserve
    autocorrelation structure.

    Args:
        trade_returns: Array of per-trade returns (decimal, e.g. 0.015 for +1.5%)
        n_paths: Number of simulation paths
        horizon_days: Trading days to simulate
        initial_equity: Starting equity
        ruin_threshold_pct: Drawdown % that constitutes ruin
        block_size: Size of blocks for bootstrap
        seed: Random seed for reproducibility
    """
    rng = np.random.default_rng(seed)
    n_trades = len(trade_returns)
    result = MCResult(n_paths=n_paths, horizon_days=horizon_days, ruin_threshold_pct=ruin_threshold_pct)

    if n_trades < 10:
        log.warning("MC: insufficient trades (%d < 10), returning empty result", n_trades)
        return result

    # Compute base statistics
    mean_r = float(np.mean(trade_returns))
    std_r = float(np.std(trade_returns, ddof=1))
    result.sharpe_estimate = mean_r / max(std_r, 1e-10) * math.sqrt(252) if std_r > 0 else 0

    # Block bootstrap: resample blocks of consecutive trades
    n_blocks = max(1, n_trades // block_size)

    terminal_equities = np.zeros(n_paths)
    max_drawdowns = np.zeros(n_paths)
    path_sharpes = np.zeros(n_paths)
    ruin_count = 0

    for i in range(n_paths):
        # Resample blocks
        block_starts = rng.integers(0, max(1, n_trades - block_size), size=horizon_days // block_size + 1)
        sampled_returns = []
        for start in block_starts:
            end = min(start + block_size, n_trades)
            sampled_returns.extend(trade_returns[start:end])

        # Trim to horizon
        path_returns = np.array(sampled_returns[:horizon_days])

        # Compute equity path
        equity_path = initial_equity * np.cumprod(1 + path_returns)

        # Terminal equity
        terminal_equities[i] = equity_path[-1]

        # Max drawdown
        running_max = np.maximum.accumulate(equity_path)
        drawdowns = (running_max - equity_path) / running_max * 100
        max_drawdowns[i] = float(np.max(drawdowns))

        # Ruin check
        if max_drawdowns[i] >= ruin_threshold_pct:
            ruin_count += 1

        # Path Sharpe
        if len(path_returns) > 1 and np.std(path_returns) > 0:
            path_sharpes[i] = np.mean(path_returns) / np.std(path_returns) * math.sqrt(252)

    # Aggregate results
    result.ruin_probability = ruin_count / n_paths
    result.median_max_drawdown_pct = float(np.median(max_drawdowns))
    result.p5_max_drawdown_pct = float(np.percentile(max_drawdowns, 95))  # Worst 5%
    result.p95_max_drawdown_pct = float(np.percentile(max_drawdowns, 5))  # Best 5%

    result.median_terminal_equity = float(np.median(terminal_equities))
    result.p5_terminal_equity = float(np.percentile(terminal_equities, 5))
    result.p95_terminal_equity = float(np.percentile(terminal_equities, 95))

    result.sharpe_ci_low = float(np.percentile(path_sharpes, 5))
    result.sharpe_ci_high = float(np.percentile(path_sharpes, 95))

    result.prob_positive = float(np.mean(terminal_equities > initial_equity))

    # Kelly CI via bootstrap
    wins = trade_returns[trade_returns > 0]
    losses = trade_returns[trade_returns <= 0]
    if len(wins) > 0 and len(losses) > 0:
        kelly_samples = []
        for _ in range(1000):
            idx = rng.integers(0, n_trades, size=n_trades)
            boot_returns = trade_returns[idx]
            boot_wins = boot_returns[boot_returns > 0]
            boot_losses = boot_returns[boot_returns <= 0]
            if len(boot_wins) > 0 and len(boot_losses) > 0:
                p = len(boot_wins) / len(boot_returns)
                b = np.mean(boot_wins) / abs(np.mean(boot_losses))
                kelly = max(0, (p * b - (1 - p)) / b * 0.5)  # Half-Kelly
                kelly_samples.append(kelly)
        if kelly_samples:
            arr = np.array(kelly_samples)
            result.kelly_estimate = float(np.median(arr))
            result.kelly_ci_low = float(np.percentile(arr, 5))
            result.kelly_ci_high = float(np.percentile(arr, 95))

    log.info(
        "MC complete: %d paths, ruin=%.1f%%, median_DD=%.1f%%, "
        "Sharpe=[%.2f, %.2f], P(positive)=%.0f%%",
        n_paths, result.ruin_probability * 100,
        result.median_max_drawdown_pct,
        result.sharpe_ci_low, result.sharpe_ci_high,
        result.prob_positive * 100,
    )
    return result
