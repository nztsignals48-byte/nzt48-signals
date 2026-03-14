"""
SUE / PEAD Scorer — NZT-48
Bernard & Thomas (1989): "Post-Earnings-Announcement Drift: Delayed Price Response
or Risk Premium?" Journal of Accounting and Economics 11(2-3):375-413.
Ball & Brown (1968): "An Empirical Evaluation of Accounting Income Numbers."
Journal of Accounting Research 6(2):159-178.

KEY FINDING: Standardised Unexpected Earnings (SUE) score predicts post-announcement
drift over 60 days. SUE > 2.0 = top decile = +4.2% average 60-day abnormal return
(large-caps: +1.8-2.5%). 40% of the drift occurs in the FIRST 5 TRADING DAYS.
For leveraged ETPs: use 5-day window only (volatility decay makes longer hold suboptimal).

SUE FORMULA:
  SUE = (Actual EPS - Consensus EPS) / STD(analyst forecast errors over last 4 quarters)

PEAD CONFIDENCE BOOSTS:
  SUE > 3.0:  +15 confidence (top decile — strongest drift signal)
  SUE 2.0-3.0: +10 confidence
  SUE 1.0-2.0: +5 confidence
  SUE < 0.0:  -10 confidence (negative surprise = PEAD short signal)
"""

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "data/sue_pead.json"

# SUE thresholds mapped to confidence boosts
SUE_TIERS = [
    (3.0,  +15, "TOP DECILE"),
    (2.0,  +10, "HIGH"),
    (1.0,  +5,  "MODERATE"),
    (0.0,   0,  "NEUTRAL"),
    (-1.0, -5,  "MISS"),
    (-9.9, -10, "BIG MISS"),
]

# PEAD holding rules
PEAD_PRIMARY_HOLD_DAYS = 5    # capture 40% of drift rapidly
PEAD_EXTENDED_HOLD_DAYS = 20  # secondary hold for very high SUE
PEAD_ETP_HOLD_DAYS = 3        # shorter for 3x ETPs (vol drag)

# Days to check for stale data
MAX_PEAD_HOLD_DAYS = 60


