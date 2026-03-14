"""
Example: How to use the Master Orchestrator in your trading loop

This shows the full Q1-Q10 integration in action.
"""

import asyncio
import logging
from datetime import datetime
from core.master_orchestrator import get_orchestrator

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("orchestrator_example")


async def example_single_signal():
    """Example 1: Generate single signal through full Q1-Q10 pipeline"""
    
    # Initialize orchestrator
    config = {
        'use_postgresql': False,
        'use_fpga': False,
        'use_quantum': False,
        'universe': ['QQQ3.L', '3LUS.L', 'TSL3.L', 'NVD3.L', 'GPT3.L', 'MU2.L', 'TSM3.L', '3SEM.L']
    }
    orchestrator = get_orchestrator(config)
    
    # Market data for a single asset
    market_data = {
        'timestamp': datetime.now(),
        'volatility': 0.15,           # 15% annualized
        'momentum': 0.02,             # 2% short-term momentum
        'ofi': 100000,                # Order flow imbalance
        'regime': 'NORMAL',           # Market regime
        'minutes_to_close': 60,       # Time to market close
    }
    
    # Run full pipeline: Q1 → Q7-Q8 → Q2 → Q5 → Q6
    ticker = 'QQQ3.L'
    signal = await orchestrator.run_full_pipeline(ticker, market_data)
    
    if signal:
        print(f"\n✅ Signal Generated for {ticker}")
        print(f"   Confidence: {signal.get('confidence', 0):.0f}%")
        print(f"   Position Size: {signal.get('position_size', 0):.2f}")
        print(f"   Direction: {signal.get('direction', 'UNKNOWN')}")
    else:
        print(f"\n❌ No signal for {ticker} (confidence gate not passed)")


async def example_batch_universe():
    """Example 2: Scan entire universe for signals"""
    
    orchestrator = get_orchestrator({
        'universe': ['QQQ3.L', '3LUS.L', 'TSL3.L', 'NVD3.L', 'GPT3.L', 'MU2.L', 'TSM3.L', '3SEM.L']
    })
    
    universe = ['QQQ3.L', '3LUS.L', 'TSL3.L', 'NVD3.L']
    
    print(f"\nScanning {len(universe)} assets...")
    
    tasks = []
    for ticker in universe:
        market_data = {
            'timestamp': datetime.now(),
            'volatility': 0.12 + (hash(ticker) % 50) / 1000,
            'momentum': 0.01 + (hash(ticker) % 20) / 1000,
            'ofi': 50000 + (hash(ticker) % 100000),
            'regime': 'NORMAL',
            'minutes_to_close': 60,
        }
        
        task = orchestrator.run_full_pipeline(ticker, market_data)
        tasks.append((ticker, task))
    
    # Gather all results
    results = []
    for ticker, task in tasks:
        signal = await task
        if signal and signal.get('confidence', 0) >= 65:
            results.append({
                'ticker': ticker,
                'confidence': signal.get('confidence', 0),
                'size': signal.get('position_size', 0)
            })
    
    print(f"✅ Found {len(results)} tradeable signals")
    for r in results:
        print(f"   {r['ticker']}: {r['confidence']:.0f}% confidence, {r['size']:.2f} size")


async def example_with_position():
    """Example 3: Use DQN + Hawkes when managing existing position"""
    
    orchestrator = get_orchestrator({
        'universe': ['QQQ3.L']
    })
    
    # Simulate an open position
    position = {
        'ticker': 'QQQ3.L',
        'pnl_pct': 0.5,        # +0.5% P&L
        'status': 'OPEN',
        'entry_price': 100.0,
        'current_price': 100.5,
    }
    
    market_data = {
        'timestamp': datetime.now(),
        'volatility': 0.18,
        'momentum': -0.01,     # Negative momentum
        'ofi': -50000,         # Negative order flow
        'regime': 'VOLATILE',
        'minutes_to_close': 30,
    }
    
    # Pipeline will use DQN to optimize execution + Hawkes for exit timing
    signal = await orchestrator.run_full_pipeline(
        'QQQ3.L',
        market_data,
        position=position
    )
    
    if signal:
        print(f"\n✅ Position Update for {position['ticker']}")
        print(f"   Current P&L: {position['pnl_pct']:.2f}%")
        print(f"   Suggested Exit: {position.get('suggested_exit', 'N/A')}")
        print(f"   DQN Action: {signal.get('execution_action', 'HOLD')}")


def example_check_status():
    """Example 4: Check orchestrator status"""
    
    orchestrator = get_orchestrator({})
    status = orchestrator.get_status()
    
    print("\n" + "="*70)
    print("ORCHESTRATOR STATUS")
    print("="*70)
    print(f"Overall: {status['status']}")
    print(f"Operational: {status['operational']}")
    print(f"\nActive Phases: {status['phases_active']}/10")
    print(f"Ready Phases: {status['phases_ready']}/10")
    
    print("\nPhase Details:")
    for k in sorted([k for k in status.keys() if k.startswith('q')]):
        state = status[k]
        symbol = "✅" if state == "active" else ("⏳" if state == "ready" else "🔮")
        print(f"  {symbol} {k}: {state}")
    print("="*70)


async def main():
    """Run all examples"""
    
    print("\n" + "="*70)
    print("Q1-Q10 MASTER ORCHESTRATOR EXAMPLES")
    print("="*70)
    
    # Example 1: Single signal
    print("\n[Example 1: Single Signal]")
    await example_single_signal()
    
    # Example 2: Batch universe scan
    print("\n[Example 2: Batch Universe Scan]")
    await example_batch_universe()
    
    # Example 3: Position management with DQN + Hawkes
    print("\n[Example 3: Position Management with DQN + Hawkes]")
    await example_with_position()
    
    # Example 4: Status check (non-async)
    print("\n[Example 4: Orchestrator Status]")
    example_check_status()
    
    print("\n✅ All examples completed successfully")


if __name__ == "__main__":
    asyncio.run(main())
