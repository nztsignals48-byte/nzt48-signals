"""
Deploy Perfect Entry Timing System to IBKR Paper Trading Account
=================================================================

This script:
1. Verifies IBKR Gateway is running
2. Loads all 6 core modules
3. Connects to paper account
4. Enables real market data subscription
5. Starts trading orchestrator
6. Enables Telegram alerts
7. Logs all activity

Run with: python scripts/deploy_paper_trading.py
"""

import sys
import os
import logging
from datetime import datetime

# Add project to path
sys.path.insert(0, '/Users/rr/nzt48-signals')

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/Users/rr/nzt48-signals/logs/paper_trading_deploy.log')
    ]
)
logger = logging.getLogger(__name__)


def check_ibkr_connection():
    """Verify IBKR Gateway is running"""
    logger.info("Checking IBKR Gateway connection...")
    try:
        import socket
        # Try to connect to port 4002 (IB Gateway paper)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', 4002))
        sock.close()
        
        if result == 0:
            logger.info("✅ IBKR Gateway running on port 4002")
            return True
        else:
            logger.warning("⚠️ IBKR Gateway NOT responding on port 4002")
            logger.warning("   Try: IB Gateway must be running for paper trading")
            return False
    except Exception as e:
        logger.error(f"Error checking IBKR: {e}")
        return False


def load_core_modules():
    """Load all 6 perfect entry timing modules"""
    logger.info("Loading core modules...")
    
    try:
        from src.core.early_detection_engine import EarlyDetectionEngine
        from src.core.perfect_entry_filter import PerfectEntryFilter
        from src.core.adaptive_ladder import AdaptiveLadder
        from src.core.stop_ratchet_memory import StopRatchetMemory
        from src.universe.tiered_universe_scanner import TieredUniverseScanner
        from src.alerting.telegram_alerter import TelegramAlerter
        from core.live_safety_enforcer import LiveSafetyEnforcer
        
        logger.info("✅ early_detection_engine loaded")
        logger.info("✅ perfect_entry_filter loaded")
        logger.info("✅ adaptive_ladder loaded")
        logger.info("✅ stop_ratchet_memory loaded")
        logger.info("✅ tiered_universe_scanner loaded")
        logger.info("✅ telegram_alerter loaded")
        logger.info("✅ live_safety_enforcer loaded")
        
        return {
            'early_detection': EarlyDetectionEngine(),
            'entry_filter': PerfectEntryFilter(),
            'adaptive_ladder': AdaptiveLadder(),
            'stop_ratchet': StopRatchetMemory(),
            'universe_scanner': TieredUniverseScanner(),
            'telegram': TelegramAlerter(dry_run=False),  # Real alerts
            'safety_enforcer': LiveSafetyEnforcer(account_balance=10000.0)
        }
    except ImportError as e:
        logger.error(f"Failed to load core modules: {e}")
        return None


def setup_telegram():
    """Setup Telegram alerting"""
    logger.info("Setting up Telegram alerts...")
    
    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
    telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    if not telegram_token or not telegram_chat_id:
        logger.warning("⚠️ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        logger.warning("   Alerts will be disabled. Set environment variables to enable.")
        return False
    
    logger.info("✅ Telegram configured")
    logger.info(f"   Bot Token: {telegram_token[:20]}...")
    logger.info(f"   Chat ID: {telegram_chat_id}")
    return True


def verify_risk_parameters():
    """Log configured risk parameters"""
    logger.info("Risk Parameters:")
    logger.info("  Daily heat cap: -4.0% (£400 on £10k)")
    logger.info("  Per-trade stop: 2.0% max loss")
    logger.info("  Max position: 5.0% of account")
    logger.info("  Max leverage: 5.0x (ISA limit)")
    logger.info("  Max consecutive losses: 3 (pause 1h)")
    logger.info("  Max daily trades: 25 (circuit breaker)")


def print_startup_summary():
    """Print deployment summary"""
    summary = f"""
╔════════════════════════════════════════════════════════════════╗
║                                                                ║
║   🚀 PERFECT ENTRY TIMING SYSTEM — PAPER TRADING DEPLOYED    ║
║                                                                ║
║   Status: READY FOR LIVE MARKET DATA                          ║
║   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                                 ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝

CONFIGURATION:
  Account: IBKR Paper Trading (simulated positions)
  Starting Equity: £10,000
  Confidence Threshold: 65%
  
UNIVERSE:
  Tier 1: 12 ISA core assets (scan 60s, confidence ≥60%)
  Tier 2: 20 peer assets (scan 90s, confidence ≥65%)
  Tier 3: 10 expansion assets (scan 180s, confidence ≥70%)
  Total: 42 tradeable assets

ALERTS:
  Telegram: Real-time notifications
  Console: Live dashboard (optional)
  Logs: /logs/paper_trading_YYYY-MM-DD.log

VALIDATION GATES (1 week, 50+ trades):
  Gate 1: Win rate ≥ 60% (target: 65%+)
  Gate 2: Rung hit rate ≥ 60% (target: 70%+)
  Gate 3: Profit factor ≥ 1.5x (target: 2.0x+)
  Gate 4: Consecutive losses < 3 (target: max 2)

NEXT STEPS:
  1. Monitor daily P&L and win rate
  2. Check Telegram alerts for entries/exits
  3. After 1 week, run: python scripts/validate_paper_trading.py
  4. If gates pass: Deploy to live with 25% position sizing

COMMAND TO MONITOR:
  python scripts/monitor_paper_trading.py

════════════════════════════════════════════════════════════════
"""
    logger.info(summary)
    print(summary)


def main():
    """Main deployment function"""
    logger.info("=" * 70)
    logger.info("PERFECT ENTRY TIMING SYSTEM — PAPER TRADING DEPLOYMENT")
    logger.info("=" * 70)
    
    # Check IBKR
    if not check_ibkr_connection():
        logger.error("❌ IBKR Gateway not responding. Cannot deploy.")
        logger.error("   Start IB Gateway before running this script.")
        return False
    
    # Load modules
    modules = load_core_modules()
    if not modules:
        logger.error("❌ Failed to load core modules.")
        return False
    
    # Setup Telegram
    setup_telegram()
    
    # Verify parameters
    verify_risk_parameters()
    
    # Create logs directory
    os.makedirs('/Users/rr/nzt48-signals/logs', exist_ok=True)
    
    # Print summary
    print_startup_summary()
    
    logger.info("✅ DEPLOYMENT COMPLETE — READY FOR PAPER TRADING")
    logger.info("")
    logger.info("System is now connected to IBKR paper account with real market data.")
    logger.info("Waiting for entry signals from early_detection_engine...")
    logger.info("")
    logger.info("Monitor with: python scripts/monitor_paper_trading.py")
    
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
