"""Ouroboros CLI — nightly analytics runner.

Usage: python -m ouroboros.cli [--config-dir PATH] [--wal-path PATH] [--day-count N]

Runs after LSE close (23:45 ET). Reads the day's WAL journal,
produces dynamic_weights.toml and universe_classification.toml.

Client ID: 200 (H41).
"""

from __future__ import annotations

import argparse
import atexit
import sys
import traceback
from pathlib import Path

from .config import CLIENT_ID, LSE_CLOSE_SECS, LSE_OPEN_SECS
from .pipeline import run_pipeline
from .toml_writer import flush_all


# P2-B: Register atexit handler as backup to ensure TOML files are flushed
# even if the pipeline crashes without reaching the finally block.
atexit.register(flush_all)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ouroboros nightly analytics")
    parser.add_argument(
        "--config-dir", type=str, default="config",
        help="Path to config/ directory",
    )
    parser.add_argument(
        "--wal-path", type=str, required=True,
        help="Path to day's WAL ndjson file",
    )
    parser.add_argument(
        "--day-count", type=int, default=1,
        help="Number of days Ouroboros has run (for cold start)",
    )
    parser.add_argument(
        "--london-time-secs", type=int, default=23 * 3600,
        help="Current London time in seconds from midnight",
    )
    args = parser.parse_args()

    config_dir = Path(args.config_dir)
    wal_path = Path(args.wal_path)

    print(f"Ouroboros nightly analytics (client_id={CLIENT_ID})")
    print(f"  WAL: {wal_path}")
    print(f"  Config: {config_dir}")
    print(f"  Day count: {args.day_count}")

    try:
        result = run_pipeline(
            wal_path=wal_path,
            config_dir=config_dir,
            london_time_secs=args.london_time_secs,
            day_count=args.day_count,
        )
    except Exception:
        # P2-B: Log crash traceback before cleanup
        print("CRASH during pipeline execution:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        # P2-B: Ensure TOML files are flushed even on crash
        flush_all()

    if not result.success:
        print(f"FAILED: {result.error}", file=sys.stderr)
        return 1

    print("SUCCESS:")
    if result.cold_start:
        print("  Mode: Cold start (conservative defaults)")
    else:
        if result.bayesian:
            print(f"  Bayesian WR: {result.bayesian.bayesian_win_rate:.1%}")
            print(f"  Trades: {result.bayesian.trade_count}")
        if result.dsr:
            print(f"  DSR: {result.dsr.dsr:.4f} (significant={result.dsr.is_significant})")
        if result.exit_cal:
            print(f"  Chandelier mult: {result.exit_cal.new_multiplier:.2f}")
    print(f"  dynamic_weights: {result.dynamic_weights_path}")
    print(f"  universe_class: {result.universe_class_path}")
    if result.archive_path:
        print(f"  Archive: {result.archive_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
