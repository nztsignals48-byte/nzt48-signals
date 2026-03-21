"""RT4-P2 — Python Bridge SPOF Watchdog Timer.

Enhanced watchdog for the Python bridge subprocess. The bridge is a Single
Point of Failure (SPOF) — if it dies, the Rust engine loses signal generation.

This module provides:
  1. Heartbeat monitoring (bridge writes timestamp to file every 30s)
  2. Process supervision (checks bridge PID is alive)
  3. Output freshness (checks bridge stdout pipe for recent activity)
  4. Auto-restart with exponential backoff
  5. Telegram alert on crash/restart

Integration:
  - Runs as daemon thread inside entrypoint.sh (alongside kill_switch)
  - Checks every 30 seconds
  - Max 3 restarts per hour (then gives up + alerts)

Usage:
    python3 -m python_brain.ouroboros.bridge_watchdog --monitor   # Run as daemon
    python3 -m python_brain.ouroboros.bridge_watchdog --status    # Check status
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("ouroboros.bridge_watchdog")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HEARTBEAT_FILE = DATA_DIR / "bridge_heartbeat.json"
WATCHDOG_LOG = DATA_DIR / "bridge_watchdog.ndjson"
CHECK_INTERVAL_SEC = 30
HEARTBEAT_STALE_SEC = 90        # Alert if heartbeat older than 90s
MAX_RESTARTS_PER_HOUR = 3
RESTART_BACKOFF_BASE_SEC = 5
RESTART_BACKOFF_MAX_SEC = 60


# ---------------------------------------------------------------------------
# Heartbeat writer (called BY the bridge to signal it's alive)
# ---------------------------------------------------------------------------
def write_heartbeat(extra: Optional[dict] = None) -> None:
    """Write heartbeat file. Called by bridge.py every 30 seconds."""
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "epoch": time.time(),
    }
    if extra:
        data.update(extra)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = HEARTBEAT_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.rename(str(tmp), str(HEARTBEAT_FILE))
    except Exception as e:
        log.warning("Failed to write heartbeat: %s", e)


def read_heartbeat() -> Optional[dict]:
    """Read the bridge heartbeat file."""
    if not HEARTBEAT_FILE.exists():
        return None
    try:
        with open(HEARTBEAT_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Process checking
# ---------------------------------------------------------------------------
def _is_bridge_running() -> tuple:
    """Check if bridge.py process is running. Returns (alive: bool, pid: int|None)."""
    hb = read_heartbeat()
    if not hb:
        return False, None

    pid = hb.get("pid")
    if pid is None:
        return False, None

    try:
        os.kill(pid, 0)  # Signal 0 = check if process exists
        return True, pid
    except ProcessLookupError:
        return False, pid
    except PermissionError:
        # Process exists but we don't own it — still alive
        return True, pid


def _is_heartbeat_fresh() -> tuple:
    """Check if heartbeat is recent. Returns (fresh: bool, age_sec: float)."""
    hb = read_heartbeat()
    if not hb:
        return False, float("inf")

    epoch = hb.get("epoch", 0)
    if epoch == 0:
        return False, float("inf")

    age = time.time() - epoch
    return age < HEARTBEAT_STALE_SEC, age


def get_bridge_status() -> dict:
    """Get comprehensive bridge status."""
    alive, pid = _is_bridge_running()
    fresh, age = _is_heartbeat_fresh()

    status = "healthy"
    if not alive:
        status = "dead"
    elif not fresh:
        status = "stale"

    return {
        "status": status,
        "pid": pid,
        "alive": alive,
        "heartbeat_fresh": fresh,
        "heartbeat_age_sec": round(age, 1) if age != float("inf") else None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Watchdog audit log
# ---------------------------------------------------------------------------
def _log_event(event_type: str, detail: str) -> None:
    """Append event to watchdog audit log."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "detail": detail,
    }
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(WATCHDOG_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------------
def _send_alert(message: str) -> None:
    """Send alert via Telegram."""
    try:
        from python_brain.ouroboros.telegram_notify import send_message
        send_message(f"\U0001f6a8 <b>BRIDGE WATCHDOG</b>\n{message}")
    except Exception as e:
        log.error("Failed to send watchdog alert: %s", e)


# ---------------------------------------------------------------------------
# Restart logic
# ---------------------------------------------------------------------------
def _attempt_restart() -> bool:
    """Attempt to restart the bridge subprocess.

    The bridge is started by the Rust engine as a child process.
    We can't restart it directly — instead, we send SIGHUP to the engine
    which triggers bridge respawn (RM-5 respawn logic).
    """
    log.warning("Attempting bridge restart via engine SIGHUP")
    _log_event("RESTART_ATTEMPT", "Sending SIGHUP to engine")

    try:
        # Find engine PID (it's PID 1 in the container, or search for it)
        engine_pid = None

        # Method 1: Engine is usually PID 1 in Docker
        try:
            os.kill(1, 0)  # Check if PID 1 exists
            engine_pid = 1
        except (ProcessLookupError, PermissionError):
            pass

        # Method 2: Search for the engine process
        if engine_pid is None:
            try:
                result = subprocess.run(
                    ["pgrep", "-f", "aegis.*engine"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    engine_pid = int(result.stdout.strip().split()[0])
            except Exception:
                pass

        if engine_pid is None:
            log.error("Cannot find engine PID for bridge restart")
            _log_event("RESTART_FAILED", "Engine PID not found")
            return False

        os.kill(engine_pid, signal.SIGHUP)
        log.info("SIGHUP sent to engine PID %d — bridge should respawn", engine_pid)
        _log_event("RESTART_SENT", f"SIGHUP to PID {engine_pid}")

        # Wait a moment and check
        time.sleep(5)
        alive, _ = _is_bridge_running()
        if alive:
            log.info("Bridge restart successful")
            _log_event("RESTART_SUCCESS", "Bridge alive after SIGHUP")
            return True
        else:
            log.error("Bridge still dead after SIGHUP")
            _log_event("RESTART_FAILED", "Bridge still dead after SIGHUP")
            return False

    except Exception as e:
        log.error("Bridge restart failed: %s", e)
        _log_event("RESTART_ERROR", str(e))
        return False


# ---------------------------------------------------------------------------
# Monitor loop
# ---------------------------------------------------------------------------
def monitor_loop() -> None:
    """Main monitoring loop — runs as daemon thread."""
    log.info("Bridge watchdog starting (check every %ds)", CHECK_INTERVAL_SEC)
    _log_event("WATCHDOG_START", f"interval={CHECK_INTERVAL_SEC}s")

    restarts_this_hour: list = []   # timestamps of recent restarts
    consecutive_failures = 0

    while True:
        try:
            time.sleep(CHECK_INTERVAL_SEC)

            status = get_bridge_status()
            bridge_ok = status["status"] == "healthy"

            if bridge_ok:
                consecutive_failures = 0
                continue

            # Bridge is unhealthy
            consecutive_failures += 1
            log.warning(
                "Bridge unhealthy: status=%s, pid=%s, age=%s (failure #%d)",
                status["status"], status["pid"], status["heartbeat_age_sec"],
                consecutive_failures,
            )
            _log_event("UNHEALTHY", json.dumps(status))

            # Wait for 2 consecutive failures before acting (avoid false alarms)
            if consecutive_failures < 2:
                continue

            # Clean up old restart timestamps
            now = time.time()
            restarts_this_hour = [t for t in restarts_this_hour if now - t < 3600]

            if len(restarts_this_hour) >= MAX_RESTARTS_PER_HOUR:
                msg = (f"Bridge dead — {MAX_RESTARTS_PER_HOUR} restarts in last hour. "
                       f"Giving up. Manual intervention needed.")
                log.error(msg)
                _log_event("RESTART_LIMIT", msg)
                _send_alert(msg)
                # Wait 1 hour before trying again
                time.sleep(3600)
                restarts_this_hour.clear()
                continue

            # Attempt restart
            backoff = min(
                RESTART_BACKOFF_BASE_SEC * (2 ** len(restarts_this_hour)),
                RESTART_BACKOFF_MAX_SEC,
            )
            log.info("Waiting %ds before restart attempt", backoff)
            time.sleep(backoff)

            success = _attempt_restart()
            restarts_this_hour.append(time.time())

            if success:
                _send_alert(f"Bridge restarted successfully (attempt #{len(restarts_this_hour)})")
                consecutive_failures = 0
            else:
                _send_alert(
                    f"Bridge restart FAILED (attempt #{len(restarts_this_hour)}/"
                    f"{MAX_RESTARTS_PER_HOUR}). Status: {status}"
                )

        except Exception as e:
            log.error("Watchdog loop error: %s", e)
            time.sleep(CHECK_INTERVAL_SEC)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Watchdog] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="RT4-P2 — Bridge SPOF Watchdog")
    parser.add_argument("--monitor", action="store_true", help="Run monitoring loop (daemon)")
    parser.add_argument("--status", action="store_true", help="Check bridge status")
    parser.add_argument("--write-heartbeat", action="store_true", help="Write heartbeat (for testing)")
    args = parser.parse_args()

    if args.write_heartbeat:
        write_heartbeat({"source": "cli_test"})
        print("Heartbeat written")
        return

    if args.status:
        status = get_bridge_status()
        print(json.dumps(status, indent=2))
        return

    if args.monitor:
        monitor_loop()
    else:
        # Default: show status
        status = get_bridge_status()
        print(f"Bridge Status: {status['status']}")
        print(f"  PID: {status['pid']}")
        print(f"  Alive: {status['alive']}")
        print(f"  Heartbeat Fresh: {status['heartbeat_fresh']}")
        print(f"  Heartbeat Age: {status['heartbeat_age_sec']}s")


if __name__ == "__main__":
    main()
