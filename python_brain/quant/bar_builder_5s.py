#!/usr/bin/env python3
"""bar_builder_5s — tick-aggregated 5-second OHLCV bars.

Replacement for reqRealTimeBars: consumes ticks.live.*, builds 5-second
rolling OHLCV bars per ticker, publishes bars.5s.{ticker}.

Why not reqRealTimeBars directly: it consumes a per-ticker IBKR slot we
can't spare. Aggregating from ticks uses zero IBKR slots.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger("bar-5s")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
BAR_WIDTH_S = 5


@dataclass
class Bar5s:
    start_s: int = 0
    o: float = 0.0
    h: float = 0.0
    l: float = float("inf")
    c: float = 0.0
    v: float = 0.0
    last_vol_cum: float = 0.0


async def run() -> None:
    import nats  # type: ignore
    nc = await nats.connect(NATS_URL, name="aegis-v5-bar-5s")
    log.info("bar builder 5s connected to NATS")

    bars: Dict[str, Bar5s] = defaultdict(Bar5s)

    async def on_tick(msg):
        try:
            d = json.loads(msg.data)
        except Exception:
            return
        t = d.get("ticker")
        price = d.get("last")
        if not t or not price:
            return
        try:
            price = float(price)
        except Exception:
            return
        if price <= 0:
            return

        now_s = int(time.time())
        bucket_start = (now_s // BAR_WIDTH_S) * BAR_WIDTH_S

        b = bars[t]
        if b.start_s == 0:
            b.start_s = bucket_start
            b.o = price
            b.h = price
            b.l = price
            b.c = price
            b.last_vol_cum = float(d.get("volume") or 0)
        elif bucket_start != b.start_s:
            # close & publish previous bar
            if b.o > 0 and b.start_s > 0:
                payload = {
                    "ticker": t,
                    "start": b.start_s,
                    "open": b.o,
                    "high": b.h,
                    "low": b.l,
                    "close": b.c,
                    "volume": b.v,
                    "width_s": BAR_WIDTH_S,
                }
                try:
                    await nc.publish(f"bars.5s.{t}", json.dumps(payload).encode())
                except Exception:
                    pass
            # Reset for new bucket
            b.start_s = bucket_start
            b.o = price
            b.h = price
            b.l = price
            b.c = price
            b.v = 0.0
        else:
            # Accumulate
            b.c = price
            if price > b.h:
                b.h = price
            if price < b.l:
                b.l = price
        # Volume delta from cumulative
        cum = float(d.get("volume") or 0)
        if cum > b.last_vol_cum:
            b.v += cum - b.last_vol_cum
            b.last_vol_cum = cum

    await nc.subscribe("ticks.live.*", cb=on_tick)
    log.info("bar builder listening on ticks.live.*")

    while True:
        await asyncio.sleep(60)
        active = sum(1 for b in bars.values() if b.o > 0)
        log.info("bars state: %d tickers tracked, %d active bars", len(bars), active)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
