"""Dynamic Capital Reallocation & Meta-Allocator — Book 131.

The meta-allocator sits ABOVE individual strategy sizing.
It decides how much capital each strategy gets based on:
1. Live Sharpe ratio (rolling 60-trade)
2. Win rate trend (improving/declining)
3. Drawdown phase contribution
4. Correlation with other active strategies
5. Capital efficiency (return per unit risk)

Darwinian principle: capital flows FROM underperformers TO outperformers.
Strategies that prove themselves get more. Unproven get minimum flow.

Rebalancing: nightly (Book 10 Shannon's Demon bonus).

Usage:
    from python_brain.sizing.meta_allocator import MetaAllocator

    allocator = MetaAllocator(total_equity=10000)
    weights = allocator.compute_weights(strategy_metrics)
    allocation = allocator.allocate(weights)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

log = logging.getLogger("meta_allocator")


@dataclass
class StrategyMetrics:
    """Performance metrics for allocation decision."""
    name: str
    sharpe_60: float = 0.0
    win_rate: float = 0.5
    n_trades: int = 0
    pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_correlation: float = 0.0  # With other strategies


@dataclass
class AllocationResult:
    """Capital allocation for a single strategy."""
    strategy: str
    weight: float = 0.0      # 0.0-1.0 fraction of total
    capital_gbp: float = 0.0
    reason: str = ""


class MetaAllocator:
    """Allocate capital across strategies based on demonstrated performance."""

    def __init__(
        self,
        total_equity: float = 10000.0,
        min_weight: float = 0.05,    # Every strategy gets at least 5%
        max_weight: float = 0.40,    # No strategy gets more than 40%
        min_trades_for_full: int = 50,  # Need 50 trades for full weight
    ):
        self.total_equity = total_equity
        self.min_weight = min_weight
        self.max_weight = max_weight
        self.min_trades = min_trades_for_full

    def compute_weights(
        self,
        metrics: Dict[str, StrategyMetrics],
    ) -> Dict[str, float]:
        """Compute allocation weights from strategy metrics.

        Scoring formula:
          raw_score = sharpe_component (40%) + win_rate_component (30%) +
                      efficiency_component (20%) + decorrelation_bonus (10%)
        """
        if not metrics:
            return {}

        scores: Dict[str, float] = {}

        for name, m in metrics.items():
            # Sharpe component (40%): 0 at Sharpe<=0, linear to 1.0 at Sharpe=2.0
            sharpe_score = max(0, min(1.0, m.sharpe_60 / 2.0)) * 0.40

            # Win rate component (30%): 0 at WR<=0.35, 1.0 at WR>=0.65
            wr_score = max(0, min(1.0, (m.win_rate - 0.35) / 0.30)) * 0.30

            # Efficiency component (20%): P&L per trade
            if m.n_trades > 0:
                pnl_per_trade = m.pnl / m.n_trades
                eff_score = max(0, min(1.0, pnl_per_trade / 10.0)) * 0.20  # 10 GBP/trade = 1.0
            else:
                eff_score = 0.0

            # Decorrelation bonus (10%): lower correlation = higher weight
            decorr_score = max(0, (1.0 - m.avg_correlation)) * 0.10

            # Trade count ramp: strategies with <min_trades get penalized
            if m.n_trades < self.min_trades:
                ramp = m.n_trades / max(self.min_trades, 1)
            else:
                ramp = 1.0

            total_score = (sharpe_score + wr_score + eff_score + decorr_score) * ramp
            scores[name] = total_score

        # Normalize to weights
        total_score = sum(scores.values())
        if total_score <= 0:
            # Equal weight if no strategy has positive score
            n = len(metrics)
            return {name: 1.0 / n for name in metrics}

        weights = {}
        for name, score in scores.items():
            raw_weight = score / total_score
            # Clamp to [min, max]
            clamped = max(self.min_weight, min(self.max_weight, raw_weight))
            weights[name] = clamped

        # Renormalize after clamping
        total_w = sum(weights.values())
        if total_w > 0:
            weights = {k: v / total_w for k, v in weights.items()}

        return weights

    def allocate(
        self,
        weights: Dict[str, float],
    ) -> Dict[str, AllocationResult]:
        """Convert weights to capital allocations."""
        results = {}
        for strategy, weight in weights.items():
            results[strategy] = AllocationResult(
                strategy=strategy,
                weight=round(weight, 4),
                capital_gbp=round(weight * self.total_equity, 2),
                reason=f"weight={weight:.1%} of {self.total_equity:.0f}",
            )
        return results

    def rebalance_report(
        self,
        current_weights: Dict[str, float],
        target_weights: Dict[str, float],
    ) -> Dict[str, float]:
        """Compute rebalancing trades needed.

        Returns: {strategy: delta_weight} where positive = increase, negative = decrease.
        """
        all_strategies = set(current_weights.keys()) | set(target_weights.keys())
        deltas = {}
        for s in all_strategies:
            current = current_weights.get(s, 0)
            target = target_weights.get(s, 0)
            delta = target - current
            if abs(delta) > 0.01:  # Only rebalance if >1% change
                deltas[s] = round(delta, 4)
        return deltas
