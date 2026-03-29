"""Graceful Degradation & Circuit Breakers — Book 73.

Circuit breakers prevent cascading failures. When a subsystem fails,
the breaker opens and the system continues with reduced capability
rather than crashing entirely.

Three breaker states:
  CLOSED:    Normal operation, all requests pass through
  OPEN:      Subsystem failed, all requests fail-fast (no retry)
  HALF_OPEN: Testing recovery, limited requests pass through

Fail-safe philosophy (Book 57):
  Every subsystem has a defined degraded mode.
  Bridge crash → engine runs with cached signals
  Redis down → WAL becomes sole state store
  Claude down → no nightly intelligence, engine continues
  Grafana down → monitoring blind but trading continues

Usage:
    from python_brain.risk.circuit_breakers import CircuitBreaker, BreakerState

    breaker = CircuitBreaker("ibkr_connection", failure_threshold=5)
    if breaker.allow_request():
        try:
            result = call_ibkr()
            breaker.record_success()
        except Exception:
            breaker.record_failure()
    else:
        use_fallback()
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional

log = logging.getLogger("circuit_breakers")


class BreakerState(Enum):
    CLOSED = "CLOSED"        # Normal — requests pass through
    OPEN = "OPEN"            # Failed — requests fail fast
    HALF_OPEN = "HALF_OPEN"  # Recovery test — limited requests


@dataclass
class CircuitBreaker:
    """Circuit breaker for a single subsystem."""
    name: str
    failure_threshold: int = 5        # Failures before opening
    recovery_timeout_secs: float = 60  # Time before half-open test
    half_open_max_requests: int = 3    # Requests allowed in half-open

    # Internal state
    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _half_open_requests: int = field(default=0, init=False)

    @property
    def state(self) -> BreakerState:
        # Auto-transition OPEN → HALF_OPEN after timeout
        if self._state == BreakerState.OPEN:
            if time.time() - self._last_failure_time > self.recovery_timeout_secs:
                self._state = BreakerState.HALF_OPEN
                self._half_open_requests = 0
                log.info("BREAKER %s: OPEN → HALF_OPEN (testing recovery)", self.name)
        return self._state

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        state = self.state  # Triggers auto-transition check
        if state == BreakerState.CLOSED:
            return True
        elif state == BreakerState.HALF_OPEN:
            if self._half_open_requests < self.half_open_max_requests:
                self._half_open_requests += 1
                return True
            return False
        return False  # OPEN — fail fast

    def record_success(self):
        """Record a successful request."""
        self._success_count += 1
        if self._state == BreakerState.HALF_OPEN:
            # Recovery confirmed — close breaker
            self._state = BreakerState.CLOSED
            self._failure_count = 0
            log.info("BREAKER %s: HALF_OPEN → CLOSED (recovery confirmed)", self.name)
        elif self._state == BreakerState.CLOSED:
            self._failure_count = max(0, self._failure_count - 1)  # Decay failures

    def record_failure(self):
        """Record a failed request."""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state == BreakerState.HALF_OPEN:
            # Recovery failed — reopen
            self._state = BreakerState.OPEN
            log.warning("BREAKER %s: HALF_OPEN → OPEN (recovery failed)", self.name)
        elif self._state == BreakerState.CLOSED and self._failure_count >= self.failure_threshold:
            self._state = BreakerState.OPEN
            log.warning("BREAKER %s: CLOSED → OPEN (%d failures)", self.name, self._failure_count)

    def reset(self):
        """Manually reset breaker to closed state."""
        self._state = BreakerState.CLOSED
        self._failure_count = 0
        self._half_open_requests = 0
        log.info("BREAKER %s: manually reset to CLOSED", self.name)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "threshold": self.failure_threshold,
        }


class CircuitBreakerRegistry:
    """Manage all circuit breakers in the system."""

    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        # Register standard subsystem breakers
        self._register_defaults()

    def _register_defaults(self):
        """Register breakers for all major subsystems."""
        defaults = {
            "ibkr_connection": CircuitBreaker("ibkr_connection", failure_threshold=5, recovery_timeout_secs=30),
            "ibkr_data": CircuitBreaker("ibkr_data", failure_threshold=10, recovery_timeout_secs=60),
            "redis": CircuitBreaker("redis", failure_threshold=3, recovery_timeout_secs=15),
            "bridge_python": CircuitBreaker("bridge_python", failure_threshold=3, recovery_timeout_secs=30),
            "claude_api": CircuitBreaker("claude_api", failure_threshold=2, recovery_timeout_secs=300),
            "gemini_api": CircuitBreaker("gemini_api", failure_threshold=2, recovery_timeout_secs=300),
            "wal_writer": CircuitBreaker("wal_writer", failure_threshold=1, recovery_timeout_secs=10),
            "telegram": CircuitBreaker("telegram", failure_threshold=5, recovery_timeout_secs=600),
            "sheets_sync": CircuitBreaker("sheets_sync", failure_threshold=3, recovery_timeout_secs=600),
            "nightly_pipeline": CircuitBreaker("nightly_pipeline", failure_threshold=1, recovery_timeout_secs=3600),
        }
        self._breakers = defaults

    def get(self, name: str) -> Optional[CircuitBreaker]:
        return self._breakers.get(name)

    def allow(self, name: str) -> bool:
        """Check if requests to subsystem are allowed."""
        breaker = self._breakers.get(name)
        if breaker is None:
            return True  # Unknown breaker = allow
        return breaker.allow_request()

    def success(self, name: str):
        breaker = self._breakers.get(name)
        if breaker:
            breaker.record_success()

    def failure(self, name: str):
        breaker = self._breakers.get(name)
        if breaker:
            breaker.record_failure()

    def status(self) -> Dict[str, dict]:
        return {name: b.to_dict() for name, b in self._breakers.items()}

    def any_open(self) -> bool:
        return any(b.state == BreakerState.OPEN for b in self._breakers.values())

    def critical_open(self) -> bool:
        """Check if critical subsystems have open breakers."""
        critical = {"ibkr_connection", "wal_writer", "bridge_python"}
        return any(
            self._breakers[n].state == BreakerState.OPEN
            for n in critical if n in self._breakers
        )
