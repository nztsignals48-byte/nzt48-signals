"""
IV Crush Monitor — NZT-48 Microstructure Module
Amin & Lee (1997): Options markets signal information before announcements.
Detects pre-event IV inflation and post-announcement IV crush.
Adjusts position sizing and stop width in high-IV environments.
"""

import json
import os
import logging
from datetime import datetime, date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Stocks that have tradeable options (LSE leveraged ETPs do not)
OPTIONS_UNIVERSE = {"NVDA", "TSLA", "AMD", "TSM", "MU", "ARM", "AAPL", "MSFT", "META", "GOOGL"}

STATE_FILE = "data/iv_state.json"


class IVCrushMonitor:
    """
    Monitors implied volatility levels to detect:
    1. Pre-earnings IV inflation → reduce size, widen stops
    2. Post-announcement IV crush → normal sizing resumes
    3. Sustained high-IV environments → structural caution flag

    LSE leveraged ETPs (QQQ3.L etc.) have no options — handled gracefully.
    """

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = self._load_state()

    # ─────────────────────────────────────────────────────────
    # State persistence
    # ─────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"iv_snapshots": {}, "earnings_events": {}}

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning(f"IVCrushMonitor: failed to save state: {e}")

    # ─────────────────────────────────────────────────────────
    # IV data retrieval
    # ─────────────────────────────────────────────────────────

    def get_iv_data(self, ticker: str) -> Optional[dict]:
        """
        Fetch current IV from yfinance options chain.
        Returns dict with avg_iv, iv_percentile (vs 30-day history), implied_move_pct.
        Returns None for LSE tickers (no options).
        """
        # Strip .L suffix — LSE ETPs have no options
        base = ticker.replace(".L", "")
        if base not in OPTIONS_UNIVERSE:
            return None

        try:
            import yfinance as yf
            stock = yf.Ticker(base)
            expirations = stock.options
            if not expirations:
                return None

            # Use nearest expiry (index 0)
            nearest = expirations[0]
            chain = stock.option_chain(nearest)
            calls = chain.calls
            puts = chain.puts

            if calls.empty or puts.empty:
                return None

            # Average IV across ATM strikes
            spot = stock.fast_info.get("lastPrice", None)
            if spot is None or spot <= 0:
                return None

            # ATM = strikes within 5% of spot
            atm_calls = calls[abs(calls["strike"] - spot) / spot < 0.05]
            atm_puts = puts[abs(puts["strike"] - spot) / spot < 0.05]

            iv_values = []
            if not atm_calls.empty:
                iv_values.extend(atm_calls["impliedVolatility"].dropna().tolist())
            if not atm_puts.empty:
                iv_values.extend(atm_puts["impliedVolatility"].dropna().tolist())

            if not iv_values:
                return None

            avg_iv = sum(iv_values) / len(iv_values)

            # Implied move = IV * sqrt(days_to_expiry / 252) — approximate
            exp_date = datetime.strptime(nearest, "%Y-%m-%d").date()
            days = max(1, (exp_date - date.today()).days)
            implied_move_pct = avg_iv * (days / 252) ** 0.5 * 100

            # Store snapshot for percentile calc
            snapshots = self.state["iv_snapshots"].setdefault(base, [])
            snapshots.append({"date": date.today().isoformat(), "iv": avg_iv})
            # Keep last 30 days
            if len(snapshots) > 30:
                snapshots[:] = snapshots[-30:]
            self._save_state()

            # IV percentile vs 30-day history
            all_ivs = [s["iv"] for s in snapshots]
            rank = sorted(all_ivs).index(avg_iv) + 1
            percentile = (rank / len(all_ivs)) * 100

            return {
                "ticker": base,
                "avg_iv": round(avg_iv, 4),
                "implied_move_pct": round(implied_move_pct, 2),
                "iv_percentile": round(percentile, 1),
                "days_to_expiry": days,
                "expiry": nearest,
            }

        except Exception as e:
            logger.warning(f"IVCrushMonitor.get_iv_data({ticker}): {e}")
            return None

    # ─────────────────────────────────────────────────────────
    # Pre-event / crush detection
    # ─────────────────────────────────────────────────────────

    def is_pre_event_elevated(self, ticker: str, iv_data: Optional[dict] = None) -> bool:
        """
        True if IV percentile ≥ 75 — market is pricing in a big move.
        Pre-event elevated IV → reduce size, widen stops.
        """
        if iv_data is None:
            iv_data = self.get_iv_data(ticker)
        if iv_data is None:
            return False  # LSE ETP — no IV data, treat as normal
        return iv_data.get("iv_percentile", 0) >= 75

    def is_crush_window(self, ticker: str) -> bool:
        """
        True if an earnings event was recorded for this ticker
        within the last 2 trading days (post-announcement crush window).
        """
        events = self.state["earnings_events"]
        key = ticker.replace(".L", "")
        if key not in events:
            return False
        event_date = datetime.fromisoformat(events[key]).date()
        days_since = (date.today() - event_date).days
        return 0 <= days_since <= 2

    def record_earnings_event(self, ticker: str, event_date: Optional[date] = None):
        """Call this when an earnings announcement occurs."""
        key = ticker.replace(".L", "")
        d = event_date or date.today()
        self.state["earnings_events"][key] = d.isoformat()
        self._save_state()
        logger.info(f"IVCrushMonitor: recorded earnings event for {key} on {d}")

    # ─────────────────────────────────────────────────────────
    # Confidence / sizing adjustments
    # ─────────────────────────────────────────────────────────

    def get_stop_multiplier(self, ticker: str, iv_data: Optional[dict] = None) -> float:
        """
        High IV → widen stop to avoid being shaken out by normal IV noise.
        Returns multiplier for ATR-based stop: 1.0 (normal) or 1.25 (elevated IV).
        """
        if self.is_pre_event_elevated(ticker, iv_data):
            return 1.25
        return 1.0

    def get_size_multiplier(self, ticker: str, iv_data: Optional[dict] = None) -> float:
        """
        High IV → reduce size to control £ risk with wider stop.
        Returns multiplier for position size: 1.0 (normal) or 0.80 (elevated IV).
        """
        if self.is_pre_event_elevated(ticker, iv_data):
            return 0.80
        return 1.0

    def get_confidence_adjustment(self, ticker: str, iv_data: Optional[dict] = None) -> int:
        """
        IV percentile 90+ → -5 confidence (extreme uncertainty).
        IV crush window → +3 (volatility resolved, direction clearer).
        """
        if self.is_crush_window(ticker):
            return 3
        if iv_data is None:
            iv_data = self.get_iv_data(ticker)
        if iv_data and iv_data.get("iv_percentile", 0) >= 90:
            return -5
        return 0

    # ─────────────────────────────────────────────────────────
    # Telegram note
    # ─────────────────────────────────────────────────────────

    def get_telegram_note(self, ticker: str) -> str:
        iv_data = self.get_iv_data(ticker)
        if iv_data is None:
            return f"📊 {ticker}: No options data (LSE ETP — IV crush N/A)"

        parts = [
            f"📊 IV Monitor — {ticker}",
            f"  IV: {iv_data['avg_iv']*100:.1f}%  |  "
            f"Percentile: {iv_data['iv_percentile']:.0f}th  |  "
            f"Implied move: ±{iv_data['implied_move_pct']:.1f}%",
        ]
        if self.is_pre_event_elevated(ticker, iv_data):
            parts.append(f"  ⚠️  ELEVATED IV — stop ×1.25, size ×0.80")
        if self.is_crush_window(ticker):
            parts.append(f"  ✅ POST-EARNINGS CRUSH WINDOW — normal sizing")
        return "\n".join(parts)
