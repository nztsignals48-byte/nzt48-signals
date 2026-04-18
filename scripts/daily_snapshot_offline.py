"""Daily snapshot — offline version that reads only V5's own archived data.

Doesn't need an IBKR connection. Aggregates:
  data/fills/realised_*.jsonl          (compounding_tracker output)
  data/archive/portfolio_equity_*.jsonl
  data/archive/orders_filled_*.jsonl
  data/archive/news_alpha_*.jsonl
  docs/incidents/ouroboros_*.json

Writes the same docs/paper_graduation/ YYYY-MM-DD.{md,json} files.
Safe to run every hour; idempotent.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
OUT_DIR = ROOT / "docs" / "paper_graduation"
ARCHIVE_DIR = ROOT / "data" / "archive"
FILLS_DIR = ROOT / "data" / "fills"
TRACKER = OUT_DIR / "tracker.json"


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def main() -> None:
    day = datetime.now(timezone.utc).date().isoformat()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    orders = _read_jsonl(ARCHIVE_DIR / f"orders_filled_{day}.jsonl")
    equity = _read_jsonl(ARCHIVE_DIR / f"portfolio_equity_{day}.jsonl")
    news_alpha = _read_jsonl(ARCHIVE_DIR / f"news_alpha_{day}.jsonl")
    realised = _read_jsonl(FILLS_DIR / f"realised_{day}.jsonl")

    buys = [o for o in orders if (o.get("payload", {}).get("side") or "").upper() == "BUY"]
    sells = [o for o in orders if (o.get("payload", {}).get("side") or "").upper() == "SELL"]

    realised_sells = [r for r in realised if (r.get("side") or "").upper() == "SELL"]
    pnls = [float(r.get("realised_pnl_usd") or 0) for r in realised_sells]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    win_rate = len(wins) / max(1, len(pnls)) if pnls else 0.0
    pf = (sum(wins) / sum(losses)) if sum(losses) > 0 else (sum(wins) if sum(wins) else 0.0)

    last_eq = None
    if equity:
        last_eq = equity[-1].get("payload") or equity[-1]

    snapshot = {
        "day": day,
        "account": "DUM983136",
        "source": "offline-archive",
        "equity_gbp": last_eq.get("equity_gbp") if last_eq else None,
        "hwm_gbp": last_eq.get("hwm_gbp") if last_eq else None,
        "drawdown_pct": last_eq.get("drawdown_pct") if last_eq else None,
        "realised_pnl_gbp": last_eq.get("realised_pnl_gbp") if last_eq else None,
        "trades_win": len(wins),
        "trades_loss": len(losses),
        "win_rate": round(win_rate, 3),
        "profit_factor": round(pf, 3),
        "fills_total": len(orders),
        "buys": len(buys),
        "sells": len(sells),
        "round_trip_count": len(pnls),
        "news_alpha_today": len(news_alpha),
        "ts_utc": datetime.now(timezone.utc).isoformat(),
    }

    (OUT_DIR / f"{day}.json").write_text(json.dumps(snapshot, indent=2))

    md = [
        f"# Paper snapshot (offline) — {day}",
        "",
        f"Account: **DUM983136**",
        f"- Equity: **£{snapshot['equity_gbp']}**  HWM £{snapshot['hwm_gbp']}  DD {snapshot['drawdown_pct']}%",
        f"- Realised P&L: £{snapshot['realised_pnl_gbp']}",
        f"- Fills: {snapshot['fills_total']}  ({snapshot['buys']} BUY / {snapshot['sells']} SELL)",
        f"- Round-trips: {snapshot['round_trip_count']}",
        f"- Win rate: **{win_rate:.1%}**  PF **{pf:.2f}**",
        f"- News alpha today: {snapshot['news_alpha_today']}",
        "",
        "## Phase 12 graduation checkpoints (60-day target)",
        f"- Trades ≥ 500 round-trips: {snapshot['round_trip_count']} / 500",
        f"- Profit factor > 1.05:       {'PASS' if pf > 1.05 else 'not yet'}",
        "",
    ]
    (OUT_DIR / f"{day}.md").write_text("\n".join(md))

    # Tracker
    tracker = {"days": {}, "started": day}
    if TRACKER.exists():
        try:
            tracker = json.loads(TRACKER.read_text())
        except Exception:
            pass
    tracker.setdefault("days", {})[day] = snapshot
    tracker["days_elapsed"] = len(tracker["days"])
    tracker["graduation_target_days"] = 60
    TRACKER.write_text(json.dumps(tracker, indent=2, default=str))

    log.info("snapshot %s written (fills=%d round-trips=%d PF=%.2f)",
             day, snapshot["fills_total"], snapshot["round_trip_count"], pf)


if __name__ == "__main__":
    main()
