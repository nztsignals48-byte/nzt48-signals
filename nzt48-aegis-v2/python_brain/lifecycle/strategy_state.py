"""Strategy Lifecycle State Machine — Books 47, 141, 189.

9-stage lifecycle with automated alpha decay detection and SPRT
(Sequential Probability Ratio Test) for edge validation.

States:
  DISCOVERY → DEVELOPMENT → VALIDATION → PAPER_TRADING →
  PROMOTION_REVIEW → LIVE → UNDER_REVIEW → DEMOTED → RETIRED

Each strategy lives in exactly one state. Transitions are gated
by quantitative criteria (not operator judgment alone).

Key metrics per strategy:
  - SPRT: Ongoing sequential test for edge existence
  - Rolling Sharpe: 30/60/90-day windows
  - Alpha decay: IC trend over time
  - Win rate trend: 7d vs 30d comparison
  - Cannibalization: Signal overlap with other strategies

Usage:
    from python_brain.lifecycle.strategy_state import (
        StrategyLifecycle, LifecycleState,
    )

    lifecycle = StrategyLifecycle("TypeF")
    lifecycle.record_trade(pnl=15.0, won=True)
    lifecycle.evaluate()  # May trigger state transition
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional, Tuple

log = logging.getLogger("strategy_lifecycle")


class LifecycleState(Enum):
    DISCOVERY = "DISCOVERY"
    DEVELOPMENT = "DEVELOPMENT"
    VALIDATION = "VALIDATION"
    PAPER_TRADING = "PAPER_TRADING"
    PROMOTION_REVIEW = "PROMOTION_REVIEW"
    LIVE = "LIVE"
    UNDER_REVIEW = "UNDER_REVIEW"
    DEMOTED = "DEMOTED"
    RETIRED = "RETIRED"


# Valid state transitions
VALID_TRANSITIONS: Dict[LifecycleState, List[LifecycleState]] = {
    LifecycleState.DISCOVERY: [LifecycleState.DEVELOPMENT, LifecycleState.RETIRED],
    LifecycleState.DEVELOPMENT: [LifecycleState.VALIDATION, LifecycleState.RETIRED],
    LifecycleState.VALIDATION: [LifecycleState.PAPER_TRADING, LifecycleState.DEVELOPMENT, LifecycleState.RETIRED],
    LifecycleState.PAPER_TRADING: [LifecycleState.PROMOTION_REVIEW, LifecycleState.UNDER_REVIEW, LifecycleState.RETIRED],
    LifecycleState.PROMOTION_REVIEW: [LifecycleState.LIVE, LifecycleState.PAPER_TRADING],
    LifecycleState.LIVE: [LifecycleState.UNDER_REVIEW, LifecycleState.DEMOTED],
    LifecycleState.UNDER_REVIEW: [LifecycleState.LIVE, LifecycleState.DEMOTED, LifecycleState.RETIRED],
    LifecycleState.DEMOTED: [LifecycleState.UNDER_REVIEW, LifecycleState.RETIRED],
    LifecycleState.RETIRED: [],  # Terminal state
}


@dataclass
class SPRTState:
    """Sequential Probability Ratio Test for ongoing edge validation.

    H0: Strategy has edge (WR >= p0)
    H1: Strategy has no edge (WR <= p1)

    After each trade, update the likelihood ratio. When it crosses
    the upper bound → accept H0 (edge exists). When it crosses the
    lower bound → accept H1 (no edge, kill strategy).
    """
    p0: float = 0.55  # Edge hypothesis: WR >= 55%
    p1: float = 0.45  # No-edge hypothesis: WR <= 45%
    alpha: float = 0.05  # Type I error (false positive: say edge exists when it doesn't)
    beta: float = 0.10   # Type II error (false negative: miss real edge)
    log_lr: float = 0.0  # Log likelihood ratio (cumulative)

    @property
    def upper_bound(self) -> float:
        """Accept H0 (edge exists) when log_lr > this."""
        return math.log((1 - self.beta) / self.alpha)

    @property
    def lower_bound(self) -> float:
        """Accept H1 (no edge) when log_lr < this."""
        return math.log(self.beta / (1 - self.alpha))

    def update(self, won: bool) -> str:
        """Update SPRT with new trade outcome.

        Returns: "continue", "edge_confirmed", or "edge_dead"
        """
        if won:
            lr_increment = math.log(self.p0 / max(self.p1, 1e-10))
        else:
            lr_increment = math.log((1 - self.p0) / max(1 - self.p1, 1e-10))

        self.log_lr += lr_increment

        if self.log_lr >= self.upper_bound:
            return "edge_confirmed"
        elif self.log_lr <= self.lower_bound:
            return "edge_dead"
        return "continue"

    def reset(self):
        self.log_lr = 0.0


class StrategyLifecycle:
    """Lifecycle manager for a single strategy."""

    def __init__(self, name: str, initial_state: LifecycleState = LifecycleState.LIVE):
        self.name = name
        self._state = initial_state
        self._sprt = SPRTState()
        self._trade_count = 0
        self._wins = 0
        self._pnl_history: Deque[float] = deque(maxlen=200)
        self._rolling_sharpes: Dict[int, float] = {}  # window → sharpe
        self._consecutive_losses = 0

    @property
    def state(self) -> LifecycleState:
        return self._state

    @property
    def win_rate(self) -> float:
        return self._wins / max(self._trade_count, 1)

    @property
    def trade_count(self) -> int:
        return self._trade_count

    def can_trade(self) -> bool:
        """Whether this strategy is allowed to generate live signals."""
        return self._state in (
            LifecycleState.LIVE,
            LifecycleState.PAPER_TRADING,
            LifecycleState.UNDER_REVIEW,  # Still trades but at reduced size
        )

    def record_trade(self, pnl: float, won: bool):
        """Record a completed trade."""
        self._trade_count += 1
        self._pnl_history.append(pnl)
        if won:
            self._wins += 1
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1

        # Update SPRT
        sprt_result = self._sprt.update(won)
        if sprt_result == "edge_dead":
            log.warning("SPRT: %s edge DEAD (log_lr=%.2f)", self.name, self._sprt.log_lr)
        elif sprt_result == "edge_confirmed":
            log.info("SPRT: %s edge CONFIRMED (log_lr=%.2f)", self.name, self._sprt.log_lr)

        # Update rolling Sharpes
        self._update_rolling_sharpes()

    def _update_rolling_sharpes(self):
        """Compute rolling Sharpe ratios at multiple windows."""
        import numpy as np
        returns = list(self._pnl_history)
        for window in (30, 60, 90):
            if len(returns) >= window:
                recent = np.array(returns[-window:])
                mean = np.mean(recent)
                std = np.std(recent, ddof=1)
                self._rolling_sharpes[window] = float(mean / std * math.sqrt(252)) if std > 0 else 0.0

    def evaluate(self) -> Optional[LifecycleState]:
        """Evaluate whether a state transition is warranted.

        Returns new state if transition occurred, None otherwise.
        """
        old_state = self._state

        # Hard kill criteria (Book 47)
        if self._should_kill():
            self._transition(LifecycleState.RETIRED)
            return self._state if self._state != old_state else None

        # Quarantine criteria
        if self._should_quarantine():
            if self._state == LifecycleState.LIVE:
                self._transition(LifecycleState.UNDER_REVIEW)
            return self._state if self._state != old_state else None

        # Recovery from quarantine
        if self._state == LifecycleState.UNDER_REVIEW and self._should_recover():
            self._transition(LifecycleState.LIVE)
            return self._state if self._state != old_state else None

        return None

    def _should_kill(self) -> bool:
        """Hard kill criteria (Book 47)."""
        # 90-day Sharpe < -0.5
        if self._rolling_sharpes.get(90, 0) < -0.5 and self._trade_count >= 30:
            log.warning("KILL: %s 90d Sharpe=%.2f", self.name, self._rolling_sharpes[90])
            return True

        # SPRT says no edge with statistical confidence
        if self._sprt.log_lr <= self._sprt.lower_bound and self._trade_count >= 20:
            log.warning("KILL: %s SPRT edge_dead", self.name)
            return True

        # 15 consecutive losses
        if self._consecutive_losses >= 15:
            log.warning("KILL: %s 15 consecutive losses", self.name)
            return True

        return False

    def _should_quarantine(self) -> bool:
        """Quarantine criteria (Book 47)."""
        # 30-day Sharpe < 0
        if self._rolling_sharpes.get(30, 0) < 0 and self._trade_count >= 15:
            return True

        # 8 consecutive losses
        if self._consecutive_losses >= 8:
            return True

        # Win rate declining: 7d WR < 30d WR by >10pp
        if len(self._pnl_history) >= 30:
            recent_7 = list(self._pnl_history)[-7:]
            recent_30 = list(self._pnl_history)[-30:]
            wr_7 = sum(1 for p in recent_7 if p > 0) / max(len(recent_7), 1)
            wr_30 = sum(1 for p in recent_30 if p > 0) / max(len(recent_30), 1)
            if wr_30 - wr_7 > 0.10:
                return True

        return False

    def _should_recover(self) -> bool:
        """Recovery criteria from quarantine."""
        # 30-day Sharpe > 0.3 and at least 10 new trades
        if self._rolling_sharpes.get(30, 0) > 0.3 and self._trade_count >= 10:
            return True
        return False

    def _transition(self, new_state: LifecycleState):
        """Execute state transition with validation."""
        if new_state in VALID_TRANSITIONS.get(self._state, []):
            log.info(
                "LIFECYCLE: %s %s → %s (trades=%d, WR=%.1f%%, SPRT=%.2f)",
                self.name, self._state.value, new_state.value,
                self._trade_count, self.win_rate * 100, self._sprt.log_lr,
            )
            self._state = new_state
        else:
            log.warning(
                "LIFECYCLE: Invalid transition %s → %s for %s",
                self._state.value, new_state.value, self.name,
            )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self._state.value,
            "trade_count": self._trade_count,
            "win_rate": round(self.win_rate, 3),
            "consecutive_losses": self._consecutive_losses,
            "sprt_log_lr": round(self._sprt.log_lr, 3),
            "rolling_sharpes": {str(k): round(v, 3) for k, v in self._rolling_sharpes.items()},
            "can_trade": self.can_trade(),
        }


# ---------------------------------------------------------------------------
# Strategy Cannibalization Detection (Book 98)
# ---------------------------------------------------------------------------
def detect_cannibalization(
    strategy_signals: Dict[str, List[Tuple[str, int]]],
    window_secs: int = 300,
) -> Dict[Tuple[str, str], float]:
    """Detect signal overlap between strategy pairs.

    Args:
        strategy_signals: {strategy_name: [(ticker, timestamp_ns), ...]}
        window_secs: Overlap window in seconds

    Returns:
        {(strategy_A, strategy_B): overlap_rate} where overlap_rate > 0.3 = problematic
    """
    overlap_rates: Dict[Tuple[str, str], float] = {}
    strategies = list(strategy_signals.keys())

    for i, s_a in enumerate(strategies):
        for s_b in strategies[i + 1:]:
            signals_a = set((t, ts // (window_secs * 1_000_000_000)) for t, ts in strategy_signals[s_a])
            signals_b = set((t, ts // (window_secs * 1_000_000_000)) for t, ts in strategy_signals[s_b])

            intersection = signals_a & signals_b
            union = signals_a | signals_b

            sor = len(intersection) / max(len(union), 1)
            overlap_rates[(s_a, s_b)] = round(sor, 3)

            if sor > 0.3:
                log.warning(
                    "CANNIBALIZATION: %s × %s SOR=%.2f (%d/%d overlapping signals)",
                    s_a, s_b, sor, len(intersection), len(union),
                )

    return overlap_rates
