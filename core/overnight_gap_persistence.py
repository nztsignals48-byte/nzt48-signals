"""
Overnight Gap Persistence — NZT-48 Academic Signal Module
Lou, Polk & Sornette (2013): gaps are NOT mean-reverting for leveraged ETPs.
Gap up → trend up intraday 68% of the time for leveraged products.
Also tracks Opening Range Breakout (ORB) — first 30-minute high/low.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Gap thresholds
GAP_SIGNIFICANT_PCT = 1.5    # Gaps above this % are significant
GAP_EXHAUSTION_PCT = 3.0     # Gaps above this + low RVOL = exhaustion signal
GAP_EXHAUSTION_RVOL = 1.0    # RVOL threshold for exhaustion detection

# Confidence adjustments
GAP_GO_BOOST = 7             # Strong gap continuation signal
GAP_EXHAUSTION_PENALTY = -5  # Exhaustion — possible fade
ORB_BREAKOUT_BOOST = 10      # Opening Range Breakout confirmation

_LSE_TO_UNDERLYING = {
    "QQQ3.L": "QQQ", "3LUS.L": "QQQ", "QQQ5.L": "QQQ", "QQQS.L": "QQQ",
    "SP5L.L": "SPY", "3USS.L": "SPY",
    "GPT3.L": "MSFT", "NVD3.L": "NVDA", "TSL3.L": "TSLA",
    "TSM3.L": "TSM", "MU2.L": "MU", "3SEM.L": "SMH",
}


class OvernightGapPersistence:
    """
    Monitors overnight gaps and Opening Range Breakouts.

    Lou et al. (2013) key finding for leveraged ETPs:
    - Gap > +1.5%: BULLISH continuation (+7 conf)
    - Gap > -1.5%: Wait 09:30 confirmation; inverse ETPs may benefit
    - Gap > +3.0% AND RVOL < 1.0: Exhaustion (-5 conf — possible fade)
    - ORB breakout with volume: +10 conf (institutional conviction)
    """

    def __init__(self):
        self._gap_cache: dict = {}    # ticker → {gap_pct, gap_type, ts}
        self._orb_cache: dict = {}    # ticker → {high, low, ts}

    def record_gap(self, ticker: str, open_price: float, prev_close: float,
                   rvol: float = 1.0) -> dict:
        """
        Records and classifies an overnight gap.
        Call at session open (09:00 UK for LSE, 09:30 ET for US).

        Returns: {gap_pct, gap_type, confidence_adjustment, is_exhaustion}
        """
        if prev_close <= 0 or open_price <= 0:
            return {"gap_pct": 0, "gap_type": "NO_DATA", "confidence_adjustment": 0}

        gap_pct = ((open_price - prev_close) / prev_close) * 100
        abs_gap = abs(gap_pct)

        is_bullish_gap = gap_pct > GAP_SIGNIFICANT_PCT
        is_bearish_gap = gap_pct < -GAP_SIGNIFICANT_PCT
        is_exhaustion = abs_gap > GAP_EXHAUSTION_PCT and rvol < GAP_EXHAUSTION_RVOL

        if is_exhaustion:
            gap_type = "GAP_EXHAUSTION"
            conf_adj = GAP_EXHAUSTION_PENALTY
        elif is_bullish_gap:
            gap_type = "GAP_AND_GO"
            conf_adj = GAP_GO_BOOST
        elif is_bearish_gap:
            # For inverse ETPs, a down gap means LONG opportunity
            is_inverse = any(t in ticker for t in ["QQQS", "3USS", "NVDS", "TSLS"])
            if is_inverse:
                gap_type = "GAP_DOWN_INVERSE_LONG"
                conf_adj = GAP_GO_BOOST
            else:
                gap_type = "GAP_DOWN_WAIT"
                conf_adj = 0  # Wait for confirmation — no boost yet
        else:
            gap_type = "GAP_NORMAL"
            conf_adj = 0

        result = {
            "ticker": ticker,
            "gap_pct": round(gap_pct, 3),
            "gap_type": gap_type,
            "confidence_adjustment": conf_adj,
            "is_exhaustion": is_exhaustion,
            "rvol": rvol,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self._gap_cache[ticker] = result
        return result

    def record_orb(self, ticker: str, first_30min_high: float, first_30min_low: float) -> None:
        """
        Records Opening Range (first 30 minutes high/low).
        Used to detect ORB breakouts during the main session.
        """
        self._orb_cache[ticker] = {
            "high": first_30min_high,
            "low": first_30min_low,
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    def check_orb_breakout(self, ticker: str, current_price: float, volume_ratio: float = 1.0) -> dict:
        """
        Checks if current price has broken out of the Opening Range.
        Volume confirmation (RVOL > 1.0) required for full boost.

        Returns: {breakout: bool, direction: str, confidence_adjustment: int}
        """
        orb = self._orb_cache.get(ticker)
        if not orb:
            return {"breakout": False, "direction": "NONE", "confidence_adjustment": 0}

        if current_price > orb["high"] and volume_ratio >= 1.0:
            return {
                "breakout": True, "direction": "LONG",
                "confidence_adjustment": ORB_BREAKOUT_BOOST,
                "orb_high": orb["high"], "orb_low": orb["low"],
            }
        elif current_price < orb["low"] and volume_ratio >= 1.0:
            return {
                "breakout": True, "direction": "SHORT",
                "confidence_adjustment": -ORB_BREAKOUT_BOOST,
                "orb_high": orb["high"], "orb_low": orb["low"],
            }
        return {"breakout": False, "direction": "NONE", "confidence_adjustment": 0}

    def get_gap_signal(self, ticker: str) -> dict:
        """Returns cached gap signal for ticker, or neutral if no data."""
        return self._gap_cache.get(ticker, {
            "ticker": ticker, "gap_type": "NO_DATA", "confidence_adjustment": 0
        })

    def get_confidence_adjustment(self, ticker: str) -> int:
        """Returns confidence adjustment from gap analysis for hot path use."""
        return self.get_gap_signal(ticker).get("confidence_adjustment", 0)

    def get_telegram_note(self, ticker: str) -> str:
        gap = self.get_gap_signal(ticker)
        gap_type = gap.get("gap_type", "NO_DATA")
        gap_pct = gap.get("gap_pct", 0)
        adj = gap.get("confidence_adjustment", 0)
        emoji = "🚀" if adj > 0 else "⚠️" if adj < 0 else "➡️"
        return f"{emoji} Gap {ticker}: {gap_pct:+.2f}% → {gap_type} (conf {adj:+d})"
