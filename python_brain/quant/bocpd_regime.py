"""
Bayesian Online Changepoint Detection (BOCPD) for Market Regime Detection

Adams & MacKay (2007). Detects regime shifts in real time without requiring
fixed windows. Publishes regime.current on NATS for sig2order consumption.

Regimes:
- calm: low vol, normal correlations
- trending: steady direction, medium vol
- choppy: whipsaw, high vol, no trend
- crisis: extreme vol, correlations → 1
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass
class RegimeState:
    regime: str
    probability: float
    changepoint_prob: float
    run_length: int
    volatility: float


class BocpdRegimeDetector:
    """
    Online changepoint detector with 4 regime classification.

    Uses hazard function + posterior over run lengths.
    """

    def __init__(
        self,
        hazard_rate: float = 0.01,           # 1% prior of regime change per step
        history_size: int = 200,
    ):
        self.hazard = hazard_rate
        self.history_size = history_size
        self.returns = deque(maxlen=history_size)
        self.run_length_probs = np.array([1.0])  # P(run_length = t)
        self._current_regime = "calm"

    def update(self, x: float) -> RegimeState:
        """Add observation, update regime probabilities."""
        self.returns.append(x)

        if len(self.returns) < 10:
            return RegimeState(
                regime="calm", probability=0.5, changepoint_prob=0.0,
                run_length=len(self.returns), volatility=0.0,
            )

        # Compute predictive probability under current params
        sigma = float(np.std(self.returns)) or 0.01
        pred_prob = math.exp(-x ** 2 / (2 * sigma ** 2))

        # Growth probabilities (no changepoint)
        growth = self.run_length_probs * (1 - self.hazard) * pred_prob

        # Changepoint probability
        cp = float(self.run_length_probs.sum() * self.hazard * pred_prob)

        # New run-length distribution
        new_probs = np.concatenate([[cp], growth])
        total = new_probs.sum()
        if total > 0:
            new_probs = new_probs / total
        self.run_length_probs = new_probs[:self.history_size]

        # Classify regime by recent returns + volatility
        regime = self._classify_regime()
        run_length = int(np.argmax(self.run_length_probs))

        return RegimeState(
            regime=regime,
            probability=float(self.run_length_probs[run_length]),
            changepoint_prob=float(self.run_length_probs[0]),
            run_length=run_length,
            volatility=sigma * math.sqrt(252),
        )

    def _classify_regime(self) -> str:
        """Heuristic regime classification from return history."""
        if len(self.returns) < 10:
            return "calm"

        r = np.array(list(self.returns)[-50:])
        vol = float(np.std(r))
        mean_ret = float(np.mean(r))
        # Trend strength: ratio of mean to vol
        trend = mean_ret / max(vol, 1e-6)

        if vol > 0.03:
            regime = "crisis"
        elif vol > 0.015:
            if abs(trend) > 0.5:
                regime = "trending"
            else:
                regime = "choppy"
        else:
            if abs(trend) > 0.3:
                regime = "trending"
            else:
                regime = "calm"

        self._current_regime = regime
        return regime

    def current_regime(self) -> str:
        return self._current_regime

    def size_multiplier(self) -> float:
        """Multiplier for position sizing based on current regime."""
        return {"calm": 1.0, "trending": 1.2, "choppy": 0.6, "crisis": 0.3}.get(
            self._current_regime, 1.0,
        )


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        det = BocpdRegimeDetector()
        rng = np.random.default_rng(42)

        # Calm regime
        for x in rng.normal(0, 0.005, 50):
            state = det.update(float(x))
        print(f"After calm: regime={state.regime}, vol={state.volatility:.2%}")

        # Crisis regime
        for x in rng.normal(0, 0.05, 50):
            state = det.update(float(x))
        print(f"After crisis: regime={state.regime}, vol={state.volatility:.2%}")
        print(f"Size multiplier: {det.size_multiplier()}")
        print("OK")
