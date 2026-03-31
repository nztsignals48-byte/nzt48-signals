"""Book 18: Factor Zoo & Alpha Taxonomy.

Classifies every AEGIS strategy into one of 8 canonical alpha factor categories.
Provides portfolio-level factor concentration analysis and a gate that blocks
over-concentration in any single factor (>60% of positions).

Nightly: compute_factor_attribution() decomposes P&L by factor category so
the self-reflection loop can detect factor-level decay.

Usage (bridge.py — factor concentration gate):
    from python_brain.analytics.factor_zoo import (
        classify_signal, get_factor_exposure, factor_concentration_gate,
    )
    factor = classify_signal(sig["strategy"])
    exposure = get_factor_exposure(open_positions)
    blocked, reason = factor_concentration_gate(factor, exposure)

Usage (nightly):
    from python_brain.analytics.factor_zoo import compute_factor_attribution
    attribution = compute_factor_attribution(closed_trades)
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("factor_zoo")

# ---------------------------------------------------------------------------
# Factor categories — the canonical alpha taxonomy
# ---------------------------------------------------------------------------

class FactorCategory(Enum):
    """Alpha factor categories from the Factor Zoo literature (Book 18).

    Each strategy in the AEGIS pipeline maps to exactly one primary factor.
    """
    MOMENTUM = "MOMENTUM"
    MEAN_REVERSION = "MEAN_REVERSION"
    VOLATILITY = "VOLATILITY"
    FLOW = "FLOW"
    STRUCTURAL = "STRUCTURAL"
    CALENDAR = "CALENDAR"
    CROSS_MARKET = "CROSS_MARKET"
    SENTIMENT = "SENTIMENT"


# ---------------------------------------------------------------------------
# Strategy → Factor mapping
# ---------------------------------------------------------------------------

# Exhaustive map of all AEGIS strategy names to their primary factor.
# New strategies should be added here when created.
_STRATEGY_FACTOR_MAP: Dict[str, FactorCategory] = {
    # Core systems
    "S1_Microstructure": FactorCategory.STRUCTURAL,
    "S2_Reversion": FactorCategory.MEAN_REVERSION,
    "S3_MacroTrend": FactorCategory.MOMENTUM,
    "S4_VolPremium": FactorCategory.VOLATILITY,
    "S5_OvernightCarry": FactorCategory.CALENDAR,
    "S6_Catalyst": FactorCategory.SENTIMENT,
    "S7_TailHedge": FactorCategory.VOLATILITY,

    # Classic generators
    "Momentum": FactorCategory.MOMENTUM,
    "IBS_MeanReversion": FactorCategory.MEAN_REVERSION,
    "VolExpansion": FactorCategory.VOLATILITY,
    "ORB_Breakout": FactorCategory.MOMENTUM,
    "GapFade": FactorCategory.MEAN_REVERSION,
    "VolCompression": FactorCategory.VOLATILITY,
    "RebalancingFlow": FactorCategory.FLOW,
    "NAVArbitrage": FactorCategory.STRUCTURAL,
    "AlphaFactory": FactorCategory.MOMENTUM,

    # Cross-market & pairs
    "LeadLag": FactorCategory.CROSS_MARKET,
    "PairsReversion": FactorCategory.CROSS_MARKET,
    "CointPairs": FactorCategory.CROSS_MARKET,
    "NegRiskArb": FactorCategory.STRUCTURAL,

    # ML / attention
    "EMAT_Attention": FactorCategory.MOMENTUM,
    "TemporalAttention": FactorCategory.MOMENTUM,
    "SwarmPredictor": FactorCategory.SENTIMENT,
    "HFT_Probability": FactorCategory.STRUCTURAL,

    # Flow & sentiment
    "HighFlyer": FactorCategory.FLOW,
    "CopyTrading": FactorCategory.FLOW,

    # Calendar & event
    "NightRider": FactorCategory.CALENDAR,
    "EventDrift": FactorCategory.CALENDAR,
    "FOMMCDriftT-1": FactorCategory.CALENDAR,
    "FOMMCDriftT+N": FactorCategory.CALENDAR,

    # Hedge strategies
    "HEDGE_InverseETP": FactorCategory.VOLATILITY,
    "HEDGE_VIXAllocation": FactorCategory.VOLATILITY,
    "HEDGE_CashRaise": FactorCategory.VOLATILITY,

    # Scout
    "ApexScout": FactorCategory.MOMENTUM,
}

# Prefix-based fallback for Orchestrator_* strategies
_PREFIX_FACTOR_MAP: Dict[str, FactorCategory] = {
    "Orchestrator_Momentum": FactorCategory.MOMENTUM,
    "Orchestrator_Reversion": FactorCategory.MEAN_REVERSION,
    "Orchestrator_Vol": FactorCategory.VOLATILITY,
    "Orchestrator_Flow": FactorCategory.FLOW,
    "Orchestrator_Calendar": FactorCategory.CALENDAR,
    "Orchestrator_Macro": FactorCategory.MOMENTUM,
}


def classify_signal(strategy_name: str) -> FactorCategory:
    """Classify a strategy name into its primary alpha factor.

    Args:
        strategy_name: Strategy name from signal dict (e.g. "S1_Microstructure").

    Returns:
        FactorCategory enum value.
    """
    # Direct lookup
    factor = _STRATEGY_FACTOR_MAP.get(strategy_name)
    if factor is not None:
        return factor

    # Prefix-based lookup for Orchestrator_* and dynamic strategies
    for prefix, cat in _PREFIX_FACTOR_MAP.items():
        if strategy_name.startswith(prefix):
            return cat

    # Heuristic fallback based on common substrings
    name_lower = strategy_name.lower()
    if "reversion" in name_lower or "mean" in name_lower or "ibs" in name_lower:
        return FactorCategory.MEAN_REVERSION
    if "momentum" in name_lower or "trend" in name_lower or "breakout" in name_lower:
        return FactorCategory.MOMENTUM
    if "vol" in name_lower or "hedge" in name_lower or "vix" in name_lower:
        return FactorCategory.VOLATILITY
    if "flow" in name_lower or "copy" in name_lower or "retail" in name_lower:
        return FactorCategory.FLOW
    if "event" in name_lower or "fomc" in name_lower or "night" in name_lower or "carry" in name_lower:
        return FactorCategory.CALENDAR
    if "pair" in name_lower or "lead" in name_lower or "coint" in name_lower or "cross" in name_lower:
        return FactorCategory.CROSS_MARKET
    if "sentiment" in name_lower or "swarm" in name_lower or "catalyst" in name_lower:
        return FactorCategory.SENTIMENT
    if "micro" in name_lower or "nav" in name_lower or "arb" in name_lower:
        return FactorCategory.STRUCTURAL

    # Ultimate fallback — unknown strategies default to MOMENTUM (most common)
    log.warning("factor_zoo: unmapped strategy '%s' — defaulting to MOMENTUM", strategy_name)
    return FactorCategory.MOMENTUM


def get_factor_exposure(active_positions: List[Dict[str, Any]]) -> Dict[str, float]:
    """Compute current portfolio factor concentration.

    Args:
        active_positions: List of position dicts, each must have "strategy" key.

    Returns:
        Dict mapping FactorCategory.value → fraction of total positions (0.0 - 1.0).
        Example: {"MOMENTUM": 0.4, "MEAN_REVERSION": 0.2, ...}
    """
    if not active_positions:
        return {}

    counts: Dict[str, int] = {}
    total = 0
    for pos in active_positions:
        strat = pos.get("strategy", "")
        if not strat:
            continue
        factor = classify_signal(strat)
        counts[factor.value] = counts.get(factor.value, 0) + 1
        total += 1

    if total == 0:
        return {}

    return {k: v / total for k, v in counts.items()}


def factor_concentration_gate(
    new_signal_factor: FactorCategory,
    current_exposure: Dict[str, float],
    max_single_factor: float = 0.60,
) -> Tuple[bool, str]:
    """Gate: block new signal if its factor already exceeds concentration limit.

    Args:
        new_signal_factor: Factor category of the proposed new signal.
        current_exposure: Current factor exposure dict from get_factor_exposure().
        max_single_factor: Maximum allowed fraction for any single factor (default 60%).

    Returns:
        (blocked, reason) — blocked=True means signal should be vetoed.
    """
    if not current_exposure:
        return False, ""

    factor_name = new_signal_factor.value
    current_pct = current_exposure.get(factor_name, 0.0)

    if current_pct >= max_single_factor:
        reason = (
            f"factor_concentration: {factor_name} at {current_pct:.0%} "
            f"(limit {max_single_factor:.0%}) — blocked"
        )
        log.info("FACTOR_ZOO_GATE: %s", reason)
        return True, reason

    return False, ""


# ---------------------------------------------------------------------------
# Nightly: Factor attribution analysis
# ---------------------------------------------------------------------------

def compute_factor_attribution(
    trades: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Compute P&L attribution by alpha factor category.

    Used by the nightly pipeline to decompose performance into factor buckets.

    Args:
        trades: List of closed trade dicts. Each must have:
            - "strategy" (str): strategy name
            - "pnl" (float): trade P&L
            Optional:
            - "cost_adjusted_pnl" (float): P&L after costs
            - "hold_time_mins" (int): hold duration

    Returns:
        Dict mapping factor name to stats:
        {
            "MOMENTUM": {
                "trade_count": 15,
                "total_pnl": 42.50,
                "win_rate": 0.60,
                "avg_pnl": 2.83,
                "cost_adjusted_pnl": 38.20,
                "avg_hold_mins": 145,
                "sharpe_estimate": 1.2,
                "strategies": ["Momentum", "S3_MacroTrend", "ORB_Breakout"],
            },
            ...
        }
    """
    if not trades:
        return {}

    # Accumulate per-factor
    factor_data: Dict[str, Dict[str, Any]] = {}

    for trade in trades:
        strat = trade.get("strategy", "")
        if not strat:
            continue

        factor = classify_signal(strat).value
        if factor not in factor_data:
            factor_data[factor] = {
                "trade_count": 0,
                "total_pnl": 0.0,
                "cost_adjusted_pnl": 0.0,
                "wins": 0,
                "pnl_list": [],
                "hold_times": [],
                "strategies": set(),
            }

        fd = factor_data[factor]
        pnl = trade.get("pnl", 0.0)
        fd["trade_count"] += 1
        fd["total_pnl"] += pnl
        fd["cost_adjusted_pnl"] += trade.get("cost_adjusted_pnl", pnl)
        fd["pnl_list"].append(pnl)
        if pnl > 0:
            fd["wins"] += 1
        ht = trade.get("hold_time_mins", 0)
        if ht > 0:
            fd["hold_times"].append(ht)
        fd["strategies"].add(strat)

    # Compute summary stats
    result: Dict[str, Dict[str, Any]] = {}
    for factor, fd in factor_data.items():
        n = fd["trade_count"]
        pnl_list = fd["pnl_list"]

        # Sharpe estimate: mean / stdev (annualised is meaningless here, just relative)
        avg_pnl = fd["total_pnl"] / n if n > 0 else 0.0
        sharpe = 0.0
        if n >= 3:
            variance = sum((p - avg_pnl) ** 2 for p in pnl_list) / (n - 1)
            stdev = variance ** 0.5
            if stdev > 1e-9:
                sharpe = avg_pnl / stdev

        avg_hold = 0
        if fd["hold_times"]:
            avg_hold = int(sum(fd["hold_times"]) / len(fd["hold_times"]))

        result[factor] = {
            "trade_count": n,
            "total_pnl": round(fd["total_pnl"], 2),
            "win_rate": round(fd["wins"] / n, 3) if n > 0 else 0.0,
            "avg_pnl": round(avg_pnl, 2),
            "cost_adjusted_pnl": round(fd["cost_adjusted_pnl"], 2),
            "avg_hold_mins": avg_hold,
            "sharpe_estimate": round(sharpe, 3),
            "strategies": sorted(fd["strategies"]),
        }

    return result
