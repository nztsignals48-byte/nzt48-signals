"""Capacity Analysis & Market Impact — Books 49, 181.

Monitor and enforce capacity constraints for illiquid leveraged ETPs.
At £10K, capacity is rarely binding. But for growth planning, we need
to know the ceiling per instrument and per strategy.

Key metrics:
  ADV: Average Daily Volume (GBP)
  Participation rate: our order / ADV (max 5% for liquid, 1% for illiquid)
  Capacity wall: equity level where we can't size up without impact

Usage:
    from python_brain.execution.capacity_monitor import (
        CapacityMonitor, InstrumentCapacity,
    )

    monitor = CapacityMonitor()
    cap = monitor.check("NVD3.L", order_gbp=5000)
    if cap.participation_rate > 0.05:
        use_algo_order()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

log = logging.getLogger("capacity_monitor")


# Estimated ADV (GBP) for core instruments — update from live data
INSTRUMENT_ADV: Dict[str, float] = {
    "3USL.L": 2_000_000, "3USS.L": 800_000,
    "QQQ3.L": 1_500_000, "QQQS.L": 500_000,
    "NVD3.L": 3_000_000, "NV3S.L": 400_000,
    "TSL3.L": 1_000_000, "TS3S.L": 300_000,
    "AAP3.L": 500_000, "MSF3.L": 400_000,
    "GOO3.L": 300_000, "AML3.L": 400_000,
    "AMD3.L": 500_000, "MET3.L": 300_000,
    "GPT3.L": 200_000, "MS23.L": 150_000,
    "COI3.L": 100_000, "TSM3.L": 200_000,
    "3LOI.L": 300_000, "3LGD.L": 400_000,
    "3UKL.L": 200_000, "3UKS.L": 100_000,
    "VIXL.L": 500_000,
}

# Max participation rate by liquidity tier
MAX_PARTICIPATION: Dict[str, float] = {
    "high": 0.05,    # 5% of ADV
    "medium": 0.03,  # 3% of ADV
    "low": 0.01,     # 1% of ADV
}


@dataclass
class InstrumentCapacity:
    """Capacity assessment for a single instrument."""
    ticker: str
    adv_gbp: float
    liquidity_tier: str  # "high", "medium", "low"
    max_order_gbp: float
    max_daily_gbp: float
    participation_rate: float = 0.0
    within_capacity: bool = True
    requires_algo: bool = False


class CapacityMonitor:
    """Monitor order sizes against instrument capacity."""

    def __init__(self, adv_overrides: Optional[Dict[str, float]] = None):
        self._adv = dict(INSTRUMENT_ADV)
        if adv_overrides:
            self._adv.update(adv_overrides)

    def update_adv(self, ticker: str, adv_gbp: float):
        """Update ADV from live data."""
        self._adv[ticker] = adv_gbp

    def _tier(self, adv: float) -> str:
        if adv >= 1_000_000:
            return "high"
        elif adv >= 300_000:
            return "medium"
        return "low"

    def check(self, ticker: str, order_gbp: float) -> InstrumentCapacity:
        """Check if an order is within capacity for an instrument."""
        adv = self._adv.get(ticker, 100_000)  # Default 100K for unknown
        tier = self._tier(adv)
        max_pct = MAX_PARTICIPATION[tier]

        max_order = adv * max_pct
        participation = order_gbp / max(adv, 1)

        cap = InstrumentCapacity(
            ticker=ticker,
            adv_gbp=adv,
            liquidity_tier=tier,
            max_order_gbp=round(max_order, 0),
            max_daily_gbp=round(adv * max_pct * 3, 0),  # 3 trades/day max
            participation_rate=round(participation, 4),
            within_capacity=order_gbp <= max_order,
            requires_algo=participation > 0.005,  # >0.5% ADV → use algo
        )

        if not cap.within_capacity:
            log.warning(
                "CAPACITY: %s order %.0f GBP exceeds max %.0f GBP (%.1f%% of ADV %.0f)",
                ticker, order_gbp, max_order, participation * 100, adv,
            )

        return cap

    def portfolio_capacity_wall(self, equity: float, n_positions: int = 3) -> float:
        """Estimate total portfolio capacity (max equity before impact).

        The capacity wall is where average position size hits the 5% ADV limit
        for the median instrument in the universe.
        """
        adv_values = sorted(self._adv.values())
        if not adv_values:
            return equity

        # Median ADV
        median_adv = adv_values[len(adv_values) // 2]
        # Max position per instrument = 5% of median ADV
        max_per_instrument = median_adv * 0.05
        # Max equity = max_per_instrument × n_positions / max_single_pct
        max_equity = max_per_instrument * n_positions / 0.33  # 33% max single position

        return round(max_equity, 0)

    def to_dict(self) -> dict:
        return {
            "n_instruments": len(self._adv),
            "median_adv": sorted(self._adv.values())[len(self._adv) // 2] if self._adv else 0,
            "capacity_wall_gbp": self.portfolio_capacity_wall(10000),
        }
