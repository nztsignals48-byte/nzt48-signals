"""
signal_engine/adapters/lockup_adapter.py
=========================================
Stub: IPO lockup expiry adapter.
STATUS: INACTIVE until manual CSV or feed connected.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class LockupEvent:
    ticker:       str
    expiry_date:  date
    days_until:   int
    shares_locked: Optional[int] = None


class LockupAdapter:
    STATUS                = "INACTIVE"
    REQUIRED_CONFIG_KEY   = "feeds.lockup_calendar_url"
    RECOMMENDED_PROVIDER  = "manual_csv / stockanalysis.com"

    def get_upcoming(self, ticker: str, days_ahead: int = 5) -> Optional[LockupEvent]:
        return None

    def is_available(self) -> bool:
        return False

    @classmethod
    def strategy_availability(cls) -> dict:
        return {
            "name":                 "LOCKUP_EXPIRY",
            "status":               cls.STATUS,
            "reason":               "Lockup calendar feed not connected",
            "required_config_key":  cls.REQUIRED_CONFIG_KEY,
            "recommended_provider": cls.RECOMMENDED_PROVIDER,
        }
