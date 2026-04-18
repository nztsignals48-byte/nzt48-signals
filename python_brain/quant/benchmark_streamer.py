"""SPY benchmark streamer — computes buy-and-hold reference P&L.

Subscribes to ticks.live.SPY (or synthesizes if not available), tracks
simulated $10k-initial SPY portfolio, publishes benchmark.spy_pnl on NATS.

Consumed by Grafana v5_trading_live dashboard + ouroboros nightly for
SPY-relative alpha reporting.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

try:
    from nats.aio.client import Client as NATS
except ImportError:
    NATS = None


log = logging.getLogger("benchmark")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class SPYBenchmark:
    def __init__(self, initial_usd: float = 10000.0):
        self.initial_usd = initial_usd
        self.shares = 0.0
        self.initial_price: float | None = None
        self.last_price: float | None = None
        self.last_ts: float = 0.0

    def on_tick(self, price: float, ts: float):
        self.last_price = price
        self.last_ts = ts
        if self.initial_price is None and price > 0:
            self.initial_price = price
            self.shares = self.initial_usd / price

    def current_value_usd(self) -> float:
        if self.last_price is None or self.initial_price is None:
            return self.initial_usd
        return self.shares * self.last_price

    def pnl_usd(self) -> float:
        return self.current_value_usd() - self.initial_usd

    def pnl_pct(self) -> float:
        if self.initial_price is None:
            return 0.0
        return self.pnl_usd() / self.initial_usd * 100.0

    def snapshot(self) -> dict:
        return {
            "ts": self.last_ts,
            "initial_usd": self.initial_usd,
            "shares": self.shares,
            "initial_price": self.initial_price or 0.0,
            "current_price": self.last_price or 0.0,
            "current_value_usd": self.current_value_usd(),
            "pnl_usd": self.pnl_usd(),
            "pnl_pct": self.pnl_pct(),
        }


async def run():
    if NATS is None:
        log.error("nats-py unavailable")
        return
    url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    bench = SPYBenchmark(initial_usd=float(os.environ.get("BENCH_INITIAL_USD", "10000")))
    nc = NATS()
    await nc.connect(servers=[url])
    log.info("connected to NATS")

    async def on_spy(msg):
        try:
            data = json.loads(msg.data)
            price = data.get("last") or data.get("mid") or data.get("bid")
            ts = data.get("ts") or time.time()
            if price and price > 0:
                bench.on_tick(float(price), float(ts))
        except Exception as e:
            log.warning("bad SPY tick: %s", e)

    await nc.subscribe("ticks.live.SPY", cb=on_spy)
    await nc.subscribe("ticks.delayed.SPY", cb=on_spy)
    log.info("subscribed to ticks.live.SPY + ticks.delayed.SPY")

    last_publish = 0.0
    while True:
        await asyncio.sleep(10)
        now = time.time()
        if now - last_publish < 10:
            continue
        snap = bench.snapshot()
        try:
            await nc.publish("benchmark.spy_pnl", json.dumps(snap).encode())
            log.info("pnl=%.2f (%.2f%%)", snap["pnl_usd"], snap["pnl_pct"])
        except Exception as e:
            log.warning("publish fail: %s", e)
        last_publish = now


if __name__ == "__main__":
    asyncio.run(run())
