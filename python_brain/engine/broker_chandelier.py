"""Broker-side Chandelier v2 monitor — watches every position on DUM983136.

Runs independently of the engine. Subscribes to `ticks.live.*` for price
updates and queries IBKR directly for the current position list. For every
position:
  - Maintain peak_price since entry (or first tick seen)
  - Apply Chandelier v2 (vol-regime, time-ramp, rungs, hard SL/TP)
  - When stop hits, publish SELL via orders.submit

This is the catch-all exit: the engine's internal exit_engine covers
engine-owned positions; this covers everything in the real broker
account, including synthetic injections and orphan fills.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from python_brain.engine.chandelier_v2 import (
    ChandelierV2State, evaluate_v2,
)
from python_brain.engine.chandelier_v4 import (
    V4InputFrame, evaluate_v4,
)
from python_brain.engine.chandelier_v3 import (
    evaluate_v3, IndicatorFrame,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@dataclass
class BrokerPosition:
    ticker: str
    con_id: int
    qty: int
    avg_cost: float
    entry_ts_ns: int
    chand_state: ChandelierV2State = field(default_factory=ChandelierV2State)
    last_tick_price: float = 0.0
    last_atr: float = 0.5
    sell_submitted: bool = False


class BrokerChandelier:
    def __init__(self) -> None:
        self.positions: Dict[str, BrokerPosition] = {}   # ticker -> BrokerPosition
        self.refresh_interval_s = 30
        self.nats_url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
        self.account = os.environ.get("IBKR_ACCOUNT", "DUM983136")
        # Cached indicator frames from indicator_framer service.
        # Key: ticker. Value: the latest indicators.live.{ticker} payload.
        # Populated by subscription in run(); consumed when evaluating v4.
        self._ind: Dict[str, dict] = {}

    async def _refresh_from_ibkr(self) -> None:
        """Poll IBKR for the current position list; add/remove tracked ones."""
        try:
            from ib_insync import IB  # type: ignore
        except ImportError:
            return
        ib = IB()
        try:
            await ib.connectAsync(
                "127.0.0.1", 4002, clientId=127, readonly=True, timeout=20,
            )
        except Exception as e:
            log.warning("IBKR refresh connect failed: %s", e)
            return
        try:
            port_items = ib.portfolio()
            live_tickers = set()
            for p in port_items:
                sym = p.contract.symbol
                live_tickers.add(sym)
                qty = int(p.position or 0)
                if qty <= 0:
                    continue
                if sym not in self.positions:
                    now_ns = time.time_ns()
                    bp = BrokerPosition(
                        ticker=sym,
                        con_id=int(p.contract.conId or 0),
                        qty=qty,
                        avg_cost=float(p.averageCost or 0),
                        entry_ts_ns=now_ns,
                    )
                    bp.chand_state.entry_price = bp.avg_cost
                    bp.chand_state.entry_ts_ns = now_ns
                    bp.chand_state.peak_price = max(bp.avg_cost, float(p.marketPrice or 0))
                    bp.chand_state.trough_price = min(bp.avg_cost, float(p.marketPrice or 0))
                    self.positions[sym] = bp
                    log.info("ADOPT %s qty=%d avg=$%.2f  (now tracked by broker-Chandelier)",
                             sym, qty, bp.avg_cost)
                else:
                    self.positions[sym].qty = qty
                    self.positions[sym].avg_cost = float(p.averageCost or 0)
            # Prune positions that no longer exist on broker
            for t in list(self.positions):
                if t not in live_tickers:
                    log.info("DROP %s (no longer on broker)", t)
                    self.positions.pop(t, None)
        finally:
            ib.disconnect()

    async def run(self) -> None:
        import nats  # type: ignore
        nc = await nats.connect(self.nats_url, name="aegis-v5-broker-chandelier")
        log.info("broker-Chandelier connected to NATS %s", self.nats_url)

        # First refresh
        await self._refresh_from_ibkr()
        log.info("initial tracked positions: %s",
                 list(self.positions.keys()))

        async def on_tick(msg):
            try:
                t = json.loads(msg.data)
            except Exception:
                return
            ticker = t.get("ticker")
            bp = self.positions.get(ticker)
            if bp is None or bp.sell_submitted:
                return
            price = float(t.get("last") or t.get("mid") or 0)
            if price <= 0:
                return
            bp.last_tick_price = price
            # Crude ATR proxy: half the bid-ask spread, floored at 20 bps
            def _f(x, default):
                try:
                    return float(x) if x is not None else float(default)
                except (TypeError, ValueError):
                    return float(default)
            ask = _f(t.get("ask"), price)
            bid = _f(t.get("bid"), price)
            spread = abs(ask - bid)
            bp.last_atr = max(spread * 2.0, price * 0.002)

            # Build minimal indicator frame from tick data for v3 checks.
            frame = IndicatorFrame(
                volume=_f(t.get("volume"), 0),
                avg_volume=_f(t.get("avg_volume"), 0),
                close_is_red=(price < _f(t.get("open"), price)),
                atr=bp.last_atr,
                atr_prev=getattr(bp, "_prev_atr", bp.last_atr),
                vwap=(_f(t.get("vwap"), 0) or None),
            )
            bp._prev_atr = bp.last_atr
            # Pull real indicators from cache populated by indicator_framer.
            ind = self._ind.get(ticker, {})
            # Per-position anchored VWAP: use session VWAP as a proxy for
            # positions opened mid-session, until we track entry ts to anchor
            # exactly. Better than None.
            avwap = ind.get("avwap_session")
            # ATR from indicator framer overrides our spread-based proxy if available.
            real_atr = ind.get("atr")
            if real_atr and real_atr > 0:
                bp.last_atr = real_atr

            v4f = V4InputFrame(
                entry_price=bp.avg_cost,
                current_price=price,
                current_ts_ns=time.time_ns(),
                entry_ts_ns=getattr(bp.chand_state, "entry_ts_ns", 0),
                atr=bp.last_atr,
                bars_since_entry=int(getattr(bp, "_bars", 0)),
                ker10=ind.get("ker10"),
                rsi=ind.get("rsi"),
                macd_hist=ind.get("macd_hist"),
                macd_hist_prev=ind.get("macd_hist_prev"),
                avwap_entry=avwap,
                bar_volume=ind.get("bar_volume"),
                avg_volume_20=ind.get("avg_volume_20"),
                bar_close_in_lower_third=ind.get("bar_close_in_lower_third"),
                bar_is_red=ind.get("bar_is_red", (price < _f(t.get("open"), price))),
                rv_now=ind.get("rv_now"),
                rv_20d_ema=ind.get("rv_20d_ema"),
                regime_probs=[0.70, 0.15, 0.05, 0.10],
                pctl80_giveback_pct=None,  # nightly-calibrated; not yet populated
                is_leveraged_etp=(bp.ticker.upper().startswith(("3L", "3S")) or "LEV" in bp.ticker.upper()),
                nights_held=0,
            )
            bp._bars = int(getattr(bp, "_bars", 0)) + 1
            decision = evaluate_v4(state=bp.chand_state, frame=v4f)
            if decision.flatten:
                bp.sell_submitted = True
                order = {
                    "signal_id": f"brokerchand-{ticker}-{int(time.time())}",
                    "ticker": bp.ticker,
                    "exchange": "SMART",
                    "currency": "USD",
                    "con_id": bp.con_id,
                    "side": "SELL",
                    "qty": bp.qty,
                    "order_type": "MKT",
                    "strategy": "broker_chandelier_v4",
                    "account": self.account,
                    "exit_reason": decision.reason,
                }
                await nc.publish("orders.submit",
                                 json.dumps(order).encode("utf-8"))
                # Emit positions.close so rotator releases the ticker for eviction
                try:
                    await nc.publish("positions.close", json.dumps({
                        "ts": time.time(),
                        "ticker": bp.ticker,
                        "con_id": bp.con_id,
                        "qty": bp.qty,
                        "strategy": "broker_chandelier_v4",
                        "exit_reason": decision.reason,
                    }).encode("utf-8"))
                except Exception:
                    pass
                pnl_per = price - bp.avg_cost
                log.info(
                    "SELL %s qty=%d @~$%.2f  reason=%s  stop=$%.2f  entry=$%.2f  "
                    "PnL/share=$%+.2f  lockin=%.0f%%",
                    bp.ticker, bp.qty, price, decision.reason,
                    decision.stop_price, bp.avg_cost, pnl_per,
                    float(getattr(decision, "rung_lockin", 0.0)) * 100,
                )

        async def on_indicator(msg):
            try:
                payload = json.loads(msg.data)
            except Exception:
                return
            t = payload.get("ticker")
            if t:
                self._ind[t] = payload

        await nc.subscribe("ticks.live.*", cb=on_tick)
        await nc.subscribe("indicators.live.*", cb=on_indicator)
        log.info("subscribed to ticks.live.*; watching %d positions",
                 len(self.positions))

        # Periodically re-sync from IBKR
        while True:
            await asyncio.sleep(self.refresh_interval_s)
            await self._refresh_from_ibkr()
            # Log state
            msgs = []
            for t, bp in self.positions.items():
                if bp.sell_submitted:
                    msgs.append(f"{t}[SOLD]")
                    continue
                st = bp.chand_state
                unreal_pct = 0.0
                if bp.avg_cost > 0 and bp.last_tick_price > 0:
                    unreal_pct = (bp.last_tick_price - bp.avg_cost) / bp.avg_cost * 100
                msgs.append(
                    f"{t}({unreal_pct:+.2f}% peak=${st.peak_price:.2f} "
                    f"stop=${st.stop_price:.2f})"
                )
            log.info("state: %s", " ".join(msgs))


if __name__ == "__main__":
    asyncio.run(BrokerChandelier().run())
