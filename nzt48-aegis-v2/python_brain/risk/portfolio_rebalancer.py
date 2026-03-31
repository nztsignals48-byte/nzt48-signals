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


# ---------------------------------------------------------------------------
# Almgren-Chriss No-Trade Zone + Regime Bands — Book 56 extensions
# ---------------------------------------------------------------------------

import math


def almgren_chriss_ntz(
    position_gbp: float,
    target_gbp: float,
    vol: float,
    trading_cost: float,
    risk_aversion: float = 1e-6,
) -> float:
    """Almgren-Chriss no-trade zone width.

    The optimal NTZ half-width where the cost of trading exceeds
    the benefit of being closer to target:
        width = sqrt(4 * trading_cost * vol / lambda)

    Args:
        position_gbp: current position value in GBP
        target_gbp: target position value in GBP
        vol: annualized volatility of the position (decimal, e.g. 0.20)
        trading_cost: round-trip cost as fraction (e.g. 0.003 for 30 bps)
        risk_aversion: lambda parameter (default 1e-6)

    Returns:
        NTZ half-width in GBP. If |position - target| < width, don't trade.
    """
    if risk_aversion <= 0 or vol <= 0:
        return 0.0
    width = math.sqrt(4.0 * trading_cost * vol / risk_aversion)
    return width


@dataclass
class AsymmetricBand:
    """Asymmetric no-trade band (wider upper for leveraged ETPs due to decay)."""
    lower: float  # Lower NTZ bound (GBP below target)
    upper: float  # Upper NTZ bound (GBP above target) — wider for leveraged ETPs
    reason: str = ""  # e.g. "3x leveraged ETP — decay widens upper band"


def regime_ntz_multiplier(regime: str) -> float:
    """Regime-dependent NTZ multiplier.

    Wider no-trade zones in volatile regimes to reduce churn.

    Args:
        regime: one of STEADY, WOI (watch-of-interest), CRISIS, EXTREME

    Returns:
        Multiplier for NTZ width (1.0 = normal)
    """
    multipliers = {
        "STEADY": 1.0,
        "WOI": 1.5,
        "CRISIS": 2.0,
        "EXTREME": 3.0,
    }
    return multipliers.get(regime.upper(), 1.0)


def coordinated_rebalance(
    positions: Dict[str, float],
    targets: Dict[str, float],
    costs: Dict[str, float],
    inter_cluster_delay_secs: float = 2.0,
) -> List[Dict]:
    """Greedy coordinated rebalance: sell first, buy second, inter-cluster delay.

    Ensures sells generate cash before buys consume it.

    Args:
        positions: current position values {strategy: gbp_value}
        targets: target position values {strategy: gbp_value}
        costs: trading cost per strategy {strategy: cost_fraction}
        inter_cluster_delay_secs: delay between sell and buy clusters

    Returns:
        Ordered list of rebalance instructions:
        [{"strategy": str, "action": "sell"|"buy", "delta_gbp": float, "cost_gbp": float, "cluster": int}]
    """
    sells = []
    buys = []

    all_strategies = set(positions.keys()) | set(targets.keys())

    for strategy in all_strategies:
        current = positions.get(strategy, 0.0)
        target = targets.get(strategy, 0.0)
        delta = target - current
        cost_rate = costs.get(strategy, 0.003)
        cost_gbp = abs(delta) * cost_rate

        if delta < -1.0:  # Sell (reduce position)
            sells.append({
                "strategy": strategy,
                "action": "sell",
                "delta_gbp": round(delta, 2),
                "cost_gbp": round(cost_gbp, 2),
                "cluster": 0,
            })
        elif delta > 1.0:  # Buy (increase position)
            buys.append({
                "strategy": strategy,
                "action": "buy",
                "delta_gbp": round(delta, 2),
                "cost_gbp": round(cost_gbp, 2),
                "cluster": 1,
            })

    # Sort sells by magnitude (largest sell first to free cash)
    sells.sort(key=lambda x: x["delta_gbp"])
    # Sort buys by magnitude (largest buy first for best fill)
    buys.sort(key=lambda x: x["delta_gbp"], reverse=True)

    orders = sells + buys

    if sells and buys:
        log.info("COORDINATED_REBALANCE: %d sells then %.0fs delay then %d buys",
                 len(sells), inter_cluster_delay_secs, len(buys))

    return orders
