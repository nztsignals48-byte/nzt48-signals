#!/usr/bin/env python3
"""
Verify Paper Trading Readiness Checklist
=========================================

Validates:
  1. IBKR connection (localhost:4002)
  2. Market data access (5-sec bars)
  3. Order execution capability
  4. Account balance (£10k starting equity)
  5. Telegram configuration
  6. SQLite database ready
  7. Validator module loadable
  8. Configuration file valid

Run before starting paper trading:
  python3 scripts/verify_paper_trading_ready.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Setup path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("verify_paper_trading")

# ─────────────────────────────────────────────────────────────────────────────
# Test Results
# ─────────────────────────────────────────────────────────────────────────────

class VerificationResult:
    """Track test results."""
    def __init__(self):
        self.tests = []
        self.passed = 0
        self.failed = 0

    def add(self, name: str, passed: bool, message: str = ""):
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"{status} | {name}: {message}")
        self.tests.append((name, passed, message))
        if passed:
            self.passed += 1
        else:
            self.failed += 1

    def summary(self):
        logger.info(f"\n{'='*70}")
        logger.info(f"SUMMARY: {self.passed} passed, {self.failed} failed")
        logger.info(f"{'='*70}\n")
        if self.failed == 0:
            logger.info("🎯 ALL CHECKS PASSED — Paper trading is ready!")
            logger.info("Start with: python3 scripts/run_paper_trading.py\n")
        else:
            logger.error(f"⚠️  {self.failed} checks failed — address above issues before starting")
            for name, passed, msg in self.tests:
                if not passed:
                    logger.error(f"   → {name}: {msg}")
        return self.failed == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: IBKR Connection
# ─────────────────────────────────────────────────────────────────────────────

def test_ibkr_connection(result: VerificationResult) -> bool:
    """Test connection to IBKR paper account."""
    try:
        from ib_insync import IB

        ib = IB()
        ib.connect('localhost', 4002, clientId=2)

        # Check connection
        account_values = ib.accountValues()
        if not account_values:
            result.add("IBKR Connection", False, "Connected but no account values")
            return False

        ib.disconnect()
        result.add("IBKR Connection", True, "Connected to localhost:4002")
        return True

    except ImportError:
        result.add("IBKR Connection", False, "ib_insync not installed")
        return False
    except ConnectionRefusedError:
        result.add("IBKR Connection", False, "Port 4002 refused — IBKR Gateway not running")
        return False
    except Exception as e:
        result.add("IBKR Connection", False, str(e)[:80])
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Market Data
# ─────────────────────────────────────────────────────────────────────────────

def test_market_data(result: VerificationResult) -> bool:
    """Test live 5-second bar subscription."""
    try:
        from ib_insync import IB, Stock

        ib = IB()
        ib.connect('localhost', 4002, clientId=2)

        # Subscribe to QQQ3.L
        contract = Stock('QQQ3.L', 'SMART', 'GBP')
        bars = ib.reqHistoricalData(
            contract, endDateTime='', durationStr='1 D',
            barSizeSetting='5 secs', whatToShow='TRADES',
            useRTH=False, formatDate=1, keepUpToDate=True
        )

        # Wait for data
        time.sleep(2)

        if bars and len(bars) > 0:
            last_bar = bars[-1]
            msg = f"QQQ3.L: {len(bars)} bars, last close £{last_bar.close:.2f}"
            result.add("Market Data (QQQ3.L)", True, msg)
            ib.disconnect()
            return True
        else:
            result.add("Market Data (QQQ3.L)", False, "No bars received")
            return False

    except Exception as e:
        result.add("Market Data (QQQ3.L)", False, str(e)[:80])
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Order Execution
# ─────────────────────────────────────────────────────────────────────────────

def test_order_execution(result: VerificationResult) -> bool:
    """Test order placement (BUY 1 share, then cancel)."""
    try:
        from ib_insync import IB, Stock, MarketOrder

        ib = IB()
        ib.connect('localhost', 4002, clientId=2)

        # Place test order
        contract = Stock('QQQ3.L', 'SMART', 'GBP')
        order = MarketOrder('BUY', 1)

        trade = ib.placeOrder(contract, order)
        time.sleep(1)

        # Check order was placed
        if trade.order.orderId <= 0:
            result.add("Order Execution", False, "Order ID not assigned")
            return False

        # Cancel order
        ib.cancelOrder(order)
        time.sleep(1)

        msg = f"Order {trade.order.orderId} placed and cancelled"
        result.add("Order Execution", True, msg)
        ib.disconnect()
        return True

    except Exception as e:
        result.add("Order Execution", False, str(e)[:80])
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Account Balance
# ─────────────────────────────────────────────────────────────────────────────

def test_account_balance(result: VerificationResult) -> bool:
    """Test account has starting equity."""
    try:
        from ib_insync import IB

        ib = IB()
        ib.connect('localhost', 4002, clientId=2)

        account_values = ib.accountValues()
        net_liq = None
        for av in account_values:
            if av.tag == 'NetLiquidation':
                net_liq = float(av.value)
                break

        ib.disconnect()

        if net_liq is None:
            result.add("Account Balance", False, "Cannot read NetLiquidation")
            return False

        if net_liq < 5000:
            result.add("Account Balance", False, f"Balance £{net_liq:.2f} (need ≥£5000)")
            return False

        msg = f"Net Liquidation: £{net_liq:,.2f}"
        result.add("Account Balance", True, msg)
        return True

    except Exception as e:
        result.add("Account Balance", False, str(e)[:80])
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Telegram Configuration
# ─────────────────────────────────────────────────────────────────────────────

def test_telegram_config(result: VerificationResult) -> bool:
    """Test Telegram token and chat ID are configured."""
    try:
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')

        if not bot_token or not chat_id:
            result.add("Telegram Config", False, "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env")
            return False

        # Test token format
        if not bot_token.count(':') == 1 or len(bot_token.split(':')[0]) < 5:
            result.add("Telegram Config", False, "TELEGRAM_BOT_TOKEN format invalid")
            return False

        if not chat_id.lstrip('-').isdigit():
            result.add("Telegram Config", False, "TELEGRAM_CHAT_ID format invalid")
            return False

        token_preview = f"{bot_token[:10]}...:{bot_token.split(':')[1][:10]}..."
        msg = f"Bot token: {token_preview}, Chat ID: {chat_id}"
        result.add("Telegram Config", True, msg)
        return True

    except Exception as e:
        result.add("Telegram Config", False, str(e)[:80])
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Database
# ─────────────────────────────────────────────────────────────────────────────

def test_database(result: VerificationResult) -> bool:
    """Test SQLite database is ready."""
    try:
        db_path = _PROJECT_ROOT / 'data' / 'paper_trades.db'
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connect and check schema
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Check paper_trades table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_trades'"
        )
        if not cursor.fetchone():
            result.add("Database", False, "paper_trades table missing — will be created")
            conn.close()
            return False

        # Check session_metrics table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='session_metrics'"
        )
        if not cursor.fetchone():
            result.add("Database", False, "session_metrics table missing — will be created")
            conn.close()
            return False

        conn.close()
        msg = f"Database ready: {db_path}"
        result.add("Database", True, msg)
        return True

    except Exception as e:
        result.add("Database", False, str(e)[:80])
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Validator Module
# ─────────────────────────────────────────────────────────────────────────────

def test_validator_module(result: VerificationResult) -> bool:
    """Test PaperTradingValidator is loadable."""
    try:
        from uk_isa.paper_trading_validator import PaperTradingValidator, SessionMetrics, PaperTrade

        # Create instance
        validator = PaperTradingValidator()

        # Check gates initialized
        if validator.gate_win_rate != 60.0:
            result.add("Validator Module", False, "Gate thresholds not initialized")
            return False

        msg = f"Validator ready, gates: entry_quality={validator.gate_entry_quality}%, " \
              f"rung_hit={validator.gate_rung_hit}%, win_rate={validator.gate_win_rate}%"
        result.add("Validator Module", True, msg)
        return True

    except ImportError as e:
        result.add("Validator Module", False, f"Cannot import: {str(e)[:60]}")
        return False
    except Exception as e:
        result.add("Validator Module", False, str(e)[:80])
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Configuration File
# ─────────────────────────────────────────────────────────────────────────────

def test_config_file(result: VerificationResult) -> bool:
    """Test configuration file is valid."""
    try:
        import config as cfg

        # Load config
        config = cfg.load_config()

        if not config:
            result.add("Configuration File", False, "config/settings.yaml is empty or invalid")
            return False

        msg = f"config/settings.yaml loaded ({len(config)} keys)"
        result.add("Configuration File", True, msg)
        return True

    except Exception as e:
        result.add("Configuration File", False, str(e)[:80])
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: .env File
# ─────────────────────────────────────────────────────────────────────────────

def test_env_file(result: VerificationResult) -> bool:
    """Test .env file exists and is readable."""
    try:
        env_path = _PROJECT_ROOT / '.env'

        if not env_path.exists():
            result.add(".env File", False, ".env file not found at /Users/rr/nzt48-signals/.env")
            return False

        # Check readability
        with open(env_path, 'r') as f:
            lines = f.readlines()

        msg = f".env file found ({len(lines)} lines)"
        result.add(".env File", True, msg)
        return True

    except Exception as e:
        result.add(".env File", False, str(e)[:80])
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """Run all verification tests."""
    logger.info("=" * 70)
    logger.info("PAPER TRADING READINESS VERIFICATION")
    logger.info(f"Started: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70 + "\n")

    result = VerificationResult()

    # Run tests
    logger.info("Running tests...\n")
    test_env_file(result)
    test_ibkr_connection(result)
    test_market_data(result)
    test_order_execution(result)
    test_account_balance(result)
    test_telegram_config(result)
    test_database(result)
    test_validator_module(result)
    test_config_file(result)

    # Summary
    all_passed = result.summary()

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
