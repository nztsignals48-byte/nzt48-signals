"""Forwards signals.gated → signals.core (marked) for sig2order consumption.

adaptive_gate_chain.py publishes to signals.gated after applying 9 gates.
signal_to_order_bridge.py subscribes to signals.core.

This forwarder bridges them: consumes signals.gated, adds _gated_pass: true
marker, republishes to signals.post_gated. This new subject is what sig2order
should subscribe to in the upgraded pipeline.

Supervisor v3_ext should register this + a sig2order variant that listens on
signals.post_gated. For now, forwarder alone is the non-invasive bridge.
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


log = logging.getLogger("gated-fwd")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class GatedForwarder:
    def __init__(self):
        self.count_in = 0
        self.count_out = 0

    async def run(self):
        if NATS is None:
            log.error("nats-py required")
            return
        url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
        nc = NATS()
        await nc.connect(servers=[url])

        async def on_gated(msg):
            try:
                s = json.loads(msg.data)
                self.count_in += 1
                # Tag as already-gated so consumers can skip re-gating
                s["_gated_pass"] = True
                s["_forwarded_ts"] = time.time()
                # Publish to a new subject that sig2order (upgraded) subscribes to
                await nc.publish("signals.post_gated", json.dumps(s).encode())
                self.count_out += 1
            except Exception as e:
                log.warning("forward fail: %s", e)

        await nc.subscribe("signals.gated", cb=on_gated)
        log.info("gated forwarder listening: signals.gated → signals.post_gated")

        while True:
            await asyncio.sleep(60)
            log.info("forwarded: %d in / %d out", self.count_in, self.count_out)


async def main():
    fwd = GatedForwarder()
    await fwd.run()


if __name__ == "__main__":
    asyncio.run(main())
