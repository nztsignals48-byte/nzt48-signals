"""Sector regime daemon — maintains per-sector (XLK/XLF/XLE/...) regime state
from live ETF ticks and publishes regime.sector.{ETF} on NATS.

A "calm" SPY can hide a "crisis" semiconductor. Strategies trading tech
stocks should receive XLK's regime multiplier, not the broad-market one.

Consumed by signal_to_order_bridge: per-signal ticker mapped to sector ETF,
then regime multiplier applied.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque

try:
    from nats.aio.client import Client as NATS
except ImportError:
    NATS = None

from python_brain.quant.sector_regime import (
    SectorRegimeDetector,
    SECTOR_ETFS,
    TICKER_SECTOR,
)


log = logging.getLogger("sector-daemon")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


WINDOW_DAYS = 30
PUBLISH_INTERVAL_S = 60


class TickReturnBuilder:
    """Convert tick stream to daily-like return updates per symbol."""

    def __init__(self):
        self._last_price: dict[str, float] = {}
        self._prev_snapshot: dict[str, float] = {}
        self._last_snapshot_ts: float = 0.0
        self.SNAPSHOT_INTERVAL_S = 3600  # hourly snapshot — fresher than daily for intraday regime

    def on_tick(self, ticker: str, last: float):
        self._last_price[ticker] = last

    def pending_returns(self) -> dict[str, float]:
        """Return dict of {ticker: return_since_last_snapshot}, updating state."""
        now = time.time()
        if now - self._last_snapshot_ts < self.SNAPSHOT_INTERVAL_S:
            return {}
        out: dict[str, float] = {}
        for t, cur in self._last_price.items():
            prev = self._prev_snapshot.get(t)
            if prev and prev > 0:
                out[t] = (cur - prev) / prev
            self._prev_snapshot[t] = cur
        self._last_snapshot_ts = now
        return out


class SectorRegimeDaemon:
    def __init__(self):
        self.detector = SectorRegimeDetector(window_days=WINDOW_DAYS)
        self.returns_builder = TickReturnBuilder()

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
                if t and last and t in SECTOR_ETFS:
                    self.returns_builder.on_tick(t, float(last))
            except Exception:
                pass

        await nc.subscribe("ticks.live.*", cb=on_tick)
        await nc.subscribe("ticks.delayed.*", cb=on_tick)
        log.info("sector regime daemon listening for sector ETFs: %s",
                 list(SECTOR_ETFS.keys()))

        last_publish = 0.0
        while True:
            await asyncio.sleep(PUBLISH_INTERVAL_S)
            new_returns = self.returns_builder.pending_returns()
            for etf, ret in new_returns.items():
                self.detector.add_return(etf, ret)

            now = time.time()
            # Publish snapshot periodically
            if now - last_publish >= PUBLISH_INTERVAL_S:
                published = 0
                for etf in SECTOR_ETFS:
                    state = self.detector.classify(etf)
                    if state.regime == "uninitialized":
                        continue
                    payload = {
                        "ts": now,
                        "etf": etf,
                        "sector": state.sector,
                        "regime": state.regime,
                        "vol_annualized": state.vol_annualized,
                        "trend_strength": state.trend_strength,
                        "size_multiplier": state.size_multiplier,
                    }
                    try:
                        await nc.publish(f"regime.sector.{etf}", json.dumps(payload).encode())
                        published += 1
                    except Exception:
                        pass
                if published:
                    log.info("published %d sector regime states", published)
                last_publish = now


async def main():
    d = SectorRegimeDaemon()
    await d.run()


if __name__ == "__main__":
    asyncio.run(main())
