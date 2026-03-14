"""
NZT-48 Startup Readiness Gate — AEGIS Phase H-01
==================================================
8 pre-flight checks before any trading activity commences.

Three-tier outcome:
    READY    = all checks pass, full trading enabled
    DEGRADED = non-critical checks fail, proceed with reduced functionality
    HALTED   = critical checks fail, sys.exit(1)

Checks:
    1. DB connectivity (SQLite file exists and readable)
    2. Redis connected + Chandelier state loaded
    3. Data feed fresh <5 min (bypassed outside market hours 06:00-22:00 UK)
    4. Kill switch OFF (check Redis ``nzt:kill``)
    5. Circuit breaker GREEN/YELLOW (not RED/HALTED)
    6. Disk >20% free
    7. Memory >500MB free
    8. Time sync <5s drift (compare system time to NTP)

Reference: AEGIS_MASTER_PLAN Section H — Operational Infrastructure.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import struct
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("nzt48.startup_gate")

_UK_TZ = ZoneInfo("Europe/London")

# ── Thresholds ───────────────────────────────────────────────────────────────
_DISK_FREE_PCT_MIN = 0.20          # 20% minimum free disk space
_MEMORY_FREE_MB_MIN = 500          # 500 MB minimum free memory
_NTP_DRIFT_MAX_SEC = 5.0           # Maximum acceptable NTP drift in seconds
_DATA_FEED_STALE_SEC = 300         # 5 minutes max staleness
_MARKET_HOURS_START = dtime(6, 0)  # 06:00 UK — data freshness required from here
_MARKET_HOURS_END = dtime(22, 0)   # 22:00 UK — data freshness required until here

# NTP pool servers (pool.ntp.org)
_NTP_SERVERS = [
    "pool.ntp.org",
    "time.google.com",
    "time.cloudflare.com",
]


class StartupGateResult:
    """Result of the startup readiness gate."""

    READY = "READY"
    DEGRADED = "DEGRADED"
    HALTED = "HALTED"

    def __init__(self) -> None:
        self.status: str = self.READY
        self.checks: list[dict] = []
        self.critical_failures: list[str] = []
        self.warnings: list[str] = []

    def add_pass(self, name: str, detail: str = "") -> None:
        self.checks.append({"name": name, "status": "PASS", "detail": detail})

    def add_warn(self, name: str, detail: str) -> None:
        self.checks.append({"name": name, "status": "WARN", "detail": detail})
        self.warnings.append(f"{name}: {detail}")
        if self.status == self.READY:
            self.status = self.DEGRADED

    def add_fail(self, name: str, detail: str) -> None:
        self.checks.append({"name": name, "status": "FAIL", "detail": detail})
        self.critical_failures.append(f"{name}: {detail}")
        self.status = self.HALTED

    def summary(self) -> str:
        lines = [f"STARTUP GATE: {self.status}"]
        for c in self.checks:
            icon = {"PASS": "[OK]", "WARN": "[!!]", "FAIL": "[XX]"}[c["status"]]
            lines.append(f"  {icon} {c['name']}: {c['detail'] or 'pass'}")
        return "\n".join(lines)


# ── NTP Query (RFC 4330, minimal client) ─────────────────────────────────────

def _query_ntp(server: str, timeout: float = 3.0) -> Optional[float]:
    """Query an NTP server and return the clock offset in seconds.

    Returns None on failure. Uses NTPv3 (mode 3 = client).
    """
    try:
        # NTP packet: 48 bytes, LI=0, VN=3, Mode=3 → first byte = 0x1B
        msg = b"\x1b" + 47 * b"\0"
        # Set transmit timestamp (bytes 40-47)
        t_send = time.time()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(msg, (server, 123))
        data, _ = sock.recvfrom(1024)
        sock.close()

        t_recv = time.time()

        if len(data) < 48:
            return None

        # Extract NTP timestamps (seconds since 1900-01-01)
        # Transmit timestamp is at bytes 40-43 (integer) + 44-47 (fraction)
        _NTP_EPOCH = 2208988800  # seconds between 1900-01-01 and 1970-01-01
        t_ntp_int = struct.unpack("!I", data[40:44])[0]
        t_ntp_frac = struct.unpack("!I", data[44:48])[0]
        t_ntp = (t_ntp_int - _NTP_EPOCH) + (t_ntp_frac / 2**32)

        # Simple offset = NTP time - midpoint of send/recv
        offset = t_ntp - (t_send + t_recv) / 2
        return offset

    except Exception:
        return None


def _get_ntp_offset() -> Optional[float]:
    """Try multiple NTP servers, return first successful offset."""
    for server in _NTP_SERVERS:
        offset = _query_ntp(server)
        if offset is not None:
            return offset
    return None


# ── Memory Check ─────────────────────────────────────────────────────────────

def _get_free_memory_mb() -> Optional[float]:
    """Return available system memory in MB. Platform-aware."""
    try:
        # Try psutil first (most reliable)
        import psutil
        mem = psutil.virtual_memory()
        return mem.available / (1024 * 1024)
    except ImportError:
        pass

    # Fallback: read /proc/meminfo on Linux
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    # Value is in kB
                    kb = int(line.split()[1])
                    return kb / 1024.0
    except (FileNotFoundError, ValueError, IndexError):
        pass

    # macOS fallback via vm_stat
    try:
        import subprocess
        result = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            page_size = 4096  # default macOS page size
            free_pages = 0
            for line in result.stdout.splitlines():
                if "page size" in line.lower():
                    parts = line.split()
                    for part in parts:
                        if part.isdigit():
                            page_size = int(part)
                            break
                for label in ("Pages free:", "Pages inactive:", "Pages speculative:"):
                    if line.strip().startswith(label):
                        val = line.split(":")[1].strip().rstrip(".")
                        free_pages += int(val)
            return (free_pages * page_size) / (1024 * 1024)
    except Exception:
        pass

    return None


# ── Main Gate ────────────────────────────────────────────────────────────────

def run_startup_gate(
    db_path: Optional[Path] = None,
    redis_client=None,
    circuit_breakers=None,
    data_feed_last_ts: Optional[float] = None,
) -> StartupGateResult:
    """Execute all 8 pre-flight checks and return the gate result.

    Args:
        db_path: Path to the SQLite database file.
        redis_client: Sync redis.Redis client (or None if unavailable).
        circuit_breakers: CircuitBreakerSystem instance (or None).
        data_feed_last_ts: Unix timestamp of last data feed update (or None).

    Returns:
        StartupGateResult with status READY/DEGRADED/HALTED.
    """
    result = StartupGateResult()

    # ── Check 1: DB connectivity ─────────────────────────────────────────
    if db_path is None:
        db_path = Path(__file__).parent.parent / "data" / "nzt48.db"

    try:
        if db_path.exists() and db_path.is_file():
            # Try opening the DB to verify it is readable
            import sqlite3
            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.execute("SELECT 1")
            conn.close()
            result.add_pass("DB_CONNECTIVITY", f"{db_path} readable")
        else:
            # DB file might not exist on first run — that is acceptable
            result.add_warn("DB_CONNECTIVITY", f"{db_path} does not exist (first run?)")
    except Exception as e:
        result.add_fail("DB_CONNECTIVITY", f"SQLite open failed: {e}")

    # ── Check 2: Redis connected + Chandelier state ──────────────────────
    try:
        if redis_client is not None:
            redis_client.ping()
            # Check if chandelier keys exist (at least one = state loaded)
            chandelier_keys = redis_client.keys("nzt:chandelier:*")
            if chandelier_keys:
                result.add_pass("REDIS_CHANDELIER", f"Redis OK, {len(chandelier_keys)} chandelier state(s)")
            else:
                result.add_pass("REDIS_CHANDELIER", "Redis OK, no active chandelier state (clean start)")
        else:
            # H-02: Redis is critical for positions, stops, and kill switch — HALT
            result.add_fail("REDIS_CHANDELIER", "Redis client not provided — critical for positions, stops, kill switch")
    except Exception as e:
        # H-02: Redis unreachable is a critical failure, not degraded
        result.add_fail("REDIS_CHANDELIER", f"Redis unreachable (critical): {e}")

    # ── Check 3: Data feed freshness ─────────────────────────────────────
    now_uk_time = datetime.now(_UK_TZ).time()
    in_market_hours = _MARKET_HOURS_START <= now_uk_time <= _MARKET_HOURS_END

    if not in_market_hours:
        result.add_pass("DATA_FEED_FRESH", "Outside market hours (06:00-22:00 UK), freshness check bypassed")
    elif data_feed_last_ts is not None:
        age = time.time() - data_feed_last_ts
        if age < _DATA_FEED_STALE_SEC:
            result.add_pass("DATA_FEED_FRESH", f"Last update {age:.0f}s ago")
        else:
            result.add_warn("DATA_FEED_FRESH", f"Data feed stale: {age:.0f}s ago (>{_DATA_FEED_STALE_SEC}s)")
    else:
        # No data feed timestamp provided — warn during market hours
        result.add_warn("DATA_FEED_FRESH", "No data feed timestamp available at startup")

    # ── Check 4: Kill switch OFF ─────────────────────────────────────────
    try:
        if redis_client is not None:
            kill_data = redis_client.hgetall("nzt:kill")
            if kill_data and kill_data.get("active") == "1":
                reason = kill_data.get("reason", "unknown")
                result.add_fail("KILL_SWITCH", f"Kill switch ACTIVE: {reason}")
            else:
                result.add_pass("KILL_SWITCH", "Kill switch OFF")
        else:
            # Without Redis, cannot check persisted kill state — warn
            result.add_warn("KILL_SWITCH", "Cannot verify kill switch (no Redis)")
    except Exception as e:
        result.add_warn("KILL_SWITCH", f"Kill switch check failed: {e}")

    # ── Check 5: Circuit breaker GREEN/YELLOW ────────────────────────────
    try:
        if circuit_breakers is not None:
            cb_status = circuit_breakers.get_status()
            if cb_status.get("halted_for_session", False):
                halt_reason = cb_status.get("halt_reason", "unknown")
                result.add_fail("CIRCUIT_BREAKER", f"Circuit breaker HALTED: {halt_reason}")
            else:
                result.add_pass("CIRCUIT_BREAKER", "Circuit breaker GREEN/YELLOW")
        else:
            result.add_warn("CIRCUIT_BREAKER", "CircuitBreakerSystem not provided")
    except Exception as e:
        result.add_warn("CIRCUIT_BREAKER", f"Circuit breaker check failed: {e}")

    # ── Check 6: Disk space >20% free ────────────────────────────────────
    try:
        disk = shutil.disk_usage("/")
        free_pct = disk.free / disk.total
        free_gb = disk.free / (1024**3)
        if free_pct >= _DISK_FREE_PCT_MIN:
            result.add_pass("DISK_SPACE", f"{free_pct:.1%} free ({free_gb:.1f} GB)")
        else:
            result.add_fail("DISK_SPACE", f"Only {free_pct:.1%} free ({free_gb:.1f} GB), need >{_DISK_FREE_PCT_MIN:.0%}")
    except Exception as e:
        result.add_warn("DISK_SPACE", f"Disk check failed: {e}")

    # ── Check 7: Memory >500MB free ──────────────────────────────────────
    free_mb = _get_free_memory_mb()
    if free_mb is not None:
        if free_mb >= _MEMORY_FREE_MB_MIN:
            result.add_pass("MEMORY", f"{free_mb:.0f} MB available")
        else:
            result.add_fail("MEMORY", f"Only {free_mb:.0f} MB available, need >{_MEMORY_FREE_MB_MIN} MB")
    else:
        result.add_warn("MEMORY", "Could not determine available memory")

    # ── Check 8: Time sync <5s drift ─────────────────────────────────────
    ntp_offset = _get_ntp_offset()
    if ntp_offset is not None:
        abs_drift = abs(ntp_offset)
        if abs_drift < _NTP_DRIFT_MAX_SEC:
            result.add_pass("TIME_SYNC", f"NTP offset {ntp_offset:+.2f}s")
        else:
            result.add_fail("TIME_SYNC", f"NTP drift {abs_drift:.2f}s exceeds {_NTP_DRIFT_MAX_SEC}s limit")
    else:
        result.add_warn("TIME_SYNC", "NTP query failed (all servers unreachable)")

    # ── Check 9: IB Gateway connectivity (H-01) ─────────────────────────
    # TCP socket probe to IB Gateway host/port. WARNING only (not FAIL)
    # because paper mode may not always require IB connectivity.
    ib_host = os.environ.get("IBKR_HOST", "ib-gateway")
    ib_port = int(os.environ.get("IBKR_PORT", "4004"))
    try:
        ib_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ib_sock.settimeout(5.0)
        ib_sock.connect((ib_host, ib_port))
        ib_sock.close()
        result.add_pass("IB_GATEWAY", f"TCP connection to {ib_host}:{ib_port} OK")
    except Exception as e:
        result.add_warn("IB_GATEWAY", f"Cannot reach IB Gateway at {ib_host}:{ib_port}: {e}")

    return result


def enforce_startup_gate(
    db_path: Optional[Path] = None,
    redis_client=None,
    circuit_breakers=None,
    data_feed_last_ts: Optional[float] = None,
) -> StartupGateResult:
    """Run the startup gate and enforce the result.

    HALTED = log critical + sys.exit(1).
    DEGRADED = log warnings, return result.
    READY = log info, return result.
    """
    gate_result = run_startup_gate(
        db_path=db_path,
        redis_client=redis_client,
        circuit_breakers=circuit_breakers,
        data_feed_last_ts=data_feed_last_ts,
    )

    # Log the full summary
    summary = gate_result.summary()

    if gate_result.status == StartupGateResult.HALTED:
        logger.critical("STARTUP_GATE_HALTED:\n%s", summary)
        for fail in gate_result.critical_failures:
            logger.critical("  CRITICAL: %s", fail)
        print("\n" + "=" * 60, file=sys.stderr)
        print("  NZT-48 STARTUP GATE: HALTED", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        for fail in gate_result.critical_failures:
            print(f"  [XX] {fail}", file=sys.stderr)
        print("=" * 60 + "\n", file=sys.stderr)
        sys.exit(1)

    elif gate_result.status == StartupGateResult.DEGRADED:
        logger.warning("STARTUP_GATE_DEGRADED:\n%s", summary)
        for warn in gate_result.warnings:
            logger.warning("  DEGRADED: %s", warn)
    else:
        logger.info("STARTUP_GATE_READY:\n%s", summary)

    return gate_result
