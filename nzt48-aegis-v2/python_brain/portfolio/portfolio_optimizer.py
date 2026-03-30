"""Book 20 — Portfolio Optimizer (HRP + ISA Constraints + Brinson Attribution).

Implements Hierarchical Risk Parity for 7 core strategies with ISA-compliant constraints.
Uses stdlib only (math, statistics) — no numpy/scipy dependencies.

Strategies:
  1. Reversion       — Mean reversion + IBS
  2. TrendSurfer     — Multi-timeframe momentum
  3. NightRider      — Overnight gap exploitation
  4. VolHarvester    — Volatility arbitrage
  5. MetaRotator     — Cross-asset rotation
  6. EventSniper     — Event-driven alpha
  7. CrisisAlpha     — Tail-risk hedging

ISA Constraints:
  - No shorting (weights >= 0)
  - No margin (sum <= 1.0)
  - Max single strategy: 40%
  - Min single strategy: 5% (if allocated)

Usage:
    from python_brain.portfolio.portfolio_optimizer import run_portfolio_rebalance

    result = run_portfolio_rebalance()
    # Returns dict with allocation, attribution, rebalancing trades
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))

STRATEGIES = [
    "Reversion",
    "TrendSurfer",
    "NightRider",
    "VolHarvester",
    "MetaRotator",
    "EventSniper",
    "CrisisAlpha",
]

# ISA constraints
MAX_SINGLE_WEIGHT = 0.40  # 40%
MIN_SINGLE_WEIGHT = 0.05  # 5%
MAX_TOTAL_WEIGHT = 1.00   # No margin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PortfolioOptimizer] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("portfolio_optimizer")


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class PortfolioAllocation:
    """Portfolio allocation snapshot."""
    timestamp: str
    weights: Dict[str, float]
    target_weights: Dict[str, float]
    rebalance_trades: Dict[str, float]
    correlation_matrix: Dict[str, Dict[str, float]]
    total_weight: float
    constraints_satisfied: bool

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class AttributionResult:
    """Brinson-Fachler attribution analysis."""
    allocation_effect: float
    selection_effect: float
    total_excess_return: float
    breakdown: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------

def _safe_div(num: float, denom: float, default: float = 0.0) -> float:
    """Safe division avoiding ZeroDivisionError."""
    return num / denom if denom != 0 else default


def correlation(returns_a: List[float], returns_b: List[float]) -> float:
    """Compute Pearson correlation coefficient."""
    if len(returns_a) != len(returns_b) or len(returns_a) < 2:
        return 0.0
    n = len(returns_a)
    mean_a, mean_b = mean(returns_a), mean(returns_b)
    numerator = sum((returns_a[i] - mean_a) * (returns_b[i] - mean_b) for i in range(n))
    std_a = math.sqrt(sum((x - mean_a) ** 2 for x in returns_a) / n)
    std_b = math.sqrt(sum((x - mean_b) ** 2 for x in returns_b) / n)
    return numerator / (n * std_a * std_b) if std_a > 0 and std_b > 0 else 0.0


# ---------------------------------------------------------------------------
# HRP Implementation (Simplified Stdlib Version)
# ---------------------------------------------------------------------------

class PortfolioOptimizer:
    """Hierarchical Risk Parity optimizer with ISA constraints."""

    def __init__(self):
        self.strategies = STRATEGIES
        self.data_dir = DATA_DIR

    def load_strategy_returns(self) -> Dict[str, List[float]]:
        """Load strategy returns from strategy_pnl_history.json."""
        pnl_file = self.data_dir / "strategy_pnl_history.json"
        if not pnl_file.exists():
            log.warning("strategy_pnl_history.json not found, using dummy data")
            return {s: [0.01, -0.005, 0.015, 0.002, -0.01] for s in self.strategies}
        try:
            with open(pnl_file, "r") as f:
                data = json.load(f)
            return {s: data.get(s, [0.0] * 20) for s in self.strategies}
        except Exception as e:
            log.error(f"Failed to load strategy returns: {e}")
            return {s: [0.0] * 20 for s in self.strategies}

    def compute_correlation_matrix(
        self, returns_by_strategy: Dict[str, List[float]]
    ) -> Dict[str, Dict[str, float]]:
        """Compute correlation matrix for all strategies."""
        return {
            s1: {
                s2: 1.0 if s1 == s2 else correlation(returns_by_strategy[s1], returns_by_strategy[s2])
                for s2 in self.strategies
            }
            for s1 in self.strategies
        }

    def compute_distance_matrix(
        self, corr_matrix: Dict[str, Dict[str, float]]
    ) -> Dict[str, Dict[str, float]]:
        """Convert correlation to distance: d = sqrt(0.5 * (1 - corr))."""
        strategies = list(corr_matrix.keys())
        return {
            s1: {s2: math.sqrt(0.5 * (1 - corr_matrix[s1][s2])) for s2 in strategies}
            for s1 in strategies
        }


    def recursive_bisection(
        self,
        strategies: List[str],
        returns_by_strategy: Dict[str, List[float]],
        corr_matrix: Dict[str, Dict[str, float]],
    ) -> Dict[str, float]:
        """Recursively split strategies and allocate weights using inverse-volatility."""
        if len(strategies) == 0:
            return {}
        if len(strategies) == 1:
            return {strategies[0]: 1.0}

        # Find two least correlated strategies
        min_corr = float('inf')
        split_pair = None
        for s1 in strategies:
            for s2 in strategies:
                if s1 != s2 and corr_matrix[s1][s2] < min_corr:
                    min_corr = corr_matrix[s1][s2]
                    split_pair = (s1, s2)

        if not split_pair:
            mid = len(strategies) // 2
            group1, group2 = strategies[:mid], strategies[mid:]
        else:
            s1, s2 = split_pair
            group1, group2 = [s1], [s2]
            for s in strategies:
                if s not in (s1, s2):
                    (group1 if corr_matrix[s][s1] > corr_matrix[s][s2] else group2).append(s)

        # Inverse volatility allocation
        vol1 = mean([stdev(returns_by_strategy[s]) if len(returns_by_strategy[s]) >= 2 else 1.0 for s in group1])
        vol2 = mean([stdev(returns_by_strategy[s]) if len(returns_by_strategy[s]) >= 2 else 1.0 for s in group2])
        inv1, inv2 = 1.0 / vol1 if vol1 > 0 else 1.0, 1.0 / vol2 if vol2 > 0 else 1.0
        weight1, weight2 = inv1 / (inv1 + inv2), inv2 / (inv1 + inv2)

        # Recurse and combine
        weights1 = self.recursive_bisection(group1, returns_by_strategy, corr_matrix)
        weights2 = self.recursive_bisection(group2, returns_by_strategy, corr_matrix)
        return {**{s: w * weight1 for s, w in weights1.items()}, **{s: w * weight2 for s, w in weights2.items()}}

    def compute_hrp_weights(self, returns_by_strategy: Dict[str, List[float]]) -> Dict[str, float]:
        """Compute HRP weights using simplified stdlib approach."""
        corr_matrix = self.compute_correlation_matrix(returns_by_strategy)
        return self.recursive_bisection(self.strategies, returns_by_strategy, corr_matrix)

    def apply_constraints(self, weights: Dict[str, float]) -> Dict[str, float]:
        """Apply ISA constraints: no shorting, no margin, max 40%, min 5%."""
        # Floor negatives and zero out tiny allocations
        constrained = {s: max(0.0, w) for s, w in weights.items()}
        for s in list(constrained.keys()):
            if 0 < constrained[s] < MIN_SINGLE_WEIGHT:
                log.info(f"Zeroing {s} (below min {MIN_SINGLE_WEIGHT:.2%}): {constrained[s]:.2%}")
                constrained[s] = 0.0

        # Normalize to sum = 1.0
        total = sum(constrained.values())
        if total > 0:
            for s in constrained:
                constrained[s] *= MAX_TOTAL_WEIGHT / total

        # Iteratively cap and redistribute excess
        for _ in range(10):
            over_limit = {s: w - MAX_SINGLE_WEIGHT for s, w in constrained.items() if w > MAX_SINGLE_WEIGHT}
            if not over_limit:
                break
            for s in over_limit:
                constrained[s] = MAX_SINGLE_WEIGHT
            eligible = {s: w for s, w in constrained.items() if s not in over_limit and w > 0}
            if eligible:
                total_excess = sum(over_limit.values())
                total_eligible = sum(eligible.values())
                for s in eligible:
                    constrained[s] += (eligible[s] / total_eligible) * total_excess

        # Final normalization
        total = sum(constrained.values())
        if total > MAX_TOTAL_WEIGHT:
            for s in constrained:
                constrained[s] *= MAX_TOTAL_WEIGHT / total

        return constrained

    def compute_attribution(
        self, actual_weights: Dict[str, float], actual_returns: Dict[str, float],
        benchmark_weights: Dict[str, float], benchmark_returns: Dict[str, float],
    ) -> AttributionResult:
        """Compute Brinson-Fachler attribution."""
        breakdown = {}
        for s in self.strategies:
            w_a, w_b = actual_weights.get(s, 0.0), benchmark_weights.get(s, 0.0)
            r_a, r_b = actual_returns.get(s, 0.0), benchmark_returns.get(s, 0.0)
            breakdown[s] = {
                "allocation_effect": (w_a - w_b) * r_b,
                "selection_effect": w_b * (r_a - r_b),
                "weight_actual": w_a, "weight_benchmark": w_b,
                "return_actual": r_a, "return_benchmark": r_b,
            }
        alloc = sum(b["allocation_effect"] for b in breakdown.values())
        select = sum(b["selection_effect"] for b in breakdown.values())
        return AttributionResult(alloc, select, alloc + select, breakdown)

    def rebalance(self) -> Dict:
        """Run full portfolio rebalance and return summary."""
        log.info("Starting portfolio rebalance")
        returns_by_strategy = self.load_strategy_returns()
        hrp_weights = self.compute_hrp_weights(returns_by_strategy)
        log.info(f"HRP weights (raw): {hrp_weights}")
        target_weights = self.apply_constraints(hrp_weights)
        log.info(f"Target weights (constrained): {target_weights}")

        # Load current allocation
        allocation_file = self.data_dir / "portfolio_allocation.json"
        current_weights = {s: 0.0 for s in self.strategies}
        if allocation_file.exists():
            try:
                with open(allocation_file, "r") as f:
                    current_weights = json.load(f).get("weights", current_weights)
            except Exception as e:
                log.warning(f"Failed to load previous allocation: {e}")

        # Compute rebalancing trades
        rebalance_trades = {s: target_weights.get(s, 0.0) - current_weights.get(s, 0.0)
                           for s in self.strategies if abs(target_weights.get(s, 0.0) - current_weights.get(s, 0.0)) > 0.01}

        # Build allocation and save
        corr_matrix = self.compute_correlation_matrix(returns_by_strategy)
        total_weight = sum(target_weights.values())
        constraints_ok = all(0 <= w <= MAX_SINGLE_WEIGHT for w in target_weights.values()) and total_weight <= MAX_TOTAL_WEIGHT

        allocation = PortfolioAllocation(
            timestamp=datetime.now(timezone.utc).isoformat(),
            weights=current_weights, target_weights=target_weights,
            rebalance_trades=rebalance_trades, correlation_matrix=corr_matrix,
            total_weight=total_weight, constraints_satisfied=constraints_ok,
        )

        try:
            with open(allocation_file, "w") as f:
                json.dump(allocation.to_dict(), f, indent=2)
            log.info(f"Saved allocation to {allocation_file}")
        except Exception as e:
            log.error(f"Failed to save allocation: {e}")

        # Attribution
        benchmark_weights = {s: 1.0 / len(self.strategies) for s in self.strategies}
        latest_returns = {s: returns_by_strategy[s][-1] if returns_by_strategy[s] else 0.0 for s in self.strategies}
        attribution = self.compute_attribution(target_weights, latest_returns, benchmark_weights, latest_returns)

        log.info("Rebalance complete")
        return {
            "timestamp": allocation.timestamp, "target_weights": target_weights,
            "rebalance_trades": rebalance_trades, "total_weight": total_weight,
            "constraints_satisfied": constraints_ok, "attribution": attribution.to_dict(),
            "metrics": {
                "avg_correlation": mean([corr_matrix[s1][s2] for s1 in self.strategies for s2 in self.strategies if s1 != s2]),
                "max_weight": max(target_weights.values()) if target_weights else 0.0,
                "min_weight": min(w for w in target_weights.values() if w > 0) if any(target_weights.values()) else 0.0,
                "num_strategies": sum(1 for w in target_weights.values() if w > 0),
            }
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_portfolio_rebalance() -> Dict:
    """Run portfolio rebalance and return summary dict.

    For use in nightly pipeline.
    """
    optimizer = PortfolioOptimizer()
    return optimizer.rebalance()


if __name__ == "__main__":
    result = run_portfolio_rebalance()
    print("=" * 70)
    print("BOOK 20 — PORTFOLIO OPTIMIZER")
    print("=" * 70)
    print(f"Timestamp: {result['timestamp']}")
    print(f"Constraints satisfied: {result['constraints_satisfied']}\n")
    print("TARGET ALLOCATION:")
    for strategy, weight in sorted(result['target_weights'].items(), key=lambda x: -x[1]):
        print(f"  {strategy:15s} {weight:6.2%}")
    print(f"  {'TOTAL':15s} {result['total_weight']:6.2%}\n")
    if result['rebalance_trades']:
        print("REBALANCING TRADES:")
        for strategy, trade in sorted(result['rebalance_trades'].items(), key=lambda x: -abs(x[1])):
            print(f"  {strategy:15s} {'+' if trade > 0 else ''}{trade:6.2%}")
        print()
    print("ATTRIBUTION (Brinson-Fachler):")
    attr = result['attribution']
    print(f"  Allocation effect:  {attr['allocation_effect']:+7.4f}")
    print(f"  Selection effect:   {attr['selection_effect']:+7.4f}")
    print(f"  Total excess:       {attr['total_excess_return']:+7.4f}\n")
    print("METRICS:")
    m = result['metrics']
    print(f"  Avg correlation:    {m['avg_correlation']:6.3f}")
    print(f"  Max single weight:  {m['max_weight']:6.2%}")
    print(f"  Min single weight:  {m['min_weight']:6.2%}")
    print(f"  Active strategies:  {m['num_strategies']}\n")
    print(json.dumps(result, indent=2))
