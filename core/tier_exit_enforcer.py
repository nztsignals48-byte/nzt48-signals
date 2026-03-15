"""
Tier-Based Exit Enforcement
===========================

Enforces session discipline: Tier 3 (extreme volatility) positions MUST
close before market close to prevent overnight gaps and avoid tail risks.

TIER 3 RULES:
- No overnight holds under any circumstances
- Exit trigger: 5 minutes before market close
- Position must be fully liquidated or converted to stop-protected hold
- Alerts: 15 min before close (warning), 5 min before close (critical)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("nzt48.core.tier_exit_enforcer")

UTC = ZoneInfo("UTC")


class ExitReason(Enum):
    """Reason for exit enforcement."""
    TIER3_PRE_CLOSE = "tier3_pre_close"
    TIER3_OVERNIGHT_PROTECTION = "tier3_overnight"
    SESSION_BOUNDARY = "session_boundary"
    FIFTY_PERCENT_RALLY = "fifty_percent_rally"  # NEW: 50% rally detected
    CHANDELIER_HIT = "chandelier_hit"  # Stop-loss from Chandelier trailing


@dataclass
class ExitInstruction:
    """Instruction to exit a position."""
    ticker: str
    tier: int
    current_price: float
    position_size_shares: float
    reason: ExitReason
    urgency: str  # "warning" (15min), "critical" (5min), "immediate"
    time_detected: datetime
    market_close_time: datetime
    message: str
    exit_price: float = None  # Specific exit price (for limit/target exits)
    remaining_qty: float = None  # Qty to hold after partial exit (NEW)
    carry_over_stop: float = None  # Stop for remaining position (NEW)


@dataclass
class RallyExit:
    """Record of 50% rally exit."""
    trade_id: str
    ticker: str
    entry_price: float
    exit_price: float
    rally_pct: float  # e.g., 50.0
    exit_qty: float  # Amount exited
    remaining_qty: float  # Amount carried forward
    remaining_stop: float  # Adaptive stop for remaining
    created_at: datetime = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(UTC)


class SessionExitEnforcer:
    """Enforces mandatory exits based on tier and session timing."""

    def __init__(self):
        """Initialize exit enforcer with timing constants."""
        # Exit timing thresholds
        self.tier3_warning_threshold = timedelta(minutes=15)  # Alert 15 min before
        self.tier3_critical_threshold = timedelta(minutes=5)  # Force exit 5 min before
        self.tier3_immediate_threshold = timedelta(minutes=1)  # Last-second emergency

        # Rally exit settings
        self.rally_threshold = 0.50  # 50% rally
        self.rally_exit_pct = 1.25  # Sell 125% of initial position
        self.rally_carry_pct = 0.25  # Keep 25% for next day

    def check_tier3_exit_time(
        self,
        ticker: str,
        tier: int,
        current_time: datetime,
        session_close_time: datetime,
        current_price: float,
        position_size_shares: float,
    ) -> Optional[ExitInstruction]:
        """
        Check if Tier 3 position should be exited based on session timing.

        Returns ExitInstruction if exit is required, None otherwise.

        Tier 3 exit rules:
        - 15 min before close: Warning alert
        - 5 min before close: Critical alert + force exit
        - After market close: Emergency liquidation
        """
        if tier != 3:
            return None

        time_until_close = session_close_time - current_time

        # Critical exit (5 min before close) - MUST exit now
        if time_until_close <= self.tier3_critical_threshold and time_until_close > timedelta(0):
            return ExitInstruction(
                ticker=ticker,
                tier=tier,
                current_price=current_price,
                position_size_shares=position_size_shares,
                reason=ExitReason.TIER3_PRE_CLOSE,
                urgency="critical",
                time_detected=current_time,
                market_close_time=session_close_time,
                message=f"TIER 3 CRITICAL EXIT: {ticker} at {current_price:.2f} ({time_until_close.total_seconds():.0f}s to close)"
            )

        # Warning exit (15 min before close) - Alert and prepare
        elif time_until_close <= self.tier3_warning_threshold and time_until_close > self.tier3_critical_threshold:
            return ExitInstruction(
                ticker=ticker,
                tier=tier,
                current_price=current_price,
                position_size_shares=position_size_shares,
                reason=ExitReason.TIER3_PRE_CLOSE,
                urgency="warning",
                time_detected=current_time,
                market_close_time=session_close_time,
                message=f"TIER 3 EXIT WARNING: {ticker} - {(time_until_close.total_seconds()/60):.0f} min to close"
            )

        # After market close - emergency
        elif time_until_close <= timedelta(0):
            return ExitInstruction(
                ticker=ticker,
                tier=tier,
                current_price=current_price,
                position_size_shares=position_size_shares,
                reason=ExitReason.TIER3_OVERNIGHT_PROTECTION,
                urgency="immediate",
                time_detected=current_time,
                market_close_time=session_close_time,
                message=f"TIER 3 EMERGENCY: {ticker} STILL OPEN AFTER MARKET CLOSE - liquidate immediately!"
            )

        return None

    def evaluate_fifty_percent_rally(
        self,
        trade_id: str,
        ticker: str,
        entry_price: float,
        current_price: float,
        position_size_shares: float,
        adaptive_stop_price: float = None,
    ) -> Optional[RallyExit]:
        """
        Detect and handle 50% rally exits with partial position carry-over.

        Logic:
        - If unrealized profit >= 50%: Sell 125% of initial position
        - Exit price: entry_price × 1.25 (take full profit + 25% of gains)
        - Remaining: 25% with adaptive stop for next session
        - Alert: Telegram notification

        Args:
            trade_id: Internal trade ID
            ticker: Stock ticker
            entry_price: Entry price
            current_price: Current price
            position_size_shares: Initial position size
            adaptive_stop_price: Adaptive stop for remaining position (from Chandelier)

        Returns:
            RallyExit record if 50% rally detected, None otherwise
        """
        unrealized_pnl_pct = (current_price - entry_price) / entry_price

        if unrealized_pnl_pct < self.rally_threshold:
            return None

        # CASE: 50%+ rally detected
        logger.info(
            f"[{ticker}] 50% RALLY DETECTED! {unrealized_pnl_pct*100:.1f}% gain | "
            f"entry={entry_price:.2f} current={current_price:.2f}"
        )

        # Calculate exit amounts
        exit_qty = position_size_shares * self.rally_exit_pct  # 125% of initial
        exit_price = entry_price * 1.25  # Lock in full + 25% of gains
        remaining_qty = position_size_shares - exit_qty  # 25% carry-forward

        # Use adaptive stop if provided, else default to entry - 2%
        carry_stop = adaptive_stop_price if adaptive_stop_price else entry_price * 0.98

        rally_exit = RallyExit(
            trade_id=trade_id,
            ticker=ticker,
            entry_price=entry_price,
            exit_price=exit_price,
            rally_pct=unrealized_pnl_pct * 100,
            exit_qty=exit_qty,
            remaining_qty=remaining_qty,
            remaining_stop=carry_stop,
        )

        logger.info(
            f"[{ticker}] Rally exit plan: "
            f"exit {exit_qty:.0f} @ {exit_price:.2f}, "
            f"carry {remaining_qty:.0f} @ stop {carry_stop:.2f}"
        )

        return rally_exit

    def should_block_new_tier3_entry(
        self,
        current_time: datetime,
        session_close_time: datetime,
    ) -> bool:
        """
        Check if new Tier 3 entries should be blocked (too close to close).

        Block new entries within 30 minutes of session close to avoid
        entry-immediately-into-exit situations.
        """
        time_until_close = session_close_time - current_time
        return time_until_close <= timedelta(minutes=30)

    def validate_tier3_exit_compliance(
        self,
        open_positions: list,  # List of (ticker, tier, entry_time) tuples
        current_time: datetime,
        session_start_time: datetime,
    ) -> list:
        """
        Audit: check all open Tier 3 positions for overnight hold violations.

        Returns list of violating positions.
        """
        violations = []

        for ticker, tier, entry_time in open_positions:
            if tier != 3:
                continue

            # Check if position has been held longer than one session
            time_held = current_time - entry_time

            # If held overnight (>20 hours), flag violation
            if time_held > timedelta(hours=20):
                violations.append({
                    "ticker": ticker,
                    "tier": tier,
                    "entry_time": entry_time,
                    "time_held_hours": time_held.total_seconds() / 3600,
                    "violation": "Tier 3 overnight hold (>20 hours)"
                })

        return violations
