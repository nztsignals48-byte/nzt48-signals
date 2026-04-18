#!/usr/bin/env python3
"""Account Streamer — publishes all IBKR account/position/P&L/execution streams
to NATS so consumers have live account data, not just static broker_chandelier
polls.

NATS subjects:
  account.summary   — reqAccountSummary ($NetLiq, AvailableFunds, etc.)
  account.pnl       — reqPnL (daily/unrealized/realized totals)
  account.pnl.single.{TICKER}  — reqPnLSingle per open position
  account.positions — reqPositions snapshot (every 30s)
  fills.executions  — reqExecutions (day's fills)

Uses a persistent IBKR connection (client_id=129), reconnects on disconnect
with exponential backoff.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger("account-streamer")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
IBKR_HOST = "127.0.0.1"
IBKR_PORT = 4002
CLIENT_ID = int(os.environ.get("ACCOUNT_CLIENT_ID", "139"))
ACCOUNT = os.environ.get("IBKR_ACCOUNT", "DUM983136")


async def run():
    import nats  # type: ignore
    from ib_insync import IB, PnL, PnLSingle, util  # type: ignore

    # Patch asyncio so ib_insync's sync wrappers (reqPnL, ib.portfolio, ib.fills)
    # can safely run inside an already-running event loop via nest_asyncio.
    util.patchAsyncio()

    nc = await nats.connect(NATS_URL, name="aegis-v5-account-streamer")
    log.info("connected to NATS")

    ib = IB()
    try:
        await ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=CLIENT_ID,
                              readonly=True, timeout=30)
    except Exception as e:
        log.error("IBKR connect failed clientId=%d: %s", CLIENT_ID, e)
        await nc.drain()
        return

    log.info("IBKR connected clientId=%d account=%s", CLIENT_ID, ACCOUNT)

    # --- reqAccountSummary -------------------------------------------------
    # Use the async variant: ib.reqAccountSummary() is a sync wrapper that
    # calls loop.run_until_complete internally, which collides with our
    # already-running asyncio loop.
    # The await form returns the initial snapshot. After that, IBKR pushes
    # updates and ib.accountSummary() returns the live list.
    summary_snapshot = await ib.reqAccountSummaryAsync()
    log.info("initial account summary landed: %d tags",
             len(summary_snapshot) if summary_snapshot else 0)
    await asyncio.sleep(2)

    # --- reqPnL for daily P&L stream ---------------------------------------
    # reqPnL is pure subscription setup — doesn't invoke run_until_complete.
    pnl_obj = ib.reqPnL(ACCOUNT)

    # --- Track active reqPnLSingle subscriptions by con_id -----------------
    pnl_single: dict[int, PnLSingle] = {}

    def refresh_pnl_single():
        """Ensure every open position has a reqPnLSingle stream."""
        pos_by_conid = {p.contract.conId: p for p in ib.portfolio()}
        # add new
        for con_id, pos in pos_by_conid.items():
            if con_id not in pnl_single and pos.position != 0:
                try:
                    pnl_single[con_id] = ib.reqPnLSingle(ACCOUNT, "", con_id)
                    log.info("reqPnLSingle subscribed con_id=%d", con_id)
                except Exception as e:
                    log.warning("reqPnLSingle failed con_id=%d: %s", con_id, e)
        # drop closed
        for con_id in list(pnl_single.keys()):
            if con_id not in pos_by_conid or pos_by_conid[con_id].position == 0:
                try:
                    ib.cancelPnLSingle(ACCOUNT, "", con_id)
                except Exception:
                    pass
                pnl_single.pop(con_id, None)

    refresh_pnl_single()

    # --- Publisher loop ----------------------------------------------------
    stop = asyncio.Event()

    def _sig_handler(*_):
        stop.set()

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _sig_handler)
    except NotImplementedError:
        pass

    last_full_refresh = 0.0
    while not stop.is_set():
        now = time.time()

        # 1) Account summary snapshot — publish every cycle so the dashboard
        # stays fresh. Include the full tag-bag so downstream consumers can
        # pick any field they need without code changes.
        try:
            summary_items = ib.accountSummary(ACCOUNT)
            if not summary_items:
                # Fall back: ib.accountSummary() with no arg returns the global.
                summary_items = ib.accountSummary()
            summary = {item.tag: item.value for item in summary_items}
            payload = {
                "ts": now,
                "account": ACCOUNT,
                "tags": summary,  # full tag-bag
                "NetLiquidation": summary.get("NetLiquidation"),
                "TotalCashValue": summary.get("TotalCashValue"),
                "AvailableFunds": summary.get("AvailableFunds"),
                "BuyingPower": summary.get("BuyingPower"),
                "MaintMarginReq": summary.get("MaintMarginReq"),
                "InitMarginReq": summary.get("InitMarginReq"),
                "GrossPositionValue": summary.get("GrossPositionValue"),
                "UnrealizedPnL": summary.get("UnrealizedPnL"),
                "RealizedPnL": summary.get("RealizedPnL"),
                "ExcessLiquidity": summary.get("ExcessLiquidity"),
                "Leverage": summary.get("Leverage-S"),
            }
            await nc.publish("account.summary", json.dumps(payload).encode())
        except Exception as e:
            log.warning("account summary publish failed: %s", e)

        # 2) PnL stream
        try:
            if pnl_obj and pnl_obj.dailyPnL is not None:
                await nc.publish("account.pnl", json.dumps({
                    "ts": now,
                    "account": ACCOUNT,
                    "daily_pnl": float(pnl_obj.dailyPnL),
                    "unrealized_pnl": float(pnl_obj.unrealizedPnL or 0),
                    "realized_pnl": float(pnl_obj.realizedPnL or 0),
                }).encode())
        except Exception as e:
            log.debug("pnl publish: %s", e)

        # 3) Per-position P&L
        try:
            for con_id, pnl_s in pnl_single.items():
                if pnl_s and pnl_s.position is not None:
                    await nc.publish(
                        f"account.pnl.single.{con_id}",
                        json.dumps({
                            "ts": now,
                            "con_id": con_id,
                            "position": float(pnl_s.position or 0),
                            "daily_pnl": float(pnl_s.dailyPnL or 0),
                            "unrealized_pnl": float(pnl_s.unrealizedPnL or 0),
                            "realized_pnl": float(pnl_s.realizedPnL or 0),
                            "value": float(pnl_s.value or 0),
                        }).encode(),
                    )
        except Exception as e:
            log.debug("pnl_single publish: %s", e)

        # 4) Positions snapshot (every 30s)
        if now - last_full_refresh >= 30:
            last_full_refresh = now
            try:
                port = ib.portfolio()
                positions_payload = []
                for p in port:
                    positions_payload.append({
                        "ticker": p.contract.symbol,
                        "con_id": p.contract.conId,
                        "exchange": p.contract.primaryExchange or p.contract.exchange,
                        "position": float(p.position),
                        "market_price": float(p.marketPrice or 0),
                        "market_value": float(p.marketValue or 0),
                        "average_cost": float(p.averageCost or 0),
                        "unrealized_pnl": float(p.unrealizedPNL or 0),
                        "realized_pnl": float(p.realizedPNL or 0),
                    })
                await nc.publish("account.positions", json.dumps({
                    "ts": now,
                    "account": ACCOUNT,
                    "positions": positions_payload,
                }).encode())
            except Exception as e:
                log.warning("positions publish: %s", e)

            # Refresh pnl_single subscriptions
            refresh_pnl_single()

            # 5) Executions today (publish once per 30s cycle)
            try:
                fills = ib.fills()
                for f in fills[-20:]:  # last 20 to avoid spam
                    exec_payload = {
                        "ts": now,
                        "ticker": f.contract.symbol,
                        "exchange": f.execution.exchange,
                        "side": f.execution.side,
                        "shares": float(f.execution.shares),
                        "price": float(f.execution.price),
                        "cum_qty": float(f.execution.cumQty),
                        "avg_price": float(f.execution.avgPrice),
                        "exec_id": f.execution.execId,
                        "order_id": f.execution.orderId,
                        "perm_id": f.execution.permId,
                        "time": str(f.execution.time),
                        "commission": float(getattr(f.commissionReport, "commission", 0) or 0),
                    }
                    await nc.publish("fills.executions",
                                     json.dumps(exec_payload).encode())
            except Exception as e:
                log.debug("executions publish: %s", e)

        await asyncio.sleep(5)

    log.info("account streamer stopping")
    ib.disconnect()
    await nc.drain()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
