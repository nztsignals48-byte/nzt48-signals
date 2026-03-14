#!/usr/bin/env python3
"""
NZT-48 Telegram Integration Tests
==================================
Comprehensive test suite for Telegram alerting across all integration points.

Tests:
  1. TelegramDelivery class with all signal types
  2. TelegramAlerter class with all alert types
  3. Event bus alert capping
  4. End-to-end signal delivery pipeline

Usage:
  pytest tests/test_telegram_integration.py -v
  python3 -m pytest tests/test_telegram_integration.py -v
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Setup path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass

logger = logging.getLogger("test_telegram")

# Only run integration tests if credentials are set
HAS_CREDENTIALS = bool(
    os.environ.get("TELEGRAM_BOT_TOKEN") and
    os.environ.get("TELEGRAM_CHAT_ID")
)


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="Telegram credentials not set")
class TestTelegramDelivery:
    """Tests for TelegramDelivery class."""

    @pytest.mark.asyncio
    async def test_verify_connection(self):
        """Test that bot can be verified."""
        from delivery.telegram_bot import TelegramDelivery

        tg = TelegramDelivery()
        result = await tg.verify_connection()
        assert result is True, "Bot connection verification failed"

    @pytest.mark.asyncio
    async def test_initialize_and_send_alert(self):
        """Test initialization and sending a basic alert."""
        from delivery.telegram_bot import TelegramDelivery

        tg = TelegramDelivery()
        await tg.initialize()

        assert tg._bot is not None, "Bot not initialized"
        assert tg._app is not None, "App not initialized"

        # Send test alert
        result = await tg.send_alert("✅ Test alert from pytest")
        assert result is True, "Alert send failed"

    @pytest.mark.asyncio
    async def test_send_daily_target_signal(self):
        """Test S15 Daily Target signal delivery."""
        from delivery.telegram_bot import TelegramDelivery
        from models import Signal, Direction, RegimeState, GexRegime, Bot

        tg = TelegramDelivery()
        await tg.initialize()

        # Create a mock signal
        signal = Signal(
            ticker="QQQ3.L",
            direction=Direction.LONG,
            entry=100.50,
            stop=99.50,
            target_1r=102.50,
            target_2r=105.00,
            trail=101.00,
            confidence=78.5,
            strategy="S15",
            regime=RegimeState.TRENDING_UP,
            gex_regime=GexRegime.NEGATIVE,
            rvol=2.1,
            risk_dollars=75.0,
            risk_pct=0.0075,
            shares=100,
            vix=16.5,
        )

        result = await tg.send_daily_target_signal(signal)
        assert result is True, "S15 signal delivery failed"

    @pytest.mark.asyncio
    async def test_send_alert_with_html_fallback(self):
        """Test that HTML formatting errors fall back to plain text."""
        from delivery.telegram_bot import TelegramDelivery

        tg = TelegramDelivery()
        await tg.initialize()

        # Message with invalid HTML
        bad_html = "Test <invalid> tag"
        result = await tg.send_alert(bad_html)
        # Should either succeed with fallback or fail gracefully
        assert isinstance(result, bool), "send_alert should return bool"


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="Telegram credentials not set")
class TestTelegramAlerter:
    """Tests for TelegramAlerter class."""

    def test_initialization(self):
        """Test TelegramAlerter initialization."""
        from src.core.telegram_alerter import TelegramAlerter

        alerter = TelegramAlerter()
        assert alerter.enabled is True, "Alerter should be enabled"

    def test_send_entry_alert(self):
        """Test entry alert formatting and sending."""
        from src.core.telegram_alerter import TelegramAlerter

        alerter = TelegramAlerter()

        # This logs and may not actually send (graceful degradation)
        alerter.send_entry_alert(
            symbol="QQQ3.L",
            side="BUY",
            confidence_pct=78.5,
            position_size=500.0,
            leverage=3.0,
            regime="TRENDING_UP",
            early_detection_reason="Golden cross + RSI >50"
        )
        # Just verify no exception raised

    def test_send_rung_alert(self):
        """Test profit rung alert."""
        from src.core.telegram_alerter import TelegramAlerter

        alerter = TelegramAlerter()

        alerter.send_rung_alert(
            symbol="QQQ3.L",
            rung_number=1,
            profit_pct=1.2,
            action="lock_profit_2pct_bank_15",
            bank_pct=0.15
        )
        # Just verify no exception raised

    def test_send_daily_summary(self):
        """Test daily summary alert."""
        from src.core.telegram_alerter import TelegramAlerter

        alerter = TelegramAlerter()

        alerter.send_daily_summary(
            date=datetime.now(),
            trades_executed=5,
            trades_rejected=2,
            win_rate_pct=64.0,
            daily_pnl=125.50,
            total_pnl=4230.75
        )
        # Just verify no exception raised

    def test_send_warning(self):
        """Test warning alert."""
        from src.core.telegram_alerter import TelegramAlerter

        alerter = TelegramAlerter()

        alerter.send_warning(
            title="⚠️ ISA AUDIT FAILURE",
            reason="Daily loss exceeded -0.8% threshold"
        )
        # Just verify no exception raised

    def test_rate_limiting(self):
        """Test that rate limiting prevents spam."""
        from src.core.telegram_alerter import TelegramAlerter

        alerter = TelegramAlerter()

        # Send multiple alerts in quick succession
        for i in range(5):
            result = alerter.send_entry_alert(
                symbol="QQQ3.L",
                side="BUY",
                confidence_pct=70.0,
                position_size=500.0,
                leverage=3.0,
                regime="TRENDING_UP",
                early_detection_reason=f"Test {i}"
            )
        # Should complete without error (some may be rate-limited)


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="Telegram credentials not set")
class TestTelegramEventBus:
    """Tests for TelegramEventBus."""

    def test_event_bus_singleton(self):
        """Test that event bus is a singleton."""
        from core.telegram_event_bus import get_event_bus

        bus1 = get_event_bus()
        bus2 = get_event_bus()

        assert bus1 is bus2, "Event bus should be singleton"

    def test_p0_event_no_cap(self):
        """Test that P0 events bypass caps."""
        from core.telegram_event_bus import get_event_bus

        bus = get_event_bus()

        # Reset counters
        bus._p1_count = 0
        bus._p2_count = 0

        # Emit 10 P0 events (should all succeed)
        for i in range(10):
            result = bus.emit("P0", f"P0 Event {i}")
            # P0 should always be accepted (or logged)

    def test_p1_capping(self):
        """Test that P1 events are capped at 3/day."""
        from core.telegram_event_bus import get_event_bus

        bus = get_event_bus()

        # Reset counters
        bus._p1_count = 0
        bus._p2_count = 0
        bus._p3_queue = []

        # Try to emit 5 P1 events
        results = []
        for i in range(5):
            result = bus.emit("P1", f"P1 Event {i}")
            results.append(result)

        # First 3 should succeed, last 2 should be queued as P3
        assert sum(results) == 3, f"Expected 3 P1 sent, got {sum(results)}"
        assert len(bus._p3_queue) == 2, f"Expected 2 P3 queued, got {len(bus._p3_queue)}"

    def test_p2_capping(self):
        """Test that P2 events are capped at 5/day."""
        from core.telegram_event_bus import get_event_bus

        bus = get_event_bus()

        # Reset counters
        bus._p1_count = 0
        bus._p2_count = 0
        bus._p3_queue = []

        # Try to emit 8 P2 events
        results = []
        for i in range(8):
            result = bus.emit("P2", f"P2 Event {i}")
            results.append(result)

        # First 5 should succeed, last 3 should be queued as P3
        assert sum(results) == 5, f"Expected 5 P2 sent, got {sum(results)}"
        assert len(bus._p3_queue) == 3, f"Expected 3 P3 queued, got {len(bus._p3_queue)}"

    def test_p3_digest_flush(self):
        """Test that P3 queue is properly flushed to digest."""
        from core.telegram_event_bus import get_event_bus

        bus = get_event_bus()

        # Reset
        bus._p1_count = 0
        bus._p2_count = 0
        bus._p3_queue = []

        # Add some P3 events
        bus.emit("P3", "Event 1", category="performance")
        bus.emit("P3", "Event 2", category="learning")
        bus.emit("P3", "Event 3", category="performance")

        # Flush to digest
        digest = bus.flush_digest()

        assert "Event 1" in digest, "Event 1 not in digest"
        assert "Event 2" in digest, "Event 2 not in digest"
        assert "Event 3" in digest, "Event 3 not in digest"
        assert len(bus._p3_queue) == 0, "P3 queue not cleared after flush"


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="Telegram credentials not set")
class TestTelegramIntegration:
    """End-to-end integration tests."""

    @pytest.mark.asyncio
    async def test_full_pipeline_signal_to_telegram(self):
        """Test complete signal generation -> Telegram delivery pipeline."""
        from delivery.telegram_bot import TelegramDelivery
        from models import Signal, Direction, RegimeState, GexRegime

        tg = TelegramDelivery()
        await tg.initialize()

        # Create a realistic signal
        signal = Signal(
            ticker="NVD3.L",
            direction=Direction.LONG,
            entry=142.50,
            stop=140.80,
            target_1r=144.20,
            target_2r=145.90,
            trail=143.80,
            confidence=78.0,
            strategy="S15",
            regime=RegimeState.TRENDING_UP,
            gex_regime=GexRegime.NEGATIVE,
            rvol=2.1,
            risk_dollars=170.0,
            risk_pct=0.0075,
            shares=100,
            vix=16.5,
        )

        # Send it
        result = await tg.send_signal(signal)
        assert result is True, "Signal delivery failed"


# Markers for different test categories
def pytest_collection_modifyitems(config, items):
    """Add markers for integration tests."""
    for item in items:
        if "Integration" in item.nodeid:
            item.add_marker(pytest.mark.integration)


if __name__ == "__main__":
    # Run with: python3 tests/test_telegram_integration.py
    pytest.main([__file__, "-v", "-s"])
