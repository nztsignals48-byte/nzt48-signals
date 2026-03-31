"""Maintenance Utilities — Ouroboros infrastructure module.

ISS-017:  Persistent memory read-back (close learning feedback loop)
v19-P2-15: Parquet orphan cleanup (prevent disk fill from intermediate files)
ISS-020:  BST/DST awareness helper for crontab scheduling
SC-19:    System memory check + step health monitor

Usage: python3 -m python_brain.ouroboros.maintenance [--cleanup|--read-memory|--bst-check|--mem-check|--step-health]
"""

from __future__ import annotations

import glob
import json
import logging
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Maintenance] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("maintenance")


# ---------------------------------------------------------------------------
# ISS-017: Persistent Memory Read-Back
# ---------------------------------------------------------------------------
# persistent_memory.py writes lessons (trade patterns, ticker blacklists,
# session insights) but nightly_v6.py never reads them back.
# This function loads accumulated lessons for consumption.

MEMORY_FILE = DATA_DIR / "persistent_memory.json"


def load_persistent_memory() -> dict:
    """Load accumulated persistent memory lessons.

    Returns dict with:
        ticker_blacklist: list of tickers to avoid
        session_insights: dict of session→insight
        trade_patterns: list of pattern dicts
        parameter_hints: dict of param→suggested_value
        loaded: bool
        entries: int (total memory entries)
    """
    result = {
        "ticker_blacklist": [],
        "session_insights": {},
        "trade_patterns": [],
        "parameter_hints": {},
        "loaded": False,
        "entries": 0,
    }

    if not MEMORY_FILE.exists():
        log.info("ISS-017: No persistent memory file found (first run?)")
        return result

    try:
        memory = json.loads(MEMORY_FILE.read_text())

        # Extract blacklisted tickers
        if "blacklisted_tickers" in memory:
            result["ticker_blacklist"] = list(memory["blacklisted_tickers"])

        # Extract session insights
        if "sessions" in memory:
            for session_key, session_data in memory["sessions"].items():
                if isinstance(session_data, dict):
                    insight = session_data.get("insight", session_data.get("summary", ""))
                    if insight:
                        result["session_insights"][session_key] = insight

        # Extract trade patterns (lessons from wins/losses)
        if "patterns" in memory:
            result["trade_patterns"] = memory["patterns"][:50]  # Cap at 50 most recent

        # Extract parameter hints (Ouroboros-suggested overrides)
        if "parameter_hints" in memory:
            result["parameter_hints"] = memory["parameter_hints"]

        # Also check for individual lesson files
        lessons_dir = DATA_DIR / "lessons"
        if lessons_dir.exists():
            for lesson_file in sorted(lessons_dir.glob("*.json"))[-20:]:  # Last 20
                try:
                    lesson = json.loads(lesson_file.read_text())
                    if "pattern" in lesson:
                        result["trade_patterns"].append(lesson["pattern"])
                except (json.JSONDecodeError, KeyError):
                    continue

        result["loaded"] = True
        result["entries"] = (
            len(result["ticker_blacklist"])
            + len(result["session_insights"])
            + len(result["trade_patterns"])
            + len(result["parameter_hints"])
        )

        log.info(f"ISS-017: Loaded persistent memory: {result['entries']} entries "
                 f"({len(result['ticker_blacklist'])} blacklisted, "
                 f"{len(result['trade_patterns'])} patterns, "
                 f"{len(result['parameter_hints'])} param hints)")

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.error(f"ISS-017: Failed to load persistent memory: {e}")

    return result


def apply_memory_to_config(memory: dict, config: dict) -> dict:
    """Apply persistent memory lessons to nightly config.

    Called by config_writer to incorporate learned lessons:
    - Blacklisted tickers → [ticker_blacklist] section
    - Parameter hints → override defaults if within guardrails

    Args:
        memory: Output from load_persistent_memory()
        config: Current config dict from nightly_v6

    Returns:
        Updated config dict
    """
    # Apply parameter hints with guardrails
    hints = memory.get("parameter_hints", {})

    GUARDRAILS = {
        "kelly_fraction": (0.15, 0.30),
        "chandelier_atr_mult": (1.5, 4.0),
        "confidence_floor": (0.50, 0.90),
    }

    for param, suggested in hints.items():
        if param in GUARDRAILS:
            lo, hi = GUARDRAILS[param]
            clamped = max(lo, min(hi, float(suggested)))
            if param in config:
                old = config[param]
                config[param] = clamped
                log.info(f"ISS-017: Applied memory hint: {param} {old} → {clamped}")

    return config


# ---------------------------------------------------------------------------
# v19-P2-15: Parquet Orphan Cleanup
# ---------------------------------------------------------------------------
# Polars/Parquet intermediate files can accumulate in /tmp and /app/data.
# Clean up files older than 1 hour to prevent disk fill.

