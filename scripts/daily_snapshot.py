"""Daily paper snapshot — writes a per-day ledger of the IBKR DUM983136 state.

Used by Phase 12 60-day graduation tracker. Every day at market close:
  - Read IBKR fills/positions
  - Compute: realised + unrealised P&L, equity, win_rate, PF, max drawdown
  - Write docs/paper_graduation/YYYY-MM-DD.md and .json
  - Update docs/paper_graduation/tracker.json with running metrics

Graduation criteria (from Master Plan Phase 12):
  - 60 continuous days of paper trading
  - 500+ trades (fills on both sides)
  - Sharpe > 0.5
  - Profit Factor > 1.05
  - Max drawdown < 2× backtest
  - Deflated Sharpe > 0
"""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
OUT_DIR = ROOT / "docs" / "paper_graduation"
TRACKER = OUT_DIR / "tracker.json"


async def main() -> None:
    day = datetime.now(timezone.utc).date().isoformat()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from ib_insync import IB
    except ImportError:
        log.error("ib_insync not available")
        return

    ib = IB()
    try:
        # account=False avoids subscribing to account update stream (which
        # blocks startup when >3 other clients are requesting account data).
        await ib.connectAsync("127.0.0.1", 4002, clientId=133,
                              readonly=True, timeout=30, account="")
    except Exception as e:
        log.error("IBKR connect failed: %s; writing empty snapshot", e)
        return

    fills = ib.fills()
    positions = ib.positions()
    portfolio = ib.portfolio()
    acct = ib.accountValues()

    net_liq_usd = 0.0
    total_cash_usd = 0.0
    for v in acct:
        if v.tag == "NetLiquidation" and v.currency == "USD":
            try: net_liq_usd = float(v.value)
            except: pass
        if v.tag == "TotalCashValue" and v.currency == "USD":
            try: total_cash_usd = float(v.value)
            except: pass

    unrealised = sum((p.unrealizedPNL or 0) for p in portfolio)
    realised = sum((p.realizedPNL or 0) for p in portfolio)
    commissions = sum((f.commissionReport.commission or 0) for f in fills)

    buys = [f for f in fills if f.execution.side == "BOT"]
    sells = [f for f in fills if f.execution.side == "SLD"]

    # Crude per-fill round-trip PnL — replaced once compounding_tracker archive
    # feeds structured realised PnL per close.
    by_sym_entries: dict = {}
    pnls: list = []
    for f in fills:
        sym = f.contract.symbol
        by_sym_entries.setdefault(sym, []).append(f)
    for sym, fs in by_sym_entries.items():
        fs.sort(key=lambda x: x.execution.time)
        inventory = []  # (qty, price)
        for f in fs:
            side = f.execution.side
            qty = f.execution.shares
            px = f.execution.price
            if side == "BOT":
                inventory.append([qty, px])
            else:  # SLD
                remaining = qty
                while remaining > 0 and inventory:
                    lot_qty, lot_px = inventory[0]
                    take = min(remaining, lot_qty)
                    pnls.append((px - lot_px) * take)
                    lot_qty -= take
                    remaining -= take
                    if lot_qty <= 0:
                        inventory.pop(0)
                    else:
                        inventory[0][0] = lot_qty

    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    win_rate = len(wins) / max(1, len(pnls)) if pnls else 0.0
    pf = (sum(wins) / sum(losses)) if sum(losses) > 0 else (sum(wins) if sum(wins) else 0.0)

    snapshot = {
        "day": day,
        "account": "DUM983136",
        "net_liq_usd": round(net_liq_usd, 2),
        "cash_usd": round(total_cash_usd, 2),
        "unrealised_pnl_usd": round(unrealised, 2),
        "realised_pnl_usd_today": round(realised, 2),
        "commissions_usd": round(commissions, 2),
        "fills_total": len(fills),
        "buys": len(buys),
        "sells": len(sells),
        "round_trip_pnls": pnls,
        "win_rate": round(win_rate, 3),
        "profit_factor": round(pf, 3),
        "wins": len(wins),
        "losses": len(losses),
        "positions_count": len(positions),
        "ts_utc": datetime.now(timezone.utc).isoformat(),
    }

    (OUT_DIR / f"{day}.json").write_text(json.dumps(snapshot, indent=2))

    md = [
        f"# Paper graduation snapshot — {day}",
        "",
        f"Account: **DUM983136**",
        f"- Net liquidation: **${net_liq_usd:,.2f}**",
        f"- Cash: ${total_cash_usd:,.2f}",
        f"- Unrealised P&L: **${unrealised:+,.2f}**",
        f"- Realised P&L (today, IBKR): ${realised:+,.2f}",
        f"- Commissions today: ${commissions:,.2f}",
        f"- Fills: {len(fills)} ({len(buys)} BUY / {len(sells)} SELL)",
        f"- Round-trip PnLs computed: {len(pnls)}  (wins {len(wins)}, losses {len(losses)})",
        f"- Win rate: **{win_rate:.1%}**  Profit factor: **{pf:.2f}**",
        f"- Positions still open: {len(positions)}",
        "",
        "## Phase 12 graduation metrics (need, cumulative over 60 days)",
        "- Trades ≥ 500        → NOT YET",
        "- Sharpe > 0.5         → NOT YET (needs multi-day returns)",
        "- Profit Factor > 1.05 → " + ("✅" if pf > 1.05 else "NOT YET"),
        "- Max DD < 2× backtest → NOT YET",
        "- DSR > 0              → NOT YET",
        "",
    ]
    (OUT_DIR / f"{day}.md").write_text("\n".join(md))

    # Update tracker
    tracker = {"days": {}, "started": day}
    if TRACKER.exists():
        try:
            tracker = json.loads(TRACKER.read_text())
        except Exception:
            pass
    tracker.setdefault("days", {})[day] = {
        "net_liq_usd": snapshot["net_liq_usd"],
        "unrealised_pnl_usd": snapshot["unrealised_pnl_usd"],
        "fills_total": snapshot["fills_total"],
        "round_trip_count": len(pnls),
        "profit_factor": snapshot["profit_factor"],
    }
    tracker["days_elapsed"] = len(tracker["days"])
    tracker["graduation_target"] = 60
    tracker["target_date_utc"] = (
        datetime.fromisoformat(tracker["started"]).replace(tzinfo=timezone.utc)
    ).isoformat()
    TRACKER.write_text(json.dumps(tracker, indent=2, default=str))

    log.info("wrote snapshot %s (fills=%d PF=%.2f net_liq=$%.2f)",
             day, len(fills), pf, net_liq_usd)

    ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
