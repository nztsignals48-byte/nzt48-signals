"""Thompson Sampling for dark-horse exploration — 40 slots out of the top-100."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Posterior:
    alpha: float = 1.0
    beta: float = 1.0

    def sample(self) -> float:
        return random.betavariate(self.alpha, self.beta)

    def update(self, success: bool) -> None:
        if success:
            self.alpha += 1.0
        else:
            self.beta += 1.0


@dataclass
class ThompsonSampler:
    slots: int = 40
    posteriors: Dict[str, Posterior] = field(default_factory=dict)

    def pick(self, candidates: List[str]) -> List[str]:
        samples = [(c, self.posteriors.get(c, Posterior()).sample()) for c in candidates]
        samples.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in samples[: self.slots]]

    def record(self, ticker: str, success: bool) -> None:
        self.posteriors.setdefault(ticker, Posterior()).update(success)
