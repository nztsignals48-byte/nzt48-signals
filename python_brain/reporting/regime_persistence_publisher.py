"""Live regime persistence publisher.

Subscribes regime.current; tracks regime transitions; publishes
regime.persistence — "current regime has X days expected remaining".

Consumed by exit engine + sig2order for exit timing / sizing adjustments.
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

from python_brain.quant.regime_persistence import (
    expected_remaining_duration,
    persistence_multiplier,
    record_regime_transition,
)


log = logging.getLogger("regime-pers")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class PersistenceTracker:
    def __init__(self):
        self.current_regime: str | None = None
        self.regime_start_ts: float = 0

    def on_regime(self, regime: str) -> dict:
        now = time.time()
        if self.current_regime is None:
            self.current_regime = regime
            self.regime_start_ts = now
            return {"regime": regime, "run_days": 0}

        if regime != self.current_regime:
            # Transition: record duration
            duration_days = (now - self.regime_start_ts) / 86400
            record_regime_transition(self.current_regime, duration_days)
            log.info("regime transition: %s -> %s after %.2f days",
                     self.current_regime, regime, duration_days)
            self.current_regime = regime
            self.regime_start_ts = now

        run_days = (now - self.regime_start_ts) / 86400
        info = expected_remaining_duration(self.current_regime, run_days)
        mult = persistence_multiplier(self.current_regime, run_days)
        return {
            "ts": now,
            "regime": self.current_regime,
            "run_days": run_days,
            "expected_remaining_days": info.get("expected_remaining_days"),
            "persistence_multiplier": mult,
            "n_observations": info.get("n_observations", 0),
        }


async def main():
    if NATS is None:
        return
    tracker = PersistenceTracker()
    url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    nc = NATS()
    await nc.connect(servers=[url])

    async def on_regime(msg):
        try:
            d = json.loads(msg.data)
            regime = d.get("regime") or d.get("regime_label")
            if regime:
                snap = tracker.on_regime(regime)
                await nc.publish("regime.persistence", json.dumps(snap).encode())
        except Exception as e:
            log.warning("regime update fail: %s", e)

    await nc.subscribe("regime.current", cb=on_regime)
    log.info("regime persistence publisher listening")

    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
