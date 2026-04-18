"""Venue slippage tracker — arrival-price vs fill-price per venue.

Measures implementation shortfall (IS) in bps: (fill - arrival) / arrival for BUY,
(arrival - fill) / arrival for SELL. Aggregates per venue, per order type.

Consumed by Ouroboros to tune venue preferences in impact_aware_router and
update paper_haircut estimates.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path

try:
    from nats.aio.client import Client as NATS
except ImportError:
    NATS = None


log = logging.getLogger("venue-slip")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
REPORT_PATH = ROOT / "data/venue_slippage.jsonl"


class VenueSlippageTracker:
    def __init__(self):
        # arrival prices by signal_id
        self.arrival: dict[str, dict] = {}
        # per-venue stats
        self.stats: dict[tuple[str, str], list[float]] = defaultdict(list)

    def record_arrival(self, signal_id: str, venue: str, order_type: str,
                       side: str, arrival_price: float) -> None:
        self.arrival[signal_id] = {
            "venue": venue,
            "order_type": order_type,
            "side": side,
            "arrival": arrival_price,
            "ts": time.time(),
        }

    def record_fill(self, signal_id: str, fill_price: float) -> float | None:
        info = self.arrival.pop(signal_id, None)
        if info is None:
            return None
        arrival = info["arrival"]
        if arrival <= 0:
            return None
        side = info["side"].upper()
        if side == "BUY":
            is_bps = (fill_price - arrival) / arrival * 10000
        else:
            is_bps = (arrival - fill_price) / arrival * 10000
        key = (info["venue"], info["order_type"])
        self.stats[key].append(is_bps)
        # Cap list length
        if len(self.stats[key]) > 500:
            self.stats[key] = self.stats[key][-500:]
        return is_bps

    def snapshot(self) -> dict:
        import numpy as np
        out = {"by_venue": {}, "ts": time.time()}
        for (venue, ot), samples in self.stats.items():
            if not samples:
                continue
            arr = np.array(samples)
            out["by_venue"][f"{venue}::{ot}"] = {
                "n": len(samples),
                "mean_bps": float(arr.mean()),
                "median_bps": float(np.median(arr)),
                "p95_bps": float(np.percentile(arr, 95)),
                "std_bps": float(arr.std()),
            }
        return out


async def main():
    if NATS is None:
        log.error("nats-py required")
        return

    tracker = VenueSlippageTracker()
    url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    nc = NATS()
    await nc.connect(servers=[url])

    async def on_order_submit(msg):
        try:
            d = json.loads(msg.data)
            sig = d.get("signal_id")
            venue = d.get("exchange", "SMART")
            ot = d.get("order_type", "MKT")
            side = d.get("side", "BUY")
            arrival = float(d.get("arrival_price") or d.get("expected_fill_price") or 0)
            if sig and arrival > 0:
                tracker.record_arrival(sig, venue, ot, side, arrival)
        except Exception:
            pass

    async def on_fill(msg):
        try:
            d = json.loads(msg.data)
            sig = d.get("signal_id")
            fill_px = float(d.get("fill_price") or d.get("avg_fill_price") or 0)
            if sig and fill_px > 0:
                tracker.record_fill(sig, fill_px)
        except Exception:
            pass

    await nc.subscribe("orders.submit", cb=on_order_submit)
    await nc.subscribe("orders.filled", cb=on_fill)
    log.info("listening")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    while True:
        await asyncio.sleep(300)
        snap = tracker.snapshot()
        try:
            await nc.publish("venue.slippage.report", json.dumps(snap).encode())
            with open(REPORT_PATH, "a") as f:
                f.write(json.dumps(snap) + "\n")
            log.info("%d venue/type pairs tracked", len(snap["by_venue"]))
        except Exception as e:
            log.warning("publish fail: %s", e)


if __name__ == "__main__":
    asyncio.run(main())
