"""Book 12: Realistic Slippage & Cost Model.

Replaces the flat 0.5% slippage assumption with a dynamic model:
  1. Spread model: RVOL-adjusted, time-of-day U-shape
  2. Slippage model: Order-size-dependent (Almgren-Chriss simplified)
  3. Commission: Per-exchange IBKR tiered
  4. Total round-trip cost estimator

Wired into bridge.py _apply_adjustments() to replace hardcoded costs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


# ─── Exchange-specific base costs ─────────────────────────────────────────────

# Base half-spread by instrument tier (fraction of mid price)
SPREAD_TIERS = {
    "MEGA_CAP": 0.0003,    # AAPL, MSFT, etc.
    "LARGE_CAP": 0.0005,   # Top 100 equities
    "LSE_ETP_LIQUID": 0.0012,  # QQQ3, 3USL, etc. (core AEGIS universe)
    "LSE_ETP_ILLIQUID": 0.0025,  # Smaller ETPs
    "ASIAN_ETP": 0.0020,   # TSE, HKEX, SGX
    "DEFAULT": 0.0015,
}

# IBKR commission per leg (GBP equivalent)
COMMISSION = {
    "LSE": 1.70,       # IBKR minimum for UK
    "LSEETF": 1.70,
    "SMART": 0.80,     # US equities (converted ~$1.00)
    "ARCA": 0.80,
    "NASDAQ": 0.80,
    "NYSE": 0.80,
    "IBIS": 1.25,      # IBKR EU
    "XETRA": 1.25,
    "TSE": 1.50,
    "SEHK": 2.00,
    "SGX": 1.80,
    "DEFAULT": 1.70,
}

# FX conversion cost (fraction per leg, 0.2% for non-GBP instruments)
FX_COST = {
    "GBP": 0.0,
    "GBX": 0.0,
    "USD": 0.002,
    "EUR": 0.002,
    "JPY": 0.002,
    "HKD": 0.002,
    "SGD": 0.002,
    "CHF": 0.002,
    "AUD": 0.002,
    "KRW": 0.003,
    "DEFAULT": 0.002,
}

# Stamp duty / Financial Transaction Tax
STAMP_DUTY = {
    "LSE": 0.005,       # 0.5% SDRT (UK equities only, NOT ETPs)
    "SBF": 0.003,       # 0.3% FTT (French equities > €1B)
    "BVME": 0.002,      # 0.2% Italian FTT
    "SEHK": 0.0013,     # 0.13% stamp duty (HK)
    "DEFAULT": 0.0,     # Most exchanges have no FTT for ETPs
}

# Time-of-day spread multiplier (U-shape: wider at open/close)
TOD_SPREAD_MULT = {
    7: 1.8,    # LSE pre-open
    8: 1.4,    # LSE first hour
    9: 1.1,
    10: 1.0,
    11: 1.0,
    12: 1.0,
    13: 1.0,
    14: 1.3,   # US open overlap
    15: 1.2,
    16: 1.1,   # LSE close
    17: 1.0,
    18: 1.0,
    19: 1.3,   # ETP rebalancing window
    20: 1.5,   # Close auction
    21: 1.4,   # US close
}


@dataclass
class CostEstimate:
    """Breakdown of estimated round-trip costs."""
    half_spread_entry: float = 0.0
    half_spread_exit: float = 0.0
    slippage_entry: float = 0.0
    slippage_exit: float = 0.0
    commission_entry: float = 0.0
    commission_exit: float = 0.0
    fx_cost: float = 0.0
    stamp_duty: float = 0.0
    total_gbp: float = 0.0
    total_pct: float = 0.0
    breakeven_move_pct: float = 0.0


def estimate_spread(
    tier: str = "DEFAULT",
    rvol: float = 1.0,
    utc_hour: int = 12,
) -> float:
    """Estimate half-spread as fraction of mid-price.

    Adjusts base spread by:
    - RVOL: higher vol = wider spreads (sqrt scaling)
    - Time of day: U-shape (wider at open/close)
    """
    base = SPREAD_TIERS.get(tier, SPREAD_TIERS["DEFAULT"])

    # RVOL adjustment: spread widens with sqrt(rvol) above 1.0
    rvol_mult = 1.0
    if rvol > 1.5:
        rvol_mult = math.sqrt(rvol / 1.5)

    # Time of day
    tod_mult = TOD_SPREAD_MULT.get(utc_hour, 1.0)

    return base * rvol_mult * tod_mult


def estimate_slippage(
    notional_gbp: float,
    daily_volume_gbp: float = 1_000_000.0,
    participation_rate: float = 0.01,
) -> float:
    """Almgren-Chriss simplified slippage (fraction of notional).

    Temporary market impact: sigma * sqrt(participation_rate)
    For small orders (AEGIS typical £500-£3000), impact is minimal.
    """
    if daily_volume_gbp <= 0 or notional_gbp <= 0:
        return 0.0

    # Participation rate = order_size / daily_volume
    actual_participation = notional_gbp / daily_volume_gbp

    # Simplified Almgren-Chriss: impact ~ 0.1 * sqrt(participation)
    # This is calibrated for liquid ETPs
    impact_frac = 0.10 * math.sqrt(min(actual_participation, 0.05))

    # Floor: minimum slippage of 0.01% (always some execution cost)
    return max(impact_frac, 0.0001)


def classify_instrument_tier(
    symbol: str,
    exchange: str = "",
    avg_daily_volume: float = 0,
) -> str:
    """Classify instrument into cost tier based on symbol/exchange."""
    # Major US mega caps
    mega_caps = {"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B"}
    if symbol in mega_caps:
        return "MEGA_CAP"

    # Core AEGIS ETP universe (liquid LSE ETPs)
    liquid_etps = {
        "QQQ3.L", "3USL.L", "3LUS.L", "SP5C.L", "VUAG.L", "VUSA.L",
        "3LNV.L", "3LAP.L", "3LMS.L", "3LAM.L", "3LTS.L",
    }
    if symbol in liquid_etps or (exchange in ("LSE", "LSEETF") and avg_daily_volume > 500_000):
        return "LSE_ETP_LIQUID"

    if exchange in ("LSE", "LSEETF"):
        return "LSE_ETP_ILLIQUID"

    if exchange in ("TSE", "SEHK", "SGX"):
        return "ASIAN_ETP"

    if avg_daily_volume > 5_000_000:
        return "LARGE_CAP"

    return "DEFAULT"


def total_round_trip_cost(
    notional_gbp: float,
    symbol: str = "",
    exchange: str = "LSE",
    currency: str = "GBP",
    rvol: float = 1.0,
    utc_hour: int = 12,
    daily_volume_gbp: float = 1_000_000.0,
    is_etp: bool = True,
) -> CostEstimate:
    """Full round-trip cost estimate for a trade.

    Returns detailed breakdown and total in GBP.
    """
    tier = classify_instrument_tier(symbol, exchange)
    result = CostEstimate()

    # Half-spread (entry + exit)
    hs = estimate_spread(tier, rvol, utc_hour)
    result.half_spread_entry = notional_gbp * hs
    result.half_spread_exit = notional_gbp * hs

    # Slippage (entry + exit)
    slip = estimate_slippage(notional_gbp, daily_volume_gbp)
    result.slippage_entry = notional_gbp * slip
    result.slippage_exit = notional_gbp * slip

    # Commission (entry + exit)
    comm = COMMISSION.get(exchange, COMMISSION["DEFAULT"])
    result.commission_entry = comm
    result.commission_exit = comm

    # FX cost (entry + exit, 0 for GBP)
    fx_rate = FX_COST.get(currency, FX_COST["DEFAULT"])
    result.fx_cost = notional_gbp * fx_rate * 2  # Both legs

    # Stamp duty (entry only, and usually exempt for ETPs)
    if is_etp:
        result.stamp_duty = 0.0
    else:
        sd_rate = STAMP_DUTY.get(exchange, STAMP_DUTY["DEFAULT"])
        result.stamp_duty = notional_gbp * sd_rate

    # Total
    result.total_gbp = (
        result.half_spread_entry + result.half_spread_exit
        + result.slippage_entry + result.slippage_exit
        + result.commission_entry + result.commission_exit
        + result.fx_cost
        + result.stamp_duty
    )

    if notional_gbp > 0:
        result.total_pct = result.total_gbp / notional_gbp * 100
        result.breakeven_move_pct = result.total_gbp / notional_gbp * 100
    else:
        result.total_pct = 0.0
        result.breakeven_move_pct = 0.0

    return result


if __name__ == "__main__":
    # QQQ3.L typical trade
    cost = total_round_trip_cost(
        notional_gbp=2000, symbol="QQQ3.L", exchange="LSE",
        currency="GBP", rvol=1.2, utc_hour=14,
    )
    print(f"QQQ3.L £2000 trade:")
    print(f"  Spread: £{cost.half_spread_entry + cost.half_spread_exit:.2f}")
    print(f"  Slippage: £{cost.slippage_entry + cost.slippage_exit:.2f}")
    print(f"  Commission: £{cost.commission_entry + cost.commission_exit:.2f}")
    print(f"  FX: £{cost.fx_cost:.2f}")
    print(f"  Total: £{cost.total_gbp:.2f} ({cost.total_pct:.2f}%)")
    print(f"  Breakeven: {cost.breakeven_move_pct:.2f}%")

    # US equity comparison
    cost_us = total_round_trip_cost(
        notional_gbp=2000, symbol="AAPL", exchange="SMART",
        currency="USD", rvol=1.0, utc_hour=15,
    )
    print(f"\nAAPL £2000 trade:")
    print(f"  Total: £{cost_us.total_gbp:.2f} ({cost_us.total_pct:.2f}%)")
