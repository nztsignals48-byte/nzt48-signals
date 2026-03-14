"""
Analyst Revision Tracker — NZT-48 Academic Signal Module
Boni (2004) + Womack (1996): analyst EPS/target revisions predict returns.
Upward revisions in past 5 trading days = +6 to +10 confidence boost.
Fresh BUY initiations = +8 confidence.
Data: yfinance info (recommendationMean, targetMeanPrice). Cached 24h.
"""

import logging
import os
import json
from datetime import datetime, date, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "data/analyst_revisions.json"

# Revision thresholds
REVISION_UP_MINOR = 5.0    # % change to count as minor upward revision
REVISION_UP_MAJOR = 10.0   # % change to count as major upward revision
REVISION_DOWN_MINOR = 5.0  # % change to count as downward revision

# Confidence adjustments
CONF_REVISION_UP_MINOR = 6
CONF_REVISION_UP_MAJOR = 10
CONF_BUY_INITIATION = 8
CONF_REVISION_DOWN = -6

# Recommendation mean thresholds (1=Strong Buy, 5=Strong Sell)
RECOMMENDATION_BUY_THRESHOLD = 2.5   # ≤2.5 = Buy/Strong Buy territory

_LSE_TO_UNDERLYING = {
    "QQQ3.L": "QQQ", "3LUS.L": "QQQ", "QQQ5.L": "QQQ", "QQQS.L": "QQQ",
    "SP5L.L": "SPY", "3USS.L": "SPY",
    "GPT3.L": "MSFT", "NVD3.L": "NVDA", "TSL3.L": "TSLA",
    "TSM3.L": "TSM", "MU2.L": "MU", "3SEM.L": "SMH",
}


