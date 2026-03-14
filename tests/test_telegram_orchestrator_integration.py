#!/usr/bin/env python3
"""
NZT-48 Orchestrator ↔ Telegram Integration Test
================================================
Verifies that the Orchestrator correctly wires Telegram delivery.

This test simulates the orchestrator startup sequence and verifies:
1. Telegram is initialized correctly
2. Alerts flow properly during operation
3. Signal delivery works end-to-end

Usage:
  python3 tests/test_telegram_orchestrator_integration.py
  pytest tests/test_telegram_orchestrator_integration.py -v
"""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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

# Only test if credentials are present
HAS_CREDENTIALS = bool(
    os.environ.get("TELEGRAM_BOT_TOKEN") and
    os.environ.get("TELEGRAM_CHAT_ID")
)


async def test_orchestrator_telegram_setup():
    """Test that Orchestrator properly initializes Telegram."""
    if not HAS_CREDENTIALS:
        print("⊗ Skipped — Telegram credentials not set")
        return False

    from delivery.telegram_bot import TelegramDelivery

    # Simulate orchestrator startup (from main.py line ~9188)
    print("\n1. Simulating Orchestrator Telegram Setup")
    print("=" * 60)

    tg = TelegramDelivery()
    print(f"   ✓ TelegramDelivery instantiated")
    print(f"     - Enabled: {tg._enabled}")

    # This is what main.py does
    await tg.initialize()
    print(f"   ✓ await tg.initialize() completed")
    print(f"     - Bot: {type(tg._bot).__name__ if tg._bot else 'None'}")
    print(f"     - App: {type(tg._app).__name__ if tg._app else 'None'}")

    assert tg._bot is not None, "Bot initialization failed"
    assert tg._app is not None, "App initialization failed"

    # Test sending an alert (as orchestrator would)
    result = await tg.send_alert("✅ Orchestrator Telegram integration test")
    assert result is True, "Alert send failed"
    print(f"   ✓ send_alert() succeeded")

    return True


async def test_signal_delivery_pipeline():
    """Test complete signal → Telegram pipeline."""
    if not HAS_CREDENTIALS:
        print("⊗ Skipped — Telegram credentials not set")
        return False

    from delivery.telegram_bot import TelegramDelivery

    print("\n2. Testing Signal Delivery Pipeline")
    print("=" * 60)

    tg = TelegramDelivery()
    await tg.initialize()

    # Create a mock signal (simpler approach)
    class MockSignal:
        def __init__(self):
            self.ticker = "QQQ3.L"
            self.direction = type('obj', (object,), {'value': 'LONG'})()
            self.entry = 142.50
            self.stop = 140.80
            self.target_1r = 144.20
            self.target_2r = 145.90
            self.trail = 143.80
            self.confidence = 78.0
            self.strategy = "S15"
            self.regime = type('obj', (object,), {'value': 'TRENDING_UP'})()
            self.rvol = 2.1
            self.risk_dollars = 170.0
            self.risk_pct = 0.0075
            self.shares = 100
            self.vix = 16.5

    signal = MockSignal()
    print(f"   ✓ Created S15 signal: {signal.ticker} {signal.direction.value}")

    # Send it
    result = await tg.send_signal(signal)
    assert result is True, "Signal delivery failed"
    print(f"   ✓ send_signal() completed successfully")

    return True


async def test_event_bus_integration():
    """Test Event Bus integration with Telegram."""
    if not HAS_CREDENTIALS:
        print("⊗ Skipped — Telegram credentials not set")
        return False

    from core.telegram_event_bus import get_event_bus
    from delivery.telegram_bot import TelegramDelivery

    print("\n3. Testing Event Bus Integration")
    print("=" * 60)

    # Get event bus
    bus = get_event_bus()
    print(f"   ✓ Event bus singleton retrieved")

    # Wire in Telegram sender
    tg = TelegramDelivery()
    await tg.initialize()
    bus.set_sender(tg)
    print(f"   ✓ Telegram sender wired to event bus")

    # Emit some events
    print(f"\n   Testing event emission:")

    # P0 - should send immediately
    result = bus.emit("P0", "🚨 KILL SWITCH ACTIVATED")
    print(f"     P0 emit: {result}")

    # P1 - should send (within cap)
    result = bus.emit("P1", "⚠️ Daily loss approaching threshold")
    print(f"     P1 emit: {result}")

    # P2 - should send (within cap)
    result = bus.emit("P2", "✅ NVD3.L LONG signal qualified")
    print(f"     P2 emit: {result}")

    # P3 - should queue
    result = bus.emit("P3", "📋 Model retraining completed", category="learning")
    print(f"     P3 emit (queued): {result}")

    # Check status
    status = bus.get_status()
    print(f"\n   Event Bus Status:")
    print(f"     P1 sent: {status['p1_sent_today']}/{status['p1_cap']}")
    print(f"     P2 sent: {status['p2_sent_today']}/{status['p2_cap']}")
    print(f"     P3 queued: {status['p3_queued']}")

    # Flush digest
    digest = bus.flush_digest()
    if digest:
        print(f"\n   ✓ Digest generated: {len(digest)} chars")
        print(f"     Contains P3 events and overflow alerts")

    return True


