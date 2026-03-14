#!/usr/bin/env python3
"""
Comprehensive Telegram Diagnostics
===================================
Tests the complete Telegram alerting pipeline.

Usage:
  python test_telegram_diagnostics.py
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("telegram_diagnostics")

# Add project root to path
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass

async def test_environment_vars():
    """Check if Telegram credentials are set."""
    print("\n" + "="*60)
    print("1. ENVIRONMENT VARIABLE CHECK")
    print("="*60)

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    print(f"✓ TELEGRAM_BOT_TOKEN: {'SET' if bot_token else 'NOT SET'}")
    if bot_token:
        print(f"  Length: {len(bot_token)} chars")
        print(f"  Masked: {bot_token[:10]}...{bot_token[-4:]}")

    print(f"✓ TELEGRAM_CHAT_ID: {'SET' if chat_id else 'NOT SET'}")
    if chat_id:
        print(f"  Value: {chat_id}")
        try:
            chat_id_int = int(chat_id)
            print(f"  Valid: YES (numeric)")
        except ValueError:
            print(f"  Valid: NO (not numeric) — this is a problem!")

    return bool(bot_token and chat_id)

async def test_telegram_api_direct():
    """Test Telegram Bot API with direct HTTP requests."""
    print("\n" + "="*60)
    print("2. TELEGRAM BOT API DIRECT TEST (via requests)")
    print("="*60)

    import requests

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not bot_token or not chat_id:
        print("❌ Skipped — missing credentials")
        return False

    try:
        # Test 1: getMe
        print("\n  [getMe] Testing bot identity...")
        url = f"https://api.telegram.org/bot{bot_token}/getMe"
        resp = requests.get(url, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                result = data.get("result", {})
                print(f"  ✓ Bot found: @{result.get('username')} (ID: {result.get('id')})")
            else:
                print(f"  ❌ API error: {data.get('description', 'unknown')}")
                return False
        else:
            print(f"  ❌ HTTP {resp.status_code}: {resp.text}")
            return False

        # Test 2: sendMessage with test message
        print(f"\n  [sendMessage] Sending test message to chat {chat_id}...")
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": f"🤖 NZT-48 Telegram Test — {datetime.now().strftime('%H:%M:%S UTC')}\nDiagnostics PASSED",
            "parse_mode": "HTML"
        }

        resp = requests.post(url, json=payload, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                msg_id = data.get("result", {}).get("message_id")
                print(f"  ✓ Message sent successfully (ID: {msg_id})")
                return True
            else:
                print(f"  ❌ API error: {data.get('description', 'unknown')}")
                return False
        else:
            print(f"  ❌ HTTP {resp.status_code}: {resp.text}")
            return False

    except Exception as e:
        print(f"  ❌ Exception: {e}")
        return False

async def test_telegram_delivery_class():
    """Test the TelegramDelivery class."""
    print("\n" + "="*60)
    print("3. TELEGRAM DELIVERY CLASS TEST")
    print("="*60)

    try:
        from delivery.telegram_bot import TelegramDelivery

        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        print(f"\n  Instantiating TelegramDelivery...")
        tg = TelegramDelivery(token=bot_token, chat_id=chat_id)

        print(f"  ✓ Created instance")
        print(f"    - Enabled: {tg._enabled}")
        print(f"    - Bot: {tg._bot}")
        print(f"    - App: {tg._app}")

        # Check verify_connection
        print(f"\n  Calling verify_connection()...")
        result = await tg.verify_connection()
        print(f"  ✓ verify_connection returned: {result}")

        # Try to initialize
        print(f"\n  Calling initialize()...")
        await tg.initialize()
        print(f"  ✓ Initialize called")
        print(f"    - Bot now: {tg._bot}")
        print(f"    - App now: {tg._app}")

        # Try to send a test message
        if tg._enabled:
            print(f"\n  Calling send_alert()...")
            text = f"📋 NZT-48 Class Test — {datetime.now().strftime('%H:%M:%S UTC')}\nClass test PASSED"
            result = await tg.send_alert(text)
            print(f"  ✓ send_alert returned: {result}")
        else:
            print(f"  ❌ TelegramDelivery disabled after initialize()")

        return True

    except Exception as e:
        logger.exception("Exception in TelegramDelivery test:")
        print(f"  ❌ Exception: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_telegram_alerter_class():
    """Test the TelegramAlerter class."""
    print("\n" + "="*60)
    print("4. TELEGRAM ALERTER CLASS TEST")
    print("="*60)

    try:
        from src.core.telegram_alerter import TelegramAlerter

        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        print(f"\n  Instantiating TelegramAlerter...")
        alerter = TelegramAlerter(bot_token=bot_token, chat_id=chat_id)

        print(f"  ✓ Created instance")
        print(f"    - Enabled: {alerter.enabled}")

        # Try to send an entry alert
        print(f"\n  Calling send_entry_alert()...")
        alerter.send_entry_alert(
            symbol="QQQ3.L",
            side="BUY",
            confidence_pct=78.5,
            position_size=500.0,
            leverage=3.0,
            regime="TRENDING_UP",
            early_detection_reason="Golden cross + RSI >50"
        )
        print(f"  ✓ send_entry_alert called")

        return True

    except Exception as e:
        logger.exception("Exception in TelegramAlerter test:")
        print(f"  ❌ Exception: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """Run all diagnostics."""
    print("\n" + "█"*60)
    print("  NZT-48 TELEGRAM ALERTING DIAGNOSTICS")
    print("█"*60)

    results = {}

    # Test 1: Environment variables
    results["env_vars"] = await test_environment_vars()

    # Test 2: Direct API test
    if results["env_vars"]:
        results["direct_api"] = await test_telegram_api_direct()
    else:
        print("\n✓ Skipped direct API test — missing credentials")
        results["direct_api"] = None

    # Test 3: TelegramDelivery class
    if results["env_vars"]:
        results["delivery_class"] = await test_telegram_delivery_class()
    else:
        print("\n✓ Skipped TelegramDelivery test — missing credentials")
        results["delivery_class"] = None

    # Test 4: TelegramAlerter class
    if results["env_vars"]:
        results["alerter_class"] = await test_telegram_alerter_class()
    else:
        print("\n✓ Skipped TelegramAlerter test — missing credentials")
        results["alerter_class"] = None

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for test_name, result in results.items():
        if result is None:
            status = "⊗ SKIPPED"
        elif result:
            status = "✓ PASSED"
        else:
            status = "❌ FAILED"
        print(f"{test_name:20s} {status}")

    all_passed = all(v for v in results.values() if v is not None)

    print("\n" + "="*60)
    if all_passed:
        print("✓ ALL TESTS PASSED — Telegram alerting is working!")
    else:
        print("❌ SOME TESTS FAILED — See details above")
    print("="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