class AnalystRevisionTracker:
    """
    Tracks analyst consensus changes to detect revision momentum.

    Boni (2004): Upward EPS revisions in the past 5 days significantly
    predict positive abnormal returns over the following 10 trading days.

    Womack (1996): Fresh analyst BUY initiations (first coverage) generate
    +3.8% average excess return in the first month.

    Data source: yfinance Ticker.info — free, sufficient for daily signals.
    Refreshed daily (analysts don't change intraday).
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
        return {"snapshots": {}, "last_update": {}}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self.state_file) if os.path.dirname(self.state_file) else ".", exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning("AnalystRevisionTracker: save failed: %s", e)

    # ─────────────────────────────────────────────────────────
    # Data fetching
    # ─────────────────────────────────────────────────────────

    def fetch_and_store(self, ticker: str) -> Optional[dict]:
        """
        Fetches current analyst data for ticker and stores snapshot.
        Returns current data dict or None on failure.
        """
        underlying = _LSE_TO_UNDERLYING.get(ticker, ticker.replace(".L", ""))

        # Index ETFs don't have analyst coverage
        if underlying in ("QQQ", "SPY", "SMH"):
            return None

        try:
            import yfinance as yf
            info = yf.Ticker(underlying).info
            if not info:
                return None

            rec_mean = info.get("recommendationMean")
            target_mean = info.get("targetMeanPrice")
            target_high = info.get("targetHighPrice")
            target_low = info.get("targetLowPrice")
            num_analysts = info.get("numberOfAnalystOpinions", 0)

            data = {
                "ticker": underlying,
                "date": date.today().isoformat(),
                "recommendation_mean": rec_mean,
                "target_mean_price": target_mean,
                "target_high_price": target_high,
                "target_low_price": target_low,
                "num_analysts": num_analysts,
                "ts": datetime.now(timezone.utc).isoformat(),
            }

            # Store in history
            snaps = self.state["snapshots"].setdefault(underlying, [])
            snaps.append(data)
            # Keep last 10 snapshots (10 trading days)
            if len(snaps) > 10:
                snaps[:] = snaps[-10:]
            self.state["last_update"][underlying] = data["ts"]
            self._save_state()

            logger.debug(
                "AnalystRevision: %s rec=%.1f target=$%.2f analysts=%d",
                underlying,
                rec_mean or 0,
                target_mean or 0,
                num_analysts or 0,
            )
            return data

        except Exception as e:
            logger.debug("AnalystRevisionTracker.fetch_and_store(%s): %s", ticker, e)
            return None

    # ─────────────────────────────────────────────────────────
    # Revision detection
    # ─────────────────────────────────────────────────────────

    def get_revision_signal(self, ticker: str) -> dict:
        """
        Detects revision direction vs snapshot from 5 trading days ago.

        Returns:
          {direction, target_change_pct, rec_change, confidence_adjustment, signal_type}
        """
        underlying = _LSE_TO_UNDERLYING.get(ticker, ticker.replace(".L", ""))
        snaps = self.state["snapshots"].get(underlying, [])

        if len(snaps) < 2:
            return {
                "direction": "NO_DATA",
                "confidence_adjustment": 0,
                "signal_type": "INSUFFICIENT_HISTORY",
            }

        current = snaps[-1]
        # Compare against 5-day-old snapshot (or oldest available)
        lookback_idx = max(0, len(snaps) - 6)
        prior = snaps[lookback_idx]

        current_target = current.get("target_mean_price")
        prior_target = prior.get("target_mean_price")
        current_rec = current.get("recommendation_mean")
        prior_rec = prior.get("recommendation_mean")

        # Target price revision
        target_change_pct = 0.0
        if current_target and prior_target and prior_target > 0:
            target_change_pct = ((current_target - prior_target) / prior_target) * 100

        # Recommendation change (lower = more bullish: 1=StrongBuy, 5=StrongSell)
        rec_change = 0.0
        if current_rec is not None and prior_rec is not None:
            rec_change = prior_rec - current_rec  # Positive = upgraded (more bullish)

        # Classify signal
        conf_adj = 0
        signal_type = "NEUTRAL"

        if target_change_pct >= REVISION_UP_MAJOR or rec_change >= 1.0:
            conf_adj = CONF_REVISION_UP_MAJOR
            signal_type = "STRONG_UPWARD_REVISION"
        elif target_change_pct >= REVISION_UP_MINOR or rec_change >= 0.5:
            conf_adj = CONF_REVISION_UP_MINOR
            signal_type = "UPWARD_REVISION"
        elif target_change_pct <= -REVISION_DOWN_MINOR or rec_change <= -0.5:
            conf_adj = CONF_REVISION_DOWN
            signal_type = "DOWNWARD_REVISION"

        # Fresh BUY initiation: current rec ≤ 2.5 AND prior was None (new coverage)
        if current_rec is not None and current_rec <= RECOMMENDATION_BUY_THRESHOLD and prior_rec is None:
            conf_adj = CONF_BUY_INITIATION
            signal_type = "BUY_INITIATION"

        return {
            "ticker": underlying,
            "direction": "UP" if conf_adj > 0 else "DOWN" if conf_adj < 0 else "NEUTRAL",
            "target_change_pct": round(target_change_pct, 2),
            "rec_change": round(rec_change, 2),
            "current_target": current_target,
            "current_rec": current_rec,
            "num_analysts": current.get("num_analysts"),
            "confidence_adjustment": conf_adj,
            "signal_type": signal_type,
        }

    def get_confidence_adjustment(self, ticker: str) -> int:
        """Returns confidence adjustment for use in S15/S16 hot path."""
        return self.get_revision_signal(ticker).get("confidence_adjustment", 0)

    def update_all(self, tickers: list) -> dict:
        """Fetch and store latest analyst data for all given tickers."""
        results = {}
        for ticker in tickers:
            data = self.fetch_and_store(ticker)
            if data:
                results[ticker] = data
        return results

    # ─────────────────────────────────────────────────────────
    # Telegram
    # ─────────────────────────────────────────────────────────

    def get_telegram_note(self, ticker: str) -> str:
        sig = self.get_revision_signal(ticker)
        adj = sig["confidence_adjustment"]
        signal_type = sig["signal_type"]
        target = sig.get("current_target")
        target_chg = sig.get("target_change_pct", 0)

        if sig["direction"] == "NO_DATA":
            return f"📊 Analyst {ticker}: no revision data"

        emoji = "📈" if adj > 0 else "📉" if adj < 0 else "➡️"
        target_str = f" | target=${target:.2f} ({target_chg:+.1f}%)" if target else ""
        return (
            f"{emoji} Analyst {ticker}: {signal_type}{target_str} "
            f"(conf {adj:+d})"
        )
