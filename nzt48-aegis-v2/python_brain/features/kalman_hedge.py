"""Kalman Filter for Dynamic Hedge Ratios — adaptive pairs/hedging.

Uses a 1-D Kalman filter to estimate time-varying hedge ratios
between correlated instruments. Adapts to structural breaks.
Reference: Avellaneda & Lee (2010), "Statistical Arbitrage in the U.S. Equities Market"

License dependency: numpy (BSD-3), scipy (BSD-3) — both stdlib-grade.
"""

import json
import os
import time
from typing import Dict, List, Optional, Tuple


class KalmanHedgeFilter:
    """1-D Kalman filter for dynamic hedge ratio estimation.

    State: beta (hedge ratio between Y and X)
    Observation: Y_t = beta_t * X_t + epsilon_t
    Transition: beta_t = beta_{t-1} + eta_t
    """

    __slots__ = ('beta', 'P', 'Q', 'R', 'n_obs', 'spread_history')

    def __init__(self, initial_beta: float = 1.0,
                 process_noise: float = 1e-5,
                 observation_noise: float = 1e-2):
        self.beta = initial_beta
        self.P = 1.0  # State covariance
        self.Q = process_noise  # Process noise variance
        self.R = observation_noise  # Observation noise variance
        self.n_obs = 0
        self.spread_history: List[float] = []

    def update(self, y: float, x: float) -> Tuple[float, float]:
        """Update with a new observation pair.

        Args:
            y: dependent instrument price
            x: independent instrument price

        Returns:
            (hedge_ratio, spread) where spread = y - beta * x
        """
        if abs(x) < 1e-10:
            return (self.beta, 0.0)

        # Predict
        beta_pred = self.beta
        P_pred = self.P + self.Q

        # Update
        innovation = y - beta_pred * x
        S = x * P_pred * x + self.R  # Innovation variance
        K = P_pred * x / S  # Kalman gain

        self.beta = beta_pred + K * innovation
        self.P = (1.0 - K * x) * P_pred
        self.n_obs += 1

        spread = y - self.beta * x
        self.spread_history.append(spread)
        if len(self.spread_history) > 500:
            self.spread_history = self.spread_history[-500:]

        return (self.beta, spread)

    def spread_zscore(self, lookback: int = 60) -> float:
        """Compute z-score of current spread vs rolling window."""
        if len(self.spread_history) < max(lookback, 20):
            return 0.0

        window = self.spread_history[-lookback:]
        mean = sum(window) / len(window)
        var = sum((s - mean) ** 2 for s in window) / len(window)
        if var < 1e-12:
            return 0.0
        std = var ** 0.5
        return (self.spread_history[-1] - mean) / std

    def half_life(self) -> float:
        """Estimate mean-reversion half-life from spread autocorrelation."""
        if len(self.spread_history) < 30:
            return float('inf')

        spreads = self.spread_history[-100:]
        n = len(spreads)
        if n < 20:
            return float('inf')

        # OLS: spread_t = a + b * spread_{t-1}
        y_vals = spreads[1:]
        x_vals = spreads[:-1]
        n_reg = len(y_vals)
        sum_x = sum(x_vals)
        sum_y = sum(y_vals)
        sum_xy = sum(xi * yi for xi, yi in zip(x_vals, y_vals))
        sum_x2 = sum(xi ** 2 for xi in x_vals)

        denom = n_reg * sum_x2 - sum_x ** 2
        if abs(denom) < 1e-12:
            return float('inf')

        b = (n_reg * sum_xy - sum_x * sum_y) / denom

        if b >= 1.0 or b <= 0.0:
            return float('inf')

        import math
        return -math.log(2) / math.log(abs(b))


class PairsHedgeManager:
    """Manage Kalman hedge filters for multiple pairs."""

    def __init__(self):
        self._filters: Dict[str, KalmanHedgeFilter] = {}

    def get_or_create(self, pair_key: str,
                      initial_beta: float = 1.0) -> KalmanHedgeFilter:
        if pair_key not in self._filters:
            self._filters[pair_key] = KalmanHedgeFilter(initial_beta=initial_beta)
        return self._filters[pair_key]

    def update_pair(self, pair_key: str, y_price: float, x_price: float) -> Dict:
        """Update a pair and return signal info."""
        f = self.get_or_create(pair_key)
        beta, spread = f.update(y_price, x_price)
        zscore = f.spread_zscore()
        hl = f.half_life()

        return {
            "pair": pair_key,
            "hedge_ratio": round(beta, 6),
            "spread": round(spread, 6),
            "zscore": round(zscore, 3),
            "half_life": round(hl, 1) if hl != float('inf') else None,
            "n_obs": f.n_obs,
        }

    def tradeable_signals(self, zscore_threshold: float = 2.0,
                          max_half_life: float = 50.0) -> List[Dict]:
        """Return pairs with tradeable mean-reversion signals."""
        signals = []
        for key, f in self._filters.items():
            if f.n_obs < 60:
                continue
            z = f.spread_zscore()
            hl = f.half_life()
            if abs(z) >= zscore_threshold and hl < max_half_life:
                signals.append({
                    "pair": key,
                    "zscore": round(z, 3),
                    "half_life": round(hl, 1),
                    "hedge_ratio": round(f.beta, 6),
                    "direction": "Short" if z > 0 else "Long",
                    "strength": min(abs(z) / 3.0, 1.0),  # Normalized [0, 1]
                })
        return sorted(signals, key=lambda s: abs(s["zscore"]), reverse=True)

    def snapshot(self) -> Dict:
        return {
            k: {
                "beta": round(f.beta, 6),
                "n_obs": f.n_obs,
                "zscore": round(f.spread_zscore(), 3),
            }
            for k, f in self._filters.items()
            if f.n_obs >= 20
        }


# Module-level singleton
_manager: Optional[PairsHedgeManager] = None


def get_manager() -> PairsHedgeManager:
    global _manager
    if _manager is None:
        _manager = PairsHedgeManager()
    return _manager


# Alias for backward compatibility — some callers use KalmanHedgeEngine
KalmanHedgeEngine = PairsHedgeManager


def save_snapshot(data_dir: str = "/app/data") -> None:
    """Save hedge ratio snapshot to disk."""
    mgr = get_manager()
    snap = mgr.snapshot()
    if not snap:
        return
    path = os.path.join(data_dir, "kalman_hedge_snapshot.json")
    try:
        with open(path, "w") as f:
            json.dump({"timestamp": time.time(), "pairs": snap}, f)
    except Exception:
        pass
