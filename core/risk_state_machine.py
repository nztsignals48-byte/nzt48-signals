"""
G-04: Formal Risk State Machine.
States: NORMAL -> REDUCE -> EXIT_ONLY -> EMERGENCY_FLATTEN -> SYSTEM_HALTED
Transitions only go UP in severity. Single executor processes one risk action at a time.
Recovery back to NORMAL is explicit and gated (cannot auto-recover from SYSTEM_HALTED).
"""
import enum
import logging
import asyncio
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class RiskState(enum.IntEnum):
    NORMAL = 0
    REDUCE = 1
    EXIT_ONLY = 2
    EMERGENCY_FLATTEN = 3
    SYSTEM_HALTED = 4


class RiskStateTransition:
    """Immutable record of a state transition."""
    __slots__ = ('from_state', 'to_state', 'reason', 'timestamp')

    def __init__(
        self,
        from_state: RiskState,
        to_state: RiskState,
        reason: str,
        timestamp: datetime,
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        self.timestamp = timestamp

    def __repr__(self) -> str:
        return (
            f"RiskStateTransition({self.from_state.name} -> {self.to_state.name}, "
            f"reason={self.reason!r}, ts={self.timestamp.isoformat()})"
        )


class RiskStateMachine:
    """
    Monotonically-escalating risk state machine.

    Rules:
      - Transitions only go UP in severity (higher IntEnum value).
      - The sole exception is an explicit ``recover()`` call, which resets to NORMAL.
      - ``recover()`` is forbidden from SYSTEM_HALTED (requires human confirmation).
      - All transitions are serialized via an asyncio.Lock (single executor).
      - State is optionally persisted to Redis for cross-process visibility.
    """

    def __init__(self, redis_client=None) -> None:
        self._state: RiskState = RiskState.NORMAL
        self._lock: asyncio.Lock = asyncio.Lock()
        self._redis = redis_client
        self._history: list[RiskStateTransition] = []
        self._state_since: datetime = datetime.utcnow()

    # ── read-only properties ────────────────────────────────────────────

    @property
    def state(self) -> RiskState:
        return self._state

    @property
    def state_since(self) -> datetime:
        return self._state_since

    # ── capability queries ──────────────────────────────────────────────

    def can_trade(self) -> bool:
        """True when new entries or position adjustments are allowed."""
        return self._state <= RiskState.REDUCE

    def can_enter(self) -> bool:
        """True only in NORMAL — the only state permitting new positions."""
        return self._state == RiskState.NORMAL

    def can_exit(self) -> bool:
        """True when exit orders are still accepted (up to EXIT_ONLY)."""
        return self._state <= RiskState.EXIT_ONLY

    def must_flatten(self) -> bool:
        """True when ALL positions must be liquidated immediately."""
        return self._state >= RiskState.EMERGENCY_FLATTEN

    # ── state transitions ───────────────────────────────────────────────

    async def transition(self, new_state: RiskState, reason: str) -> bool:
        """
        Escalate to *new_state*.

        Returns True if the transition was applied, False if it was rejected
        (because the requested state is not strictly higher than the current one,
        or is NORMAL which requires ``recover()``).
        """
        async with self._lock:
            if new_state <= self._state and new_state != RiskState.NORMAL:
                logger.debug(
                    "RISK STATE transition rejected: current=%s, requested=%s",
                    self._state.name,
                    new_state.name,
                )
                return False

            # Block downward transitions via this method — use recover() instead
            if new_state < self._state:
                logger.warning(
                    "RISK STATE downward transition denied via transition(). "
                    "Use recover() instead. current=%s requested=%s",
                    self._state.name,
                    new_state.name,
                )
                return False

            old = self._state
            self._state = new_state
            self._state_since = datetime.utcnow()

            txn = RiskStateTransition(old, new_state, reason, self._state_since)
            self._history.append(txn)

            logger.warning(
                "RISK STATE: %s -> %s | reason=%s",
                old.name,
                new_state.name,
                reason,
            )

            if self._redis:
                try:
                    self._redis.set("nzt:risk_state", new_state.value)
                    self._redis.set("nzt:risk_state_reason", reason)
                    self._redis.set(
                        "nzt:risk_state_since",
                        self._state_since.isoformat(),
                    )
                except Exception:
                    logger.exception("Failed to persist risk state to Redis")

            return True

    async def recover(self, reason: str = "manual_recovery") -> bool:
        """
        Reset back to NORMAL.

        Forbidden from SYSTEM_HALTED — that state requires human confirmation
        outside of the automated system.
        """
        async with self._lock:
            if self._state == RiskState.SYSTEM_HALTED:
                logger.error(
                    "Cannot auto-recover from SYSTEM_HALTED — "
                    "requires human confirmation"
                )
                return False

            if self._state == RiskState.NORMAL:
                logger.debug("Already in NORMAL — nothing to recover")
                return True

            old = self._state
            self._state = RiskState.NORMAL
            self._state_since = datetime.utcnow()

            txn = RiskStateTransition(
                old, RiskState.NORMAL, reason, self._state_since
            )
            self._history.append(txn)

            logger.warning(
                "RISK RECOVERY: %s -> NORMAL | reason=%s",
                old.name,
                reason,
            )

            if self._redis:
                try:
                    self._redis.set("nzt:risk_state", RiskState.NORMAL.value)
                    self._redis.set("nzt:risk_state_reason", reason)
                    self._redis.set(
                        "nzt:risk_state_since",
                        self._state_since.isoformat(),
                    )
                except Exception:
                    logger.exception("Failed to persist recovery state to Redis")

            return True

    # ── introspection ───────────────────────────────────────────────────

    def get_history(self) -> list[RiskStateTransition]:
        """Return a copy of all transitions since process start."""
        return list(self._history)

    def summary(self) -> dict:
        """JSON-serializable snapshot for telemetry / dashboards."""
        return {
            "state": self._state.name,
            "state_value": self._state.value,
            "state_since": self._state_since.isoformat(),
            "can_enter": self.can_enter(),
            "can_trade": self.can_trade(),
            "can_exit": self.can_exit(),
            "must_flatten": self.must_flatten(),
            "transition_count": len(self._history),
        }
