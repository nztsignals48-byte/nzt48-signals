"""
NZT-48 Live Trading Monitor (v1.0)
==================================

Real-time monitoring daemon that:
  1. Reads from SQLite trade database
  2. Exposes metrics via Prometheus format (/metrics endpoint)
  3. Tracks KPIs:
     - Daily P&L (realized + unrealized)
     - Win rate (rolling 50 trades)
     - Entry quality (% in first rung)
     - Heat level (current daily drawdown %)
     - Position count + leverage

  4. Alerts if:
     - Heat > RED (-4%)
     - Win rate drops <50%
     - Entry quality drops <60%

  5. Feeds into Grafana dashboard (prometheus datasource)

Runs as independent process alongside main trading engine.
Connects to SQLite database + Redis state.
"""

import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List
from zoneinfo import ZoneInfo
from collections import deque

try:
    from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry
    from prometheus_client.exposition import generate_latest
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logging.warning("prometheus_client not available — metrics disabled")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)-8s] %(name)s: %(message)s',
)
logger = logging.getLogger('nzt48.live_monitor')

# UK timezone
UK_TZ = ZoneInfo('Europe/London')

# ============================================================================
# PROMETHEUS METRICS
# ============================================================================

if PROMETHEUS_AVAILABLE:
    registry = CollectorRegistry()

    # Daily P&L
    daily_pnl_realized = Gauge(
        'nzt48_daily_pnl_realized_pounds',
        'Daily realized P&L (closed trades)',
        registry=registry
    )
    daily_pnl_unrealized = Gauge(
        'nzt48_daily_pnl_unrealized_pounds',
        'Daily unrealized P&L (open positions)',
        registry=registry
    )
    daily_pnl_total = Gauge(
        'nzt48_daily_pnl_total_pounds',
        'Total daily P&L (realized + unrealized)',
        registry=registry
    )
    daily_heat_pct = Gauge(
        'nzt48_daily_heat_percentage',
        'Daily loss as % of account equity',
        registry=registry
    )

    # Win rate
    win_rate_pct = Gauge(
        'nzt48_win_rate_percentage',
        'Win rate (% of profitable trades)',
        registry=registry
    )
    trades_total = Counter(
        'nzt48_trades_total',
        'Total trades (all time)',
        registry=registry
    )
    trades_won = Counter(
        'nzt48_trades_won',
        'Total winning trades',
        registry=registry
    )
    trades_lost = Counter(
        'nzt48_trades_lost',
        'Total losing trades',
        registry=registry
    )

    # Entry quality
    entry_quality_pct = Gauge(
        'nzt48_entry_quality_percentage',
        'Entry quality (% entries in first rung)',
        registry=registry
    )
    entry_delay_seconds = Histogram(
        'nzt48_entry_delay_seconds',
        'Entry delay distribution (seconds)',
        buckets=(1, 5, 10, 30, 60, 120, 300),
        registry=registry
    )

    # Positions
    open_positions_count = Gauge(
        'nzt48_open_positions_count',
        'Current open position count',
        registry=registry
    )
    max_concurrent_positions = Gauge(
        'nzt48_max_concurrent_positions',
        'Peak concurrent positions (session)',
        registry=registry
    )
    avg_leverage = Gauge(
        'nzt48_average_leverage',
        'Average position leverage',
        registry=registry
    )

    # Circuit breaker
    circuit_breaker_triggered = Gauge(
        'nzt48_circuit_breaker_triggered',
        '1 if circuit breaker active, 0 otherwise',
        registry=registry
    )

else:
    registry = None


# ============================================================================
# LIVE TRADING MONITOR
# ============================================================================

