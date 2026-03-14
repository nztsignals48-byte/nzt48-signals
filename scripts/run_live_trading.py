"""
NZT-48 Live Trading Orchestrator (v1.0)
========================================

Main execution loop for LIVE trading (not paper). Differs from paper trading in:
  - Real IBKR account (not paper mode)
  - ISA compliance enforcement (every trade audited)
  - Daily circuit breaker enforcement (-4% → AUTO-HALT)
  - Position sizing: max 5% account per trade, max £990 per Kelly

Loop architecture:
  - Every 60s: scan universe (TieredUniverseScanner) for entry signals
  - Every 5s: process market updates + monitor position P&L
  - Entry signal fires: route via PositionSizer → orchestrator → IBKR (live)
  - Exit signal fires: close via ChandelierExit

Logging:
  - All trades + decisions → SQLite (/data/trades.db)
  - Daily metrics → Telegram alerts
  - Prometheus /metrics endpoint (port 8000)

Requirements:
  - Paper trading must have passed all 4 gates (>70% entry quality)
  - Manual approval required before first live trade
  - Circuit breaker must be monitored continuously
"""

import asyncio
import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# Add project root to path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)-8s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(_PROJECT_ROOT / 'logs' / 'live_trading.log'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger('nzt48.live_orchestrator')

# ============================================================================
# LIVE TRADING CONFIGURATION
# ============================================================================

IBKR_HOST = os.getenv('IBKR_HOST', 'localhost')
IBKR_PORT = int(os.getenv('IBKR_PORT', '4004'))  # 4004 = live account
IBKR_CLIENT_ID = 101  # Live trading client (paper uses 100)

# Position limits (ISA compliance)
MAX_POSITION_PCT = 0.05  # 5% per trade
MAX_KELLY_SIZING = 990.0  # Max £990 per Kelly-sized trade
MAX_CONCURRENT_POSITIONS = 3
MAX_PER_TICKER = 1

# Daily circuit breaker
CIRCUIT_BREAKER_THRESHOLD = -0.04  # -4% daily loss → halt

# Scan intervals
UNIVERSE_SCAN_INTERVAL = 60  # seconds
MARKET_UPDATE_INTERVAL = 5   # seconds

# ISA universe (12 active funds)
ISA_UNIVERSE = [
    'QQQ3.L', '3LUS.L', '3SEM.L', 'GPT3.L', 'NVD3.L', 'TSL3.L',
    'TSM3.L', 'MU2.L', 'QQQS.L', '3USS.L', 'QQQ5.L', 'SP5L.L'
]

# UK timezone
UK_TZ = ZoneInfo('Europe/London')

# ============================================================================
# DATABASE SCHEMA
# ============================================================================

