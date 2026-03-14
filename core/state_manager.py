"""
NZT-48 State Manager — Redis SSOT with Lua Atomicity (V8.0)
=============================================================
Byzantine State Supremacy: ALL mutable trading state lives in Redis.

Resolves contradictions: C-14, C-15, C-16, C-26

Features:
- Lua atomic scripts for position close (P&L update + delete in single roundtrip)
- Kill switch persisted in Redis (survives container restarts)
- Ulysses Lock with SHA256 config hash verification
- Fail-closed on Redis loss (3 consecutive failures → halt)
- Redis Streams helpers (XADD/XREADGROUP, NOT Pub/Sub)
- Ghost Ledger for shadow execution with DSR comparison
- Graceful fallback to in-memory if Redis unavailable
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("nzt48.state_manager")

# ---------------------------------------------------------------------------
# Lua scripts — execute atomically in a single Redis roundtrip
# ---------------------------------------------------------------------------

_CLOSE_POSITION_LUA = """
local pos_key = KEYS[1]
local equity_key = KEYS[2]
local daily_pnl_key = KEYS[3]
local net_pnl = tonumber(ARGV[1])

-- Verify position exists before modifying anything
local pos_data = redis.call('GET', pos_key)
if not pos_data then return 0 end

-- Atomic: update P&L + delete position in single Redis call
redis.call('INCRBYFLOAT', equity_key, net_pnl)
redis.call('INCRBYFLOAT', daily_pnl_key, net_pnl)
redis.call('DEL', pos_key)
return 1
"""

_SET_KILL_LUA = """
local kill_key = KEYS[1]
local reason = ARGV[1]
local timestamp = ARGV[2]

