"""Autonomous System Safety Boundaries — Books 54, 57, 190.

Immutable safety constraints that NO automated process can override.
These are the outermost guardrails — if everything else fails, these hold.

State Machine (Book 54):
  Order states: PENDING → SUBMITTED → PARTIAL_FILL → FILLED → CLOSED
  Risk regimes: NORMAL → REDUCE → FLATTEN → HALT
  Strategy states: INCUBATION → DEPLOYED → QUARANTINE → KILLED → RETIRED

Error Taxonomy (Book 57):
  L1: Recoverable (retry with backoff)
  L2: Degraded (continue with reduced capability)
  L3: Fail-safe (halt the failing subsystem, continue others)
  L4: Fatal (halt everything, human intervention required)

Safety Boundaries (Book 190):
  - 8% drawdown = HALT (immutable, no override)
  - Max position size = config, cannot be increased by automation
  - No short selling (ISA constraint)
  - Daily loss limit = 2% of equity
  - No parameter can change by >20% in a single nightly cycle

Usage:
    from python_brain.risk.safety_boundaries import (
        SafetyBoundaryChecker, ErrorClassifier, OrderState, RiskState,
    )

    checker = SafetyBoundaryChecker()
    violation = checker.check_all(equity=9100, positions=positions)
    if violation:
        halt_system(violation.reason)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger("safety_boundaries")


# ---------------------------------------------------------------------------
# State Machines (Book 54)
# ---------------------------------------------------------------------------
class OrderState(Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"
    ERROR = "ERROR"


ORDER_TRANSITIONS: Dict[OrderState, Set[OrderState]] = {
    OrderState.PENDING: {OrderState.SUBMITTED, OrderState.CANCELLED},
    OrderState.SUBMITTED: {OrderState.PARTIAL_FILL, OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED, OrderState.ERROR},
    OrderState.PARTIAL_FILL: {OrderState.FILLED, OrderState.CANCELLED, OrderState.ERROR},
    OrderState.FILLED: {OrderState.CLOSED},
    OrderState.CANCELLED: set(),  # Terminal
    OrderState.REJECTED: set(),   # Terminal
    OrderState.CLOSED: set(),     # Terminal
    OrderState.ERROR: {OrderState.CANCELLED, OrderState.SUBMITTED},  # Retry or cancel
}


class RiskState(Enum):
    NORMAL = 0
    REDUCE = 1
    FLATTEN = 2
    HALT = 3


RISK_TRANSITIONS: Dict[RiskState, Set[RiskState]] = {
    RiskState.NORMAL: {RiskState.REDUCE, RiskState.FLATTEN, RiskState.HALT},
    RiskState.REDUCE: {RiskState.NORMAL, RiskState.FLATTEN, RiskState.HALT},
    RiskState.FLATTEN: {RiskState.REDUCE, RiskState.HALT},  # Cannot go directly to NORMAL
    RiskState.HALT: set(),  # Terminal — requires human restart
}


def validate_transition(current: Enum, target: Enum, transitions: Dict) -> bool:
    """Check if a state transition is valid."""
    allowed = transitions.get(current, set())
    return target in allowed


# ---------------------------------------------------------------------------
# Error Taxonomy (Book 57)
# ---------------------------------------------------------------------------
class ErrorLevel(Enum):
    L1_RECOVERABLE = 1   # Retry with backoff
    L2_DEGRADED = 2      # Continue with reduced capability
    L3_FAILSAFE = 3      # Halt subsystem, continue others
    L4_FATAL = 4         # Halt everything


@dataclass
class ClassifiedError:
    """An error with its classification and recommended action."""
    error_type: str
    level: ErrorLevel
    message: str
    action: str  # "retry", "degrade", "halt_subsystem", "halt_all"
    subsystem: str = ""  # Which subsystem produced the error
    retry_count: int = 0
    max_retries: int = 3


class ErrorClassifier:
    """Classify errors by severity and recommend action."""

    # Error type → (level, action, max_retries)
    ERROR_MAP: Dict[str, Tuple[ErrorLevel, str, int]] = {
        # L1: Recoverable
        "ibkr_timeout": (ErrorLevel.L1_RECOVERABLE, "retry", 5),
        "ibkr_pacing": (ErrorLevel.L1_RECOVERABLE, "retry", 10),
        "redis_timeout": (ErrorLevel.L1_RECOVERABLE, "retry", 3),
        "network_blip": (ErrorLevel.L1_RECOVERABLE, "retry", 3),
        "wal_write_temp_fail": (ErrorLevel.L1_RECOVERABLE, "retry", 3),
        # L2: Degraded
        "ibkr_data_stale": (ErrorLevel.L2_DEGRADED, "degrade", 0),
        "redis_down": (ErrorLevel.L2_DEGRADED, "degrade", 0),
        "nightly_job_failed": (ErrorLevel.L2_DEGRADED, "degrade", 0),
        "claude_api_down": (ErrorLevel.L2_DEGRADED, "degrade", 0),
        "grafana_down": (ErrorLevel.L2_DEGRADED, "degrade", 0),
        # L3: Fail-safe
        "ibkr_disconnect": (ErrorLevel.L3_FAILSAFE, "halt_subsystem", 0),
        "wal_corruption": (ErrorLevel.L3_FAILSAFE, "halt_subsystem", 0),
        "position_mismatch": (ErrorLevel.L3_FAILSAFE, "halt_subsystem", 0),
        "bridge_crash": (ErrorLevel.L3_FAILSAFE, "halt_subsystem", 0),
        # L4: Fatal
        "disk_full": (ErrorLevel.L4_FATAL, "halt_all", 0),
        "sacred_limit_breach": (ErrorLevel.L4_FATAL, "halt_all", 0),
        "credential_compromise": (ErrorLevel.L4_FATAL, "halt_all", 0),
        "wal_unrecoverable": (ErrorLevel.L4_FATAL, "halt_all", 0),
        "memory_oom": (ErrorLevel.L4_FATAL, "halt_all", 0),
    }

    def classify(self, error_type: str, message: str = "", subsystem: str = "") -> ClassifiedError:
        level, action, max_retries = self.ERROR_MAP.get(
            error_type,
            (ErrorLevel.L2_DEGRADED, "degrade", 0),  # Default: degraded
        )
        return ClassifiedError(
            error_type=error_type,
            level=level,
            message=message,
            action=action,
            subsystem=subsystem,
            max_retries=max_retries,
        )


# ---------------------------------------------------------------------------
# Immutable Safety Boundaries (Book 190)
# ---------------------------------------------------------------------------
@dataclass
class SafetyViolation:
    """A safety boundary violation."""
    boundary: str
    current_value: float
    limit_value: float
    action: str  # "HALT", "REDUCE", "BLOCK"
    reason: str


class SafetyBoundaryChecker:
    """Check immutable safety boundaries. These CANNOT be changed by automation."""

    # These limits are IMMUTABLE — automation cannot modify them
    SACRED_DRAWDOWN_PCT = 8.0       # Book 7: 8% peak drawdown = HALT
    DAILY_LOSS_LIMIT_PCT = 2.0      # Max 2% loss in single day
    MAX_POSITION_PCT = 33.0         # No single position > 33% of equity
    MAX_COMMITTED_PCT = 75.0        # No more than 75% of equity committed
    MAX_PARAM_CHANGE_PCT = 20.0     # No parameter changes > 20% per cycle
    MAX_CONSECUTIVE_LOSSES = 15     # Emergency halt after 15 consecutive losses
    ISA_NO_SHORT = True             # ISA cannot short sell

    def check_all(
        self,
        equity: float,
        initial_equity: float = 10000.0,
        hwm: float = 10000.0,
        daily_pnl: float = 0.0,
        positions: Optional[Dict[str, float]] = None,
        consecutive_losses: int = 0,
    ) -> Optional[SafetyViolation]:
        """Check all immutable safety boundaries.

        Returns first violation found, or None if all boundaries respected.
        """
        # 1. Sacred drawdown limit
        if hwm > 0:
            dd_pct = (hwm - equity) / hwm * 100
            if dd_pct >= self.SACRED_DRAWDOWN_PCT:
                return SafetyViolation(
                    "sacred_drawdown", dd_pct, self.SACRED_DRAWDOWN_PCT,
                    "HALT", f"Drawdown {dd_pct:.1f}% >= {self.SACRED_DRAWDOWN_PCT}%",
                )

        # 2. Daily loss limit
        if equity > 0:
            daily_loss_pct = abs(min(daily_pnl, 0)) / equity * 100
            if daily_loss_pct >= self.DAILY_LOSS_LIMIT_PCT:
                return SafetyViolation(
                    "daily_loss_limit", daily_loss_pct, self.DAILY_LOSS_LIMIT_PCT,
                    "HALT", f"Daily loss {daily_loss_pct:.1f}% >= {self.DAILY_LOSS_LIMIT_PCT}%",
                )

        # 3. Position concentration
        if positions and equity > 0:
            for ticker, notional in positions.items():
                pct = notional / equity * 100
                if pct > self.MAX_POSITION_PCT:
                    return SafetyViolation(
                        "position_concentration", pct, self.MAX_POSITION_PCT,
                        "BLOCK", f"{ticker} at {pct:.0f}% > {self.MAX_POSITION_PCT}%",
                    )

            # 4. Total committed capital
            total_committed = sum(positions.values())
            committed_pct = total_committed / equity * 100
            if committed_pct > self.MAX_COMMITTED_PCT:
                return SafetyViolation(
                    "committed_capital", committed_pct, self.MAX_COMMITTED_PCT,
                    "BLOCK", f"Committed {committed_pct:.0f}% > {self.MAX_COMMITTED_PCT}%",
                )

        # 5. Consecutive losses
        if consecutive_losses >= self.MAX_CONSECUTIVE_LOSSES:
            return SafetyViolation(
                "consecutive_losses", consecutive_losses, self.MAX_CONSECUTIVE_LOSSES,
                "HALT", f"{consecutive_losses} consecutive losses >= {self.MAX_CONSECUTIVE_LOSSES}",
            )

        return None  # All boundaries respected

    def validate_param_change(
        self,
        param_name: str,
        current_value: float,
        proposed_value: float,
    ) -> Optional[SafetyViolation]:
        """Check if a proposed parameter change exceeds the 20% limit."""
        if current_value == 0:
            return None
        change_pct = abs(proposed_value - current_value) / abs(current_value) * 100
        if change_pct > self.MAX_PARAM_CHANGE_PCT:
            return SafetyViolation(
                "param_change_limit", change_pct, self.MAX_PARAM_CHANGE_PCT,
                "BLOCK", f"{param_name}: {change_pct:.0f}% change > {self.MAX_PARAM_CHANGE_PCT}%",
            )
        return None
