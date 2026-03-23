#!/usr/bin/env python3
"""
update_universe.py — Daily universe refresh pipeline.

Orchestrates the two-step process:
  1. Re-scrape Wikipedia for FTSE100, FTSE250, S&P500, NASDAQ-100
     (delegates to build_universe.py)
  2. Sync any new tickers into contracts.toml
     (delegates to sync_universe.py)

Idempotent. Safe to run daily via cron.

Usage:
    python scripts/update_universe.py                # scrape + sync (dry run)
    python scripts/update_universe.py --apply        # scrape + sync (write changes)
    python scripts/update_universe.py --sync-only    # skip scrape, just sync
    python scripts/update_universe.py --scrape-only  # just scrape, no sync

Cron example (run at 04:55 UTC daily, after market close everywhere):
    55 4 * * * cd /home/ubuntu/nzt48-aegis-v2 && python3 scripts/update_universe.py --apply >> /app/data/universe_update.log 2>&1
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
UNIVERSE_PATH = PROJECT_ROOT / "config" / "universe.json"
CONTRACTS_PATH = PROJECT_ROOT / "config" / "contracts.toml"
BUILD_SCRIPT = SCRIPT_DIR / "build_universe.py"
SYNC_SCRIPT = SCRIPT_DIR / "sync_universe.py"


def load_universe_meta() -> dict:
    """Load metadata from existing universe.json (if it exists)."""
    if not UNIVERSE_PATH.exists():
        return {}
    with open(UNIVERSE_PATH) as f:
        data = json.load(f)
    return data.get("metadata", {})


def run_build_universe() -> bool:
    """
    Run build_universe.py to re-scrape Wikipedia indices.
    Returns True on success, False on failure.
    """
    log.info("=" * 60)
    log.info("STEP 1: Re-scraping Wikipedia indices → universe.json")
    log.info("=" * 60)

    old_meta = load_universe_meta()
    old_total = old_meta.get("total", 0)

    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT)],
        cwd=str(PROJECT_ROOT),
        capture_output=False,
    )

    if result.returncode != 0:
        log.error(f"build_universe.py failed with exit code {result.returncode}")
        return False

    new_meta = load_universe_meta()
    new_total = new_meta.get("total", 0)
    delta = new_total - old_total

    log.info(f"Universe updated: {old_total} → {new_total} tickers (delta: {delta:+d})")
    return True


def run_sync_universe(apply: bool) -> bool:
    """
    Run sync_universe.py to sync universe.json → contracts.toml.
    Returns True on success, False on failure.
    """
    log.info("=" * 60)
    log.info("STEP 2: Syncing universe.json → contracts.toml")
    log.info("=" * 60)

    cmd = [sys.executable, str(SYNC_SCRIPT)]
    if apply:
        cmd.append("--apply")

    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=False,
    )

    if result.returncode != 0:
        log.error(f"sync_universe.py failed with exit code {result.returncode}")
        return False

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Daily universe refresh: scrape indices + sync contracts"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to contracts.toml (default: dry run)",
    )
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Skip Wikipedia scrape, just sync existing universe.json → contracts.toml",
    )
    parser.add_argument(
        "--scrape-only",
        action="store_true",
        help="Only scrape Wikipedia → universe.json, don't sync contracts.toml",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    log.info(f"Universe update pipeline started at {now}")

    success = True

    # Step 1: Scrape (unless --sync-only)
    if not args.sync_only:
        if not run_build_universe():
            log.error("Scrape step failed. Aborting.")
            return 1
    else:
        log.info("Skipping scrape (--sync-only)")

    # Step 2: Sync (unless --scrape-only)
    if not args.scrape_only:
        if not run_sync_universe(apply=args.apply):
            log.error("Sync step failed.")
            return 1
    else:
        log.info("Skipping sync (--scrape-only)")

    log.info("Universe update pipeline complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
