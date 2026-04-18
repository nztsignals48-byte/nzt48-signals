"""Order router. 4 tiers + KILL. Picks IBKR algo per strategy + urgency + ADV."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AlgoTier(str, Enum):
    URGENT = "Adaptive/Urgent"
    PATIENT = "Adaptive/Patient"
    PEG_MID = "Peg-to-Midpoint"
    ARRIVAL_PRICE = "Arrival Price"
    MARKET = "Market"


# Per-strategy preferred tier.
STRATEGY_TIER = {
    "sentiment_long_short":  AlgoTier.URGENT,     # news-driven
    "filing_change_detect":  AlgoTier.PATIENT,
    "index_recon":           AlgoTier.PATIENT,
    "earnings_pattern":      AlgoTier.PATIENT,
    "overnight_return":      AlgoTier.PEG_MID,
    "ibs_mean_reversion":    AlgoTier.PATIENT,
}


@dataclass
class OrderDecision:
    tier: AlgoTier
    is_market: bool


def pick_tier(strategy: str, is_stop: bool, adv_pct: float) -> OrderDecision:
    if is_stop:
        return OrderDecision(AlgoTier.MARKET, True)
    if adv_pct > 0.01:                # > 1% ADV
        return OrderDecision(AlgoTier.ARRIVAL_PRICE, False)
    return OrderDecision(STRATEGY_TIER.get(strategy, AlgoTier.PATIENT), False)
