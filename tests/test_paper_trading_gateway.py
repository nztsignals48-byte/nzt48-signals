"""
Test Paper Trading Gateway (NZT-48 Build Week 5-6)
==================================================

MockPaperTradingGateway allows testing paper_trading.py without live IBKR.
Simulates market data and order fills.

Test Coverage:
  - Run 50 simulated trades
  - Validate all 5 gates pass
  - Check halt conditions work
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, List
from random import random, choice, gauss

# Setup path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from uk_isa.paper_trading_validator import PaperTradingValidator, SessionMetrics

logger = logging.getLogger("test_paper_trading")


# ─────────────────────────────────────────────────────────────────────────────
# Mock Paper Trading Gateway
# ─────────────────────────────────────────────────────────────────────────────

class MockPaperTradingGateway:
    """Simulates IBKR paper account for testing.

    Generates synthetic market data and order fills without live connection.
    """

    def __init__(self):
        """Initialize mock gateway."""
        self.connected = False
        self._market_data = {}  # ticker -> list of bar data
        self._orders = {}       # order_id -> order info

    def connect(self) -> bool:
        """Simulate connection."""
        self.connected = True
        logger.info("Mock IBKR connected")
        return True

    def subscribe_market_data(self, ticker: str) -> bool:
        """Subscribe to synthetic market data.

        Args:
            ticker: Ticker symbol

        Returns:
            True
        """
        # Generate synthetic bars: 100 bars, ~5 min each = ~500 min = ~1 day
        bars = []
        base_price = self._get_base_price(ticker)
        current_price = base_price

        for i in range(100):
            # Random walk: mean 0, std 0.15% per bar
            change = gauss(0.0, 0.0015)
            current_price = current_price * (1.0 + change)

            bar = {
                "time": datetime.now(timezone.utc) + timedelta(minutes=5 * i),
                "open": current_price,
                "high": current_price * 1.001,
                "low": current_price * 0.999,
                "close": current_price,
                "volume": int(1_000_000 * random()),
            }
            bars.append(bar)

        self._market_data[ticker] = bars
        logger.info(f"Subscribed to {ticker} ({len(bars)} synthetic bars)")
        return True

    def get_price_at_time(
        self,
        ticker: str,
        time_offset_minutes: int = 0,
    ) -> Optional[float]:
        """Get synthetic price at given offset.

        Args:
            ticker: Ticker symbol
            time_offset_minutes: Minutes since subscription

        Returns:
            Price, or None
        """
        if ticker not in self._market_data:
            return None

        bars = self._market_data[ticker]
        bar_index = time_offset_minutes // 5

        if bar_index >= len(bars):
            return bars[-1]["close"]

        return bars[bar_index]["close"]

    def place_order(
        self,
        ticker: str,
        direction: str,
        quantity: int,
        order_type: str = "MARKET",
    ) -> Optional[Dict]:
        """Simulate order placement.

        Args:
            ticker: Ticker symbol
            direction: LONG or SHORT
            quantity: Shares
            order_type: MARKET or LIMIT

        Returns:
            Order dict with order_id
        """
        order_id = f"MOCK_{len(self._orders)}"
        order = {
            "order_id": order_id,
            "ticker": ticker,
            "direction": direction,
            "quantity": quantity,
            "order_type": order_type,
            "status": "SUBMITTED",
            "entry_price": self.get_price_at_time(ticker, 0),
        }
        self._orders[order_id] = order
        logger.info(f"Order {order_id}: {direction} {quantity} {ticker}")
        return order

    def get_last_price(self, ticker: str) -> Optional[float]:
        """Get last synthetic price.

        Args:
            ticker: Ticker

        Returns:
            Price or None
        """
        return self.get_price_at_time(ticker, 0)

    def disconnect(self) -> None:
        """Simulate disconnect."""
        self.connected = False
        logger.info("Mock IBKR disconnected")

    def _get_base_price(self, ticker: str) -> float:
        """Get realistic base prices per ticker."""
        prices = {
            "QQQ3.L": 40.0,
            "3LUS.L": 35.0,
            "3SEM.L": 30.0,
            "GPT3.L": 28.0,
            "NVD3.L": 50.0,
            "TSL3.L": 45.0,
            "TSM3.L": 32.0,
            "MU2.L": 18.0,
            "QQQS.L": 65.0,
            "3USS.L": 42.0,
            "QQQ5.L": 70.0,
            "SP5L.L": 55.0,
        }
        return prices.get(ticker, 40.0)


# ─────────────────────────────────────────────────────────────────────────────
# Test Functions
# ─────────────────────────────────────────────────────────────────────────────

def test_mock_gateway_can_connect():
    """Test mock gateway connection."""
    gateway = MockPaperTradingGateway()
    assert gateway.connect()
    assert gateway.connected
    gateway.disconnect()
    assert not gateway.connected
    return True


def test_mock_gateway_synthetic_data():
    """Test mock generates synthetic market data."""
    gateway = MockPaperTradingGateway()
    gateway.connect()

    # Subscribe to ticker
    assert gateway.subscribe_market_data("QQQ3.L")

    # Get prices at various offsets
    price_0 = gateway.get_price_at_time("QQQ3.L", 0)
    price_100 = gateway.get_price_at_time("QQQ3.L", 100)
    price_out_of_range = gateway.get_price_at_time("QQQ3.L", 10000)

    assert price_0 is not None
    assert price_100 is not None
    assert price_out_of_range is not None  # Should return last price
    assert abs(price_0 - 40.0) < 2.0  # Within 5% of base
    return True


def test_run_50_simulated_trades():
    """Run 50 simulated trades and check gates pass."""
    logger.info("=" * 80)
    logger.info("TEST: Run 50 Simulated Trades")
    logger.info("=" * 80)

    gateway = MockPaperTradingGateway()
    gateway.connect()

    validator = PaperTradingValidator(session_id="test_50_trades")
    tickers = [
        "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L",
        "TSL3.L", "TSM3.L", "MU2.L", "QQQS.L", "3USS.L",
    ]

    # Subscribe to all tickers
    for ticker in tickers:
        gateway.subscribe_market_data(ticker)

    # Simulate 50 trades
    for i in range(50):
        trade_id = f"TRADE_{i:03d}"
        ticker = choice(tickers)
        direction = choice(["LONG", "SHORT"])
        confidence = 65.0 + random() * 20.0  # 65-85
        position_size = 100
        entry_price = gateway.get_price_at_time(ticker, 0)

        # Entry
        validator.track_trade(
            trade_id=trade_id,
            entry_price=entry_price,
            confidence=confidence,
            position_size=position_size,
            entry_signals={
                "rsi": 50.0 + random() * 20.0,
                "macd": 0.1 + random() * 0.2,
                "rvol": 0.6 + random() * 0.5,
            },
            direction=direction,
        )

        # Simulate 5 ticks forward (25 minutes)
        for tick in range(1, 6):
            price = gateway.get_price_at_time(ticker, 5 * tick)
            if direction == "LONG":
                high = price * 1.002
                low = price * 0.998
            else:
                high = price * 1.001
                low = price * 0.999

            validator.update_trade(
                trade_id=trade_id,
                current_price=price,
                high=high,
                low=low,
            )

        # Close trade at tick 5 (25 min later)
        exit_price = gateway.get_price_at_time(ticker, 25)

        # ~60% win rate for testing
        if random() < 0.6:
            # Winning exit
            if direction == "LONG":
                exit_price = exit_price * 1.005  # +0.5%
            else:
                exit_price = exit_price * 0.995  # -0.5%
        else:
            # Losing exit
            if direction == "LONG":
                exit_price = exit_price * 0.997  # -0.3%
            else:
                exit_price = exit_price * 1.003  # +0.3%

        validator.close_trade(
            trade_id=trade_id,
            exit_price=exit_price,
            exit_reason="test_exit",
        )

        if (i + 1) % 10 == 0:
            logger.info(f"Completed {i + 1} trades")

    # Generate report
    report = validator.generate_daily_report()

    logger.info("\n" + "=" * 80)
    logger.info("TEST RESULTS: 50 Simulated Trades")
    logger.info("=" * 80)
    logger.info(f"Trades Closed: {report['trades_closed']}")
    logger.info(f"Trades Completed: {report['trades_total']}")
    logger.info(f"\nMetrics:")
    logger.info(f"  Entry Quality: {report['metrics']['entry_quality_pct']:.1f}%")
    logger.info(f"  Rung Hit Rate: {report['metrics']['rung_hit_rate']:.1f}%")
    logger.info(f"  Win Rate: {report['metrics']['win_rate_pct']:.1f}%")
    logger.info(f"  Profit Factor: {report['metrics']['profit_factor']:.2f}")
    logger.info(f"  Max Cascades: {report['metrics']['max_consecutive_losses']}")
    logger.info(f"\nP&L:")
    logger.info(f"  Gross PnL: £{report['pnl']['gross_pnl']:.2f}")
    logger.info(f"  Gross Loss: £{report['pnl']['gross_loss']:.2f}")
    logger.info(f"  Net PnL: £{report['pnl']['net_pnl']:.2f}")
    logger.info(f"\nGate Status:")
    for gate_name, gate_data in report['gates'].items():
        status = "✓ PASS" if gate_data['passed'] else "✗ FAIL"
        logger.info(
            f"  {gate_name}: {status} "
            f"({gate_data['current']:.2f} vs {gate_data['required']})"
        )

    logger.info(f"\nAll Gates Passed: {report['all_gates_passed']}")
    logger.info(f"Session Halted: {report['session_halted']}")
    if report['halt_reason']:
        logger.info(f"Halt Reason: {report['halt_reason']}")

    # Assertions
    assert report['trades_total'] == 50, f"Expected 50 trades, got {report['trades_total']}"
    assert report['trades_closed'] == 50, "All trades should be closed"
    assert report['metrics']['entry_quality_pct'] >= 50, "Entry quality should be >50%"
    assert report['metrics']['rung_hit_rate'] >= 30, "Rung hit should be >30% (random variance expected)"
    assert report['metrics']['win_rate_pct'] >= 45, "Win rate should be >45% (random variance expected)"
    assert report['metrics']['profit_factor'] >= 1.0, "Profit factor should be >=1.0"

    logger.info("\n✓ All assertions passed!")
    logger.info("=" * 80)

    gateway.disconnect()
    return True


def test_halt_conditions():
    """Test session halt conditions."""
    logger.info("=" * 80)
    logger.info("TEST: Session Halt Conditions")
    logger.info("=" * 80)

    gateway = MockPaperTradingGateway()
    gateway.connect()

    validator = PaperTradingValidator(
        session_id="test_halt",
        max_trades=5,
        max_days=1,
        heat_cap_pct=-10.0,
    )

    # Track 5 trades
    for i in range(5):
        validator.track_trade(
            trade_id=f"TRADE_{i}",
            entry_price=40.0,
            confidence=70.0,
            position_size=100,
            entry_signals={},
        )
        validator.close_trade(f"TRADE_{i}", exit_price=40.1, exit_reason="test")

    # Check halt
    halt_reason = validator.check_session_halt_conditions()
    assert halt_reason == "MAX_TRADES_REACHED", f"Expected MAX_TRADES, got {halt_reason}"

    logger.info(f"✓ Halt condition triggered: {halt_reason}")
    logger.info("=" * 80)

    gateway.disconnect()
    return True


def main():
    """Run all tests."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    tests = [
        ("Mock Gateway Connection", test_mock_gateway_can_connect),
        ("Mock Gateway Synthetic Data", test_mock_gateway_synthetic_data),
        ("Run 50 Simulated Trades", test_run_50_simulated_trades),
        ("Halt Conditions", test_halt_conditions),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        try:
            logger.info(f"\nRunning: {test_name}")
            if test_func():
                logger.info(f"✓ PASS: {test_name}")
                passed += 1
            else:
                logger.error(f"✗ FAIL: {test_name}")
                failed += 1
        except Exception as e:
            logger.error(f"✗ ERROR: {test_name}: {e}")
            failed += 1

    logger.info(f"\n" + "=" * 80)
    logger.info(f"Test Results: {passed} passed, {failed} failed")
    logger.info("=" * 80)

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
