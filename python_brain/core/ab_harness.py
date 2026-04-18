"""A/B harness. Every LLM agent and every ML model reports delta here.

is_alpha_positive() returns True only if the 95% bootstrap CI of (agent_impact)
is > 0 after N >= min_samples observations, stratified by regime.
"""
from __future__ import annotations

import random
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple


@dataclass
class AgentABHarness:
    agent_name: str
    min_samples: int = 200
    # (strategy_default, llm_output, realized_pnl_bps, regime_label)
    samples: Deque[Tuple[float, float, float, str]] = field(default_factory=lambda: deque(maxlen=2000))

    def record(self, strategy_default: float, llm_output: float, realized_pnl: float, regime: str = "steady") -> None:
        self.samples.append((strategy_default, llm_output, realized_pnl, regime))

    def can_report_delta(self) -> bool:
        return len(self.samples) >= self.min_samples

    def delta_with_ci(self, bootstrap_n: int = 2000) -> Optional[Tuple[float, float, float]]:
        if not self.can_report_delta():
            return None
        deltas = [r for (_, _, r, _) in self.samples]
        n = len(deltas)
        means: List[float] = []
        for _ in range(bootstrap_n):
            idx = [random.randrange(n) for _ in range(n)]
            means.append(statistics.mean(deltas[i] for i in idx))
        means.sort()
        mean = statistics.mean(deltas)
        lo = means[int(bootstrap_n * 0.025)]
        hi = means[int(bootstrap_n * 0.975)]
        return (mean, lo, hi)

    def is_alpha_positive(self) -> bool:
        ci = self.delta_with_ci()
        if ci is None:
            return False
        _, lo, _ = ci
        return lo > 0.0

    def by_regime(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for (_, _, _, r) in self.samples:
            counts[r] = counts.get(r, 0) + 1
        return counts
