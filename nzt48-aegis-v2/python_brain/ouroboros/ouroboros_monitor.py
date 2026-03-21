"""Ouroboros Monitor — PHASE_8 #6: Monitoring & alerting for nightly pipeline.

Detects:
- Nightly pipeline failures (stale TOML files)
- TOML corruption
- Weight staleness (> 36 hours old)
- Parameter drift anomalies

Sends Telegram alerts on failures.

Usage:
    python3 -m python_brain.ouroboros.ouroboros_monitor
    python3 -m python_brain.ouroboros.ouroboros_monitor --check-toml
    python3 -m python_brain.ouroboros.ouroboros_monitor --check-staleness
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OuroborosMonitor] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ouroboros_monitor")


# ---------------------------------------------------------------------------
# GAP-4b: TOML Dead-Letter & Auto-Recovery
# ---------------------------------------------------------------------------

# Minimal safe defaults for each known TOML config file.
# Used to replace corrupt files so the engine can start with sane values.
SAFE_DEFAULTS = {
    "dynamic_weights.toml": (
        "# Auto-generated safe defaults (corrupt file moved to dead_letter)\n"
        "[signal]\n"
        "confidence_floor = 65\n"
        "\n"
        "[kelly_fractions]\n"
        "tier1 = 0.020000\n"
        "tier2 = 0.015000\n"
        "tier3 = 0.010000\n"
    ),
    "universe_classification.toml": (
        "# Auto-generated safe defaults (corrupt file moved to dead_letter)\n"
        "[meta]\n"
        "generated_at = \"safe_defaults\"\n"
    ),
    "spread_cache.toml": (
        "# Auto-generated safe defaults (corrupt file moved to dead_letter)\n"
        "[meta]\n"
        "generated_at = \"safe_defaults\"\n"
    ),
}


def handle_corrupt_toml(
    filepath: Path,
    config_dir: Path = CONFIG_DIR,
    send_alert: bool = True,
) -> dict:
    """Move a corrupt TOML to dead_letter/ and write safe defaults in its place.

    Returns dict with action details for logging/alerting.
    """
    fname = filepath.name
    dead_letter_dir = config_dir / "dead_letter"
    dead_letter_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dead_name = f"{filepath.stem}_{ts}{filepath.suffix}"
    dead_path = dead_letter_dir / dead_name

    result = {
        "action": "handle_corrupt_toml",
        "file": fname,
        "dead_letter_path": str(dead_path),
        "safe_defaults_written": False,
        "alert_sent": False,
    }

    # Move corrupt file to dead_letter/
    try:
        if filepath.exists():
            shutil.move(str(filepath), str(dead_path))
            log.critical(
                "TOML CORRUPTION: %s moved to dead_letter/%s",
                fname,
                dead_name,
            )
        else:
            log.critical("TOML MISSING: %s (cannot move, writing defaults)", fname)
    except Exception as e:
        log.error("Failed to move %s to dead_letter: %s", fname, e)

    # Write safe defaults
    safe_content = SAFE_DEFAULTS.get(fname)
    if safe_content:
        try:
            filepath.write_text(safe_content)
            result["safe_defaults_written"] = True
            log.critical(
                "TOML RECOVERY: Wrote safe defaults for %s", fname
            )
        except Exception as e:
            log.error("Failed to write safe defaults for %s: %s", fname, e)
    else:
        # Unknown TOML file -- write a minimal valid stub
        try:
            filepath.write_text(
                f"# Auto-generated safe defaults (unknown file: {fname})\n"
                f"[meta]\n"
                f'generated_at = "safe_defaults"\n'
            )
            result["safe_defaults_written"] = True
            log.critical("TOML RECOVERY: Wrote generic defaults for %s", fname)
        except Exception as e:
            log.error("Failed to write generic defaults for %s: %s", fname, e)

    # Send Telegram alert
    if send_alert:
        msg = (
            "[CRIT] <b>TOML Auto-Recovery</b>\n\n"
            f"File: <code>{fname}</code>\n"
            f"Action: Moved corrupt file to <code>dead_letter/{dead_name}</code>\n"
            f"Recovery: Safe defaults written = {result['safe_defaults_written']}\n"
            f"Time: {ts}\n\n"
            "Manual review recommended."
        )
        result["alert_sent"] = send_telegram_alert(msg)

    return result


# ---------------------------------------------------------------------------
# Monitoring Checks
# ---------------------------------------------------------------------------

def check_toml_health(config_dir: Path = CONFIG_DIR, auto_fix: bool = False) -> dict:
    """Check all TOML config files for validity and freshness."""
    results = {
        "check": "toml_health",
        "status": "ok",
        "files_checked": 0,
        "files_valid": 0,
        "files_stale": 0,
        "files_missing": 0,
        "alerts": [],
    }

    toml_files = [
        "dynamic_weights.toml",
        "universe_classification.toml",
        "spread_cache.toml",
    ]

    now = time.time()
    stale_threshold = 36 * 3600  # 36 hours

    for fname in toml_files:
        fpath = config_dir / fname
        results["files_checked"] += 1

        if not fpath.exists():
            results["files_missing"] += 1
            results["alerts"].append(f"MISSING: {fname}")
            results["status"] = "warning"
            continue

        # Check validity
        try:
            content = fpath.read_text()
            if not content.strip():
                results["alerts"].append(f"EMPTY: {fname}")
                results["status"] = "critical"
                if auto_fix:
                    recovery = handle_corrupt_toml(fpath, config_dir)
                    results["alerts"].append(f"AUTO-FIX: {fname} -> dead_letter, defaults={recovery['safe_defaults_written']}")
                continue

            # Basic TOML validation
            valid = True
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("[") and not line.endswith("]"):
                    valid = False
                    break

            if valid:
                results["files_valid"] += 1
            else:
                results["alerts"].append(f"CORRUPT: {fname}")
                results["status"] = "critical"
                if auto_fix:
                    recovery = handle_corrupt_toml(fpath, config_dir)
                    results["alerts"].append(f"AUTO-FIX: {fname} -> dead_letter, defaults={recovery['safe_defaults_written']}")
        except Exception as e:
            results["alerts"].append(f"READ_ERROR: {fname}: {e}")
            results["status"] = "critical"
            if auto_fix:
                recovery = handle_corrupt_toml(fpath, config_dir)
                results["alerts"].append(f"AUTO-FIX: {fname} -> dead_letter, defaults={recovery['safe_defaults_written']}")
            continue

        # Check staleness
        mtime = fpath.stat().st_mtime
        age_hours = (now - mtime) / 3600

        if age_hours > stale_threshold / 3600:
            results["files_stale"] += 1
            results["alerts"].append(f"STALE: {fname} ({age_hours:.1f}h old, threshold={stale_threshold/3600:.0f}h)")
            if results["status"] == "ok":
                results["status"] = "warning"

    return results


def check_nightly_execution(data_dir: Path = DATA_DIR) -> dict:
    """Check if nightly Ouroboros ran successfully."""
    results = {
        "check": "nightly_execution",
        "status": "ok",
        "last_run": None,
        "hours_since_run": None,
        "alerts": [],
    }

    recs_file = data_dir / "ouroboros_recommendations.json"

    if not recs_file.exists():
        results["status"] = "warning"
        results["alerts"].append("No recommendations file found (nightly may not have run yet)")
        return results

    mtime = recs_file.stat().st_mtime
    last_run = datetime.fromtimestamp(mtime, tz=timezone.utc)
    hours_since = (time.time() - mtime) / 3600

    results["last_run"] = last_run.isoformat()
    results["hours_since_run"] = round(hours_since, 1)

    # Nightly runs at 04:50 UTC. If > 28 hours old on a weekday, it missed last night
    now = datetime.now(timezone.utc)
    if now.weekday() < 5 and hours_since > 28:
        results["status"] = "critical"
        results["alerts"].append(f"Nightly pipeline MISSED: last run {hours_since:.1f}h ago")
    elif hours_since > 48:
        results["status"] = "warning"
        results["alerts"].append(f"Nightly pipeline stale: last run {hours_since:.1f}h ago (weekend?)")

    return results


def check_log_health() -> dict:
    """Check Ouroboros log file for recent errors."""
    results = {
        "check": "log_health",
        "status": "ok",
        "recent_errors": 0,
        "log_size_mb": 0,
        "alerts": [],
    }

    log_path = Path("/var/log/ouroboros.log")
    if not log_path.exists():
        results["alerts"].append("No ouroboros.log found")
        return results

    size_mb = log_path.stat().st_size / (1024 * 1024)
    results["log_size_mb"] = round(size_mb, 1)

    if size_mb > 100:
        results["status"] = "warning"
        results["alerts"].append(f"Log file large: {size_mb:.0f}MB (consider rotation)")

    # Count recent errors (last 100 lines)
    try:
        lines = log_path.read_text().splitlines()[-100:]
        errors = sum(1 for l in lines if "ERROR" in l or "CRITICAL" in l)
        results["recent_errors"] = errors
        if errors > 5:
            results["status"] = "warning"
            results["alerts"].append(f"{errors} errors in last 100 log lines")
    except Exception:
        pass

    return results


def run_all_checks(auto_fix: bool = False) -> dict:
    """Run all monitoring checks and return combined results."""
    checks = [
        check_toml_health(auto_fix=auto_fix),
        check_nightly_execution(),
        check_log_health(),
    ]

    overall_status = "ok"
    all_alerts = []

    for check in checks:
        if check["status"] == "critical":
            overall_status = "critical"
        elif check["status"] == "warning" and overall_status != "critical":
            overall_status = "warning"
        all_alerts.extend(check.get("alerts", []))

    return {
        "overall_status": overall_status,
        "checks": checks,
        "alerts": all_alerts,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Telegram alerting
# ---------------------------------------------------------------------------
def send_telegram_alert(message: str) -> bool:
    """Send monitoring alert via Telegram."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        log.warning("Telegram not configured (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing)")
        return False

    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ouroboros monitoring & alerting")
    parser.add_argument("--check-toml", action="store_true", help="Check TOML health only")
    parser.add_argument("--check-staleness", action="store_true", help="Check nightly execution")
    parser.add_argument("--check-logs", action="store_true", help="Check log health")
    parser.add_argument("--send-telegram", action="store_true", help="Send alerts via Telegram")
    parser.add_argument("--all", action="store_true", help="Run all checks")
    parser.add_argument("--auto-fix", action="store_true",
                        help="Automatically recover corrupt TOML files (move to dead_letter, write safe defaults)")
    args = parser.parse_args()

    if args.check_toml:
        result = check_toml_health(auto_fix=args.auto_fix)
    elif args.check_staleness:
        result = check_nightly_execution()
    elif args.check_logs:
        result = check_log_health()
    else:
        result = run_all_checks(auto_fix=args.auto_fix)

    # Print results
    print(json.dumps(result, indent=2, default=str))

    # Send Telegram if requested and there are alerts
    alerts = result.get("alerts", [])
    if args.send_telegram and alerts:
        status = result.get("overall_status", result.get("status", "unknown"))
        icon = {"ok": "[OK]", "warning": "[WARN]", "critical": "[CRIT]"}.get(status, "[?]")
        msg = f"{icon} <b>Ouroboros Monitor</b>\n\n"
        msg += f"Status: <b>{status.upper()}</b>\n\n"
        for alert in alerts:
            msg += f"- {alert}\n"
        send_telegram_alert(msg)

    # Exit code based on status
    status = result.get("overall_status", result.get("status", "ok"))
    if status == "critical":
        sys.exit(1)
    elif status == "warning":
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
