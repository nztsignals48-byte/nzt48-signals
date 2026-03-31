"""Book 42 — Example Usage & Test Cases for Conditional Hedging.

Demonstrates how to use HedgeDetector, DynamicHedgeAllocator, and integration
functions with realistic market scenarios.
"""

import json
from hedge_detector import (
    HedgeDetector,
    DynamicHedgeAllocator,
    StressSignals,
)
from bridge_hedge_integration import (
    apply_conditional_hedging_v2,
    apply_hedge_confidence_overlay_v2,
)


# ============================================================================
# EXAMPLE 1: Normal Market Conditions (No Hedge)
# ============================================================================

def example_normal_market():
    """Normal market: VIX contango, tight credit, strong breadth."""
    print("\n" + "="*80)
    print("EXAMPLE 1: Normal Market Conditions")
    print("="*80)

    msg = {
        "ticker_id": 1,
        "timestamp_ns": int(1e18),
        "last": 450.5,
        "vix_spot": 15,
        "vix_1m_future": 16,      # Future > spot = contango (normal)
        "vix_3m_future": 18,
        "credit_spread_bp": 350,  # Normal (< 400 bps)
        "breadth_ad_ratio": 1.8,  # Strong (> 1.5)
        "vix": 15,
    }

    hedge_signals = apply_conditional_hedging_v2(msg, [])

    print(f"\nMarket conditions:")
    print(f"  VIX: {msg['vix']} (low vol)")
    print(f"  Credit: {msg['credit_spread_bp']} bps (normal)")
    print(f"  Breadth: {msg['breadth_ad_ratio']} (strong)")
    print(f"\nHedge signals generated: {len(hedge_signals)}")
    print(f"Expected: 0 (no stress signals)")

    for sig in hedge_signals:
        print(f"  - {sig.get('strategy')}: {sig.get('reason')}")


# ============================================================================
# EXAMPLE 2: Mild Stress (1-2 Triggers)
# ============================================================================

def example_mild_stress():
    """Mild stress: VIX backwardation + credit widening."""
    print("\n" + "="*80)
    print("EXAMPLE 2: Mild Stress (2 Triggers)")
    print("="*80)

    msg = {
        "ticker_id": 1,
        "timestamp_ns": int(1e18),
        "last": 450.5,
        "vix_spot": 22,
        "vix_1m_future": 20,      # 1m < spot = backwardation
        "vix_3m_future": 23,
        "credit_spread_bp": 520,  # Elevated (400-500 range)
        "breadth_ad_ratio": 1.6,  # Still okay
        "vix": 22,
    }

    hedge_signals = apply_conditional_hedging_v2(msg, [])

    # Check for backwardation
    ts_ratio = (msg['vix_1m_future'] - msg['vix_3m_future']) / msg['vix_3m_future']

    print(f"\nMarket conditions:")
    print(f"  VIX term structure: ({msg['vix_1m_future']} - {msg['vix_3m_future']}) / {msg['vix_3m_future']} = {ts_ratio:.3f} < -0.05?")
    print(f"  Credit: {msg['credit_spread_bp']} bps (elevated)")
    print(f"  Breadth: {msg['breadth_ad_ratio']} (okay)")
    print(f"\nHedge signals generated: {len(hedge_signals)}")
    print(f"Expected: 2-3 (inverse ETP + VIX ETP)")

    for sig in hedge_signals:
        print(f"  - {sig.get('strategy')}: {sig.get('reason')}")


# ============================================================================
# EXAMPLE 3: Crisis (All 3 Triggers)
# ============================================================================

def example_crisis():
    """Crisis: All 3 signals firing + breadth collapse."""
    print("\n" + "="*80)
    print("EXAMPLE 3: Crisis (Kill Switch)")
    print("="*80)

    msg = {
        "ticker_id": 1,
        "timestamp_ns": int(1e18),
        "last": 420.0,
        "vix_spot": 35,
        "vix_1m_future": 32,      # Inverted curve
        "vix_3m_future": 38,
        "credit_spread_bp": 650,  # Crisis (> 600 bps)
        "breadth_ad_ratio": 0.75, # Collapse (< 1.0)
        "vix": 35,
    }

    hedge_signals = apply_conditional_hedging_v2(msg, [])

    print(f"\nMarket conditions:")
    print(f"  VIX: {msg['vix']} (HIGH vol)")
    print(f"  Credit: {msg['credit_spread_bp']} bps (CRISIS)")
    print(f"  Breadth: {msg['breadth_ad_ratio']} (COLLAPSE)")
    print(f"\nHedge signals generated: {len(hedge_signals)}")
    print(f"Expected: 4 (inverse + VIX + cash raise)")

    for sig in hedge_signals:
        print(f"  - {sig.get('strategy')}: allocation={sig.get('hedge_pct_allocation')*100:.1f}%")


