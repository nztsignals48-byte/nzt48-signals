"""Book 42 — Conditional Hedging: HedgeDetector & DynamicHedgeAllocator.

Advanced hedge activation based on 3-signal stress detection:
  1. VIX Term Structure: (VIX_1m - VIX_3m) / VIX_3m
     - Backwardation (< -0.05) signals market stress
  2. Credit Spread: HY OAS (basis points)
     - Normal: < 400 bps
     - Stressed: 400-500 bps
     - Crisis: > 600 bps
  3. Market Breadth: Advance/Decline ratio
     - Normal: > 1.5
     - Stressed: 1.0-1.5
     - Crisis: < 1.0

Allocation Rules:
  - 3 triggers = 15% hedge allocation (long_notional - hedge_notional) / long_notional
  - 2 triggers = 10% hedge allocation
  - 1 trigger = 5% hedge allocation
  - 0 triggers = 0% hedge allocation (full long exposure)

Hedge Instruments:
  - Equity hedging: SPY puts (20 delta, 45 DTE)
  - Leverage hedging: 3USL/3QQL inverse positions
  - Bond hedging: TLT long (if rates rising)
  - Cost/benefit: Skip if VIX premium < 0.5% of notional

Cost Limits:
  - Don't hedge if credit spread in normal range (< 400 bps)
  - Skip hedge if VIX put cost > 0.5% monthly (~0.017% daily)
  - Track hedge P&L separately
  - If cumulative hedge cost > 1% per month, reduce allocation by 20%
"""

import json
import math
import os
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ============================================================================
# HEDGE DETECTOR: Monitor 3 stress signals
# ============================================================================

@dataclass
class StressSignals:
    """Current stress signal states."""
    vix_backwardation: bool  # (VIX_1m - VIX_3m) / VIX_3m < -0.05
    credit_widening: bool    # HY OAS > 500 bps or elevated vs MA
    breadth_decline: bool    # A/D ratio < 1.5
    breadth_crisis: bool     # A/D ratio < 1.0 (kill switch)
    num_triggers: int        # Count of active triggers (0-3)
    timestamp_s: float       # When detected