def cleanup_parquet_orphans(
    dirs: List[Path] = None,
    max_age_hours: float = 1.0,
    dry_run: bool = False,
) -> dict:
    """Clean up orphaned Parquet files from intermediate processing.

    Args:
        dirs: Directories to scan (default: /tmp, DATA_DIR)
        max_age_hours: Delete files older than this (default: 1 hour)
        dry_run: If True, report but don't delete

    Returns:
        dict with count and bytes freed
    """
    if dirs is None:
        dirs = [Path("/tmp"), DATA_DIR]

    results = {
        "files_found": 0,
        "files_deleted": 0,
        "bytes_freed": 0,
        "errors": [],
        "dry_run": dry_run,
    }

    now = time.time()
    max_age_sec = max_age_hours * 3600

    for scan_dir in dirs:
        if not scan_dir.exists():
            continue

        # Find .parquet and .parquet.tmp files
        for pattern in ["*.parquet", "*.parquet.tmp", "*.parquet.crc"]:
            for fpath in scan_dir.glob(pattern):
                try:
                    age = now - fpath.stat().st_mtime
                    if age > max_age_sec:
                        results["files_found"] += 1
                        size = fpath.stat().st_size

                        if dry_run:
                            log.info(f"Would delete: {fpath} ({size/1024:.1f}KB, {age/3600:.1f}h old)")
                        else:
                            fpath.unlink()
                            results["files_deleted"] += 1
                            results["bytes_freed"] += size
                            log.info(f"Deleted orphan: {fpath} ({size/1024:.1f}KB)")
                except OSError as e:
                    results["errors"].append(f"{fpath}: {e}")

    if results["files_deleted"] > 0:
        log.info(f"v19-P2-15: Cleaned {results['files_deleted']} Parquet orphans, "
                 f"freed {results['bytes_freed']/1024/1024:.1f}MB")

    return results


# ---------------------------------------------------------------------------
# ISS-020: BST/DST Awareness Helper
# ---------------------------------------------------------------------------
# Crontab uses UTC times but some sessions assume London times.
# During BST transitions, timing can be off by 1 hour.

def get_london_offset() -> timedelta:
    """Get current London UTC offset (0h GMT or +1h BST)."""
    london = ZoneInfo("Europe/London")
    now_london = datetime.now(london)
    offset = now_london.utcoffset()
    return offset if offset else timedelta(0)


def is_bst() -> bool:
    """Check if London is currently in BST (British Summer Time)."""
    return get_london_offset() == timedelta(hours=1)


def london_to_utc(hour: int, minute: int = 0) -> tuple:
    """Convert London local time to UTC, accounting for BST.

    Args:
        hour: London local hour (0-23)
        minute: London local minute (0-59)

    Returns:
        (utc_hour, utc_minute) tuple
    """
    offset = get_london_offset()
    offset_hours = int(offset.total_seconds() / 3600)

    utc_hour = (hour - offset_hours) % 24
    return (utc_hour, minute)


def compute_session_times_utc() -> dict:
    """Compute all session times in UTC, accounting for current DST state.

    Returns dict of session→(open_utc, close_utc) pairs.
    """
    is_bst_now = is_bst()
    offset = 1 if is_bst_now else 0

    return {
        "lse": {
            "open_utc": f"{8 - offset:02d}:00",
            "close_utc": f"{16 - offset:02d}:30",
            "is_bst": is_bst_now,
        },
        "nightly_ouroboros_utc": f"{5 - offset:02d}:50",  # 05:50 London
        "config_writer_utc": f"{5 - offset:02d}:51",
        "dark_window": {
            "start_utc": f"{21 - offset:02d}:00",
            "end_utc": f"{23 - offset:02d}:00",
        },
    }


# ---------------------------------------------------------------------------
# ISS-012: WAL Archive Cleanup
# ---------------------------------------------------------------------------
# WAL archive files accumulate in /app/events/archive/ after engine restarts.
# Clean up files older than max_age_days to prevent disk fill.

EVENTS_DIR = Path(os.environ.get("AEGIS_EVENTS_DIR", "/app/events"))