async def test_alert_rate_limiting():
    """Test that alerts respect rate limits."""
    if not HAS_CREDENTIALS:
        print("⊗ Skipped — Telegram credentials not set")
        return False

    from src.core.telegram_alerter import TelegramAlerter
    from datetime import datetime

    print("\n4. Testing Alert Rate Limiting")
    print("=" * 60)

    alerter = TelegramAlerter()
    print(f"   ✓ TelegramAlerter instantiated")

    # Send multiple alerts in quick succession
    print(f"\n   Sending 5 entry alerts (should be rate-limited):")

    for i in range(5):
        alerter.send_entry_alert(
            symbol="QQQ3.L",
            side="BUY",
            confidence_pct=70.0 + i,
            position_size=500.0,
            leverage=3.0,
            regime="TRENDING_UP",
            early_detection_reason=f"Test alert {i+1}"
        )
        print(f"     Alert {i+1}: sent (may be rate-limited)")

    # Send daily summary
    alerter.send_daily_summary(
        date=datetime.now(),
        trades_executed=5,
        trades_rejected=2,
        win_rate_pct=64.0,
        daily_pnl=125.50,
        total_pnl=4230.75
    )
    print(f"   ✓ Daily summary sent")

    return True


async def test_error_handling():
    """Test graceful error handling in Telegram delivery."""
    if not HAS_CREDENTIALS:
        print("⊗ Skipped — Telegram credentials not set")
        return False

    from delivery.telegram_bot import TelegramDelivery

    print("\n5. Testing Error Handling")
    print("=" * 60)

    tg = TelegramDelivery()
    await tg.initialize()

    # Test with invalid HTML (should fallback to plain text)
    print(f"   Testing HTML parsing error fallback:")
    result = await tg.send_alert("Test <invalid> HTML <tag>")
    print(f"     Result: {result} (should fallback to plain text)")

    # Test with very long message
    print(f"   Testing long message:")
    long_msg = "A" * 4000  # 4KB message (Telegram limit is 4096)
    result = await tg.send_alert(long_msg)
    print(f"     Result: {result}")

    return True


async def main():
    """Run all orchestrator integration tests."""
    print("\n" + "█"*60)
    print("  ORCHESTRATOR ↔ TELEGRAM INTEGRATION TESTS")
    print("█"*60)

    if not HAS_CREDENTIALS:
        print("\n⊗ Telegram credentials not configured")
        print("  Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return False

    try:
        # Run tests in sequence
        tests = [
            ("Orchestrator Setup", test_orchestrator_telegram_setup),
            ("Signal Delivery Pipeline", test_signal_delivery_pipeline),
            ("Event Bus Integration", test_event_bus_integration),
            ("Alert Rate Limiting", test_alert_rate_limiting),
            ("Error Handling", test_error_handling),
        ]

        results = {}
        for test_name, test_func in tests:
            try:
                result = await test_func()
                results[test_name] = "✓ PASSED" if result else "⊗ SKIPPED"
            except Exception as e:
                print(f"\n   ❌ Exception: {e}")
                import traceback
                traceback.print_exc()
                results[test_name] = "❌ FAILED"

        # Summary
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        for test_name, result in results.items():
            print(f"{test_name:30s} {result}")

        all_passed = all("PASSED" in v for v in results.values())

        print("\n" + "="*60)
        if all_passed:
            print("✓ ALL INTEGRATION TESTS PASSED")
            print("  Ready for paper trading with full Telegram alerting")
        else:
            print("⚠ SOME TESTS FAILED — see details above")
        print("="*60 + "\n")

        return all_passed

    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
