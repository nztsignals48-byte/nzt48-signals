"""
NZT-48 Trading System -- ScanHealth Heartbeat Tracker
======================================================
Thread-safe singleton that tracks the health of the continuous scan loop.

State machine:
    OK        -- All ticks < 120s, no recent errors.
    DEGRADED  -- Tick stale (> 120s) OR isolated error.
    HALTED    -- Kill switch activated OR 5+ consecutive errors.

Usage:
    from core.scan_health import ScanHealthTracker
    tracker = ScanHealthTracker.instance()
    tracker.record_tick()
    tracker.record_engine_run(signals_emitted=3, signals_logged=3)
    health = tracker.get_health()   # -> ScanHealth dataclass
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.schemas import ScanHealth

logger = logging.getLogger("nzt48.core.scan_health")

# Thresholds
_TICK_STALE_SECONDS = 120.0         # Tick older than this -> DEGRADED
_CONSECUTIVE_ERROR_HALT = 5         # This many consecutive errors -> HALTED
_DEFAULT_PERSIST_PATH = "data/scan_health.json"


class ScanHealthTracker:
    """Thread-safe singleton heartbeat tracker for the scan engine.

    The tracker maintains running counters and timestamps that are
    queried by the dashboard, Telegram alerter, and system watchdog.

    Thread safety is provided by a threading.Lock around all mutable
    state mutations and reads.
    """

    _instance: Optional[ScanHealthTracker] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tick_count: int = 0
        self._engine_runs: int = 0
        self._signals_emitted: int = 0
        self._signals_logged: int = 0
        self._last_tick_ts: float = 0.0             # time.time() of last tick
        self._last_success_ts: str = ""
        self._last_error_ts: str = ""
        self._last_error_msg: str = ""
        self._consecutive_errors: int = 0
        self._kill_switch: bool = False
        self._start_time: float = time.time()

    @classmethod
    def instance(cls) -> ScanHealthTracker:
        """Return the global singleton instance (lazy init, thread-safe)."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (for testing only)."""
        with cls._instance_lock:
            cls._instance = None

    # ------------------------------------------------------------------
    # Recording methods
    # ------------------------------------------------------------------

    def record_tick(self) -> None:
        """Record a successful scan tick (heartbeat).

        Called by the main engine loop every 60s (or whatever the scan
        interval is) to indicate the engine is alive and scanning.
        """
        with self._lock:
            self._tick_count += 1
            self._last_tick_ts = time.time()
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            self._last_success_ts = now_iso
            # A successful tick breaks the consecutive error chain
            self._consecutive_errors = 0
            logger.debug("Scan tick #%d recorded", self._tick_count)

    def record_engine_run(self, signals_emitted: int = 0, signals_logged: int = 0) -> None:
        """Record a completed engine run with signal counts.

        Args:
            signals_emitted: Number of signals generated in this run.
            signals_logged:  Number of signals successfully written to disk/DB.
        """
        with self._lock:
            self._engine_runs += 1
            self._signals_emitted += max(0, signals_emitted)
            self._signals_logged += max(0, signals_logged)
            self._consecutive_errors = 0
            self._last_success_ts = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            logger.debug(
                "Engine run #%d: emitted=%d, logged=%d",
                self._engine_runs, signals_emitted, signals_logged,
            )

    def record_error(self, msg: str) -> None:
        """Record a scan error.

        Args:
            msg: Human-readable error description.
        """
        with self._lock:
            self._consecutive_errors += 1
            self._last_error_ts = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            self._last_error_msg = str(msg)[:500]  # Truncate to prevent bloat
            logger.warning(
                "Scan error #%d (consecutive=%d): %s",
                self._engine_runs, self._consecutive_errors, msg[:200],
            )

    def activate_kill_switch(self, reason: str = "") -> None:
        """Manually halt the engine via kill switch.

        Args:
            reason: Why the kill switch was activated.
        """
        with self._lock:
            self._kill_switch = True
            self._last_error_msg = f"KILL SWITCH: {reason}" if reason else "KILL SWITCH activated"
            self._last_error_ts = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            logger.critical("KILL SWITCH activated: %s", reason)

    def deactivate_kill_switch(self) -> None:
        """Deactivate the kill switch (manual recovery)."""
        with self._lock:
            self._kill_switch = False
            self._consecutive_errors = 0
            logger.warning("Kill switch deactivated -- engine may resume")

    # ------------------------------------------------------------------
    # State query
    # ------------------------------------------------------------------

    def get_health(self) -> ScanHealth:
        """Return the current health snapshot as a ScanHealth dataclass.

        State transitions:
            OK       -> Tick age < 120s AND consecutive_errors == 0 AND no kill switch.
            DEGRADED -> Tick age >= 120s OR 1-4 consecutive errors.
            HALTED   -> Kill switch active OR >= 5 consecutive errors.
        """
        with self._lock:
            now = time.time()
            tick_age = (now - self._last_tick_ts) if self._last_tick_ts > 0 else 0.0
            uptime = now - self._start_time

            # Determine state
            if self._kill_switch:
                state = "HALTED"
            elif self._consecutive_errors >= _CONSECUTIVE_ERROR_HALT:
                state = "HALTED"
            elif tick_age > _TICK_STALE_SECONDS and self._tick_count > 0:
                state = "DEGRADED"
            elif self._consecutive_errors > 0:
                state = "DEGRADED"
            else:
                state = "OK"

            return ScanHealth(
                tick_count=self._tick_count,
                engine_runs=self._engine_runs,
                signals_emitted=self._signals_emitted,
                signals_logged=self._signals_logged,
                last_success_ts=self._last_success_ts,
                last_error_ts=self._last_error_ts,
                last_error_msg=self._last_error_msg,
                state=state,
                uptime_seconds=round(uptime, 1),
            )

    @property
    def is_halted(self) -> bool:
        """Quick check: is the engine in HALTED state?"""
        return self.get_health().state == "HALTED"

    @property
    def tick_age_seconds(self) -> float:
        """Seconds since the last successful tick."""
        with self._lock:
            if self._last_tick_ts == 0:
                return 0.0
            return time.time() - self._last_tick_ts

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str = _DEFAULT_PERSIST_PATH) -> None:
        """Persist the current health state to a JSON file.

        Args:
            path: Filesystem path for the JSON output.
        """
        health = self.get_health()
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(health.to_dict(), f, indent=2, default=str)
            logger.debug("ScanHealth saved to %s", p)
        except Exception as e:
            logger.error("Failed to save ScanHealth to %s: %s", p, e)

    def load(self, path: str = _DEFAULT_PERSIST_PATH) -> None:
        """Load health state from a JSON file (for cold-start recovery).

        Only restores cumulative counters (tick_count, engine_runs,
        signals_emitted, signals_logged). State is always recomputed
        fresh from current conditions.

        Args:
            path: Filesystem path to the JSON file.
        """
        p = Path(path)
        if not p.exists():
            logger.info("No persisted ScanHealth at %s -- starting fresh", p)
            return

        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to load ScanHealth from %s: %s", p, e)
            return

        with self._lock:
            self._tick_count = max(self._tick_count, data.get("tick_count", 0))
            self._engine_runs = max(self._engine_runs, data.get("engine_runs", 0))
            self._signals_emitted = max(
                self._signals_emitted, data.get("signals_emitted", 0)
            )
            self._signals_logged = max(
                self._signals_logged, data.get("signals_logged", 0)
            )
            self._last_success_ts = data.get("last_success_ts", self._last_success_ts)
            self._last_error_ts = data.get("last_error_ts", self._last_error_ts)
            self._last_error_msg = data.get("last_error_msg", self._last_error_msg)

        logger.info(
            "ScanHealth restored from %s: ticks=%d, runs=%d",
            p, self._tick_count, self._engine_runs,
        )