def cleanup_old_wal_archives(
    max_age_days: int = 30,
    dry_run: bool = False,
) -> dict:
    """Delete WAL archive files older than max_age_days from /app/events/archive/.

    Args:
        max_age_days: Delete archives older than this (default: 30 days)
        dry_run: If True, report but don't delete

    Returns:
        dict with count and bytes freed
    """
    archive_dir = EVENTS_DIR / "archive"
    results = {
        "files_found": 0,
        "files_deleted": 0,
        "bytes_freed": 0,
        "errors": [],
        "dry_run": dry_run,
    }

    if not archive_dir.exists():
        log.info("ISS-012: WAL archive dir %s does not exist, skipping", archive_dir)
        return results

    now = time.time()
    max_age_sec = max_age_days * 86400

    for fpath in archive_dir.glob("*.ndjson"):
        try:
            age = now - fpath.stat().st_mtime
            if age > max_age_sec:
                results["files_found"] += 1
                size = fpath.stat().st_size

                if dry_run:
                    log.info("Would delete WAL archive: %s (%.1fKB, %.0fd old)",
                             fpath.name, size / 1024, age / 86400)
                else:
                    fpath.unlink()
                    results["files_deleted"] += 1
                    results["bytes_freed"] += size
                    log.info("Deleted WAL archive: %s (%.1fKB, %.0fd old)",
                             fpath.name, size / 1024, age / 86400)
        except OSError as e:
            results["errors"].append(f"{fpath}: {e}")

    if results["files_deleted"] > 0:
        log.info("ISS-012: Cleaned %d WAL archives, freed %.1fMB",
                 results["files_deleted"], results["bytes_freed"] / 1024 / 1024)

    return results


# ---------------------------------------------------------------------------
# ISS-012: Report Cleanup
# ---------------------------------------------------------------------------
# Ouroboros report files accumulate in data/ouroboros_reports/.
# Clean up files older than max_age_days to prevent disk fill.

REPORTS_DIR = DATA_DIR / "ouroboros_reports"


def cleanup_old_reports(
    max_age_days: int = 30,
    dry_run: bool = False,
) -> dict:
    """Delete report files older than max_age_days from data/ouroboros_reports/.

    Args:
        max_age_days: Delete reports older than this (default: 30 days)
        dry_run: If True, report but don't delete

    Returns:
        dict with count and bytes freed
    """
    results = {
        "files_found": 0,
        "files_deleted": 0,
        "bytes_freed": 0,
        "errors": [],
        "dry_run": dry_run,
    }

    if not REPORTS_DIR.exists():
        log.info("ISS-012: Reports dir %s does not exist, skipping", REPORTS_DIR)
        return results

    now = time.time()
    max_age_sec = max_age_days * 86400

    # Clean all file types: .json, .html, .pdf, .txt, etc.
    for fpath in REPORTS_DIR.iterdir():
        if not fpath.is_file():
            continue
        try:
            age = now - fpath.stat().st_mtime
            if age > max_age_sec:
                results["files_found"] += 1
                size = fpath.stat().st_size

                if dry_run:
                    log.info("Would delete report: %s (%.1fKB, %.0fd old)",
                             fpath.name, size / 1024, age / 86400)
                else:
                    fpath.unlink()
                    results["files_deleted"] += 1
                    results["bytes_freed"] += size
                    log.info("Deleted report: %s (%.1fKB, %.0fd old)",
                             fpath.name, size / 1024, age / 86400)
        except OSError as e:
            results["errors"].append(f"{fpath}: {e}")

    if results["files_deleted"] > 0:
        log.info("ISS-012: Cleaned %d reports, freed %.1fMB",
                 results["files_deleted"], results["bytes_freed"] / 1024 / 1024)

    return results


# ---------------------------------------------------------------------------
# SC-19: System Memory Check
# ---------------------------------------------------------------------------
# Provides system-wide memory stats for OOM prevention and monitoring.
# Platform-aware: Linux (/proc/meminfo) preferred, psutil fallback, macOS vm_stat.

STEP_LOG_DIR = DATA_DIR / "step_logs"


