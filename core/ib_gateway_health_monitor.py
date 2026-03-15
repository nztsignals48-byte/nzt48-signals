"""
NZT-48 IB Gateway Health Monitor (Phase 2b)

3-LAYER RESILIENCE STACK:
1. Continuous connectivity checks via socket (every 30s)
2. Auto-restart after 3 consecutive failures (Docker container)
3. Market-aware alerts (10min before market open if unhealthy)

All health checks are non-blocking async operations.
Compatible with market_session_scheduler for timezone-aware alert timing.

---

ARCHITECTURE:

Layer 1: Connectivity Check
- Test port 4002 (paper) via socket.create_connection()
- 5-second timeout per check
- Non-blocking (returns immediately)
- Tracks failure count

Layer 2: Auto-Restart
- After 3 consecutive failures: docker-compose restart nzt48-ib-gateway
- Logged to Telegram (if configured)
- Resets failure counter on restart

Layer 3: Market-Aware Alerts
- Integrates with MarketSessionScheduler for timezone-aware timing
- Triggers alert 10min before LSE/US market open IF gateway unhealthy
- Alert includes: timestamp, status, action taken

---

INTEGRATION:
1. In main.py startup: initialize IBGatewayHealthMonitor(market_session_scheduler)
2. Start monitor_loop() as background async task
3. Before trading starts: call wait_for_ready() with timeout
4. During trading: monitor_loop() runs continuously (silent operation)
5. On failure: logs + Telegram alert + auto-restart
"""

from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Any

logger = logging.getLogger("nzt48.ib_gateway_health")