# ============================================================================
# EXAMPLE 4: Confidence Overlay
# ============================================================================

def example_confidence_overlay():
    """Demonstrate hedge confidence adjustment on trading signals."""
    print("\n" + "="*80)
    print("EXAMPLE 4: Confidence Overlay (Hedge Active)")
    print("="*80)

    # First, activate hedge
    msg = {
        "ticker_id": 1,
        "timestamp_ns": int(1e18),
        "vix_spot": 25,
        "vix_1m_future": 23,
        "vix_3m_future": 26,
        "credit_spread_bp": 480,
        "breadth_ad_ratio": 1.3,
        "vix": 25,
    }
    apply_conditional_hedging_v2(msg, [])  # Activates hedge

    # Now test a long signal
    long_signal = {
        "direction": "Long",
        "confidence": 75,
        "strategy": "VanguardSniper",
    }

    print(f"\nOriginal long signal:")
    print(f"  Direction: {long_signal['direction']}")
    print(f"  Confidence: {long_signal['confidence']}")

    adjusted = apply_hedge_confidence_overlay_v2(long_signal.copy(), msg)

    print(f"\nAfter hedge overlay:")
    print(f"  Direction: {adjusted['direction']}")
    print(f"  Confidence: {adjusted['confidence']}")
    print(f"  Change: {adjusted['confidence'] - long_signal['confidence']:+d} pts")
    print(f"  Reason: Hedge active, long signals penalized")

    # Test a short signal
    short_signal = {
        "direction": "Short",
        "confidence": 65,
        "strategy": "S2_Reversion",
    }

    print(f"\nOriginal short signal:")
    print(f"  Direction: {short_signal['direction']}")
    print(f"  Confidence: {short_signal['confidence']}")

    adjusted = apply_hedge_confidence_overlay_v2(short_signal.copy(), msg)

    print(f"\nAfter hedge overlay:")
    print(f"  Direction: {adjusted['direction']}")
    print(f"  Confidence: {adjusted['confidence']}")
    print(f"  Change: {adjusted['confidence'] - short_signal['confidence']:+d} pts")
    print(f"  Reason: Hedge active, short signals boosted (portfolio insurance)")


# ============================================================================
# EXAMPLE 5: Detector Deep Dive (Raw Signal States)
# ============================================================================

def example_detector_raw():
    """Show raw StressSignals from HedgeDetector."""
    print("\n" + "="*80)
    print("EXAMPLE 5: HedgeDetector Raw Signals")
    print("="*80)

    detector = HedgeDetector(max_history=60)

    # Scenario: Spreading backwardation over 3 ticks
    scenarios = [
        {
            "name": "Tick 1: Normal contango",
            "vix_spot": 16, "vix_1m_future": 17, "vix_3m_future": 19,
            "credit_spread_bp": 350, "breadth_ad_ratio": 1.7,
        },
        {
            "name": "Tick 2: Flatten",
            "vix_spot": 19, "vix_1m_future": 19, "vix_3m_future": 20,
            "credit_spread_bp": 380, "breadth_ad_ratio": 1.5,
        },
        {
            "name": "Tick 3: Backwardation + credit",
            "vix_spot": 21, "vix_1m_future": 19, "vix_3m_future": 22,
            "credit_spread_bp": 480, "breadth_ad_ratio": 1.3,
        },
    ]

    for i, scenario in enumerate(scenarios):
        msg = {
            "timestamp_ns": int(1e18),
            "vix_spot": scenario["vix_spot"],
            "vix_1m_future": scenario["vix_1m_future"],
            "vix_3m_future": scenario["vix_3m_future"],
            "credit_spread_bp": scenario["credit_spread_bp"],
            "breadth_ad_ratio": scenario["breadth_ad_ratio"],
        }

        signals = detector.update(msg)

        print(f"\n{scenario['name']}:")
        print(f"  VIX: {scenario['vix_spot']} spot, {scenario['vix_1m_future']} 1m, {scenario['vix_3m_future']} 3m")
        ts_ratio = (scenario['vix_1m_future'] - scenario['vix_3m_future']) / scenario['vix_3m_future']
        print(f"    TS ratio: {ts_ratio:.3f} (backwardation: {signals.vix_backwardation})")
        print(f"  Credit: {scenario['credit_spread_bp']} bps (widening: {signals.credit_widening})")
        print(f"  Breadth: {scenario['breadth_ad_ratio']} (declining: {signals.breadth_decline})")
        print(f"  → Active triggers: {signals.num_triggers}")