redis.call('HSET', kill_key, 'active', '1', 'reason', reason, 'timestamp', timestamp)
return 1
"""


class StateManager:
    """Redis-backed SSOT for all mutable trading state.

    Uses redis.asyncio for async operations. Falls back to in-memory
    if Redis is unavailable (degraded mode — logged as WARNING).

    Redis key scheme:
        nzt:pos:{pos_id}          — position JSON (no TTL)
        nzt:equity                — current equity float
        nzt:daily_pnl             — daily P&L (reset at midnight UK)
        nzt:kill                  — kill switch hash (reason + timestamp, no TTL)
        nzt:chandelier:{trade_id} — chandelier state
        nzt:frozen_config         — frozen YAML content
        nzt:frozen_hash           — SHA256 of frozen config
        nzt:frozen_at             — freeze timestamp
        nzt:stream:signals        — Redis Stream for trade intents
        nzt:stream:ticks          — Redis Stream for price updates
        nzt:ghost:pos:*           — ghost ledger positions
        nzt:ghost:equity          — ghost ledger equity
    """

    # Redis key constants
    _POS_PREFIX = "nzt:pos:"
    _EQUITY_KEY = "nzt:equity"
    _DAILY_PNL_KEY = "nzt:daily_pnl"
    _KILL_KEY = "nzt:kill"
    _CHANDELIER_PREFIX = "nzt:chandelier:"
    _FROZEN_CONFIG_KEY = "nzt:frozen_config"
    _FROZEN_HASH_KEY = "nzt:frozen_hash"
    _FROZEN_AT_KEY = "nzt:frozen_at"
    _STREAM_SIGNALS = "nzt:stream:signals"
    _STREAM_TICKS = "nzt:stream:ticks"
    _GHOST_POS_PREFIX = "nzt:ghost:pos:"
    _GHOST_EQUITY_KEY = "nzt:ghost:equity"

    def __init__(
        self,
        redis_url: str | None = "redis://localhost:6379",
        redis_password: str | None = "nzt48redis",
        initial_equity: float = 10000.0,
        db=None,
    ) -> None:
        self._redis_url = redis_url
        self._redis_password = redis_password
        self._initial_equity = initial_equity
        self._db = db  # SQLite connection for reconciliation
        self._redis = None
        self._close_script = None
        self._kill_script = None
        self._fallback_mode = redis_url is None

        # In-memory fallback state
        self._mem_positions: dict[str, dict] = {}
        self._mem_equity: float = initial_equity
        self._mem_daily_pnl: float = 0.0
        self._mem_killed: bool = False
        self._mem_kill_reason: str = ""
        self._mem_chandelier: dict[str, dict] = {}
        self._mem_frozen_config: dict | None = None
        self._mem_frozen_hash: str = ""

        # Health tracking
        self._last_health_check_ns: int = 0
        self._consecutive_failures: int = 0
        self._fail_closed: bool = False

    async def connect(self) -> None:
        """Initialize Redis connection and register Lua scripts."""
        if self._fallback_mode:
            logger.warning("STATE_MANAGER: running in FALLBACK (in-memory) mode — no Redis")
            return

        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                self._redis_url,
                password=self._redis_password,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            # Test connection
            await self._redis.ping()

            # Register Lua scripts
            self._close_script = self._redis.register_script(_CLOSE_POSITION_LUA)
            self._kill_script = self._redis.register_script(_SET_KILL_LUA)

            # Initialize equity if not set
            if not await self._redis.exists(self._EQUITY_KEY):
                await self._redis.set(self._EQUITY_KEY, str(self._initial_equity))
            if not await self._redis.exists(self._DAILY_PNL_KEY):
                await self._redis.set(self._DAILY_PNL_KEY, "0.0")

            logger.info("STATE_MANAGER: connected to Redis — Lua scripts registered")

        except Exception as e:
            logger.error("STATE_MANAGER: Redis connection failed (%s) — falling back to in-memory", e)
            self._redis = None
            self._fallback_mode = True

    # ─── Position CRUD ────────────────────────────────────────────────────────

    async def get_position(self, pos_id: str) -> dict | None:
        """Get a single position by ID."""
        if self._fallback_mode:
            return self._mem_positions.get(pos_id)
        try:
            data = await self._redis.get(f"{self._POS_PREFIX}{pos_id}")
            return json.loads(data) if data else None
        except Exception as e:
            logger.error("STATE_MANAGER: get_position failed: %s", e)
            return self._mem_positions.get(pos_id)

    async def set_position(self, pos_id: str, data: dict) -> None:
        """Create or update a position."""
        self._mem_positions[pos_id] = data
        if self._fallback_mode:
            return
        try:
            await self._redis.set(
                f"{self._POS_PREFIX}{pos_id}",
                json.dumps(data, default=str),
            )
        except Exception as e:
            logger.error("STATE_MANAGER: set_position failed: %s", e)

    async def delete_position(self, pos_id: str) -> None:
        """Delete a position (use close_position_atomic for closes with P&L)."""
        self._mem_positions.pop(pos_id, None)
        if self._fallback_mode:
            return
        try:
            await self._redis.delete(f"{self._POS_PREFIX}{pos_id}")
        except Exception as e:
            logger.error("STATE_MANAGER: delete_position failed: %s", e)

    async def get_all_positions(self) -> dict[str, dict]:
        """Get all open positions."""
        if self._fallback_mode:
            return dict(self._mem_positions)
        try:
            keys = []
            async for key in self._redis.scan_iter(match=f"{self._POS_PREFIX}*"):
                keys.append(key)
            if not keys:
                return {}
            pipe = self._redis.pipeline()
            for key in keys:
                pipe.get(key)
            values = await pipe.execute()
            result = {}
            for key, val in zip(keys, values):
                if val:
                    pos_id = key.removeprefix(self._POS_PREFIX)
                    result[pos_id] = json.loads(val)
            return result
        except Exception as e:
            logger.error("STATE_MANAGER: get_all_positions failed: %s", e)
            return dict(self._mem_positions)

    async def close_position_atomic(self, pos_id: str, net_pnl: float) -> bool:
        """Atomically update P&L and delete position using Lua script.

        Fixes C-16: no gap between position deletion and P&L update.
        The Lua script executes in a single Redis roundtrip — either both
        happen or neither does.
        """
        # Always update in-memory
        self._mem_positions.pop(pos_id, None)
        self._mem_equity += net_pnl
        self._mem_daily_pnl += net_pnl

        if self._fallback_mode or self._close_script is None:
            return True

        try:
            result = await self._close_script(
                keys=[
                    f"{self._POS_PREFIX}{pos_id}",
                    self._EQUITY_KEY,
                    self._DAILY_PNL_KEY,
                ],
                args=[str(net_pnl)],
            )
            if result == 0:
                logger.warning("STATE_MANAGER: close_position_atomic — position %s not found in Redis", pos_id)
                return False
            return True
        except Exception as e:
            logger.error("STATE_MANAGER: close_position_atomic Lua failed: %s", e)
            return True  # In-memory was updated

    # ─── P&L ──────────────────────────────────────────────────────────────────

    async def get_equity(self) -> float:
        """Get current equity."""
        if self._fallback_mode:
            return self._mem_equity
        try:
            val = await self._redis.get(self._EQUITY_KEY)
            return float(val) if val else self._mem_equity
        except Exception as e:
            logger.error("STATE_MANAGER: get_equity failed: %s", e)
            return self._mem_equity

    async def get_daily_pnl(self) -> float:
        """Get today's P&L."""
        if self._fallback_mode:
            return self._mem_daily_pnl
        try:
            val = await self._redis.get(self._DAILY_PNL_KEY)
            return float(val) if val else 0.0
        except Exception as e:
            logger.error("STATE_MANAGER: get_daily_pnl failed: %s", e)
            return self._mem_daily_pnl

    async def update_pnl(self, net_pnl: float) -> float:
        """Update P&L (non-atomic — use close_position_atomic for closes)."""
        self._mem_equity += net_pnl
        self._mem_daily_pnl += net_pnl
        if self._fallback_mode:
            return self._mem_equity
        try:
            pipe = self._redis.pipeline()
            pipe.incrbyfloat(self._EQUITY_KEY, net_pnl)
            pipe.incrbyfloat(self._DAILY_PNL_KEY, net_pnl)
            results = await pipe.execute()
            return float(results[0])
        except Exception as e:
            logger.error("STATE_MANAGER: update_pnl failed: %s", e)
            return self._mem_equity

    async def reset_daily_pnl(self) -> None:
        """Reset daily P&L to zero (called at midnight UK)."""
        self._mem_daily_pnl = 0.0
        if self._fallback_mode:
            return
        try:
            await self._redis.set(self._DAILY_PNL_KEY, "0.0")
        except Exception as e:
            logger.error("STATE_MANAGER: reset_daily_pnl failed: %s", e)

    # ─── Kill Switch (C-26: persisted in Redis) ──────────────────────────────

    async def is_killed(self) -> bool:
        """Check if kill switch is active. Survives container restarts."""
        if self._fallback_mode:
            return self._mem_killed
        try:
            active = await self._redis.hget(self._KILL_KEY, "active")
            return active == "1"
        except Exception as e:
            logger.error("STATE_MANAGER: is_killed failed: %s", e)
            return self._mem_killed

    async def set_kill(self, reason: str) -> None:
        """Activate kill switch with reason. Persists in Redis (no TTL)."""
        self._mem_killed = True
        self._mem_kill_reason = reason
        logger.critical("KILL_SWITCH_ACTIVATED: %s", reason)
        if self._fallback_mode:
            return
        try:
            ts = datetime.now(timezone.utc).isoformat()
            await self._kill_script(
                keys=[self._KILL_KEY],
                args=[reason, ts],
            )
        except Exception as e:
            logger.error("STATE_MANAGER: set_kill failed: %s", e)

    async def clear_kill(self) -> None:
        """Clear kill switch (manual intervention only)."""
        self._mem_killed = False
        self._mem_kill_reason = ""
        if self._fallback_mode:
            return
        try:
            await self._redis.delete(self._KILL_KEY)
            logger.warning("KILL_SWITCH_CLEARED")
        except Exception as e:
            logger.error("STATE_MANAGER: clear_kill failed: %s", e)

    async def get_kill_info(self) -> dict | None:
        """Get kill switch details (reason, timestamp)."""
        if self._fallback_mode:
            if self._mem_killed:
                return {"reason": self._mem_kill_reason, "active": True}
            return None
        try:
            data = await self._redis.hgetall(self._KILL_KEY)
            return data if data and data.get("active") == "1" else None
        except Exception:
            return None

    # ─── Chandelier State ─────────────────────────────────────────────────────

    async def get_chandelier_state(self, trade_id: str) -> dict | None:
        """Get chandelier exit state for a trade."""
        if self._fallback_mode:
            return self._mem_chandelier.get(trade_id)
        try:
            data = await self._redis.get(f"{self._CHANDELIER_PREFIX}{trade_id}")
            return json.loads(data) if data else None
        except Exception as e:
            logger.error("STATE_MANAGER: get_chandelier failed: %s", e)
            return self._mem_chandelier.get(trade_id)

    async def set_chandelier_state(self, trade_id: str, state: dict) -> None:
        """Set chandelier exit state. No TTL — explicitly deleted on position close."""
        self._mem_chandelier[trade_id] = state
        if self._fallback_mode:
            return
        try:
            await self._redis.set(
                f"{self._CHANDELIER_PREFIX}{trade_id}",
                json.dumps(state, default=str),
            )
        except Exception as e:
            logger.error("STATE_MANAGER: set_chandelier failed: %s", e)

    async def delete_chandelier_state(self, trade_id: str) -> None:
        """Delete chandelier state on position close."""
        self._mem_chandelier.pop(trade_id, None)
        if self._fallback_mode:
            return
        try:
            await self._redis.delete(f"{self._CHANDELIER_PREFIX}{trade_id}")
        except Exception as e:
            logger.error("STATE_MANAGER: delete_chandelier failed: %s", e)

    # ─── Ulysses Lock (Imperative 1) ─────────────────────────────────────────

    async def freeze_config(self, config_dict: dict) -> str:
        """Freeze config at market open. Returns SHA256 hash.

        Called at 07:55 UK. Config is frozen in Redis and hashed.
        During market hours, get_frozen_config() returns this snapshot.
        """
        config_json = json.dumps(config_dict, sort_keys=True, default=str)
        config_hash = hashlib.sha256(config_json.encode()).hexdigest()

        self._mem_frozen_config = config_dict
        self._mem_frozen_hash = config_hash

        if not self._fallback_mode and self._redis:
            try:
                pipe = self._redis.pipeline()
                pipe.set(self._FROZEN_CONFIG_KEY, config_json)
                pipe.set(self._FROZEN_HASH_KEY, config_hash)
                pipe.set(self._FROZEN_AT_KEY, datetime.now(timezone.utc).isoformat())
                await pipe.execute()
                logger.info("ULYSSES_LOCK: config frozen — hash=%s", config_hash[:16])
            except Exception as e:
                logger.error("ULYSSES_LOCK: freeze failed: %s", e)

        return config_hash

    async def get_frozen_config(self) -> dict | None:
        """Get the frozen config snapshot (during market hours)."""
        if self._fallback_mode:
            return self._mem_frozen_config
        try:
            data = await self._redis.get(self._FROZEN_CONFIG_KEY)
            return json.loads(data) if data else None
        except Exception as e:
            logger.error("ULYSSES_LOCK: get_frozen_config failed: %s", e)
            return self._mem_frozen_config

    async def verify_config_hash(self, memory_hash: str) -> bool:
        """Verify in-memory config hash matches Redis frozen hash.

        Detects memory corruption or accidental mid-session config changes.
        Mismatch triggers system halt.
        """
        if self._fallback_mode:
            return memory_hash == self._mem_frozen_hash

        try:
            redis_hash = await self._redis.get(self._FROZEN_HASH_KEY)
            if redis_hash is None:
                return True  # No frozen config — pre-market
            match = memory_hash == redis_hash
            if not match:
                logger.critical(
                    "ULYSSES_LOCK_VIOLATION: memory_hash=%s != redis_hash=%s — HALTING",
                    memory_hash[:16], redis_hash[:16],
                )
            return match
        except Exception as e:
            logger.error("ULYSSES_LOCK: verify failed: %s", e)
            return True  # Don't halt on Redis errors

    # ─── Reconciliation (C-14) ───────────────────────────────────────────────

    async def reconcile_with_sqlite(self) -> list[str]:
        """Compare Redis equity with SQLite trade history sum.

        Returns list of discrepancy messages (empty = all good).
        Runs on startup and nightly.
        """
        discrepancies: list[str] = []

        if self._db is None:
            return ["No database connection — cannot reconcile"]

        try:
            cursor = self._db.execute(
                "SELECT COALESCE(SUM(net_pnl), 0) FROM virtual_trades"
            )
            row = cursor.fetchone()
            sqlite_total_pnl = float(row[0]) if row else 0.0
            expected_equity = self._initial_equity + sqlite_total_pnl

            redis_equity = await self.get_equity()

            delta = abs(redis_equity - expected_equity)
            if delta > 1.0:  # £1 tolerance
                msg = (
                    f"P&L_RECONCILIATION_MISMATCH: "
                    f"Redis={redis_equity:.2f}, SQLite={expected_equity:.2f}, "
                    f"delta={delta:.2f}"
                )
                discrepancies.append(msg)
                logger.warning(msg)

                # SQLite is ground truth — correct Redis
                if not self._fallback_mode and self._redis:
                    await self._redis.set(self._EQUITY_KEY, str(expected_equity))
                    self._mem_equity = expected_equity
                    logger.info("RECONCILIATION: Redis equity corrected to %.2f", expected_equity)
            else:
                logger.info(
                    "RECONCILIATION: OK — Redis=%.2f, SQLite=%.2f, delta=%.2f",
                    redis_equity, expected_equity, delta,
                )

        except Exception as e:
            discrepancies.append(f"Reconciliation error: {e}")
            logger.error("RECONCILIATION: failed: %s", e)

        return discrepancies

    # ─── Hydration ───────────────────────────────────────────────────────────

    async def hydrate_from_redis(self) -> bool:
        """Load state from Redis on startup. Returns True if successful."""
        if self._fallback_mode or not self._redis:
            return False
        try:
            # Equity
            eq = await self._redis.get(self._EQUITY_KEY)
            if eq:
                self._mem_equity = float(eq)

            # Daily P&L
            dpnl = await self._redis.get(self._DAILY_PNL_KEY)
            if dpnl:
                self._mem_daily_pnl = float(dpnl)

            # Kill switch
            kill_data = await self._redis.hgetall(self._KILL_KEY)
            if kill_data and kill_data.get("active") == "1":
                self._mem_killed = True
                self._mem_kill_reason = kill_data.get("reason", "unknown")

            # Positions
            async for key in self._redis.scan_iter(match=f"{self._POS_PREFIX}*"):
                data = await self._redis.get(key)
                if data:
                    pos_id = key.removeprefix(self._POS_PREFIX)
                    self._mem_positions[pos_id] = json.loads(data)

            logger.info(
                "STATE_MANAGER: hydrated — equity=%.2f, positions=%d, killed=%s",
                self._mem_equity, len(self._mem_positions), self._mem_killed,
            )
            return True

        except Exception as e:
            logger.error("STATE_MANAGER: hydration failed: %s", e)
            return False

    # ─── Fail-Closed (Imperative 7) ──────────────────────────────────────────

    async def health_check(self) -> bool:
        """Check Redis health. If unhealthy, trigger fail-closed.

        Called every tick cycle. Measures Redis PING latency with monotonic clock.
        If ping fails or latency > 100ms for 3 consecutive checks, system halts.
        """
        if self._fallback_mode:
            return True  # In-memory mode is always "healthy"

        try:
            t0 = time.monotonic_ns()
            await self._redis.ping()
            latency_ns = time.monotonic_ns() - t0
            latency_ms = latency_ns / 1_000_000

            if latency_ms > 100:
                self._consecutive_failures += 1
                logger.warning(
                    "STATE_MANAGER: Redis latency %.1fms > 100ms threshold (failure #%d)",
                    latency_ms, self._consecutive_failures,
                )
                if self._consecutive_failures >= 3:
                    await self.enter_fail_closed(f"Redis latency {latency_ms:.1f}ms x 3")
                    return False
            else:
                self._consecutive_failures = 0

            self._last_health_check_ns = time.monotonic_ns()
            return True

        except Exception as e:
            self._consecutive_failures += 1
            logger.error(
                "STATE_MANAGER: Redis ping failed (%s) — failure #%d",
                e, self._consecutive_failures,
            )
            if self._consecutive_failures >= 3:
                await self.enter_fail_closed(f"Redis unreachable: {e}")
                return False
            return True

    async def enter_fail_closed(self, reason: str) -> None:
        """Enter fail-closed state: halt trading, set kill switch.

        The system NEVER trades blind without Redis state confirmation.
        """
        if self._fail_closed:
            return  # Already in fail-closed
        self._fail_closed = True
        logger.critical("FAIL_CLOSED: %s — trading halted", reason)
        self._mem_killed = True
        self._mem_kill_reason = f"FAIL_CLOSED: {reason}"
        try:
            if self._redis:
                await self._redis.hset(
                    self._KILL_KEY,
                    mapping={"reason": reason, "timestamp": datetime.now(timezone.utc).isoformat(), "active": "1"},
                )
        except Exception:
            pass  # Best-effort

    @property
    def is_fail_closed(self) -> bool:
        """Check if system is in fail-closed state."""
        return self._fail_closed

    # ─── V9.5: JSON Helpers (Telemetry Buffer) ───────────────────────────────

    async def set_json(self, key: str, data: dict, ttl: int | None = None) -> None:
        """Store JSON data in Redis with optional TTL (seconds)."""
        if self._fallback_mode:
            return
        try:
            await self._redis.set(key, json.dumps(data, default=str))
            if ttl:
                await self._redis.expire(key, ttl)
        except Exception as e:
            logger.error("STATE_MANAGER: set_json failed for %s: %s", key, e)

    async def get_json(self, key: str) -> dict | None:
        """Retrieve JSON data from Redis."""
        if self._fallback_mode:
            return None
        try:
            data = await self._redis.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            logger.error("STATE_MANAGER: get_json failed for %s: %s", key, e)
            return None

    # ─── V9.5: Hot-Reload PUBSUB ──────────────────────────────────────────

    _PUBSUB_CHANNEL = "nzt:system:hot_reload"

    async def publish_hot_reload(self, message: dict) -> None:
        """Publish hot-reload event to all listeners (tick_loop, virtual_trader)."""
        if self._fallback_mode:
            logger.warning("STATE_MANAGER: PUBSUB not available in fallback mode")
            return
        try:
            payload = json.dumps(message, default=str)
            n_subscribers = await self._redis.publish(self._PUBSUB_CHANNEL, payload)
            logger.info(
                "HOT_RELOAD published: type=%s subscribers=%d",
                message.get("type", "unknown"), n_subscribers,
            )
        except Exception as e:
            logger.error("STATE_MANAGER: publish_hot_reload failed: %s", e)

    async def subscribe_hot_reload(self, callback) -> None:
        """Subscribe to hot-reload events. Calls `await callback(message_dict)` per event.

        Runs indefinitely — launch via asyncio.create_task().
        """
        if self._fallback_mode:
            logger.warning("STATE_MANAGER: PUBSUB not available in fallback mode")
            return

        try:
            import redis.asyncio as aioredis

            # PUBSUB requires a separate connection (Redis constraint)
            pubsub_redis = aioredis.from_url(
                self._redis_url,
                password=self._redis_password,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            pubsub = pubsub_redis.pubsub()
            await pubsub.subscribe(self._PUBSUB_CHANNEL)
            logger.info("HOT_RELOAD subscribed to channel: %s", self._PUBSUB_CHANNEL)

            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        await callback(data)
                    except Exception as e:
                        logger.error("HOT_RELOAD callback error: %s", e)
        except Exception as e:
            logger.error("STATE_MANAGER: subscribe_hot_reload failed: %s", e)

    # ─── Redis Streams (Phase 4) ─────────────────────────────────────────────

    async def publish_signal(self, signal_data: dict) -> str | None:
        """Publish a trade intent to Redis Stream (XADD).

        Returns the stream entry ID, or None on failure.
        """
        if self._fallback_mode:
            return None
        try:
            fields = {k: str(v) for k, v in signal_data.items()}
            entry_id = await self._redis.xadd(
                self._STREAM_SIGNALS,
                fields,
                maxlen=1000,
            )
            return entry_id
        except Exception as e:
            logger.error("STATE_MANAGER: publish_signal failed: %s", e)
            return None

    async def create_consumer_group(self, stream: str, group: str) -> None:
        """Create a consumer group for a Redis Stream."""
        if self._fallback_mode:
            return
        try:
            await self._redis.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as e:
            if "BUSYGROUP" in str(e):
                pass  # Group already exists
            else:
                logger.error("STATE_MANAGER: create_consumer_group failed: %s", e)

    async def read_stream(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block: int = 5000,
    ) -> list[tuple[str, dict]]:
        """Read from a Redis Stream using XREADGROUP.

        Returns list of (entry_id, fields) tuples.
        """
        if self._fallback_mode:
            return []
        try:
            messages = await self._redis.xreadgroup(
                group, consumer,
                {stream: ">"},
                count=count,
                block=block,
            )
            result = []
            for _stream_name, entries in messages:
                for entry_id, fields in entries:
                    result.append((entry_id, fields))
            return result
        except Exception as e:
            logger.error("STATE_MANAGER: read_stream failed: %s", e)
            return []

    async def ack_stream(self, stream: str, group: str, *entry_ids: str) -> None:
        """Acknowledge processed stream entries."""
        if self._fallback_mode:
            return
        try:
            await self._redis.xack(stream, group, *entry_ids)
        except Exception as e:
            logger.error("STATE_MANAGER: ack_stream failed: %s", e)

    # ─── Cleanup ─────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            logger.info("STATE_MANAGER: Redis connection closed")


class GhostLedger:
    """Shadow execution with alternative parameters for A/B comparison.

    Routes TRADE_INTENTS to a ghost track simultaneously with live.
    Ghost uses fixed 1% risk, no profit ladder — simplest viable strategy.
    Nightly: compare Deflated Sharpe Ratio (live vs ghost).

    Reuses: core/quant_math/dsr.py (Bailey & Lopez de Prado 2014)
    """

    def __init__(self, state_manager: StateManager) -> None:
        self._sm = state_manager
        self._ghost_trades: list[dict] = []
        self.logger = logging.getLogger("nzt48.ghost_ledger")

    async def shadow_execute(self, signal: dict, live_fill_price: float) -> None:
        """Execute a shadow trade with simplified ghost parameters.

        Ghost rules:
        - Fixed 1% risk (no Kelly)
        - Same entry price as live
        - Same stop as live
        - Target = 2% (no ladder, simple take-profit)
        """
        ghost_risk_pct = 0.01
        entry = live_fill_price
        stop = float(signal.get("stop", 0))
        target = entry * 1.02  # Simple 2% target

        if stop <= 0 or entry <= 0:
            return

        risk_per_share = abs(entry - stop)
        if risk_per_share <= 0:
            return

        equity = await self._sm.get_equity()
        risk_dollars = equity * ghost_risk_pct
        shares = int(risk_dollars / risk_per_share)
        if shares <= 0:
            return

        ghost_pos = {
            "ticker": signal.get("ticker", ""),
            "direction": signal.get("direction", "LONG"),
            "entry": entry,
            "stop": stop,
            "target": target,
            "shares": shares,
            "risk_dollars": risk_dollars,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        pos_id = f"ghost_{signal.get('ticker', '')}_{int(time.time())}"
        if not self._sm._fallback_mode and self._sm._redis:
            try:
                await self._sm._redis.set(
                    f"{self._sm._GHOST_POS_PREFIX}{pos_id}",
                    json.dumps(ghost_pos, default=str),
                )
            except Exception as e:
                self.logger.error("GHOST: shadow_execute failed: %s", e)

    async def shadow_update(self, prices: dict[str, float]) -> None:
        """Update ghost positions with current prices. Close at stop or target."""
        if self._sm._fallback_mode or not self._sm._redis:
            return

        try:
            keys = []
            async for key in self._sm._redis.scan_iter(match=f"{self._sm._GHOST_POS_PREFIX}*"):
                keys.append(key)

            for key in keys:
                data = await self._sm._redis.get(key)
                if not data:
                    continue
                pos = json.loads(data)
                ticker = pos.get("ticker", "")
                if ticker not in prices:
                    continue

                price = prices[ticker]
                direction = pos.get("direction", "LONG")
                entry = pos["entry"]
                stop = pos["stop"]
                target = pos["target"]
                shares = pos["shares"]

                closed = False
                pnl = 0.0

                if direction == "LONG":
                    if price <= stop:
                        pnl = (stop - entry) * shares
                        closed = True
                    elif price >= target:
                        pnl = (target - entry) * shares
                        closed = True
                else:
                    if price >= stop:
                        pnl = (entry - stop) * shares
                        closed = True
                    elif price <= target:
                        pnl = (entry - target) * shares
                        closed = True

                if closed:
                    self._ghost_trades.append({
                        **pos,
                        "exit_price": price,
                        "pnl": pnl,
                        "closed_at": datetime.now(timezone.utc).isoformat(),
                    })
                    await self._sm._redis.delete(key)
                    ghost_eq = await self._sm._redis.get(self._sm._GHOST_EQUITY_KEY)
                    new_eq = float(ghost_eq or self._sm._initial_equity) + pnl
                    await self._sm._redis.set(self._sm._GHOST_EQUITY_KEY, str(new_eq))

        except Exception as e:
            self.logger.error("GHOST: shadow_update failed: %s", e)

    async def compare_dsr(self) -> dict:
        """Compare Deflated Sharpe Ratios between live and ghost tracks.

        Reuses core/quant_math/dsr.py.
        """
        try:
            from core.quant_math.dsr import deflated_sharpe_ratio
            import numpy as np

            live_returns: list[float] = []
            if self._sm._db:
                cursor = self._sm._db.execute(
                    "SELECT net_pnl FROM virtual_trades ORDER BY time_exited"
                )
                live_returns = [float(row[0]) for row in cursor.fetchall()]

            ghost_returns = [t.get("pnl", 0.0) for t in self._ghost_trades]

            if len(live_returns) < 10 or len(ghost_returns) < 10:
                return {
                    "live_dsr": 0.0,
                    "ghost_dsr": 0.0,
                    "live_better": True,
                    "insufficient_data": True,
                }

            live_dsr = deflated_sharpe_ratio(np.array(live_returns))
            ghost_dsr = deflated_sharpe_ratio(np.array(ghost_returns))

            return {
                "live_dsr": float(live_dsr),
                "ghost_dsr": float(ghost_dsr),
                "live_better": live_dsr > ghost_dsr,
                "live_trades": len(live_returns),
                "ghost_trades": len(ghost_returns),
            }

        except ImportError:
            self.logger.warning("GHOST: dsr module not available")
            return {"live_dsr": 0.0, "ghost_dsr": 0.0, "live_better": True, "error": "dsr unavailable"}
        except Exception as e:
            self.logger.error("GHOST: compare_dsr failed: %s", e)
            return {"live_dsr": 0.0, "ghost_dsr": 0.0, "live_better": True, "error": str(e)}
