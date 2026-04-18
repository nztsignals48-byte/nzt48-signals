"""Cross-portfolio halt — global kill switch on aggregate drawdown.

Monitors account.equity stream; if total DD across all strategies > 8%,
publishes risk.kill_switch triggering paper_executor to flatten.

Goes beyond per-strategy kill by catching cross-correlated blow-ups.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

try:
    from nats.aio.client import Client as NATS
except ImportError:
    NATS = None


log = logging.getLogger("xport-halt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

KILL_FLAG = Path("/Users/rr/aegis-v5/data/KILL")


class HaltMonitor:
    def __init__(self, kill_dd: float = 0.08):
        self.kill_dd = kill_dd
        self.peak_equity: float | None = None
        self.current_equity: float | None = None
        self.armed = True

    def update(self, equity: float) -> bool:
        """Return True if kill should fire."""
        self.current_equity = equity
        if self.peak_equity is None:
            self.peak_equity = equity
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity <= 0 or not self.armed:
            return False
        dd = (self.peak_equity - equity) / self.peak_equity
        if dd > self.kill_dd:
            self.armed = False  # only fire once
            return True
        return False

    def snapshot(self) -> dict:
        dd = 0.0
        if self.peak_equity and self.current_equity:
            dd = (self.peak_equity - self.current_equity) / self.peak_equity
        return {
            "peak": self.peak_equity,
            "current": self.current_equity,
            "drawdown_pct": dd,
            "kill_threshold_pct": self.kill_dd,
            "armed": self.armed,
        }


async def main():
    if NATS is None:
        log.error("nats-py required")
        return
    mon = HaltMonitor(kill_dd=float(os.environ.get("KILL_DD", "0.08")))
    url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    nc = NATS()
    await nc.connect(servers=[url])

    async def on_equity(msg):
        try:
            d = json.loads(msg.data)
            eq = float(d.get("net_liq") or d.get("equity") or d.get("current_value_usd") or 0)
            if eq <= 0:
                return
            if mon.update(eq):
                log.error("KILL SWITCH TRIGGERED: drawdown breached %.2f%%", mon.kill_dd * 100)
                KILL_FLAG.parent.mkdir(parents=True, exist_ok=True)
                KILL_FLAG.write_text(json.dumps(mon.snapshot()))
                await nc.publish("risk.kill_switch", json.dumps({
                    "ts": time.time(),
                    "reason": "cross_portfolio_drawdown",
                    **mon.snapshot(),
                }).encode())
        except Exception as e:
            log.warning("equity update fail: %s", e)

    await nc.subscribe("account.equity", cb=on_equity)
    await nc.subscribe("account.summary", cb=on_equity)
    await nc.subscribe("portfolio.equity", cb=on_equity)
    log.info("listening (kill_dd=%.2f%%)", mon.kill_dd * 100)

    while True:
        await asyncio.sleep(60)
        snap = mon.snapshot()
        await nc.publish("risk.halt.status", json.dumps(snap).encode())


if __name__ == "__main__":
    asyncio.run(main())
