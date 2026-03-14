"""
NZT-48 Trading System -- Data Feed Validation & Staleness Detection
====================================================================
Validates every data point before it enters the indicator engine.
Detects stale data, NaN/inf values, extreme outliers, and suspicious gaps.

Without this layer the system trades on bad yfinance data and loses real money.

Quality scoring (0-100):
  Freshness   40 pts  — full if <30s old, 0 if >5min
  Continuity  30 pts  — full if no gaps in last 20 bars
  Volume      20 pts  — full if volume >50% of 20-day ADV
  Completeness 10 pts — all OHLCV fields present and valid

Alert thresholds:
  Quality <50         → WARNING logged
  Quality <25         → CRITICAL logged, suggest skip
  3+ tickers below 25 → SYSTEM_DEGRADED
  >90% tickers below 30 (min 10 checked) → SYSTEM_DOWN, halt trading
"""

from __future__ import annotations

import logging
import math
import sys
import threading
from collections import deque
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Project-root path insertion (standard pattern for feeds/ modules)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.data_validator")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
from core.clock import ET_TZ as _ET

_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)

# Staleness thresholds (seconds)
_STALE_MARKET_HOURS_SEC = 5 * 60     # 5 minutes during market hours
_STALE_OFF_HOURS_SEC = 60 * 60       # 60 minutes outside hours

# Price continuity thresholds
_PRICE_SUSPICIOUS_PCT = 0.05   # 5%  → suspicious
_PRICE_REJECT_PCT = 0.10       # 10% → reject

# Spread thresholds
_SPREAD_LIQUID_PCT = 0.02      # 2% for liquid names
_SPREAD_ILLIQUID_PCT = 0.05    # 5% for illiquid names

# Tickers treated as illiquid for spread validation purposes
_ILLIQUID_TICKERS: set[str] = set()  # populated at runtime if needed

# Price history length per ticker
_PRICE_HISTORY_LEN = 10

# Quality scoring weights
_Q_FRESHNESS_MAX = 40
_Q_CONTINUITY_MAX = 30
_Q_VOLUME_MAX = 20
_Q_COMPLETENESS_MAX = 10

# Freshness scoring breakpoints (seconds)
_FRESH_FULL_SEC = 30.0
_FRESH_ZERO_SEC = 300.0   # 5 minutes

# Continuity: how many recent bars to evaluate
_CONTINUITY_WINDOW = 20

# Required OHLCV fields
_REQUIRED_BAR_FIELDS = {"open", "high", "low", "close", "volume", "timestamp"}


# ---------------------------------------------------------------------------
# Helper: is it market hours right now?
# ---------------------------------------------------------------------------
def _is_market_hours(dt: Optional[datetime] = None) -> bool:
    """Return True if *dt* falls within regular US equity market hours (ET)."""
    if dt is None:
        dt = datetime.now(_ET)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone(_ET)
    else:
        dt = dt.astimezone(_ET)
    # Weekends
    if dt.weekday() >= 5:
        return False
    t = dt.time()
    return _MARKET_OPEN <= t < _MARKET_CLOSE


def _median(values: list[float]) -> float:
    """Simple median without numpy dependency."""
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2.0
    return s[mid]


# ---------------------------------------------------------------------------
# Per-ticker tracking state
# ---------------------------------------------------------------------------
class _TickerState:
    """Mutable state tracked per ticker. All access guarded by parent lock."""

    __slots__ = (
        "price_history",
        "last_price",
        "last_timestamp",
        "bars_received",
        "bars_valid",
        "gap_count",
        "continuity_flags",
        "adv_20",
        "last_volume",
    )

    def __init__(self) -> None:
        self.price_history: deque[float] = deque(maxlen=_PRICE_HISTORY_LEN)
        self.last_price: Optional[float] = None
        self.last_timestamp: Optional[datetime] = None
        self.bars_received: int = 0
        self.bars_valid: int = 0
        self.gap_count: int = 0
        self.continuity_flags: deque[bool] = deque(maxlen=_CONTINUITY_WINDOW)
        self.adv_20: Optional[float] = None   # 20-day average daily volume
        self.last_volume: float = 0.0


