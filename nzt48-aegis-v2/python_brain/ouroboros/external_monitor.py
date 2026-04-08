"""N10b — External Monitoring Module for AEGIS V2.

Deep health checks beyond bridge_health.py (RT2). Monitors:
  1. Engine process alive + telemetry freshness
  2. Disk space (Docker volumes)
  3. IBKR connection status (via telemetry)
  4. Trade flow health (drought detection, stuck positions)
  5. Cron job health (last-run timestamps)
  6. Redis connectivity
  7. Python bridge subprocess health

Runs every 5 minutes via cron. Sends graduated Telegram alerts:
  - INFO: daily summary (08:00 UTC)
  - WARNING: degraded state (retry before alerting)
  - CRITICAL: engine down, disk full, IBKR disconnected

Also writes /app/data/monitor_status.json for the kill switch /status command.

Usage:
    python3 -m python_brain.ouroboros.external_monitor           # Single check
    python3 -m python_brain.ouroboros.external_monitor --daily    # Daily report
    python3 -m python_brain.ouroboros.external_monitor --json     # JSON output
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Monitor] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("external_monitor")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", "/app/events"))
LOG_DIR = Path("/var/log")
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", "/app/config"))

TELEMETRY_FILE = WAL_DIR / "telemetry_snapshot.json"
BRIDGE_HEALTH_FILE = DATA_DIR / "bridge_health.json"
MONITOR_STATUS_FILE = DATA_DIR / "monitor_status.json"
MONITOR_HISTORY_FILE = DATA_DIR / "monitor_checks.ndjson"
ALERT_COOLDOWN_FILE = DATA_DIR / "monitor_alert_cooldown.txt"

REDIS_URL = os.environ.get("REDIS_URL", "redis://aegis-redis:6379/0")

# Thresholds
DISK_WARN_PCT = 80       # Warn when disk > 80% full
DISK_CRIT_PCT = 90       # Critical when disk > 90% full
TELEMETRY_STALE_S = 300  # Telemetry > 5 min old = stale
ENGINE_DOWN_S = 120      # Engine PID gone for > 2 min = dead
ALERT_COOLDOWN_S = 600   # 10 min between repeat alerts
CRON_STALE_HOURS = 26    # Cron log not updated in 26h = missed


# ---------------------------------------------------------------------------
# Check functions (each returns a check result dict)
# ---------------------------------------------------------------------------
def check_engine_process() -> Dict[str, Any]:
    """Check if the aegis engine process is alive."""
    check = {"name": "engine_process", "status": "ok", "detail": ""}
    try:
        result = subprocess.run(
            ["pgrep", "-x", "aegis"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            pid = int(result.stdout.strip().split()[0])
            check["detail"] = f"PID {pid}"
        else:
            check["status"] = "critical"
            check["detail"] = "Engine process not found"
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        check["status"] = "warning"
        check["detail"] = "Could not check process (pgrep unavailable)"
    return check


def check_telemetry() -> Dict[str, Any]:
    """Check telemetry snapshot freshness and contents."""
    check = {"name": "telemetry", "status": "ok", "detail": "", "data": {}}
    if not TELEMETRY_FILE.exists():
        check["status"] = "warning"
        check["detail"] = "No telemetry file found"
        return check

    try:
        age_s = time.time() - TELEMETRY_FILE.stat().st_mtime
        with open(TELEMETRY_FILE) as f:
            data = json.load(f)
        check["data"] = data

        if age_s > TELEMETRY_STALE_S:
            check["status"] = "critical"
            check["detail"] = f"Stale ({age_s:.0f}s old, threshold {TELEMETRY_STALE_S}s)"
        else:
            positions = data.get("positions", 0)
            regime = data.get("regime", "?")
            equity = data.get("equity", 0)
            check["detail"] = (
                f"Fresh ({age_s:.0f}s), regime={regime}, "
                f"positions={positions}, equity={equity:.2f}"
            )
    except (json.JSONDecodeError, OSError) as e:
        check["status"] = "warning"
        check["detail"] = f"Failed to read: {e}"
    return check


def check_disk_space() -> Dict[str, Any]:
    """Check disk space on the root filesystem."""
    check = {"name": "disk_space", "status": "ok", "detail": ""}
    try:
        usage = shutil.disk_usage("/")
        pct_used = (usage.used / usage.total) * 100
        free_gb = usage.free / (1024 ** 3)
        check["detail"] = f"{pct_used:.1f}% used, {free_gb:.1f} GB free"

        if pct_used > DISK_CRIT_PCT:
            check["status"] = "critical"
        elif pct_used > DISK_WARN_PCT:
            check["status"] = "warning"
    except OSError as e:
        check["status"] = "warning"
        check["detail"] = f"Check failed: {e}"
    return check


def check_redis() -> Dict[str, Any]:
    """Check Redis connectivity."""
    check = {"name": "redis", "status": "ok", "detail": ""}
    try:
        import redis
        # Parse URL: redis://:password@host:port/db
        r = redis.from_url(REDIS_URL, socket_timeout=5)
        info = r.info("memory")
        used_mb = info.get("used_memory", 0) / (1024 * 1024)
        check["detail"] = f"Connected, {used_mb:.1f} MB used"
        r.close()
    except ImportError:
        check["status"] = "warning"
        check["detail"] = "redis-py not installed"
    except Exception as e:
        check["status"] = "critical"
        check["detail"] = f"Connection failed: {e}"
    return check


def check_bridge_health() -> Dict[str, Any]:
    """Check Python bridge health from RT2 data."""
    check = {"name": "bridge_health", "status": "ok", "detail": ""}
    if not BRIDGE_HEALTH_FILE.exists():
        # Not critical — bridge_health.json may not exist if engine doesn't write it yet
        check["status"] = "info"
        check["detail"] = "No bridge health file (normal if engine doesn't write it)"
        return check

    try:
        age_s = time.time() - BRIDGE_HEALTH_FILE.stat().st_mtime
        with open(BRIDGE_HEALTH_FILE) as f:
            data = json.load(f)
        status = data.get("status", "unknown")
        errors = data.get("consecutive_errors", 0)

        if status in ("healthy",):
            check["detail"] = f"Healthy (age {age_s:.0f}s, errors={errors})"
        elif status in ("stale", "degraded", "drought"):
            check["status"] = "warning"
            check["detail"] = f"{status} (age {age_s:.0f}s, errors={errors})"
        else:
            check["status"] = "critical"
            check["detail"] = f"{status} (age {age_s:.0f}s, errors={errors})"
    except (json.JSONDecodeError, OSError) as e:
        check["status"] = "warning"
        check["detail"] = f"Read error: {e}"
    return check


def check_cron_jobs() -> Dict[str, Any]:
    """Check that key cron jobs have run recently."""
    check = {"name": "cron_jobs", "status": "ok", "detail": "", "data": {}}
    # Key log files and their expected freshness
    cron_logs = {
        "ouroboros": LOG_DIR / "ouroboros.log",
        "config_writer": LOG_DIR / "config_writer.log",
        "ticker_selector": LOG_DIR / "ticker_selector_15m.log",
        "bridge_health": LOG_DIR / "bridge_health.log",
    }

    stale = []
    for name, path in cron_logs.items():
        if not path.exists():
            stale.append(f"{name}(missing)")
            continue
        age_h = (time.time() - path.stat().st_mtime) / 3600
        check["data"][name] = f"{age_h:.1f}h ago"
        if age_h > CRON_STALE_HOURS:
            stale.append(f"{name}({age_h:.0f}h)")

    if stale:
        check["status"] = "warning"
        check["detail"] = f"Stale: {', '.join(stale)}"
    else:
        check["detail"] = f"All {len(cron_logs)} key cron jobs recent"
    return check


def check_wal_files() -> Dict[str, Any]:
    """Check WAL directory health."""
    check = {"name": "wal_files", "status": "ok", "detail": ""}
    if not WAL_DIR.exists():
        check["status"] = "critical"
        check["detail"] = "WAL directory missing"
        return check

    ndjson_files = list(WAL_DIR.glob("*.ndjson"))
    total_size_mb = sum(f.stat().st_size for f in ndjson_files) / (1024 * 1024)

    # Find most recent WAL write
    if ndjson_files:
        newest = max(ndjson_files, key=lambda f: f.stat().st_mtime)
        age_s = time.time() - newest.stat().st_mtime
        check["detail"] = (
            f"{len(ndjson_files)} files, {total_size_mb:.1f} MB, "
            f"latest={newest.name} ({age_s:.0f}s ago)"
        )
        if total_size_mb > 500:
            check["status"] = "warning"
            check["detail"] += " — WAL growing large, consider archival"
    else:
        check["detail"] = "No WAL files (engine may not have started)"
    return check


def check_kill_switch() -> Dict[str, Any]:
    """Check N10a kill switch state."""
    check = {"name": "kill_switch", "status": "ok", "detail": ""}
    kill_file = DATA_DIR / "KILL"
    pause_file = DATA_DIR / "PAUSE"

    if kill_file.exists():
        check["status"] = "critical"
        check["detail"] = "KILL file present — engine should be shutting down"
    elif pause_file.exists():
        check["status"] = "warning"
        check["detail"] = "PAUSED — signal generation frozen"
    else:
        check["detail"] = "Active (no KILL/PAUSE)"
    return check


def check_ibkr_connection() -> Dict[str, Any]:
    """Check IBKR connection status from telemetry."""
    check = {"name": "ibkr_connection", "status": "ok", "detail": ""}
    if not TELEMETRY_FILE.exists():
        check["status"] = "warning"
        check["detail"] = "No telemetry to check"
        return check

    try:
        with open(TELEMETRY_FILE) as f:
            data = json.load(f)
        sub_lines = data.get("sub_lines", 0)
        ticks = data.get("ticks_received", 0)

        if sub_lines == 0 and ticks == 0:
            check["status"] = "critical"
            check["detail"] = "No subscriptions and no ticks — IBKR disconnected"
        elif sub_lines == 0:
            check["status"] = "warning"
            check["detail"] = f"No subscriptions but {ticks} ticks received"
        else:
            check["detail"] = f"{sub_lines} subscriptions, {ticks} ticks received"
    except (json.JSONDecodeError, OSError):
        check["status"] = "warning"
        check["detail"] = "Could not read telemetry"
    return check


def check_independent_risk() -> Dict[str, Any]:
    """SC-05: Independent risk monitoring — drawdown, equity floor, position age.

    This runs INDEPENDENTLY of the engine's internal risk arbiter, providing
    a second pair of eyes on portfolio risk. If the engine's risk checks fail
    or are bypassed, this monitor will catch it and alert via Telegram.
    """
    check = {"name": "independent_risk", "status": "ok", "detail": "", "data": {}}
    if not TELEMETRY_FILE.exists():
        check["status"] = "info"
        check["detail"] = "No telemetry available"
        return check

    try:
        with open(TELEMETRY_FILE) as f:
            data = json.load(f)

        equity = data.get("equity", 0)
        initial_equity = data.get("initial_equity", 10000)
        positions = data.get("positions", 0)
        regime = data.get("regime", "Normal")
        drawdown_pct = data.get("drawdown_pct", 0)

        details = []

        # CHECK R1: Drawdown breach — independent of Rust arbiter
        if drawdown_pct > 8.0:
            check["status"] = "critical"
            details.append(f"DRAWDOWN {drawdown_pct:.1f}% > 8% SACRED LIMIT")
            # Write KILL file to force engine shutdown
            kill_file = DATA_DIR / "KILL"
            if not kill_file.exists():
                try:
                    kill_file.write_text(f"INDEPENDENT_RISK: drawdown {drawdown_pct:.1f}%")
                    details.append("KILL file written — engine will shutdown")
                except OSError:
                    details.append("WARNING: could not write KILL file")
        elif drawdown_pct > 5.0:
            check["status"] = "warning"
            details.append(f"Drawdown {drawdown_pct:.1f}% approaching limit")

        # CHECK R2: Equity floor breach (70% of initial)
        if initial_equity > 0 and equity > 0:
            equity_ratio = equity / initial_equity
            check["data"]["equity_ratio"] = round(equity_ratio, 3)
            if equity_ratio < 0.70:
                check["status"] = "critical"
                details.append(f"EQUITY FLOOR BREACH: {equity:.0f} < 70% of {initial_equity:.0f}")
            elif equity_ratio < 0.80:
                if check["status"] != "critical":
                    check["status"] = "warning"
                details.append(f"Equity {equity:.0f} = {equity_ratio*100:.1f}% of initial")

        # CHECK R3: Regime sanity — if regime is HALT/FLATTEN but positions exist
        if regime in ("Halt", "Flatten") and positions > 0:
            if check["status"] != "critical":
                check["status"] = "warning"
            details.append(f"Regime={regime} but {positions} positions still open")

        # CHECK R4: Excessive position count
        if positions > 5:
            if check["status"] != "critical":
                check["status"] = "warning"
            details.append(f"{positions} open positions (limit 3, max 5)")

        if not details:
            details.append(f"OK: equity={equity:.0f}, dd={drawdown_pct:.1f}%, {positions} pos, regime={regime}")

        check["detail"] = "; ".join(details)
        check["data"]["drawdown_pct"] = drawdown_pct
        check["data"]["equity"] = equity
        check["data"]["positions"] = positions
        check["data"]["regime"] = regime

    except (json.JSONDecodeError, OSError, KeyError) as e:
        check["status"] = "warning"
        check["detail"] = f"Risk check failed: {e}"
    return check


# ---------------------------------------------------------------------------
# Main monitoring orchestrator
# ---------------------------------------------------------------------------
def run_full_check() -> Dict[str, Any]:
    """Run all health checks and return comprehensive status."""
    checks = [
        check_engine_process(),
        check_telemetry(),
        check_disk_space(),
        check_redis(),
        check_bridge_health(),
        check_cron_jobs(),
        check_wal_files(),
        check_kill_switch(),
        check_ibkr_connection(),
        check_independent_risk(),
    ]

    # Determine overall status
    statuses = [c["status"] for c in checks]
    if "critical" in statuses:
        overall = "critical"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "healthy"

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall,
        "checks": checks,
        "critical_count": statuses.count("critical"),
        "warning_count": statuses.count("warning"),
    }

    # Write status file for other modules to read
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        tmp = MONITOR_STATUS_FILE.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(result, f, indent=2, default=str)
        os.rename(str(tmp), str(MONITOR_STATUS_FILE))
    except OSError:
        pass

    # Append to history (compact — no telemetry data blob)
    compact = {
        "ts": result["timestamp"],
        "status": overall,
        "checks": {c["name"]: c["status"] for c in checks},
    }
    try:
        with open(MONITOR_HISTORY_FILE, "a") as f:
            f.write(json.dumps(compact) + "\n")
    except OSError:
        pass

    return result


def should_alert(severity: str) -> bool:
    """Check alert cooldown. Critical always alerts; warning has cooldown."""
    if severity == "critical":
        return True  # Always alert on critical
    if not ALERT_COOLDOWN_FILE.exists():
        return True
    try:
        age_s = time.time() - ALERT_COOLDOWN_FILE.stat().st_mtime
        return age_s > ALERT_COOLDOWN_S
    except OSError:
        return True


def mark_alerted():
    """Touch cooldown file."""
    try:
        ALERT_COOLDOWN_FILE.write_text(datetime.now(timezone.utc).isoformat())
    except OSError:
        pass


def send_alert(result: Dict[str, Any]):
    """Send Telegram alert for degraded or critical states."""
    severity = result["overall_status"]
    if severity == "healthy":
        return

    if not should_alert(severity):
        log.info("Alert suppressed (cooldown active)")
        return

    # Build alert message
    icons = {"critical": "\u26a0\ufe0f", "warning": "\U0001f7e1"}
    icon = icons.get(severity, "\u2139\ufe0f")

    lines = [
        f"{icon} <b>AEGIS MONITOR [{severity.upper()}]</b>",
        "",
    ]

    for check in result["checks"]:
        if check["status"] in ("critical", "warning"):
            check_icon = "\U0001f534" if check["status"] == "critical" else "\U0001f7e1"
            lines.append(f"{check_icon} <b>{check['name']}</b>: {check['detail']}")

    lines.append(f"\n<i>{result['timestamp']}</i>")
    message = "\n".join(lines)

    try:
        from python_brain.ouroboros.telegram_notify import send_message
        send_message(message)
        log.info("Alert sent via Telegram")
    except ImportError:
        log.warning("Telegram not available — alert: %s", message)
    except Exception as e:
        log.error("Telegram send failed: %s", e)

    mark_alerted()


def format_daily_report(result: Dict[str, Any]) -> str:
    """Format a daily health summary."""
    lines = [
        "\U0001f4ca <b>AEGIS DAILY HEALTH REPORT</b>",
        "",
        f"Overall: <b>{result['overall_status'].upper()}</b>",
        f"Checks: {len(result['checks'])} total, "
        f"{result['critical_count']} critical, {result['warning_count']} warning",
        "",
    ]

    for check in result["checks"]:
        icons = {"ok": "\u2705", "critical": "\U0001f534", "warning": "\U0001f7e1",
                 "info": "\u2139\ufe0f"}
        icon = icons.get(check["status"], "\u2753")
        lines.append(f"{icon} <b>{check['name']}</b>: {check['detail']}")

    # Add trade summary from telemetry if available
    for check in result["checks"]:
        if check["name"] == "telemetry" and check.get("data"):
            t = check["data"]
            lines.extend([
                "",
                "<b>Engine Metrics:</b>",
                f"  Ticks: {t.get('ticks_received', '?')} received, {t.get('ticks_filtered', '?')} filtered",
                f"  Signals: {t.get('signals_generated', '?')} generated, "
                f"{t.get('signals_approved', '?')} approved, {t.get('signals_vetoed', '?')} vetoed",
                f"  Latency: p50={t.get('t2t_p50_ms', '?')}ms, p95={t.get('t2t_p95_ms', '?')}ms",
            ])
            break

    lines.append(f"\n<i>Generated {result['timestamp']}</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI + Cron entry point
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="AEGIS V2 External Monitor (N10b)")
    parser.add_argument("--daily", action="store_true",
                        help="Send daily health report via Telegram")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON to stdout")
    parser.add_argument("--quiet", action="store_true",
                        help="Only alert on issues (suppress normal output)")
    args = parser.parse_args()

    result = run_full_check()

    if args.json:
        # Strip telemetry data blob for compact output
        for check in result["checks"]:
            check.pop("data", None)
        print(json.dumps(result, indent=2, default=str))
        return

    if args.daily:
        report = format_daily_report(result)
        try:
            from python_brain.ouroboros.telegram_notify import send_message
            send_message(report)
            log.info("Daily report sent")
        except Exception as e:
            log.error("Failed to send daily report: %s", e)
            print(report)
        return

    # Normal cron mode: check + alert if degraded
    if result["overall_status"] == "healthy":
        if not args.quiet:
            log.info(
                "All checks OK (%d checks, 0 issues)",
                len(result["checks"]),
            )
    else:
        log.warning(
            "Status: %s (%d critical, %d warning)",
            result["overall_status"],
            result["critical_count"],
            result["warning_count"],
        )
        send_alert(result)

    # Print summary
    if not args.quiet:
        for check in result["checks"]:
            icon = {"ok": "OK", "critical": "CRIT", "warning": "WARN",
                    "info": "INFO"}.get(check["status"], "?")
            print(f"  [{icon:>4}] {check['name']:<20} {check['detail']}")


if __name__ == "__main__":
    main()
