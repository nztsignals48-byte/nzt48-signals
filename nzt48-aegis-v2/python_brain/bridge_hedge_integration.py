"""Book 42 Integration Layer — Wires HedgeDetector & DynamicHedgeAllocator into bridge.py signal flow.

This module provides drop-in replacement functions for bridge.py's hedge logic:
  - _monitor_hedge_signals_v2(): Enhanced signal detection
  - _calculate_hedge_allocation_v2(): Dynamic sizing with cost controls
  - _generate_hedge_signals_v2(): Create hedge order signals
  - _track_hedge_effectiveness_v2(): Monitor hedge P&L

Call from bridge.py's main signal processing loop (process_tick).
"""

import json
import math
import os
import sys
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

from python_brain.hedge_detector import (
    HedgeDetector,
    DynamicHedgeAllocator,
    IBKRHedgePricer,
    StressSignals,
    get_hedge_detector,
    get_hedge_allocator,
    get_hedge_pricer,
)


# ============================================================================
# GLOBAL STATE
# ============================================================================

_hedge_state = {
    "status": "INACTIVE",  # INACTIVE, MONITORING, HEDGE_ACTIVE, KILLING
    "activation_count": 0,
    "signal_fire_times": {},
    "consecutive_days_clear": 0,
    "last_clear_date": None,
    "current_allocation": {
        "inverse_etp_pct": 0.0,
        "vix_etp_pct": 0.0,
        "bond_hedge_pct": 0.0,
        "cash_raised_pct": 0.0,
    },
    "cost_tracking": {
        "last_cost_pct": 0.0,
        "cumulative_cost_30d": 0.0,
        "hedge_pnl_ytd": 0.0,
    },
}

_hedge_cost_history = deque(maxlen=30)  # 30-day rolling costs
_hedge_pnl_history = deque(maxlen=90)  # 90-day P&L tracking


# ============================================================================
# PERSISTENCE
# ============================================================================

def _load_hedge_state():
    """Load persisted hedge state from /app/data/hedge_state.json."""
    global _hedge_state
    try:
        state_path = "/app/data/hedge_state.json"
        if os.path.exists(state_path):
            with open(state_path) as f:
                persisted = json.load(f)
            _hedge_state.update(persisted)
            sys.stderr.write(
                f"HEDGE_LOAD_V2: status={_hedge_state['status']} "
                f"activation_count={_hedge_state['activation_count']} "
                f"cumulative_cost={_hedge_state.get('cost_tracking', {}).get('cumulative_cost_30d', 0)*100:.2f}%\n"
            )
            sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"HEDGE_LOAD_V2: failed to load state: {e} (non-fatal)\n")
        sys.stderr.flush()


def _save_hedge_state():
    """Persist hedge state to /app/data/hedge_state.json."""
    try:
        state_path = "/app/data/hedge_state.json"
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w") as f:
            json.dump(_hedge_state, f, indent=2)
    except Exception as e:
        sys.stderr.write(f"HEDGE_SAVE_V2: failed to save state: {e}\n")
        sys.stderr.flush()


# ============================================================================
# SIGNAL DETECTION (V2)
# ============================================================================

def _monitor_hedge_signals_v2(msg: Dict) -> StressSignals:
    """
    Enhanced hedge signal monitoring using HedgeDetector.

    Monitors:
      1. VIX term structure: (VIX_1m - VIX_3m) / VIX_3m < -0.05 (backwardation)
      2. Credit spreads: HY OAS > 500 bps (crisis) or > ma20 + 100
      3. Market breadth: A/D < 1.5 (stress) or < 1.0 (crisis)

    Returns:
        StressSignals with current trigger state and count
    """
    detector = get_hedge_detector()
    signals = detector.update(msg)

    # Optional: Log signal state every 100 ticks
    if detector.signal_fire_count % 100 == 0:
        sys.stderr.write(
            f"HEDGE_SIGNALS_V2: triggers={signals.num_triggers} "
            f"vix_bw={signals.vix_backwardation} credit={signals.credit_widening} "
            f"breadth={signals.breadth_decline} crisis={signals.breadth_crisis}\n"
        )
        sys.stderr.flush()

    return signals


