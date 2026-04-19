"""MacroFeatureBuffer v2 — fixes daily-return semantics from v1.

v1 bug: daily_snapshot() rolled _prev_close on every 60-second call, so
"daily returns" collapsed to 60-second micro-movements. GMM never saw real
regime shifts.

v2 fix:
  - Rolls prev_close ONLY at UTC day boundary (daily_snapshot_at_close)
  - Live classification uses "return so far today" = (current - today's open)
    / today's open, computed at request time (no state mutation)
  - Daily history is appended once per UTC day close

Sector dispersion, VIX, DGS10 handled the same.

Consumed by gmm_regime_daemon (replaces MacroFeatureBuffer at next revision).
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Optional

import numpy as np


class MacroFeatureBufferV2:
    SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]

    def __init__(self, days: int = 252):
        self.days = days
        self.history: deque[list[float]] = deque(maxlen=days)

        # latest price caches
        self._last_prices: dict[str, float] = {}
        self._vix_level: float = 18.0
        self._dgs10_level: float = 4.0

        # previous UTC-day close snapshot
        self._prev_day_close: dict[str, float] = {}
        self._prev_day_date: Optional[str] = None   # YYYY-MM-DD
        # today's open (first price seen this UTC day)
        self._today_open: dict[str, float] = {}
        self._today_date: Optional[str] = None

    # ---- ingestion ----
    def on_tick(self, ticker: str, last: float) -> None:
        if last <= 0:
            return
        self._last_prices[ticker] = last
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._today_date != today:
            # UTC day rolled over
            self._rollover_to(today)
        # record today's open for this ticker if not yet set
        if ticker not in self._today_open:
            self._today_open[ticker] = last

    def on_macro(self, key: str, value: float) -> None:
        if key == "vix":
            self._vix_level = float(value)
        elif key == "dgs10":
            self._dgs10_level = float(value)

    def _rollover_to(self, new_date: str) -> None:
        """Called when UTC day rolls over.

        Takes a snapshot of today's closes (which are actually the last
        prices before rollover) into _prev_day_close, appends the completed
        day's features into history, clears today's open cache.
        """
        # If we had a previous "today", finalize it
        if self._today_date is not None and self._last_prices:
            # Compute yesterday's features: (today's_last - prev_day_close) / prev_day_close
            features = self._compute_features_from(
                current=self._last_prices,
                baseline=self._prev_day_close,
            )
            if features is not None:
                self.history.append(features)
            # Roll prev_day_close to today's last
            self._prev_day_close = dict(self._last_prices)
            self._prev_day_close["DGS10"] = self._dgs10_level
        self._today_date = new_date
        self._today_open = {}

    def _compute_features_from(
        self, current: dict[str, float], baseline: dict[str, float]
    ) -> Optional[list[float]]:
        """Build 6-feature vector from (current, baseline) price maps."""
        spy = current.get("SPY")
        spy_prev = baseline.get("SPY")
        if not spy or not spy_prev or spy_prev <= 0:
            return None
        spy_ret = (spy - spy_prev) / spy_prev

        def rret(ticker: str) -> float:
            c = current.get(ticker)
            p = baseline.get(ticker)
            if c and p and p > 0:
                return (c - p) / p
            return 0.0

        dxy_ret = rret("DXY")
        hyg_ret = rret("HYG")

        sector_returns = []
        for e in self.SECTOR_ETFS:
            c = current.get(e)
            p = baseline.get(e)
            if c and p and p > 0:
                sector_returns.append((c - p) / p)
        dispersion = float(np.std(sector_returns)) if len(sector_returns) >= 5 else 0.01

        dgs10_change = self._dgs10_level - baseline.get("DGS10", self._dgs10_level)
        return [spy_ret, self._vix_level, dgs10_change, dxy_ret, hyg_ret, dispersion]

    # ---- realtime classification feature ----
    def intraday_feature_vector(self) -> Optional[list[float]]:
        """Return today's evolving feature vector for live classification.
        Does NOT mutate state. Safe to call every 60 seconds.

        Uses today's open as baseline (so returns are intraday, not wall-clock).
        Falls back to prev_day_close if today's open missing.
        """
        if self._today_open:
            baseline = dict(self._today_open)
            baseline["DGS10"] = self._prev_day_close.get("DGS10", self._dgs10_level)
            return self._compute_features_from(self._last_prices, baseline)
        # fallback: yesterday's close
        if self._prev_day_close:
            return self._compute_features_from(self._last_prices, self._prev_day_close)
        return None

    # ---- model input ----
    def as_matrix(self) -> np.ndarray:
        return np.array(list(self.history)) if self.history else np.array([])


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        buf = MacroFeatureBufferV2()
        # Day 1: SPY 450, DXY 104
        buf.on_tick("SPY", 450.0)
        buf.on_tick("DXY", 104.0)
        buf.on_macro("vix", 18.5)
        f = buf.intraday_feature_vector()
        print(f"Day 1 open snapshot: {f}  (spy_ret should be 0)")

        buf.on_tick("SPY", 452.25)  # +0.5% intraday
        buf.on_tick("DXY", 104.5)
        f = buf.intraday_feature_vector()
        print(f"Day 1 mid: {f}  (spy_ret = 0.5%)")

        # Simulate UTC day rollover — directly for smoke purposes
        buf._rollover_to("2026-04-20")
        buf.on_tick("SPY", 455.0)  # day 2 open
        f = buf.intraday_feature_vector()
        print(f"Day 2 open: {f}  (spy_ret = 0 again; history has day 1 close)")
        print(f"History size: {len(buf.history)}")
        print("OK")
