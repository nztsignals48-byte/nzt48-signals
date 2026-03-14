"""
NZT-48 CloudWatch Metrics Emitter (H-10)
=========================================
Emits system health metrics to AWS CloudWatch every 60 seconds.

Metrics (Namespace: NZT48):
  - cpu_percent:        CPU usage percentage (0-100)
  - memory_percent:     RSS memory as percentage of system RAM
  - disk_percent:       Disk usage percentage on / partition
  - scan_loop_health:   1 = OK/DEGRADED, 0 = HALTED or missed
  - positions_open:     Number of currently open positions
  - daily_pnl_pct:      Daily P&L percentage

Alarm thresholds (set up manually in CloudWatch console):
  - CPU > 80% for 5 min sustained           -> WARNING alarm
  - Memory > 90%                             -> CRITICAL alarm
  - scan_loop_health == 0 for 3 cycles       -> CRITICAL alarm (scan missed)
  - No heartbeat (no datapoints) for 10 min  -> CRITICAL alarm (process dead)

Graceful degradation:
  - If boto3 is not installed, logs WARNING and becomes a no-op.
  - If AWS credentials are not configured, logs WARNING on first failure and skips.
  - Never crashes the engine -- all errors are caught and logged.

References:
  AWS CloudWatch PutMetricData API
  https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_PutMetricData.html
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.state_manager import StateManager

logger = logging.getLogger("nzt48.cloudwatch")

# CloudWatch namespace for all NZT-48 metrics
_NAMESPACE = "NZT48"

# Metric names
_METRIC_CPU = "cpu_percent"
_METRIC_MEMORY = "memory_percent"
_METRIC_DISK = "disk_percent"
_METRIC_SCAN_HEALTH = "scan_loop_health"
_METRIC_POSITIONS = "positions_open"
_METRIC_DAILY_PNL = "daily_pnl_pct"


class CloudWatchMetricsEmitter:
    """Emits system and trading metrics to AWS CloudWatch.

    Safe to call even if boto3 or AWS credentials are not available --
    degrades gracefully with logged warnings (never crashes).

    Usage:
        emitter = CloudWatchMetricsEmitter()
        emitter.emit()  # called every 60s by APScheduler
    """

    def __init__(self, region: str = "eu-west-2") -> None:
        self._region = region
        self._client = None
        self._available = False
        self._init_failed_logged = False
        self._emit_error_count = 0
        self._max_consecutive_errors = 5  # stop spamming logs after 5 consecutive failures
        self._init_client()

    def _init_client(self) -> None:
        """Try to initialise the boto3 CloudWatch client.
        Logs WARNING and sets _available=False if boto3 or credentials are missing.
        """
        try:
            import boto3
            self._client = boto3.client("cloudwatch", region_name=self._region)
            # Test credentials with a dry-run (list metrics is cheap)
            self._client.list_metrics(Namespace=_NAMESPACE, MaxResults=1)
            self._available = True
            logger.info("CloudWatch metrics emitter initialised (namespace=%s, region=%s)",
                        _NAMESPACE, self._region)
        except ImportError:
            logger.warning("CloudWatch metrics DISABLED: boto3 not installed. "
                           "Install with: pip install boto3")
            self._available = False
        except Exception as e:
            logger.warning("CloudWatch metrics DISABLED: AWS credentials not configured "
                           "or CloudWatch not reachable: %s", e)
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def emit(self,
             state_manager: Optional[StateManager] = None,
             scan_health_tracker=None) -> None:
        """Collect and emit all metrics to CloudWatch.

        Args:
            state_manager: Optional StateManager for position count and daily P&L.
            scan_health_tracker: Optional ScanHealthTracker for loop health status.
        """
        if not self._available:
            return

        try:
            metric_data = []
            now = datetime.now(timezone.utc)

            # ── System metrics (psutil) ──────────────────────────────
            cpu_pct, mem_pct, disk_pct = self._collect_system_metrics()

            if cpu_pct is not None:
                metric_data.append(self._metric(_METRIC_CPU, cpu_pct, "Percent", now))
            if mem_pct is not None:
                metric_data.append(self._metric(_METRIC_MEMORY, mem_pct, "Percent", now))
            if disk_pct is not None:
                metric_data.append(self._metric(_METRIC_DISK, disk_pct, "Percent", now))

            # ── Scan loop health ─────────────────────────────────────
            scan_ok = self._collect_scan_health(scan_health_tracker)
            metric_data.append(self._metric(_METRIC_SCAN_HEALTH, scan_ok, "None", now))

            # ── Trading metrics (positions + daily P&L) ──────────────
            positions, daily_pnl = self._collect_trading_metrics(state_manager)
            metric_data.append(self._metric(_METRIC_POSITIONS, positions, "Count", now))
            metric_data.append(self._metric(_METRIC_DAILY_PNL, daily_pnl, "Percent", now))

            # ── Emit batch ───────────────────────────────────────────
            if metric_data:
                self._client.put_metric_data(
                    Namespace=_NAMESPACE,
                    MetricData=metric_data,
                )
                self._emit_error_count = 0
                logger.debug(
                    "CloudWatch: emitted %d metrics (CPU=%.1f%% MEM=%.1f%% DISK=%.1f%% "
                    "scan=%d pos=%d pnl=%.2f%%)",
                    len(metric_data),
                    cpu_pct or 0, mem_pct or 0, disk_pct or 0,
                    scan_ok, positions, daily_pnl,
                )

        except Exception as e:
            self._emit_error_count += 1
            if self._emit_error_count <= self._max_consecutive_errors:
                logger.warning("CloudWatch emit failed (%d/%d): %s",
                               self._emit_error_count, self._max_consecutive_errors, e)
            elif self._emit_error_count == self._max_consecutive_errors + 1:
                logger.warning("CloudWatch emit: suppressing further warnings after %d failures",
                               self._max_consecutive_errors)

    # ── Private helpers ──────────────────────────────────────────────

    @staticmethod
    def _metric(name: str, value: float, unit: str, timestamp: datetime) -> dict:
        """Build a single CloudWatch MetricDatum dict."""
        return {
            "MetricName": name,
            "Timestamp": timestamp,
            "Value": float(value),
            "Unit": unit,
            "Dimensions": [
                {"Name": "Environment", "Value": "paper"},
            ],
        }

    @staticmethod
    def _collect_system_metrics() -> tuple[Optional[float], Optional[float], Optional[float]]:
        """Collect CPU, memory, and disk usage via psutil.
        Returns (cpu_pct, mem_pct, disk_pct) or None for each if psutil unavailable.
        """
        try:
            import psutil
            cpu_pct = psutil.cpu_percent(interval=None)  # non-blocking (uses cached value)
            mem = psutil.virtual_memory()
            mem_pct = mem.percent
            disk = psutil.disk_usage("/")
            disk_pct = disk.percent
            return cpu_pct, mem_pct, disk_pct
        except ImportError:
            logger.debug("psutil not available — system metrics skipped")
            return None, None, None
        except Exception as e:
            logger.debug("System metrics collection failed: %s", e)
            return None, None, None

    @staticmethod
    def _collect_scan_health(scan_health_tracker) -> int:
        """Check scan loop health: 1 = OK or DEGRADED, 0 = HALTED or unavailable.

        Falls back to ScanHealthTracker singleton if no tracker passed.
        """
        try:
            if scan_health_tracker is None:
                from core.scan_health import ScanHealthTracker
                scan_health_tracker = ScanHealthTracker.instance()

            health = scan_health_tracker.get_health()
            # ScanHealth dataclass has a .state field: "OK", "DEGRADED", "HALTED"
            return 0 if health.state == "HALTED" else 1
        except Exception:
            return 0  # conservative: report unhealthy if we can't check

    @staticmethod
    def _collect_trading_metrics(state_manager: Optional[StateManager]) -> tuple[int, float]:
        """Collect open position count and daily P&L percentage.

        Returns (positions_open, daily_pnl_pct).
        Falls back to SQLite if StateManager not available.
        """
        positions = 0
        daily_pnl = 0.0

        # Try StateManager (Redis) first
        if state_manager is not None:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Can't await in sync context -- use Redis directly
                    pass
                else:
                    pos = loop.run_until_complete(state_manager.get_all_positions())
                    positions = len(pos) if pos else 0
            except Exception:
                pass

        # Fall back to SQLite for positions + daily P&L
        try:
            from delivery.database import get_connection, get_daily_pnl
            import config as cfg
            conn = get_connection()
            try:
                # Count open virtual positions
                row = conn.execute(
                    "SELECT COUNT(*) FROM virtual_positions WHERE status = 'OPEN'"
                ).fetchone()
                if row:
                    positions = max(positions, row[0])

                # Get daily P&L
                pnl_dollars = get_daily_pnl(conn)
                equity = float(cfg.get("system.starting_equity", 10_000))
                if equity > 0:
                    daily_pnl = (pnl_dollars / equity) * 100.0
            finally:
                conn.close()
        except Exception:
            pass  # graceful — metrics default to 0

        return positions, round(daily_pnl, 4)