class HedgeDetector:
    """Monitor VIX term structure, credit spreads, and breadth for stress signals."""

    def __init__(self, max_history: int = 60):
        """Initialize detector with rolling windows."""
        self.max_history = max_history

        # VIX term structure
        self.vix_spot_history = deque(maxlen=max_history)
        self.vix_1m_future_history = deque(maxlen=max_history)
        self.vix_3m_future_history = deque(maxlen=max_history)

        # Credit spreads (HY OAS in basis points)
        self.credit_spread_history = deque(maxlen=max_history)
        self.credit_spread_ma20 = 350.0  # Initialize to normal

        # Market breadth (A/D ratio)
        self.breadth_ratio_history = deque(maxlen=max_history)
        self.breadth_ratio_ma10 = 1.5  # Initialize to normal

        # Stress signal tracking
        self.last_signals = None
        self.signal_fire_count = 0
        self.consecutive_clear_periods = 0

    def update(self, msg: Dict) -> StressSignals:
        """Update detector with new market data. Returns current stress signals."""
        timestamp_s = msg.get("timestamp_ns", 0) / 1_000_000_000 if msg.get("timestamp_ns", 0) > 0 else time.time()

        # ── Signal 1: VIX Backwardation ──
        vix_backwardation = self._check_vix_backwardation(msg)

        # ── Signal 2: Credit Spread Widening ──
        credit_widening = self._check_credit_widening(msg)

        # ── Signal 3: Market Breadth Declining ──
        breadth_decline, breadth_crisis = self._check_breadth_decline(msg)

        # Aggregate signals
        num_triggers = sum([vix_backwardation, credit_widening, breadth_decline])

        signals = StressSignals(
            vix_backwardation=vix_backwardation,
            credit_widening=credit_widening,
            breadth_decline=breadth_decline,
            breadth_crisis=breadth_crisis,
            num_triggers=num_triggers,
            timestamp_s=timestamp_s,
        )

        self.last_signals = signals

        # Track consecutive clear periods (for unwinding)
        if num_triggers == 0 and not breadth_crisis:
            self.consecutive_clear_periods += 1
        else:
            self.consecutive_clear_periods = 0

        if num_triggers > 0:
            self.signal_fire_count += 1

        return signals

    def _check_vix_backwardation(self, msg: Dict) -> bool:
        """
        VIX Backwardation: (VIX_1m - VIX_3m) / VIX_3m < -0.05 (inverted term structure).

        Interpretation:
          - Normal (contango): VIX futures curve slopes upward
          - Backwardation: VIX futures curve inverted (spot > future or flat)
          - Signal stress when 1m future < 3m future (negative roll yield)

        Returns True if backwardated (stress signal).
        """
        vix_spot = msg.get("vix_spot")
        vix_1m = msg.get("vix_1m_future")
        vix_3m = msg.get("vix_3m_future")

        if vix_spot is not None and vix_spot > 0:
            self.vix_spot_history.append(vix_spot)
        if vix_1m is not None and vix_1m > 0:
            self.vix_1m_future_history.append(vix_1m)
        if vix_3m is not None and vix_3m > 0:
            self.vix_3m_future_history.append(vix_3m)

        # Calculate term structure ratio: (1m - 3m) / 3m
        if vix_1m is not None and vix_3m is not None and vix_3m > 0:
            ts_ratio = (vix_1m - vix_3m) / vix_3m
            # Backwardation: ratio < -0.05 (1m future 5% lower than 3m)
            return ts_ratio < -0.05

        return False

    def _check_credit_widening(self, msg: Dict) -> bool:
        """
        Credit Spread Widening: HY OAS > 500 bps or elevated vs 20-day MA.

        Interpretation:
          - Normal: < 400 bps (benign risk appetite)
          - Stressed: 400-500 bps (caution zone)
          - Crisis: > 600 bps (emergency hedging)

        Returns True if credit spread elevated (stress signal).
        """
        credit_spread = msg.get("credit_spread_bp")

        if credit_spread is not None and credit_spread > 0:
            self.credit_spread_history.append(credit_spread)

            # Update 20-day MA
            if len(self.credit_spread_history) >= 20:
                self.credit_spread_ma20 = sum(self.credit_spread_history) / min(20, len(self.credit_spread_history))

        # Stress threshold: spread > 500 bps OR > ma20 + 100 bps (elevated)
        if credit_spread is not None:
            return credit_spread > 500 or credit_spread > (self.credit_spread_ma20 + 100)

        return False

    def _check_breadth_decline(self, msg: Dict) -> Tuple[bool, bool]:
        """
        Market Breadth Declining: Advance/Decline ratio < 1.5 (stressed) or < 1.0 (crisis).

        Interpretation:
          - Normal: A/D > 1.5 (more advances than declines)
          - Stressed: 1.0-1.5 (broad weakness emerging)
          - Crisis: < 1.0 (breadth collapse, hedge urgently)

        Returns (breadth_decline, breadth_crisis).
        """
        breadth_ratio = msg.get("breadth_ad_ratio")

        if breadth_ratio is not None and breadth_ratio > 0:
            self.breadth_ratio_history.append(breadth_ratio)
            if len(self.breadth_ratio_history) >= 10:
                self.breadth_ratio_ma10 = sum(self.breadth_ratio_history) / min(10, len(self.breadth_ratio_history))

        if breadth_ratio is not None:
            crisis = breadth_ratio < 1.0
            decline = breadth_ratio < 1.5
            return decline, crisis

        return False, False


# ============================================================================
# DYNAMIC HEDGE ALLOCATOR: Size hedges based on trigger count
# ============================================================================

