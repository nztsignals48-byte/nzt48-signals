"""Chandelier v2 — optimised trailing stop.

Improvements over v1:
  1. ATR baseline adapts to VOL REGIME (GARCH vol) → tight in steady, wide in crisis.
  2. Asymmetric: losers tighten faster than winners loosen.
  3. Time-since-entry ramp: first 5 min use wider stop (entry noise), then compress.
  4. Session-EOD tightener: last 30 min of exchange session → cut ATR mult in half.
  5. Peak-locked: stop never drops (classic Chandelier invariant).
  6. Profit-target hybrid: hard TP at +3% AND chandelier still active.
  7. Emergency stop: -3% hard stop regardless of ATR (catastrophe guard).
  8. Rung system for winners: as MFE grows, lock in a % of the peak.

Drop-in replacement for the ChandelierStop branch of exit_engine.evaluate().
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# Base ATR multiplier (will be scaled by regime + time).
BASE_ATR_MULT = 3.0

# Profit-giveback rungs — as MFE grows, lock-in % of peak.
#   (mfe_pct, lockin_pct)
#   once price peaked +2%, lock in 50% of it (stop at entry + 1%)
#   once peaked +5%, lock in 75% (stop at entry + 3.75%)
RUNGS = [
    (0.010, 0.30),   # +1%   → lock 30% of peak
    (0.020, 0.50),   # +2%   → lock 50%
    (0.035, 0.65),   # +3.5% → lock 65%
    (0.060, 0.75),   # +6%   → lock 75%
    (0.100, 0.85),   # +10%  → lock 85%
    (0.150, 0.90),   # +15%  → lock 90%
]

HARD_STOP_PCT = -0.02      # -2% catastrophe stop (tighter, Option C)
HARD_TARGET_PCT = 0.10     # +10% hard profit target (let winners run longer)
ENTRY_NOISE_WINDOW_S = 300 # first 5 min use wider stop
EOD_WINDOW_S = 1800        # last 30 min cut ATR

# Regime → multiplier modifier
REGIME_ATR_MOD = {
    "steady":   0.85,   # tighter in calm markets
    "trending": 1.10,   # let winners run in trends
    "crisis":   1.40,   # wide stops, chaos
    "rotation": 0.95,
}


@dataclass
class ChandelierV2State:
    peak_price: float = 0.0
    trough_price: float = 1e18
    entry_price: float = 0.0
    entry_ts_ns: int = 0
    stop_price: float = 0.0
    max_mfe_pct: float = 0.0
    min_mae_pct: float = 0.0


@dataclass
class ChandelierV2Decision:
    flatten: bool
    reason: str
    stop_price: float
    rung_lockin: float = 0.0


def _regime_mod(regime_probs) -> float:
    """Weighted ATR modifier from regime probability vector."""
    if not regime_probs or len(regime_probs) < 4:
        return 1.0
    steady, trending, crisis, rotation = regime_probs[:4]
    return (steady * REGIME_ATR_MOD["steady"] +
            trending * REGIME_ATR_MOD["trending"] +
            crisis * REGIME_ATR_MOD["crisis"] +
            rotation * REGIME_ATR_MOD["rotation"])


def _time_mod(age_s: float, time_to_close_s: Optional[float]) -> float:
    """Time ramp: wider at entry, tighter near close."""
    if age_s < ENTRY_NOISE_WINDOW_S:
        # First 5 min: wider (1.5× → 1.0×)
        ramp = 1.5 - 0.5 * (age_s / ENTRY_NOISE_WINDOW_S)
        return ramp
    if time_to_close_s is not None and 0 < time_to_close_s < EOD_WINDOW_S:
        # Last 30 min: linear taper 1.0× → 0.5×
        return max(0.5, 0.5 + 0.5 * (time_to_close_s / EOD_WINDOW_S))
    return 1.0


def _rung_lockin(mfe_pct: float) -> float:
    """Return the highest achieved lock-in ratio (0..1)."""
    lockin = 0.0
    for (threshold, ratio) in RUNGS:
        if mfe_pct >= threshold:
            lockin = ratio
    return lockin


def evaluate_v2(
    state: ChandelierV2State,
    current_price: float,
    current_ts_ns: int,
    atr: float,
    regime_probs,
    garch_vol_annualized: float,
    time_to_close_s: Optional[float] = None,
) -> ChandelierV2Decision:
    """Compute and return exit decision. state is mutated in-place."""
    if state.peak_price == 0:  # first tick
        state.peak_price = current_price
        state.trough_price = current_price
        if state.entry_price == 0:
            state.entry_price = current_price
        if state.entry_ts_ns == 0:
            state.entry_ts_ns = current_ts_ns

    entry = state.entry_price
    state.peak_price = max(state.peak_price, current_price)
    state.trough_price = min(state.trough_price, current_price)

    unrealized_pct = (current_price - entry) / entry if entry > 0 else 0.0
    state.max_mfe_pct = max(state.max_mfe_pct, unrealized_pct)
    state.min_mae_pct = min(state.min_mae_pct, unrealized_pct)

    # --- 1. Hard catastrophe stop
    if unrealized_pct <= HARD_STOP_PCT:
        return ChandelierV2Decision(True, "HardStop-3pct",
                                    stop_price=entry * (1 + HARD_STOP_PCT))

    # --- 2. Hard profit target
    if unrealized_pct >= HARD_TARGET_PCT:
        return ChandelierV2Decision(True, "HardTarget+5pct",
                                    stop_price=current_price,
                                    rung_lockin=HARD_TARGET_PCT)

    # --- 3. Rung lock-in — once price has peaked enough, protect % of the move.
    lockin_ratio = _rung_lockin(state.max_mfe_pct)
    if lockin_ratio > 0:
        lockin_floor = entry * (1 + state.max_mfe_pct * lockin_ratio)
    else:
        lockin_floor = 0.0

    # --- 4. ATR trailing stop (Chandelier base)
    age_s = (current_ts_ns - state.entry_ts_ns) / 1e9 if state.entry_ts_ns else 0.0
    atr_mult = BASE_ATR_MULT
    atr_mult *= _regime_mod(regime_probs)
    atr_mult *= _time_mod(age_s, time_to_close_s)

    # Loser asymmetry: if currently underwater AND age > entry window, tighten faster.
    if unrealized_pct < 0 and age_s > ENTRY_NOISE_WINDOW_S:
        atr_mult *= 0.7

    # High-vol adjustment: in abnormally high GARCH vol, tighten (risk-off).
    if garch_vol_annualized > 0.5:
        atr_mult *= 0.85

    atr_effective = max(atr, current_price * 0.002)  # floor at 20 bps
    chandelier_stop = state.peak_price - atr_effective * atr_mult

    # --- 5. Composite stop — highest of (peak-ATR, lockin-floor, hard-stop).
    hard_stop = entry * (1 + HARD_STOP_PCT)
    final_stop = max(chandelier_stop, lockin_floor, hard_stop)

    # Enforce peak-locked invariant (stop never drops).
    final_stop = max(final_stop, state.stop_price)
    state.stop_price = final_stop

    if current_price <= final_stop:
        reason = "ChandelierV2-Lockin" if lockin_ratio > 0 else "ChandelierV2-ATR"
        return ChandelierV2Decision(True, reason,
                                    stop_price=final_stop,
                                    rung_lockin=lockin_ratio)

    return ChandelierV2Decision(False, "", stop_price=final_stop,
                                rung_lockin=lockin_ratio)