def _calculate_hedge_allocation_v2(signals: StressSignals, msg: Dict) -> Dict:
    """
    Calculate hedge allocation with dynamic sizing and cost controls.

    Returns:
        {
            "hedge_pct": float,
            "inverse_etp_pct": float,
            "vix_etp_pct": float,
            "bond_hedge_pct": float,
            "cash_raise_pct": float,
            "spy_puts_cost_pct": float,
            "total_cost_pct": float,
            "should_hedge": bool,
            "reason": str,
        }
    """
    detector = get_hedge_detector()
    allocator = get_hedge_allocator()

    alloc = allocator.calculate(signals, msg, detector)

    return {
        "hedge_pct": alloc.hedge_pct,
        "inverse_etp_pct": alloc.inverse_etp_pct,
        "vix_etp_pct": alloc.vix_etp_pct,
        "bond_hedge_pct": alloc.bond_hedge_pct,
        "cash_raise_pct": alloc.cash_raise_pct,
        "spy_puts_cost_pct": alloc.spy_puts_cost_pct,
        "total_cost_pct": alloc.total_cost_pct,
        "should_hedge": alloc.should_hedge,
        "reason": alloc.reason,
    }


def _generate_hedge_signals_v2(msg: Dict, allocation: Dict, signals: StressSignals) -> List[Dict]:
    """
    Generate hedge order signals based on allocation.

    Creates separate signals for:
      1. Inverse ETP positions (SH, PSQ, 3USL, 3QQL)
      2. VIX ETP positions (UVXY, VIXY)
      3. Bond hedge (TLT long)
      4. Cash raise (flatten long positions)

    Returns:
        List of hedge signals ready for Rust engine
    """
    ticker_id = msg.get("ticker_id", 0)
    timestamp_ns = msg.get("timestamp_ns", 0)
    hedge_signals = []

    # ── Inverse ETP Signal ──
    if allocation["inverse_etp_pct"] > 0:
        hedge_signals.append({
            "type": "signal",
            "ticker_id": ticker_id,
            "direction": "Long",  # Buy inverse ETP (shorts market)
            "confidence": 55 + (signals.num_triggers * 12),  # Scales with signal count
            "kelly_fraction": 0.03,  # Conservative hedge sizing
            "shares": 0,  # Rust-side sizing
            "strategy": "HEDGE_InverseETP",
            "book_reference": 42,
            "hedge_pct_allocation": allocation["inverse_etp_pct"],
            "hedge_instruments": ["SH", "PSQ", "3USL", "3QQL"],
            "reason": allocation["reason"],
            "timestamp_ns": timestamp_ns,
        })

    # ── VIX ETP Signal ──
    if allocation["vix_etp_pct"] > 0:
        hedge_signals.append({
            "type": "signal",
            "ticker_id": ticker_id,
            "direction": "Long",  # VIX long (portfolio insurance)
            "confidence": 52 + (signals.num_triggers * 10),
            "kelly_fraction": 0.02,  # Small VIX position
            "shares": 0,
            "strategy": "HEDGE_VIXAllocation",
            "book_reference": 42,
            "hedge_pct_allocation": allocation["vix_etp_pct"],
            "hedge_instruments": ["UVXY", "VIXY"],
            "reason": allocation["reason"],
            "timestamp_ns": timestamp_ns,
        })

    # ── Bond Hedge Signal (TLT long if rates rising) ──
    if allocation["bond_hedge_pct"] > 0:
        hedge_signals.append({
            "type": "signal",
            "ticker_id": ticker_id,
            "direction": "Long",
            "confidence": 50 + (signals.num_triggers * 8),
            "kelly_fraction": 0.02,
            "shares": 0,
            "strategy": "HEDGE_BondDuration",
            "book_reference": 42,
            "hedge_pct_allocation": allocation["bond_hedge_pct"],
            "hedge_instruments": ["TLT"],
            "reason": "Long duration hedge if credit widens" if allocation["bond_hedge_pct"] > 0 else "",
            "timestamp_ns": timestamp_ns,
        })

    # ── Cash Raise Signal ──
    if allocation["cash_raise_pct"] > 0:
        hedge_signals.append({
            "type": "signal",
            "ticker_id": ticker_id,
            "direction": "Flat",  # Special: flatten positions
            "confidence": 65 + (signals.num_triggers * 12),
            "kelly_fraction": 0.0,
            "shares": 0,
            "strategy": "HEDGE_CashRaise",
            "book_reference": 42,
            "hedge_pct_allocation": allocation["cash_raise_pct"],
            "reason": f"Raise {allocation['cash_raise_pct']*100:.0f}% to cash (kill switch)" if signals.breadth_crisis else allocation["reason"],
            "timestamp_ns": timestamp_ns,
        })

    return hedge_signals


