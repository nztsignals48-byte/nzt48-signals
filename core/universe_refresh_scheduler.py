"""
NZT-48 Universe Refresh Scheduler
==================================
Implements dynamic universe refresh schedule:
  - 15 minutes pre-session: Full initial scan
  - First hour: Every 15 minutes (catch early runners)
  - Rest of session: Every 60 minutes (catch mid-session momentum)
  - Between sessions: Continuous monitoring

Phase Timeline (UTC):
  Phase 1 (LSE+Euro):  08:00-14:30 (6.5h)
  Phase 2 (LSE+US):    14:30-16:30 (2h)
  Phase 3 (US only):   16:30-21:00 (4.5h)
  Phase 4 (US close):  21:00-22:00 (1h)
  Phase 5 (Asia):      22:00-08:00 (10h)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, Dict, List
from zoneinfo import ZoneInfo

logger = logging.getLogger("nzt48.core.universe_refresh_scheduler")

UTC = ZoneInfo("UTC")
UK_TZ = ZoneInfo("Europe/London")


class Phase(Enum):
    """Trading phases."""
    PHASE_1 = "phase_1"  # LSE + European (08:00-14:30 UTC)
    PHASE_2 = "phase_2"  # LSE + US (14:30-16:30 UTC)
    PHASE_3 = "phase_3"  # US only (16:30-21:00 UTC)
    PHASE_4 = "phase_4"  # US close + Asia warmup (21:00-22:00 UTC)
    PHASE_5 = "phase_5"  # Asia (22:00-08:00 UTC)
    BETWEEN = "between"  # Between sessions


class ScanType(Enum):
    """Type of universe scan."""
    INITIAL = "initial"              # 15 min pre-session
    HOUR_1_REFRESH_1 = "h1_r1"      # +15 min in hour 1
    HOUR_1_REFRESH_2 = "h1_r2"      # +30 min in hour 1
    HOUR_1_REFRESH_3 = "h1_r3"      # +45 min in hour 1
    HOURLY = "hourly"                # Hourly scans
    CONTINUOUS = "continuous"        # Between sessions


@dataclass
class RefreshSchedule:
    """Scheduled refresh for a phase."""
    phase: Phase
    utc_time: datetime
    scan_type: ScanType
    description: str
    lookback_minutes: int = 5  # How far back to look for new runners

    def __repr__(self) -> str:
        return (
            f"<RefreshSchedule {self.phase.value} @ "
            f"{self.utc_time.strftime('%H:%M')} UTC - {self.scan_type.value}>"
        )


@dataclass
class TickerProfile:
    """Ticker profile with volatility classification."""
    ticker: str
    daily_range_pct: float  # Average daily range as %
    tier: str  # "conservative", "moderate", "volatile", "scalp"
    liquidity_score: float  # 0-1 (bid-ask spread, volume)
    isa_eligible: bool  # ISA compliance
    holding_style: str  # "swing" (hours), "scalp" (same-day), "momentum" (minutes)

    def __post_init__(self):
        """Auto-classify volatility tier based on daily range."""
        if self.daily_range_pct <= 3.0:
            self.tier = "conservative"
            self.holding_style = "swing"
        elif self.daily_range_pct <= 7.0:
            self.tier = "moderate"
            self.holding_style = "scalp"
        elif self.daily_range_pct <= 15.0:
            self.tier = "volatile"
            self.holding_style = "scalp"
        else:
            self.tier = "extreme"
            self.holding_style = "momentum"  # Minute-level entries/exits only


@dataclass
class UniverseSnapshot:
    """Snapshot of universe at a point in time."""
    timestamp: datetime
    phase: Phase
    scan_type: ScanType
    lse_tickers: List[str] = field(default_factory=list)
    euro_tickers: List[str] = field(default_factory=list)
    us_tickers: List[str] = field(default_factory=list)
    asia_tickers: List[str] = field(default_factory=list)
    total_count: int = 0
    new_runners: List[str] = field(default_factory=list)  # Detected this scan
    removed_tickers: List[str] = field(default_factory=list)  # Halted/delisted this scan
    ticker_profiles: Dict[str, TickerProfile] = field(default_factory=dict)  # Per-ticker analysis

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "phase": self.phase.value,
            "scan_type": self.scan_type.value,
            "lse_count": len(self.lse_tickers),
            "euro_count": len(self.euro_tickers),
            "us_count": len(self.us_tickers),
            "asia_count": len(self.asia_tickers),
            "total_count": self.total_count,
            "new_runners": self.new_runners,
            "removed_tickers": self.removed_tickers,
            "ticker_profiles": {
                ticker: {
                    "tier": profile.tier,
                    "daily_range_pct": profile.daily_range_pct,
                    "holding_style": profile.holding_style,
                    "liquidity_score": profile.liquidity_score,
                }
                for ticker, profile in self.ticker_profiles.items()
            },
        }


class UniverseRefreshScheduler:
    """Manages dynamic universe refresh schedule across all phases."""

    def __init__(self, artifacts_dir: Optional[Path] = None):
        self.artifacts_dir = artifacts_dir or Path(__file__).parent.parent / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.refresh_log_path = self.artifacts_dir / "universe_refreshes.json"

        self.current_phase: Optional[Phase] = None
        self.phase_start_time: Optional[datetime] = None
        self.last_refresh: Optional[datetime] = None
        self.last_snapshot: Optional[UniverseSnapshot] = None
        self.refresh_history: List[UniverseSnapshot] = []

        # Callbacks for actual universe scanning
        self.on_initial_scan: Optional[Callable] = None
        self.on_refresh_scan: Optional[Callable] = None
        self.on_runner_detected: Optional[Callable] = None
        self.on_ticker_removed: Optional[Callable] = None

    def get_current_phase(self, now: Optional[datetime] = None) -> Phase:
        """Determine which phase we're in based on UTC time."""
        if now is None:
            now = datetime.now(UTC)

        hour = now.hour
        minute = now.minute

        # Phase 1: LSE + European (08:00-14:30 UTC)
        if 8 <= hour < 14 or (hour == 14 and minute < 30):
            return Phase.PHASE_1

        # Phase 2: LSE + US (14:30-16:30 UTC)
        elif (hour == 14 and minute >= 30) or (15 <= hour < 16) or (hour == 16 and minute < 30):
            return Phase.PHASE_2

        # Phase 3: US only (16:30-21:00 UTC)
        elif (hour == 16 and minute >= 30) or (17 <= hour < 21):
            return Phase.PHASE_3

        # Phase 4: US close + Asia warmup (21:00-22:00 UTC)
        elif 21 <= hour < 22:
            return Phase.PHASE_4

        # Phase 5: Asia (22:00-08:00 UTC)
        else:
            return Phase.PHASE_5

    def get_next_refresh_times(self, now: Optional[datetime] = None) -> List[RefreshSchedule]:
        """Get all upcoming refresh times for today/tomorrow."""
        if now is None:
            now = datetime.now(UTC)

        schedules: List[RefreshSchedule] = []

        # Phase 1: LSE + Euro (08:00-14:30 UTC)
        phase1_open = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now < phase1_open:
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_1,
                utc_time=phase1_open.replace(hour=7, minute=45),
                scan_type=ScanType.INITIAL,
                description="Phase 1: LSE + Euro initial scan"
            ))
        if now < phase1_open.replace(hour=8, minute=15):
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_1,
                utc_time=phase1_open.replace(hour=8, minute=15),
                scan_type=ScanType.HOUR_1_REFRESH_1,
                description="Phase 1: First hour refresh #1 (check new LSE ETPs)"
            ))
        if now < phase1_open.replace(hour=8, minute=30):
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_1,
                utc_time=phase1_open.replace(hour=8, minute=30),
                scan_type=ScanType.HOUR_1_REFRESH_2,
                description="Phase 1: First hour refresh #2 (catch early runners)"
            ))
        if now < phase1_open.replace(hour=8, minute=45):
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_1,
                utc_time=phase1_open.replace(hour=8, minute=45),
                scan_type=ScanType.HOUR_1_REFRESH_3,
                description="Phase 1: First hour refresh #3 (lock universe)"
            ))

        # Phase 1 hourly: 09:00, 10:00, 11:00, 12:00, 13:00, 14:00
        for hour in range(9, 15):
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_1,
                utc_time=phase1_open.replace(hour=hour, minute=0),
                scan_type=ScanType.HOURLY,
                description=f"Phase 1: Hourly refresh @ {hour:02d}:00 UTC"
            ))

        # Phase 2: LSE + US (14:30-16:30 UTC)
        phase2_open = now.replace(hour=14, minute=30, second=0, microsecond=0)
        if now < phase2_open:
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_2,
                utc_time=phase2_open.replace(hour=14, minute=15),
                scan_type=ScanType.INITIAL,
                description="Phase 2: LSE + US initial scan"
            ))
        if now < phase2_open.replace(hour=14, minute=45):
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_2,
                utc_time=phase2_open.replace(hour=14, minute=45),
                scan_type=ScanType.HOUR_1_REFRESH_1,
                description="Phase 2: First hour refresh #1 (pre-market movers)"
            ))
        if now < phase2_open.replace(hour=15, minute=0):
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_2,
                utc_time=phase2_open.replace(hour=15, minute=0),
                scan_type=ScanType.HOUR_1_REFRESH_2,
                description="Phase 2: First hour refresh #2 (NYSE just opened)"
            ))
        if now < phase2_open.replace(hour=15, minute=15):
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_2,
                utc_time=phase2_open.replace(hour=15, minute=15),
                scan_type=ScanType.HOUR_1_REFRESH_3,
                description="Phase 2: First hour refresh #3 (lock universe)"
            ))

        # Phase 2 hourly: 16:00
        schedules.append(RefreshSchedule(
            phase=Phase.PHASE_2,
            utc_time=phase2_open.replace(hour=16, minute=0),
            scan_type=ScanType.HOURLY,
            description="Phase 2: Hourly refresh @ 16:00 UTC (US peak)"
        ))

        # Phase 3: US only (16:30-21:00 UTC)
        phase3_open = now.replace(hour=16, minute=30, second=0, microsecond=0)
        if now < phase3_open:
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_3,
                utc_time=phase3_open.replace(hour=16, minute=15),
                scan_type=ScanType.INITIAL,
                description="Phase 3: US only initial scan"
            ))
        if now < phase3_open.replace(hour=16, minute=45):
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_3,
                utc_time=phase3_open.replace(hour=16, minute=45),
                scan_type=ScanType.HOUR_1_REFRESH_1,
                description="Phase 3: First hour refresh #1 (afternoon runners)"
            ))
        if now < phase3_open.replace(hour=17, minute=0):
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_3,
                utc_time=phase3_open.replace(hour=17, minute=0),
                scan_type=ScanType.HOUR_1_REFRESH_2,
                description="Phase 3: First hour refresh #2 (US afternoon activity)"
            ))
        if now < phase3_open.replace(hour=17, minute=45):
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_3,
                utc_time=phase3_open.replace(hour=17, minute=45),
                scan_type=ScanType.HOUR_1_REFRESH_3,
                description="Phase 3: First hour refresh #3 (lock universe)"
            ))

        # Phase 3 hourly: 17:30, 18:30, 19:30, 20:30
        for hour in [17, 18, 19, 20]:
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_3,
                utc_time=phase3_open.replace(hour=hour, minute=30),
                scan_type=ScanType.HOURLY,
                description=f"Phase 3: Hourly refresh @ {hour:02d}:30 UTC"
            ))

        # Phase 4: US close + Asia warmup (21:00-22:00 UTC)
        phase4_open = now.replace(hour=21, minute=0, second=0, microsecond=0)
        if now < phase4_open:
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_4,
                utc_time=phase4_open.replace(hour=20, minute=45),
                scan_type=ScanType.INITIAL,
                description="Phase 4: US close + Asia warmup initial"
            ))
        if now < phase4_open.replace(hour=21, minute=30):
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_4,
                utc_time=phase4_open.replace(hour=21, minute=30),
                scan_type=ScanType.HOURLY,
                description="Phase 4: Single refresh (Asia ready check)"
            ))

        # Phase 5: Asia (22:00-08:00 UTC)
        phase5_open = now.replace(hour=22, minute=0, second=0, microsecond=0)
        if now < phase5_open:
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_5,
                utc_time=phase5_open.replace(hour=21, minute=45),
                scan_type=ScanType.INITIAL,
                description="Phase 5: Asia initial scan"
            ))
        if now < phase5_open.replace(hour=22, minute=15):
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_5,
                utc_time=phase5_open.replace(hour=22, minute=15),
                scan_type=ScanType.HOUR_1_REFRESH_1,
                description="Phase 5: First hour refresh #1 (new Asia runners)"
            ))
        if now < phase5_open.replace(hour=22, minute=30):
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_5,
                utc_time=phase5_open.replace(hour=22, minute=30),
                scan_type=ScanType.HOUR_1_REFRESH_2,
                description="Phase 5: First hour refresh #2 (Asia market warming)"
            ))
        if now < phase5_open.replace(hour=22, minute=45):
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_5,
                utc_time=phase5_open.replace(hour=22, minute=45),
                scan_type=ScanType.HOUR_1_REFRESH_3,
                description="Phase 5: First hour refresh #3 (lock universe)"
            ))

        # Phase 5 hourly: 23:00, 00:00, 01:00, 02:00, 03:00, 04:00, 05:00, 06:00, 07:00
        for hour in [23, 0, 1, 2, 3, 4, 5, 6, 7]:
            schedules.append(RefreshSchedule(
                phase=Phase.PHASE_5,
                utc_time=phase5_open.replace(hour=hour, minute=0),
                scan_type=ScanType.HOURLY,
                description=f"Phase 5: Hourly refresh @ {hour:02d}:00 UTC"
            ))

        # Filter to future times only
        schedules = [s for s in schedules if s.utc_time > now]
        schedules.sort(key=lambda s: s.utc_time)

        return schedules

    async def execute_refresh(
        self,
        schedule: RefreshSchedule,
        universe_scanner_fn: Optional[Callable] = None,
    ) -> UniverseSnapshot:
        """Execute a scheduled universe refresh.

        Args:
            schedule: The refresh schedule to execute
            universe_scanner_fn: Async function that returns universe state

        Returns:
            UniverseSnapshot of the scan result
        """
        now = datetime.now(UTC)
        logger.info(
            "Executing universe refresh: %s at %s",
            schedule.scan_type.value,
            now.strftime("%H:%M:%S UTC")
        )

        # Call the actual universe scanner
        if universe_scanner_fn:
            try:
                result = await universe_scanner_fn(schedule)
                snapshot = result if isinstance(result, UniverseSnapshot) else None
            except Exception as e:
                logger.error(f"Universe scanner failed: {e}")
                snapshot = UniverseSnapshot(
                    timestamp=now,
                    phase=schedule.phase,
                    scan_type=schedule.scan_type,
                    total_count=0,
                )
        else:
            # Placeholder if no scanner provided
            snapshot = UniverseSnapshot(
                timestamp=now,
                phase=schedule.phase,
                scan_type=schedule.scan_type,
                total_count=0,
            )

        # Update state
        self.last_refresh = now
        self.last_snapshot = snapshot
        self.refresh_history.append(snapshot)
        self.current_phase = schedule.phase
        self.phase_start_time = now

        # Log to artifacts
        self._log_refresh(snapshot)

        # Fire callbacks
        if schedule.scan_type == ScanType.INITIAL and self.on_initial_scan:
            await asyncio.coroutine(self.on_initial_scan)(snapshot)
        elif schedule.scan_type in [
            ScanType.HOUR_1_REFRESH_1,
            ScanType.HOUR_1_REFRESH_2,
            ScanType.HOUR_1_REFRESH_3,
            ScanType.HOURLY,
        ] and self.on_refresh_scan:
            await asyncio.coroutine(self.on_refresh_scan)(snapshot)

        if snapshot.new_runners and self.on_runner_detected:
            for runner in snapshot.new_runners:
                await asyncio.coroutine(self.on_runner_detected)(runner, snapshot)

        if snapshot.removed_tickers and self.on_ticker_removed:
            for ticker in snapshot.removed_tickers:
                await asyncio.coroutine(self.on_ticker_removed)(ticker, snapshot)

        return snapshot

    def _log_refresh(self, snapshot: UniverseSnapshot) -> None:
        """Log refresh to artifacts file."""
        try:
            existing = {}
            if self.refresh_log_path.exists():
                with open(self.refresh_log_path) as f:
                    existing = json.load(f)

            # Keep last 100 refreshes per phase
            phase_key = snapshot.phase.value
            if phase_key not in existing:
                existing[phase_key] = []

            existing[phase_key].append(snapshot.to_dict())
            existing[phase_key] = existing[phase_key][-100:]

            with open(self.refresh_log_path, "w") as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to log refresh: {e}")

    def get_stats(self) -> dict:
        """Get refresh statistics."""
        return {
            "current_phase": self.current_phase.value if self.current_phase else None,
            "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
            "total_refreshes": len(self.refresh_history),
            "last_snapshot": self.last_snapshot.to_dict() if self.last_snapshot else None,
        }
