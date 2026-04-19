"""GMM regime daemon — fits 4-regime GMM on macro factor history, publishes
regime.gmm on NATS every minute.

Features (daily returns + levels):
  - SPY return
  - VIX level
  - DGS10 daily change
  - DXY daily return
  - HYG daily return
  - sector dispersion (stddev of 11 sector-ETF returns)

Refits nightly (kicked by ouroboros_v3_ext via direct call, not this daemon).
Between refits, runs classify() on latest feature vector every 60s.

Consumed by signal_to_order_bridge + adaptive_gate_chain for regime-aware
sizing + gating. Complements BOCPD (univariate changepoint on SPY only).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path

import numpy as np

try:
    from nats.aio.client import Client as NATS
except ImportError:
    NATS = None

from python_brain.quant.gmm_regime import GMMRegimeClassifier, REGIME_LABELS


log = logging.getLogger("gmm-daemon")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path(os.environ.get("V5_ROOT", "/Users/rr/aegis-v5"))
HISTORY_DAYS = 252
PUBLISH_INTERVAL_S = 60


class MacroFeatureBuffer:
    """Rolling buffer of daily macro features."""

    SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]

    def __init__(self, days: int = HISTORY_DAYS):
        self.days = days
        self.history: deque[list[float]] = deque(maxlen=days)
        # last-tick cache
        self._last_prices: dict[str, float] = {}
        self._prev_close: dict[str, float] = {}
        self._vix_level: float = 18.0
        self._dgs10_level: float = 4.0
        self._dxy_last: float | None = None
        self._hyg_last: float | None = None
        self._sector_last: dict[str, float] = {}

    def on_tick(self, ticker: str, last: float):
        self._last_prices[ticker] = last
        if ticker == "SPY":
            pass  # daily snapshot on interval
        elif ticker in self.SECTOR_ETFS:
            self._sector_last[ticker] = last
        elif ticker == "DXY":
            self._dxy_last = last
        elif ticker == "HYG":
            self._hyg_last = last

    def on_macro(self, key: str, value: float):
        """key: 'vix' or 'dgs10'."""
        if key == "vix":
            self._vix_level = float(value)
        elif key == "dgs10":
            self._dgs10_level = float(value)

    def daily_snapshot(self) -> list[float] | None:
        """Compute feature vector from current + prev closes."""
        spy = self._last_prices.get("SPY")
        spy_prev = self._prev_close.get("SPY")
        if spy is None or spy_prev is None or spy_prev <= 0:
            # seed prev_close from current so next iteration has baseline
            if spy:
                self._prev_close["SPY"] = spy
            return None
        spy_ret = (spy - spy_prev) / spy_prev

        dxy_ret = 0.0
        if self._dxy_last and self._prev_close.get("DXY"):
            p = self._prev_close["DXY"]
            if p > 0:
                dxy_ret = (self._dxy_last - p) / p

        hyg_ret = 0.0
        if self._hyg_last and self._prev_close.get("HYG"):
            p = self._prev_close["HYG"]
            if p > 0:
                hyg_ret = (self._hyg_last - p) / p

        # Sector dispersion: stddev of sector returns
        sector_returns = []
        for e in self.SECTOR_ETFS:
            cur = self._sector_last.get(e)
            prev = self._prev_close.get(e)
            if cur and prev and prev > 0:
                sector_returns.append((cur - prev) / prev)
        dispersion = float(np.std(sector_returns)) if len(sector_returns) >= 5 else 0.01

        # DGS10 daily change (proxy: always 0 unless we get macro feed updates)
        dgs10_prev = self._prev_close.get("DGS10", self._dgs10_level)
        dgs10_change = self._dgs10_level - dgs10_prev

        features = [spy_ret, self._vix_level, dgs10_change, dxy_ret, hyg_ret, dispersion]

        # Roll prev_close for next snapshot
        for k, v in list(self._last_prices.items()):
            if k in ("SPY",) or k in self.SECTOR_ETFS or k in ("DXY", "HYG"):
                self._prev_close[k] = v
        self._prev_close["DGS10"] = self._dgs10_level
        return features

    def add_daily(self, features: list[float]):
        self.history.append(features)

    def as_matrix(self) -> np.ndarray:
        return np.array(list(self.history)) if self.history else np.array([])


class GMMRegimeDaemon:
    def __init__(self):
        self.buffer = MacroFeatureBuffer()
        self.classifier = GMMRegimeClassifier(n_regimes=4)
        self._last_daily_snapshot_ts = 0.0
        self._last_refit_day: int = -1

    async def run(self):
        if NATS is None:
            log.error("nats-py required")
            return
        url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
        nc = NATS()
        await nc.connect(servers=[url])

        async def on_tick(msg):
            try:
                d = json.loads(msg.data)
                t = d.get("ticker") or d.get("symbol")
                last = d.get("last") or d.get("mid") or d.get("bid")
                if t and last:
                    self.buffer.on_tick(t, float(last))
            except Exception:
                pass

        async def on_macro(msg):
            try:
                d = json.loads(msg.data)
                # intel.macro publishes {VIXCLS: ..., DGS10: ...}
                if "VIXCLS" in d:
                    self.buffer.on_macro("vix", float(d["VIXCLS"]))
                if "DGS10" in d:
                    self.buffer.on_macro("dgs10", float(d["DGS10"]))
            except Exception:
                pass

        await nc.subscribe("ticks.live.*", cb=on_tick)
        await nc.subscribe("ticks.delayed.*", cb=on_tick)
        await nc.subscribe("intel.macro", cb=on_macro)
        log.info("GMM regime daemon listening")

        while True:
            await asyncio.sleep(PUBLISH_INTERVAL_S)
            # Daily snapshot at first opportunity each UTC day
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            day = now.timetuple().tm_yday
            snap = self.buffer.daily_snapshot()
            if snap:
                self.buffer.add_daily(snap)
                # Refit classifier once per day when we have enough data
                if day != self._last_refit_day and len(self.buffer.history) >= 30:
                    X = self.buffer.as_matrix()
                    try:
                        self.classifier.fit(X)
                        self._last_refit_day = day
                        log.info("refit GMM on %d days", len(X))
                    except Exception as e:
                        log.warning("refit fail: %s", e)

                # Classify latest
                try:
                    state = self.classifier.classify(snap)
                    payload = {
                        "ts": time.time(),
                        "regime_id": state.regime_id,
                        "regime_label": state.regime_label,
                        "posterior_probs": state.posterior_probs,
                        "size_multiplier": self.classifier.size_multiplier(state.regime_id),
                        "feature_vector": snap,
                        "n_history_days": len(self.buffer.history),
                    }
                    await nc.publish("regime.gmm", json.dumps(payload).encode())
                    log.info("published regime.gmm: %s (mult=%.2f)",
                             state.regime_label,
                             self.classifier.size_multiplier(state.regime_id))
                except Exception as e:
                    log.warning("classify fail: %s", e)


async def main():
    d = GMMRegimeDaemon()
    await d.run()


if __name__ == "__main__":
    asyncio.run(main())