@dataclass
class HedgeAllocation:
    """Calculated hedge allocation and cost."""
    hedge_pct: float           # Total hedge allocation (5%, 10%, 15%)
    inverse_etp_pct: float     # % in inverse positions (3USL/3QQL, SH, PSQ)
    vix_etp_pct: float         # % in VIX ETPs (UVXY)
    bond_hedge_pct: float      # % in TLT (bond hedge)
    cash_raise_pct: float      # % to raise to cash
    spy_puts_cost_pct: float   # Estimated cost of SPY puts (20d, 45 DTE) as % of notional
    total_cost_pct: float      # Total estimated hedge cost (% annualized)
    should_hedge: bool         # True if cost/benefit favorable
    reason: str                # Explanation


class DynamicHedgeAllocator:
    """Size hedges dynamically based on stress signal count and cost thresholds."""

    # Thresholds
    CREDIT_NORMAL_THRESHOLD = 400  # bps
    CREDIT_STRESSED_THRESHOLD = 500  # bps
    CREDIT_CRISIS_THRESHOLD = 600  # bps

    BREADTH_NORMAL_THRESHOLD = 1.5
    BREADTH_STRESSED_THRESHOLD = 1.0

    # Allocation matrix by trigger count
    ALLOCATIONS = {
        0: (0.00, 0.0, 0.0, 0.0, 0.0),      # No triggers: no hedge
        1: (0.05, 2.0, 1.5, 1.5, 0.0),      # 1 trigger: 5% hedge
        2: (0.10, 3.5, 2.5, 4.0, 0.0),      # 2 triggers: 10% hedge
        3: (0.15, 5.0, 3.5, 6.5, 0.0),      # 3 triggers: 15% hedge (omit cash raise)
    }

    # Kill switch (breadth crisis): max hedge + cash raise
    KILL_SWITCH_ALLOCATIONS = (0.20, 8.0, 5.0, 0.0, 15.0)  # 20% total, raise 15% cash

    # SPY put cost models (approximated from IV surface)
    # 20 delta, 45 DTE: approximately 0.25-0.35% of notional per month
    SPY_PUT_COST_MONTHLY = 0.003  # 0.3% per month = 0.01% daily
    SPY_PUT_COST_MIN_THRESHOLD = 0.005  # 0.5% notional (skip if cheaper than this)

    # Cost limit: if cumulative hedge cost > 1% per month, reduce by 20%
    MAX_HEDGE_COST_MONTHLY = 0.01  # 1% per month

    def __init__(self):
        """Initialize allocator with cost tracking."""
        self.hedge_cost_history = deque(maxlen=30)  # 30-day rolling cost
        self.cumulative_cost_pct = 0.0

    def calculate(self, signals: StressSignals, msg: Dict, detector: HedgeDetector) -> HedgeAllocation:
        """
        Calculate hedge allocation based on stress signals and cost constraints.

        Args:
            signals: Current stress signals from HedgeDetector
            msg: Market data message (contains VIX, equity values)
            detector: HedgeDetector instance for historical data

        Returns:
            HedgeAllocation with sizing and cost analysis
        """
        # Get base allocation from trigger count
        if signals.breadth_crisis:
            hedge_pct, inv_pct, vix_pct, bond_pct, cash_pct = self.KILL_SWITCH_ALLOCATIONS
            reason = f"BREADTH_CRISIS (A/D < 1.0): max hedge + 15% cash raise"
        else:
            num = signals.num_triggers
            hedge_pct, inv_pct, vix_pct, bond_pct, cash_pct = self.ALLOCATIONS.get(num, (0, 0, 0, 0, 0))
            reason = f"{num} triggers: {hedge_pct*100:.0f}% hedge allocation"

        # ── Cost constraint 1: VIX premium ──
        vix_level = msg.get("vix", 20)
        spy_put_cost_pct = self._estimate_spy_put_cost(vix_level)

        # If VIX premium too low (< 0.5% notional), reduce VIX ETP allocation
        if spy_put_cost_pct < self.SPY_PUT_COST_MIN_THRESHOLD:
            vix_pct = max(0, vix_pct * 0.5)
            reason += "; VIX premium low, halving VIX ETP"

        # ── Cost constraint 2: Credit spread in normal range ──
        credit_spread = msg.get("credit_spread_bp", 350)
        if credit_spread < self.CREDIT_STRESSED_THRESHOLD and signals.num_triggers == 1:
            # Only 1 trigger + normal credit = weak signal, reduce allocation
            hedge_pct = max(0, hedge_pct * 0.7)
            inv_pct = max(0, inv_pct * 0.7)
            reason += "; only 1 trigger + normal credit, reducing"

        # ── Cost constraint 3: Cumulative hedge cost limit ──
        total_cost = spy_put_cost_pct + (inv_pct * 0.002) + (vix_pct * 0.005) + (bond_pct * 0.001)
        self.hedge_cost_history.append(total_cost)
        self.cumulative_cost_pct = sum(self.hedge_cost_history) / len(self.hedge_cost_history) if self.hedge_cost_history else 0

        if self.cumulative_cost_pct > self.MAX_HEDGE_COST_MONTHLY:
            reduction_factor = 0.8  # Reduce by 20%
            hedge_pct *= reduction_factor
            inv_pct *= reduction_factor
            vix_pct *= reduction_factor
            reason += f"; cumulative cost {self.cumulative_cost_pct*100:.2f}% > 1%, reducing"

        # ── Final decision ──
        should_hedge = hedge_pct > 0 and (
            signals.num_triggers >= 2 or
            (signals.num_triggers == 1 and credit_spread >= self.CREDIT_STRESSED_THRESHOLD)
        )

        return HedgeAllocation(
            hedge_pct=hedge_pct,
            inverse_etp_pct=inv_pct,
            vix_etp_pct=vix_pct,
            bond_hedge_pct=bond_pct,
            cash_raise_pct=cash_pct,
            spy_puts_cost_pct=spy_put_cost_pct,
            total_cost_pct=total_cost,
            should_hedge=should_hedge,
            reason=reason,
        )

    def _estimate_spy_put_cost(self, vix_level: float) -> float:
        """
        Estimate cost of SPY puts (20 delta, 45 DTE) based on VIX level.

        IV skew model:
          - VIX < 15: 0.15% monthly
          - VIX 15-20: 0.25% monthly
          - VIX 20-30: 0.4% monthly
          - VIX > 30: 0.7% monthly

        Args:
            vix_level: Current VIX level

        Returns:
            Estimated cost as % of notional (annualized)
        """
        if vix_level < 15:
            monthly_cost = 0.0015
        elif vix_level < 20:
            monthly_cost = 0.0025
        elif vix_level < 30:
            monthly_cost = 0.004
        else:
            monthly_cost = 0.007

        # Return annualized (multiply by 12)
        return monthly_cost * 12

    def track_hedge_pnl(self, pnl_pct: float) -> None:
        """Track hedge P&L for effectiveness monitoring."""
        if len(self.hedge_cost_history) > 0:
            self.cumulative_cost_pct -= pnl_pct  # Reduce cost if hedge profitable