def check_system_memory() -> dict:
    """Read system memory information.

    Returns dict with:
        total_mb: Total physical RAM in MB
        available_mb: Available memory in MB (can be used without swapping)
        used_pct: Percentage of memory in use
        swap_used_mb: Swap space currently used in MB
        source: How memory was read (procfs, psutil, vm_stat)
        warning: Optional warning string if memory is low
    """
    result = {
        "total_mb": 0.0,
        "available_mb": 0.0,
        "used_pct": 0.0,
        "swap_used_mb": 0.0,
        "source": "unknown",
        "warning": "",
    }

    # ----- Try /proc/meminfo (Linux, fast, no dependencies) -----
    meminfo_path = Path("/proc/meminfo")
    if meminfo_path.exists():
        try:
            text = meminfo_path.read_text()
            fields = {}
            for line in text.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    val_kb = int(parts[1])
                    fields[key] = val_kb

            total_kb = fields.get("MemTotal", 0)
            available_kb = fields.get("MemAvailable", 0)
            swap_total_kb = fields.get("SwapTotal", 0)
            swap_free_kb = fields.get("SwapFree", 0)

            result["total_mb"] = total_kb / 1024
            result["available_mb"] = available_kb / 1024
            if total_kb > 0:
                result["used_pct"] = round(((total_kb - available_kb) / total_kb) * 100, 1)
            result["swap_used_mb"] = (swap_total_kb - swap_free_kb) / 1024
            result["source"] = "procfs"

            log.info("SC-19: Memory — %.0f MB total, %.0f MB available (%.1f%% used), "
                     "%.0f MB swap used [procfs]",
                     result["total_mb"], result["available_mb"],
                     result["used_pct"], result["swap_used_mb"])

            # Warn thresholds
            if result["available_mb"] < 512:
                result["warning"] = f"LOW MEMORY: {result['available_mb']:.0f} MB available (< 512 MB)"
                log.warning("SC-19: %s", result["warning"])

            return result
        except (OSError, ValueError, KeyError) as e:
            log.debug("SC-19: /proc/meminfo parse failed: %s", e)

    # ----- Fallback: psutil (works on macOS + Linux) -----
    try:
        import psutil
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        result["total_mb"] = vm.total / (1024 * 1024)
        result["available_mb"] = vm.available / (1024 * 1024)
        result["used_pct"] = round(vm.percent, 1)
        result["swap_used_mb"] = sw.used / (1024 * 1024)
        result["source"] = "psutil"

        log.info("SC-19: Memory — %.0f MB total, %.0f MB available (%.1f%% used), "
                 "%.0f MB swap used [psutil]",
                 result["total_mb"], result["available_mb"],
                 result["used_pct"], result["swap_used_mb"])

        if result["available_mb"] < 512:
            result["warning"] = f"LOW MEMORY: {result['available_mb']:.0f} MB available (< 512 MB)"
            log.warning("SC-19: %s", result["warning"])

        return result
    except ImportError:
        log.debug("SC-19: psutil not available")
    except Exception as e:
        log.debug("SC-19: psutil failed: %s", e)

    # ----- Last resort on macOS: vm_stat + sysctl -----
    if platform.system() == "Darwin":
        try:
            out = subprocess.check_output(["vm_stat"], text=True, timeout=5)
            page_size = 16384  # default on Apple Silicon
            for line in out.splitlines():
                if "page size of" in line:
                    page_size = int(line.split()[-2])
                    break

            fields = {}
            for line in out.splitlines()[1:]:
                if ":" in line:
                    key, val = line.split(":", 1)
                    val = val.strip().rstrip(".")
                    try:
                        fields[key.strip()] = int(val)
                    except ValueError:
                        continue

            free_pages = fields.get("Pages free", 0)
            inactive_pages = fields.get("Pages inactive", 0)
            speculative_pages = fields.get("Pages speculative", 0)
            available_pages = free_pages + inactive_pages + speculative_pages

            sysctl_out = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True, timeout=5
            ).strip()
            total_bytes = int(sysctl_out)

            result["total_mb"] = total_bytes / (1024 * 1024)
            result["available_mb"] = (available_pages * page_size) / (1024 * 1024)
            if total_bytes > 0:
                result["used_pct"] = round(
                    (1 - (available_pages * page_size / total_bytes)) * 100, 1
                )
            result["swap_used_mb"] = 0.0  # vm_stat doesn't easily give swap
            result["source"] = "vm_stat"

            log.info("SC-19: Memory — %.0f MB total, %.0f MB available (%.1f%% used) [vm_stat]",
                     result["total_mb"], result["available_mb"], result["used_pct"])
            return result
        except Exception as e:
            log.debug("SC-19: vm_stat fallback failed: %s", e)

    log.warning("SC-19: Could not determine system memory")
    return result


# ---------------------------------------------------------------------------
# SC-19: Step Health Check
# ---------------------------------------------------------------------------
# Reads step_logs/ directory to report on last run of each step.

