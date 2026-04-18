#!/usr/bin/env python3
"""kill_switch — emergency-flatten service.

Listens on NATS `risk.kill` — any message with {"reason": "..."} triggers
reqGlobalCancel (cancel all working orders) + optionally fires SELL-all via
orders.submit for each held position.

Also owns the equity-drawdown auto-kill: if portfolio.equity shows
drawdown_pct > 8%, auto-publish a risk.kill internally.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger("kill-switch")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
IBKR_HOST = "127.0.0.1"
IBKR_PORT = 4002
CLIENT_ID = int(os.environ.get("KILL_SWITCH_CLIENT_ID", "141"))
ACCOUNT = os.environ.get("IBKR_ACCOUNT", "DUM983136")
DRAWDOWN_KILL_PCT = float(os.environ.get("DRAWDOWN_KILL_PCT", "8.0"))


async def run():
    import nats  # type: ignore
    from ib_insync import IB, MarketOrder, util  # type: ignore

    util.patchAsyncio()
    nc = await nats.connect(NATS_URL, name="aegis-v5-kill-switch")
    log.info("connected to NATS")

    ib = IB()
    try:
        await ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=CLIENT_ID, timeout=30)
    except Exception as e:
        log.error("IBKR connect failed cid=%d: %s", CLIENT_ID, e)
        await nc.drain()
        return
    log.info("IBKR connected clientId=%d", CLIENT_ID)

    killed = {"count": 0}

    async def flatten_all(reason: str):
        killed["count"] += 1
        log.warning("KILL TRIGGERED (#%d) reason=%s — calling reqGlobalCancel",
                    killed["count"], reason)
        try:
            ib.reqGlobalCancel()
        except Exception as e:
            log.error("reqGlobalCancel failed: %s", e)

        # Then flatten every position
        try:
            port = ib.portfolio()
            flat_count = 0
            for p in port:
                if p.position == 0:
                    continue
                side = "SELL" if p.position > 0 else "BUY"
                qty = int(abs(p.position))
                try:
                    order = {
                        "signal_id": f"kill-{int(time.time())}-{p.contract.symbol}",
                        "ticker": p.contract.symbol,
                        "exchange": p.contract.primaryExchange or p.contract.exchange or "SMART",
                        "currency": p.contract.currency or "USD",
                        "con_id": p.contract.conId,
                        "side": side,
                        "qty": qty,
                        "order_type": "MKT",
                        "strategy": "kill_switch",
                        "account": ACCOUNT,
                        "exit_reason": f"KILL: {reason}",
                    }
                    await nc.publish("orders.submit",
                                     json.dumps(order).encode("utf-8"))
                    flat_count += 1
                except Exception as e:
                    log.warning("flatten %s failed: %s", p.contract.symbol, e)
            log.warning("KILL flattened %d positions via orders.submit", flat_count)
        except Exception as e:
            log.error("flatten_all: %s", e)

    async def on_kill(msg):
        try:
            d = json.loads(msg.data)
        except Exception:
            d = {"reason": "unparseable"}
        reason = d.get("reason", "manual")
        await flatten_all(reason)

    async def on_equity(msg):
        try:
            d = json.loads(msg.data)
            dd = float(d.get("drawdown_pct") or 0)
            if dd > DRAWDOWN_KILL_PCT:
                log.warning("equity drawdown %.2f%% > %.2f%% — auto-killing",
                            dd, DRAWDOWN_KILL_PCT)
                await flatten_all(f"drawdown_{dd:.2f}pct")
        except Exception:
            pass

    await nc.subscribe("risk.kill", cb=on_kill)
    await nc.subscribe("portfolio.equity", cb=on_equity)
    log.info("kill switch armed (drawdown auto-kill > %.1f%%)", DRAWDOWN_KILL_PCT)

    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
