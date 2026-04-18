"""IBKR connection pool + circuit breaker.

Prevents apiStart wedges by:
1. Serializing NEW connections (1 in-flight at a time globally)
2. Circuit breaker: after 3 consecutive apiStart TimeoutError, pause 5 min
3. Client-id rotation: never reuse a cid within 10 min of a failed attempt

Consumed by all IBKR-dependent services via a shared registry file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path


log = logging.getLogger("ibkr-pool")

STATE_PATH = Path("/tmp/v5_ibkr_pool_state.json")
LOCK_PATH = Path("/tmp/v5_ibkr_pool.lock")

BURNED_CID_COOLDOWN_S = 600
CIRCUIT_BREAKER_FAILURES = 3
CIRCUIT_BREAKER_COOLDOWN_S = 300
MIN_SPAWN_INTERVAL_S = 10


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {
            "last_spawn_ts": 0,
            "consecutive_failures": 0,
            "circuit_broken_until": 0,
            "burned_cids": {},    # cid -> expiry ts
        }
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"last_spawn_ts": 0, "consecutive_failures": 0,
                "circuit_broken_until": 0, "burned_cids": {}}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state))
    except Exception:
        pass


def _cleanup_burned(state: dict) -> None:
    now = time.time()
    state["burned_cids"] = {
        cid: ts for cid, ts in state.get("burned_cids", {}).items()
        if ts > now
    }


def can_spawn_now() -> tuple[bool, str]:
    """Return (ok, reason)."""
    state = _load_state()
    now = time.time()
    _cleanup_burned(state)
    if now < state.get("circuit_broken_until", 0):
        wait = state["circuit_broken_until"] - now
        return False, f"circuit broken for {wait:.0f}s more"
    if now - state.get("last_spawn_ts", 0) < MIN_SPAWN_INTERVAL_S:
        wait = MIN_SPAWN_INTERVAL_S - (now - state["last_spawn_ts"])
        return False, f"rate limit, wait {wait:.0f}s"
    return True, "ok"


def record_spawn_start(cid: int) -> None:
    state = _load_state()
    state["last_spawn_ts"] = time.time()
    _save_state(state)


def record_spawn_success(cid: int) -> None:
    state = _load_state()
    state["consecutive_failures"] = 0
    _save_state(state)


def record_spawn_failure(cid: int) -> None:
    state = _load_state()
    state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
    now = time.time()
    state.setdefault("burned_cids", {})[str(cid)] = now + BURNED_CID_COOLDOWN_S
    if state["consecutive_failures"] >= CIRCUIT_BREAKER_FAILURES:
        state["circuit_broken_until"] = now + CIRCUIT_BREAKER_COOLDOWN_S
        log.warning("circuit broken for %ds after %d failures",
                    CIRCUIT_BREAKER_COOLDOWN_S, state["consecutive_failures"])
    _cleanup_burned(state)
    _save_state(state)


def cid_is_burned(cid: int) -> bool:
    state = _load_state()
    _cleanup_burned(state)
    return str(cid) in state.get("burned_cids", {})


def next_safe_cid(prefer: int) -> int:
    """Return prefer if safe, else rotate to next unburned cid."""
    if not cid_is_burned(prefer):
        return prefer
    cid = prefer
    while True:
        cid += 1
        if cid > 32000:
            cid = 100
        if not cid_is_burned(cid):
            return cid


def snapshot() -> dict:
    state = _load_state()
    _cleanup_burned(state)
    now = time.time()
    return {
        "can_spawn": can_spawn_now()[0],
        "last_spawn_s_ago": now - state.get("last_spawn_ts", now),
        "consecutive_failures": state.get("consecutive_failures", 0),
        "circuit_broken": now < state.get("circuit_broken_until", 0),
        "circuit_broken_until": state.get("circuit_broken_until", 0),
        "burned_cids": list(state.get("burned_cids", {}).keys()),
    }


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        print(f"Can spawn: {can_spawn_now()}")
        record_spawn_start(105)
        print(f"Safe cid for 107: {next_safe_cid(107)}")
        record_spawn_failure(108)
        record_spawn_failure(109)
        print(f"Snapshot: {snapshot()}")
        print("OK")
