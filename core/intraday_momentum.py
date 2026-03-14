"""
Intraday Momentum Module — NZT-48
Gao, Han, Li & Zhou (2018): "Intraday Momentum: The First Half-Hour Return
Predicts the Last Half-Hour Return." Journal of Financial Economics 129(2):394-414.

KEY FINDING: The return of the first 30-minute window (09:30-10:00 ET) is a
statistically significant predictor of the last 30-minute return (15:30-16:00 ET)
for S&P 500 stocks and index ETFs. Sharpe ratio ~1.2-1.4 before costs.
Effect is stronger on high-VIX days.

IMPLEMENTATION:
- At 10:00 ET: compute first-half-hour return
- If |return| > 0.5%: flag direction for end-of-day entry
- At 15:00 ET: enter in direction of first-half-hour return
- Exit at 15:55 ET
- Hard stop: 1.5% adverse move

For 3x leveraged ETPs: underlying return × 3 — so a 0.5% QQQ open
implies ~1.5% directional bias in QQQ3.L.
"""

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

STATE_FILE = "data/intraday_momentum.json"

# Minimum underlying return to trigger signal (Gao et al. threshold)
FIRST_HALF_HOUR_THRESHOLD_PCT = 0.5   # 0.5% in underlying
ETP_THRESHOLD_PCT = 0.3               # Lower threshold for 3x ETPs (already amplified)

# When to take the EOD trade
EOD_ENTRY_HOUR_ET = 15
EOD_ENTRY_MINUTE_ET = 0
EOD_EXIT_HOUR_ET = 15
EOD_EXIT_MINUTE_ET = 55

# Tickers and their underlyings for leveraged ETP lookthrough
UNDERLYING_MAP = {
    "QQQ3.L": "QQQ",   "3LUS.L": "QQQ",   "QQQ5.L": "QQQ",
    "NVD3.L": "NVDA",  "TSL3.L": "TSLA",  "TSM3.L": "TSM",
    "MU2.L":  "MU",    "GPT3.L": "MSFT",
    "QQQS.L": "QQQ",   "3USS.L": "QQQ",   "SP5L.L": "SPY",
    "3SEM.L": "SOX",   # Semiconductor ETF proxy
}

from core.clock import ET_TZ as ET_ZONE, UK_TZ as UK_ZONE


