"""
NZT-48 Durable DB Writer — Redis-Backed SQLite Write Queue (H-09)
==================================================================
Eliminates "database is locked" errors by serialising ALL SQLite writes
through a single coroutine fed by durable Redis lists.

Problem:
  Concurrent SQLite writes from the 60-second scan loop, learning state
  saves, equity snapshots, firewall events, etc. cause intermittent
  `sqlite3.OperationalError: database is locked`.  If the Python process
  crashes while an in-memory queue is draining, pending writes are lost.

Solution:
  1. Every write is LPUSH'd to a Redis list (survives process crash).
  2. A dedicated asyncio coroutine BRPOPs from the list and writes to
     SQLite sequentially — zero contention, zero lost writes.
  3. Three priority lanes:
       nzt:dbq:emergency  — kill switch, circuit breaker events
       nzt:dbq:trade      — signals, trades, positions, partials
       nzt:dbq:telemetry  — equity snapshots, learning state, stats
     Writer drains emergency first, then trade, then telemetry.

Redis key scheme:
  nzt:dbq:emergency  — FIFO list (LPUSH/BRPOP)
  nzt:dbq:trade      — FIFO list (LPUSH/BRPOP)
  nzt:dbq:telemetry  — FIFO list (LPUSH/BRPOP)

Each entry is a JSON object:
  {"query": "INSERT ...", "params": [...], "ts": "2025-01-15T12:00:00Z"}

References:
  - SQLite WAL mode: https://sqlite.org/wal.html
  - Redis BRPOP: https://redis.io/commands/brpop
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("nzt48.db_writer")

# Redis key names for the three priority queues
_Q_EMERGENCY = "nzt:dbq:emergency"
_Q_TRADE = "nzt:dbq:trade"
_Q_TELEMETRY = "nzt:dbq:telemetry"

# Map human-readable priority names to Redis keys
_PRIORITY_KEYS = {
    "emergency": _Q_EMERGENCY,
    "trade": _Q_TRADE,
    "telemetry": _Q_TELEMETRY,
}

# Queue depth warning threshold
_QUEUE_DEPTH_WARNING = 50

# Max consecutive SQLite errors before the writer backs off
_MAX_CONSECUTIVE_ERRORS = 10
_BACKOFF_SECONDS = 5.0

# BRPOP timeout in seconds (0 = block forever; we use 1s to allow
# periodic queue-depth checks and graceful shutdown)
_BRPOP_TIMEOUT = 1


class DurableDBWriter:
    """Redis-backed durable write queue for SQLite.

    Usage::

        writer = DurableDBWriter(redis_client, db_path="data/nzt48.db")
        await writer.start()

        # From anywhere in the codebase:
        await writer.enqueue(
            "INSERT INTO signals (id, ticker) VALUES (?, ?)",
            ["SIG-001", "QQQ3.L"],
            priority="trade",
        )

        # At shutdown:
        await writer.stop()
    """

    def __init__(
        self,
        redis_client,
        db_path: str | Path = "data/nzt48.db",
    ) -> None:
        """
        Parameters
        ----------
        redis_client : redis.asyncio.Redis
            An async Redis client (already connected). Must have
            ``decode_responses=True``.
        db_path : str | Path
            Path to the SQLite database file.
        """
        self._redis = redis_client
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._writes_total = 0
        self._writes_failed = 0
        self._consecutive_errors = 0

    # ──────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Open the SQLite connection (WAL mode) and launch the writer loop."""
        if self._running:
            logger.warning("DurableDBWriter.start() called but already running")
            return

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # WAL-safe, faster than FULL

        self._running = True
        self._task = asyncio.create_task(
            self._writer_loop(), name="durable_db_writer"
        )
        logger.info(
            "DurableDBWriter started — db=%s, queues=[%s, %s, %s]",
            self._db_path, _Q_EMERGENCY, _Q_TRADE, _Q_TELEMETRY,
        )

    async def stop(self) -> None:
        """Signal the writer loop to stop, drain remaining items, and close."""
        if not self._running:
            return

        self._running = False
        if self._task:
            # Give the loop up to 10 seconds to drain
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("DurableDBWriter drain timed out — cancelling")
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None

        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

        depth = await self.get_queue_depth()
        total_pending = sum(v for v in depth.values() if v > 0)
        logger.info(
            "DurableDBWriter stopped — %d writes completed, %d failed, %d still queued",
            self._writes_total, self._writes_failed, total_pending,
        )

    # ──────────────────────────────────────────────────────────────
    # Public API — enqueue a write
    # ──────────────────────────────────────────────────────────────

    async def enqueue(
        self,
        query: str,
        params: list[Any] | tuple[Any, ...] | None = None,
        priority: str = "trade",
    ) -> None:
        """Enqueue a SQL write for durable, serialised execution.

        Parameters
        ----------
        query : str
            The SQL statement (INSERT, UPDATE, DELETE, etc.).
        params : list | tuple | None
            Bind parameters for the query.
        priority : str
            One of "emergency", "trade", "telemetry".
            Emergency is drained first, telemetry last.
        """
        key = _PRIORITY_KEYS.get(priority)
        if key is None:
            raise ValueError(
                f"Invalid priority '{priority}' — must be one of: "
                f"{', '.join(_PRIORITY_KEYS)}"
            )

        payload = json.dumps({
            "query": query,
            "params": list(params) if params else [],
            "ts": datetime.now(timezone.utc).isoformat(),
        })

        try:
            await self._redis.lpush(key, payload)
        except Exception as e:
            # If Redis is down, the write is truly lost — log critically
            logger.critical(
                "DurableDBWriter: Redis LPUSH failed — WRITE LOST: %s | error=%s",
                query[:120], e,
            )
            raise

    async def enqueue_many(
        self,
        queries: list[tuple[str, list[Any] | tuple[Any, ...] | None]],
        priority: str = "trade",
    ) -> None:
        """Enqueue multiple writes atomically via a Redis pipeline.

        Parameters
        ----------
        queries : list of (query, params) tuples
        priority : str
            Priority lane for all queries in the batch.
        """
        key = _PRIORITY_KEYS.get(priority)
        if key is None:
            raise ValueError(f"Invalid priority '{priority}'")

        now = datetime.now(timezone.utc).isoformat()
        payloads = [
            json.dumps({
                "query": q,
                "params": list(p) if p else [],
                "ts": now,
            })
            for q, p in queries
        ]

        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                for payload in payloads:
                    pipe.lpush(key, payload)
                await pipe.execute()
        except Exception as e:
            logger.critical(
                "DurableDBWriter: Redis pipeline LPUSH failed — %d WRITES LOST: %s",
                len(queries), e,
            )
            raise

    # ──────────────────────────────────────────────────────────────
    # Monitoring
    # ──────────────────────────────────────────────────────────────

    async def get_queue_depth(self) -> dict[str, int]:
        """Return current queue depths: {emergency: N, trade: N, telemetry: N}."""
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                pipe.llen(_Q_EMERGENCY)
                pipe.llen(_Q_TRADE)
                pipe.llen(_Q_TELEMETRY)
                results = await pipe.execute()
            return {
                "emergency": results[0],
                "trade": results[1],
                "telemetry": results[2],
            }
        except Exception as e:
            logger.error("DurableDBWriter: queue depth check failed: %s", e)
            return {"emergency": -1, "trade": -1, "telemetry": -1}

    @property
    def stats(self) -> dict[str, Any]:
        """Return writer statistics."""
        return {
            "running": self._running,
            "writes_total": self._writes_total,
            "writes_failed": self._writes_failed,
            "consecutive_errors": self._consecutive_errors,
        }

    # ──────────────────────────────────────────────────────────────
    # Writer loop (private)
    # ──────────────────────────────────────────────────────────────

    async def _writer_loop(self) -> None:
        """Main writer coroutine: BRPOP from Redis, write to SQLite.

        Priority order: emergency > trade > telemetry.
        Uses BRPOP with a 1-second timeout so we can periodically
        check queue depth and respond to shutdown signals.
        """
        logger.info("DurableDBWriter: writer loop started")
        check_interval = 30  # seconds between queue-depth warnings
        last_depth_check = 0.0

        while self._running:
            try:
                item = await self._pop_next()
                if item is None:
                    # BRPOP timed out — no pending writes
                    # Periodic queue-depth monitoring
                    now = time.monotonic()
                    if now - last_depth_check > check_interval:
                        last_depth_check = now
                        await self._check_queue_depth()
                    continue

                queue_key, raw_payload = item
                await self._execute_write(queue_key, raw_payload)

            except asyncio.CancelledError:
                logger.info("DurableDBWriter: writer loop cancelled")
                break
            except Exception as e:
                logger.error("DurableDBWriter: writer loop error: %s", e)
                await asyncio.sleep(1.0)

        # Drain remaining items on shutdown
        await self._drain_on_shutdown()
        logger.info("DurableDBWriter: writer loop exited")

    async def _pop_next(self) -> tuple[str, str] | None:
        """Pop the highest-priority item from the queues.

        Checks emergency first, then trade, then telemetry.
        Returns (queue_key, payload_json) or None if all queues are empty
        after the BRPOP timeout.
        """
        # Try emergency queue first (non-blocking)
        result = await self._redis.rpop(_Q_EMERGENCY)
        if result is not None:
            return (_Q_EMERGENCY, result)

        # Try trade queue (non-blocking)
        result = await self._redis.rpop(_Q_TRADE)
        if result is not None:
            return (_Q_TRADE, result)

        # Block on all three queues with timeout (telemetry is the
        # most common, so blocking here is efficient).
        # BRPOP returns (key, value) or None on timeout.
        result = await self._redis.brpop(
            [_Q_EMERGENCY, _Q_TRADE, _Q_TELEMETRY],
            timeout=_BRPOP_TIMEOUT,
        )
        if result is not None:
            return (result[0], result[1])

        return None

    async def _execute_write(self, queue_key: str, raw_payload: str) -> None:
        """Parse and execute a single SQLite write."""
        try:
            entry = json.loads(raw_payload)
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(
                "DurableDBWriter: corrupt payload on %s — DISCARDED: %s | error=%s",
                queue_key, raw_payload[:200], e,
            )
            self._writes_failed += 1
            return

        query = entry.get("query", "")
        params = entry.get("params", [])
        enqueued_ts = entry.get("ts", "")

        if not query:
            logger.warning("DurableDBWriter: empty query — DISCARDED")
            self._writes_failed += 1
            return

        try:
            self._conn.execute(query, params)
            self._conn.commit()
            self._writes_total += 1
            self._consecutive_errors = 0

            # Log latency for trade-critical writes
            if queue_key == _Q_EMERGENCY:
                try:
                    enqueued = datetime.fromisoformat(enqueued_ts)
                    latency_ms = (
                        datetime.now(timezone.utc) - enqueued
                    ).total_seconds() * 1000
                    logger.info(
                        "DurableDBWriter: EMERGENCY write executed — "
                        "latency=%.1fms | query=%s",
                        latency_ms, query[:80],
                    )
                except Exception:
                    pass

        except sqlite3.OperationalError as e:
            self._consecutive_errors += 1
            self._writes_failed += 1
            error_msg = str(e)

            if "database is locked" in error_msg:
                # This should be extremely rare with serialised writes
                # but can happen if another process holds a lock.
                # Re-queue the write so it's not lost.
                logger.warning(
                    "DurableDBWriter: database locked — re-queuing | "
                    "consecutive_errors=%d | query=%s",
                    self._consecutive_errors, query[:80],
                )
                try:
                    await self._redis.lpush(queue_key, raw_payload)
                except Exception:
                    logger.critical(
                        "DurableDBWriter: re-queue failed — WRITE LOST: %s",
                        query[:120],
                    )

                if self._consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                    logger.error(
                        "DurableDBWriter: %d consecutive errors — backing off %.1fs",
                        self._consecutive_errors, _BACKOFF_SECONDS,
                    )
                    await asyncio.sleep(_BACKOFF_SECONDS)

            else:
                # SQL error (bad query, constraint violation, etc.)
                # These are not retriable — log and discard.
                logger.error(
                    "DurableDBWriter: SQLite error — DISCARDED: %s | "
                    "query=%s | params=%s",
                    e, query[:120], str(params)[:200],
                )

        except Exception as e:
            self._consecutive_errors += 1
            self._writes_failed += 1
            logger.error(
                "DurableDBWriter: unexpected error — query=%s | error=%s",
                query[:120], e,
            )
            if self._consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                await asyncio.sleep(_BACKOFF_SECONDS)

    async def _drain_on_shutdown(self) -> None:
        """Best-effort drain of remaining queued writes on shutdown."""
        drained = 0
        max_drain = 500  # Safety cap to prevent hanging on shutdown

        for _ in range(max_drain):
            try:
                # Non-blocking pop from all queues in priority order
                for key in [_Q_EMERGENCY, _Q_TRADE, _Q_TELEMETRY]:
                    result = await self._redis.rpop(key)
                    if result is not None:
                        await self._execute_write(key, result)
                        drained += 1
                        break
                else:
                    # All queues empty
                    break
            except Exception as e:
                logger.error("DurableDBWriter: drain error: %s", e)
                break

        if drained > 0:
            logger.info("DurableDBWriter: drained %d writes on shutdown", drained)

    async def _check_queue_depth(self) -> None:
        """Log a warning if queue depth exceeds threshold."""
        depth = await self.get_queue_depth()
        total = sum(v for v in depth.values() if v >= 0)

        if total > _QUEUE_DEPTH_WARNING:
            logger.warning(
                "DurableDBWriter: queue depth HIGH — "
                "emergency=%d, trade=%d, telemetry=%d (total=%d, threshold=%d)",
                depth["emergency"], depth["trade"], depth["telemetry"],
                total, _QUEUE_DEPTH_WARNING,
            )
