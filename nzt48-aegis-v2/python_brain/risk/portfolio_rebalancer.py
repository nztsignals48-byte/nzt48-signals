"""Real-Time Portfolio Rebalancing Under Costs — Book 56.

Rebalances portfolio allocation across strategies while accounting for
transaction costs. Only rebalance when the benefit exceeds the cost.

No-trade zone: if current weight is within ±threshold of target,
don't rebalance (the cost of trading would destroy the benefit).

Usage:
    from python_brain.risk.portfolio_rebalancer import (
        PortfolioRebalancer, RebalanceOrder,
    )

    rebalancer = PortfolioRebalancer(cost_per_trade_pct=0.30)
    orders = rebalancer.compute_rebalance(
        current_weights={"TypeF": 0.40, "S2": 0.30, "TypeB": 0.30},
        target_weights={"TypeF": 0.50, "S2": 0.25, "TypeB": 0.25},
        equity=10000,
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

log = logging.getLogger("portfolio_rebalancer")


@dataclass
class RebalanceOrder:
    """A single rebalancing trade."""
    strategy: str
    direction: str  # "increase" or "decrease"
    current_weight: float
    target_weight: float
    delta_weight: float
    delta_gbp: float
    cost_gbp: float  # Expected cost of this rebalance trade
    net_benefit: float  # Expected benefit minus cost


class PortfolioRebalancer:
    """Cost-aware portfolio rebalancing."""

    def __init__(
        self,
        cost_per_trade_pct: float = 0.30,  # Round-trip cost as % of notional
        no_trade_zone_pct: float = 5.0,    # Don't rebalance if within ±5%
        min_rebalance_gbp: float = 200.0,  # Min trade size to bother
    ):
        self.cost_pct = cost_per_trade_pct
        self.no_trade_zone = no_trade_zone_pct / 100
        self.min_rebalance = min_rebalance_gbp

    def compute_rebalance(
        self,
        current_weights: Dict[str, float],
        target_weights: Dict[str, float],
        equity: float,
    ) -> List[RebalanceOrder]:
        """Compute rebalancing orders with cost awareness.

        Only generates orders where benefit > cost.
        """
        orders = []
        all_strategies = set(current_weights.keys()) | set(target_weights.keys())

        for strategy in all_strategies:
            current = current_weights.get(strategy, 0)
            target = target_weights.get(strategy, 0)
            delta = target - current

            # No-trade zone: skip if within threshold
            if abs(delta) < self.no_trade_zone:
                continue

            delta_gbp = delta * equity
            if abs(delta_gbp) < self.min_rebalance:
                continue

            # Cost of rebalancing this position
            cost = abs(delta_gbp) * self.cost_pct / 100

            # Expected benefit: reduced tracking error
            # Simplified: benefit proportional to square of deviation (quadratic utility)
            benefit = delta * delta * equity * 0.5  # Rough benefit estimate

            direction = "increase" if delta > 0 else "decrease"

            orders.append(RebalanceOrder(
                strategy=strategy,
                direction=direction,
                current_weight=round(current, 4),
                target_weight=round(target, 4),
                delta_weight=round(delta, 4),
                delta_gbp=round(delta_gbp, 2),
                cost_gbp=round(cost, 2),
                net_benefit=round(benefit - cost, 2),
            ))

        # Only execute orders with positive net benefit
        profitable = [o for o in orders if o.net_benefit > 0]

        # Sort: largest absolute delta first
        profitable.sort(key=lambda o: abs(o.delta_gbp), reverse=True)

        if profitable:
            total_cost = sum(o.cost_gbp for o in profitable)
            log.info("REBALANCE: %d orders, total cost=%.2f GBP", len(profitable), total_cost)

        return profitable

    def should_rebalance(
        self,
        current_weights: Dict[str, float],
        target_weights: Dict[str, float],
        threshold: float = 0.10,  # 10% total deviation
    ) -> bool:
        """Quick check: is total portfolio drift large enough to warrant rebalancing?"""
        all_strategies = set(current_weights.keys()) | set(target_weights.keys())
        total_drift = sum(
            abs(target_weights.get(s, 0) - current_weights.get(s, 0))
            for s in all_strategies
        )
        return total_drift > threshold
