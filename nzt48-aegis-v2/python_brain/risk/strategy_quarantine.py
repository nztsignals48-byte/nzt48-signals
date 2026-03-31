"""Book 47: Strategy Quarantine — SPRT sequential test for edge death.

Per-strategy rolling health tracking. If edge dies, quarantine (50% allocation)
then kill (zero allocation) using Sequential Probability Ratio Test.

Consumed by: bridge.py _apply_adjustments() — killed=drop signal, quarantine=50%.
State persisted to /app/data/strategy_quarantine.json.
"""

import json
import math
import os
import time
from collections import defaultdict

_STATE_PATH = "/app/data/strategy_quarantine.json"
_WINDOW = 50  # Rolling trade window for health checks
_QUARANTINE_DAYS = 30  # Max days in quarantine before kill
_SPRT_H0_WR = 0.45  # Edge alive: WR > 45%
_SPRT_H1_WR = 0.35  # Edge dead: WR < 35%
_SPRT_ALPHA = 0.05  # Type I error (false kill)
_SPRT_BETA = 0.05   # Type II error (miss dead edge)


def _sprt_boundaries():
    """Compute SPRT decision boundaries A (accept H0) and B (accept H1)."""
    A = math.log((1 - _SPRT_BETA) / _SPRT_ALPHA)   # ~2.944
    B = math.log(_SPRT_BETA / (1 - _SPRT_ALPHA))    # ~-2.944
    return A, B


def _sprt_log_lr(win, p0=_SPRT_H0_WR, p1=_SPRT_H1_WR):
    """Log-likelihood ratio for a single observation."""
    if win:
        return math.log(p1 / p0) if p0 > 0 and p1 > 0 else 0
    else:
        return math.log((1 - p1) / (1 - p0)) if p0 < 1 and p1 < 1 else 0


class StrategyQuarantine:
    """Per-strategy health tracker with SPRT edge-death test."""

    def __init__(self):
        self._trades = defaultdict(list)  # strategy -> list of (timestamp, pnl)
        self._status = {}  # strategy -> "live" | "quarantine" | "killed"
        self._quarantine_start = {}  # strategy -> timestamp
        self._sprt_sum = defaultdict(float)  # strategy -> cumulative SPRT log-LR
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        if not os.path.exists(_STATE_PATH):
            return
        try:
            with open(_STATE_PATH) as f:
                data = json.load(f)
            self._status = data.get("status", {})
            self._quarantine_start = {k: float(v) for k, v in data.get("quarantine_start", {}).items()}
            self._sprt_sum = defaultdict(float, {k: float(v) for k, v in data.get("sprt_sum", {}).items()})
            # Trades not persisted (rebuilt from daily WAL)
        except Exception:
            pass

    def update(self, strategy, pnl):
        """Feed a trade outcome. Updates health tracking and SPRT test."""
        self._ensure_loaded()
        now = time.time()
        self._trades[strategy].append((now, pnl))
        # Keep rolling window
        if len(self._trades[strategy]) > _WINDOW:
            self._trades[strategy] = self._trades[strategy][-_WINDOW:]

        # SPRT sequential test
        win = pnl > 0
        self._sprt_sum[strategy] += _sprt_log_lr(win)
        A, B = _sprt_boundaries()

        if self._sprt_sum[strategy] <= B:
            # Accept H1: edge is dead
            if self._status.get(strategy) == "quarantine":
                self._status[strategy] = "killed"
            elif self._status.get(strategy) != "killed":
                self._status[strategy] = "quarantine"
                self._quarantine_start[strategy] = now
        elif self._sprt_sum[strategy] >= A:
            # Accept H0: edge is alive — reset
            if self._status.get(strategy) in ("quarantine",):
                self._status[strategy] = "live"
                self._quarantine_start.pop(strategy, None)
            self._sprt_sum[strategy] = 0  # Reset for next test

        # Quarantine timeout: 30 days → kill
        if self._status.get(strategy) == "quarantine":
            start = self._quarantine_start.get(strategy, now)
            if now - start > _QUARANTINE_DAYS * 86400:
                self._status[strategy] = "killed"

        # Rolling stats check (supplement SPRT)
        trades = self._trades[strategy]
        if len(trades) >= 30:
            wins = sum(1 for _, p in trades if p > 0)
            wr = wins / len(trades)
            rets = [p for _, p in trades]
            mean_r = sum(rets) / len(rets)
            var_r = sum((r - mean_r) ** 2 for r in rets) / len(rets)
            std_r = var_r ** 0.5
            sharpe = (mean_r / std_r * (252 ** 0.5)) if std_r > 1e-9 else 0

            if sharpe < -0.5 and wr < 0.35 and self._status.get(strategy) != "killed":
                self._status[strategy] = "quarantine"
                if strategy not in self._quarantine_start:
                    self._quarantine_start[strategy] = now

    def get_status(self, strategy):
        """Get current status: 'live', 'quarantine', or 'killed'."""
        self._ensure_loaded()
        return self._status.get(strategy, "live")

    def get_allocation_scale(self, strategy):
        """Get allocation scaling factor. live=1.0, quarantine=0.5, killed=0.0."""
        status = self.get_status(strategy)
        if status == "killed":
            return 0.0
        if status == "quarantine":
            return 0.5
        return 1.0

    def save(self):
        """Persist state to disk."""
        try:
            os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
            data = {
                "status": self._status,
                "quarantine_start": {k: str(v) for k, v in self._quarantine_start.items()},
                "sprt_sum": {k: round(v, 4) for k, v in self._sprt_sum.items()},
                "updated": time.time(),
            }
            with open(_STATE_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass


_instance = None


def get_quarantine():
    """Singleton accessor."""
    global _instance
    if _instance is None:
        _instance = StrategyQuarantine()
    return _instance
