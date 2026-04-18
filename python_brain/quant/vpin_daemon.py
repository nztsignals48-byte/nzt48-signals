#!/usr/bin/env python3
"""vpin_daemon — Volume-Synchronised Probability of Informed Trading.

Reads ticks.live.*, buckets trades into constant-volume buckets, and for
each ticker computes VPIN = |buy_vol - sell_vol| / total_vol per bucket,
averaged over the last N buckets.

VPIN > 0.7 = high toxicity (informed traders dominating order flow). The
sig2order bridge can subscribe to `regime.vpin.{ticker}` and veto BUY
signals when toxicity is elevated — preventing us from stepping in front
of HFT sweeps.

Emits:
  regime.vpin.{ticker}   per-ticker payload { vpin, toxicity, bucket_n, ts }
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Deque

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger("vpin")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
BUCKET_SIZE_USD = 50_000    # constant-dollar-volume bucket
ROLLING_BUCKETS = 50        # average VPIN over last N buckets
PUBLISH_INTERVAL_S = 5


@dataclass
class TickerVpin:
    cum_buy: float = 0.0
    cum_sell: float = 0.0
    cum_vol: float = 0.0
    bucket_deltas: Deque[float] = field(default_factory=lambda: deque(maxlen=ROLLING_BUCKETS))
    last_mid: float = 0.0
    last_publish_ts: float = 0.0


def classify_trade_bvc(price: float, last_mid: float) -> tuple[float, float]:
    """Bulk-Volume Classification: use price movement sign to split buy/sell.
    Returns (buy_fraction, sell_fraction). 50/50 if price unchanged."""
    if last_mid <= 0:
        return 0.5, 0.5
    delta = price - last_mid
    if abs(delta) < 1e-6:
        return 0.5, 0.5
    # sigmoid on normalized delta
    z = delta / (last_mid * 0.0001)  # normalize by 1bp
    buy_frac = 1.0 / (1.0 + math.exp(-z))
    return buy_frac, 1.0 - buy_frac


async def run() -> None:
    import nats  # type: ignore
    nc = await nats.connect(NATS_URL, name="aegis-v5-vpin-daemon")
    log.info("vpin daemon connected to NATS")

    state: Dict[str, TickerVpin] = defaultdict(TickerVpin)

    async def on_tick(msg):
        try:
            d = json.loads(msg.data)
        except Exception:
            return
        ticker = d.get("ticker")
        if not ticker:
            return
        last = d.get("last")
        last_size = d.get("last_size") or 0
        if not last or last <= 0 or not last_size or last_size <= 0:
            return
        try:
            price = float(last)
            size = float(last_size)
        except Exception:
            return

        st = state[ticker]
        if st.last_mid <= 0:
            st.last_mid = price
            return

        buy_frac, sell_frac = classify_trade_bvc(price, st.last_mid)
        dollar_vol = price * size
        st.cum_buy += dollar_vol * buy_frac
        st.cum_sell += dollar_vol * sell_frac
        st.cum_vol += dollar_vol
        st.last_mid = price

        # Close bucket when we hit BUCKET_SIZE_USD
        while st.cum_vol >= BUCKET_SIZE_USD:
            # pro-rate this bucket's buy/sell
            frac = BUCKET_SIZE_USD / st.cum_vol
            bucket_buy = st.cum_buy * frac
            bucket_sell = st.cum_sell * frac
            delta = abs(bucket_buy - bucket_sell) / BUCKET_SIZE_USD
            st.bucket_deltas.append(delta)
            st.cum_buy -= bucket_buy
            st.cum_sell -= bucket_sell
            st.cum_vol -= BUCKET_SIZE_USD

        # Periodic publish
        now = time.time()
        if len(st.bucket_deltas) >= 5 and (now - st.last_publish_ts) >= PUBLISH_INTERVAL_S:
            st.last_publish_ts = now
            vpin = sum(st.bucket_deltas) / len(st.bucket_deltas)
            if vpin > 0.7:
                toxicity = "high"
            elif vpin > 0.4:
                toxicity = "elevated"
            else:
                toxicity = "normal"
            try:
                await nc.publish(f"regime.vpin.{ticker}", json.dumps({
                    "ts": now,
                    "ticker": ticker,
                    "vpin": round(vpin, 4),
                    "toxicity": toxicity,
                    "bucket_n": len(st.bucket_deltas),
                }).encode())
            except Exception:
                pass

    await nc.subscribe("ticks.live.*", cb=on_tick)
    log.info("vpin daemon listening on ticks.live.*")

    while True:
        await asyncio.sleep(60)
        active = sum(1 for v in state.values() if len(v.bucket_deltas) >= 5)
        log.info("vpin state: %d tickers tracked, %d with ≥5 buckets",
                 len(state), active)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
