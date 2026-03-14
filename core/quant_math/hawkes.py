"""
Hawkes Process -- Self-Exciting Cascade Detection.
Bacry, Iuga, Lasnier & Lehalle (2015).
"""
from __future__ import annotations
import numpy as np
import time
import logging

logger = logging.getLogger("nzt48.hawkes")


class HawkesMicrostructureMonitor:
    """Models self-exciting volatility cascades.
    Freezes the system during micro-flash crashes.
    """
    def __init__(self, baseline: float = 0.1, alpha: float = 0.8, beta: float = 1.2):
        self.mu = baseline
        self.alpha = alpha
        self.beta = beta
        self.events: list[float] = []

    def add_toxic_event(self, timestamp: float | None = None) -> None:
        self.events.append(timestamp or time.time())

    def current_intensity(self, now: float | None = None) -> float:
        """lambda(t) = mu + alpha * sum(exp(-beta * (t - t_i)))"""
        now = now or time.time()
        recent = [t for t in self.events if (now - t) < 60.0]
        self.events = recent
        if not recent:
            return self.mu

        arr = np.array(recent)
        excitation = np.sum(np.exp(-self.beta * (now - arr)))
        return float(self.mu + self.alpha * excitation)

    def is_cascade_active(self, threshold: float = 5.0) -> bool:
        """Returns True if current intensity exceeds threshold."""
        return self.current_intensity() > threshold

    def reset(self) -> None:
        self.events.clear()
