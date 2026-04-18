"""exit_to_order_bridge — turns engine TradeClosed events into real SELL orders.

The engine's exit_engine decides to flatten a position (Chandelier, etc.) and
publishes `fills.closed` on NATS. That only closes the engine's IN-MEMORY
portfolio — the real IBKR paper position stays open. This bridge:

  subscribe: fills.closed
  publish:   orders.submit  (side=SELL, MKT, matching qty)

Tracks outstanding_real_qty per ticker (from orders.filled buys) so it
doesn't try to SELL more than actually owned.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


# Mapping from signal_id -> (ticker, con_id, qty, currency) at BUY fill time,
# so we can match TradeClosed events back to the correct BUY for a SELL.
@dataclass
class OpenPos:
    ticker: str
    con_id: int
    currency: str
    qty: int
    entry_price: float
    strategy: str


@dataclass
class ExitToOrderBridge:
    nats_url: str = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    account: str = os.environ.get("IBKR_ACCOUNT", "DUM983136")

    open_by_signal: Dict[str, OpenPos] = field(default_factory=dict)
    open_qty_by_ticker: Dict[str, int] = field(default_factory=dict)

    async def run(self) -> None:
        import nats  # type: ignore
        nc = await nats.connect(self.nats_url, name="aegis-v5-exit-to-order")
        log.info("exit bridge connected to NATS %s", self.nats_url)

        async def on_submit(msg):
            # Mirror submitted BUYs so we know what's open, keyed by signal_id.
            try:
                o = json.loads(msg.data)
                if o.get("side", "").upper() != "BUY":
                    return
                sid = o.get("signal_id") or ""
                if not sid:
                    return
                self.open_by_signal[sid] = OpenPos(
                    ticker=o["ticker"],
                    con_id=int(o.get("con_id") or 0),
                    currency=o.get("currency", "USD"),
                    qty=int(o.get("qty") or 0),
                    entry_price=0.0,
                    strategy=o.get("strategy") or "?",
                )
            except Exception:
                pass

        async def on_fill(msg):
            try:
                f = json.loads(msg.data)
                sig_id = f.get("signal_id") or ""
                side = (f.get("side") or "").upper()
                t = f.get("ticker")
                qty = int(f.get("filled_qty") or 0)
                if side == "BUY" and t:
                    self.open_qty_by_ticker[t] = self.open_qty_by_ticker.get(t, 0) + qty
                    if sig_id in self.open_by_signal:
                        self.open_by_signal[sig_id].entry_price = float(f.get("avg_price") or 0)
                elif side == "SELL" and t:
                    self.open_qty_by_ticker[t] = max(
                        0, self.open_qty_by_ticker.get(t, 0) - qty
                    )
            except Exception as e:
                log.warning("fill parse: %s", e)

        async def on_closed(msg):
            # fills.closed is the engine's internal "position flatten" signal.
            try:
                c = json.loads(msg.data)
            except Exception:
                return
            sid = c.get("signal_id") or ""
            if sid not in self.open_by_signal:
                log.debug("close for unknown signal_id=%s; skipping", sid)
                return
            pos = self.open_by_signal.pop(sid)
            # Check we really have those shares with the broker.
            have = self.open_qty_by_ticker.get(pos.ticker, 0)
            sell_qty = min(pos.qty, have)
            if sell_qty <= 0:
                log.info("close %s: no broker qty to SELL (have=%d pos=%d)",
                         pos.ticker, have, pos.qty)
                return
            if not pos.con_id:
                log.warning("close %s: no con_id; cannot SELL", pos.ticker)
                return
            order = {
                "signal_id": f"exit-{sid[:8]}-{int(time.time())}",
                "ticker": pos.ticker,
                "exchange": "SMART",
                "currency": pos.currency,
                "con_id": pos.con_id,
                "side": "SELL",
                "qty": sell_qty,
                "order_type": "MKT",
                "strategy": pos.strategy,
                "account": self.account,
                "exit_reason": c.get("exit_reason", "engine_flatten"),
            }
            await nc.publish("orders.submit", json.dumps(order).encode("utf-8"))
            # positions.close — lets rotator release this ticker for eviction
            try:
                await nc.publish("positions.close", json.dumps({
                    "ts": time.time(),
                    "ticker": pos.ticker,
                    "con_id": pos.con_id,
                    "qty": sell_qty,
                    "strategy": pos.strategy,
                    "exit_reason": order["exit_reason"],
                }).encode("utf-8"))
            except Exception:
                pass
            log.info("SELL submitted: %s qty=%d reason=%s (entry $%.2f)",
                     pos.ticker, sell_qty, order["exit_reason"], pos.entry_price)

        await nc.subscribe("orders.submit", cb=on_submit)
        await nc.subscribe("orders.filled", cb=on_fill)
        await nc.subscribe("fills.closed", cb=on_closed)

        log.info("listening on fills.closed + orders.submit + orders.filled")
        while True:
            await asyncio.sleep(60)
            log.info("state: %d open_signals %d tickers with qty",
                     len(self.open_by_signal), sum(1 for v in self.open_qty_by_ticker.values() if v > 0))


if __name__ == "__main__":
    asyncio.run(ExitToOrderBridge().run())