class LiveTradingMonitor:
    """Monitor live trading activity and expose metrics."""

    def __init__(self, db_path: Path, positions_path: Path):
        self.db_path = db_path
        self.positions_path = positions_path

        # Metrics storage
        self.daily_pnl = 0.0
        self.daily_realized_pnl = 0.0
        self.daily_unrealized_pnl = 0.0
        self.daily_heat_pct = 0.0

        self.win_rate = 0.0
        self.trades_today = 0
        self.wins_today = 0
        self.losses_today = 0

        self.entry_quality = 0.0
        self.open_positions = 0
        self.max_positions = 0
        self.avg_leverage = 0.0

        self.circuit_breaker_active = False

        # Rolling trade history (for 50-trade win rate)
        self.trade_history: deque = deque(maxlen=50)

        logger.info("LiveTradingMonitor initialized")
        logger.info("Database: %s", db_path)
        logger.info("Positions: %s", positions_path)

    # ========================================================================
    # Database Reading
    # ========================================================================

    def read_daily_pnl(self):
        """Calculate today's P&L from database."""
        if not self.db_path.exists():
            return

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            today = datetime.now(UK_TZ).date()

            # Realized P&L from closed trades
            cursor.execute('''
                SELECT SUM(realized_pnl) FROM trades
                WHERE DATE(timestamp) = ? AND realized_pnl IS NOT NULL
            ''', (str(today),))
            result = cursor.fetchone()
            self.daily_realized_pnl = result[0] if result[0] else 0.0

            # Total P&L from all trades today (including open entries)
            cursor.execute('''
                SELECT COUNT(*), SUM(CASE WHEN realized_pnl >= 0 THEN 1 ELSE 0 END)
                FROM trades
                WHERE DATE(timestamp) = ?
            ''', (str(today),))
            result = cursor.fetchone()
            self.trades_today = result[0] if result[0] else 0
            self.wins_today = result[1] if result[1] else 0
            self.losses_today = self.trades_today - self.wins_today

            # Win rate
            if self.trades_today > 0:
                self.win_rate = (self.wins_today / self.trades_today) * 100.0
            else:
                self.win_rate = 0.0

            # Entry quality (% in first rung)
            cursor.execute('''
                SELECT AVG(entry_quality_pct) FROM trades
                WHERE DATE(timestamp) = ?
            ''', (str(today),))
            result = cursor.fetchone()
            self.entry_quality = result[0] if result[0] else 0.0

            conn.close()

        except Exception as e:
            logger.error("Error reading daily P&L: %s", e)

    def read_unrealized_pnl(self):
        """Calculate unrealized P&L from open positions."""
        if not self.positions_path.exists():
            self.daily_unrealized_pnl = 0.0
            self.open_positions = 0
            return

        try:
            with open(self.positions_path) as f:
                positions = json.load(f)

            self.open_positions = len(positions)
            self.daily_unrealized_pnl = 0.0

            for ticker, pos in positions.items():
                if pos.get('current_pnl'):
                    self.daily_unrealized_pnl += pos['current_pnl']

        except Exception as e:
            logger.error("Error reading positions: %s", e)

    def read_circuit_breaker_status(self):
        """Read circuit breaker status from database."""
        if not self.db_path.exists():
            return

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Check if circuit breaker was triggered today
            today = datetime.now(UK_TZ).date()
            cursor.execute('''
                SELECT COUNT(*) FROM circuit_breaker_events
                WHERE DATE(timestamp) = ? AND trigger_type = 'DAILY_LOSS'
            ''', (str(today),))

            result = cursor.fetchone()
            self.circuit_breaker_active = (result[0] > 0) if result else False

            conn.close()

        except Exception as e:
            logger.error("Error reading circuit breaker status: %s", e)

    # ========================================================================
    # Metrics Update
    # ========================================================================

    def update_all_metrics(self):
        """Read all metrics from databases."""
        self.read_daily_pnl()
        self.read_unrealized_pnl()
        self.read_circuit_breaker_status()

        # Calculate total P&L
        self.daily_pnl = self.daily_realized_pnl + self.daily_unrealized_pnl

        # Calculate heat % (as % of £10k account)
        self.daily_heat_pct = (self.daily_pnl / 10000.0) * 100.0

        # Update Prometheus metrics
        if PROMETHEUS_AVAILABLE:
            self._update_prometheus()

        logger.info(
            "Metrics: PnL=£%.2f (Real=£%.2f, Unreal=£%.2f) | "
            "Heat=%.2f%% | Trades=%d (W=%d, L=%d, WR=%.1f%%) | "
            "Positions=%d | Quality=%.1f%% | CB=%s",
            self.daily_pnl,
            self.daily_realized_pnl,
            self.daily_unrealized_pnl,
            self.daily_heat_pct,
            self.trades_today,
            self.wins_today,
            self.losses_today,
            self.win_rate,
            self.open_positions,
            self.entry_quality,
            "ACTIVE" if self.circuit_breaker_active else "OFF"
        )

    def _update_prometheus(self):
        """Update Prometheus metrics."""
        if not PROMETHEUS_AVAILABLE:
            return

        daily_pnl_realized.set(self.daily_realized_pnl)
        daily_pnl_unrealized.set(self.daily_unrealized_pnl)
        daily_pnl_total.set(self.daily_pnl)
        daily_heat_pct.set(self.daily_heat_pct)

        win_rate_pct.set(self.win_rate)
        open_positions_count.set(self.open_positions)
        entry_quality_pct.set(self.entry_quality)

        circuit_breaker_triggered.set(1.0 if self.circuit_breaker_active else 0.0)

    # ========================================================================
    # Alert Logic
    # ========================================================================

    def check_alerts(self):
        """Check for alert conditions."""
        alerts = []

        # Heat alert
        if self.daily_heat_pct < -4.0:
            alerts.append(f"🚨 HEAT CRITICAL: {self.daily_heat_pct:.2f}% (< -4%)")
        elif self.daily_heat_pct < -2.5:
            alerts.append(f"⚠️  HEAT HIGH: {self.daily_heat_pct:.2f}% (< -2.5%)")

        # Win rate alert
        if self.trades_today >= 10 and self.win_rate < 50.0:
            alerts.append(f"⚠️  WIN RATE LOW: {self.win_rate:.1f}% (< 50%)")

        # Entry quality alert
        if self.trades_today >= 5 and self.entry_quality < 60.0:
            alerts.append(f"⚠️  QUALITY LOW: {self.entry_quality:.1f}% (< 60%)")

        return alerts

    # ========================================================================
    # Metrics Export
    # ========================================================================

    def get_metrics_prometheus(self) -> str:
        """Return metrics in Prometheus format."""
        if not PROMETHEUS_AVAILABLE:
            return "# prometheus_client not available\n"

        return generate_latest(registry).decode('utf-8')

    def get_metrics_json(self) -> Dict[str, Any]:
        """Return metrics as JSON."""
        return {
            'timestamp': datetime.now(UK_TZ).isoformat(),
            'daily_pnl': {
                'total': self.daily_pnl,
                'realized': self.daily_realized_pnl,
                'unrealized': self.daily_unrealized_pnl,
                'heat_pct': self.daily_heat_pct,
            },
            'trading': {
                'trades_today': self.trades_today,
                'wins': self.wins_today,
                'losses': self.losses_today,
                'win_rate_pct': self.win_rate,
                'entry_quality_pct': self.entry_quality,
            },
            'positions': {
                'open_count': self.open_positions,
                'max_concurrent': self.max_positions,
                'avg_leverage': self.avg_leverage,
            },
            'circuit_breaker': {
                'active': self.circuit_breaker_active,
            },
            'alerts': self.check_alerts(),
        }

    # ========================================================================
    # Main Loop
    # ========================================================================

    def run(self, update_interval: int = 5):
        """Main monitoring loop (updates every N seconds)."""
        logger.info("Starting monitor loop (update interval: %ds)", update_interval)

        while True:
            try:
                # Update all metrics
                self.update_all_metrics()

                # Check for alerts
                alerts = self.check_alerts()
                if alerts:
                    for alert in alerts:
                        logger.warning(alert)

                # Sleep before next update
                time.sleep(update_interval)

            except KeyboardInterrupt:
                logger.info("Monitor stopped by user")
                break
            except Exception as e:
                logger.error("Error in monitor loop: %s", e, exc_info=True)
                time.sleep(update_interval)