# ============================================================================
# HEDGE EFFECTIVENESS TRACKING
# ============================================================================

def _track_hedge_effectiveness_v2(hedge_pnl: float, allocation: Dict) -> None:
    """
    Track hedge P&L and effectiveness.

    Args:
        hedge_pnl: P&L on hedge positions (as % of notional)
        allocation: Current allocation dict
    """
    global _hedge_cost_history, _hedge_pnl_history

    total_cost = allocation.get("total_cost_pct", 0)
    _hedge_cost_history.append(total_cost)
    _hedge_pnl_history.append(hedge_pnl)

    # Update persistent state
    _hedge_state["cost_tracking"]["last_cost_pct"] = total_cost
    _hedge_state["cost_tracking"]["cumulative_cost_30d"] = (
        sum(_hedge_cost_history) / len(_hedge_cost_history) if _hedge_cost_history else 0
    )
    _hedge_state["cost_tracking"]["hedge_pnl_ytd"] = (
        sum(_hedge_pnl_history) if _hedge_pnl_history else 0
    )

    # Alert if costs exceed 1% per month
    if _hedge_state["cost_tracking"]["cumulative_cost_30d"] > 0.01:
        sys.stderr.write(
            f"HEDGE_COST_ALERT: cumulative 30d cost {_hedge_state['cost_tracking']['cumulative_cost_30d']*100:.2f}% "
            f"exceeds 1% threshold (hedge_pnl_ytd={_hedge_state['cost_tracking']['hedge_pnl_ytd']*100:.2f}%)\n"
        )
        sys.stderr.flush()

    _save_hedge_state()


# ============================================================================
# BRIDGE.PY INTEGRATION POINT
# ============================================================================

def apply_conditional_hedging_v2(msg: Dict, all_signals: List[Dict]) -> List[Dict]:
    """
    Enhanced conditional hedging integration for bridge.py.

    Called from process_tick() after signal generation but before selection.

    Usage in bridge.py:
        # Around line 4661, replace:
        # hedge_sigs = _apply_conditional_hedge(msg, all_signals)
        # with:
        # hedge_sigs = apply_conditional_hedging_v2(msg, all_signals)

    Args:
        msg: Tick message with market data
        all_signals: Generated signals from strategies

    Returns:
        List of hedge signals to append to all_signals
    """
    global _hedge_state

    # Load initial state once per session
    if _hedge_state["status"] == "INACTIVE" and not os.path.exists("/app/data/hedge_state.json"):
        _load_hedge_state()

    try:
        # Step 1: Detect stress signals
        signals = _monitor_hedge_signals_v2(msg)

        # Step 2: Calculate allocation
        allocation = _calculate_hedge_allocation_v2(signals, msg)

        # Step 3: Update state
        if allocation["should_hedge"]:
            _hedge_state["status"] = "HEDGE_ACTIVE" if signals.num_triggers <= 2 else "KILLING"
            _hedge_state["activation_count"] = signals.num_triggers
        else:
            # Check if should unwind
            if _hedge_state["status"] != "INACTIVE" and signals.num_triggers == 0:
                from datetime import datetime, timezone
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if today != _hedge_state.get("last_clear_date"):
                    _hedge_state["last_clear_date"] = today
                    _hedge_state["consecutive_days_clear"] += 1
                else:
                    pass

                if _hedge_state["consecutive_days_clear"] >= 5:
                    _hedge_state["status"] = "INACTIVE"
                    _hedge_state["activation_count"] = 0
                    _hedge_state["consecutive_days_clear"] = 0
                    _hedge_state["current_allocation"] = {
                        "inverse_etp_pct": 0,
                        "vix_etp_pct": 0,
                        "bond_hedge_pct": 0,
                        "cash_raised_pct": 0,
                    }
                    _save_hedge_state()
                    return []

        # Step 4: Generate hedge signals
        if allocation["should_hedge"]:
            hedge_signals = _generate_hedge_signals_v2(msg, allocation, signals)

            # Step 5: Update allocation tracking
            _hedge_state["current_allocation"] = {
                "inverse_etp_pct": allocation["inverse_etp_pct"],
                "vix_etp_pct": allocation["vix_etp_pct"],
                "bond_hedge_pct": allocation["bond_hedge_pct"],
                "cash_raised_pct": allocation["cash_raise_pct"],
            }
            _save_hedge_state()

            # Log activation
            sys.stderr.write(
                f"HEDGE_ACTIVATED_V2: {allocation['reason']} | "
                f"cost={allocation['total_cost_pct']*100:.2f}% | "
                f"signals_generated={len(hedge_signals)}\n"
            )
            sys.stderr.flush()

            return hedge_signals
        else:
            return []

    except Exception as e:
        sys.stderr.write(f"HEDGE_ERROR_V2: {e}\n")
        sys.stderr.flush()
        return []


