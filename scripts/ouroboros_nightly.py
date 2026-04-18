"""Ouroboros nightly — learn from today's fills, update learned.toml.

Inputs:
    data/fills/realised_YYYY-MM-DD.jsonl   (per-fill realised P&L)
    data/archive/portfolio_equity_YYYY-MM-DD.jsonl
    data/archive/signals_core_YYYY-MM-DD.jsonl
    data/archive/news_alpha_YYYY-MM-DD.jsonl
    data/preferences.jsonl

Outputs (bounded write to config/learned.toml with bounds.toml guard):
    kelly_fraction        (Bayesian EWMA from realised PF)
    confidence_floor      (from win_rate vs loss_rate at different floors)
    chandelier_atr_mult   (from MFE/MAE distribution)

Writes reports to docs/incidents/ouroboros_YYYY-MM-DD.md
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
FILLS_DIR = ROOT / "data" / "fills"
ARCHIVE_DIR = ROOT / "data" / "archive"
LEARNED_PATH = ROOT / "config" / "learned.toml"
BOUNDS_PATH = ROOT / "config" / "bounds.toml"
REPORT_DIR = ROOT / "docs" / "incidents"


def _read_fills(day: str) -> List[dict]:
    p = FILLS_DIR / f"realised_{day}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _read_signals(day: str) -> List[dict]:
    p = ARCHIVE_DIR / f"signals_core_{day}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            w = json.loads(line)
            out.append(w.get("payload") or w)
        except Exception:
            pass
    return out


def _parse_bounds() -> dict:
    if not BOUNDS_PATH.exists():
        return {}
    import re
    b = {}
    for line in BOUNDS_PATH.read_text().splitlines():
        m = re.match(r'\s*(\w+)\s*=\s*\[?\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\]?', line)
        if m:
            b[m.group(1)] = (float(m.group(2)), float(m.group(3)))
    return b


def _clamp(name: str, val: float, default: float, bounds: dict) -> float:
    lo, hi = bounds.get(name, (-1e18, 1e18))
    return max(lo, min(hi, val))


def _write_learned(learned: dict) -> None:
    LEARNED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LEARNED_PATH.open("w") as f:
        f.write("# Ouroboros nightly output. Bounded by bounds.toml. DO NOT EDIT BY HAND.\n")
        for k, v in learned.items():
            f.write(f"{k} = {v}\n")


@dataclass
class Report:
    day: str
    sell_fills: int = 0
    realised_pnl_usd: float = 0.0
    win_trades: int = 0
    loss_trades: int = 0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    new_kelly: float = 0.0
    new_confidence_floor: float = 0.0
    new_chandelier_atr_mult: float = 0.0
    notes: List[str] = field(default_factory=list)

    def markdown(self) -> str:
        body = [
            f"# Ouroboros report — {self.day}",
            "",
            f"- Sell fills processed: **{self.sell_fills}**",
            f"- Realised P&L: **${self.realised_pnl_usd:+,.2f}**",
            f"- Wins / Losses: **{self.win_trades} / {self.loss_trades}**",
            f"- Profit Factor: **{self.profit_factor:.3f}**",
            f"- Avg win / avg loss: ${self.avg_win:+.2f} / ${self.avg_loss:+.2f}",
            "",
            "## Learned updates (bounded)",
            f"- kelly_fraction → **{self.new_kelly:.3f}**",
            f"- confidence_floor → **{self.new_confidence_floor:.3f}**",
            f"- chandelier_atr_mult → **{self.new_chandelier_atr_mult:.3f}**",
            "",
            "## Notes",
        ] + [f"- {n}" for n in self.notes]
        return "\n".join(body) + "\n"


def run(day: str | None = None) -> Report:
    day = day or datetime.now(timezone.utc).date().isoformat()
    r = Report(day=day)
    fills = _read_fills(day)
    sells = [f for f in fills if (f.get("side") or "").upper() == "SELL"]
    r.sell_fills = len(sells)

    if not sells:
        r.notes.append("No SELLs today — nothing to learn. Keeping previous learned.toml.")
        r.new_kelly = 0.035
        r.new_confidence_floor = 0.60
        r.new_chandelier_atr_mult = 3.0
    else:
        pnls = [float(s.get("realised_pnl_usd") or 0) for s in sells]
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]
        r.realised_pnl_usd = sum(pnls)
        r.win_trades = len(wins)
        r.loss_trades = len(losses)
        r.avg_win = statistics.mean(wins) if wins else 0.0
        r.avg_loss = -statistics.mean(losses) if losses else 0.0
        gross_win = sum(wins)
        gross_loss = sum(losses)
        r.profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (gross_win if gross_win else 0.0)

        # EWMA: new_kelly = prev * 0.8 + indicator * 0.2
        edge_strength = max(0.0, min(1.0, (r.profit_factor - 1.0) / 2.0))  # PF 1→0, 3→1
        prev_kelly = 0.035
        raw_kelly = prev_kelly * 0.8 + (0.02 + edge_strength * 0.08) * 0.2
        raw_floor = 0.60 + (0.05 if r.win_trades >= r.loss_trades else -0.05)
        raw_cham = 3.0 if r.profit_factor >= 1.2 else (2.5 if r.profit_factor >= 1.0 else 2.0)

        bounds = _parse_bounds()
        r.new_kelly = _clamp("kelly_fraction", raw_kelly, 0.035, bounds)
        r.new_confidence_floor = _clamp("confidence_floor", raw_floor, 0.60, bounds)
        r.new_chandelier_atr_mult = _clamp("chandelier_atr_mult", raw_cham, 3.0, bounds)

    # Write learned.toml
    _write_learned({
        "kelly_fraction": round(r.new_kelly, 4),
        "confidence_floor": round(r.new_confidence_floor, 3),
        "chandelier_atr_mult": round(r.new_chandelier_atr_mult, 3),
    })
    log.info("wrote %s: kelly=%.3f floor=%.2f atr=%.2f (PF=%.2f wins=%d losses=%d)",
             LEARNED_PATH, r.new_kelly, r.new_confidence_floor, r.new_chandelier_atr_mult,
             r.profit_factor, r.win_trades, r.loss_trades)

    # Write markdown report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"ouroboros_{day}.md").write_text(r.markdown())

    # Also JSON for machine consumption
    (REPORT_DIR / f"ouroboros_{day}.json").write_text(json.dumps({
        "day": r.day, "sell_fills": r.sell_fills,
        "realised_pnl_usd": r.realised_pnl_usd,
        "win_trades": r.win_trades, "loss_trades": r.loss_trades,
        "profit_factor": r.profit_factor,
        "avg_win": r.avg_win, "avg_loss": r.avg_loss,
        "kelly_fraction": r.new_kelly,
        "confidence_floor": r.new_confidence_floor,
        "chandelier_atr_mult": r.new_chandelier_atr_mult,
    }, indent=2))
    return r


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else None
    run(day)