# ===========================================================================
# DataFeedValidator
# ===========================================================================
class DataFeedValidator:
    """Thread-safe validation layer sitting between data feeds and indicators.

    Usage::

        validator = DataFeedValidator()
        ok, issues = validator.validate_bar("AAPL", bar_dict)
        if not ok:
            logger.warning("Bad bar for AAPL: %s", issues)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tickers: dict[str, _TickerState] = {}

    # ----- internal helpers ------------------------------------------------

    def _get_state(self, ticker: str) -> _TickerState:
        """Return (or create) per-ticker state. Caller must hold _lock."""
        if ticker not in self._tickers:
            self._tickers[ticker] = _TickerState()
        return self._tickers[ticker]

    @staticmethod
    def _is_finite(v: Any) -> bool:
        """Return True if *v* is a finite number (not NaN, not inf)."""
        try:
            return math.isfinite(float(v))
        except (TypeError, ValueError):
            return False

    # ===================================================================== #
    #  1. validate_bar                                                       #
    # ===================================================================== #
    def validate_bar(self, ticker: str, bar: dict) -> tuple[bool, list[str]]:
        """Validate a single OHLCV bar.

        Args:
            ticker: Symbol, e.g. ``"NVDA"``.
            bar: Dict with keys ``open, high, low, close, volume, timestamp``.

        Returns:
            ``(is_valid, list_of_issues)``
        """
        issues: list[str] = []

        # --- field presence ---
        missing = _REQUIRED_BAR_FIELDS - set(bar.keys())
        if missing:
            issues.append(f"Missing fields: {sorted(missing)}")
            return False, issues

        o = bar["open"]
        h = bar["high"]
        lo = bar["low"]
        c = bar["close"]
        v = bar["volume"]
        ts = bar["timestamp"]

        # --- NaN / inf check ---
        for name, val in [("open", o), ("high", h), ("low", lo), ("close", c), ("volume", v)]:
            if not self._is_finite(val):
                issues.append(f"{name} is NaN/inf: {val}")

        if issues:
            return False, issues

        o, h, lo, c, v = float(o), float(h), float(lo), float(c), float(v)

        # --- OHLCV structural rules ---
        if h < lo:
            issues.append(f"High ({h}) < Low ({lo})")

        if c > h or c < lo:
            # Allow tiny float rounding (1e-6 tolerance)
            if c > h + 1e-6:
                issues.append(f"Close ({c}) > High ({h})")
            if c < lo - 1e-6:
                issues.append(f"Close ({c}) < Low ({lo})")

        if o > h + 1e-6:
            issues.append(f"Open ({o}) > High ({h})")
        if o < lo - 1e-6:
            issues.append(f"Open ({o}) < Low ({lo})")

        if v < 0:
            issues.append(f"Volume negative: {v}")

        if v == 0 and _is_market_hours(ts if isinstance(ts, datetime) else None):
            issues.append("Volume is 0 during market hours (likely bad data)")

        # --- price sanity ---
        for name, val in [("open", o), ("high", h), ("low", lo), ("close", c)]:
            if val <= 0:
                issues.append(f"{name} <= 0: {val}")

        is_valid = len(issues) == 0

        # Update state if valid
        if is_valid:
            with self._lock:
                state = self._get_state(ticker)
                state.bars_received += 1
                state.bars_valid += 1
                state.last_volume = v
        else:
            with self._lock:
                state = self._get_state(ticker)
                state.bars_received += 1

        return is_valid, issues

    # ===================================================================== #
    #  2. validate_indicator_snapshot                                         #
    # ===================================================================== #
    def validate_indicator_snapshot(
        self, ticker: str, snapshot: dict
    ) -> tuple[bool, list[str]]:
        """Validate computed indicator values for reasonableness.

        Expected keys (all optional — only present ones are checked):
            rsi14, atr14, price, vwap, stochastic_rsi, adx14,
            ema9, ema20, ema50, bb_upper, bb_lower, macd_line, macd_signal.

        Returns:
            ``(is_valid, list_of_issues)``
        """
        issues: list[str] = []

        # RSI in [0, 100]
        for rsi_key in ("rsi14", "stochastic_rsi"):
            if rsi_key in snapshot:
                val = snapshot[rsi_key]
                if self._is_finite(val):
                    if not (0.0 <= float(val) <= 100.0):
                        issues.append(f"{rsi_key} out of range [0,100]: {val}")
                else:
                    issues.append(f"{rsi_key} is NaN/inf: {val}")

        # ATR must be > 0
        if "atr14" in snapshot:
            val = snapshot["atr14"]
            if self._is_finite(val):
                if float(val) < 0:
                    issues.append(f"atr14 negative: {val}")
            else:
                issues.append(f"atr14 is NaN/inf: {val}")

        # ADX in [0, 100]
        if "adx14" in snapshot:
            val = snapshot["adx14"]
            if self._is_finite(val):
                if not (0.0 <= float(val) <= 100.0):
                    issues.append(f"adx14 out of range [0,100]: {val}")
            else:
                issues.append(f"adx14 is NaN/inf: {val}")

        # Price > 0
        if "price" in snapshot:
            val = snapshot["price"]
            if self._is_finite(val):
                if float(val) <= 0:
                    issues.append(f"price <= 0: {val}")
            else:
                issues.append(f"price is NaN/inf: {val}")

        # VWAP within 5% of price
        if "vwap" in snapshot and "price" in snapshot:
            vwap = snapshot["vwap"]
            price = snapshot["price"]
            if self._is_finite(vwap) and self._is_finite(price) and float(price) > 0:
                deviation = abs(float(vwap) - float(price)) / float(price)
                if deviation > 0.05:
                    issues.append(
                        f"VWAP ({vwap}) deviates >{5}% from price ({price}): "
                        f"{deviation:.2%}"
                    )

        # EMAs should be > 0 if present
        for ema_key in ("ema9", "ema20", "ema50"):
            if ema_key in snapshot:
                val = snapshot[ema_key]
                if self._is_finite(val):
                    if float(val) <= 0:
                        issues.append(f"{ema_key} <= 0: {val}")
                else:
                    issues.append(f"{ema_key} is NaN/inf: {val}")

        # Bollinger sanity: upper >= lower
        if "bb_upper" in snapshot and "bb_lower" in snapshot:
            bbu = snapshot["bb_upper"]
            bbl = snapshot["bb_lower"]
            if self._is_finite(bbu) and self._is_finite(bbl):
                if float(bbu) < float(bbl):
                    issues.append(
                        f"BB upper ({bbu}) < BB lower ({bbl})"
                    )

        # Generic NaN sweep over all numeric values
        for key, val in snapshot.items():
            if key in ("ticker", "timestamp"):
                continue
            if isinstance(val, (int, float)):
                if not self._is_finite(val):
                    issues.append(f"{key} is NaN/inf: {val}")

        return (len(issues) == 0), issues

    # ===================================================================== #
    #  3. check_staleness                                                    #
    # ===================================================================== #
    def check_staleness(
        self, ticker: str, latest_timestamp: datetime
    ) -> tuple[bool, float]:
        """Check whether data for *ticker* is stale.

        Args:
            ticker: Symbol string.
            latest_timestamp: Timestamp of most recent bar received.

        Returns:
            ``(is_stale, staleness_seconds)``
            ``is_stale`` is True when data exceeds threshold.
        """
        now = datetime.now(timezone.utc)

        if latest_timestamp.tzinfo is None:
            latest_timestamp = latest_timestamp.replace(tzinfo=timezone.utc)

        staleness_sec = (now - latest_timestamp).total_seconds()

        if staleness_sec < 0:
            # Timestamp in the future — suspicious but not stale
            staleness_sec = 0.0

        threshold = (
            _STALE_MARKET_HOURS_SEC if _is_market_hours() else _STALE_OFF_HOURS_SEC
        )

        is_stale = staleness_sec > threshold

        # Update per-ticker state
        with self._lock:
            state = self._get_state(ticker)
            state.last_timestamp = latest_timestamp

        return is_stale, staleness_sec

    # ===================================================================== #
    #  4. check_price_continuity                                             #
    # ===================================================================== #
    def check_price_continuity(
        self, ticker: str, new_price: float
    ) -> tuple[bool, str]:
        """Detect suspicious price jumps.

        Maintains last 10 prices per ticker.  Compares *new_price* to
        the median of the history.

        Returns:
            ``(is_ok, message)``
            ``is_ok`` is False when the jump exceeds the reject threshold (10%).
            A 5-10% jump returns ``is_ok=True`` but with a warning message.
        """
        if not self._is_finite(new_price) or new_price <= 0:
            return False, f"Invalid price: {new_price}"

        with self._lock:
            state = self._get_state(ticker)

            if len(state.price_history) == 0:
                # First price — nothing to compare against
                return True, "First price registered"

            med = _median(list(state.price_history))
            if med <= 0:
                return True, "Median is zero; cannot assess continuity"

            pct_change = abs(new_price - med) / med

            if pct_change > _PRICE_REJECT_PCT:
                # Track consecutive rejections — after 3 consecutive rejects at a
                # similar level, accept the new price as a legitimate gap (overnight
                # gap, earnings, etc.) to prevent history from freezing permanently.
                state.gap_count += 1
                state.continuity_flags.append(False)
                if state.gap_count >= 3:
                    # Force-accept: this is likely a genuine price gap, not bad data.
                    # Reset history around the new price level to prevent further freezing.
                    state.price_history.clear()
                    state.price_history.append(new_price)
                    state.last_price = new_price
                    state.gap_count = 0
                    return True, (
                        f"GAP ACCEPTED: price {new_price:.4f} deviates "
                        f"{pct_change:.2%} from median {med:.4f} — "
                        f"accepted after 3 consecutive rejections (legitimate gap)"
                    )
                return False, (
                    f"REJECT: price {new_price:.4f} deviates "
                    f"{pct_change:.2%} from median {med:.4f} (>{_PRICE_REJECT_PCT:.0%})"
                )
            if pct_change > _PRICE_SUSPICIOUS_PCT:
                state.gap_count = 0  # Reset gap counter on accepted price
                return True, (
                    f"SUSPICIOUS: price {new_price:.4f} deviates "
                    f"{pct_change:.2%} from median {med:.4f} (>{_PRICE_SUSPICIOUS_PCT:.0%})"
                )

            state.gap_count = 0  # Reset gap counter on accepted price
            return True, "OK"

    # ===================================================================== #
    #  5. validate_spread                                                    #
    # ===================================================================== #
    def validate_spread(
        self, ticker: str, bid: float, ask: float
    ) -> tuple[bool, str]:
        """Validate that the bid-ask spread is reasonable.

        Liquid names: spread must be <2% of midpoint.
        Illiquid names: spread must be <5%.

        Returns:
            ``(is_valid, message)``
        """
        if not (self._is_finite(bid) and self._is_finite(ask)):
            return False, f"Non-finite bid/ask: bid={bid}, ask={ask}"
        if bid <= 0 or ask <= 0:
            return False, f"Non-positive bid/ask: bid={bid}, ask={ask}"
        if bid > ask:
            return False, f"Bid ({bid}) > Ask ({ask}) — crossed market"

        mid = (bid + ask) / 2.0
        spread_pct = (ask - bid) / mid

        threshold = (
            _SPREAD_ILLIQUID_PCT
            if ticker.upper() in _ILLIQUID_TICKERS
            else _SPREAD_LIQUID_PCT
        )

        if spread_pct > threshold:
            return False, (
                f"Spread too wide: {spread_pct:.4%} "
                f"(bid={bid}, ask={ask}, threshold={threshold:.2%})"
            )
        return True, f"Spread OK: {spread_pct:.4%}"

    # ===================================================================== #
    #  6. register_price                                                     #
    # ===================================================================== #
    def register_price(
        self, ticker: str, price: float, timestamp: datetime
    ) -> None:
        """Register a validated price for continuity tracking.

        Call this after a bar passes validation so the continuity checker has
        an accurate history.
        """
        if not self._is_finite(price) or price <= 0:
            return
        with self._lock:
            state = self._get_state(ticker)
            state.price_history.append(price)
            state.last_price = price
            state.last_timestamp = timestamp
            state.continuity_flags.append(True)

    # ===================================================================== #
    #  7. get_data_quality_score                                             #
    # ===================================================================== #
    def get_data_quality_score(self, ticker: str) -> dict:
        """Compute a quality score (0-100) for *ticker*.

        Components:
            freshness    (40)  — based on staleness of last timestamp
            continuity   (30)  — ratio of clean bars in last 20
            volume       (20)  — last volume vs 20-day ADV
            completeness (10)  — ratio of valid bars to total bars
        """
        with self._lock:
            state = self._get_state(ticker)

            # -- Freshness (40 pts) ----------------------------------------
            freshness = 0.0
            staleness_sec = None
            if state.last_timestamp is not None:
                now = datetime.now(timezone.utc)
                ts = state.last_timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                staleness_sec = (now - ts).total_seconds()
                if staleness_sec < 0:
                    staleness_sec = 0.0
                if staleness_sec <= _FRESH_FULL_SEC:
                    freshness = _Q_FRESHNESS_MAX
                elif staleness_sec >= _FRESH_ZERO_SEC:
                    freshness = 0.0
                else:
                    # Linear interpolation
                    ratio = 1.0 - (
                        (staleness_sec - _FRESH_FULL_SEC)
                        / (_FRESH_ZERO_SEC - _FRESH_FULL_SEC)
                    )
                    freshness = ratio * _Q_FRESHNESS_MAX

            # -- Continuity (30 pts) ---------------------------------------
            continuity = 0.0
            total_flags = len(state.continuity_flags)
            if total_flags > 0:
                good_flags = sum(1 for f in state.continuity_flags if f)
                continuity = (good_flags / total_flags) * _Q_CONTINUITY_MAX

            # -- Volume (20 pts) -------------------------------------------
            volume = 0.0
            if state.adv_20 is not None and state.adv_20 > 0:
                ratio = state.last_volume / state.adv_20
                # Full points if ratio >= 0.5 (50% of ADV)
                volume = min(1.0, ratio / 0.5) * _Q_VOLUME_MAX
            elif state.last_volume > 0:
                # No ADV reference — give partial credit if volume exists
                volume = _Q_VOLUME_MAX * 0.5

            # -- Completeness (10 pts) -------------------------------------
            completeness = 0.0
            if state.bars_received > 0:
                completeness = (
                    state.bars_valid / state.bars_received
                ) * _Q_COMPLETENESS_MAX

            total_score = freshness + continuity + volume + completeness
            total_score = round(min(100.0, max(0.0, total_score)), 1)

            # -- Logging based on thresholds --------------------------------
            if total_score < 25:
                logger.critical(
                    "CRITICAL quality for %s: %.1f/100 — consider skipping this ticker",
                    ticker,
                    total_score,
                )
            elif total_score < 50:
                logger.warning(
                    "WARNING quality for %s: %.1f/100",
                    ticker,
                    total_score,
                )

            return {
                "ticker": ticker,
                "score": total_score,
                "freshness": round(freshness, 1),
                "continuity": round(continuity, 1),
                "volume": round(volume, 1),
                "completeness": round(completeness, 1),
                "staleness_seconds": round(staleness_sec, 1) if staleness_sec is not None else None,
                "bars_received": state.bars_received,
                "bars_valid": state.bars_valid,
            }

    # ===================================================================== #
    #  8. get_system_health                                                  #
    # ===================================================================== #
    def get_system_health(self) -> dict:
        """Return overall system health across all tracked tickers.

        Checks:
            - Number of stale feeds
            - Average quality score
            - Worst tickers
            - System-level alerts (DEGRADED / DOWN)
        """
        with self._lock:
            tickers = list(self._tickers.keys())

        if not tickers:
            return {
                "status": "NO_DATA",
                "tracked_tickers": 0,
                "stale_feeds": 0,
                "avg_quality": 0.0,
                "worst_tickers": [],
                "alerts": ["No tickers being tracked"],
            }

        scores: list[dict] = []
        stale_count = 0
        now = datetime.now(timezone.utc)

        for t in tickers:
            q = self.get_data_quality_score(t)
            scores.append(q)
            with self._lock:
                state = self._get_state(t)
                if state.last_timestamp is not None:
                    ts = state.last_timestamp
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    sec = (now - ts).total_seconds()
                    threshold = (
                        _STALE_MARKET_HOURS_SEC
                        if _is_market_hours()
                        else _STALE_OFF_HOURS_SEC
                    )
                    if sec > threshold:
                        stale_count += 1

        avg_quality = sum(s["score"] for s in scores) / len(scores) if scores else 0.0
        sorted_scores = sorted(scores, key=lambda s: s["score"])
        worst = sorted_scores[:5]  # 5 worst tickers

        below_25_count = sum(1 for s in scores if s["score"] < 25)
        below_30_count = sum(1 for s in scores if s["score"] < 30)
        alerts: list[str] = []

        # SYSTEM_DOWN: >90% of tickers below quality 30 AND at least 10 checked.
        # Previous threshold (all < 50) was too aggressive for leveraged LSE ETPs
        # with thin volume — they routinely scored below 50, halting all trading.
        if (
            len(scores) >= 10
            and below_30_count > len(scores) * 0.9
        ):
            alerts.append(
                f"SYSTEM_DOWN: {below_30_count}/{len(scores)} tickers below quality 30 "
                f"(>{90}%) — halt trading"
            )
            logger.critical(
                "SYSTEM_DOWN: %d/%d tickers below quality 30 (>90%%)",
                below_30_count,
                len(scores),
            )
        elif below_25_count >= 3:
            alerts.append(
                f"SYSTEM_DEGRADED: {below_25_count}/{len(scores)} tickers below quality 25"
            )
            logger.error(
                "SYSTEM_DEGRADED: %d/%d tickers below quality 25",
                below_25_count,
                len(scores),
            )

        if stale_count > 0:
            alerts.append(f"{stale_count} stale feed(s)")

        status = "HEALTHY"
        if any("SYSTEM_DOWN" in a for a in alerts):
            status = "SYSTEM_DOWN"
        elif any("SYSTEM_DEGRADED" in a for a in alerts):
            status = "SYSTEM_DEGRADED"
        elif stale_count > 0:
            status = "DEGRADED"

        return {
            "status": status,
            "tracked_tickers": len(tickers),
            "stale_feeds": stale_count,
            "avg_quality": round(avg_quality, 1),
            "below_25_count": below_25_count,
            "below_30_count": below_30_count,
            "worst_tickers": worst,
            "alerts": alerts,
        }

    # ===================================================================== #
    #  9. reset_daily                                                        #
    # ===================================================================== #
    def reset_daily(self) -> None:
        """Reset daily counters for all tickers. Call at start of each trading day."""
        with self._lock:
            for state in self._tickers.values():
                state.bars_received = 0
                state.bars_valid = 0
                state.gap_count = 0
                state.continuity_flags.clear()
            logger.info("Daily counters reset for %d tickers", len(self._tickers))

    # ===================================================================== #
    #  Convenience: set ADV for volume scoring                               #
    # ===================================================================== #
    def set_adv(self, ticker: str, adv_20: float) -> None:
        """Set the 20-day average daily volume for volume quality scoring."""
        with self._lock:
            state = self._get_state(ticker)
            state.adv_20 = adv_20

    def mark_illiquid(self, ticker: str) -> None:
        """Mark *ticker* as illiquid (wider spread threshold)."""
        _ILLIQUID_TICKERS.add(ticker.upper())


# ===========================================================================
# Self-test
# ===========================================================================
if __name__ == "__main__":
    import json

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    validator = DataFeedValidator()
    passed = 0
    failed = 0

    def _check(name: str, condition: bool, detail: str = "") -> None:
        global passed, failed
        status = "PASS" if condition else "FAIL"
        if not condition:
            failed += 1
        else:
            passed += 1
        msg = f"  [{status}] {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)

    print("=" * 70)
    print("NZT-48 DataFeedValidator — Self-Test Suite")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. validate_bar — good bar
    # ------------------------------------------------------------------
    print("\n--- validate_bar ---")
    now_utc = datetime.now(timezone.utc)

    good_bar = {
        "open": 150.0,
        "high": 155.0,
        "low": 149.0,
        "close": 153.0,
        "volume": 1_000_000,
        "timestamp": now_utc,
    }
    ok, issues = validator.validate_bar("AAPL", good_bar)
    _check("Good bar accepted", ok, f"issues={issues}")

    # ------------------------------------------------------------------
    # 2. validate_bar — NaN values
    # ------------------------------------------------------------------
    nan_bar = dict(good_bar, close=float("nan"))
    ok, issues = validator.validate_bar("AAPL", nan_bar)
    _check("NaN close rejected", not ok, f"issues={issues}")

    # ------------------------------------------------------------------
    # 3. validate_bar — inf values
    # ------------------------------------------------------------------
    inf_bar = dict(good_bar, high=float("inf"))
    ok, issues = validator.validate_bar("AAPL", inf_bar)
    _check("Inf high rejected", not ok, f"issues={issues}")

    # ------------------------------------------------------------------
    # 4. validate_bar — high < low
    # ------------------------------------------------------------------
    bad_hl = dict(good_bar, high=148.0, low=150.0)
    ok, issues = validator.validate_bar("AAPL", bad_hl)
    _check("High < Low rejected", not ok, f"issues={issues}")

    # ------------------------------------------------------------------
    # 5. validate_bar — negative volume
    # ------------------------------------------------------------------
    neg_vol = dict(good_bar, volume=-100)
    ok, issues = validator.validate_bar("AAPL", neg_vol)
    _check("Negative volume rejected", not ok, f"issues={issues}")

    # ------------------------------------------------------------------
    # 6. validate_bar — missing fields
    # ------------------------------------------------------------------
    missing = {"open": 150.0, "close": 153.0}
    ok, issues = validator.validate_bar("AAPL", missing)
    _check("Missing fields rejected", not ok, f"issues={issues}")

    # ------------------------------------------------------------------
    # 7. validate_bar — zero price
    # ------------------------------------------------------------------
    zero_price = dict(good_bar, open=0, high=0, low=0, close=0)
    ok, issues = validator.validate_bar("AAPL", zero_price)
    _check("Zero prices rejected", not ok, f"issues={issues}")

    # ------------------------------------------------------------------
    # 8. validate_bar — close outside high/low
    # ------------------------------------------------------------------
    bad_close = dict(good_bar, close=160.0)
    ok, issues = validator.validate_bar("AAPL", bad_close)
    _check("Close > High rejected", not ok, f"issues={issues}")

    # ------------------------------------------------------------------
    # 9. check_price_continuity
    # ------------------------------------------------------------------
    print("\n--- check_price_continuity ---")

    # Register a price history
    for p in [100.0, 100.5, 101.0, 100.2, 99.8, 100.3, 100.1, 99.9, 100.4, 100.0]:
        validator.register_price("TEST", p, now_utc)

    # Normal move — should pass
    ok, msg = validator.check_price_continuity("TEST", 101.5)
    _check("Normal price move OK", ok, msg)

    # 6% jump — suspicious but OK
    ok, msg = validator.check_price_continuity("TEST", 106.5)
    _check("5%+ jump suspicious but passes", ok and "SUSPICIOUS" in msg, msg)

    # 12% jump — should reject
    ok, msg = validator.check_price_continuity("TEST", 113.0)
    _check("10%+ jump rejected", not ok, msg)

    # Invalid price
    ok, msg = validator.check_price_continuity("TEST", float("nan"))
    _check("NaN price rejected", not ok, msg)

    # ------------------------------------------------------------------
    # 10. validate_spread
    # ------------------------------------------------------------------
    print("\n--- validate_spread ---")

    ok, msg = validator.validate_spread("AAPL", 150.00, 150.20)
    _check("Normal spread accepted", ok, msg)

    ok, msg = validator.validate_spread("AAPL", 150.00, 155.00)
    _check("Wide spread rejected", not ok, msg)

    ok, msg = validator.validate_spread("AAPL", 155.00, 150.00)
    _check("Crossed market rejected", not ok, msg)

    ok, msg = validator.validate_spread("AAPL", -1.0, 150.00)
    _check("Negative bid rejected", not ok, msg)

    # Illiquid ticker — wider threshold
    validator.mark_illiquid("PENNY")
    ok, msg = validator.validate_spread("PENNY", 5.00, 5.20)
    _check("Illiquid 4% spread accepted", ok, msg)

    ok, msg = validator.validate_spread("PENNY", 5.00, 5.30)
    _check("Illiquid 6% spread rejected", not ok, msg)

    # ------------------------------------------------------------------
    # 11. check_staleness
    # ------------------------------------------------------------------
    print("\n--- check_staleness ---")

    fresh_ts = datetime.now(timezone.utc) - timedelta(seconds=10)
    is_stale, sec = validator.check_staleness("AAPL", fresh_ts)
    _check("10s-old data not stale", not is_stale, f"staleness={sec:.1f}s")

    old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    is_stale, sec = validator.check_staleness("AAPL", old_ts)
    _check("2h-old data is stale", is_stale, f"staleness={sec:.1f}s")

    # ------------------------------------------------------------------
    # 12. validate_indicator_snapshot
    # ------------------------------------------------------------------
    print("\n--- validate_indicator_snapshot ---")

    good_snap = {
        "price": 150.0,
        "rsi14": 55.0,
        "atr14": 2.5,
        "vwap": 149.5,
        "ema9": 150.2,
        "ema20": 149.8,
        "ema50": 148.5,
        "adx14": 30.0,
        "stochastic_rsi": 45.0,
        "bb_upper": 155.0,
        "bb_lower": 145.0,
        "macd_line": 0.5,
        "macd_signal": 0.3,
    }
    ok, issues = validator.validate_indicator_snapshot("AAPL", good_snap)
    _check("Good snapshot accepted", ok, f"issues={issues}")

    bad_snap_rsi = dict(good_snap, rsi14=150.0)
    ok, issues = validator.validate_indicator_snapshot("AAPL", bad_snap_rsi)
    _check("RSI > 100 rejected", not ok, f"issues={issues}")

    bad_snap_atr = dict(good_snap, atr14=-1.0)
    ok, issues = validator.validate_indicator_snapshot("AAPL", bad_snap_atr)
    _check("Negative ATR rejected", not ok, f"issues={issues}")

    bad_snap_vwap = dict(good_snap, vwap=200.0)
    ok, issues = validator.validate_indicator_snapshot("AAPL", bad_snap_vwap)
    _check("VWAP >5% from price rejected", not ok, f"issues={issues}")

    bad_snap_nan = dict(good_snap, ema9=float("nan"))
    ok, issues = validator.validate_indicator_snapshot("AAPL", bad_snap_nan)
    _check("NaN EMA rejected", not ok, f"issues={issues}")

    bad_snap_bb = dict(good_snap, bb_upper=140.0, bb_lower=160.0)
    ok, issues = validator.validate_indicator_snapshot("AAPL", bad_snap_bb)
    _check("BB upper < lower rejected", not ok, f"issues={issues}")

    # ------------------------------------------------------------------
    # 13. get_data_quality_score
    # ------------------------------------------------------------------
    print("\n--- get_data_quality_score ---")

    # Register fresh data for quality scoring
    validator2 = DataFeedValidator()
    for i in range(20):
        bar_i = {
            "open": 150.0 + i * 0.1,
            "high": 155.0 + i * 0.1,
            "low": 149.0 + i * 0.1,
            "close": 153.0 + i * 0.1,
            "volume": 1_000_000,
            "timestamp": now_utc,
        }
        validator2.validate_bar("NVDA", bar_i)
        validator2.register_price("NVDA", 153.0 + i * 0.1, now_utc)
    validator2.set_adv("NVDA", 1_500_000)

    q = validator2.get_data_quality_score("NVDA")
    _check(
        "Quality score computed",
        q["score"] > 0,
        f"score={q['score']}, breakdown={json.dumps(q, indent=2, default=str)}",
    )
    _check("Freshness > 0", q["freshness"] > 0, f"freshness={q['freshness']}")
    _check("Continuity > 0", q["continuity"] > 0, f"continuity={q['continuity']}")

    # ------------------------------------------------------------------
    # 14. get_system_health
    # ------------------------------------------------------------------
    print("\n--- get_system_health ---")
    health = validator2.get_system_health()
    _check(
        "System health computed",
        "status" in health,
        f"status={health['status']}, avg_quality={health['avg_quality']}",
    )
    print(f"  Health detail: {json.dumps(health, indent=2, default=str)}")

    # ------------------------------------------------------------------
    # 15. reset_daily
    # ------------------------------------------------------------------
    print("\n--- reset_daily ---")
    validator2.reset_daily()
    q_after = validator2.get_data_quality_score("NVDA")
    _check(
        "Counters reset",
        q_after["bars_received"] == 0,
        f"bars_received={q_after['bars_received']}",
    )

    # ------------------------------------------------------------------
    # 16. System degradation test (SYSTEM_DEGRADED: 3+ tickers below quality 25)
    # ------------------------------------------------------------------
    print("\n--- System degradation alerts ---")
    validator3 = DataFeedValidator()
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    # Create 4 tickers with only stale timestamps → quality 0 (no continuity,
    # no volume, no completeness, freshness=0 because 3h old).
    # check_staleness creates the state and sets last_timestamp but adds no
    # continuity flags, so all quality components stay at 0.
    for t in ["BAD1", "BAD2", "BAD3", "BAD4"]:
        validator3.check_staleness(t, old)
    health3 = validator3.get_system_health()
    has_degrade = any("SYSTEM_DEGRADED" in a for a in health3.get("alerts", []))
    _check(
        "SYSTEM_DEGRADED with 4 bad tickers (3+ below quality 25)",
        has_degrade,
        f"alerts={health3['alerts']}",
    )

    # ------------------------------------------------------------------
    # 16b. SYSTEM_DOWN should NOT trigger with <10 tickers
    # ------------------------------------------------------------------
    has_down = any("SYSTEM_DOWN" in a for a in health3.get("alerts", []))
    _check(
        "SYSTEM_DOWN NOT triggered with only 4 tickers (<10 minimum)",
        not has_down,
        f"alerts={health3['alerts']}",
    )

    # ------------------------------------------------------------------
    # 16c. SYSTEM_DOWN SHOULD trigger with 12 bad tickers (>90% below 30)
    # ------------------------------------------------------------------
    validator4 = DataFeedValidator()
    for t in [f"XBAD{i}" for i in range(12)]:
        validator4.check_staleness(t, old)
    health4 = validator4.get_system_health()
    has_down_12 = any("SYSTEM_DOWN" in a for a in health4.get("alerts", []))
    _check(
        "SYSTEM_DOWN triggered with 12 bad tickers (>90% below 30, >=10 checked)",
        has_down_12,
        f"alerts={health4['alerts']}",
    )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    total = passed + failed
    print(f"Self-test complete: {passed}/{total} passed, {failed}/{total} failed")
    if failed > 0:
        print("*** FAILURES DETECTED — review output above ***")
        sys.exit(1)
    else:
        print("All tests passed.")
    print("=" * 70)
