"""IBKR Failure Modes & Reconnection — Book 44.

Handles the many ways IBKR connections fail:
1. Weekend disconnect (expected, Friday 21:00 → Sunday 18:00)
2. Monday 2FA stall (requires manual intervention)
3. Pacing violations (too many requests per second)
4. Data subscription limits (100 lines max)
5. Order rejection codes (200+ known codes)
6. Gateway restart (loses all subscriptions)

Connection state machine:
  DISCONNECTED → CONNECTING → AUTHENTICATING → SUBSCRIBING → CONNECTED
  Any state → DEGRADED (partial data) → DEAD (no data)

Usage:
    from python_brain.execution.ibkr_resilience import (
        IBKRConnectionManager, ConnectionState,
    )

    mgr = IBKRConnectionManager()
    mgr.on_disconnect()
    mgr.attempt_reconnect()
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

log = logging.getLogger("ibkr_resilience")


class ConnectionState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATING = "AUTHENTICATING"
    SUBSCRIBING = "SUBSCRIBING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"  # Partial data
    DEAD = "DEAD"           # No data, needs human intervention


# Valid transitions
VALID_TRANSITIONS: Dict[ConnectionState, Set[ConnectionState]] = {
    ConnectionState.DISCONNECTED: {ConnectionState.CONNECTING},
    ConnectionState.CONNECTING: {ConnectionState.AUTHENTICATING, ConnectionState.DISCONNECTED},
    ConnectionState.AUTHENTICATING: {ConnectionState.SUBSCRIBING, ConnectionState.DISCONNECTED},
    ConnectionState.SUBSCRIBING: {ConnectionState.CONNECTED, ConnectionState.DEGRADED, ConnectionState.DISCONNECTED},
    ConnectionState.CONNECTED: {ConnectionState.DEGRADED, ConnectionState.DISCONNECTED},
    ConnectionState.DEGRADED: {ConnectionState.CONNECTED, ConnectionState.DEAD, ConnectionState.DISCONNECTED},
    ConnectionState.DEAD: {ConnectionState.DISCONNECTED},  # Must go through full reconnect
}


# IBKR error codes → action mapping (subset of 200+ codes from Book 44)
ERROR_ACTIONS: Dict[int, str] = {
    # Recoverable — retry
    502: "retry",        # Couldn't connect
    504: "retry",        # Not connected
    1100: "retry",       # Connectivity lost
    2104: "retry",       # Market data farm connection OK
    2106: "retry",       # HMDS data farm connection OK
    # Pacing — backoff
    100: "backoff",      # Max rate of messages exceeded
    162: "backoff",      # Historical data request pacing violation
    # Auth — needs human
    1101: "human",       # Connectivity restored (read-only)
    326: "human",        # Client not connected (auth needed)
    # Fatal — halt
    1102: "halt",        # Connectivity restored (data lost)
    10225: "halt",       # Bust confirmed — trade cancelled by exchange
}


@dataclass
class ReconnectAttempt:
    """Record of a reconnection attempt."""
    attempt_number: int
    timestamp: float
    result: str  # "success", "failed", "timeout"
    delay_secs: float


class IBKRConnectionManager:
    """Manage IBKR connection with exponential backoff reconnection."""

    def __init__(
        self,
        max_retries: int = 10,
        base_delay_secs: float = 10.0,
        max_delay_secs: float = 160.0,
    ):
        self._state = ConnectionState.DISCONNECTED
        self._max_retries = max_retries
        self._base_delay = base_delay_secs
        self._max_delay = max_delay_secs
        self._retry_count = 0
        self._last_connected_time = 0.0
        self._attempts: List[ReconnectAttempt] = []
        self._subscriptions: Set[str] = set()
        self._pacing_violations = 0

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state in (ConnectionState.CONNECTED, ConnectionState.DEGRADED)

    @property
    def next_retry_delay(self) -> float:
        """Exponential backoff: 10s, 20s, 40s, 80s, 160s, 160s, ..."""
        delay = self._base_delay * (2 ** min(self._retry_count, 4))
        return min(delay, self._max_delay)

    def _transition(self, new_state: ConnectionState) -> bool:
        allowed = VALID_TRANSITIONS.get(self._state, set())
        if new_state not in allowed:
            log.warning("IBKR: invalid transition %s → %s", self._state.value, new_state.value)
            return False
        old = self._state
        self._state = new_state
        log.info("IBKR: %s → %s", old.value, new_state.value)
        return True

    def on_connect(self):
        """Called when connection established."""
        self._state = ConnectionState.CONNECTED
        self._retry_count = 0
        self._last_connected_time = time.time()
        log.info("IBKR: CONNECTED")

    def on_disconnect(self):
        """Called on disconnection."""
        self._transition(ConnectionState.DISCONNECTED)

    def on_error(self, error_code: int, message: str = ""):
        """Handle IBKR error code."""
        action = ERROR_ACTIONS.get(error_code, "retry")

        if action == "retry":
            log.info("IBKR error %d (retry): %s", error_code, message)
        elif action == "backoff":
            self._pacing_violations += 1
            log.warning("IBKR pacing violation %d (#%d): %s",
                       error_code, self._pacing_violations, message)
        elif action == "human":
            self._transition(ConnectionState.DEAD)
            log.warning("IBKR error %d (HUMAN NEEDED): %s", error_code, message)
        elif action == "halt":
            self._transition(ConnectionState.DEAD)
            log.error("IBKR FATAL error %d: %s", error_code, message)

        return action

    def attempt_reconnect(self) -> bool:
        """Attempt to reconnect with exponential backoff.

        Returns True if should attempt, False if max retries exceeded.
        """
        if self._retry_count >= self._max_retries:
            self._transition(ConnectionState.DEAD)
            log.error("IBKR: max retries (%d) exceeded → DEAD", self._max_retries)
            return False

        delay = self.next_retry_delay
        self._retry_count += 1

        attempt = ReconnectAttempt(
            attempt_number=self._retry_count,
            timestamp=time.time(),
            result="pending",
            delay_secs=delay,
        )
        self._attempts.append(attempt)

        log.info("IBKR: reconnect attempt %d/%d (delay=%.0fs)",
                self._retry_count, self._max_retries, delay)

        self._transition(ConnectionState.CONNECTING)
        return True

    def on_reconnect_success(self):
        """Called after successful reconnection."""
        if self._attempts:
            self._attempts[-1].result = "success"
        self.on_connect()
        # Must resubscribe to all market data after reconnect
        log.info("IBKR: reconnected — resubscribing %d instruments", len(self._subscriptions))

    def on_reconnect_failure(self):
        """Called after failed reconnection attempt."""
        if self._attempts:
            self._attempts[-1].result = "failed"
        self._transition(ConnectionState.DISCONNECTED)

    def is_monday_2fa_stall(self) -> bool:
        """Detect Monday morning 2FA authentication stall.

        Pattern: disconnected on Friday, reconnect fails on Monday
        with auth timeout. Requires human to complete 2FA.
        """
        import datetime
        now = datetime.datetime.now()
        is_monday = now.weekday() == 0
        is_morning = now.hour < 10
        auth_stalled = self._state == ConnectionState.AUTHENTICATING
        return is_monday and is_morning and auth_stalled

    def track_subscription(self, ticker: str):
        self._subscriptions.add(ticker)

    def remove_subscription(self, ticker: str):
        self._subscriptions.discard(ticker)

    def to_dict(self) -> dict:
        return {
            "state": self._state.value,
            "retry_count": self._retry_count,
            "max_retries": self._max_retries,
            "next_delay_secs": self.next_retry_delay,
            "pacing_violations": self._pacing_violations,
            "subscriptions": len(self._subscriptions),
            "total_attempts": len(self._attempts),
            "is_connected": self.is_connected,
        }
