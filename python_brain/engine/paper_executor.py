"""Paper order executor — consumes orders.submit from NATS, places them via
IBKR paper account, publishes orders.filled on fill.

client_id=108 dedicated to order placement (separate from live-tick bridge on
105 and scanner on 107). Paper-only for Phase 12.

NATS subjects:
    orders.submit   (consumed)
    orders.filled   (published)
    orders.reject   (published)

Expected orders.submit payload:
    {
        "signal_id": "uuid",
        "ticker": "AAPL",
        "exchange": "SMART",
        "currency": "USD",
        "con_id": 265598,
        "side": "BUY" | "SELL",
        "qty": 100,
        "order_type": "MKT" | "LMT",
        "limit_price": 197.50,           # if LMT
        "strategy": "sentiment_long_short",
        "account": "DEMOXXXX"            # optional
    }

Paper fills arrive via ib_insync's trade.fillEvent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@dataclass
class PaperExecutor:
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002
    ibkr_client_id: int = 108
    nats_url: str = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")

    async def run(self) -> None:
        import nats  # type: ignore
        from ib_insync import IB, Contract, MarketOrder, LimitOrder  # type: ignore

        nc = await nats.connect(self.nats_url, name="aegis-v5-paper-executor")
        log.info("executor connected to NATS %s", self.nats_url)

        ib = IB()
        # Retry loop: Gateway can be temporarily jammed on handshake.
        connected = False
        for attempt in range(5):
            try:
                cid = self.ibkr_client_id + attempt
                await ib.connectAsync(
                    self.ibkr_host, self.ibkr_port, clientId=cid,
                    timeout=20, account="",
                )
                log.info("executor connected to IBKR client_id=%d (attempt %d)",
                         cid, attempt + 1)
                connected = True
                break
            except Exception as e:
                log.warning("connect attempt %d failed: %s", attempt + 1, e)
                await asyncio.sleep(5)
        if not connected:
            log.error("executor could not connect to IBKR; aborting")
            return

        # Prefer env-configured account, avoid blocking accountValues() call.
        default_account = os.environ.get("IBKR_ACCOUNT", "DUM983136")
        log.info("paper account: %s", default_account)

        # Rate limiter: max 2 orders per second to avoid swamping Gateway.
        order_ts = []
        # UK/EU retail can't trade US ETFs without a PRIIPs KID (IBKR error 201).
        # LSE / XETRA / Euronext / BVME ETFs (UCITS) DO have KIDs and are fine.
        # Only block tickers where BOTH conditions hold: US exchange + known US ETF.
        us_etfs_blocked: set[str] = {
            "QQQ", "SPY", "IWM", "VTI", "VOO", "VXX", "XLF", "XLE", "XLK",
            "XLV", "XLY", "XLI", "XLU", "XLB", "XLP", "DIA", "ARKK", "ARKG",
            "GLD", "SLV", "TLT", "HYG", "LQD", "AGG", "BND", "VEA", "VWO",
            "EEM", "FXI", "EWZ", "IBIT",
            # 3× leveraged / inverse US ETPs:
            "TQQQ", "SQQQ", "SOXL", "SOXS", "TMF", "TMV", "SPXL", "SPXS",
            "UVXY", "SVIX", "USO", "SCO", "UNG", "UCO", "BOIL", "KOLD",
            "NUGT", "DUST", "JNUG", "JDST", "NRGU", "NRGD",
            # Single-stock 2×/inverse:
            "TSLL", "TSLS", "NVDL", "NVD", "AAPL", "AAPD",  # dup AAPL intentional guard
            "MSTU", "MSTX", "MSTZ", "MSTP", "MSDD", "MSTQ",
            "NFXL", "NFLY", "NFLU", "NFXS",
        }
        us_etfs_blocked.discard("AAPL")   # AAPL the stock is fine
        us_exchanges = {"SMART", "NASDAQ", "NYSE", "ARCA", "BATS", "AMEX", "IEX"}

        async def on_submit(msg):
            try:
                p = json.loads(msg.data)
            except Exception as e:
                log.warning("submit decode failed: %s", e)
                return
            ticker_ck = (p.get("ticker") or "").upper()
            exchange_ck = (p.get("exchange") or "").upper()
            if exchange_ck in us_exchanges and ticker_ck in us_etfs_blocked:
                await _reject(nc, p, "KID_blocked_US_ETF")
                return
            # Rate limit
            now = time.time()
            order_ts[:] = [t for t in order_ts if now - t < 1.0]
            if len(order_ts) >= 2:
                wait = 1.0 - (now - order_ts[0]) + 0.05
                await asyncio.sleep(wait)
            order_ts.append(time.time())
            try:
                # Build contract. Prefer con_id for exact match.
                c = Contract(
                    conId=int(p.get("con_id") or 0),
                    symbol=p["ticker"],
                    secType=p.get("sec_type", "STK"),
                    exchange=p.get("exchange", "SMART"),
                    currency=p.get("currency", "USD"),
                )
                if not c.conId:
                    quals = await ib.qualifyContractsAsync(c)
                    if not quals:
                        await _reject(nc, p, "contract qualification failed")
                        return
                    c = quals[0]

                side = p["side"].upper()
                qty = int(p["qty"])
                order_type = p.get("order_type", "MKT").upper()

                # bracketOrder path: if the signal payload carries
                # hard_stop_pct / hard_target_pct and a fill_price, build an
                # atomic bracket (entry + stop + target) so we don't rely on
                # downstream Chandelier for base-case exits.
                hard_stop_pct = p.get("hard_stop_pct")
                hard_target_pct = p.get("hard_target_pct")
                ref_px = p.get("fill_price") or p.get("last") or 0
                use_bracket = (
                    side == "BUY" and order_type == "MKT"
                    and hard_stop_pct is not None
                    and hard_target_pct is not None
                    and ref_px and ref_px > 0
                )
                if use_bracket:
                    try:
                        ref_px = float(ref_px)
                        stop_px = round(ref_px * (1 - abs(float(hard_stop_pct))), 2)
                        target_px = round(ref_px * (1 + abs(float(hard_target_pct))), 2)
                        bracket = ib.bracketOrder(
                            "BUY", qty,
                            limitPrice=round(ref_px * 1.005, 2),  # entry as aggressive limit
                            takeProfitPrice=target_px,
                            stopLossPrice=stop_px,
                        )
                        for o in bracket:
                            o.account = p.get("account") or default_account
                        trade = ib.placeOrder(c, bracket.parent)
                        ib.placeOrder(c, bracket.takeProfit)
                        ib.placeOrder(c, bracket.stopLoss)
                        log.info("bracket order %s qty=%d entry@%.2f stop@%.2f target@%.2f",
                                 p.get("ticker"), qty, ref_px, stop_px, target_px)
                    except Exception as e:
                        log.warning("bracket fallback to MKT: %s", e)
                        order = MarketOrder(side, qty)
                        order.account = p.get("account") or default_account
                        trade = ib.placeOrder(c, order)
                elif order_type == "LMT":
                    limit_price = float(p["limit_price"])
                    order = LimitOrder(side, qty, limit_price)
                    order.account = p.get("account") or default_account
                    trade = ib.placeOrder(c, order)
                else:
                    order = MarketOrder(side, qty)
                    order.account = p.get("account") or default_account
                    trade = ib.placeOrder(c, order)
                place_ts = time.time()

                async def watch_fills():
                    # Timeout protection: if order doesn't progress past
                    # PendingSubmit in 30s, cancel it. Prevents the stuck-order
                    # problem that happened during Gateway handshake wedges.
                    while not trade.isDone():
                        await asyncio.sleep(0.2)
                        age = time.time() - place_ts
                        st = trade.orderStatus.status
                        if age > 30 and st in ("PendingSubmit", "PreSubmitted"):
                            log.warning(
                                "order stuck %s %s qty=%s status=%s age=%.0fs → cancelling",
                                side, p['ticker'], qty, st, age,
                            )
                            try:
                                ib.cancelOrder(order)
                            except Exception as e:
                                log.error("cancel failed: %s", e)
                            await _reject(nc, p, f"timeout_{st}")
                            return
                    for fill in trade.fills:
                        payload = {
                            "signal_id": p.get("signal_id"),
                            "ticker": p["ticker"],
                            "side": side,
                            "filled_qty": fill.execution.shares,
                            "avg_price": fill.execution.avgPrice,
                            "commission": getattr(fill.commissionReport, "commission", 0.0),
                            "exchange": fill.execution.exchange,
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "order_status": trade.orderStatus.status,
                        }
                        await nc.publish("orders.filled", json.dumps(payload).encode("utf-8"))
                        log.info(
                            "filled: %s %s %s@%.2f (commission=%.2f)",
                            side, fill.execution.shares, p["ticker"],
                            fill.execution.avgPrice,
                            payload["commission"],
                        )
                    # Cancelled / inactive — emit reject.
                    if trade.orderStatus.status in ("Cancelled", "Inactive", "ApiCancelled"):
                        await _reject(nc, p, f"order {trade.orderStatus.status}")

                asyncio.create_task(watch_fills())
                log.info(
                    "submitted %s %s %s qty=%d account=%s",
                    order_type, side, p["ticker"], qty, order.account,
                )
            except Exception as e:
                log.exception("submit failed: %s", e)
                await _reject(nc, p, str(e))

        await nc.subscribe("orders.submit", cb=on_submit)
        log.info("executor listening on orders.submit")

        # --- Kill switch (was a separate service cid=141) -----------------
        # Listens for risk.kill NATS events + auto-kills on drawdown > 8%
        DRAWDOWN_KILL_PCT = float(os.environ.get("DRAWDOWN_KILL_PCT", "8.0"))

        async def flatten_all(reason: str):
            log.warning("KILL TRIGGERED reason=%s — reqGlobalCancel + flatten", reason)
            try:
                ib.reqGlobalCancel()
            except Exception as e:
                log.error("reqGlobalCancel failed: %s", e)
            try:
                port = ib.portfolio()
                for p in port:
                    if p.position == 0:
                        continue
                    side = "SELL" if p.position > 0 else "BUY"
                    qty = int(abs(p.position))
                    order = {
                        "signal_id": f"kill-{int(time.time())}-{p.contract.symbol}",
                        "ticker": p.contract.symbol,
                        "exchange": p.contract.primaryExchange or p.contract.exchange or "SMART",
                        "currency": p.contract.currency or "USD",
                        "con_id": p.contract.conId,
                        "side": side, "qty": qty,
                        "order_type": "MKT",
                        "strategy": "kill_switch",
                        "account": default_account,
                        "exit_reason": f"KILL: {reason}",
                    }
                    await nc.publish("orders.submit", json.dumps(order).encode("utf-8"))
            except Exception as e:
                log.error("flatten_all: %s", e)

        async def on_kill(msg):
            try:
                d = json.loads(msg.data)
            except Exception:
                d = {"reason": "unparseable"}
            await flatten_all(d.get("reason", "manual"))

        async def on_equity(msg):
            try:
                d = json.loads(msg.data)
                dd = float(d.get("drawdown_pct") or 0)
                if dd > DRAWDOWN_KILL_PCT:
                    log.warning("drawdown %.2f%% > %.2f%% — auto-kill", dd, DRAWDOWN_KILL_PCT)
                    await flatten_all(f"drawdown_{dd:.2f}pct")
            except Exception:
                pass

        await nc.subscribe("risk.kill", cb=on_kill)
        await nc.subscribe("portfolio.equity", cb=on_equity)
        log.info("kill switch armed (drawdown auto-kill > %.1f%%)", DRAWDOWN_KILL_PCT)

        # --- Stuck order watchdog (was separate cid=130) ------------------
        # Every 30s, find orders in PendingSubmit/Submitted for >60s and cancel
        async def stuck_watchdog():
            while True:
                await asyncio.sleep(30)
                try:
                    now_ts = time.time()
                    for trade in ib.openTrades():
                        try:
                            status = trade.orderStatus.status
                            if status not in ("PendingSubmit", "Submitted"):
                                continue
                            # Gauge age from last log entry
                            entries = trade.log or []
                            if not entries:
                                continue
                            last_ts = entries[-1].time.timestamp() if entries[-1].time else now_ts
                            age = now_ts - last_ts
                            if age > 60:
                                log.warning("cancelling stuck order: %s qty=%s age=%.1fs",
                                            trade.contract.symbol, trade.order.totalQuantity, age)
                                ib.cancelOrder(trade.order)
                                try:
                                    await nc.publish("orders.stuck_cancelled", json.dumps({
                                        "ts": now_ts,
                                        "ticker": trade.contract.symbol,
                                        "orderId": trade.order.orderId,
                                        "age_s": age,
                                    }).encode())
                                except Exception:
                                    pass
                        except Exception as e:
                            log.debug("stuck check failure: %s", e)
                except Exception as e:
                    log.debug("stuck watchdog cycle: %s", e)

        asyncio.create_task(stuck_watchdog())
        log.info("stuck order watchdog active (60s threshold)")

        # Keep alive.
        while True:
            await asyncio.sleep(60)
            log.info("executor heartbeat (account=%s)", default_account)


async def _reject(nc, submitted: dict, reason: str) -> None:
    payload = {
        "signal_id": submitted.get("signal_id"),
        "ticker": submitted.get("ticker"),
        "reason": reason,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    await nc.publish("orders.reject", json.dumps(payload).encode("utf-8"))
    log.warning("reject %s: %s", submitted.get("ticker"), reason)


if __name__ == "__main__":
    asyncio.run(PaperExecutor().run())