class IntradayMomentumEngine:
    """
    Tracks first-half-hour returns of underlying indices/stocks and
    generates end-of-day entry signals at 15:00 ET.

    Usage in scan loop:
        signal = engine.get_eod_signal("QQQ3.L", current_time_et)
        if signal:
            # signal.direction, signal.confidence_boost, signal.underlying_return
    """

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"first_half_hour": {}, "signals": {}}

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning("IntradayMomentumEngine: save failed: %s", e)

    # ─────────────────────────────────────────────────────────
    # First-half-hour tracking
    # ─────────────────────────────────────────────────────────

    def record_open_price(self, ticker: str, open_price: float, timestamp: Optional[datetime] = None):
        """
        Record the price at market open (09:30 ET for US, 08:00 UK for LSE).
        Called once per ticker per day at open.
        """
        key = ticker
        self.state["first_half_hour"].setdefault(key, {})
        self.state["first_half_hour"][key]["open_price"] = open_price
        self.state["first_half_hour"][key]["date"] = date.today().isoformat()
        self._save_state()

    def record_half_hour_price(self, ticker: str, price_at_halfhour: float, timestamp: Optional[datetime] = None):
        """
        Record the price at 10:00 ET / 08:30 UK — end of first half-hour.
        Computes and stores the first-half-hour return.
        """
        key = ticker
        entry = self.state["first_half_hour"].get(key, {})
        open_price = entry.get("open_price")
        if not open_price or open_price <= 0:
            logger.debug("IntradayMomentum: no open price recorded for %s", ticker)
            return

        entry_date = entry.get("date", "")
        if entry_date != date.today().isoformat():
            logger.debug("IntradayMomentum: stale open price for %s (date mismatch)", ticker)
            return

        return_pct = (price_at_halfhour - open_price) / open_price * 100
        self.state["first_half_hour"][key]["halfhour_price"] = price_at_halfhour
        self.state["first_half_hour"][key]["return_pct"] = round(return_pct, 4)
        self.state["first_half_hour"][key]["halfhour_recorded"] = True

        logger.info(
            "IntradayMomentum: %s first-half-hour return = %+.3f%%",
            ticker, return_pct,
        )
        self._save_state()

    def get_first_halfhour_return(self, ticker: str) -> Optional[float]:
        """Returns today's first-half-hour return % or None if not recorded."""
        entry = self.state["first_half_hour"].get(ticker, {})
        if entry.get("date") != date.today().isoformat():
            return None
        return entry.get("return_pct")

    # ─────────────────────────────────────────────────────────
    # EOD signal generation
    # ─────────────────────────────────────────────────────────

    def get_eod_signal(self, ticker: str, current_time_et: Optional[datetime] = None) -> Optional[dict]:
        """
        Returns EOD signal dict if conditions are met, else None.

        Signal conditions:
        1. Current time is in 15:00-15:45 ET window
        2. First-half-hour return was recorded today
        3. |return| > threshold for this ticker type

        Returns:
            {
                "ticker": str,
                "direction": "LONG" or "SHORT",
                "underlying": str,
                "underlying_return_pct": float,
                "confidence_boost": int,   # +5 to +12
                "enter_by": "15:00 ET",
                "exit_at": "15:55 ET",
                "academic_basis": "Gao et al. (2018)",
            }
        """
        now_et = current_time_et or datetime.now(ET_ZONE)

        # Only in EOD window: 15:00-15:45 ET
        if not (EOD_ENTRY_HOUR_ET <= now_et.hour <= 15 and
                (now_et.hour < 15 or now_et.minute <= 45)):
            return None

        # Get underlying ticker for lookthrough
        underlying = UNDERLYING_MAP.get(ticker, ticker.replace(".L", ""))
        fhr = self.get_first_halfhour_return(underlying) or self.get_first_halfhour_return(ticker)

        if fhr is None:
            return None

        is_lse_etp = ticker.endswith(".L")
        threshold = ETP_THRESHOLD_PCT if is_lse_etp else FIRST_HALF_HOUR_THRESHOLD_PCT

        if abs(fhr) < threshold:
            return None

        direction = "LONG" if fhr > 0 else "SHORT"

        # Confidence boost scales with magnitude: +5 at threshold, +12 at 2x threshold
        magnitude_ratio = min(abs(fhr) / threshold, 2.0)
        confidence_boost = int(5 + 7 * (magnitude_ratio - 1.0))
        confidence_boost = max(5, min(12, confidence_boost))

        return {
            "ticker": ticker,
            "direction": direction,
            "underlying": underlying,
            "underlying_return_pct": fhr,
            "etp_leverage": 3 if is_lse_etp else 1,
            "confidence_boost": confidence_boost,
            "enter_by": "15:00 ET",
            "exit_at": "15:55 ET",
            "academic_basis": "Gao, Han, Li & Zhou (2018) — first-half-hour predicts last-half-hour",
        }

    def is_eod_entry_window(self, check_time: Optional[datetime] = None) -> bool:
        """True between 15:00 and 15:45 ET."""
        now_et = check_time or datetime.now(ET_ZONE)
        return now_et.hour == 15 and now_et.minute <= 45

    def is_eod_exit_time(self, check_time: Optional[datetime] = None) -> bool:
        """True at/after 15:55 ET — force-close any EOD intraday momentum positions."""
        now_et = check_time or datetime.now(ET_ZONE)
        return now_et.hour == 15 and now_et.minute >= 55

    # ─────────────────────────────────────────────────────────
    # LSE equivalent: UK first-half-hour (08:00-08:30 BST)
    # ─────────────────────────────────────────────────────────

    def is_uk_halfhour_recording_window(self) -> bool:
        """True at 08:30 BST — record UK first-half-hour for LSE ETPs."""
        now_uk = datetime.now(UK_ZONE)
        return now_uk.hour == 8 and 28 <= now_uk.minute <= 32

    def is_uk_open_recording_window(self) -> bool:
        """True at 08:00-08:02 BST — record open prices for LSE ETPs."""
        now_uk = datetime.now(UK_ZONE)
        return now_uk.hour == 8 and now_uk.minute <= 2

    def get_telegram_note(self, ticker: str) -> str:
        fhr = self.get_first_halfhour_return(ticker)
        underlying = UNDERLYING_MAP.get(ticker, ticker.replace(".L", ""))
        if fhr is None:
            fhr = self.get_first_halfhour_return(underlying)
        if fhr is None:
            return ""

        threshold = ETP_THRESHOLD_PCT if ticker.endswith(".L") else FIRST_HALF_HOUR_THRESHOLD_PCT
        if abs(fhr) < threshold:
            return ""

        direction = "BULLISH ⬆️" if fhr > 0 else "BEARISH ⬇️"
        return (
            f"⏰ Intraday Momentum ({underlying}): {fhr:+.2f}% first-half-hour → "
            f"{direction} EOD signal for {ticker} (enter 15:00 ET, exit 15:55 ET)"
        )