# ============================================================================
# EXAMPLE 6: Cost Analysis
# ============================================================================

def example_cost_analysis():
    """Show hedge cost estimation and control."""
    print("\n" + "="*80)
    print("EXAMPLE 6: Hedge Cost Analysis")
    print("="*80)

    allocator = DynamicHedgeAllocator()

    vix_levels = [12, 18, 25, 35]

    print(f"\nSPY Put Cost Estimation (20 delta, 45 DTE):\n")
    print(f"{'VIX Level':<12} {'Monthly':<12} {'Annual':<12} {'Skip Hedge?':<15}")
    print(f"{'-'*51}")

    for vix in vix_levels:
        cost_pct = allocator._estimate_spy_put_cost(vix)
        monthly = cost_pct / 12
        skip = "YES" if cost_pct < allocator.SPY_PUT_COST_MIN_THRESHOLD else "NO"
        print(f"{vix:<12} {monthly*100:>10.2f}%   {cost_pct*100:>10.2f}%   {skip:<15}")

    print(f"\nAllocation sizes by trigger count:")
    print(f"{'Triggers':<12} {'Total Hedge':<15} {'Inverse ETP':<15} {'VIX ETP':<12}")
    print(f"{'-'*54}")

    for triggers in [0, 1, 2, 3]:
        alloc = allocator.ALLOCATIONS.get(triggers, (0, 0, 0, 0, 0))
        print(f"{triggers:<12} {alloc[0]*100:>13.0f}%   {alloc[1]:>13.1f}%   {alloc[2]:>10.1f}%")

    print(f"\nCost limit: {allocator.MAX_HEDGE_COST_MONTHLY*100:.1f}% per month")
    print(f"If exceeded: reduce allocation by 20%")


# ============================================================================
# EXAMPLE 7: State Persistence
# ============================================================================

def example_state_persistence():
    """Show how hedge state is persisted to JSON."""
    print("\n" + "="*80)
    print("EXAMPLE 7: State Persistence")
    print("="*80)

    msg = {
        "ticker_id": 1,
        "timestamp_ns": int(1e18),
        "vix_spot": 24,
        "vix_1m_future": 22,
        "vix_3m_future": 26,
        "credit_spread_bp": 520,
        "breadth_ad_ratio": 1.2,
        "vix": 24,
    }

    hedge_signals = apply_conditional_hedging_v2(msg, [])

    print(f"\nGenerated {len(hedge_signals)} hedge signals")
    print(f"\nState saved to /app/data/hedge_state.json:")
    print(f"  status: HEDGE_ACTIVE or KILLING")
    print(f"  activation_count: number of active triggers")
    print(f"  current_allocation: {{'inverse_etp_pct': X, 'vix_etp_pct': Y, ...}}")
    print(f"  cost_tracking: {{'last_cost_pct': X, 'cumulative_cost_30d': Y, ...}}")

    print(f"\nExample JSON structure:")
    example_state = {
        "status": "HEDGE_ACTIVE",
        "activation_count": 2,
        "current_allocation": {
            "inverse_etp_pct": 3.5,
            "vix_etp_pct": 2.5,
            "bond_hedge_pct": 4.0,
            "cash_raised_pct": 0.0,
        },
        "cost_tracking": {
            "last_cost_pct": 0.0047,
            "cumulative_cost_30d": 0.0051,
            "hedge_pnl_ytd": 0.0234,
        },
    }
    print(json.dumps(example_state, indent=2))


# ============================================================================
# MAIN RUNNER
# ============================================================================

if __name__ == "__main__":
    print("\n" + "="*80)
    print("BOOK 42 — CONDITIONAL HEDGING: EXAMPLE USAGE")
    print("="*80)

    example_normal_market()
    example_mild_stress()
    example_crisis()
    example_confidence_overlay()
    example_detector_raw()
    example_cost_analysis()
    example_state_persistence()

    print("\n" + "="*80)
    print("EXAMPLES COMPLETE")
    print("="*80)
    print("\nFor integration into bridge.py, see:")
    print("  - HEDGE_INTEGRATION_GUIDE.md")
    print("  - BRIDGE_PY_PATCH.txt")
    print("  - HEDGE_THRESHOLDS_CHEATSHEET.txt")
