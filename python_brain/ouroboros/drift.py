"""ADWIN drift detection. Phase 9: real River ADWIN wrapper; scaffold uses a simple window."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import mean


@dataclass
class Adwin:
    threshold: float = 2.0
    window: deque = None

    def __post_init__(self):
        if self.window is None:
            self.window = deque(maxlen=200)

    def update(self, x: float) -> bool:
        self.window.append(x)
        if len(self.window) < 40:
            return False
        half = len(self.window) // 2
        left = list(self.window)[:half]
        right = list(self.window)[half:]
        return abs(mean(left) - mean(right)) > self.threshold
