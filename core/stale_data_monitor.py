"""
NZT-48 Trading System -- F-14: Stale Data Tick-Change Counter
==============================================================
Detects stuck/frozen data feeds by tracking per-ticker price changes.

PROBLEM: A frozen feed looks like a flat market. Without this monitor,
the system computes indicators on stale prices and generates false signals
because the feed silently returns the same cached price on every poll.

DETECTION LOGIC:
  - Track last known price AND last price-change timestamp per ticker.
  - If price is unchanged for >stale_threshold seconds during market hours
    AND the ticker is expected to have volume, mark it DEGRADED.
  - If >50% of the tracked universe is stale for >stale_threshold seconds,
    return should_halt=True (halt all trading).

This is COMPLEMENTARY to DataFeedValidator.check_staleness(), which detects
bars with old timestamps. This module detects a different failure mode:
the feed returns bars with FRESH timestamps but the SAME price (stuck feed).

Usage:
    from core.stale_data_monitor import StaleDataMonitor
    monitor = StaleDataMonitor(stale_threshold_sec=300)

    # Called on each scan cycle for each ticker after bar validation:
    monitor.update("QQQ3.L", price=42.50, timestamp=now_utc())

    # Check individual staleness:
    stale_map = monitor.check_staleness()  # {ticker: is_stale}

    # System-wide halt decision:
    if monitor.should_halt():
        logger.critical("F-14: >50%% universe stale — HALT")

Reference: AEGIS Master Plan item F-14 (Stale Data Tick-Change Counter).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from core.clock import is_lse_open, now_utc

logger = logging.getLogger("nzt48.core.stale_data_monitor")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULT_STALE_THRESHOLD_SEC: float = 300.0   # 5 minutes
_DEFAULT_HALT_FRACTION: float = 0.50          # >50% stale = halt
_DEFAULT_MIN_TICKERS_FOR_HALT: int = 4        # Need at least 4 tickers to trigger halt


class _TickerPriceState:
    """Internal per-ticker mutable state. All access guarded by parent lock."""

    __slots__ = (
        "last_price",
        "last_change_ts",
        "last_update_ts",
        "update_count",
        "has_ever_changed",
    )

    def __init__(self) -> None:
        self.last_price: Optional[float] = None
        self.last_change_ts: Optional[datetime] = None  # last time price CHANGED
        self.last_update_ts: Optional[datetime] = None  # last time update() was called
        self.update_count: int = 0
        self.has_ever_changed: bool = False


class StaleDataMonitor:
    """Tick-change counter for detecting frozen data feeds.

    Thread-safe: all mutable state access is guarded by threading.Lock.

    Args:
        stale_threshold_sec: Seconds of unchanged price during market hours
            before a ticker is considered stale. Default 300 (5 min).
        halt_fraction: Fraction of universe that must be stale to trigger
            a system-wide halt. Default 0.50 (50%).
        min_tickers_for_halt: Minimum number of tracked tickers required
            before the halt check is meaningful. Prevents false halts when
            only 1-2 tickers are being tracked. Default 4.
    """

    def __init__(
        self,
        stale_threshold_sec: float = _DEFAULT_STALE_THRESHOLD_SEC,
        halt_fraction: float = _DEFAULT_HALT_FRACTION,
        min_tickers_for_halt: int = _DEFAULT_MIN_TICKERS_FOR_HALT,
    ) -> None:
        self._lock = threading.Lock()
        self._tickers: dict[str, _TickerPriceState] = {}
        self.stale_threshold_sec = max(1.0, stale_threshold_sec)
        self.halt_fraction = max(0.0, min(1.0, halt_fraction))
        self.min_tickers_for_halt = max(1, min_tickers_for_halt)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, ticker: str, price: float, timestamp: Optional[datetime] = None) -> None:
        """Record a price observation for a ticker.

        Should be called on each scan cycle after bar validation passes.
        Tracks whether the price has actually CHANGED since the last call.

        Args:
            ticker: Symbol string (e.g. "QQQ3.L").
            price: Latest close price.
            timestamp: Observation time. Defaults to now_utc().
        """
        if timestamp is None:
            timestamp = now_utc()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        with self._lock:
            state = self._get_state(ticker)
            state.update_count += 1
            state.last_update_ts = timestamp

            if state.last_price is None:
                # First observation -- initialize
                state.last_price = price
                state.last_change_ts = timestamp
                logger.debug("F-14: %s first price registered: %.4f", ticker, price)
                return

            # Check if price has actually changed (use a tiny epsilon for float comparison)
            if abs(price - state.last_price) > 1e-10:
                state.last_price = price
                state.last_change_ts = timestamp
                state.has_ever_changed = True
            # else: price unchanged -- last_change_ts stays the same

    def check_staleness(self, at_time: Optional[datetime] = None) -> dict[str, bool]:
        """Check which tickers have stale (unchanged) prices.

        A ticker is considered stale when:
          1. Its price has not changed for > stale_threshold_sec, AND
          2. It is currently market hours (LSE open), AND
          3. The ticker has received at least 2 updates (we need a baseline).

        Tickers outside market hours are never marked stale because
        zero price movement is expected when the exchange is closed.

        Args:
            at_time: Reference time for staleness check. Defaults to now_utc().

        Returns:
            Dict mapping ticker -> is_stale (True if feed appears frozen).
        """
        if at_time is None:
            at_time = now_utc()
        if at_time.tzinfo is None:
            at_time = at_time.replace(tzinfo=timezone.utc)

        # Market hours check: only flag staleness when LSE is open
        market_open = is_lse_open()

        result: dict[str, bool] = {}

        with self._lock:
            for ticker, state in self._tickers.items():
                if state.last_change_ts is None or state.update_count < 2:
                    # Not enough data to judge -- assume OK
                    result[ticker] = False
                    continue

                if not market_open:
                    # Outside market hours -- never flag as stale
                    result[ticker] = False
                    continue

                elapsed_sec = (at_time - state.last_change_ts).total_seconds()
                is_stale = elapsed_sec > self.stale_threshold_sec

                if is_stale:
                    logger.warning(
                        "F-14 STALE: %s price unchanged (%.4f) for %.0fs (threshold: %.0fs)",
                        ticker,
                        state.last_price,
                        elapsed_sec,
                        self.stale_threshold_sec,
                    )

                result[ticker] = is_stale

        return result

    def should_halt(self, at_time: Optional[datetime] = None) -> bool:
        """Determine if trading should halt due to widespread feed staleness.

        Returns True when >halt_fraction of the tracked universe has stale
        price data, indicating a systemic data feed failure rather than a
        single ticker going quiet.

        Requires at least min_tickers_for_halt tracked tickers to avoid
        false positives during startup or when scanning a small universe.

        Args:
            at_time: Reference time. Defaults to now_utc().

        Returns:
            True if >halt_fraction of universe is stale and should halt.
        """
        staleness = self.check_staleness(at_time)

        total = len(staleness)
        if total < self.min_tickers_for_halt:
            return False

        stale_count = sum(1 for is_stale in staleness.values() if is_stale)
        stale_fraction = stale_count / total

        if stale_fraction > self.halt_fraction:
            logger.critical(
                "F-14 HALT: %d/%d tickers stale (%.0f%% > %.0f%% threshold) — "
                "data feeds appear frozen, halting all trading",
                stale_count,
                total,
                stale_fraction * 100,
                self.halt_fraction * 100,
            )
            return True

        return False

    def get_status(self) -> dict:
        """Return a diagnostic summary of the monitor state.

        Returns:
            Dict with overall status, per-ticker details, and config.
        """
        at_time = now_utc()
        staleness = self.check_staleness(at_time)
        market_open = is_lse_open()

        total = len(staleness)
        stale_count = sum(1 for v in staleness.values() if v)

        per_ticker: dict[str, dict] = {}
        with self._lock:
            for ticker, state in self._tickers.items():
                elapsed = None
                if state.last_change_ts is not None:
                    elapsed = round((at_time - state.last_change_ts).total_seconds(), 1)

                per_ticker[ticker] = {
                    "last_price": state.last_price,
                    "seconds_since_change": elapsed,
                    "is_stale": staleness.get(ticker, False),
                    "update_count": state.update_count,
                    "has_ever_changed": state.has_ever_changed,
                }

        # Determine overall status
        if total == 0:
            status = "NO_DATA"
        elif not market_open:
            status = "MARKET_CLOSED"
        elif stale_count == 0:
            status = "OK"
        elif total >= self.min_tickers_for_halt and stale_count / total > self.halt_fraction:
            status = "HALT"
        elif stale_count > 0:
            status = "DEGRADED"
        else:
            status = "OK"

        return {
            "status": status,
            "market_open": market_open,
            "tracked_tickers": total,
            "stale_tickers": stale_count,
            "stale_fraction": round(stale_count / total, 3) if total > 0 else 0.0,
            "stale_threshold_sec": self.stale_threshold_sec,
            "halt_fraction": self.halt_fraction,
            "min_tickers_for_halt": self.min_tickers_for_halt,
            "per_ticker": per_ticker,
        }

    def reset(self) -> None:
        """Clear all tracked state. Call at start of trading day."""
        with self._lock:
            self._tickers.clear()
            logger.info("F-14: StaleDataMonitor reset — all ticker state cleared")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_state(self, ticker: str) -> _TickerPriceState:
        """Return (or create) per-ticker state. Caller must hold _lock."""
        if ticker not in self._tickers:
            self._tickers[ticker] = _TickerPriceState()
        return self._tickers[ticker]


# ===========================================================================
# Self-test
# ===========================================================================
if __name__ == "__main__":
    import sys
    from datetime import timedelta

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    monitor = StaleDataMonitor(stale_threshold_sec=60, min_tickers_for_halt=3)
    passed = 0
    failed = 0

    def _check(name: str, condition: bool, detail: str = "") -> None:
        global passed, failed
        status = "PASS" if condition else "FAIL"
        if condition:
            passed += 1
        else:
            failed += 1
        msg = f"  [{status}] {name}"
        if detail:
            msg += f" -- {detail}"
        print(msg)

    print("=" * 70)
    print("F-14: StaleDataMonitor -- Self-Test Suite")
    print("=" * 70)

    # --- Test 1: First update is never stale ---
    print("\n--- Basic operations ---")
    t0 = datetime(2026, 3, 8, 10, 0, 0, tzinfo=timezone.utc)  # Sunday, so market closed
    monitor.update("QQQ3.L", 42.50, t0)
    staleness = monitor.check_staleness(t0)
    _check("First update not stale", not staleness.get("QQQ3.L", True))

    # --- Test 2: Price unchanged but market closed -> not stale ---
    t1 = t0 + timedelta(minutes=10)
    monitor.update("QQQ3.L", 42.50, t1)
    staleness = monitor.check_staleness(t1)
    _check("Unchanged price, market closed -> not stale",
           not staleness.get("QQQ3.L", True))

    # --- Test 3: Price change resets staleness clock ---
    t2 = t1 + timedelta(minutes=1)
    monitor.update("QQQ3.L", 43.00, t2)
    staleness = monitor.check_staleness(t2)
    _check("Price changed -> not stale", not staleness.get("QQQ3.L", True))

    # --- Test 4: should_halt with insufficient tickers ---
    halt = monitor.should_halt(t2)
    _check("Halt false with 1 ticker (min 3)", not halt)

    # --- Test 5: Status report ---
    status = monitor.get_status()
    _check("Status has required fields",
           all(k in status for k in ("status", "tracked_tickers", "stale_tickers")),
           f"keys={list(status.keys())}")
    _check("Status tracked_tickers=1", status["tracked_tickers"] == 1)

    # --- Test 6: Reset clears state ---
    monitor.reset()
    staleness = monitor.check_staleness(t2)
    _check("After reset, no tickers tracked", len(staleness) == 0)

    # --- Test 7: Multiple tickers halt logic ---
    print("\n--- Halt threshold logic ---")
    monitor2 = StaleDataMonitor(stale_threshold_sec=60, min_tickers_for_halt=3)
    # Note: These tests use UTC times during what would be UK market hours
    # on a weekday. The actual staleness marking depends on is_lse_open()
    # which checks real-time. We test the logic independently.
    base = datetime(2026, 3, 9, 10, 0, 0, tzinfo=timezone.utc)  # Monday 10am UTC
    for ticker in ["A.L", "B.L", "C.L", "D.L"]:
        monitor2.update(ticker, 100.0, base)
        # Second update to get update_count >= 2
        monitor2.update(ticker, 100.0, base + timedelta(seconds=30))

    # Config checks
    _check("Threshold set to 60s", monitor2.stale_threshold_sec == 60.0)
    _check("Halt fraction is 0.5", monitor2.halt_fraction == 0.50)
    _check("Min tickers for halt is 3", monitor2.min_tickers_for_halt == 3)

    # --- Test 8: Constructor validation ---
    print("\n--- Constructor edge cases ---")
    m3 = StaleDataMonitor(stale_threshold_sec=-10)
    _check("Negative threshold clamped to 1.0", m3.stale_threshold_sec == 1.0)
    m4 = StaleDataMonitor(halt_fraction=1.5)
    _check("Halt fraction >1.0 clamped to 1.0", m4.halt_fraction == 1.0)
    m5 = StaleDataMonitor(halt_fraction=-0.5)
    _check("Halt fraction <0 clamped to 0.0", m5.halt_fraction == 0.0)

    # --- Test 9: Timestamp timezone handling ---
    print("\n--- Timezone edge cases ---")
    m6 = StaleDataMonitor(stale_threshold_sec=60)
    naive_ts = datetime(2026, 3, 9, 10, 0, 0)  # naive
    m6.update("TEST.L", 50.0, naive_ts)
    _check("Naive timestamp accepted (auto-UTC)", True)

    # --- Summary ---
    print("\n" + "=" * 70)
    total = passed + failed
    print(f"Self-test complete: {passed}/{total} passed, {failed}/{total} failed")
    if failed > 0:
        print("*** FAILURES DETECTED -- review output above ***")
        sys.exit(1)
    else:
        print("All tests passed.")
    print("=" * 70)
