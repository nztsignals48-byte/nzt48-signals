"""
execution/order_rules.py
=========================
Cancel/replace rules and time-in-force logic for paper execution.
"""
from __future__ import annotations
from dataclasses import dataclass, field


# Track default time limits
TRACK_TIME_LIMITS: dict[str, dict] = {
    "SCALP": {
        "max_fill_minutes":    5,
        "max_trade_minutes":   15,
        "cancel_if_not_filled": 5,
    },
    "INTRADAY_SWING": {
        "max_fill_minutes":    15,
        "max_trade_minutes":   180,
        "cancel_if_not_filled": 20,
    },
}

DEFAULT_TRACK = "INTRADAY_SWING"


@dataclass
class CancelConditions:
    time_expiry_minutes:     int   = 20
    spread_spike_bps:        float = 35.0    # cancel if spread widens beyond this
    regime_flip:             bool  = True    # cancel if regime flips against direction
    price_invalidation_pct:  float = 0.5    # cancel if price moves >X% against before fill
    session_end:             bool  = True    # always cancel at session end
    custom:                  list  = field(default_factory=list)


@dataclass
class DoNotTradeConditions:
    spread_exceeds_bps:      float = 32.0
    rvol_below:              float = 0.40
    regime_in:               list  = field(default_factory=lambda: ["SHOCK"])
    halt_active:             bool  = True

    def check(self, spread_bps: float, rvol: float, regime: str, halt: bool) -> tuple[bool, list[str]]:
        """Returns (blocked, reasons)."""
        reasons = []
        if spread_bps > self.spread_exceeds_bps:
            reasons.append(f"Spread {spread_bps:.1f}bps > {self.spread_exceeds_bps}bps limit")
        if rvol < self.rvol_below:
            reasons.append(f"RVOL {rvol:.2f}x < {self.rvol_below}x minimum")
        if regime in self.regime_in:
            reasons.append(f"Regime={regime} is in do-not-trade list")
        if halt:
            reasons.append("Halt flag active")
        return bool(reasons), reasons


def get_cancel_conditions(track: str = None, regime: str = "NEUTRAL") -> CancelConditions:
    """Build cancel conditions for a given track."""
    tl = TRACK_TIME_LIMITS.get(track or DEFAULT_TRACK, TRACK_TIME_LIMITS[DEFAULT_TRACK])
    return CancelConditions(
        time_expiry_minutes=tl["cancel_if_not_filled"],
        spread_spike_bps=35.0,
        regime_flip=True,
        price_invalidation_pct=0.5,
        session_end=True,
    )