# ============================================================================
# HEDGE CONFIDENCE OVERLAY (Enhanced)
# ============================================================================

def apply_hedge_confidence_overlay_v2(best_signal: Dict, msg: Dict) -> Dict:
    """
    Reduce confidence for long signals when hedge is active.

    Enhanced logic:
      - INACTIVE: no adjustment
      - MONITORING: no adjustment
      - HEDGE_ACTIVE: reduce long confidence by 12 points, boost short by 8
      - KILLING: reduce long confidence by 28 points, boost short by 18

    Args:
        best_signal: Best signal to adjust
        msg: Tick message

    Returns:
        Adjusted signal
    """
    if best_signal is None:
        return best_signal

    direction = best_signal.get("direction", "").lower()
    hedge_status = _hedge_state.get("status", "INACTIVE")
    allocation = _hedge_state.get("current_allocation", {})

    if hedge_status == "INACTIVE":
        return best_signal

    # Check if this is already a hedge signal (skip adjustment)
    if best_signal.get("strategy", "").startswith("HEDGE_"):
        return best_signal

    hedge_pct = (
        allocation.get("inverse_etp_pct", 0)
        + allocation.get("vix_etp_pct", 0)
        + allocation.get("cash_raised_pct", 0)
    )

    if direction == "long":
        if hedge_status == "KILLING":
            reduction = 28
            best_signal["confidence"] = max(0, best_signal.get("confidence", 50) - reduction)
            sys.stderr.write(
                f"HEDGE_OVERLAY_V2: long signal confidence -{reduction} pts (kill switch) "
                f"→ {best_signal['confidence']}\n"
            )
        else:  # HEDGE_ACTIVE
            reduction = 12
            best_signal["confidence"] = max(0, best_signal.get("confidence", 50) - reduction)
            sys.stderr.write(
                f"HEDGE_OVERLAY_V2: long signal confidence -{reduction} pts (hedge {hedge_pct*100:.0f}%) "
                f"→ {best_signal['confidence']}\n"
            )
        sys.stderr.flush()

    elif direction == "short":
        if hedge_status == "KILLING":
            boost = 18
            best_signal["confidence"] = min(100, best_signal.get("confidence", 50) + boost)
            sys.stderr.write(
                f"HEDGE_OVERLAY_V2: short signal confidence +{boost} pts (kill switch) "
                f"→ {best_signal['confidence']}\n"
            )
        else:  # HEDGE_ACTIVE
            boost = 8
            best_signal["confidence"] = min(100, best_signal.get("confidence", 50) + boost)
            sys.stderr.write(
                f"HEDGE_OVERLAY_V2: short signal confidence +{boost} pts (hedge active) "
                f"→ {best_signal['confidence']}\n"
            )
        sys.stderr.flush()

    return best_signal
