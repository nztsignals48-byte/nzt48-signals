"""Maintenance Utilities — Ouroboros infrastructure module.

ISS-017:  Persistent memory read-back (close learning feedback loop)
v19-P2-15: Parquet orphan cleanup (prevent disk fill from intermediate files)
ISS-020:  BST/DST awareness helper for crontab scheduling

Usage: python3 -m python_brain.ouroboros.maintenance [--cleanup|--read-memory|--bst-check]
"""

from __future__ import annotations

import glob
import json
import logging
import os
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
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Maintenance utilities")
    parser.add_argument("--read-memory", action="store_true", help="ISS-017: Read persistent memory")
    parser.add_argument("--cleanup", action="store_true", help="v19-P2-15: Clean Parquet orphans")
    parser.add_argument("--cleanup-dry-run", action="store_true", help="v19-P2-15: Dry run cleanup")
    parser.add_argument("--bst-check", action="store_true", help="ISS-020: Check BST status")
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

    if args.cleanup or args.all:
        result = cleanup_parquet_orphans()
        print(f"\n=== v19-P2-15: Parquet Cleanup ===")
        print(f"Found: {result['files_found']}, Deleted: {result['files_deleted']}, "
              f"Freed: {result['bytes_freed']/1024:.1f}KB")

    if args.cleanup_dry_run:
        result = cleanup_parquet_orphans(dry_run=True)
        print(f"\n=== v19-P2-15: Parquet Cleanup (DRY RUN) ===")
        print(f"Would delete: {result['files_found']} files")

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


if __name__ == "__main__":
    main()
