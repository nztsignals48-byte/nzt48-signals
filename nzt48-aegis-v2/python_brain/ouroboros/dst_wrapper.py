"""DST-aware session PDF wrapper.

Session briefing PDFs are scheduled via cron at fixed UTC times, but the
correct trigger time depends on whether the UK is in GMT (UTC+0) or BST
(UTC+1). This wrapper computes the correct UTC time for each session based
on the current DST state of the relevant timezone, then delegates to
session_pdf if the current time is within a 10-minute window of the
correct trigger time.

Crontab calls this at the *earlier* of the two possible UTC times (the BST
time). The wrapper checks whether it is actually time to fire. If not (i.e.
we are in GMT and the cron fired too early), it exits silently.

Session trigger times in LOCAL time (Europe/London):
  - asian:     00:55 London
  - european:  07:55 London
  - american:  14:25 London
  - us_only:   16:30 London

During GMT these equal UTC. During BST, subtract 1 hour to get UTC.

Usage:
    python3 -m python_brain.ouroboros.dst_wrapper --session european --send-telegram
    python3 -m python_brain.ouroboros.dst_wrapper --session asian
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DSTWrapper] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("dst_wrapper")

# Session trigger times in Europe/London local time (hour, minute)
SESSION_LOCAL_TIMES = {
    "asian":     (0, 55),
    "european":  (7, 55),
    "american":  (14, 25),
    "us_only":   (16, 30),
}

# How far from the ideal UTC trigger we allow (minutes)
TOLERANCE_MINUTES = 10


def get_london_tz() -> ZoneInfo:
    """Return the Europe/London timezone."""
    return ZoneInfo("Europe/London")


def compute_utc_trigger(session: str) -> tuple[int, int]:
    """Compute the correct UTC (hour, minute) for a session based on current DST.

    Returns (hour, minute) in UTC that corresponds to the session's London
    local trigger time on today's date.
    """
    if session not in SESSION_LOCAL_TIMES:
        raise ValueError(f"Unknown session: {session}. Valid: {list(SESSION_LOCAL_TIMES.keys())}")

    london_hour, london_minute = SESSION_LOCAL_TIMES[session]
    london_tz = get_london_tz()
    now_utc = datetime.now(timezone.utc)

    # Build a London-local datetime for today at the target time
    london_now = now_utc.astimezone(london_tz)
    london_target = london_now.replace(hour=london_hour, minute=london_minute, second=0, microsecond=0)

    # Convert to UTC
    utc_target = london_target.astimezone(timezone.utc)
    return utc_target.hour, utc_target.minute


def is_within_window(target_hour: int, target_minute: int, tolerance_min: int = TOLERANCE_MINUTES) -> bool:
    """Check if current UTC time is within tolerance of the target."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

    delta = abs((now - target).total_seconds())
    return delta <= tolerance_min * 60


def run_session_pdf(session: str, send_telegram: bool = False) -> int:
    """Invoke session_pdf module for the given session."""
    cmd = [
        sys.executable, "-m", "python_brain.ouroboros.session_pdf",
        "--session", session,
    ]
    if send_telegram:
        cmd.append("--send-telegram")

    log.info("Delegating to session_pdf: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd="/app")
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="DST-aware wrapper for session PDF generation"
    )
    parser.add_argument(
        "--session",
        required=True,
        choices=list(SESSION_LOCAL_TIMES.keys()),
        help="Session name (asian/european/american/us_only)",
    )
    parser.add_argument(
        "--send-telegram",
        action="store_true",
        help="Forward --send-telegram to session_pdf",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip time window check, run immediately",
    )
    args = parser.parse_args()

    utc_hour, utc_minute = compute_utc_trigger(args.session)
    log.info(
        "Session '%s': London local %02d:%02d -> UTC trigger %02d:%02d",
        args.session,
        SESSION_LOCAL_TIMES[args.session][0],
        SESSION_LOCAL_TIMES[args.session][1],
        utc_hour,
        utc_minute,
    )

    if not args.force and not is_within_window(utc_hour, utc_minute):
        now = datetime.now(timezone.utc)
        log.info(
            "Not within %d-min window (now=%02d:%02d UTC, target=%02d:%02d UTC). Exiting.",
            TOLERANCE_MINUTES,
            now.hour,
            now.minute,
            utc_hour,
            utc_minute,
        )
        sys.exit(0)

    rc = run_session_pdf(args.session, send_telegram=args.send_telegram)
    sys.exit(rc)


if __name__ == "__main__":
    main()
