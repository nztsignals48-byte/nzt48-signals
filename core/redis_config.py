"""
NZT-48 Redis Configuration Constants (H-13)
=============================================
Centralised Redis database assignment and connection helpers.

Database separation policy:
  DB 0 (REDIS_DB_STATE):     Critical trading state — positions, Chandelier exits,
                              circuit breakers, kill switch, equity, frozen config.
                              Eviction policy: noeviction (MUST never lose state).

  DB 1 (REDIS_DB_TELEMETRY): Telemetry and metrics — feature snapshots (V9.5),
                              ML training buffers, CloudWatch scratchpad, temp caches.
                              Eviction policy: allkeys-lru (OK to evict oldest when full).

Usage:
    from core.redis_config import REDIS_DB_STATE, REDIS_DB_TELEMETRY, get_redis_url

    # State connection (default — used by StateManager, ChandelierExit, CircuitBreakers)
    state_url = get_redis_url(db=REDIS_DB_STATE)

    # Telemetry connection (used by TelemetryBuffer, CloudWatch scratchpad)
    telemetry_url = get_redis_url(db=REDIS_DB_TELEMETRY)

Note:
    Redis 7 applies maxmemory-policy globally. To enforce per-DB policies,
    you would need two separate Redis instances. For now, DB 0 is protected
    by the global noeviction policy set in docker-compose.yml, and DB 1 data
    is designed to be ephemeral (TTL-based expiry handles cleanup).
    If telemetry volume grows, consider adding a second Redis instance for DB 1
    with allkeys-lru policy.
"""
from __future__ import annotations

import os

# ── Database assignments ─────────────────────────────────────────────────
REDIS_DB_STATE = 0       # Critical: positions, chandelier, circuit breakers, kill switch
REDIS_DB_TELEMETRY = 1   # Ephemeral: feature snapshots, ML buffers, metrics

# ── Connection defaults ──────────────────────────────────────────────────
REDIS_HOST = "redis"                    # Docker service name
REDIS_PORT = 6379
REDIS_PASSWORD = "nzt48redis"           # Matches docker-compose.yml

# ── Key prefix conventions ───────────────────────────────────────────────
# DB 0 keys (state):
#   nzt:pos:{pos_id}          — position JSON
#   nzt:equity                — current equity
#   nzt:daily_pnl             — daily P&L
#   nzt:kill                  — kill switch hash
#   nzt:chandelier:{trade_id} — chandelier state
#   nzt:frozen_config         — frozen YAML
#   nzt:cb:{breaker_name}     — circuit breaker state
#
# DB 1 keys (telemetry):
#   nzt:telemetry:{signal_id} — feature snapshot (TTL 7d)
#   nzt:cw:last_emit          — CloudWatch last emit timestamp


def get_redis_url(
    db: int = REDIS_DB_STATE,
    host: str | None = None,
    port: int | None = None,
    password: str | None = None,
) -> str:
    """Build a Redis URL for the given database number.

    Priority: explicit args > environment variables > defaults.

    Args:
        db:       Database number (0 = state, 1 = telemetry).
        host:     Redis hostname. Defaults to REDIS_HOST or env REDIS_HOST.
        port:     Redis port. Defaults to REDIS_PORT or env REDIS_PORT.
        password: Redis password. Defaults to REDIS_PASSWORD or env REDIS_PASSWORD.

    Returns:
        Redis URL string like "redis://:password@host:port/db"
    """
    _host = host or os.environ.get("REDIS_HOST", REDIS_HOST)
    _port = port or int(os.environ.get("REDIS_PORT", str(REDIS_PORT)))
    _password = password or os.environ.get("REDIS_PASSWORD", REDIS_PASSWORD)

    return f"redis://:{_password}@{_host}:{_port}/{db}"


def get_sync_client(db: int = REDIS_DB_STATE, **kwargs):
    """Create a synchronous Redis client for the given database.

    Args:
        db: Database number (0 = state, 1 = telemetry).
        **kwargs: Additional args passed to redis.Redis().

    Returns:
        redis.Redis instance.

    Raises:
        ImportError: If redis package is not installed.
    """
    import redis
    defaults = {
        "host": os.environ.get("REDIS_HOST", REDIS_HOST),
        "port": int(os.environ.get("REDIS_PORT", str(REDIS_PORT))),
        "password": os.environ.get("REDIS_PASSWORD", REDIS_PASSWORD),
        "db": db,
        "decode_responses": True,
        "socket_connect_timeout": 5,
        "socket_timeout": 5,
    }
    defaults.update(kwargs)
    return redis.Redis(**defaults)


def get_async_client(db: int = REDIS_DB_STATE, **kwargs):
    """Create an async Redis client for the given database.

    Args:
        db: Database number (0 = state, 1 = telemetry).
        **kwargs: Additional args passed to redis.asyncio.Redis.from_url().

    Returns:
        redis.asyncio.Redis instance.

    Raises:
        ImportError: If redis package is not installed.
    """
    import redis.asyncio as aioredis
    url = get_redis_url(db=db)
    defaults = {
        "decode_responses": True,
        "socket_keepalive": True,
        "socket_timeout": 5,
        "retry_on_timeout": True,
        "health_check_interval": 30,
    }
    defaults.update(kwargs)
    return aioredis.from_url(url, **defaults)