class SUEPEADScorer:
    """
    Stores earnings announcements with their SUE scores and provides
    PEAD momentum signals for the following days.

    Integration: call record_earnings() when earnings hit, then
    call get_pead_signal() in every scan cycle — it returns a
    confidence boost and direction if within the PEAD window.
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
        return {"earnings": {}}

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning("SUEPEADScorer: save failed: %s", e)

    # ─────────────────────────────────────────────────────────
    # SUE calculation
    # ─────────────────────────────────────────────────────────

    def compute_sue(
        self,
        actual_eps: float,
        consensus_eps: float,
        forecast_error_std: Optional[float] = None,
        whisper_eps: Optional[float] = None,
    ) -> float:
        """
        Computes SUE (Standardized Unexpected Earnings).

        If forecast_error_std not available, approximates using
        |consensus_eps| * 0.10 (typical analyst error ≈ 10% of consensus).
        Whisper adjustment: if whisper > consensus, the effective 'expected'
        is the whisper (Skinner & Sloan 2002).
        """
        # Use whisper as the expected baseline if available (Skinner & Sloan 2002)
        expected = whisper_eps if whisper_eps is not None else consensus_eps

        surprise = actual_eps - expected

        # Estimate std if not provided
        if forecast_error_std is None or forecast_error_std <= 0:
            forecast_error_std = max(abs(consensus_eps) * 0.10, 0.01)

        sue = surprise / forecast_error_std
        return round(sue, 3)

    def get_sue_tier(self, sue: float) -> tuple[int, str]:
        """Returns (confidence_boost, label) for given SUE score."""
        for threshold, boost, label in SUE_TIERS:
            if sue >= threshold:
                return boost, label
        return -10, "BIG MISS"

    # ─────────────────────────────────────────────────────────
    # Recording earnings
    # ─────────────────────────────────────────────────────────

    def record_earnings(
        self,
        ticker: str,
        actual_eps: float,
        consensus_eps: float,
        announcement_date: Optional[date] = None,
        forecast_error_std: Optional[float] = None,
        whisper_eps: Optional[float] = None,
        beat_pct: Optional[float] = None,  # raw beat % if SUE not computable
    ):
        """
        Record an earnings announcement for PEAD tracking.
        Call immediately after earnings release.
        """
        sue = self.compute_sue(actual_eps, consensus_eps, forecast_error_std, whisper_eps)
        boost, label = self.get_sue_tier(sue)
        d = announcement_date or date.today()

        self.state["earnings"][ticker] = {
            "announcement_date": d.isoformat(),
            "actual_eps": actual_eps,
            "consensus_eps": consensus_eps,
            "whisper_eps": whisper_eps,
            "sue": sue,
            "confidence_boost": boost,
            "label": label,
            "beat_pct": beat_pct,
            "pead_entry_date": d.isoformat(),  # enter next day
            "pead_primary_exit": (d + timedelta(days=PEAD_PRIMARY_HOLD_DAYS)).isoformat(),
            "pead_extended_exit": (d + timedelta(days=PEAD_EXTENDED_HOLD_DAYS)).isoformat(),
        }
        self._save_state()
        logger.info(
            "SUEPEADScorer: %s earnings recorded — SUE=%.2f (%s), conf_boost=%+d",
            ticker, sue, label, boost,
        )

    # ─────────────────────────────────────────────────────────
    # PEAD signal queries
    # ─────────────────────────────────────────────────────────

    def get_pead_signal(self, ticker: str, is_lse_etp: bool = False) -> Optional[dict]:
        """
        Returns PEAD signal if within the drift window, else None.

        PEAD is active from day 1 to day 5 (primary) or day 20 (extended).
        For 3x ETPs (is_lse_etp=True), max hold is 3 days.

        Returns:
            {
                "active": bool,
                "direction": "LONG" | "SHORT",
                "sue": float,
                "label": str,
                "confidence_boost": int,
                "days_since_announcement": int,
                "primary_window": bool,  # True in first 5 days
                "academic_basis": str,
            }
        """
        rec = self.state["earnings"].get(ticker)
        if not rec:
            # Also check if this is a leveraged ETP — check its underlying
            base = ticker.replace(".L", "")
            rec = self.state["earnings"].get(base)

        if not rec:
            return None

        ann_date = date.fromisoformat(rec["announcement_date"])
        days_since = (date.today() - ann_date).days

        max_days = PEAD_ETP_HOLD_DAYS if is_lse_etp else PEAD_PRIMARY_HOLD_DAYS

        if days_since < 0 or days_since > MAX_PEAD_HOLD_DAYS:
            return None

        if days_since > max_days and rec["sue"] < 3.0:
            # Extended hold only for very high SUE (top decile)
            return None

        sue = rec["sue"]
        boost, label = self.get_sue_tier(sue)

        if boost == 0:
            return None  # Neutral — no signal

        direction = "LONG" if sue > 0 else "SHORT"
        primary_window = days_since <= PEAD_PRIMARY_HOLD_DAYS

        return {
            "active": True,
            "direction": direction,
            "sue": sue,
            "label": label,
            "confidence_boost": boost,
            "days_since_announcement": days_since,
            "primary_window": primary_window,
            "announcement_date": rec["announcement_date"],
            "academic_basis": "Bernard & Thomas (1989): PEAD — 40% of drift in first 5 days",
        }

    def is_in_pead_window(self, ticker: str, is_lse_etp: bool = False) -> bool:
        """Returns True if ticker is in an active positive PEAD window."""
        sig = self.get_pead_signal(ticker, is_lse_etp)
        return sig is not None and sig.get("direction") == "LONG"

    def is_in_negative_pead(self, ticker: str, is_lse_etp: bool = False) -> bool:
        """Returns True if ticker missed earnings and is in negative PEAD."""
        sig = self.get_pead_signal(ticker, is_lse_etp)
        return sig is not None and sig.get("direction") == "SHORT"

    def get_all_active_signals(self) -> list:
        """Returns all tickers with active PEAD signals."""
        results = []
        for ticker in self.state["earnings"]:
            sig = self.get_pead_signal(ticker)
            if sig:
                sig["ticker"] = ticker
                results.append(sig)
        return sorted(results, key=lambda x: abs(x["sue"]), reverse=True)

    def get_telegram_note(self, ticker: str) -> str:
        sig = self.get_pead_signal(ticker, is_lse_etp=ticker.endswith(".L"))
        if not sig:
            return ""
        arrow = "⬆️" if sig["direction"] == "LONG" else "⬇️"
        window = "PRIMARY" if sig["primary_window"] else "EXTENDED"
        return (
            f"📈 PEAD {arrow} {ticker}: SUE={sig['sue']:.1f} ({sig['label']}) "
            f"| Day {sig['days_since_announcement']} | {window} window "
            f"| Conf {sig['confidence_boost']:+d} (Bernard & Thomas 1989)"
        )
