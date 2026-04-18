"""Live marginal VaR attribution publisher.

Subscribes portfolio.equity + position snapshots; computes marginal VaR per
position using real return history; publishes risk.marginal_var report.

Consumes marginal_var_attribution.py (Phase E).
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

from python_brain.risk.marginal_var_attribution import attribute


log = logging.getLogger("mvar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class MVaRPublisher:
    def __init__(self, return_history_days: int = 30):
        self.returns: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=return_history_days * 6))
        self.positions: dict[str, float] = {}
        self.last_prices: dict[str, float] = {}

    def on_tick(self, ticker: str, price: float):
        prev = self.last_prices.get(ticker)
        if prev and prev > 0:
            ret = (price - prev) / prev
            self.returns[ticker].append(ret)
        self.last_prices[ticker] = price

    def on_position(self, ticker: str, usd: float):
        if abs(usd) < 1:
            self.positions.pop(ticker, None)
        else:
            self.positions[ticker] = usd

    def compute(self) -> dict:
        if len(self.positions) < 2:
            return {"n_positions": len(self.positions), "total_var": 0.0}

        tickers = [t for t in self.positions if len(self.returns[t]) >= 10]
        if len(tickers) < 2:
            return {"n_positions": len(self.positions), "total_var": 0.0}

        min_len = min(len(self.returns[t]) for t in tickers)
        returns_matrix = np.array([[r for r in list(self.returns[t])[-min_len:]] for t in tickers])
        cov = np.cov(returns_matrix)
        if cov.ndim == 0:
            cov = np.array([[float(cov)]])

        total_value = sum(self.positions[t] for t in tickers)
        weights = np.array([self.positions[t] / total_value for t in tickers])
        total_var, contribs = attribute(tickers, weights, cov)
        return {
            "ts": time.time(),
            "n_positions": len(tickers),
            "total_value_usd": float(total_value),
            "var_fraction": float(total_var),
            "contributors": [
                {"symbol": c.symbol, "pct": round(c.pct_contribution, 2)}
                for c in contribs[:10]
            ],
        }


async def main():
    if NATS is None:
        return
    pub = MVaRPublisher()
    url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    nc = NATS()
    await nc.connect(servers=[url])

    async def on_tick(msg):
        try:
            d = json.loads(msg.data)
            t = d.get("ticker") or d.get("symbol")
            p = d.get("last") or d.get("mid") or d.get("bid")
            if t and p and p > 0:
                pub.on_tick(t, float(p))
        except Exception:
            pass

    async def on_position(msg):
        try:
            d = json.loads(msg.data)
            t = d.get("ticker")
            v = d.get("market_value") or (d.get("qty", 0) * d.get("price", 0))
            if t:
                pub.on_position(t, float(v))
        except Exception:
            pass

    await nc.subscribe("ticks.live.*", cb=on_tick)
    await nc.subscribe("account.positions", cb=on_position)
    log.info("marginal VaR publisher listening")

    while True:
        await asyncio.sleep(60)
        snap = pub.compute()
        try:
            await nc.publish("risk.marginal_var", json.dumps(snap).encode())
            if snap["n_positions"] > 0:
                log.info("VaR contribs top=%s", snap.get("contributors", [])[:3])
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
