"""
NZT-48 AsyncioHeartbeat — GIL Freeze Detector (AEGIS K-02)
============================================================
Continuous 10ms heartbeat on the asyncio event loop. If observed lag
exceeds 50ms (configurable), trips the brain circuit breaker to prevent
stale-data trading.

Why this matters:
  - CPython's GIL can stall the event loop during heavy numpy/pandas
    computation, JSON serialisation, or SQLite writes.
  - A frozen event loop means price feeds, stop updates, and order
    submissions are delayed — potentially catastrophic for leveraged ETPs.
  - This detector provides P50/P95/P99 latency stats and a binary
    health signal consumed by the main engine's circuit breaker.

Detection method:
  1. Schedule asyncio.sleep(0.01) in a tight loop
  2. Measure wall-clock delta between expected and actual wakeup
  3. If delta > max_lag_ms for any single beat, flag unhealthy
  4. Maintain rolling window for percentile statistics

This is a SKELETON — full Q2 implementation will add:
  - Integration with system_watchdog.py circuit breaker
  - Telegram alerting on sustained freezes (>3 consecutive)
  - Redis publication of heartbeat stats for dashboard
  - Thread-level GIL contention tracking via sys.getswitchinterval()
  - Automatic asyncio.Task introspection to identify the blocking coroutine

References:
  - CPython GIL: Beazley (2010) "Understanding the Python GIL"
  - Event loop monitoring: uvloop benchmarks (MagicStack 2016)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger("nzt48.heartbeat")

# Default rolling window: last 1000 heartbeats (~10 seconds at 10ms interval)
_DEFAULT_WINDOW_SIZE = 1000

# Consecutive freeze threshold before circuit breaker trips
_FREEZE_TRIP_COUNT = 3


@dataclass
class HeartbeatStats:
    """Latency statistics from the heartbeat monitor.

    All values in milliseconds.
    """
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    max_ms: float = 0.0
    mean_ms: float = 0.0
    total_beats: int = 0
    total_freezes: int = 0       # beats where lag > max_lag
    consecutive_freezes: int = 0  # current streak of freezes
    healthy: bool = True


class AsyncioHeartbeat:
    """10ms heartbeat. If lag >50ms, trip brain circuit breaker.

    Usage::

        heartbeat = AsyncioHeartbeat(max_lag_ms=50)
        asyncio.create_task(heartbeat.run())

        # Later, check health:
        if not heartbeat.is_healthy():
            engine.trip_circuit_breaker("GIL freeze detected")

        # Get detailed stats:
        stats = heartbeat.get_stats()
        logger.info("Heartbeat P99: %.1fms", stats["p99_ms"])

    Parameters
    ----------
    max_lag_ms : float
        Maximum acceptable lag in milliseconds before a beat is flagged
        as a freeze (default 50).
    interval_ms : float
        Target heartbeat interval in milliseconds (default 10).
    window_size : int
        Number of recent beats to keep for percentile calculation
        (default 1000 = ~10 seconds of history).
    on_freeze : callable | None
        Optional callback invoked on each freeze event. Receives the
        lag_ms value as argument. Intended for circuit breaker integration.
    """

    def __init__(
        self,
        max_lag_ms: float = 50.0,
        interval_ms: float = 10.0,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        on_freeze: Optional[Callable[[float], None]] = None,
    ):
        self._max_lag = max_lag_ms / 1000.0       # convert to seconds
        self._interval = interval_ms / 1000.0     # convert to seconds
        self._window_size = window_size
        self._on_freeze = on_freeze

        # Rolling window of observed lags (seconds)
        self._lags: deque[float] = deque(maxlen=window_size)

        # Aggregate counters
        self._total_beats: int = 0
        self._total_freezes: int = 0
        self._consecutive_freezes: int = 0
        self._max_lag_observed: float = 0.0

        # Control
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None

        logger.info(
            "AsyncioHeartbeat initialised: interval=%.0fms, max_lag=%.0fms, "
            "window=%d",
            interval_ms, max_lag_ms, window_size,
        )

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    async def run(self) -> None:
        """Continuous heartbeat loop. Run as an asyncio task.

        Measures the wall-clock delta between expected and actual wakeup
        after each asyncio.sleep(interval). Any delta exceeding max_lag
        is recorded as a freeze event.

        This coroutine runs indefinitely until stop() is called.
        """
        self._running = True
        logger.info("Heartbeat loop started")

        # TODO (Q2): implement the heartbeat loop
        #   1. Record time before sleep
        #   2. await asyncio.sleep(self._interval)
        #   3. Record time after sleep
        #   4. lag = (after - before) - self._interval
        #   5. Append lag to self._lags
        #   6. If lag > self._max_lag:
        #      a. Increment freeze counters
        #      b. Call self._on_freeze(lag_ms) if set
        #      c. Log warning with lag value
        #   7. Else: reset consecutive freeze counter
        #   8. Update self._max_lag_observed
        #   9. Increment self._total_beats
        raise NotImplementedError("K-02 skeleton — Q2 implementation pending")

    def stop(self) -> None:
        """Stop the heartbeat loop gracefully.

        Sets the running flag to False. The run() coroutine will exit
        on its next iteration.
        """
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("Heartbeat loop stop requested")

    # -------------------------------------------------------------------
    # Health queries
    # -------------------------------------------------------------------

    def is_healthy(self) -> bool:
        """Check whether the event loop is healthy.

        Returns
        -------
        bool
            True if the last beat was within max_lag AND consecutive
            freeze count is below the trip threshold.
        """
        if self._total_beats == 0:
            return True  # no data yet — assume healthy
        return self._consecutive_freezes < _FREEZE_TRIP_COUNT

    def get_stats(self) -> dict:
        """Get detailed heartbeat statistics.

        Returns
        -------
        dict
            Keys: p50_ms, p95_ms, p99_ms, max_ms, mean_ms,
            total_beats, total_freezes, consecutive_freezes, healthy.
        """
        stats = HeartbeatStats(
            total_beats=self._total_beats,
            total_freezes=self._total_freezes,
            consecutive_freezes=self._consecutive_freezes,
            max_ms=self._max_lag_observed * 1000.0,
            healthy=self.is_healthy(),
        )

        if self._lags:
            lags_sorted = sorted(self._lags)
            n = len(lags_sorted)
            stats.mean_ms = (sum(lags_sorted) / n) * 1000.0
            stats.p50_ms = lags_sorted[int(n * 0.50)] * 1000.0
            stats.p95_ms = lags_sorted[min(int(n * 0.95), n - 1)] * 1000.0
            stats.p99_ms = lags_sorted[min(int(n * 0.99), n - 1)] * 1000.0

        return {
            "p50_ms": round(stats.p50_ms, 2),
            "p95_ms": round(stats.p95_ms, 2),
            "p99_ms": round(stats.p99_ms, 2),
            "max_ms": round(stats.max_ms, 2),
            "mean_ms": round(stats.mean_ms, 2),
            "total_beats": stats.total_beats,
            "total_freezes": stats.total_freezes,
            "consecutive_freezes": stats.consecutive_freezes,
            "healthy": stats.healthy,
        }

    def get_stats_obj(self) -> HeartbeatStats:
        """Get statistics as a typed HeartbeatStats dataclass.

        Returns
        -------
        HeartbeatStats
            Structured stats object.
        """
        raw = self.get_stats()
        return HeartbeatStats(**raw)

    # -------------------------------------------------------------------
    # Integration helpers
    # -------------------------------------------------------------------

    def create_task(self, loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Task:
        """Create and store an asyncio task for the heartbeat loop.

        Parameters
        ----------
        loop : asyncio.AbstractEventLoop | None
            Event loop to schedule on. Uses running loop if None.

        Returns
        -------
        asyncio.Task
            The created task.
        """
        if loop is None:
            loop = asyncio.get_running_loop()
        self._task = loop.create_task(self.run(), name="nzt48-heartbeat")
        return self._task

    @property
    def is_running(self) -> bool:
        """Whether the heartbeat loop is currently active."""
        return self._running and self._task is not None and not self._task.done()