def check_step_health() -> List[dict]:
    """Read step_logs directory and return health status for each step.

    Returns list of dicts, each with:
        step: step name
        last_run_utc: ISO timestamp of last run
        duration_sec: how long it took
        exit_code: 0 = success, negative = special (skip/OOM/timeout)
        peak_rss_mb: peak memory usage
        skipped: whether the run was skipped
        skip_reason: why it was skipped (if applicable)
        age_hours: hours since last run
        status: "ok", "failed", "skipped", "stale", "never_run"
    """
    results = []

    if not STEP_LOG_DIR.exists():
        log.info("SC-19: Step log dir %s does not exist (no steps run yet?)", STEP_LOG_DIR)
        return results

    # Group log files by step name, keep only the most recent per step
    latest_by_step: Dict[str, Path] = {}
    for log_file in STEP_LOG_DIR.glob("*.json"):
        # Filename format: {step_name}_{YYYYMMDD_HHMMSS}.json
        name = log_file.stem
        # Find the step name by removing the date suffix
        # step_name can contain underscores, so we split from the right
        # Date pattern: _YYYYMMDD_HHMMSS (16 chars including leading underscore)
        if len(name) > 16 and name[-15:].replace("_", "").isdigit():
            step_name = name[:-16]  # Remove _YYYYMMDD_HHMMSS
        else:
            # Fallback: try to read the file and get step name from JSON
            step_name = name

        if step_name not in latest_by_step or log_file.stat().st_mtime > latest_by_step[step_name].stat().st_mtime:
            latest_by_step[step_name] = log_file

    now = time.time()

    for step_name, log_file in sorted(latest_by_step.items()):
        try:
            data = json.loads(log_file.read_text())

            age_sec = now - log_file.stat().st_mtime
            age_hours = age_sec / 3600

            exit_code = data.get("exit_code", -99)
            skipped = data.get("skipped", False)

            # Determine status
            if skipped:
                status = "skipped"
            elif exit_code == 0:
                if age_hours > 48:
                    status = "stale"  # Haven't run in 2+ days
                else:
                    status = "ok"
            elif exit_code == -9:
                status = "oom_killed"
            elif exit_code == -3:
                status = "timeout"
            else:
                status = "failed"

            results.append({
                "step": data.get("step", step_name),
                "last_run_utc": data.get("timestamp_utc", ""),
                "duration_sec": data.get("duration_sec", 0),
                "exit_code": exit_code,
                "peak_rss_mb": data.get("peak_rss_mb", 0),
                "skipped": skipped,
                "skip_reason": data.get("skip_reason", ""),
                "age_hours": round(age_hours, 1),
                "status": status,
                "log_file": str(log_file),
            })

        except (json.JSONDecodeError, OSError, KeyError) as e:
            log.warning("SC-19: Failed to read step log %s: %s", log_file, e)
            results.append({
                "step": step_name,
                "last_run_utc": "",
                "duration_sec": 0,
                "exit_code": -99,
                "peak_rss_mb": 0,
                "skipped": False,
                "skip_reason": f"Log parse error: {e}",
                "age_hours": 0,
                "status": "error",
                "log_file": str(log_file),
            })

    # Log summary
    ok_count = sum(1 for r in results if r["status"] == "ok")
    fail_count = sum(1 for r in results if r["status"] in ("failed", "oom_killed", "timeout"))
    skip_count = sum(1 for r in results if r["status"] == "skipped")
    stale_count = sum(1 for r in results if r["status"] == "stale")

    log.info("SC-19: Step health — %d ok, %d failed, %d skipped, %d stale (of %d total)",
             ok_count, fail_count, skip_count, stale_count, len(results))

    return results


def cleanup_old_step_logs(max_age_days: int = 14, dry_run: bool = False) -> dict:
    """Delete step log files older than max_age_days.

    Args:
        max_age_days: Delete logs older than this (default: 14 days)
        dry_run: If True, report but don't delete

    Returns:
        dict with count and bytes freed
    """
    results = {
        "files_found": 0,
        "files_deleted": 0,
        "bytes_freed": 0,
        "errors": [],
        "dry_run": dry_run,
    }

    if not STEP_LOG_DIR.exists():
        return results

    now = time.time()
    max_age_sec = max_age_days * 86400

    for fpath in STEP_LOG_DIR.glob("*.json"):
        try:
            age = now - fpath.stat().st_mtime
            if age > max_age_sec:
                results["files_found"] += 1
                size = fpath.stat().st_size

                if dry_run:
                    log.info("Would delete step log: %s (%.1fKB, %.0fd old)",
                             fpath.name, size / 1024, age / 86400)
                else:
                    fpath.unlink()
                    results["files_deleted"] += 1
                    results["bytes_freed"] += size
                    log.info("Deleted step log: %s (%.1fKB)", fpath.name, size / 1024)
        except OSError as e:
            results["errors"].append(f"{fpath}: {e}")

    if results["files_deleted"] > 0:
        log.info("SC-19: Cleaned %d step logs, freed %.1fKB",
                 results["files_deleted"], results["bytes_freed"] / 1024)

    return results


# ---------------------------------------------------------------------------
# R21-16b: Circuit Breaker Redis Checkpoint
# ---------------------------------------------------------------------------
# Persist circuit breaker state to Redis + file so it survives restarts.
# Reads WAL events (RiskStateChange, PositionClosed, StateSnapshot, DailyReset)
# to derive current tier, daily loss, max drawdown, and position count.
#
# Tier mapping:
#   GREEN  — daily_loss < 0.5%, no risk state changes
#   YELLOW — daily_loss 0.5%-1.0% OR RiskStateChange to "Reduce"
#   ORANGE — daily_loss 1.0%-2.0% OR RiskStateChange to "Halt"
#   RED    — daily_loss > 2.0% OR RiskStateChange to "Flatten"/"Emergency"

REDIS_URL = os.environ.get("REDIS_URL", "redis://aegis-redis:6379/0")
CB_REDIS_KEY = "aegis:circuit_breaker:state"
CB_CHECKPOINT_FILE = DATA_DIR / "circuit_breaker_checkpoint.json"
EVENTS_DIR_CB = Path(os.environ.get("AEGIS_EVENTS_DIR", os.environ.get("AEGIS_WAL_DIR", "/app/events")))


