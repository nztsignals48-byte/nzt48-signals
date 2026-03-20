"""
RT2/Q-045 — Python Bridge Health Monitor

Reads bridge health status from /app/data/bridge_health.json (written by Rust engine)
and from engine stderr logs. Sends Telegram alert if bridge is unhealthy.

Designed to run as a cron job every 15 minutes during market hours.
Also callable from nightly_v6.py for daily summary.

Usage:
    python3 -m python_brain.ouroboros.bridge_health
"""
import json
import os
import time
from datetime import datetime, timezone

HEALTH_FILE = "/app/data/bridge_health.json"
ALERT_COOLDOWN_FILE = "/app/data/bridge_alert_cooldown.txt"
ALERT_COOLDOWN_SECS = 1800  # Don't re-alert within 30 minutes


def check_bridge_health() -> dict:
    """
    Check bridge health from the status file written by the Rust engine.
    Returns health dict with status, last_signal_ts, consecutive_errors, etc.
    """
    health = {
        "status": "unknown",
        "last_check": datetime.now(timezone.utc).isoformat(),
        "consecutive_errors": 0,
        "signal_drought_ticks": 0,
        "bridge_alive": False,
        "alerts": [],
    }

    # Read health file if it exists
    if os.path.exists(HEALTH_FILE):
        try:
            with open(HEALTH_FILE, "r") as f:
                data = json.load(f)
            health.update(data)

            # Check staleness — if file hasn't been updated in 5 minutes, bridge may be dead
            mtime = os.path.getmtime(HEALTH_FILE)
            age_secs = time.time() - mtime
            if age_secs > 300:
                health["alerts"].append(
                    f"Health file stale ({age_secs:.0f}s old, threshold 300s)"
                )
                health["status"] = "stale"
            elif health.get("consecutive_errors", 0) > 10:
                health["alerts"].append(
                    f"High consecutive errors: {health['consecutive_errors']}"
                )
                health["status"] = "degraded"
            elif health.get("signal_drought_ticks", 0) > 10000:
                health["alerts"].append(
                    f"Signal drought: {health['signal_drought_ticks']} ticks with no signal"
                )
                health["status"] = "drought"
            else:
                health["status"] = "healthy"
                health["bridge_alive"] = True
        except (json.JSONDecodeError, OSError) as e:
            health["alerts"].append(f"Failed to read health file: {e}")
            health["status"] = "error"
    else:
        health["alerts"].append("No health file found (engine may not write it yet)")
        health["status"] = "no_data"

    return health


def should_alert() -> bool:
    """Check if we're outside the alert cooldown window."""
    if not os.path.exists(ALERT_COOLDOWN_FILE):
        return True
    try:
        mtime = os.path.getmtime(ALERT_COOLDOWN_FILE)
        return (time.time() - mtime) > ALERT_COOLDOWN_SECS
    except OSError:
        return True


def mark_alerted():
    """Touch the cooldown file to prevent re-alerting."""
    try:
        with open(ALERT_COOLDOWN_FILE, "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except OSError:
        pass


def send_alert(health: dict):
    """Send Telegram alert about bridge health issue."""
    if not should_alert():
        print("RT2: Alert suppressed (cooldown active)")
        return

    alerts_str = "; ".join(health.get("alerts", ["unknown issue"]))
    message = (
        f"AEGIS BRIDGE ALERT [{health['status'].upper()}]\n"
        f"Alerts: {alerts_str}\n"
        f"Consecutive errors: {health.get('consecutive_errors', '?')}\n"
        f"Signal drought ticks: {health.get('signal_drought_ticks', '?')}\n"
        f"Time: {health.get('last_check', 'unknown')}"
    )

    # Try to send via Telegram (reuse existing telegram_notify infrastructure)
    try:
        from python_brain.ouroboros.telegram_notify import send_telegram_message

        send_telegram_message(message)
        print(f"RT2: Telegram alert sent — {health['status']}")
    except ImportError:
        print(f"RT2: Telegram not available. Alert: {message}")
    except Exception as e:
        print(f"RT2: Telegram send failed: {e}. Alert: {message}")

    mark_alerted()


def run_health_check():
    """Main entry point for cron job."""
    health = check_bridge_health()

    if health["status"] in ("healthy", "no_data", "unknown"):
        print(f"RT2: Bridge status={health['status']} — no alert needed")
    else:
        print(f"RT2: Bridge status={health['status']} — sending alert")
        send_alert(health)

    # Write check result for nightly consumption
    result_file = "/app/data/bridge_health_checks.ndjson"
    try:
        with open(result_file, "a") as f:
            f.write(json.dumps(health) + "\n")
    except OSError:
        pass

    return health


if __name__ == "__main__":
    run_health_check()
