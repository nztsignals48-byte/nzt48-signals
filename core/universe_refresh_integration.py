"""
NZT-48 Universe Refresh Integration
====================================
Integrates UniverseRefreshScheduler into the main APScheduler loop.

Adds scheduled universe refreshes to the existing APScheduler with dynamic
15-min refreshes in hour 1 of each session, then hourly refreshes thereafter.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, List
from zoneinfo import ZoneInfo

from core.universe_refresh_scheduler import (
    UniverseRefreshScheduler,
    RefreshSchedule,
    UniverseSnapshot,
    Phase,
    ScanType,
)

logger = logging.getLogger("nzt48.core.universe_refresh_integration")

UTC = ZoneInfo("UTC")


class UniverseRefreshIntegration:
    """Integration layer for universe refresh into main trading loop."""

    def __init__(
        self,
        scheduler,  # APScheduler instance
        artifacts_dir: Optional[Path] = None,
        universe_scan_fn: Optional[Callable] = None,
    ):
        """Initialize integration.

        Args:
            scheduler: APScheduler instance
            artifacts_dir: Directory for artifacts
            universe_scan_fn: Async function to perform actual universe scan
        """
        self.scheduler = scheduler
        self.refresh_scheduler = UniverseRefreshScheduler(artifacts_dir)
        self.universe_scan_fn = universe_scan_fn
        self.scheduled_jobs: dict[str, str] = {}  # Maps job_id -> apscheduler_id

    def setup(self) -> None:
        """Add all universe refresh jobs to APScheduler."""
        try:
            from apscheduler.triggers.cron import CronTrigger

            # Get all refresh schedules for this week
            schedules = self.refresh_scheduler.get_next_refresh_times()

            for schedule in schedules:
                self._add_refresh_job(schedule)

            logger.info(
                "Universe refresh integration set up with %d scheduled refreshes",
                len(self.scheduled_jobs),
            )
        except Exception as e:
            logger.error(f"Failed to set up universe refresh integration: {e}")

    def _add_refresh_job(self, schedule: RefreshSchedule) -> None:
        """Add a single refresh job to APScheduler."""
        try:
            from apscheduler.triggers.cron import CronTrigger

            # Create unique job ID
            job_id = (
                f"universe_refresh_{schedule.phase.value}_{schedule.scan_type.value}"
                f"_{schedule.utc_time.strftime('%H%M')}"
            )

            # Convert UTC time to cron trigger
            cron_trigger = CronTrigger(
                hour=schedule.utc_time.hour,
                minute=schedule.utc_time.minute,
                day_of_week="mon-fri",  # Only trading days
                timezone="UTC",
            )

            # Add job to scheduler
            job = self.scheduler.add_job(
                self._execute_refresh_wrapper,
                cron_trigger,
                kwargs={"schedule": schedule},
                id=job_id,
                name=schedule.description,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            self.scheduled_jobs[job_id] = job.id
            logger.debug(
                f"Added universe refresh job: {schedule.description} @ "
                f"{schedule.utc_time.strftime('%H:%M UTC')}"
            )
        except Exception as e:
            logger.error(f"Failed to add refresh job for {schedule}: {e}")

    async def _execute_refresh_wrapper(self, schedule: RefreshSchedule) -> None:
        """Wrapper to execute refresh and handle logging."""
        try:
            logger.info(
                f"Universe refresh starting: {schedule.description} "
                f"({schedule.scan_type.value})"
            )

            # Execute the actual refresh
            snapshot = await self.refresh_scheduler.execute_refresh(
                schedule,
                universe_scanner_fn=self.universe_scan_fn,
            )

            logger.info(
                f"Universe refresh complete: {snapshot.total_count} tickers "
                f"({len(snapshot.new_runners)} new runners, "
                f"{len(snapshot.removed_tickers)} removed)"
            )

            # Log to artifacts
            self._log_refresh_result(snapshot)

        except Exception as e:
            logger.error(f"Universe refresh failed: {e}", exc_info=True)

    def _log_refresh_result(self, snapshot: UniverseSnapshot) -> None:
        """Log refresh result to artifacts."""
        try:
            artifacts_dir = self.refresh_scheduler.artifacts_dir
            timestamp_str = snapshot.timestamp.strftime("%Y%m%d_%H%M%S")
            log_path = (
                artifacts_dir
                / f"refresh_{snapshot.phase.value}_{snapshot.scan_type.value}_{timestamp_str}.json"
            )

            import json

            with open(log_path, "w") as f:
                json.dump(snapshot.to_dict(), f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to log refresh result: {e}")

    def get_status(self) -> dict:
        """Get status of universe refresh scheduler."""
        return {
            "scheduled_jobs": len(self.scheduled_jobs),
            "scheduler_stats": self.refresh_scheduler.get_stats(),
            "last_refresh": (
                self.refresh_scheduler.last_refresh.isoformat()
                if self.refresh_scheduler.last_refresh
                else None
            ),
        }


def setup_universe_refresh_integration(
    scheduler,
    artifacts_dir: Optional[Path] = None,
    universe_scan_fn: Optional[Callable] = None,
) -> UniverseRefreshIntegration:
    """Factory function to set up universe refresh integration.

    Usage in main.py:
        from core.universe_refresh_integration import setup_universe_refresh_integration

        # In MasterOrchestrator.__init__ or setup_scheduler():
        self.universe_refresh_integration = setup_universe_refresh_integration(
            self.scheduler,
            artifacts_dir=Path("artifacts"),
            universe_scan_fn=self.scan_universe,  # async function
        )
        self.universe_refresh_integration.setup()

    Args:
        scheduler: APScheduler instance
        artifacts_dir: Directory for artifacts
        universe_scan_fn: Async function to perform universe scan

    Returns:
        UniverseRefreshIntegration instance
    """
    integration = UniverseRefreshIntegration(
        scheduler, artifacts_dir, universe_scan_fn
    )
    integration.setup()
    return integration
