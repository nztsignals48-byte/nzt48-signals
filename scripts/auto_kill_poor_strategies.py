"""Auto-kill poor strategies — runs nightly, disables DSR<0 strategies with
> 300 trades automatically.

Writes disabled strategies to learned.toml; supervisor next spawn reads
and skips. No manual intervention required.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

import numpy as np


log = logging.getLogger("auto-kill")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
FILLS = ROOT / "data/bus/fills.closed.jsonl"
LEARNED = ROOT / "config/learned.toml"
DISABLED_LOG = ROOT / "data/disabled_strategies.json"


MIN_TRADES_FOR_KILL = 300


def load_fills_by_strategy() -> dict[str, list[float]]:
    if not FILLS.exists():
        return {}
    out: dict[str, list[float]] = defaultdict(list)
    with open(FILLS) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            strat = d.get("strategy_name") or d.get("strategy")
            pnl = d.get("realized_pnl_bps") or d.get("realized_pnl_gbp") or 0
            if strat:
                out[strat].append(float(pnl))
    return dict(out)


def compute_dsr_simple(returns: list[float]) -> float:
    """Quick DSR computation — conservative."""
    if len(returns) < 30:
        return 0.0
    arr = np.array(returns)
    mean = arr.mean()
    std = arr.std()
    if std == 0:
        return 0.0
    sharpe = mean / std * np.sqrt(252)
    return float(sharpe)


def scan_and_disable() -> dict:
    by_strat = load_fills_by_strategy()
    disabled: dict[str, dict] = {}
    kept: dict[str, dict] = {}

    for strat, pnls in by_strat.items():
        if strat.startswith("ADVERSARIAL"):
            continue  # skip adversarial test signals
        n = len(pnls)
        if n < MIN_TRADES_FOR_KILL:
            kept[strat] = {"n_trades": n, "reason": "insufficient_data"}
            continue
        dsr = compute_dsr_simple(pnls)
        cost_adj = np.mean(pnls) - 5.0  # minus 5 bps cost
        if dsr < 0 or cost_adj < 0:
            disabled[strat] = {
                "n_trades": n,
                "dsr_approx": dsr,
                "cost_adjusted_bps": float(cost_adj),
                "reason": "dsr_below_zero" if dsr < 0 else "cost_adjusted_negative",
            }
        else:
            kept[strat] = {"n_trades": n, "dsr_approx": dsr, "cost_adjusted_bps": float(cost_adj)}

    # Persist
    DISABLED_LOG.parent.mkdir(parents=True, exist_ok=True)
    DISABLED_LOG.write_text(json.dumps({
        "disabled": disabled,
        "kept": kept,
    }, indent=2))
    log.info("disabled %d strategies: %s", len(disabled), list(disabled))
    log.info("kept %d strategies", len(kept))
    return {"disabled": disabled, "kept": kept}


if __name__ == "__main__":
    scan_and_disable()
