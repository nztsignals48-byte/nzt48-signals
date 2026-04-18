"""Three critical alerts. Evaluated against the metrics registry."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List

from python_brain.core.metrics import REGISTRY


@dataclass
class Alert:
    name: str
    fired: bool
    message: str


@dataclass
class AlertEvaluator:
    last_tick_ts: float = field(default_factory=time.time)
    ibkr_disconnected_since: float | None = None

    def evaluate(self, now: float | None = None) -> List[Alert]:
        now = now or time.time()
        out: List[Alert] = []

        engine_alive = (now - self.last_tick_ts) < 60.0
        out.append(Alert("engine_crash", not engine_alive,
                         "Engine not receiving ticks for >60s" if not engine_alive else ""))

        eq = REGISTRY._metrics["equity_total_gbp"].values.get((), 0.0)
        hwm = REGISTRY._metrics["equity_hwm_gbp"].values.get((), 0.0)
        dd_over_5 = hwm > 0 and (hwm - eq) / hwm > 0.05
        out.append(Alert("drawdown_over_5pct", dd_over_5,
                         f"Drawdown {(hwm - eq) / hwm * 100:.1f}%" if dd_over_5 else ""))

        ibkr_up = REGISTRY._metrics["ibkr_session_up"].values.get((), 1.0) == 1.0
        if not ibkr_up:
            if self.ibkr_disconnected_since is None:
                self.ibkr_disconnected_since = now
            disconnected_60 = (now - self.ibkr_disconnected_since) > 60
        else:
            self.ibkr_disconnected_since = None
            disconnected_60 = False
        out.append(Alert("ibkr_disconnected", disconnected_60,
                         "IBKR disconnected >60s" if disconnected_60 else ""))
        return out
