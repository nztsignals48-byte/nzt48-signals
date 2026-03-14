"""
data_hub/sources/validator_source.py
======================================
Validator source stub (secondary feed for comparison).
STATUS: INACTIVE until Polygon/Tiingo/Databento credentials are set.
Uses same interface as IBKRSource / YFinanceSource.
"""
from __future__ import annotations
import logging
from typing import Optional

logger = logging.getLogger("nzt48.data_hub.validator")

_TOLERANCE = {
    "close_pct":  0.02,   # 2% tolerance for close price
    "volume_pct": 0.30,   # 30% tolerance for volume
    "bar_count":  2,      # allow 2 bar count difference
}


class ValidatorSource:
    """
    Secondary validation feed.
    When available: compare close/range/volume against primary source.
    Disagreement > tolerance -> mark UNVERIFIED.
    """
    NAME = "polygon"   # can be swapped to tiingo/databento
    IS_TRUTH = False
    IS_AVAILABLE = False
    REQUIRED_CONFIG_KEY = "data.polygon_api_key"
    RECOMMENDED_PROVIDER = "polygon.io / tiingo.com / databento.com"

    def fetch_bars(self, ticker: str, period: str = "5d", interval: str = "1h") -> None:
        """Returns None until API key is configured."""
        logger.debug("[VALIDATOR] fetch_bars stub: %s — not configured", ticker)
        return None

    def compare(
        self,
        ticker: str,
        primary_close: float,
        primary_volume: float,
        primary_n_bars: int,
    ) -> dict:
        """
        Compare primary vs validator feed.
        Returns comparison result dict.
        When source is unavailable: returns unverified=True, agree=None.
        """
        if not self.IS_AVAILABLE:
            return {
                "ticker":        ticker,
                "unverified":    True,
                "agree":         None,
                "reason":        f"Validator source {self.NAME!r} not configured",
                "close_delta":   None,
                "volume_delta":  None,
                "bar_delta":     None,
                "reliability_penalty": 0.05,   # small penalty for unvalidated data
            }
        # Stub: would call API and compare values
        return {
            "ticker":       ticker,
            "unverified":   False,
            "agree":        True,
            "close_delta":  0.0,
            "volume_delta": 0.0,
            "bar_delta":    0,
            "reliability_penalty": 0.0,
        }

    @classmethod
    def availability(cls) -> dict:
        return {
            "name": cls.NAME,
            "is_truth": cls.IS_TRUTH,
            "is_available": cls.IS_AVAILABLE,
            "required_config_key": cls.REQUIRED_CONFIG_KEY,
            "recommended_provider": cls.RECOMMENDED_PROVIDER,
        }
