"""compounding_tracker — realised-PnL accountant.

Subscribes: orders.filled
Tracks every BUY/SELL round-trip per signal_id, computes realised PnL
after commission, recomputes equity after each fill, publishes to NATS
so sig2order + the risk arbiter can grow position sizes as equity grows.

Publishes:
    portfolio.equity     {equity_gbp, hwm_gbp, drawdown_pct, realised_pnl, trades_win, trades_loss}
    portfolio.fill       {ticker, side, qty, price, commission, realised_pnl, ts}

Also writes a realised-pnl log for Ouroboros:
    /Users/rr/aegis-v5/data/fills/realised_YYYY-MM-DD.jsonl
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
from typing import Dict, List, Optional

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

FILLS_DIR = Path("/Users/rr/aegis-v5/data/fills")


@dataclass
class OpenLot:
    ticker: str
    qty: int
    entry_price: float
    entry_commission: float
    strategy: str
    ts_open: float


@dataclass
class CompoundingTracker:
    nats_url: str = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    start_equity_gbp: float = 100_000.0
    usd_to_gbp: float = 0.79

    equity_gbp: float = 0.0
    hwm_gbp: float = 0.0
    realised_pnl_gbp: float = 0.0
    trades_win: int = 0
    trades_loss: int = 0
    open_lots: Dict[str, List[OpenLot]] = field(default_factory=dict)  # ticker -> lots

    def __post_init__(self):
        self.equity_gbp = self.start_equity_gbp
        self.hwm_gbp = self.start_equity_gbp

    async def run(self) -> None:
        import nats  # type: ignore
        nc = await nats.connect(self.nats_url, name="aegis-v5-compounding")
        log.info("compounding tracker connected to NATS %s", self.nats_url)
        log.info("start equity=£%.2f (USD→GBP=%.2f)", self.start_equity_gbp, self.usd_to_gbp)

        FILLS_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).date().isoformat()
        fills_path = FILLS_DIR / f"realised_{day}.jsonl"

        async def on_fill(msg):
            try:
                f = json.loads(msg.data)
            except Exception:
                return
            t = f.get("ticker")
            side = (f.get("side") or "").upper()
            qty = int(f.get("filled_qty") or 0)
            px = float(f.get("avg_price") or 0)
            comm = float(f.get("commission") or 0)
            if not t or qty <= 0 or px <= 0:
                return

            realised_usd = 0.0
            if side == "BUY":
                lot = OpenLot(
                    ticker=t, qty=qty, entry_price=px,
                    entry_commission=comm,
                    strategy=f.get("strategy") or "?",
                    ts_open=time.time(),
                )
                self.open_lots.setdefault(t, []).append(lot)
                # No PnL on BUY; subtract commission from equity.
                self.equity_gbp -= comm * self.usd_to_gbp

            elif side == "SELL":
                remaining = qty
                lots = self.open_lots.get(t, [])
                while remaining > 0 and lots:
                    l = lots[0]
                    take = min(remaining, l.qty)
                    gross = (px - l.entry_price) * take
                    # Prorate entry commission, include exit commission.
                    exit_comm_portion = comm * (take / qty) if qty else 0.0
                    entry_comm_portion = l.entry_commission * (take / l.qty) if l.qty else 0.0
                    pnl_usd = gross - exit_comm_portion - entry_comm_portion
                    realised_usd += pnl_usd
                    l.qty -= take
                    remaining -= take
                    if l.qty <= 0:
                        lots.pop(0)
                if not lots:
                    self.open_lots.pop(t, None)

                pnl_gbp = realised_usd * self.usd_to_gbp
                self.realised_pnl_gbp += pnl_gbp
                self.equity_gbp += pnl_gbp
                if pnl_gbp > 0:
                    self.trades_win += 1
                else:
                    self.trades_loss += 1

            self.hwm_gbp = max(self.hwm_gbp, self.equity_gbp)
            drawdown = 0.0 if self.hwm_gbp == 0 else (self.hwm_gbp - self.equity_gbp) / self.hwm_gbp

            fill_record = {
                "ticker": t, "side": side, "qty": qty, "price": px,
                "commission_usd": comm,
                "realised_pnl_usd": realised_usd,
                "realised_pnl_gbp": realised_usd * self.usd_to_gbp if side == "SELL" else 0.0,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            # Persist.
            with fills_path.open("a") as fh:
                fh.write(json.dumps(fill_record) + "\n")

            await nc.publish("portfolio.fill", json.dumps(fill_record).encode("utf-8"))

            summary = {
                "equity_gbp": round(self.equity_gbp, 2),
                "hwm_gbp": round(self.hwm_gbp, 2),
                "drawdown_pct": round(drawdown * 100, 3),
                "realised_pnl_gbp": round(self.realised_pnl_gbp, 2),
                "trades_win": self.trades_win,
                "trades_loss": self.trades_loss,
                "win_rate": round(
                    self.trades_win / max(1, self.trades_win + self.trades_loss), 3
                ),
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            await nc.publish("portfolio.equity", json.dumps(summary).encode("utf-8"))
            log.info(
                "%s %d %s @%.2f  | realised£%.2f | equity£%.2f | hwm£%.2f | dd=%.2f%% | W/L=%d/%d",
                side, qty, t, px, self.realised_pnl_gbp, self.equity_gbp, self.hwm_gbp,
                drawdown * 100, self.trades_win, self.trades_loss,
            )

        await nc.subscribe("orders.filled", cb=on_fill)
        log.info("listening on orders.filled")
        while True:
            await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(CompoundingTracker().run())