# ============================================================================
# IBKR-INTEGRATED HEDGE PRICING
# ============================================================================

class IBKRHedgePricer:
    """Price hedge instruments using IBKR data (lazy-loaded)."""

    def __init__(self):
        """Initialize with lazy IBKR connection."""
        self._provider = None
        self._last_refresh_time = 0
        self._refresh_interval_sec = 300  # Refresh every 5 minutes

    def _get_provider(self):
        """Lazy-load IBKR data provider (fail-safe)."""
        if self._provider is None:
            try:
                from python_brain.ouroboros.ibkr_data_provider import get_provider
                self._provider = get_provider()
            except ImportError:
                sys.stderr.write("HEDGE_PRICER: IBKR data provider not available (non-fatal)\n")
                sys.stderr.flush()
                return None
        return self._provider

    def price_spy_puts(self, vix_level: float) -> Optional[Dict]:
        """
        Price SPY puts (20 delta, 45 DTE) via IBKR.

        Returns:
            {
                "strike": float,
                "bid": float,
                "ask": float,
                "implied_vol": float,
                "delta": float,
                "cost_pct_notional": float,
            }
        """
        provider = self._get_provider()
        if not provider:
            return None

        try:
            # Fetch SPY market data
            spy_data = provider.get_contract_details("SPY")
            if not spy_data or not spy_data.get("bid"):
                return None

            spy_price = spy_data["bid"]

            # Estimate 20 delta put strike: ~1 std dev below current
            implied_vol = self._estimate_iv_from_vix(vix_level)
            days_to_exp = 45
            strike = spy_price * (1 - (implied_vol * math.sqrt(days_to_exp / 252)))

            # Rough IV surface: 20 delta put costs 0.25-0.4% depending on VIX
            put_cost_pct = self._estimate_put_cost(vix_level, implied_vol)

            return {
                "strike": strike,
                "bid": spy_price * (put_cost_pct * 0.95),
                "ask": spy_price * (put_cost_pct * 1.05),
                "implied_vol": implied_vol,
                "delta": -0.20,
                "cost_pct_notional": put_cost_pct,
            }
        except Exception as e:
            sys.stderr.write(f"HEDGE_PRICER: failed to price SPY puts: {e}\n")
            sys.stderr.flush()
            return None

    def price_inverse_etps(self, ticker: str = "SH") -> Optional[Dict]:
        """
        Price inverse ETPs (SH, PSQ, 3USL, 3QQL).

        Returns market price and estimated spread.
        """
        provider = self._get_provider()
        if not provider:
            return None

        try:
            data = provider.get_contract_details(ticker)
            if not data:
                return None

            bid = data.get("bid", 0)
            ask = data.get("ask", 0)
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0

            if mid <= 0:
                return None

            return {
                "ticker": ticker,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread_bps": ((ask - bid) / mid * 10000) if mid > 0 else 0,
            }
        except Exception as e:
            sys.stderr.write(f"HEDGE_PRICER: failed to price {ticker}: {e}\n")
            sys.stderr.flush()
            return None

    def price_tlt_bond_hedge(self) -> Optional[Dict]:
        """
        Price TLT (20+ year Treasury ETF) for bond hedging.

        Used when rates are rising (duration extension hedge).
        """
        provider = self._get_provider()
        if not provider:
            return None

        try:
            data = provider.get_contract_details("TLT")
            if not data:
                return None

            return {
                "ticker": "TLT",
                "price": (data.get("bid", 0) + data.get("ask", 0)) / 2,
                "bid": data.get("bid", 0),
                "ask": data.get("ask", 0),
            }
        except Exception as e:
            sys.stderr.write(f"HEDGE_PRICER: failed to price TLT: {e}\n")
            sys.stderr.flush()
            return None

    @staticmethod
    def _estimate_iv_from_vix(vix_level: float) -> float:
        """Rough proxy: IV ≈ VIX / 100 (simplified Black-Scholes)."""
        return max(0.08, min(0.80, vix_level / 100))

    @staticmethod
    def _estimate_put_cost(vix_level: float, iv: float) -> float:
        """Estimate put cost as % notional (Vega approximation)."""
        # Rough: put vega ≈ 0.04 per point IV, 45 DTE
        base_cost = iv * 0.04 * 45  # Vega * IV * days
        return max(0.001, min(0.050, base_cost))


def get_hedge_detector() -> HedgeDetector:
    """Singleton HedgeDetector for bridge.py."""
    global _hedge_detector_instance
    if "_hedge_detector_instance" not in globals():
        _hedge_detector_instance = HedgeDetector()
    return _hedge_detector_instance


def get_hedge_allocator() -> DynamicHedgeAllocator:
    """Singleton DynamicHedgeAllocator for bridge.py."""
    global _hedge_allocator_instance
    if "_hedge_allocator_instance" not in globals():
        _hedge_allocator_instance = DynamicHedgeAllocator()
    return _hedge_allocator_instance


def get_hedge_pricer() -> IBKRHedgePricer:
    """Singleton IBKRHedgePricer for bridge.py."""
    global _hedge_pricer_instance
    if "_hedge_pricer_instance" not in globals():
        _hedge_pricer_instance = IBKRHedgePricer()
    return _hedge_pricer_instance
