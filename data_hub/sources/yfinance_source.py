"""
data_hub/sources/yfinance_source.py
=====================================
yfinance fallback source (NEVER used as truth).
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional
import pandas as pd

logger = logging.getLogger("nzt48.data_hub.yfinance")


class YFinanceSource:
    """Fallback data source. Never used as truth — always labeled as such."""
    NAME = "yfinance"
    IS_TRUTH = False
    IS_AVAILABLE = True

    def fetch_bars(
        self,
        ticker: str,
        period: str = "5d",
        interval: str = "1h",
    ) -> Optional[pd.DataFrame]:
        try:
            import yfinance as yf
            df = yf.download(ticker, period=period, interval=interval,
                             progress=False, auto_adjust=True)
            if df is None or df.empty:
                return None
            # Normalise column names
            df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                          for c in df.columns]
            df.index.name = "timestamp"
            return df
        except Exception as exc:
            logger.debug("[YF] fetch failed for %s: %s", ticker, exc)
            return None

    def fetch_quote(self, ticker: str) -> Optional[dict]:
        """Returns proxy quote from last bar (bid/ask not available via yfinance)."""
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            info = t.fast_info
            last = getattr(info, "last_price", None) or getattr(info, "regular_market_price", None)
            if not last:
                return None
            # Proxy spread from typical ETP microstructure
            spread_proxy = last * 0.0010   # ~10bps proxy
            return {
                "ticker": ticker,
                "bid": last - spread_proxy / 2,
                "ask": last + spread_proxy / 2,
                "last": last,
                "spread_bps": 10.0,
                "source": "proxy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            logger.debug("[YF] quote failed for %s: %s", ticker, exc)
            return None

    @classmethod
    def availability(cls) -> dict:
        return {
            "name": cls.NAME,
            "is_truth": cls.IS_TRUTH,
            "is_available": cls.IS_AVAILABLE,
            "note": "Fallback only. Not used as truth. No bid/ask available.",
        }
