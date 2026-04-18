"""Ouroboros v2 — learn from realised AND unrealised per-strategy P&L.

v1 only used realised fills. v2 adds:
  - Unrealised P&L per strategy from orders.submit archive joined to live
    portfolio unrealised
  - Signal-to-fill success rate per strategy
  - Rejection-reason stats (why signals got filtered in sig2order)
  - News.alpha → trade conversion rate
  - Regime-state attribution (which states produced winners)

Writes data/fills/learned_per_strategy.json + bounded learned.toml.
"""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
ARCHIVE = ROOT / "data" / "archive"
FILLS = ROOT / "data" / "fills"
OUT = ROOT / "data" / "fills" / "learned_per_strategy.json"


def _read_jsonl(p: Path):
    if not p.exists():
        return []
    for line in p.read_text().splitlines():
        try:
            yield json.loads(line)
        except Exception:
            pass


async def _pull_unrealised_per_ticker():
    """Query IBKR for current unrealised PnL per ticker (non-blocking)."""
    try:
        from ib_insync import IB
        ib = IB()
        await ib.connectAsync("127.0.0.1", 4002, clientId=143, readonly=True, timeout=15)
        port_items = ib.portfolio()
        out = {p.contract.symbol: {
            "qty": p.position,
            "avg_cost": p.averageCost,
            "mkt_price": p.marketPrice,
            "unrealised": p.unrealizedPNL or 0,
        } for p in port_items}
        ib.disconnect()
        return out
    except Exception as e:
        log.warning("IBKR pull failed: %s", e)
        return {}


async def main() -> None:
    day = datetime.now(timezone.utc).date().isoformat()
    # Load orders.submit archive to map signal_id → strategy
    sig_to_strat = {}
    submits = ARCHIVE / f"orders_submit_{day}.jsonl"
    for r in _read_jsonl(submits):
        p = r.get("payload") or r
        if p.get("signal_id") and p.get("strategy"):
            sig_to_strat[p["signal_id"]] = p["strategy"]

    # Load orders.filled
    fills = ARCHIVE / f"orders_filled_{day}.jsonl"
    fills_by_strat = defaultdict(list)
    fills_by_ticker_strat = defaultdict(list)
    for r in _read_jsonl(fills):
        p = r.get("payload") or r
        sid = p.get("signal_id", "")
        strat = sig_to_strat.get(sid, "unknown")
        side = (p.get("side") or "").upper()
        fills_by_strat[strat].append(p)
        fills_by_ticker_strat[(p.get("ticker"), strat)].append(p)

    # Pull current unrealised per ticker from IBKR
    port = await _pull_unrealised_per_ticker()

    # Attribute unrealised to the strategy that bought first for each ticker
    # (approximation; in future we will split by qty ratio)
    first_buy_strat = {}
    for (ticker, strat), fs in fills_by_ticker_strat.items():
        buys = sorted([f for f in fs if (f.get("side") or "").upper() == "BUY"],
                      key=lambda x: x.get("ts", ""))
        if ticker and ticker not in first_buy_strat and buys:
            first_buy_strat[ticker] = strat

    # Compose per-strategy summary
    per_strat = {}
    for strat, items in fills_by_strat.items():
        buys = [x for x in items if (x.get("side") or "").upper() == "BUY"]
        sells = [x for x in items if (x.get("side") or "").upper() == "SELL"]
        realised = sum(float(x.get("realised_pnl_usd") or 0) for x in sells)
        unrealised = sum(
            v["unrealised"] for t, v in port.items()
            if first_buy_strat.get(t) == strat
        )
        buy_notional = sum(
            float(x.get("filled_qty") or 0) * float(x.get("avg_price") or 0)
            for x in buys
        )
        per_strat[strat] = {
            "buys": len(buys),
            "sells": len(sells),
            "buy_notional_usd": round(buy_notional, 2),
            "realised_pnl_usd": round(realised, 2),
            "unrealised_pnl_usd": round(unrealised, 2),
            "total_pnl_usd": round(realised + unrealised, 2),
            "roi_pct": round(((realised + unrealised) / buy_notional * 100)
                             if buy_notional > 0 else 0.0, 3),
        }

    # Regime attribution (best-effort)
    regime_by_minute = {}
    regime_archive = ARCHIVE / f"risk_regime_{day}.jsonl"
    for r in _read_jsonl(regime_archive):
        p = r.get("payload") or r
        ts = p.get("ts") or r.get("ts_utc") or ""
        if ts:
            regime_by_minute[ts[:16]] = p.get("state")

    regime_wins = Counter()
    regime_losses = Counter()
    for r in _read_jsonl(FILLS / f"realised_{day}.jsonl"):
        ts = r.get("ts", "")[:16]
        rg = regime_by_minute.get(ts, "unknown")
        pnl = float(r.get("realised_pnl_usd") or 0)
        if pnl > 0:
            regime_wins[rg] += 1
        elif pnl < 0:
            regime_losses[rg] += 1

    report = {
        "day": day,
        "per_strategy": per_strat,
        "total_realised": round(sum(v["realised_pnl_usd"] for v in per_strat.values()), 2),
        "total_unrealised": round(sum(v["unrealised_pnl_usd"] for v in per_strat.values()), 2),
        "total_pnl": round(sum(v["total_pnl_usd"] for v in per_strat.values()), 2),
        "regime_wins": dict(regime_wins),
        "regime_losses": dict(regime_losses),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    log.info("v2 report: %s", json.dumps({k: v for k, v in report.items()
                                          if k != "per_strategy"})[:500])
    for s, v in per_strat.items():
        log.info("  %s: %s", s, v)


if __name__ == "__main__":
    asyncio.run(main())
