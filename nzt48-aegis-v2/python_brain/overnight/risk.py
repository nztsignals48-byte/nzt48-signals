"""Overnight Gap Risk Management — Book 40, 148, 186.

Position size IS the only hedge against overnight gaps. This module enforces:
1. Per-instrument overnight tier limits
2. VIX-conditional regime exposure limits
3. Friday close protocol (30% reduction weeknight → 100% in CRISIS)
4. 5x product intraday-only enforcement
5. Earnings hard-close rules
6. VIX spike emergency override (VIX > 35)

Called by bridge.py during signal generation to:
- Block overnight entries that would exceed limits
- Scale position sizes for late-session entries
- Generate reduction orders before market close

Dependencies: config (config.toml), numpy (stdlib-level).
No side effects on WAL or engine state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional, Tuple

log = logging.getLogger("overnight_risk")


# ---------------------------------------------------------------------------
# Regime Classification (simplified from Book 15)
# ---------------------------------------------------------------------------
class OvernightRegime(Enum):
    STEADY = "STEADY"               # VIX < 18
    WALKING_ON_ICE = "WOI"          # VIX 18-30
    CRISIS = "CRISIS"               # VIX 30-50
    EXTREME_CRISIS = "EXTREME"      # VIX > 50


def classify_overnight_regime(vix: float) -> OvernightRegime:
    """Classify current regime by VIX level for overnight risk."""
    if vix >= 50:
        return OvernightRegime.EXTREME_CRISIS
    elif vix >= 30:
        return OvernightRegime.CRISIS
    elif vix >= 18:
        return OvernightRegime.WALKING_ON_ICE
    return OvernightRegime.STEADY


# ---------------------------------------------------------------------------
# Instrument Risk Tiers (Book 40, Section 12)
# ---------------------------------------------------------------------------
class InstrumentTier(Enum):
    INDEX = 1        # 3USL, QQQ3, TQQQ, SPXL — max 15% overnight
    MEGA_CAP = 2     # AAP3, AML3, MSF3, GOO3 — max 8%
    HIGH_VOL = 3     # NVD3, TSL3, AMD3, MET3 — max 5%
    EXTREME_VOL = 4  # MS23, COI3 — max 3%
    VIX_LINKED = 5   # VIXL — max 5% long only
    FIVE_X = 6       # All 5x — 0% NEVER overnight
    INVERSE = 7      # Same as corresponding long tier


# Per-tier maximum overnight position as % of equity
TIER_MAX_OVERNIGHT_PCT: Dict[InstrumentTier, float] = {
    InstrumentTier.INDEX: 15.0,
    InstrumentTier.MEGA_CAP: 8.0,
    InstrumentTier.HIGH_VOL: 5.0,
    InstrumentTier.EXTREME_VOL: 3.0,
    InstrumentTier.VIX_LINKED: 5.0,
    InstrumentTier.FIVE_X: 0.0,  # NEVER
    InstrumentTier.INVERSE: 8.0,  # Default; overridden per-instrument
}

# Ticker → Tier mapping (extend as universe grows)
TICKER_TIERS: Dict[str, InstrumentTier] = {
    # Tier 1: Index
    "3LUS.L": InstrumentTier.INDEX, "3USL.L": InstrumentTier.INDEX,
    "QQQ3.L": InstrumentTier.INDEX, "5SPY.L": InstrumentTier.INDEX,
    # Tier 2: Mega-Cap
    "AAP3.L": InstrumentTier.MEGA_CAP, "AML3.L": InstrumentTier.MEGA_CAP,
    "MSF3.L": InstrumentTier.MEGA_CAP, "GOO3.L": InstrumentTier.MEGA_CAP,
    "GPT3.L": InstrumentTier.MEGA_CAP, "3LAP.L": InstrumentTier.MEGA_CAP,
    "3LMS.L": InstrumentTier.MEGA_CAP,
    # Tier 3: High-Vol Single Stock
    "NVD3.L": InstrumentTier.HIGH_VOL, "TSL3.L": InstrumentTier.HIGH_VOL,
    "AMD3.L": InstrumentTier.HIGH_VOL, "MET3.L": InstrumentTier.HIGH_VOL,
    "MU2.L": InstrumentTier.HIGH_VOL, "TSM3.L": InstrumentTier.HIGH_VOL,
    # Tier 4: Extreme-Vol (crypto-correlated)
    "MS23.L": InstrumentTier.EXTREME_VOL, "COI3.L": InstrumentTier.EXTREME_VOL,
    # Tier 5: VIX-linked
    "VIXL.L": InstrumentTier.VIX_LINKED,
    # Tier 6: All 5x products — NEVER overnight
    "QQQ5.L": InstrumentTier.FIVE_X, "5SPY.L": InstrumentTier.FIVE_X,
    # Tier 7: Inverse products
    "QQQS.L": InstrumentTier.INVERSE, "3USS.L": InstrumentTier.INVERSE,
    "NV3S.L": InstrumentTier.INVERSE, "TS3S.L": InstrumentTier.INVERSE,
    "3STS.L": InstrumentTier.INVERSE,
}


# ---------------------------------------------------------------------------
# Regime Exposure Limits (Book 40, Section 11)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RegimeExposureLimits:
    """Max overnight exposure as % of allocated capital by regime."""
    max_long_pct: float
    max_inverse_pct: float


REGIME_LIMITS: Dict[OvernightRegime, RegimeExposureLimits] = {
    OvernightRegime.STEADY: RegimeExposureLimits(100.0, 30.0),
    OvernightRegime.WALKING_ON_ICE: RegimeExposureLimits(50.0, 50.0),
    OvernightRegime.CRISIS: RegimeExposureLimits(20.0, 80.0),
    OvernightRegime.EXTREME_CRISIS: RegimeExposureLimits(0.0, 50.0),
}

# Friday reduction multipliers (Book 40, Section 13)
FRIDAY_MULTIPLIERS: Dict[OvernightRegime, float] = {
    OvernightRegime.STEADY: 0.70,          # 30% reduction
    OvernightRegime.WALKING_ON_ICE: 0.50,  # 50% reduction
    OvernightRegime.CRISIS: 0.0,           # Complete exit
    OvernightRegime.EXTREME_CRISIS: 0.0,   # Already flat
}


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------
def get_instrument_tier(ticker: str) -> InstrumentTier:
    """Get risk tier for a ticker. Defaults to HIGH_VOL for unknown."""
    return TICKER_TIERS.get(ticker, InstrumentTier.HIGH_VOL)


def max_overnight_position_pct(
    ticker: str,
    vix: float,
    is_friday: bool = False,
    leverage: int = 3,
) -> float:
    """Calculate maximum overnight position size as % of equity.

    Returns 0.0 if the instrument should NOT be held overnight.
    """
    # Rule 1: 5x products — NEVER overnight
    if leverage >= 5:
        return 0.0

    tier = get_instrument_tier(ticker)
    if tier == InstrumentTier.FIVE_X:
        return 0.0

    # Rule 2: Per-instrument tier limit
    tier_limit = TIER_MAX_OVERNIGHT_PCT.get(tier, 5.0)

    # Rule 3: Regime limit
    regime = classify_overnight_regime(vix)
    regime_lim = REGIME_LIMITS[regime]
    is_inverse = tier == InstrumentTier.INVERSE or ticker.endswith("S.L")
    portfolio_limit = regime_lim.max_inverse_pct if is_inverse else regime_lim.max_long_pct

    # Rule 4: Friday reduction
    if is_friday:
        friday_mult = FRIDAY_MULTIPLIERS.get(regime, 0.5)
        portfolio_limit *= friday_mult

    # Rule 5: VIX > 35 emergency override (Book 27)
    if vix > 35 and not is_inverse:
        portfolio_limit = min(portfolio_limit, 10.0)  # Hard cap at 10%
    if vix > 45 and not is_inverse:
        return 0.0  # No long overnight exposure above VIX 45

    # Effective limit = min(per-instrument, portfolio regime)
    return min(tier_limit, portfolio_limit)


def should_block_overnight_entry(
    ticker: str,
    vix: float,
    london_time_secs: int,
    leverage: int = 3,
    is_friday: bool = False,
) -> Tuple[bool, str]:
    """Check if a new entry should be blocked because it would create overnight exposure.

    Called during signal generation for late-session entries.
    Returns (should_block, reason).
    """
    # Late-session entries that will become overnight positions
    # LSE close approaches: after 15:30 UTC (1.5h before close)
    # US-underlying ETPs: after 20:00 UTC (1h before US close)
    is_late_lse = london_time_secs >= 55800  # 15:30 London
    is_late_us = london_time_secs >= 72000   # 20:00 London (US session)

    if not (is_late_lse or is_late_us):
        return False, ""

    max_pct = max_overnight_position_pct(ticker, vix, is_friday, leverage)
    if max_pct <= 0.0:
        return True, f"overnight_blocked: {ticker} max_overnight=0% (VIX={vix:.0f}, leverage={leverage}x, friday={is_friday})"

    # 5x products always blocked late session
    if leverage >= 5:
        return True, f"overnight_blocked: 5x product {ticker} cannot be held overnight"

    return False, ""


def overnight_position_scale(
    ticker: str,
    vix: float,
    current_position_pct: float,
    is_friday: bool = False,
    leverage: int = 3,
) -> float:
    """Calculate scaling factor for overnight positions.

    Returns a multiplier 0.0-1.0 to apply to position size.
    Used for late-session entries and EOD position reduction.
    """
    max_pct = max_overnight_position_pct(ticker, vix, is_friday, leverage)
    if max_pct <= 0.0:
        return 0.0
    if current_position_pct <= max_pct:
        return 1.0
    # Scale down proportionally
    return max_pct / max(current_position_pct, 0.01)


def generate_reduction_orders(
    positions: Dict[str, float],  # ticker → notional GBP
    equity: float,
    vix: float,
    is_friday: bool = False,
) -> Dict[str, float]:
    """Generate position reduction amounts for overnight compliance.

    Returns dict of ticker → amount_to_reduce_gbp.
    Called T-90 to T-0 before market close.
    """
    reductions: Dict[str, float] = {}

    for ticker, notional in positions.items():
        tier = get_instrument_tier(ticker)
        leverage = 5 if tier == InstrumentTier.FIVE_X else 3
        pct_of_equity = (notional / equity * 100) if equity > 0 else 100.0

        max_pct = max_overnight_position_pct(ticker, vix, is_friday, leverage)
        max_notional = equity * max_pct / 100.0

        if notional > max_notional:
            reduction = notional - max_notional
            reductions[ticker] = round(reduction, 2)
            log.info(
                "OVERNIGHT_REDUCTION: %s reduce %.0f GBP → %.0f GBP "
                "(max=%.1f%%, current=%.1f%%, VIX=%.0f, friday=%s)",
                ticker, notional, max_notional,
                max_pct, pct_of_equity, vix, is_friday,
            )

    return reductions


# ---------------------------------------------------------------------------
# Integration point for bridge.py
# ---------------------------------------------------------------------------
def check_overnight_risk(
    ticker: str,
    confidence: int,
    kelly_fraction: float,
    vix: float,
    london_time_secs: int,
    leverage: int = 3,
    is_friday: bool = False,
) -> Tuple[int, float]:
    """Adjust confidence and Kelly for overnight gap risk.

    Called from bridge.py signal generation pipeline.
    Returns (adjusted_confidence, adjusted_kelly).
    """
    blocked, reason = should_block_overnight_entry(
        ticker, vix, london_time_secs, leverage, is_friday,
    )
    if blocked:
        log.info("OVERNIGHT_BLOCK: %s conf=%d → 0 (%s)", ticker, confidence, reason)
        return 0, 0.0

    # Scale Kelly for late-session entries approaching overnight
    max_pct = max_overnight_position_pct(ticker, vix, is_friday, leverage)
    if max_pct < 100.0 and london_time_secs >= 54000:  # After 15:00 London
        scale = max_pct / 100.0
        adjusted_kelly = kelly_fraction * scale
        # Reduce confidence proportionally (capped — don't zero it)
        conf_penalty = int((1.0 - scale) * 15)  # Max 15 point penalty
        adjusted_conf = max(confidence - conf_penalty, 30)
        return adjusted_conf, adjusted_kelly

    return confidence, kelly_fraction
