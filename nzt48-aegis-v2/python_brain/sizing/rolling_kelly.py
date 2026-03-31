"""Book 10: Rolling Kelly Estimator + Drawdown Staging.

Dynamic Kelly that adapts to recent performance using multiple time windows,
plus automatic drawdown staging that scales position sizes.

Rolling Kelly:
  - 60-day (responsive), 120-day (balanced), 250-day (stable) windows
  - Final Kelly = weighted average of all windows with sufficient data
  - Bayesian prior: starts at conservative 0.02 Kelly, converges to empirical

Drawdown Staging:
  - STEADY: dd < 2%, full allocation
  - CAUTION: 2% <= dd < 5%, 75% allocation, raise confidence floor
  - REDUCE: 5% <= dd < 8%, 50% allocation, tighter stops
  - FLATTEN: dd >= 8%, 25% allocation, exit only (SACRED LIMIT)

Wired into bridge.py _apply_adjustments().
"""

from __future__ import annotations

import json
import math
import os
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

DATA_DIR = os.environ.get("AEGIS_DATA_DIR", "/app/data")
ROLLING_KELLY_FILE = os.path.join(DATA_DIR, "rolling_kelly_state.json")


class DrawdownStage(str, Enum):
    STEADY = "STEADY"
    CAUTION = "CAUTION"
    REDUCE = "REDUCE"
    FLATTEN = "FLATTEN"


# Stage thresholds and parameters
STAGE_CONFIG = {
    DrawdownStage.STEADY: {
        "dd_threshold": 0.0,
        "kelly_scale": 1.0,
        "confidence_floor_add": 0,
        "description": "Normal operation",
    },
    DrawdownStage.CAUTION: {
        "dd_threshold": 0.02,
        "kelly_scale": 0.75,
        "confidence_floor_add": 5,
        "description": "Early warning — reduce risk",
    },
    DrawdownStage.REDUCE: {
        "dd_threshold": 0.05,
        "kelly_scale": 0.50,
        "confidence_floor_add": 10,
        "description": "Significant drawdown — half position sizes",
    },
    DrawdownStage.FLATTEN: {
        "dd_threshold": 0.08,
        "kelly_scale": 0.25,
        "confidence_floor_add": 20,
        "description": "SACRED LIMIT — quarter positions, exit only",
    },
}


@dataclass
class RollingKellyResult:
    """Output from rolling Kelly estimation."""
    kelly_60d: float = 0.0
    kelly_120d: float = 0.0
    kelly_250d: float = 0.0
    kelly_blended: float = 0.0
    n_trades_60d: int = 0
    n_trades_120d: int = 0
    n_trades_250d: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0


class RollingKellyEstimator:
    """Multi-window Kelly fraction estimator with Bayesian prior.

    Maintains trade history and computes Kelly across 60/120/250-day windows.
    Uses Bayesian shrinkage toward prior (Kelly=0.02) for low sample counts.
    """

    PRIOR_KELLY = 0.02  # Conservative prior
    PRIOR_WEIGHT = 20   # Equivalent to 20 trades of prior experience
    WINDOWS = [60, 120, 250]
    WINDOW_WEIGHTS = [0.5, 0.3, 0.2]  # More weight to responsive window

    def __init__(self):
        self._trade_returns: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
        # Each entry: (timestamp_days, return)

    def record_trade(self, strategy: str, ret: float, timestamp_days: Optional[float] = None):
        """Record a completed trade return."""
        if timestamp_days is None:
            timestamp_days = time.time() / 86400
        self._trade_returns[strategy].append((timestamp_days, ret))
        # Trim to 300 trades max per strategy
        if len(self._trade_returns[strategy]) > 300:
            self._trade_returns[strategy] = self._trade_returns[strategy][-300:]

    def kelly_for_strategy(self, strategy: str) -> RollingKellyResult:
        """Compute rolling Kelly for a specific strategy."""
        trades = self._trade_returns.get(strategy, [])
        if not trades:
            return RollingKellyResult(kelly_blended=self.PRIOR_KELLY)

        now_days = time.time() / 86400
        result = RollingKellyResult()

        kellys = []
        weights = []

        for window, w in zip(self.WINDOWS, self.WINDOW_WEIGHTS):
            cutoff = now_days - window
            window_returns = [r for t, r in trades if t >= cutoff]

            n = len(window_returns)
            if window == 60:
                result.n_trades_60d = n
            elif window == 120:
                result.n_trades_120d = n
            else:
                result.n_trades_250d = n

            if n < 5:
                kellys.append(self.PRIOR_KELLY)
                weights.append(w * 0.5)  # Halve weight for insufficient data
                continue

            k = self._compute_kelly(window_returns, n)

            if window == 60:
                result.kelly_60d = k
            elif window == 120:
                result.kelly_120d = k
            else:
                result.kelly_250d = k

            kellys.append(k)
            weights.append(w)

        # Weighted average
        total_weight = sum(weights)
        if total_weight > 0:
            result.kelly_blended = sum(k * w for k, w in zip(kellys, weights)) / total_weight
        else:
            result.kelly_blended = self.PRIOR_KELLY

        # Compute global stats for reporting
        all_returns = [r for _, r in trades]
        if all_returns:
            wins = [r for r in all_returns if r > 0]
            losses = [r for r in all_returns if r <= 0]
            result.win_rate = len(wins) / len(all_returns)
            result.avg_win = sum(wins) / len(wins) if wins else 0.0
            result.avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0

        return result

    def kelly_blended(self, strategy: str) -> float:
        """Quick accessor for the blended Kelly fraction."""
        return self.kelly_for_strategy(strategy).kelly_blended

    def _compute_kelly(self, returns: List[float], n: int) -> float:
        """Compute Kelly with Bayesian shrinkage toward prior."""
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]

        if not wins or not losses:
            return self.PRIOR_KELLY

        wr = len(wins) / n
        avg_win = sum(wins) / len(wins)
        avg_loss = abs(sum(losses) / len(losses))

        if avg_loss <= 0:
            return self.PRIOR_KELLY

        payoff = avg_win / avg_loss
        # Kelly: f* = (p*b - q) / b
        raw_kelly = (wr * payoff - (1 - wr)) / payoff
        # Half-Kelly for safety
        raw_kelly *= 0.5
        # Clamp
        raw_kelly = max(0.0, min(raw_kelly, 0.35))

        # Bayesian shrinkage: blend with prior based on sample size
        shrinkage = self.PRIOR_WEIGHT / (self.PRIOR_WEIGHT + n)
        kelly = shrinkage * self.PRIOR_KELLY + (1 - shrinkage) * raw_kelly

        return round(kelly, 6)

    def save(self):
        """Persist state to disk."""
        try:
            state = {}
            for strat, trades in self._trade_returns.items():
                state[strat] = [(t, r) for t, r in trades[-250:]]
            os.makedirs(os.path.dirname(ROLLING_KELLY_FILE), exist_ok=True)
            with open(ROLLING_KELLY_FILE, "w") as f:
                json.dump(state, f)
        except Exception:
            pass

    def load(self):
        """Load state from disk."""
        if not os.path.exists(ROLLING_KELLY_FILE):
            return
        try:
            with open(ROLLING_KELLY_FILE) as f:
                state = json.load(f)
            for strat, trades in state.items():
                self._trade_returns[strat] = [(t, r) for t, r in trades]
        except Exception:
            pass


