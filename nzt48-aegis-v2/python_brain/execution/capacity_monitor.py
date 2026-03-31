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


# ─── Market Impact Models ────────────────────────────────────────────────────

import math


def square_root_impact(order_gbp: float, adv_gbp: float, daily_vol_pct: float) -> float:
    """Square-root market impact model.

    impact_bps = sigma * sqrt(Q / ADV)

    Where:
    - sigma = daily volatility (in bps, e.g. 2.0% = 200 bps)
    - Q = order size (GBP)
    - ADV = average daily volume (GBP)

    Returns estimated impact in basis points.
    """
    if adv_gbp <= 0 or order_gbp <= 0:
        return 0.0
    sigma_bps = daily_vol_pct * 100.0  # Convert pct to bps
    return sigma_bps * math.sqrt(order_gbp / adv_gbp)


def permanent_impact(order_gbp: float, adv_gbp: float, kyle_lambda: Optional[float] = None) -> float:
    """Kyle's lambda permanent impact estimate.

    permanent_impact_bps = lambda * (Q / ADV)

    If kyle_lambda not provided, uses empirical default of 0.5 * sqrt(daily_spread_bps).
    Simplified: default lambda = 10 bps (typical for liquid small-cap ETPs).

    Returns estimated permanent impact in basis points.
    """
    if adv_gbp <= 0 or order_gbp <= 0:
        return 0.0
    lam = kyle_lambda if kyle_lambda is not None else 10.0  # Default 10 bps
    return lam * (order_gbp / adv_gbp)


# ─── TWAP / VWAP Algo Scheduling ─────────────────────────────────────────────


@dataclass
class TWAPSlice:
    """A single time slice in a TWAP schedule."""
    time: float          # Minutes from start
    size_gbp: float      # GBP to execute in this slice
    cumulative_pct: float  # Cumulative % executed after this slice


def generate_twap(total_gbp: float, n_slices: int, duration_mins: float) -> list:
    """Generate equal-sized TWAP (Time-Weighted Average Price) slices.

    Splits total_gbp evenly across n_slices over duration_mins.
    """
    if n_slices <= 0 or total_gbp <= 0:
        return []

    slice_gbp = total_gbp / n_slices
    interval = duration_mins / n_slices

    slices = []
    for i in range(n_slices):
        slices.append(TWAPSlice(
            time=round(i * interval, 2),
            size_gbp=round(slice_gbp, 2),
            cumulative_pct=round((i + 1) / n_slices * 100, 1),
        ))
    return slices


@dataclass
class VWAPSlice:
    """A single slice in a VWAP schedule."""
    time: float              # Minutes from start (or bucket label)
    size_gbp: float          # GBP to execute in this slice
    target_volume_pct: float  # Target % of volume in this bucket


def generate_vwap(total_gbp: float, volume_profile: dict) -> list:
    """Generate volume-weighted VWAP slices.

    volume_profile: dict mapping time_bucket (str/float) -> relative volume weight.
    e.g. {"09:00": 0.15, "10:00": 0.10, "11:00": 0.08, ...}

    Slices are sized proportionally to the volume profile.
    """
    if not volume_profile or total_gbp <= 0:
        return []

    total_weight = sum(volume_profile.values())
    if total_weight <= 0:
        return []

    slices = []
    for bucket, weight in volume_profile.items():
        pct = weight / total_weight
        slices.append(VWAPSlice(
            time=float(bucket) if isinstance(bucket, (int, float)) else hash(bucket) % 1440,
            size_gbp=round(total_gbp * pct, 2),
            target_volume_pct=round(pct * 100, 1),
        ))
    return slices


def should_use_algo(order_gbp: float, adv_gbp: float, urgency: float = 0.5) -> str:
    """Decide execution method based on order size vs ADV and urgency.

    urgency: 0.0 (patient) to 1.0 (immediate)

    Returns: "MARKET", "TWAP", or "VWAP"
    """
    if adv_gbp <= 0:
        return "MARKET"

    participation = order_gbp / adv_gbp

    if participation < 0.005 or urgency > 0.8:
        # Small order or urgent -> market order
        return "MARKET"
    elif participation < 0.02:
        # Moderate size -> TWAP (simple, predictable)
        return "TWAP"
    else:
        # Large order -> VWAP (minimize impact by matching volume)
        return "VWAP"
