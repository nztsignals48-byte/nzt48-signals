"""Auto-demote (14-day negative Sharpe) + auto-resurrect (14-day positive post-demotion)."""
from __future__ import annotations

from typing import Dict, List

from python_brain.ouroboros.alpha_decay import rolling_sharpe


def review(strategy_pnls: Dict[str, List[float]], state: Dict[str, str]) -> Dict[str, str]:
    out = dict(state)
    for strat, pnls in strategy_pnls.items():
        s = rolling_sharpe(pnls[-14:])
        if out.get(strat, "live") == "live" and s < 0 and len(pnls) >= 14:
            out[strat] = "demoted"
        elif out.get(strat) == "demoted" and s > 0 and len(pnls) >= 14:
            out[strat] = "live"
    return out