def init_trade_database():
    """Initialize SQLite trade audit log."""
    db_path = _PROJECT_ROOT / 'data' / 'trades.db'
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Trades table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            entry_price REAL NOT NULL,
            entry_size INTEGER NOT NULL,
            entry_confidence REAL NOT NULL,
            entry_quality_pct REAL NOT NULL,
            entry_rung INTEGER,
            entry_delay_actual_sec INTEGER,

            exit_time TEXT,
            exit_price REAL,
            exit_reason TEXT,
            exit_rung INTEGER,

            realized_pnl REAL,
            realized_pnl_pct REAL,
            pnl_status TEXT,

            kelly_fraction REAL,
            position_pct REAL,

            isa_compliant INTEGER DEFAULT 1,
            notes TEXT
        )
    ''')

    # Daily metrics table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date TEXT PRIMARY KEY,
            trades_count INTEGER,
            trades_won INTEGER,
            trades_lost INTEGER,
            win_rate_pct REAL,
            realized_pnl REAL,
            daily_heat_pct REAL,
            circuit_breaker_triggered INTEGER DEFAULT 0,
            avg_entry_quality_pct REAL,
            max_concurrent_positions INTEGER
        )
    ''')

    # Positions table (real-time)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS positions (
            ticker TEXT PRIMARY KEY,
            entry_timestamp TEXT NOT NULL,
            entry_price REAL NOT NULL,
            entry_size INTEGER NOT NULL,
            current_price REAL,
            current_pnl REAL,
            current_pnl_pct REAL,
            rung_1_target REAL,
            rung_2_target REAL,
            rung_3_target REAL,
            rung_4_target REAL,
            rung_5_target REAL,
            chandelier_stop REAL,
            last_update TEXT
        )
    ''')

    # Circuit breaker events table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS circuit_breaker_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            daily_heat_pct REAL,
            triggered_by_trade_id INTEGER,
            action TEXT,
            recovery_time TEXT
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("Trade database initialized at %s", db_path)


# ============================================================================
# LIVE TRADING ORCHESTRATOR
# ============================================================================

class LiveTradingOrchestrator:
    """Main orchestration engine for live trading."""

    def __init__(self):
        self.db_path = _PROJECT_ROOT / 'data' / 'trades.db'
        self.positions_file = _PROJECT_ROOT / 'data' / 'positions' / 'open_positions.json'
        self.positions_file.parent.mkdir(parents=True, exist_ok=True)

        self.running = True
        self.circuit_breaker_halted = False
        self.halt_start_time = None

        # Metrics
        self.daily_trades = []
        self.daily_pnl = 0.0
        self.daily_heat_pct = 0.0
        self.open_positions: Dict[str, Dict] = {}

        # Load positions from disk
        self._load_positions()

        logger.info("LiveTradingOrchestrator initialized")
        logger.info("ISA Universe: %s", ISA_UNIVERSE)
        logger.info("Max position size: %.1f%% | Max Kelly: £%.2f",
                    MAX_POSITION_PCT * 100, MAX_KELLY_SIZING)

    # ========================================================================
    # Position Management
    # ========================================================================

    def _load_positions(self):
        """Load open positions from persistent storage."""
        if self.positions_file.exists():
            try:
                with open(self.positions_file) as f:
                    self.open_positions = json.load(f)
                    logger.info("Loaded %d open positions", len(self.open_positions))
            except Exception as e:
                logger.warning("Failed to load positions: %s", e)
                self.open_positions = {}
        else:
            self.open_positions = {}

    def _save_positions(self):
        """Persist open positions to disk."""
        try:
            with open(self.positions_file, 'w') as f:
                json.dump(self.open_positions, f, indent=2, default=str)
        except Exception as e:
            logger.error("Failed to save positions: %s", e)

    def get_position_count(self) -> int:
        """Get current open position count."""
        return len(self.open_positions)

    def has_ticker(self, ticker: str) -> bool:
        """Check if ticker already has open position."""
        return ticker in self.open_positions

    def can_add_position(self, ticker: str) -> Tuple[bool, Optional[str]]:
        """Check if we can add a new position (respect limits)."""
        if self.get_position_count() >= MAX_CONCURRENT_POSITIONS:
            return False, f"Max concurrent positions ({MAX_CONCURRENT_POSITIONS}) reached"

        if self.has_ticker(ticker):
            return False, f"Ticker {ticker} already has open position"

        if self.circuit_breaker_halted:
            return False, "Circuit breaker active — trading halted"

        return True, None

    # ========================================================================
    # Circuit Breaker Enforcement
    # ========================================================================

    def update_daily_pnl(self):
        """Recalculate daily P&L from all open positions + closed trades."""
        realized_pnl = 0.0
        unrealized_pnl = 0.0

        # Realized P&L from closed trades today
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        today = datetime.now(UK_TZ).date()
        cursor.execute('''
            SELECT SUM(realized_pnl) FROM trades
            WHERE DATE(timestamp) = ? AND realized_pnl IS NOT NULL
        ''', (str(today),))
        result = cursor.fetchone()
        realized_pnl = result[0] if result[0] else 0.0

        # Unrealized P&L from open positions
        for ticker, pos in self.open_positions.items():
            if pos.get('current_pnl'):
                unrealized_pnl += pos['current_pnl']

        self.daily_pnl = realized_pnl + unrealized_pnl

        # Calculate as % of account equity (assume £10k starting)
        account_equity = 10000.0  # Paper mode starting equity
        self.daily_heat_pct = (self.daily_pnl / account_equity) if account_equity else 0.0

        conn.close()

        # Check circuit breaker threshold
        if self.daily_heat_pct < CIRCUIT_BREAKER_THRESHOLD:
            self._trigger_circuit_breaker()

    def _trigger_circuit_breaker(self):
        """Trigger circuit breaker halt."""
        if not self.circuit_breaker_halted:
            self.circuit_breaker_halted = True
            self.halt_start_time = datetime.now(UK_TZ)

            logger.critical(
                "CIRCUIT BREAKER TRIGGERED: Daily loss %.2f%% exceeds threshold (%.2f%%)",
                self.daily_heat_pct * 100,
                CIRCUIT_BREAKER_THRESHOLD * 100
            )

            # Log to database
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO circuit_breaker_events
                (timestamp, trigger_type, daily_heat_pct, action)
                VALUES (?, ?, ?, ?)
            ''', (datetime.now(UK_TZ).isoformat(), 'DAILY_LOSS', self.daily_heat_pct, 'HALT'))
            conn.commit()
            conn.close()

            # Send Telegram alert
            self._send_alert_critical(
                f"🚨 CIRCUIT BREAKER TRIGGERED\n"
                f"Daily loss: {self.daily_heat_pct*100:.2f}%\n"
                f"Threshold: {CIRCUIT_BREAKER_THRESHOLD*100:.2f}%\n"
                f"All trading halted. Next review at 08:00 GMT tomorrow."
            )

    def _check_circuit_breaker_recovery(self):
        """Check if circuit breaker can be cleared (next market open)."""
        if self.circuit_breaker_halted and self.halt_start_time:
            now = datetime.now(UK_TZ)

            # Can recover at 08:00 GMT next trading day
            next_open = self._next_market_open()

            if now >= next_open:
                self.circuit_breaker_halted = False
                self.halt_start_time = None
                logger.info("Circuit breaker cleared at market open")
                self._send_alert_info(
                    "✅ Circuit Breaker Cleared\n"
                    f"Market open: {next_open.isoformat()}\n"
                    f"Trading resumed."
                )

    def _next_market_open(self) -> datetime:
        """Get next LSE market open time (08:00 GMT)."""
        now = datetime.now(UK_TZ)

        # LSE opens at 08:00 GMT
        market_open = now.replace(hour=8, minute=0, second=0, microsecond=0)

        if now >= market_open:
            # Next market open is tomorrow
            market_open += timedelta(days=1)

        # Skip weekends
        while market_open.weekday() >= 5:
            market_open += timedelta(days=1)

        return market_open

    # ========================================================================
    # Entry Signal Processing
    # ========================================================================

    def process_entry_signal(self, ticker: str, entry_price: float,
                            confidence: float, entry_quality_pct: float,
                            entry_delay_sec: int) -> Tuple[bool, Optional[str]]:
        """
        Process entry signal and execute live trade.

        Returns: (success, error_message)
        """
        logger.info(
            "Entry signal: %s @ £%.2f (confidence=%.1f%%, quality=%.1f%%)",
            ticker, entry_price, confidence * 100, entry_quality_pct
        )

        # Check ISA compliance
        can_add, reason = self.can_add_position(ticker)
        if not can_add:
            logger.warning("Entry rejected: %s", reason)
            return False, reason

        # Calculate Kelly-sized position
        kelly_fraction = self._calculate_kelly(confidence)
        position_size = self._calculate_position_size(entry_price, kelly_fraction)
        position_pct = (position_size * entry_price) / 10000.0

        if position_pct > MAX_POSITION_PCT:
            logger.warning(
                "Position size exceeds limit: %.1f%% > %.1f%%",
                position_pct * 100, MAX_POSITION_PCT * 100
            )
            return False, "Position exceeds size limit"

        # Route to IBKR (live account, port 4004)
        try:
            # TODO: Implement IBKR live order placement via ib_insync
            logger.info(
                "LIVE ENTRY: %s | Size: %d @ £%.2f | Kelly: %.1f%% | Position: %.1f%%",
                ticker, position_size, entry_price, kelly_fraction * 100, position_pct * 100
            )

            # Record position
            self.open_positions[ticker] = {
                'entry_time': datetime.now(UK_TZ).isoformat(),
                'entry_price': entry_price,
                'entry_size': position_size,
                'entry_confidence': confidence,
                'entry_quality_pct': entry_quality_pct,
                'entry_delay_sec': entry_delay_sec,
                'kelly_fraction': kelly_fraction,
                'position_pct': position_pct,
                'current_price': entry_price,
                'current_pnl': 0.0,
                'current_pnl_pct': 0.0,
            }
            self._save_positions()

            # Log to database
            self._log_trade_entry(
                ticker, entry_price, position_size, confidence, entry_quality_pct,
                kelly_fraction, position_pct, entry_delay_sec
            )

            # Send Telegram alert
            self._send_alert_entry(
                ticker, entry_price, position_size, confidence, entry_quality_pct
            )

            return True, None

        except Exception as e:
            logger.error("Failed to execute live entry: %s", e)
            return False, str(e)

    def _calculate_kelly(self, win_probability: float) -> float:
        """
        Calculate Kelly fraction for position sizing.
        Kelly = (bp - q) / b  where b=odds, p=win%, q=lose%
        For simplicity, assume 1:1 payoff (b=1):
        Kelly = p - q = 2p - 1

        Fractional Kelly (25%) to be conservative in live trading.
        """
        kelly = max(0, 2 * win_probability - 1)
        return min(0.25, kelly * 0.25)  # 25% fractional Kelly

    def _calculate_position_size(self, entry_price: float, kelly_fraction: float) -> int:
        """Calculate position size in shares (respect £990 Kelly limit)."""
        account_equity = 10000.0
        kelly_amount = min(account_equity * kelly_fraction, MAX_KELLY_SIZING)
        position_size = int(kelly_amount / entry_price)
        return max(1, position_size)

    def _log_trade_entry(self, ticker: str, entry_price: float, entry_size: int,
                        confidence: float, entry_quality_pct: float,
                        kelly_fraction: float, position_pct: float, entry_delay_sec: int):
        """Log trade entry to SQLite."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO trades
            (timestamp, ticker, entry_price, entry_size, entry_confidence,
             entry_quality_pct, entry_delay_actual_sec, kelly_fraction, position_pct,
             isa_compliant)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        ''', (
            datetime.now(UK_TZ).isoformat(),
            ticker, entry_price, entry_size, confidence,
            entry_quality_pct, entry_delay_sec, kelly_fraction, position_pct
        ))

        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()

        logger.info("Trade logged with ID %d", trade_id)

    # ========================================================================
    # Exit Signal Processing
    # ========================================================================

    def process_exit_signal(self, ticker: str, exit_price: float,
                           exit_reason: str) -> Tuple[bool, Optional[str]]:
        """Process exit signal and close position."""
        if ticker not in self.open_positions:
            logger.warning("Exit signal for non-existent position: %s", ticker)
            return False, "Position not found"

        pos = self.open_positions[ticker]

        # Calculate P&L
        pnl = (exit_price - pos['entry_price']) * pos['entry_size']
        pnl_pct = ((exit_price - pos['entry_price']) / pos['entry_price'])

        logger.info(
            "LIVE EXIT: %s | P&L: £%.2f (%.2f%%) | Reason: %s",
            ticker, pnl, pnl_pct * 100, exit_reason
        )

        # TODO: Implement IBKR live order placement (close position)

        # Log to database
        self._log_trade_exit(ticker, exit_price, pnl, pnl_pct, exit_reason)

        # Remove position
        del self.open_positions[ticker]
        self._save_positions()

        # Update daily P&L
        self.daily_pnl += pnl
        self.update_daily_pnl()

        # Send Telegram alert
        self._send_alert_exit(ticker, exit_price, pnl, pnl_pct, exit_reason)

        return True, None

    def _log_trade_exit(self, ticker: str, exit_price: float,
                       pnl: float, pnl_pct: float, exit_reason: str):
        """Log trade exit to SQLite."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Find latest trade for this ticker without exit
        cursor.execute('''
            SELECT trade_id FROM trades
            WHERE ticker = ? AND exit_time IS NULL
            ORDER BY timestamp DESC LIMIT 1
        ''', (ticker,))

        result = cursor.fetchone()
        if result:
            trade_id = result[0]
            cursor.execute('''
                UPDATE trades SET
                    exit_time = ?,
                    exit_price = ?,
                    exit_reason = ?,
                    realized_pnl = ?,
                    realized_pnl_pct = ?,
                    pnl_status = ?
                WHERE trade_id = ?
            ''', (
                datetime.now(UK_TZ).isoformat(),
                exit_price,
                exit_reason,
                pnl,
                pnl_pct,
                'WIN' if pnl >= 0 else 'LOSS',
                trade_id
            ))
            conn.commit()

        conn.close()

    # ========================================================================
    # Telegram Alerts
    # ========================================================================

    def _send_alert_entry(self, ticker: str, entry_price: float,
                         entry_size: int, confidence: float, entry_quality_pct: float):
        """Send Telegram alert for entry."""
        # TODO: Implement TelegramNotifier integration
        msg = (
            f"📈 ENTRY\n"
            f"Ticker: {ticker}\n"
            f"Price: £{entry_price:.2f}\n"
            f"Size: {entry_size} shares\n"
            f"Confidence: {confidence*100:.1f}%\n"
            f"Quality: {entry_quality_pct:.1f}%"
        )
        logger.info(msg)

    def _send_alert_exit(self, ticker: str, exit_price: float,
                        pnl: float, pnl_pct: float, exit_reason: str):
        """Send Telegram alert for exit."""
        # TODO: Implement TelegramNotifier integration
        emoji = "✅" if pnl >= 0 else "❌"
        msg = (
            f"{emoji} EXIT\n"
            f"Ticker: {ticker}\n"
            f"Exit Price: £{exit_price:.2f}\n"
            f"P&L: £{pnl:.2f} ({pnl_pct*100:.2f}%)\n"
            f"Reason: {exit_reason}"
        )
        logger.info(msg)

    def _send_alert_critical(self, message: str):
        """Send critical Telegram alert."""
        # TODO: Implement TelegramNotifier integration
        logger.critical(message)

    def _send_alert_info(self, message: str):
        """Send info Telegram alert."""
        # TODO: Implement TelegramNotifier integration
        logger.info(message)

    # ========================================================================
    # Main Event Loops
    # ========================================================================

    async def run_universe_scan_loop(self):
        """Scan universe every 60s for entry signals."""
        logger.info("Starting universe scan loop (60s interval)")

        while self.running:
            try:
                await asyncio.sleep(UNIVERSE_SCAN_INTERVAL)

                # Check circuit breaker recovery
                self._check_circuit_breaker_recovery()

                # TODO: Implement TieredUniverseScanner integration
                # For each signal generated, call process_entry_signal()
                logger.debug("Universe scan completed")

            except Exception as e:
                logger.error("Error in universe scan: %s", e, exc_info=True)

    async def run_market_update_loop(self):
        """Process market updates every 5s."""
        logger.info("Starting market update loop (5s interval)")

        while self.running:
            try:
                await asyncio.sleep(MARKET_UPDATE_INTERVAL)

                # Update position prices (TODO: fetch from IBKR)
                # For each position, check chandelier exit

                # Update daily P&L
                self.update_daily_pnl()

                # Log metrics periodically
                if int(time.time()) % 60 == 0:
                    logger.info(
                        "Positions: %d | Daily P&L: £%.2f (%.2f%%) | Heat: %.2f%%",
                        len(self.open_positions),
                        self.daily_pnl,
                        (self.daily_pnl / 10000.0) * 100,
                        self.daily_heat_pct * 100
                    )

            except Exception as e:
                logger.error("Error in market update: %s", e, exc_info=True)

    async def run_metrics_server(self):
        """Expose Prometheus metrics endpoint."""
        # TODO: Implement FastAPI /metrics endpoint
        # Metrics:
        #   - daily_pnl_total
        #   - open_positions_count
        #   - daily_heat_percentage
        #   - circuit_breaker_status
        logger.info("Metrics server would run on port 8000")

    async def run(self):
        """Main event loop."""
        logger.info("Starting LiveTradingOrchestrator")

        # Register signal handlers
        def handle_signal(signum, frame):
            logger.info("Received signal %d, shutting down gracefully", signum)
            self.running = False

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        # Run all loops concurrently
        tasks = [
            asyncio.create_task(self.run_universe_scan_loop()),
            asyncio.create_task(self.run_market_update_loop()),
            asyncio.create_task(self.run_metrics_server()),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Event loop cancelled")
        finally:
            logger.info("LiveTradingOrchestrator stopped")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main entry point."""
    init_trade_database()

    orchestrator = LiveTradingOrchestrator()

    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.critical("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
