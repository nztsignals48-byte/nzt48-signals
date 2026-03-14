"""
execution/planner.py
=====================
ExecutionPlanner: builds an ExecutionPlan per signal card.
Produces concrete, cost-aware entry instructions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from execution.cost_model import (
    get_spread_bps, round_trip_cost_bps, net_rr_after_costs, spread_gate_result,
)
from execution.order_rules import (
    CancelConditions, DoNotTradeConditions, get_cancel_conditions,
)

logger = logging.getLogger("nzt48.execution.planner")


@dataclass
class ExecutionPlan:
    signal_id:          str
    ticker:             str
    direction:          str

    # Order mechanics
    order_type:         str    = "LIMIT"       # LIMIT | MARKETABLE_LIMIT | STOP_LIMIT
    limit_price:        float  = 0.0           # recommended entry price
    max_slippage_bps:   float  = 10.0
    spread_proxy_bps:   float  = 0.0
    spread_gate:        str    = "PASS"        # PASS | WATCH | VETO
    net_rr_after_costs: float  = 0.0
    round_trip_cost_bps: float = 0.0

    # Time controls
    time_in_force:      str    = "DAY"
    max_fill_minutes:   int    = 20
    track:              str    = "INTRADAY_SWING"

    # Cancel conditions (serializable)
    cancel_conditions:  list   = field(default_factory=list)

    # Do-not-trade gate
    do_not_trade:       bool   = False
    dnt_reasons:        list   = field(default_factory=list)

    # PM summary line
    pm_summary:         str    = ""

    generated_at:       str    = ""

    def to_dict(self) -> dict:
        return asdict(self)


class ExecutionPlanner:
    """Builds ExecutionPlan objects for SignalCards."""

    def build(
        self,
        signal_id:    str,
        ticker:       str,
        direction:    str,
        entry:        float,
        stop:         float,
        target1:      float,
        raw_rr:       float,
        track:        str    = "INTRADAY_SWING",
        rvol:         float  = 1.0,
        regime:       str    = "NEUTRAL",
        halt:         bool   = False,
        live_quote:   dict   = None,
    ) -> ExecutionPlan:

        spread_bps   = get_spread_bps(ticker, live_quote)
        rt_cost_bps  = round_trip_cost_bps(ticker, live_quote)
        net_rr       = net_rr_after_costs(raw_rr, ticker, entry, stop, target1, live_quote)
        spread_gate  = spread_gate_result(ticker, live_quote)

        # Order type: use LIMIT for low RVOL, MARKETABLE_LIMIT for active sessions
        order_type = "MARKETABLE_LIMIT" if (rvol and rvol >= 1.5) else "LIMIT"

        # Recommended limit price: entry with half-spread offset
        spread_offset = entry * spread_bps / 10_000 / 2
        if direction == "LONG":
            limit_price = round(entry + spread_offset, 4)
        else:
            limit_price = round(entry - spread_offset, 4)

        # Cancel conditions
        cc = get_cancel_conditions(track, regime)
        cancel_list = [
            f"Not filled within {cc.time_expiry_minutes} min",
            f"Spread widens above {cc.spread_spike_bps:.0f}bps",
            f"Price moves >{cc.price_invalidation_pct:.1f}% against entry before fill",
            "Session ends",
        ]
        if cc.regime_flip:
            cancel_list.append("Regime flips against direction")

        # Do-not-trade check
        dnt = DoNotTradeConditions()
        blocked, dnt_reasons = dnt.check(spread_bps, rvol or 0.0, regime, halt)

        # PM summary
        pm_summary = (
            f"{direction} {ticker} via {order_type} @ {limit_price:.4f} | "
            f"Net R:R {net_rr:.2f} | Spread {spread_bps:.0f}bps ({spread_gate}) | "
            f"Costs {rt_cost_bps:.0f}bps RT"
        )
        if blocked:
            pm_summary += f" | DNT: {'; '.join(dnt_reasons)}"

        plan = ExecutionPlan(
            signal_id=signal_id,
            ticker=ticker,
            direction=direction,
            order_type=order_type,
            limit_price=limit_price,
            max_slippage_bps=rt_cost_bps / 2,
            spread_proxy_bps=spread_bps,
            spread_gate=spread_gate,
            net_rr_after_costs=net_rr,
            round_trip_cost_bps=rt_cost_bps,
            time_in_force="DAY",
            max_fill_minutes=cc.time_expiry_minutes,
            track=track,
            cancel_conditions=cancel_list,
            do_not_trade=blocked,
            dnt_reasons=dnt_reasons,
            pm_summary=pm_summary,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

        logger.info(
            "[EXEC_PLAN] %s %s spread=%dbps spread_gate=%s net_rr=%.2f dnt=%s",
            direction, ticker, spread_bps, spread_gate, net_rr, blocked,
        )
        return plan
