"""
signal_engine/adapters/earnings_adapter.py
==========================================
Stub: earnings event adapter.
STATUS: INACTIVE until real feed connected.
Recommended provider: Alpha Vantage / OpenBB / Tiingo
Config key: feeds.earnings_calendar_url
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class EarningsEvent:
    ticker:         str
    earnings_date:  date
    days_until:     int
    surprise_pct:   Optional[float] = None   # set post-earnings; None pre-event


class EarningsAdapter:
    """
    Interface for StrategyAvailability surface and EventWindowRule.
    Returns empty results until a real feed is connected.
    """
    STATUS                = "INACTIVE"
    REQUIRED_CONFIG_KEY   = "feeds.earnings_calendar_url"
    RECOMMENDED_PROVIDER  = "alpha_vantage / openbb / tiingo"

    def get_upcoming(self, ticker: str, days_ahead: int = 5) -> Optional[EarningsEvent]:
        """Returns None until feed is connected."""
        return None

    def is_available(self) -> bool:
        return False

    @classmethod
    def strategy_availability(cls) -> dict:
        return {
            "name":                 "PEAD_CATALYST",
            "status":               cls.STATUS,
            "reason":               "Earnings calendar feed not connected",
            "required_config_key":  cls.REQUIRED_CONFIG_KEY,
            "recommended_provider": cls.RECOMMENDED_PROVIDER,
        }