class IBGatewayHealthMonitor:
    """
    Continuous health monitoring of IB Gateway with 3-layer resilience.

    Attributes:
        host: IB Gateway hostname (default: 'localhost')
        port: IB Gateway API port (default: 4002 for paper)
        is_healthy: Current health status (bool)
        failure_count: Consecutive failures (reset on success)
        last_check: Timestamp of last check (datetime)
        market_scheduler: Optional MarketSessionScheduler for timezone-aware alerts
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 4002,
        market_scheduler: Optional[Any] = None,
        telegram_notifier: Optional[Any] = None,
    ):
        """
        Initialize IB Gateway health monitor.

        Args:
            host: Hostname where IB Gateway is running
            port: Port number (4002 for paper, 4004 for live)
            market_scheduler: Optional MarketSessionScheduler for timezone-aware alerts
            telegram_notifier: Optional Telegram notifier for alerts
        """
        self.host = host
        self.port = port
        self.is_healthy = True
        self.failure_count = 0
        self.last_check: Optional[datetime] = None
        self.max_failures_before_restart = 3
        self.market_scheduler = market_scheduler
        self.telegram_notifier = telegram_notifier
        self._restart_in_progress = False

    def check_connection(self) -> bool:
        """
        Test IB Gateway connectivity via socket.

        Performs a non-blocking socket connection test to port 4002.
        Updates last_check timestamp and failure count.

        Returns:
            True if port responsive and connection successful, False otherwise
        """
        try:
            sock = socket.create_connection(
                (self.host, self.port),
                timeout=5  # 5-second timeout
            )
            sock.close()
            self.last_check = datetime.now(timezone.utc)

            # Connection healthy — reset failure counter
            if self.failure_count > 0:
                logger.info(
                    "✅ IB Gateway recovered (was %d failures, now healthy)",
                    self.failure_count
                )
                self.failure_count = 0
            self.is_healthy = True
            return True

        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            self.last_check = datetime.now(timezone.utc)
            self.failure_count += 1
            self.is_healthy = False

            logger.warning(
                "⚠️  IB Gateway connection failed (attempt %d/3): %s",
                self.failure_count,
                type(e).__name__
            )
            return False

    async def monitor_loop(self, check_interval_seconds: int = 30):
        """
        Continuous health check loop (async background task).

        Runs indefinitely, checking gateway health every check_interval_seconds.
        On 3 consecutive failures: triggers Docker container restart.
        On recovery: resets failure counter.

        Args:
            check_interval_seconds: Interval between checks (default 30s)

        Note:
            This should be started as a background asyncio task in main.py:
            asyncio.create_task(monitor.monitor_loop())
        """
        logger.info("🚀 IB Gateway health monitor started (check every %ds)", check_interval_seconds)

        while True:
            try:
                is_healthy = self.check_connection()

                if not is_healthy:
                    logger.error(
                        "🔴 IB Gateway unhealthy (failure %d/%d)",
                        self.failure_count,
                        self.max_failures_before_restart
                    )

                    if self.failure_count >= self.max_failures_before_restart:
                        await self._restart_gateway()
                        self.failure_count = 0  # Reset after restart

                # Wait before next check
                await asyncio.sleep(check_interval_seconds)

            except asyncio.CancelledError:
                logger.info("Health monitor loop cancelled")
                break
            except Exception as e:
                logger.error("Unexpected error in health monitor: %s", e)
                await asyncio.sleep(check_interval_seconds)

    async def _restart_gateway(self):
        """
        Restart IB Gateway Docker container.

        Issues docker-compose restart command to restart nzt48-ib-gateway.
        Logs outcome and sends Telegram alert if configured.

        This is triggered after 3 consecutive health check failures.
        """
        if self._restart_in_progress:
            logger.warning("Restart already in progress, skipping duplicate request")
            return

        self._restart_in_progress = True

        try:
            logger.critical(
                "🚨 IB Gateway failed 3 consecutive checks. Restarting container..."
            )

            result = subprocess.run(
                ["docker-compose", "restart", "nzt48-ib-gateway"],
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                logger.info("✅ IB Gateway container restarted successfully")
                await self._send_telegram_alert(
                    "✅ IB Gateway restarted automatically after 3 failed health checks"
                )
            else:
                logger.error(
                    "❌ IB Gateway restart failed: %s",
                    result.stderr[:200]  # Log first 200 chars of error
                )
                await self._send_telegram_alert(
                    f"❌ IB Gateway restart FAILED: {result.stderr[:100]}"
                )

        except subprocess.TimeoutExpired:
            logger.error("🚨 IB Gateway restart timed out (>60s)")
            await self._send_telegram_alert(
                "🚨 IB Gateway restart timed out (>60s). Manual intervention needed."
            )
        except Exception as e:
            logger.error("🚨 Failed to restart gateway: %s", e)
            await self._send_telegram_alert(
                f"🚨 IB Gateway restart exception: {str(e)[:100]}"
            )
        finally:
            self._restart_in_progress = False

    async def wait_for_ready(self, timeout_seconds: int = 300) -> bool:
        """
        Block until gateway is healthy or timeout expires.

        Used at startup to ensure IB Gateway is ready before trading begins.
        Checks every 10 seconds.

        Args:
            timeout_seconds: Maximum time to wait (default 300s = 5 minutes)

        Returns:
            True if gateway becomes healthy, False if timeout expires
        """
        start_time = datetime.now(timezone.utc)
        deadline = start_time + timedelta(seconds=timeout_seconds)
        attempt = 0

        while datetime.now(timezone.utc) < deadline:
            attempt += 1
            if self.check_connection():
                logger.info("✅ IB Gateway ready after %d attempts", attempt)
                return True

            remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
            logger.info(
                "⏳ Waiting for IB Gateway... (%d sec remaining)",
                int(remaining)
            )
            await asyncio.sleep(10)

        logger.critical("❌ IB Gateway failed to become ready within %ds", timeout_seconds)
        await self._send_telegram_alert(
            f"🚨 IB Gateway failed to become ready within {timeout_seconds}s. Manual intervention needed."
        )
        return False

    async def check_pre_market_health(self, market: str = "LSE"):
        """
        Alert if gateway unhealthy 10min before market open.

        Integrates with MarketSessionScheduler for timezone-aware timing.
        Called periodically (e.g., every 60s) to check if we're within
        10 minutes of market open while gateway is unhealthy.

        Args:
            market: Market name ("LSE" or "US")

        Note:
            Requires market_scheduler to be configured during init.
        """
        if not self.market_scheduler:
            return

        try:
            market_open = self.market_scheduler.fetch_market_open_time(market)
            now = datetime.now(timezone.utc)
            time_until_open = (market_open - now).total_seconds()

            # Check if within 10 minutes of market open
            if 0 <= time_until_open <= 600:  # 600 seconds = 10 minutes
                if not self.is_healthy:
                    logger.critical(
                        "🚨 IB Gateway DISCONNECTED %d seconds before %s market open",
                        int(time_until_open),
                        market
                    )
                    await self._send_telegram_alert(
                        f"🚨 IB Gateway disconnected {int(time_until_open)}s before {market} open! "
                        f"Auto-restarting container..."
                    )
                    # Attempt auto-restart
                    await self._restart_gateway()

        except Exception as e:
            logger.warning(
                "Failed to check pre-market health: %s",
                e
            )

    async def _send_telegram_alert(self, message: str):
        """
        Send Telegram alert (async).

        Integrates with existing Telegram notifier if configured.
        Logs message to file if Telegram unavailable.

        Args:
            message: Alert message to send
        """
        try:
            if self.telegram_notifier:
                # Try to send via Telegram (non-blocking)
                try:
                    # Use telegram_notifier's P0 (critical) channel
                    self.telegram_notifier.p0(message)
                    logger.info("📱 Telegram alert sent: %s", message[:80])
                except Exception as e:
                    logger.warning("Failed to send Telegram alert: %s", e)
                    logger.critical("(Fallback) IB Gateway Alert: %s", message)
            else:
                logger.critical("(No Telegram) IB Gateway Alert: %s", message)

        except Exception as e:
            logger.error("Error in _send_telegram_alert: %s", e)

    def get_status_report(self) -> Dict[str, Any]:
        """
        Return current health status for logging/monitoring.

        Returns a dict with:
            - is_healthy: bool
            - last_check: ISO string or None
            - failure_count: int
            - host: str
            - port: int
            - restart_in_progress: bool

        Used by dashboards and monitoring systems.
        """
        return {
            "is_healthy": self.is_healthy,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "failure_count": self.failure_count,
            "host": self.host,
            "port": self.port,
            "restart_in_progress": self._restart_in_progress,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_health_metric(self) -> dict:
        """
        Return health metric for Prometheus/CloudWatch.

        Returns:
            dict with gauge metrics (1=healthy, 0=unhealthy)
        """
        return {
            "ib_gateway_healthy": 1 if self.is_healthy else 0,
            "ib_gateway_failures": self.failure_count,
            "ib_gateway_last_check_age_seconds": (
                (datetime.now(timezone.utc) - self.last_check).total_seconds()
                if self.last_check
                else -1
            ),
        }
