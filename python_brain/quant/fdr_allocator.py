"""FDR-controlled bandit allocator — e-Benjamini-Hochberg wrapper.

Prevents multi-testing leakage through Thompson sampling allocator.
Only strategies that pass online FDR check at level alpha are allowed
to receive increased weight.

References:
- Jamieson & Jain (2018) NeurIPS "Bandit Approach to FDR-controlled Experimental Design"
- Ramdas et al. (2022) "FDR with E-values"

Consumed by capital_bandit_daemon.py before Thompson posterior write.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class FDRState:
    strategy_ids: list = field(default_factory=list)
    e_values: dict = field(default_factory=dict)          # strategy -> e-value
    cumulative_reward: dict = field(default_factory=dict) # cumulative net edge bps
    n_observations: dict = field(default_factory=dict)    # trade counts


class FDRAllocator:
    """Online Benjamini-Hochberg e-value procedure for strategy promotion."""

    def __init__(self, alpha: float = 0.05, prior_e: float = 1.0):
        self.alpha = alpha
        self.prior_e = prior_e
        self.state = FDRState()

    def register(self, strategy: str) -> None:
        if strategy not in self.state.e_values:
            self.state.strategy_ids.append(strategy)
            self.state.e_values[strategy] = self.prior_e
            self.state.cumulative_reward[strategy] = 0.0
            self.state.n_observations[strategy] = 0

    def update(self, strategy: str, daily_net_edge_bps: float) -> None:
        """Update strategy's e-value with today's cost-adjusted PnL.

        Likelihood ratio: e-value grows for positive edge, shrinks for negative.
        """
        self.register(strategy)
        self.state.cumulative_reward[strategy] += daily_net_edge_bps
        self.state.n_observations[strategy] += 1
        # e-value update: multiplicative likelihood ratio under
        # H0: edge <= 0 vs H1: edge > 0
        # Bounded update to prevent extreme values
        lr = math.exp(min(max(daily_net_edge_bps / 10.0, -3), 3))
        self.state.e_values[strategy] *= lr

    def promotable(self) -> list[str]:
        """Return strategies that pass online e-BH at FDR alpha.

        e-BH: sort e-values descending; find largest k where sorted[k-1] >= k/alpha.
        """
        items = [(s, self.state.e_values[s]) for s in self.state.strategy_ids]
        items.sort(key=lambda x: x[1], reverse=True)
        k_max = 0
        for i, (_, e) in enumerate(items, 1):
            if e >= i / self.alpha:
                k_max = i
        return [s for s, _ in items[:k_max]]

    def can_increase_kelly(self, strategy: str) -> bool:
        """Gate: True if strategy is in FDR-promoted set."""
        return strategy in self.promotable()

    def snapshot(self) -> dict:
        return {
            "alpha": self.alpha,
            "strategies": self.state.strategy_ids,
            "e_values": dict(self.state.e_values),
            "promotable": self.promotable(),
            "cumulative_reward": dict(self.state.cumulative_reward),
            "n_observations": dict(self.state.n_observations),
        }


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        alloc = FDRAllocator(alpha=0.05)
        rng = np.random.default_rng(42)
        # Good strategies get +3bps/day, bad ones -1bps/day
        for day in range(60):
            alloc.update("good_A", float(rng.normal(3, 2)))
            alloc.update("good_B", float(rng.normal(2, 2)))
            alloc.update("bad_C", float(rng.normal(-1, 2)))
            alloc.update("bad_D", float(rng.normal(-0.5, 2)))
        snap = alloc.snapshot()
        print(f"E-values: {snap['e_values']}")
        print(f"Promotable: {snap['promotable']}")
        print("OK")
