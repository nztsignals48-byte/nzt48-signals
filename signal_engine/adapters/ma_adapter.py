"""
signal_engine/adapters/ma_adapter.py
======================================
Stub: M&A / merger arbitrage event adapter.
STATUS: INACTIVE until deal feed connected.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class MAEvent:
    ticker:             str
    announcement_date:  date
    deal_spread_pct:    Optional[float] = None
    deal_type:          str = "unknown"   # merger / acquisition / spinoff


class MAAdapter:
    STATUS                = "INACTIVE"
    REQUIRED_CONFIG_KEY   = "feeds.deal_feed_url"
    RECOMMENDED_PROVIDER  = "dealreporter / manual_csv"

    def get_active_deals(self, ticker: str) -> Optional[MAEvent]:
        return None

    def is_available(self) -> bool:
        return False

    @classmethod
    def strategy_availability(cls) -> dict:
        return {
            "name":                 "MERGER_ARB",
            "status":               cls.STATUS,
            "reason":               "Deal feed not connected",
            "required_config_key":  cls.REQUIRED_CONFIG_KEY,
            "recommended_provider": cls.RECOMMENDED_PROVIDER,
        }
