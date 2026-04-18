"""Standalone launcher for the capital bandit daemon (used by supervisor).

Uses capital_bandit_v2.ThompsonCapitalBanditV2 so nightly writes to
learned.toml are idempotent (no duplicate [bandit] blocks).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path("/Users/rr/aegis-v5")
sys.path.insert(0, str(ROOT))

try:
    from nats.aio.client import Client as NATS
except ImportError:
    NATS = None

from python_brain.quant.capital_bandit_v2 import ThompsonCapitalBanditV2


log = logging.getLogger("bandit-launcher")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def run_daemon_v2():
    if NATS is None:
        log.error("nats-py required")
        return
    bandit = ThompsonCapitalBanditV2()
    url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    nc = NATS()
    await nc.connect(servers=[url])

    async def on_fill(msg):
        try:
            d = json.loads(msg.data)
            strat = d.get("strategy_name") or d.get("strategy", "unknown")
            pnl = d.get("realized_pnl_bps")
            if pnl is None:
                pnl = d.get("realized_pnl_gbp", 0)
            bandit.update(strat, float(pnl))
            bandit._save()
        except Exception as e:
            log.warning("fill update fail: %s", e)

    await nc.subscribe("orders.filled", cb=on_fill)
    await nc.subscribe("fills.closed", cb=on_fill)
    log.info("capital bandit v2 daemon listening")

    while True:
        await asyncio.sleep(300)
        snap = bandit.snapshot()
        try:
            await nc.publish("bandit.kelly", json.dumps(snap).encode())
        except Exception:
            pass
        bandit.write_learned_toml()  # idempotent v2 writer
        log.info("bandit v2 snapshot: %d strategies",
                 len(bandit.state.priors))


if __name__ == "__main__":
    asyncio.run(run_daemon_v2())
