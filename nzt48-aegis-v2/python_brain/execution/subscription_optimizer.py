"""IBKR Market Data Subscription Optimizer — Book 220.

IBKR limits market data to 100 simultaneous streaming lines.
This module optimizes allocation across 7 strategies + monitoring.

3-tier subscription system:
  Tier 1 (Permanent): Core instruments always subscribed (~30 slots)
  Tier 2 (Rotating): Session-aware rotation (~50 slots)
  Tier 3 (On-demand): Signal-triggered subscriptions (~20 slots)

Usage:
    from python_brain.execution.subscription_optimizer import (
        SubscriptionOptimizer, SubscriptionTier,
    )

    opt = SubscriptionOptimizer(max_slots=100)
    opt.allocate_session("US_OVERLAP")
    active = opt.get_active_subscriptions()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Set

log = logging.getLogger("subscription_optimizer")


class SubscriptionTier(Enum):
    PERMANENT = 1  # Always subscribed
    ROTATING = 2   # Session-dependent
    ON_DEMAND = 3  # Signal-triggered


@dataclass
class SubscriptionSlot:
    """A market data subscription slot."""
    ticker: str
    tier: SubscriptionTier
    priority: int = 50  # 0-100, higher = more important
    strategy: str = ""   # Which strategy needs this
    session: str = ""    # Which session this is for


# Tier 1: Permanent subscriptions (~30 slots)
TIER1_PERMANENT: List[str] = [
    # Index ETPs (always needed for regime detection)
    "3USL.L", "3USS.L", "QQQ3.L", "QQQS.L",
    # Core single-stock (highest liquidity)
    "NVD3.L", "TSL3.L", "AAP3.L", "MSF3.L", "GOO3.L", "AML3.L",
    # Inverse for hedging
    "NV3S.L", "TS3S.L",
    # VIX (always needed for regime)
    "VIXL.L",
    # Commodity
    "3LOI.L", "3LGD.L",
]

# Tier 2: Session-rotating subscriptions
TIER2_LSE_SESSION: List[str] = [
    "AMD3.L", "MET3.L", "GPT3.L", "3LAP.L", "3LMS.L",
    "MS23.L", "COI3.L", "TSM3.L",
    "3UKL.L", "3UKS.L",
]

TIER2_US_OVERLAP: List[str] = [
    # US single-stock (more active during overlap)
    "AMD3.L", "MET3.L", "GPT3.L", "TSM3.L",
    "MS23.L", "COI3.L",
    # Additional inverse for pairs
    "3SOI.L", "3SGD.L",
]


class SubscriptionOptimizer:
    """Manage IBKR market data subscription budget."""

    def __init__(self, max_slots: int = 100):
        self.max_slots = max_slots
        self._active: Dict[str, SubscriptionSlot] = {}
        self._on_demand_queue: List[str] = []

        # Initialize Tier 1 (permanent)
        for ticker in TIER1_PERMANENT:
            self._active[ticker] = SubscriptionSlot(
                ticker=ticker, tier=SubscriptionTier.PERMANENT,
                priority=90,
            )

    @property
    def slots_used(self) -> int:
        return len(self._active)

    @property
    def slots_available(self) -> int:
        return max(0, self.max_slots - self.slots_used)

    def allocate_session(self, session: str) -> List[str]:
        """Allocate Tier 2 subscriptions for a market session.

        Returns list of tickers added.
        """
        # Remove old Tier 2 subs
        to_remove = [t for t, s in self._active.items() if s.tier == SubscriptionTier.ROTATING]
        for t in to_remove:
            del self._active[t]

        # Add session-appropriate Tier 2
        tier2 = TIER2_US_OVERLAP if "US" in session else TIER2_LSE_SESSION
        added = []
        for ticker in tier2:
            if ticker not in self._active and self.slots_available > 0:
                self._active[ticker] = SubscriptionSlot(
                    ticker=ticker, tier=SubscriptionTier.ROTATING,
                    priority=60, session=session,
                )
                added.append(ticker)

        log.info("SUBS: session=%s, added=%d, total=%d/%d",
                 session, len(added), self.slots_used, self.max_slots)
        return added

    def request_on_demand(self, ticker: str, strategy: str, priority: int = 50) -> bool:
        """Request an on-demand subscription for a signal-triggered ticker.

        Returns True if subscription was granted.
        """
        if ticker in self._active:
            return True  # Already subscribed

        if self.slots_available <= 0:
            # Evict lowest-priority Tier 3 subscription
            tier3 = [(t, s) for t, s in self._active.items() if s.tier == SubscriptionTier.ON_DEMAND]
            if tier3:
                tier3.sort(key=lambda x: x[1].priority)
                if tier3[0][1].priority < priority:
                    evicted = tier3[0][0]
                    del self._active[evicted]
                    log.info("SUBS: evicted %s (pri=%d) for %s (pri=%d)",
                             evicted, tier3[0][1].priority, ticker, priority)
                else:
                    return False  # Cannot evict anything with lower priority
            else:
                return False  # No Tier 3 to evict, budget full

        self._active[ticker] = SubscriptionSlot(
            ticker=ticker, tier=SubscriptionTier.ON_DEMAND,
            priority=priority, strategy=strategy,
        )
        return True

    def release_on_demand(self, ticker: str):
        """Release an on-demand subscription."""
        slot = self._active.get(ticker)
        if slot and slot.tier == SubscriptionTier.ON_DEMAND:
            del self._active[ticker]

    def get_active_subscriptions(self) -> Dict[str, SubscriptionSlot]:
        return dict(self._active)

    def check_gap(self, required_tickers: List[str]) -> List[str]:
        """Check which required tickers are NOT subscribed."""
        return [t for t in required_tickers if t not in self._active]

    def to_dict(self) -> dict:
        by_tier = {t.value: 0 for t in SubscriptionTier}
        for s in self._active.values():
            by_tier[s.tier.value] += 1
        return {
            "total_active": self.slots_used,
            "max_slots": self.max_slots,
            "available": self.slots_available,
            "by_tier": {
                "permanent": by_tier[1],
                "rotating": by_tier[2],
                "on_demand": by_tier[3],
            },
        }
