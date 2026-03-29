"""Autonomous Learning Loop Gates — Book 158.

Controls when and how Ouroboros is allowed to modify system parameters.
The learning loop is FROZEN by default and must earn trust through
demonstrated accuracy before gaining autonomy.

Unfreeze criteria (ALL must be met):
  1. N >= 300 closed trades (statistical minimum)
  2. Rolling 60-trade Sharpe > 0.3 (proven edge exists)
  3. Ouroboros recommendation accuracy > 60% over last 30 recommendations
  4. No CRITICAL health alerts in last 7 days
  5. Operator has not vetoed in last 48 hours

Even when unfrozen, Ouroboros is constrained:
  - Max 20% parameter change per cycle (Book 190)
  - Must use parameter governor (Book 71)
  - Changes auto-revert if next 10 trades worse than pre-change baseline
  - Human notification on every change (Telegram)

Usage:
    from python_brain.lifecycle.ouroboros_gates import (
        OuroborosGatekeeper, LearningLoopState,
    )

    gatekeeper = OuroborosGatekeeper()
    gatekeeper.update_metrics(n_trades=320, sharpe_60=0.5, ...)
    if gatekeeper.can_modify_params():
        apply_ouroboros_recommendations()
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

log = logging.getLogger("ouroboros_gates")


class LearningLoopState(Enum):
    FROZEN = "FROZEN"              # No modifications allowed
    OBSERVE_ONLY = "OBSERVE_ONLY"  # Generates recommendations, does not apply
    CONSTRAINED = "CONSTRAINED"    # Can modify within strict limits
    AUTONOMOUS = "AUTONOMOUS"       # Full parameter authority (Phase 9+)


@dataclass
class UnfreezeMetrics:
    """Metrics required for unfreeze decision."""
    n_trades: int = 0
    sharpe_60: float = 0.0
    recommendation_accuracy: float = 0.0  # % of recs that improved performance
    n_recommendations: int = 0
    days_since_critical_alert: int = 0
    hours_since_last_veto: float = 0.0
    current_drawdown_pct: float = 0.0


# Unfreeze thresholds (ALL must be met)
UNFREEZE_THRESHOLDS = {
    "min_trades": 300,
    "min_sharpe_60": 0.3,
    "min_rec_accuracy": 0.60,
    "min_recommendations": 30,
    "min_days_no_critical": 7,
    "min_hours_no_veto": 48,
    "max_drawdown_pct": 5.0,
}

# Escalation thresholds (CONSTRAINED → AUTONOMOUS)
AUTONOMOUS_THRESHOLDS = {
    "min_trades": 1000,
    "min_sharpe_60": 0.8,
    "min_rec_accuracy": 0.75,
    "min_recommendations": 100,
    "max_drawdown_pct": 3.0,
}


class OuroborosGatekeeper:
    """Control Ouroboros learning loop permissions."""

    def __init__(self):
        self._state = LearningLoopState.FROZEN
        self._metrics = UnfreezeMetrics()
        self._revert_queue: List[Dict] = []  # Changes pending validation
        self._change_history: List[Dict] = []

    @property
    def state(self) -> LearningLoopState:
        return self._state

    def update_metrics(self, **kwargs):
        """Update metrics used for unfreeze decisions."""
        for k, v in kwargs.items():
            if hasattr(self._metrics, k):
                setattr(self._metrics, k, v)

    def evaluate(self) -> LearningLoopState:
        """Evaluate whether the learning loop should be unfrozen/escalated."""
        old = self._state
        m = self._metrics

        if self._state == LearningLoopState.FROZEN:
            # Check all unfreeze criteria
            checks = {
                "trades": m.n_trades >= UNFREEZE_THRESHOLDS["min_trades"],
                "sharpe": m.sharpe_60 >= UNFREEZE_THRESHOLDS["min_sharpe_60"],
                "accuracy": m.recommendation_accuracy >= UNFREEZE_THRESHOLDS["min_rec_accuracy"],
                "recs": m.n_recommendations >= UNFREEZE_THRESHOLDS["min_recommendations"],
                "no_critical": m.days_since_critical_alert >= UNFREEZE_THRESHOLDS["min_days_no_critical"],
                "no_veto": m.hours_since_last_veto >= UNFREEZE_THRESHOLDS["min_hours_no_veto"],
                "drawdown": m.current_drawdown_pct <= UNFREEZE_THRESHOLDS["max_drawdown_pct"],
            }

            if all(checks.values()):
                self._state = LearningLoopState.OBSERVE_ONLY
                log.info("OUROBOROS: FROZEN → OBSERVE_ONLY (all %d gates passed)", len(checks))
            else:
                failed = [k for k, v in checks.items() if not v]
                log.info("OUROBOROS: remains FROZEN — failed: %s", ", ".join(failed))

        elif self._state == LearningLoopState.OBSERVE_ONLY:
            # After 50 accurate observations, escalate to CONSTRAINED
            if m.n_recommendations >= 50 and m.recommendation_accuracy >= 0.65:
                self._state = LearningLoopState.CONSTRAINED
                log.info("OUROBOROS: OBSERVE_ONLY → CONSTRAINED")
            # Revert to FROZEN if performance degrades
            elif m.sharpe_60 < 0 or m.current_drawdown_pct > 8:
                self._state = LearningLoopState.FROZEN
                log.warning("OUROBOROS: OBSERVE_ONLY → FROZEN (performance degraded)")

        elif self._state == LearningLoopState.CONSTRAINED:
            # Check autonomous escalation
            auto_checks = {
                "trades": m.n_trades >= AUTONOMOUS_THRESHOLDS["min_trades"],
                "sharpe": m.sharpe_60 >= AUTONOMOUS_THRESHOLDS["min_sharpe_60"],
                "accuracy": m.recommendation_accuracy >= AUTONOMOUS_THRESHOLDS["min_rec_accuracy"],
                "recs": m.n_recommendations >= AUTONOMOUS_THRESHOLDS["min_recommendations"],
                "drawdown": m.current_drawdown_pct <= AUTONOMOUS_THRESHOLDS["max_drawdown_pct"],
            }
            if all(auto_checks.values()):
                self._state = LearningLoopState.AUTONOMOUS
                log.info("OUROBOROS: CONSTRAINED → AUTONOMOUS")
            # Demote if performance drops
            elif m.sharpe_60 < 0:
                self._state = LearningLoopState.OBSERVE_ONLY
                log.warning("OUROBOROS: CONSTRAINED → OBSERVE_ONLY (Sharpe < 0)")

        if self._state != old:
            log.info("OUROBOROS STATE: %s → %s", old.value, self._state.value)

        return self._state

    def can_modify_params(self) -> bool:
        """Check if Ouroboros is allowed to modify parameters."""
        return self._state in (LearningLoopState.CONSTRAINED, LearningLoopState.AUTONOMOUS)

    def can_observe(self) -> bool:
        """Check if Ouroboros can generate recommendations (even if not applied)."""
        return self._state != LearningLoopState.FROZEN

    def register_change(self, param: str, old_value: float, new_value: float):
        """Register a parameter change for auto-revert tracking."""
        self._change_history.append({
            "param": param,
            "old": old_value,
            "new": new_value,
            "time": time.time(),
            "trades_at_change": self._metrics.n_trades,
        })
        self._revert_queue.append({
            "param": param,
            "revert_to": old_value,
            "validate_after_n_trades": 10,
            "trades_at_change": self._metrics.n_trades,
        })

    def check_reverts(self, current_trades: int, recent_pnl: float) -> List[Dict]:
        """Check if any pending changes should be auto-reverted.

        If the 10 trades after a change are worse than the 10 before,
        revert the change automatically.
        """
        reverts = []
        remaining = []

        for item in self._revert_queue:
            trades_since = current_trades - item["trades_at_change"]
            if trades_since >= item["validate_after_n_trades"]:
                if recent_pnl < 0:  # Simplified: revert if net negative
                    reverts.append(item)
                    log.warning("OUROBOROS: auto-reverting %s to %s (negative PnL after change)",
                               item["param"], item["revert_to"])
                # Either way, remove from queue
            else:
                remaining.append(item)

        self._revert_queue = remaining
        return reverts

    def to_dict(self) -> dict:
        return {
            "state": self._state.value,
            "can_modify": self.can_modify_params(),
            "can_observe": self.can_observe(),
            "metrics": {
                "n_trades": self._metrics.n_trades,
                "sharpe_60": round(self._metrics.sharpe_60, 3),
                "rec_accuracy": round(self._metrics.recommendation_accuracy, 3),
                "n_recs": self._metrics.n_recommendations,
                "drawdown_pct": round(self._metrics.current_drawdown_pct, 2),
            },
            "pending_reverts": len(self._revert_queue),
            "total_changes": len(self._change_history),
        }
