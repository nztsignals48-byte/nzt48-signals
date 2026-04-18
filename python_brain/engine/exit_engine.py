"""Exit engine. 5 methods: Chandelier, FixedDay, EventWindow, NextOpen, ProfitTarget."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from python_brain.engine.portfolio_state import Position


@dataclass
class ExitDecision:
    flatten: bool
    reason: str


CHANDELIER_RUNGS = [
    (0.000, 1.00),
    (0.005, 1.00),
    (0.015, 0.95),
    (0.025, 0.85),
    (0.040, 0.75),
    (0.060, 0.60),
]


def _rung_mult(unrealized_pct: float) -> float:
    mult = CHANDELIER_RUNGS[0][1]
    for level, m in CHANDELIER_RUNGS:
        if unrealized_pct >= level:
            mult = m
    return mult


def evaluate(method: str, pos: Position, current_price: float, current_ts_ns: int, atr: float, session_close_ts_ns: int) -> ExitDecision:
    pos.peak_price = max(pos.peak_price, current_price)
    unrealized_pct = (current_price - pos.entry_price) / pos.entry_price if pos.entry_price else 0.0
    # Track MAE/MFE in bps.
    pos.mae_bps = min(pos.mae_bps, unrealized_pct * 1e4)
    pos.mfe_bps = max(pos.mfe_bps, unrealized_pct * 1e4)

    if method == "ChandelierStop":
        mult = _rung_mult(unrealized_pct) * 3.0  # base ATR mult = 3.0
        stop = pos.peak_price - atr * mult
        if current_price <= stop:
            return ExitDecision(True, "ChandelierStop")
    elif method == "FixedDayExpiry":
        days = (current_ts_ns - pos.entry_ts_ns) / 86_400_000_000_000
        if days >= 2.0:
            return ExitDecision(True, "FixedDayExpiry")
    elif method == "EventWindowExit":
        days = (current_ts_ns - pos.entry_ts_ns) / 86_400_000_000_000
        if days >= 5.0:
            return ExitDecision(True, "EventWindowExit")
    elif method == "NextOpen":
        if current_ts_ns >= session_close_ts_ns:
            return ExitDecision(True, "NextOpen")
    elif method == "ProfitTarget":
        if unrealized_pct >= 0.02:  # 2% PT
            return ExitDecision(True, "ProfitTargetHit")
    return ExitDecision(False, "")