# ─── Drawdown Stager ─────────────────────────────────────────────────────────

class DrawdownStager:
    """4-stage drawdown response system.

    Automatically determines the current stage based on peak-to-trough
    drawdown and returns scaling factors for Kelly and confidence floor.
    """

    def __init__(self):
        self._peak_equity: float = 0.0
        self._current_equity: float = 0.0
        self._stage_history: List[Tuple[float, str]] = []

    def update(self, equity: float):
        """Update with current equity."""
        self._current_equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

    @property
    def drawdown_pct(self) -> float:
        if self._peak_equity <= 0:
            return 0.0
        return (self._peak_equity - self._current_equity) / self._peak_equity

    @property
    def stage(self) -> DrawdownStage:
        dd = self.drawdown_pct
        if dd >= 0.08:
            return DrawdownStage.FLATTEN
        elif dd >= 0.05:
            return DrawdownStage.REDUCE
        elif dd >= 0.02:
            return DrawdownStage.CAUTION
        return DrawdownStage.STEADY

    def kelly_scale(self) -> float:
        """Returns multiplier for Kelly fraction based on current stage."""
        return STAGE_CONFIG[self.stage]["kelly_scale"]

    def confidence_floor_add(self) -> int:
        """Additional confidence floor to add based on drawdown severity."""
        return STAGE_CONFIG[self.stage]["confidence_floor_add"]

    def should_block_new_entries(self) -> bool:
        """Returns True if we're in FLATTEN stage (exit-only mode)."""
        return self.stage == DrawdownStage.FLATTEN

    def status(self) -> Dict:
        return {
            "stage": self.stage.value,
            "drawdown_pct": round(self.drawdown_pct * 100, 2),
            "kelly_scale": self.kelly_scale(),
            "confidence_floor_add": self.confidence_floor_add(),
            "peak_equity": round(self._peak_equity, 2),
            "current_equity": round(self._current_equity, 2),
        }


# ─── Singletons ──────────────────────────────────────────────────────────────

_kelly_estimator: Optional[RollingKellyEstimator] = None
_dd_stager: Optional[DrawdownStager] = None


def get_rolling_kelly() -> RollingKellyEstimator:
    global _kelly_estimator
    if _kelly_estimator is None:
        _kelly_estimator = RollingKellyEstimator()
        _kelly_estimator.load()
    return _kelly_estimator


def get_drawdown_stager() -> DrawdownStager:
    global _dd_stager
    if _dd_stager is None:
        _dd_stager = DrawdownStager()
    return _dd_stager


if __name__ == "__main__":
    # Smoke test
    rk = RollingKellyEstimator()
    import random
    random.seed(42)
    now = time.time() / 86400
    for i in range(100):
        ret = random.gauss(0.003, 0.015)
        rk.record_trade("VanguardSniper", ret, now - 100 + i)
    result = rk.kelly_for_strategy("VanguardSniper")
    print(f"Rolling Kelly: blended={result.kelly_blended:.4f}, "
          f"60d={result.kelly_60d:.4f}, 120d={result.kelly_120d:.4f}, "
          f"WR={result.win_rate:.1%}")

    ds = DrawdownStager()
    ds.update(10000)
    ds.update(9600)
    print(f"Drawdown stage: {ds.status()}")
