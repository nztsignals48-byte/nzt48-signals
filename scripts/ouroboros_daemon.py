"""Ouroboros daemon — runs ouroboros_nightly at 23:30 UTC every day.

Simpler than launchd; just a sleep-until-next-run loop.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

SCHEDULE_HOUR = 23
SCHEDULE_MIN = 30


def _next_run_delay() -> float:
    now = datetime.now(timezone.utc)
    tgt = now.replace(hour=SCHEDULE_HOUR, minute=SCHEDULE_MIN, second=0, microsecond=0)
    if tgt <= now:
        tgt += timedelta(days=1)
    return (tgt - now).total_seconds()


async def main() -> None:
    log.info("ouroboros daemon up; next run in %.1f hours", _next_run_delay() / 3600)
    while True:
        await asyncio.sleep(_next_run_delay())
        log.info("running ouroboros_nightly...")
        # Run v1 (realised-only) + v2 (realised + unrealised per-strategy)
        for script in ("ouroboros_nightly.py", "ouroboros_v2_nightly.py"):
            try:
                r = subprocess.run(
                    [sys.executable, f"/Users/rr/aegis-v5/scripts/{script}"],
                    capture_output=True, text=True, timeout=600,
                )
                log.info("%s rc=%d stdout=%s stderr=%s", script, r.returncode,
                         r.stdout[-200:], r.stderr[-200:])
            except Exception as e:
                log.exception("%s failed: %s", script, e)


if __name__ == "__main__":
    asyncio.run(main())