def _get_redis_client():
    """Create Redis client. Returns None if unavailable."""
    try:
        import redis as redis_pkg
        r = redis_pkg.from_url(REDIS_URL, socket_timeout=5, socket_connect_timeout=5)
        r.ping()
        return r
    except Exception as e:
        log.warning("R21-16b: Redis unavailable (%s) — file checkpoint only", e)
        return None


def _derive_circuit_breaker_state() -> dict:
    """Derive current circuit breaker state from today's WAL events.

    Scans WAL for:
      - StateSnapshot: latest equity + high_water for drawdown calc
      - RiskStateChange: regime transitions (Normal→Reduce→Halt→Flatten)
      - PositionClosed: count today's trades + sum P&L for daily loss
      - DailyReset: start-of-day equity baseline

    Returns dict: {tier, daily_loss_pct, max_drawdown_pct, positions_count, timestamp}
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Scan WAL files
    wal_candidates = [
        EVENTS_DIR_CB / "current.ndjson",
        EVENTS_DIR_CB / f"{today}.ndjson",
        EVENTS_DIR_CB / f"wal_{today}.ndjson",
    ]
    archive_dir = EVENTS_DIR_CB / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.ndjson")):
            if f not in wal_candidates:
                wal_candidates.append(f)

    # State accumulators
    start_equity = 10_000.0  # default
    latest_equity = 10_000.0
    high_water = 10_000.0
    risk_state = "Normal"
    positions_count = 0
    daily_pnl = 0.0
    open_position_ids = set()

    for wal_path in wal_candidates:
        if not wal_path.exists():
            continue
        try:
            with open(wal_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Filter to today's events only
                    event_time_ns = event.get("event_time_ns", 0)
                    if event_time_ns > 0:
                        try:
                            event_date = datetime.fromtimestamp(
                                event_time_ns / 1e9, tz=timezone.utc
                            ).strftime("%Y-%m-%d")
                            if event_date != today:
                                continue
                        except (OSError, ValueError):
                            continue

                    payload = event.get("payload", {})

                    # DailyReset: captures start-of-day equity
                    if "DailyReset" in payload:
                        dr = payload["DailyReset"]
                        start_equity = dr.get("new_equity", start_equity)
                        high_water = max(high_water, start_equity)

                    # StateSnapshot: latest equity snapshot
                    elif "StateSnapshot" in payload:
                        ss = payload["StateSnapshot"]
                        latest_equity = ss.get("equity", latest_equity)
                        hw = ss.get("high_water", 0.0)
                        if hw > 0:
                            high_water = max(high_water, hw)
                        # Count open positions from snapshot
                        open_pos = ss.get("open_positions", [])
                        if isinstance(open_pos, list):
                            positions_count = len(open_pos)

                    # RiskStateChange: track regime transitions
                    elif "RiskStateChange" in payload:
                        rsc = payload["RiskStateChange"]
                        risk_state = rsc.get("to", risk_state)

                    # PositionClosed: accumulate daily P&L
                    elif "PositionClosed" in payload:
                        pc = payload["PositionClosed"]
                        daily_pnl += pc.get("final_pnl", 0.0)

                    # RoutedOrder: track open positions (entry)
                    elif "RoutedOrder" in payload:
                        ro = payload["RoutedOrder"]
                        oid = ro.get("order_id", "")
                        if oid:
                            open_position_ids.add(oid)

        except Exception as e:
            log.warning("R21-16b: Error reading %s: %s", wal_path, e)

    # Compute metrics
    daily_loss_pct = 0.0
    if start_equity > 0:
        daily_loss_pct = max(0.0, -daily_pnl / start_equity * 100.0)

    max_drawdown_pct = 0.0
    if high_water > 0:
        max_drawdown_pct = max(0.0, (high_water - latest_equity) / high_water * 100.0)

    # Derive tier from risk_state + daily loss
    risk_lower = risk_state.lower()
    if "flatten" in risk_lower or "emergency" in risk_lower or daily_loss_pct > 2.0:
        tier = "RED"
    elif "halt" in risk_lower or daily_loss_pct > 1.0:
        tier = "ORANGE"
    elif "reduce" in risk_lower or daily_loss_pct > 0.5:
        tier = "YELLOW"
    else:
        tier = "GREEN"

    return {
        "tier": tier,
        "daily_loss_pct": round(daily_loss_pct, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "positions_count": positions_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "risk_state": risk_state,
        "daily_pnl": round(daily_pnl, 2),
        "start_equity": round(start_equity, 2),
        "latest_equity": round(latest_equity, 2),
    }


def checkpoint_circuit_breaker() -> dict:
    """R21-16b: Checkpoint circuit breaker state to Redis + file.

    1. Derives current state from WAL events
    2. Saves to Redis key aegis:circuit_breaker:state (JSON)
    3. Saves to data/circuit_breaker_checkpoint.json (atomic write)

    Returns the state dict.
    """
    state = _derive_circuit_breaker_state()

    # Save to Redis
    r = _get_redis_client()
    if r is not None:
        try:
            r.set(CB_REDIS_KEY, json.dumps(state), ex=86400)  # 24h TTL
            log.info("R21-16b: Saved to Redis key %s (tier=%s)", CB_REDIS_KEY, state["tier"])
        except Exception as e:
            log.warning("R21-16b: Redis save failed: %s", e)

    # Save to file (atomic write: tmp + rename)
    try:
        CB_CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = CB_CHECKPOINT_FILE.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(state, indent=2) + "\n")
        tmp_path.rename(CB_CHECKPOINT_FILE)
        log.info("R21-16b: Saved to %s (tier=%s)", CB_CHECKPOINT_FILE, state["tier"])
    except Exception as e:
        log.warning("R21-16b: File save failed: %s", e)

    # Log summary
    log.info(
        "R21-16b: Circuit breaker — tier=%s, daily_loss=%.2f%%, max_dd=%.2f%%, "
        "positions=%d, risk_state=%s",
        state["tier"], state["daily_loss_pct"], state["max_drawdown_pct"],
        state["positions_count"], state["risk_state"],
    )

    return state


def restore_circuit_breaker() -> dict:
    """R21-16b: Restore circuit breaker state on boot.

    Priority: Redis first, then file fallback.
    Returns the state dict, or a default GREEN state if nothing found.
    """
    default_state = {
        "tier": "GREEN",
        "daily_loss_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "positions_count": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "risk_state": "Normal",
        "restored_from": "default",
    }

    # Try Redis first
    r = _get_redis_client()
    if r is not None:
        try:
            raw = r.get(CB_REDIS_KEY)
            if raw:
                state = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                state["restored_from"] = "redis"
                log.info("R21-16b: Restored from Redis (tier=%s, ts=%s)",
                         state.get("tier"), state.get("timestamp"))
                return state
        except Exception as e:
            log.warning("R21-16b: Redis restore failed: %s", e)

    # Try file fallback
    if CB_CHECKPOINT_FILE.exists():
        try:
            state = json.loads(CB_CHECKPOINT_FILE.read_text())
            state["restored_from"] = "file"
            log.info("R21-16b: Restored from file (tier=%s, ts=%s)",
                     state.get("tier"), state.get("timestamp"))
            return state
        except (json.JSONDecodeError, KeyError) as e:
            log.warning("R21-16b: File restore failed: %s", e)

    log.info("R21-16b: No checkpoint found — using default GREEN state")
    return default_state


# ---------------------------------------------------------------------------
# CLI entry point (updated with --checkpoint-circuit-breaker)
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Maintenance utilities")
    parser.add_argument("--read-memory", action="store_true", help="ISS-017: Read persistent memory")
    parser.add_argument("--cleanup", action="store_true", help="v19-P2-15+ISS-012: Clean Parquet orphans, WAL archives, old reports, and step logs")
    parser.add_argument("--cleanup-dry-run", action="store_true", help="v19-P2-15+ISS-012: Dry run cleanup")
    parser.add_argument("--bst-check", action="store_true", help="ISS-020: Check BST status")
    parser.add_argument("--mem-check", action="store_true", help="SC-19: Check system memory")
    parser.add_argument("--step-health", action="store_true", help="SC-19: Check step runner health")
    parser.add_argument("--checkpoint-circuit-breaker", action="store_true",
                        help="R21-16b: Checkpoint circuit breaker state to Redis + file")
    parser.add_argument("--restore-circuit-breaker", action="store_true",
                        help="R21-16b: Restore circuit breaker state from Redis/file")
    parser.add_argument("--all", action="store_true", help="Run all maintenance tasks")
    args = parser.parse_args()

    if args.read_memory or args.all:
        memory = load_persistent_memory()
        print(f"\n=== ISS-017: Persistent Memory ===")
        print(f"Loaded: {memory['loaded']}, Entries: {memory['entries']}")
        if memory['ticker_blacklist']:
            print(f"Blacklisted: {', '.join(memory['ticker_blacklist'])}")
        if memory['parameter_hints']:
            for k, v in memory['parameter_hints'].items():
                print(f"  Hint: {k} = {v}")

    if args.cleanup or args.cleanup_dry_run or args.all:
        dry_run = args.cleanup_dry_run and not args.cleanup and not args.all

        # v19-P2-15: Parquet orphan cleanup
        result = cleanup_parquet_orphans(dry_run=dry_run)
        print(f"\n=== v19-P2-15: Parquet Cleanup {'(DRY RUN) ' if dry_run else ''}===")
        print(f"Found: {result['files_found']}, Deleted: {result['files_deleted']}, "
              f"Freed: {result['bytes_freed']/1024:.1f}KB")

        # ISS-012: WAL archive cleanup
        wal_result = cleanup_old_wal_archives(dry_run=dry_run)
        print(f"\n=== ISS-012: WAL Archive Cleanup {'(DRY RUN) ' if dry_run else ''}===")
        print(f"Found: {wal_result['files_found']}, Deleted: {wal_result['files_deleted']}, "
              f"Freed: {wal_result['bytes_freed']/1024:.1f}KB")

        # ISS-012: Report cleanup
        rpt_result = cleanup_old_reports(dry_run=dry_run)
        print(f"\n=== ISS-012: Report Cleanup {'(DRY RUN) ' if dry_run else ''}===")
        print(f"Found: {rpt_result['files_found']}, Deleted: {rpt_result['files_deleted']}, "
              f"Freed: {rpt_result['bytes_freed']/1024:.1f}KB")

        # SC-19: Step log cleanup
        step_result = cleanup_old_step_logs(dry_run=dry_run)
        print(f"\n=== SC-19: Step Log Cleanup {'(DRY RUN) ' if dry_run else ''}===")
        print(f"Found: {step_result['files_found']}, Deleted: {step_result['files_deleted']}, "
              f"Freed: {step_result['bytes_freed']/1024:.1f}KB")

    if args.bst_check or args.all:
        bst = is_bst()
        offset = get_london_offset()
        sessions = compute_session_times_utc()
        print(f"\n=== ISS-020: BST Check ===")
        print(f"London offset: UTC{'+' if offset.total_seconds() >= 0 else ''}{int(offset.total_seconds()/3600)}h "
              f"({'BST' if bst else 'GMT'})")
        print(f"LSE: {sessions['lse']['open_utc']} - {sessions['lse']['close_utc']} UTC")
        print(f"Nightly Ouroboros: {sessions['nightly_ouroboros_utc']} UTC")
        print(f"Dark window: {sessions['dark_window']['start_utc']} - {sessions['dark_window']['end_utc']} UTC")

    if args.mem_check or args.all:
        mem = check_system_memory()
        print(f"\n=== SC-19: System Memory ===")
        print(f"Total:     {mem['total_mb']:,.0f} MB")
        print(f"Available: {mem['available_mb']:,.0f} MB")
        print(f"Used:      {mem['used_pct']:.1f}%")
        print(f"Swap used: {mem['swap_used_mb']:,.0f} MB")
        print(f"Source:    {mem['source']}")
        if mem['warning']:
            print(f"WARNING:   {mem['warning']}")

    if args.step_health or args.all:
        health = check_step_health()
        print(f"\n=== SC-19: Step Health ({len(health)} steps) ===")
        if not health:
            print("No step logs found (step_runner has not been used yet)")
        else:
            # Header
            print(f"{'Step':<28s} {'Status':<12s} {'Exit':<6s} {'Duration':<10s} "
                  f"{'RSS MB':<8s} {'Age (h)':<8s}")
            print("-" * 80)
            for h in health:
                dur_str = f"{h['duration_sec']:.1f}s"
                rss_str = f"{h['peak_rss_mb']:.0f}" if h['peak_rss_mb'] > 0 else "-"
                print(f"{h['step']:<28s} {h['status']:<12s} {h['exit_code']:<6d} "
                      f"{dur_str:<10s} {rss_str:<8s} {h['age_hours']:<8.1f}")
                if h.get('skip_reason'):
                    print(f"  >> {h['skip_reason']}")

    if args.checkpoint_circuit_breaker or args.all:
        state = checkpoint_circuit_breaker()
        print(f"\n=== R21-16b: Circuit Breaker Checkpoint ===")
        print(f"Tier:          {state['tier']}")
        print(f"Daily loss:    {state['daily_loss_pct']:.2f}%")
        print(f"Max drawdown:  {state['max_drawdown_pct']:.2f}%")
        print(f"Positions:     {state['positions_count']}")
        print(f"Risk state:    {state.get('risk_state', 'N/A')}")
        print(f"Timestamp:     {state['timestamp']}")

    if args.restore_circuit_breaker:
        state = restore_circuit_breaker()
        print(f"\n=== R21-16b: Circuit Breaker Restore ===")
        print(f"Tier:          {state['tier']}")
        print(f"Daily loss:    {state['daily_loss_pct']:.2f}%")
        print(f"Max drawdown:  {state['max_drawdown_pct']:.2f}%")
        print(f"Positions:     {state['positions_count']}")
        print(f"Restored from: {state.get('restored_from', 'N/A')}")
        print(f"Timestamp:     {state['timestamp']}")


if __name__ == "__main__":
    main()
