"""
Order Flow Imbalance (OFI) — NZT-48 Academic Signal Module
Chordia & Subrahmanyam (2004): volume-weighted buy/sell pressure.
Uses yfinance 1m bars to approximate tick direction via price change sign.
High OFI → strong directional conviction → confidence boost.
"""

import logging
from datetime import datetime, date, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# OFI thresholds
OFI_BULLISH_THRESHOLD = 0.30   # > +0.30 = strong buy pressure
OFI_BEARISH_THRESHOLD = -0.30  # < -0.30 = strong sell pressure
OFI_CONFIDENCE_BOOST = 8       # ±8 confidence points
OFI_WINDOW_MINUTES = 30        # Rolling window for OFI calculation

# Map LSE ETPs to their US underlyings for 1m data
_LSE_TO_UNDERLYING = {
    "QQQ3.L": "QQQ", "3LUS.L": "QQQ", "QQQ5.L": "QQQ", "QQQS.L": "QQQ",
    "SP5L.L": "SPY", "3USS.L": "SPY",
    "GPT3.L": "MSFT", "NVD3.L": "NVDA", "TSL3.L": "TSLA",
    "TSM3.L": "TSM", "MU2.L": "MU", "3SEM.L": "SMH",
}


class OrderFlowImbalance:
    """
    Computes OFI using yfinance 1-minute bars as tick direction proxy.

    OFI = Σ(uptick_vol - downtick_vol) / total_vol over last N minutes
    Uptick: close > prev_close (net buy pressure)
    Downtick: close < prev_close (net sell pressure)

    Signal:
      OFI > +0.30: BULLISH (+8 confidence)
      OFI < -0.30: BEARISH (-8 confidence)
      [-0.30, +0.30]: NEUTRAL (0 adjustment)
    """

    def __init__(self):
        self._cache: dict = {}  # ticker → {ofi, ts, direction}

    def compute_ofi(self, ticker: str, window_minutes: int = OFI_WINDOW_MINUTES) -> Optional[float]:
        """
        Computes OFI score [-1.0, +1.0] for given ticker.
        Returns None on data failure.
        """
        underlying = _LSE_TO_UNDERLYING.get(ticker, ticker.replace(".L", ""))

        try:
            import yfinance as yf
            df = yf.Ticker(underlying).history(period="1d", interval="1m")
            if df is None or df.empty or len(df) < 2:
                return None

            # Take last N minutes
            df = df.tail(window_minutes).copy()
            df = df.reset_index()

            if len(df) < 2:
                return None

            buy_vol = 0.0
            sell_vol = 0.0
            total_vol = 0.0

            for i in range(1, len(df)):
                vol = float(df["Volume"].iloc[i] or 0)
                close = float(df["Close"].iloc[i])
                prev_close = float(df["Close"].iloc[i - 1])
                total_vol += vol

                if close > prev_close:
                    buy_vol += vol
                elif close < prev_close:
                    sell_vol += vol
                # Equal: neutral — not counted

            if total_vol <= 0:
                return None

            ofi = (buy_vol - sell_vol) / total_vol
            return round(ofi, 4)

        except Exception as e:
            logger.debug("OFI compute failed for %s: %s", ticker, e)
            return None

    def get_signal(self, ticker: str) -> dict:
        """
        Returns OFI signal dict with direction and confidence adjustment.
        Caches result for 5 minutes.
        """
        now = datetime.now(timezone.utc)
        cached = self._cache.get(ticker)
        if cached and (now - cached["ts"]).total_seconds() < 300:
            return cached

        ofi = self.compute_ofi(ticker)
        if ofi is None:
            result = {"ticker": ticker, "ofi": None, "direction": "NEUTRAL", "confidence_adjustment": 0}
        elif ofi > OFI_BULLISH_THRESHOLD:
            result = {"ticker": ticker, "ofi": ofi, "direction": "BULLISH", "confidence_adjustment": OFI_CONFIDENCE_BOOST}
        elif ofi < OFI_BEARISH_THRESHOLD:
            result = {"ticker": ticker, "ofi": ofi, "direction": "BEARISH", "confidence_adjustment": -OFI_CONFIDENCE_BOOST}
        else:
            result = {"ticker": ticker, "ofi": ofi, "direction": "NEUTRAL", "confidence_adjustment": 0}

        result["ts"] = now
        self._cache[ticker] = result
        return result

    def get_confidence_adjustment(self, ticker: str) -> int:
        """Returns confidence adjustment for use in S15/S16 hot path."""
        return self.get_signal(ticker).get("confidence_adjustment", 0)

    def get_telegram_note(self, ticker: str) -> str:
        sig = self.get_signal(ticker)
        ofi = sig.get("ofi")
        if ofi is None:
            return f"📊 OFI {ticker}: no data"
        direction = sig["direction"]
        adj = sig["confidence_adjustment"]
        emoji = "🟢" if adj > 0 else "🔴" if adj < 0 else "⚪"
        return f"{emoji} OFI {ticker}: {ofi:+.3f} → {direction} (conf {adj:+d})"
