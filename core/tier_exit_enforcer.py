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


class SessionExitEnforcer:
    """Enforces mandatory exits based on tier and session timing."""

    def __init__(self):
        """Initialize exit enforcer with timing constants."""
        # Exit timing thresholds
        self.tier3_warning_threshold = timedelta(minutes=15)  # Alert 15 min before
        self.tier3_critical_threshold = timedelta(minutes=5)  # Force exit 5 min before
        self.tier3_immediate_threshold = timedelta(minutes=1)  # Last-second emergency

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
