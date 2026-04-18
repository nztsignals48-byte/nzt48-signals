#!/usr/bin/env python3
"""portfolio_streamer — single consolidated IBKR readonly client (cid=118) that:

  1. Publishes account.summary / account.pnl / account.pnl.single.* /
     account.positions on NATS (replaces account_streamer)
  2. Updates Prometheus gauges v5_equity_gbp / v5_unrealised_pnl_usd /
     v5_position_* (replaces metrics_feeder IBKR heartbeat)
  3. Runs Chandelier v4 safety-net SELLs on every tracked position
     (replaces broker_chandelier)

Consolidating 3 services into 1 IBKR client drops our connection count from
11 → 8. The Gateway soft-throttle we've been hitting should stop.

Does NOT handle:
  * Order execution — that's paper_executor (cid=108)
  * Kill switch — moved into paper_executor
  * Stuck order cancellation — moved into paper_executor
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
from typing import Dict, Any

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from python_brain.core import metrics_http
from python_brain.core.metrics import REGISTRY
from python_brain.engine.chandelier_v2 import ChandelierV2State
from python_brain.engine.chandelier_v4 import V4InputFrame, evaluate_v4

log = logging.getLogger("portfolio-streamer")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
IBKR_HOST = "127.0.0.1"
IBKR_PORT = 4002
CLIENT_ID = int(os.environ.get("PORTFOLIO_CLIENT_ID", "118"))
ACCOUNT = os.environ.get("IBKR_ACCOUNT", "DUM983136")
METRICS_PORT = int(os.environ.get("AEGIS_V5_METRICS_PORT", "9103"))
USD_TO_GBP = 0.79


async def run():
    import nats  # type: ignore
    from ib_insync import IB, PnL, PnLSingle, util  # type: ignore

    util.patchAsyncio()

    nc = await nats.connect(NATS_URL, name="aegis-v5-portfolio-streamer")
    log.info("connected to NATS")

    # Seed Prometheus gauges
    REGISTRY.gauge("v5_equity_gbp", "Account equity in GBP")
    REGISTRY.gauge("v5_unrealised_pnl_usd", "Total unrealised P&L (USD)")
    REGISTRY.gauge("v5_realised_pnl_gbp", "Total realised P&L (GBP)")
    REGISTRY.gauge("v5_market_value_usd", "Deployed market value (USD)")
    REGISTRY.gauge("v5_position_unrealised_usd", "Per-ticker unrealised P&L",)
    REGISTRY.gauge("v5_position_qty", "Per-ticker position size")
    REGISTRY.set("v5_equity_gbp", 100_000.0)
    metrics_http.start(port=METRICS_PORT)
    log.info("metrics exporter on :%d/metrics", METRICS_PORT)

    ib = IB()
    try:
        await ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=CLIENT_ID,
                              readonly=True, timeout=30)
    except Exception as e:
        log.error("IBKR connect failed cid=%d: %s", CLIENT_ID, e)
        await nc.drain()
        return
    log.info("IBKR connected cid=%d account=%s", CLIENT_ID, ACCOUNT)

    await ib.reqAccountSummaryAsync()
    await asyncio.sleep(2)
    pnl_obj = ib.reqPnL(ACCOUNT)

    pnl_single: Dict[int, PnLSingle] = {}
    # Per-position chandelier state
    chand_state: Dict[int, ChandelierV2State] = {}
    # Track latest tick data from NATS for chandelier evaluation
    last_ticks: Dict[str, Dict[str, Any]] = {}
    # Indicator cache
    ind_cache: Dict[str, Dict[str, Any]] = {}
    # Flag positions where we already fired a SELL so we don't duplicate
    sell_fired: set[int] = set()

    def refresh_pnl_single():
        pos_by_conid = {p.contract.conId: p for p in ib.portfolio()}
        for con_id, pos in pos_by_conid.items():
            if con_id not in pnl_single and pos.position != 0:
                try:
                    pnl_single[con_id] = ib.reqPnLSingle(ACCOUNT, "", con_id)
                except Exception as e:
                    log.debug("reqPnLSingle %d: %s", con_id, e)
        for con_id in list(pnl_single.keys()):
            if con_id not in pos_by_conid or pos_by_conid[con_id].position == 0:
                try:
                    ib.cancelPnLSingle(ACCOUNT, "", con_id)
                except Exception:
                    pass
                pnl_single.pop(con_id, None)
                chand_state.pop(con_id, None)
                sell_fired.discard(con_id)

    # Subscribe to ticks + indicators for chandelier safety-net
    async def on_tick(msg):
        try:
            d = json.loads(msg.data)
            t = d.get("ticker")
            if t:
                last_ticks[t] = d
        except Exception:
            pass

    async def on_ind(msg):
        try:
            d = json.loads(msg.data)
            t = d.get("ticker")
            if t:
                ind_cache[t] = d
        except Exception:
            pass

    await nc.subscribe("ticks.live.*", cb=on_tick)
    await nc.subscribe("indicators.live.*", cb=on_ind)

    stop = asyncio.Event()
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
    except NotImplementedError:
        pass

    refresh_pnl_single()
    last_full_refresh = 0.0
    tick_counter = 0

    while not stop.is_set():
        now = time.time()
        tick_counter += 1

        # --- Portfolio snapshot --------------------------------------------
        try:
            port_items = ib.portfolio()
        except Exception as e:
            log.warning("portfolio() failed: %s", e)
            await asyncio.sleep(3)
            continue

        total_unreal = sum((p.unrealizedPNL or 0) for p in port_items)
        total_real = sum((p.realizedPNL or 0) for p in port_items)
        total_mv = sum((p.marketValue or 0) for p in port_items)

        # --- Account summary -----------------------------------------------
        try:
            summary_items = ib.accountSummary(ACCOUNT) or ib.accountSummary()
            tags = {item.tag: item.value for item in summary_items}
        except Exception:
            tags = {}

        net_liq_usd = 0.0
        try:
            net_liq_usd = float(tags.get("NetLiquidation", "0") or 0)
        except Exception:
            pass
        equity_gbp = net_liq_usd * USD_TO_GBP if net_liq_usd > 0 else 100_000.0

        # --- Prometheus gauges ---------------------------------------------
        REGISTRY.set("v5_equity_gbp", equity_gbp)
        REGISTRY.set("v5_unrealised_pnl_usd", total_unreal)
        REGISTRY.set("v5_market_value_usd", total_mv)
        REGISTRY.set("v5_realised_pnl_gbp",
                     (total_real * USD_TO_GBP) if total_real else 0.0)
        for p in port_items:
            REGISTRY.set("v5_position_unrealised_usd",
                         float(p.unrealizedPNL or 0),
                         labels=[("ticker", p.contract.symbol)])
            REGISTRY.set("v5_position_qty", float(p.position),
                         labels=[("ticker", p.contract.symbol)])

        # --- NATS publishes ------------------------------------------------
        try:
            await nc.publish("account.summary", json.dumps({
                "ts": now, "account": ACCOUNT, "tags": tags,
                "NetLiquidation": tags.get("NetLiquidation"),
                "TotalCashValue": tags.get("TotalCashValue"),
                "AvailableFunds": tags.get("AvailableFunds"),
                "BuyingPower": tags.get("BuyingPower"),
                "MaintMarginReq": tags.get("MaintMarginReq"),
                "GrossPositionValue": tags.get("GrossPositionValue"),
                "UnrealizedPnL": tags.get("UnrealizedPnL"),
                "RealizedPnL": tags.get("RealizedPnL"),
                "ExcessLiquidity": tags.get("ExcessLiquidity"),
            }).encode())
        except Exception:
            pass

        try:
            if pnl_obj and pnl_obj.dailyPnL is not None:
                await nc.publish("account.pnl", json.dumps({
                    "ts": now, "account": ACCOUNT,
                    "daily_pnl": float(pnl_obj.dailyPnL),
                    "unrealized_pnl": float(pnl_obj.unrealizedPnL or 0),
                    "realized_pnl": float(pnl_obj.realizedPnL or 0),
                }).encode())
        except Exception:
            pass

        # Per-position pnl
        try:
            for con_id, pnl_s in pnl_single.items():
                if pnl_s and pnl_s.position is not None:
                    await nc.publish(
                        f"account.pnl.single.{con_id}",
                        json.dumps({
                            "ts": now, "con_id": con_id,
                            "position": float(pnl_s.position or 0),
                            "unrealized_pnl": float(pnl_s.unrealizedPnL or 0),
                            "realized_pnl": float(pnl_s.realizedPnL or 0),
                            "value": float(pnl_s.value or 0),
                        }).encode(),
                    )
        except Exception:
            pass

        # Positions snapshot (every 30s)
        if now - last_full_refresh >= 30:
            last_full_refresh = now
            refresh_pnl_single()
            positions_payload = []
            for p in port_items:
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
            try:
                await nc.publish("account.positions", json.dumps({
                    "ts": now, "account": ACCOUNT,
                    "positions": positions_payload,
                }).encode())
            except Exception:
                pass

            # Emit recent fills
            try:
                for f in ib.fills()[-20:]:
                    await nc.publish("fills.executions", json.dumps({
                        "ts": now,
                        "ticker": f.contract.symbol,
                        "side": f.execution.side,
                        "shares": float(f.execution.shares),
                        "price": float(f.execution.price),
                        "exec_id": f.execution.execId,
                        "order_id": f.execution.orderId,
                        "perm_id": f.execution.permId,
                        "time": str(f.execution.time),
                        "commission": float(getattr(f.commissionReport, "commission", 0) or 0),
                    }).encode())
            except Exception:
                pass

        # --- Chandelier v4 safety net (every 2s) --------------------------
        if tick_counter % 2 == 0:
            for p in port_items:
                if p.position == 0:
                    continue
                con_id = p.contract.conId
                if con_id in sell_fired:
                    continue
                tk = p.contract.symbol
                tick = last_ticks.get(tk, {})
                price = tick.get("last") or tick.get("mid") or p.marketPrice or 0
                try:
                    price = float(price)
                except Exception:
                    price = 0.0
                if price <= 0:
                    continue
                ind = ind_cache.get(tk, {})
                st = chand_state.setdefault(
                    con_id,
                    ChandelierV2State(entry_price=float(p.averageCost),
                                      peak_price=float(p.averageCost),
                                      trough_price=float(p.averageCost)),
                )
                atr = ind.get("atr") or max(price * 0.002, 0.01)
                frame = V4InputFrame(
                    entry_price=float(p.averageCost),
                    current_price=price,
                    current_ts_ns=time.time_ns(),
                    entry_ts_ns=getattr(st, "entry_ts_ns", 0),
                    atr=atr,
                    bars_since_entry=int((now - (getattr(st, "entry_ts_ns", 0) / 1e9 or now)) // 60),
                    ker10=ind.get("ker10"),
                    rsi=ind.get("rsi"),
                    avwap_entry=ind.get("avwap_session"),
                    bar_volume=ind.get("bar_volume"),
                    avg_volume_20=ind.get("avg_volume_20"),
                    bar_is_red=ind.get("bar_is_red", False),
                    rv_now=ind.get("rv_now"),
                    rv_20d_ema=ind.get("rv_20d_ema"),
                    regime_probs=[0.70, 0.15, 0.05, 0.10],
                    is_leveraged_etp=(tk.upper().startswith(("3L", "3S", "2L", "2S"))),
                    nights_held=0,
                )
                decision = evaluate_v4(state=st, frame=frame)
                if decision.flatten:
                    sell_fired.add(con_id)
                    order = {
                        "signal_id": f"portchand-{tk}-{int(time.time())}",
                        "ticker": tk,
                        "exchange": p.contract.primaryExchange or "SMART",
                        "currency": p.contract.currency or "USD",
                        "con_id": con_id,
                        "side": "SELL",
                        "qty": int(abs(p.position)),
                        "order_type": "MKT",
                        "strategy": "portfolio_chandelier_v4",
                        "account": ACCOUNT,
                        "exit_reason": decision.reason,
                    }
                    try:
                        await nc.publish("orders.submit",
                                         json.dumps(order).encode("utf-8"))
                        await nc.publish("positions.close", json.dumps({
                            "ts": now, "ticker": tk, "con_id": con_id,
                            "qty": int(abs(p.position)),
                            "strategy": "portfolio_chandelier_v4",
                            "exit_reason": decision.reason,
                        }).encode("utf-8"))
                        log.warning("CHAND SELL %s qty=%d @~$%.2f reason=%s stop=$%.2f entry=$%.2f",
                                    tk, int(abs(p.position)), price, decision.reason,
                                    decision.stop_price, float(p.averageCost))
                    except Exception as e:
                        log.warning("chand publish: %s", e)

        await asyncio.sleep(5)

    log.info("portfolio streamer stopping")
    ib.disconnect()
    await nc.drain()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
