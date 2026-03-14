"""
RC-07b -- Earnings Fade Gate
===========================
Implements the Buy-the-Rumour / Sell-the-News protection protocol.

Academic basis:
  Kim & Verrecchia (1991) -- pre-announcement informed trading creates run-ups
  Bartov, Givoly & Hayn (2000) -- beat-and-fall is rational when beat < implied move
  Frazzini & Lamont (2006) -- retail crowding pre-catalyst = institutional exit ramp

Rule:
  If a ticker has rallied >= 8% in the 10 sessions before a scheduled earnings date:
    - No new LONG entry in the 48h pre-announcement window
    - Existing longs: stop ratcheted to lock >= +1% profit
    - Post-beat-fall: confirmed fade signal -> SHORT via inverse ETP if available
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inverse ETP map: underlying symbol -> LSE inverse ETP
# ---------------------------------------------------------------------------
_INVERSE_ETP_MAP: dict[str, str | None] = {
    "NVDA":   "QQQS.L",
    "TSLA":   "3USS.L",
    "QQQ":    "QQQS.L",
    "NDX":    "QQQS.L",
    "QQQ3.L": "QQQS.L",
    "SPY":    "3USS.L",
    "SPX":    "3USS.L",
    "SP5L.L": "3USS.L",
    "AMD":    None,
    "ARM":    None,
}

_RUNUP_THRESHOLD_PCT: float = 8.0
_EARNINGS_WINDOW_HOURS: int = 48
_FADE_PRICE_FALL_PCT: float = -1.5
_RUNUP_SESSIONS: int = 10


class EarningsFadeGate:
    """RC-07b: Earnings fade risk filter and fade-signal generator.

    Implements the Buy-the-Rumour / Sell-the-News protocol.  All vetoes
    are appended to data/fade_vetoes.jsonl for post-session review.
    """

    def __init__(
        self,
        data_path: str = "data/earnings_runup_scores.json",
        finnhub_api_key: str | None = None,
    ) -> None:
        """Initialise the gate.

        Args:
            data_path:        Path to the JSON file storing pre-computed
                              10-session run-up scores.
            finnhub_api_key:  Optional Finnhub key forwarded to CalendarFeed.
                              Falls back to the FINNHUB_API_KEY env var.
        """
        self._data_path = data_path
        _dir = os.path.dirname(data_path) or "data"
        os.makedirs(_dir, exist_ok=True)
        self._veto_log_path = os.path.join(_dir, "fade_vetoes.jsonl")
        self._calendar: Any | None = None
        self._finnhub_key = finnhub_api_key or os.environ.get("FINNHUB_API_KEY", "")
        self._scores: dict[str, Any] = self.load_scores()
        logger.info(
            "EarningsFadeGate initialised | data_path=%s | scores_loaded=%d",
            self._data_path, len(self._scores),
        )

    def _get_calendar(self) -> Any:
        """Lazily initialise CalendarFeed."""
        if self._calendar is None:
            try:
                from feeds.calendar_feed import CalendarFeed
                self._calendar = CalendarFeed(finnhub_api_key=self._finnhub_key)
                logger.debug("CalendarFeed initialised inside EarningsFadeGate")
            except ImportError as exc:
                logger.error("Cannot import CalendarFeed: %s", exc)
                raise
        return self._calendar

    def _hours_to_earnings(self, ticker: str) -> int | None:
        """Return hours until next earnings, or None if not within 7 days."""
        try:
            calendar = self._get_calendar()
            events = calendar.get_earnings_calendar([ticker], weeks_ahead=1)
            now_utc = datetime.now(timezone.utc)
            nearest_hours: int | None = None
            date_fmt = "%Y-%m-%d"

            for ev in events:
                if ev.get("ticker", "").upper() != ticker.upper():
                    continue
                date_str = ev.get("date", "")
                if not date_str:
                    continue
                ev_time = ev.get("time", "--").upper()
                try:
                    ev_date = datetime.strptime(date_str, date_fmt).replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    logger.debug("Cannot parse date %s for %s", date_str, ticker)
                    continue

                if ev_time in ("BMO", "BEFORE MARKET OPEN"):
                    ev_dt = ev_date.replace(hour=13, minute=30)
                elif ev_time in ("AMC", "AFTER MARKET CLOSE"):
                    ev_dt = ev_date.replace(hour=20, minute=0)
                else:
                    ev_dt = ev_date.replace(hour=13, minute=30)

                delta_hours = int((ev_dt - now_utc).total_seconds() / 3600)
                if delta_hours < 0:
                    continue
                if nearest_hours is None or delta_hours < nearest_hours:
                    nearest_hours = delta_hours

            return nearest_hours
        except Exception as exc:
            logger.warning("_hours_to_earnings(%s) error: %s", ticker, exc)
            return None

    def _append_veto_log(
        self,
        ticker: str,
        runup_pct: float,
        earnings_within_hours: int | None,
        action: str,
    ) -> None:
        """Append one JSON line per veto to data/fade_vetoes.jsonl.

        Fields: timestamp, ticker, runup_pct, earnings_within_hours, action.
        action is one of LONG_BLOCKED or FADE_SIGNAL.
        """
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ticker": ticker,
            "runup_pct": round(runup_pct, 4),
            "earnings_within_hours": earnings_within_hours,
            "action": action,
        }
        try:
            _d = os.path.dirname(self._veto_log_path) or "data"
            os.makedirs(_d, exist_ok=True)
            with open(self._veto_log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            logger.debug("Veto logged: %s", record)
        except OSError as exc:
            logger.error("Failed to write veto log: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_runup_score(self, ticker: str, price_history: list[float]) -> float:
        """Compute 10-session percentage run-up from a list of closing prices.

        If fewer than 10 sessions are available, uses all available sessions.
        Returns 0.0 if fewer than 2 prices are supplied.

        Args:
            ticker:         Symbol (used for logging only).
            price_history:  Daily closing prices, oldest first.

        Returns:
            Percentage change over the last 10 sessions (or fewer).
        """
        if not price_history or len(price_history) < 2:
            logger.debug(
                "compute_runup_score(%s): insufficient data (%d bars)",
                ticker, len(price_history) if price_history else 0,
            )
            return 0.0

        window = price_history[-(_RUNUP_SESSIONS + 1):]
        start_price = window[0]
        end_price = window[-1]

        if start_price <= 0:
            logger.warning(
                "compute_runup_score(%s): invalid start price %s", ticker, start_price,
            )
            return 0.0

        runup_pct = ((end_price - start_price) / start_price) * 100.0
        logger.debug(
            "compute_runup_score(%s): start=%.4f end=%.4f runup=%.2f%% (sessions=%d)",
            ticker, start_price, end_price, runup_pct, len(window) - 1,
        )
        return round(runup_pct, 4)

    def is_fade_risk(self, ticker: str, runup_pct: float | None = None) -> bool:
        """Return True if ticker is in a pre-earnings fade-risk zone.

        Both conditions must be met:
          1. Earnings scheduled within 48 hours.
          2. 10-session run-up >= 8%.

        When both are met, a LONG_BLOCKED veto is appended to fade_vetoes.jsonl.

        Args:
            ticker:     Ticker symbol.
            runup_pct:  If provided, used directly; otherwise loaded from the
                        scores file populated by compute_all_runup_scores.

        Returns:
            True => block all new LONG entries for this ticker.
        """
        ticker = ticker.upper()

        if runup_pct is None:
            runup_pct = self._scores.get(ticker, {}).get("runup_pct", 0.0)

        hours = self._hours_to_earnings(ticker)
        within_window = hours is not None and hours <= _EARNINGS_WINDOW_HOURS

        if not within_window:
            logger.debug(
                "is_fade_risk(%s): earnings not within %dh (hours=%s)",
                ticker, _EARNINGS_WINDOW_HOURS, hours,
            )
            return False

        if runup_pct < _RUNUP_THRESHOLD_PCT:
            logger.debug(
                "is_fade_risk(%s): runup=%.2f%% < %.1f%% threshold",
                ticker, runup_pct, _RUNUP_THRESHOLD_PCT,
            )
            return False

        logger.warning(
            "RC-07b FADE RISK: %s | runup=%.2f%% >= %.1f%% | earnings in %sh -> LONG_BLOCKED",
            ticker, runup_pct, _RUNUP_THRESHOLD_PCT, hours,
        )
        self._append_veto_log(
            ticker=ticker, runup_pct=runup_pct,
            earnings_within_hours=hours, action="LONG_BLOCKED",
        )
        return True

    def is_post_beat_fade(
        self,
        ticker: str,
        beat_pct: float,
        price_change_pct: float,
    ) -> bool:
        """Detect a confirmed post-earnings fade (beat-and-fall pattern).

        Academic basis: Bartov, Givoly & Hayn (2000) -- when the EPS beat is
        smaller than the pre-announcement implied move, institutional participants
        sell into the beat, pushing the stock lower despite positive EPS surprise.

        Args:
            ticker:            Ticker symbol.
            beat_pct:          EPS beat magnitude as a positive percentage.
                               e.g. 5.0 means beat by 5%.  Must be > 0.
            price_change_pct:  Actual post-announcement price change (%).
                               Negative means the stock fell.

        Returns:
            True if beat_pct > 0 AND price_change_pct < -1.5%.
        """
        ticker = ticker.upper()

        if beat_pct <= 0:
            logger.debug(
                "is_post_beat_fade(%s): beat_pct=%.2f <= 0, not a beat", ticker, beat_pct,
            )
            return False

        if price_change_pct >= _FADE_PRICE_FALL_PCT:
            logger.debug(
                "is_post_beat_fade(%s): price_change=%.2f%% >= %.1f%% (no fade)",
                ticker, price_change_pct, _FADE_PRICE_FALL_PCT,
            )
            return False

        logger.warning(
            "RC-07b FADE CONFIRMED: %s beat by %.2f%% but fell %.2f%% -> FADE_SIGNAL",
            ticker, beat_pct, price_change_pct,
        )
        self._append_veto_log(
            ticker=ticker,
            runup_pct=self._scores.get(ticker, {}).get("runup_pct", 0.0),
            earnings_within_hours=0,
            action="FADE_SIGNAL",
        )
        return True

    def get_inverse_etp(self, ticker: str) -> str | None:
        """Return the ISA-eligible inverse ETP for a given underlying.

        All products are UK ISA-eligible ETPs traded on the LSE.

        Mapping::

          NVDA / QQQ / NDX / QQQ3.L  ->  QQQS.L  (3x short Nasdaq)
          TSLA / SPY / SPX / SP5L.L  ->  3USS.L  (3x short US large cap)
          AMD / ARM                   ->  None

        Args:
            ticker: Underlying equity or ETP symbol.

        Returns:
            LSE inverse ETP symbol, or None if no suitable inverse exists.
        """
        result = _INVERSE_ETP_MAP.get(ticker.upper())
        logger.debug("get_inverse_etp(%s) -> %s", ticker.upper(), result)
        return result

    def save_scores(self, scores: dict[str, Any]) -> None:
        """Persist run-up scores to the data_path JSON file.

        Also updates the in-memory cache so subsequent is_fade_risk calls
        are immediately consistent without requiring a reload.
        """
        try:
            _d = os.path.dirname(self._data_path) or "data"
            os.makedirs(_d, exist_ok=True)
            with open(self._data_path, "w", encoding="utf-8") as fh:
                json.dump(scores, fh, indent=2)
            self._scores = scores
            logger.info("Saved %d run-up scores to %s", len(scores), self._data_path)
        except OSError as exc:
            logger.error("Failed to save run-up scores: %s", exc)

    def load_scores(self) -> dict[str, Any]:
        """Load run-up scores from the data_path JSON file.

        Returns:
            Dict of scores, or {} if the file does not exist or is corrupt.
        """
        if not os.path.exists(self._data_path):
            logger.debug("Scores file not found at %s -- starting empty", self._data_path)
            return {}
        try:
            with open(self._data_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            logger.info("Loaded %d run-up scores from %s", len(data), self._data_path)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Failed to load scores from %s: %s -- returning empty",
                self._data_path, exc,
            )
            return {}


# ---------------------------------------------------------------------------
# Standalone batch scorer
# ---------------------------------------------------------------------------

def compute_all_runup_scores(tickers: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch price history and compute 10-session run-up for every ticker.

    Uses yfinance to fetch 15 calendar days of daily closes (at least 10
    trading sessions), computes run-up per ticker, checks earnings proximity
    via CalendarFeed, and saves results to data/earnings_runup_scores.json.

    Args:
        tickers: List of ticker symbols to evaluate.

    Returns:
        Dict mapping ticker -> {
            "runup_pct":             float,
            "earnings_within_hours": int | None,
            "fade_risk":             bool,
        }
    """
    results: dict[str, dict[str, Any]] = {}
    gate = EarningsFadeGate()

    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance is not installed -- cannot compute run-up scores")
        return results

    logger.info(
        "compute_all_runup_scores: fetching price history for %d tickers", len(tickers),
    )
    date_fmt = "%Y-%m-%d"

    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="15d", interval="1d", auto_adjust=True)

            if hist.empty or "Close" not in hist.columns:
                logger.warning("No price history returned for %s", ticker)
                results[ticker] = {"runup_pct": 0.0, "earnings_within_hours": None, "fade_risk": False}
                continue

            closes: list[float] = [float(p) for p in hist["Close"].dropna().tolist()]

            if len(closes) < 2:
                logger.warning("Insufficient closes for %s (%d bars)", ticker, len(closes))
                results[ticker] = {"runup_pct": 0.0, "earnings_within_hours": None, "fade_risk": False}
                continue

            runup_pct = gate.compute_runup_score(ticker, closes)

            earnings_within_hours: int | None = None
            try:
                calendar = gate._get_calendar()
                events = calendar.get_earnings_calendar([ticker], weeks_ahead=1)
                now_utc = datetime.now(timezone.utc)

                for ev in events:
                    ev_ticker = ev.get("ticker", "").upper()
                    if ev_ticker != ticker.upper():
                        continue
                    date_str = ev.get("date", "")
                    if not date_str:
                        continue
                    try:
                        ev_dt = datetime.strptime(date_str, date_fmt).replace(
                            tzinfo=timezone.utc, hour=13, minute=30,
                        )
                    except ValueError:
                        continue
                    delta_h = int((ev_dt - now_utc).total_seconds() / 3600)
                    if delta_h < 0:
                        continue
                    if earnings_within_hours is None or delta_h < earnings_within_hours:
                        earnings_within_hours = delta_h
            except Exception as exc:
                logger.debug("Earnings calendar lookup failed for %s: %s", ticker, exc)

            within_48h = (
                earnings_within_hours is not None
                and earnings_within_hours <= _EARNINGS_WINDOW_HOURS
            )
            fade_risk = runup_pct >= _RUNUP_THRESHOLD_PCT and within_48h

            results[ticker] = {
                "runup_pct": runup_pct,
                "earnings_within_hours": earnings_within_hours,
                "fade_risk": fade_risk,
            }
            logger.info(
                "  %s | runup=%.2f%% | earnings_in=%s h | fade_risk=%s",
                ticker, runup_pct,
                str(earnings_within_hours) if earnings_within_hours is not None else "N/A",
                fade_risk,
            )

        except Exception as exc:
            logger.error("compute_all_runup_scores: error processing %s: %s", ticker, exc)
            results[ticker] = {"runup_pct": 0.0, "earnings_within_hours": None, "fade_risk": False}

    gate.save_scores(results)
    logger.info(
        "compute_all_runup_scores: complete -- %d tickers scored, saved to %s",
        len(results), gate._data_path,
    )
    return results