# ============================================================================
# FASTAPI SERVER (if running as standalone)
# ============================================================================

def create_fastapi_server(monitor: LiveTradingMonitor):
    """Create FastAPI server for metrics endpoint."""
    try:
        from fastapi import FastAPI
        from fastapi.responses import Response
        import uvicorn

        app = FastAPI(title="NZT-48 Live Monitor")

        @app.get("/metrics")
        async def metrics():
            """Prometheus metrics endpoint."""
            text = monitor.get_metrics_prometheus()
            return Response(content=text, media_type="text/plain")

        @app.get("/metrics/json")
        async def metrics_json():
            """JSON metrics endpoint."""
            return monitor.get_metrics_json()

        @app.get("/health")
        async def health():
            """Health check endpoint."""
            return {"status": "healthy"}

        return app, uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")

    except ImportError:
        logger.error("FastAPI not available — install with: pip install fastapi uvicorn")
        return None, None


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main entry point."""
    db_path = Path(__file__).parent.parent / 'data' / 'trades.db'
    positions_path = Path(__file__).parent.parent / 'data' / 'positions' / 'open_positions.json'

    monitor = LiveTradingMonitor(db_path, positions_path)

    # Try to run FastAPI server
    app, config = create_fastapi_server(monitor)

    if app:
        # Run server in background thread, monitor in main thread
        import threading

        def run_server():
            logger.info("Starting FastAPI metrics server on 0.0.0.0:8000")
            uvicorn_instance = asyncio.run(config.serve())

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Run monitor in main thread
        monitor.run(update_interval=5)
    else:
        # Fallback: just run monitor without API
        logger.warning("Running monitor without FastAPI server")
        monitor.run(update_interval=5)


if __name__ == '__main__':
    main()
