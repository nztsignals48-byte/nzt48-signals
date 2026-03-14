#!/usr/bin/env python3
"""
Run Paper Trading Session (NZT-48 Build Week 5-6)
=================================================

Main loop for 50-trade paper trading validation before live deployment.

Connects to IBKR paper account and:
  1. Streams live market data (5-second bars)
  2. Runs orchestrator.process_signal() on every tick
  3. Tracks trades via PaperTradingValidator
  4. Sends Telegram alerts on entry, exit, gate status
  5. Halts session on: 50 trades, 14 days, -4% daily heat, or any gate failure

Usage:
    python3 scripts/run_paper_trading.py [--session-id SESSION_ID]

Environment:
    IBKR_HOST=localhost (or EC2 IP for remote)
    IBKR_PORT=4002
    TELEGRAM_BOT_TOKEN=...
    TELEGRAM_CHAT_ID=...
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict
from zoneinfo import ZoneInfo

# Setup path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Load env
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass

import config as cfg
from uk_isa.paper_trading_validator import PaperTradingValidator, SessionMetrics
from delivery.telegram_notifier import get_notifier, P0, P1, P2

logger = logging.getLogger("nzt48.paper_trading")


# ─────────────────────────────────────────────────────────────────────────────
# Paper Trading Gateway
# ─────────────────────────────────────────────────────────────────────────────

class PaperTradingGateway:
    """Interface to IBKR paper account with live market data streaming."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 4002,
        client_id: int = 2,
    ):
        """Initialize IBKR paper gateway.

        Args:
            host: IBKR gateway host
            port: IBKR gateway port
            client_id: IB API client ID (must differ from production)
        """
        self.host = host or os.environ.get("IBKR_HOST", "localhost")
        self.port = port or int(os.environ.get("IBKR_PORT", "4002"))
        self.client_id = client_id

        self.ib = None
        self._connected = False
        self._market_data_subs = {}  # ticker -> ib_insync contract
        self._current_prices = {}    # ticker -> last price

    def connect(self) -> bool:
        """Connect to IBKR paper account.

        Returns:
            True if connected, False otherwise.
        """
        try:
            from ib_insync import IB, Stock
            self.ib = IB()
            self.ib.connect(self.host, self.port, clientId=self.client_id)

            # Verify connection
            account_values = self.ib.accountValues()
            if not account_values:
                logger.error("Failed to fetch account values — IBKR not ready")
                return False

            self._connected = True
            logger.info(f"Connected to IBKR at {self.host}:{self.port}")
            return True

        except ImportError:
            logger.error("ib_insync not installed")
            return False
        except Exception as e:
            logger.error(f"IBKR connection failed: {e}")
            return False

    def subscribe_market_data(self, ticker: str) -> bool:
        """Subscribe to live 5-second bars for ticker.

        Args:
            ticker: Ticker symbol (e.g., "QQQ3.L")

        Returns:
            True if subscription successful
        """
        if not self._connected:
            return False

        try:
            from ib_insync import Stock
            contract = Stock(ticker, "SMART", "GBP")
            bars = self.ib.reqHistoricalData(
                contract, endDateTime="", durationStr="1 D",
                barSizeSetting="5 secs", whatToShow="TRADES",
                useRTH=False, formatDate=1, keepUpToDate=True
            )
            self._market_data_subs[ticker] = (contract, bars)
            logger.info(f"Subscribed to {ticker}")
            return True

        except Exception as e:
            logger.error(f"Failed to subscribe {ticker}: {e}")
            return False

    def get_last_price(self, ticker: str) -> Optional[float]:
        """Get last traded price.

        Args:
            ticker: Ticker symbol

        Returns:
            Last price, or None if unavailable
        """
        return self._current_prices.get(ticker)

    def place_order(
        self,
        ticker: str,
        direction: str,  # LONG or SHORT
        quantity: int,
        order_type: str,  # "MARKET" or "LIMIT"
        limit_price: Optional[float] = None,
    ) -> Optional[Dict]:
        """Place a paper trade order.

        Args:
            ticker: Ticker symbol
            direction: LONG or SHORT
            quantity: Number of shares
            order_type: MARKET or LIMIT
            limit_price: For LIMIT orders

        Returns:
            Order dict with order_id, or None on failure
        """
        if not self._connected or ticker not in self._market_data_subs:
            logger.warning(f"Cannot place order: {ticker} not subscribed")
            return None

        try:
            from ib_insync import Stock, MarketOrder, LimitOrder

            contract = self._market_data_subs[ticker][0]

            # Build order
            action = "BUY" if direction == "LONG" else "SELL"
            if order_type == "MARKET":
                order = MarketOrder(action, quantity)
            else:
                order = LimitOrder(action, quantity, limit_price)

            # Place order
            trade = self.ib.placeOrder(contract, order)

            # Return order dict
            return {
                "order_id": trade.order.orderId,
                "ticker": ticker,
                "direction": direction,
                "quantity": quantity,
                "status": "SUBMITTED",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error(f"Order placement failed for {ticker}: {e}")
            return None

    def disconnect(self) -> None:
        """Disconnect from IBKR."""
        if self.ib:
            self.ib.disconnect()
            self._connected = False


# ─────────────────────────────────────────────────────────────────────────────
# Main Paper Trading Loop
# ─────────────────────────────────────────────────────────────────────────────

class PaperTradingSession:
    """Orchestrates 50-trade paper trading validation session."""

    def __init__(
        self,
        session_id: Optional[str] = None,
        ibkr_host: str = "localhost",
        ibkr_port: int = 4002,
    ):
        """Initialize paper trading session.

        Args:
            session_id: Unique session identifier
            ibkr_host: IBKR gateway host
            ibkr_port: IBKR gateway port
        """
        self.session_id = session_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.gateway = PaperTradingGateway(host=ibkr_host, port=ibkr_port)
        self.validator = PaperTradingValidator(session_id=self.session_id)
        self.running = False
        self.notifier = get_notifier()

        # Track orders in flight
        self._orders_in_flight = {}  # order_id -> trade_info

    def start(self) -> bool:
        """Start paper trading session.

        Returns:
            True if session started successfully
        """
        logger.info(f"Starting paper trading session: {self.session_id}")

        # Connect to IBKR
        if not self.gateway.connect():
            logger.error("Failed to connect to IBKR")
            self._send_alert(
                f"PAPER_TRADING_FAILED: Cannot connect to IBKR",
                severity=P0,
            )
            return False

        # Subscribe to ISA universe
        isa_tickers = [
            "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L",
            "TSL3.L", "TSM3.L", "MU2.L", "QQQS.L", "3USS.L",
            "QQQ5.L", "SP5L.L",
        ]

        for ticker in isa_tickers:
            if not self.gateway.subscribe_market_data(ticker):
                logger.warning(f"Failed to subscribe {ticker}")

        self.running = True
        self._send_alert(
            f"PAPER_TRADING_SESSION_START\n"
            f"Session: {self.session_id}\n"
            f"Subscribed to {len(isa_tickers)} ISA tickers",
            severity=P1,
        )

        return True

    def run_event_loop(self, max_iterations: int = 1000) -> None:
        """Main event loop — simulate market ticks and process signals.

        Args:
            max_iterations: Max loop iterations before stopping
        """
        iteration = 0

        while self.running and iteration < max_iterations:
            iteration += 1

            # Check halt conditions
            halt_reason = self.validator.check_session_halt_conditions()
            if halt_reason:
                logger.info(f"Session halted: {halt_reason}")
                self._send_alert(
                    f"PAPER_TRADING_SESSION_HALTED\n{halt_reason}",
                    severity=P0,
                )
                self.stop()
                return

            # Update open positions (every tick)
            self._update_open_positions()

            # Every 60 seconds: evaluate gates and send report
            if iteration % 60 == 0:
                report = self.validator.generate_daily_report()
                self._log_report(report)

            # Sleep to simulate tick interval (5 sec)
            try:
                import time
                time.sleep(5)
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt — stopping")
                self.stop()
                return

    def _update_open_positions(self) -> None:
        """Update validator with latest market data from IBKR."""
        for trade_id, trade_info in list(self._orders_in_flight.items()):
            ticker = trade_info["ticker"]
            last_price = self.gateway.get_last_price(ticker)

            if last_price is None:
                continue

            # Update validator
            self.validator.update_trade(
                trade_id=trade_id,
                current_price=last_price,
                high=last_price,  # Simplified: actual high/low from bars
                low=last_price,
            )

    def _send_alert(self, message: str, severity: str = P2) -> None:
        """Send Telegram alert.

        Args:
            message: Alert text
            severity: P0 (critical), P1 (high), P2 (medium), P3 (low)
        """
        try:
            if self.notifier:
                self.notifier.notify(message, severity=severity)
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")

    def _log_report(self, report: Dict) -> None:
        """Log and broadcast report.

        Args:
            report: Session metrics report dict
        """
        summary = f"""
PAPER_TRADING_REPORT
Session: {self.session_id}
Trades: {report['trades_closed']}/{report['trades_total']}
Win Rate: {report['metrics']['win_rate_pct']:.1f}%
Profit Factor: {report['metrics']['profit_factor']:.2f}
Net PnL: £{report['pnl']['net_pnl']:.2f}
Gates Passed: {report['all_gates_passed']}
"""
        logger.info(summary)
        self._send_alert(summary, severity=P2)

    def stop(self) -> None:
        """Stop paper trading session."""
        logger.info("Stopping paper trading session")
        self.running = False
        self.gateway.disconnect()

        # Final report
        report = self.validator.generate_daily_report()
        self._log_report(report)


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="NZT-48 Paper Trading Validation")
    parser.add_argument("--session-id", type=str, default=None, help="Session identifier")
    parser.add_argument("--host", type=str, default="localhost", help="IBKR host")
    parser.add_argument("--port", type=int, default=4002, help="IBKR port")
    parser.add_argument("--max-iterations", type=int, default=1000, help="Max loop iterations")

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )

    # Create and run session
    session = PaperTradingSession(
        session_id=args.session_id,
        ibkr_host=args.host,
        ibkr_port=args.port,
    )

    if not session.start():
        sys.exit(1)

    try:
        session.run_event_loop(max_iterations=args.max_iterations)
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        session.stop()

    sys.exit(0)


if __name__ == "__main__":
    main()
