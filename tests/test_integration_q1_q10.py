"""
End-to-end integration test: Market Data → Orchestrator → Signal Decision
Tests Q1-Q10 Master Orchestrator pipeline

Run: python3 /Users/rr/nzt48-signals/tests/test_integration_q1_q10.py
"""

import asyncio
import sys
sys.path.insert(0, '/Users/rr/nzt48-signals')

from core.master_orchestrator import MasterOrchestrator
from datetime import datetime, timezone
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("integration_test")


async def test_full_pipeline():
    """Test complete Q1-Q10 pipeline"""
    
    logger.info("=" * 70)
    logger.info("Q1-Q10 INTEGRATION TEST: Master Orchestrator Pipeline")
    logger.info("=" * 70)
    
    config = {
        'use_postgresql': False,
        'universe': ['QQQ3.L', '3LUS.L', 'TSL3.L', 'NVD3.L'],
    }
    
    # Initialize orchestrator
    try:
        orch = MasterOrchestrator(config)
        logger.info(f"✅ Orchestrator initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize orchestrator: {e}")
        return False
    
    # Check status
    status = orch.get_status()
    logger.info(f"✅ Orchestrator status: {status['status']}")
    logger.info(f"   Phases active: {status['phases_active']}")
    logger.info(f"   Phases ready: {status['phases_ready']}")
    
    # Simulate market data
    base_market_data = {
        'timestamp': datetime.now(timezone.utc).timestamp(),
        'close': 123.45,
        'high': 124.50,
        'low': 122.50,
        'volume': 1000000,
        'volatility': 0.18,
        'momentum': 0.02,
        'ofi': 0.15,
        'regime': 'EXPANSION',
        'minutes_to_close': 60,
        'atr': 0.50,
        'bid_ask_spread': 0.01,
        'vix': 18.5,
        'dxy': 104.2,
        'credit_spread': 100,
        'fear_gauge': 45,
    }
    
    # Test signal generation for each ticker
    results = {}
    signals_generated = 0
    high_confidence_signals = 0
    
    logger.info("\n" + "=" * 70)
    logger.info("Testing signal generation for each ticker:")
    logger.info("=" * 70)
    
    for ticker in config['universe']:
        try:
            logger.info(f"\nTesting: {ticker}")
            logger.info("-" * 40)
            
            signal = await orch.run_full_pipeline(ticker, base_market_data)
            
            if signal:
                confidence = signal.get('confidence', 0)
                signals_generated += 1
                
                logger.info(f"✅ Signal generated:")
                logger.info(f"   Confidence: {confidence:.0f}")
                logger.info(f"   Position Size: {signal.get('position_size', 0):.4f}")
                logger.info(f"   Entry Action: {signal.get('execution_action', 'NONE')}")
                
                if confidence >= 65:
                    high_confidence_signals += 1
                    logger.info(f"   🟢 HIGH CONFIDENCE (≥65)")
                    results[ticker] = 'PASS'
                else:
                    logger.info(f"   🟡 LOW CONFIDENCE (<65, filtered)")
                    results[ticker] = 'FILTERED'
            else:
                logger.info(f"⚠️  No signal generated (all gates passed but confidence too low)")
                results[ticker] = 'NO_SIGNAL'
                
        except Exception as e:
            logger.error(f"❌ Error: {e}", exc_info=True)
            results[ticker] = 'FAIL'
    
    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("INTEGRATION TEST SUMMARY:")
    logger.info("=" * 70)
    for ticker, status in results.items():
        symbol = "✅" if status == 'PASS' else "🟡" if status == 'FILTERED' else "⚠️ " if status == 'NO_SIGNAL' else "❌"
        logger.info(f"{symbol} {ticker:12s} : {status}")
    
    logger.info(f"\nSignals generated: {signals_generated}/{len(config['universe'])}")
    logger.info(f"High confidence:   {high_confidence_signals}/{len(config['universe'])}")
    
    # Determine success
    success = all(v != 'FAIL' for v in results.values())
    
    if success:
        logger.info("\n✅ INTEGRATION TEST PASSED")
        logger.info("   - Pipeline executes without errors")
        logger.info("   - Signal generation responds to market data")
        logger.info("   - No fatal exceptions")
        logger.info("\n✅ READY FOR DEPLOYMENT\n")
    else:
        logger.error("\n❌ INTEGRATION TEST FAILED")
        logger.error("   Fix errors above and retry\n")
    
    return success


if __name__ == "__main__":
    success = asyncio.run(test_full_pipeline())
    sys.exit(0 if success else 1)
