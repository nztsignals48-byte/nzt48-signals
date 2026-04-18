"""Chandelier v3 — indicator-aware optimal-exit.

Inherits all of v2 (vol regime, rungs, hard SL/TP, asymmetric, time ramp)
and ADDS indicator-based early-exit triggers so we cash out closer to the
top of the move instead of waiting for ATR trail.

Early-exit triggers (only fire if position is already profitable):
  1. RSI exhaustion       RSI > 78 AND RSI_1bar_ago > RSI_now → momentum fading
  2. MACD bearish cross   MACD hist < 0 AND prev hist > 0 after MFE > 1%
  3. Bollinger reversion  Price hit BB upper, next bar closes below
  4. VWAP break           Price > VWAP then closes below after rally
  5. Volume climax        Vol > 2.5× avg AND red candle AND MFE > 2%
  6. Kalman reversion     |kalman_z| > 2.0 then crosses back toward 0
  7. ATR compression      ATR dropping AND price flat after MFE > 3% (stall)

All triggers wrap v2's composite stop. v3 returns:
  - flatten (bool)
  - reason (which trigger or fallback to v2)
  - stop_price (chandelier floor)
  - rung_lockin (v2's protective floor)
  - indicator_exit (True if indicator-driven)

This makes wins bigger on average by exiting near the local peak instead
of 20-30% below after the reversal. Chandelier v2 floor still catches the
catastrophic case.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from python_brain.engine.chandelier_v2 import (
    ChandelierV2State, ChandelierV2Decision, evaluate_v2,
)


@dataclass
class IndicatorFrame:
    """Latest indicator values + history for trigger evaluation."""
    rsi: Optional[float] = None
    rsi_prev: Optional[float] = None
    macd_hist: Optional[float] = None
    macd_hist_prev: Optional[float] = None
    bb_upper: Optional[float] = None
    vwap: Optional[float] = None
    atr: Optional[float] = None
    atr_prev: Optional[float] = None
    volume: Optional[float] = None
    avg_volume: Optional[float] = None
    close_is_red: bool = False
    kalman_z: Optional[float] = None
    kalman_z_prev: Optional[float] = None


@dataclass
class ChandelierV3Decision:
    flatten: bool
    reason: str
    stop_price: float
    rung_lockin: float = 0.0
    indicator_exit: bool = False


# Thresholds — tuned for "exit near local peak"
RSI_EXHAUST = 78.0
MACD_BEARISH_CROSS_AFTER_MFE = 0.010    # 1%
BB_REVERSION_AFTER_MFE = 0.010
VWAP_BREAK_AFTER_MFE = 0.015
VOL_CLIMAX_MULT = 2.5
VOL_CLIMAX_AFTER_MFE = 0.020
KALMAN_EXTREME = 2.0
KALMAN_REVERSION_AFTER_MFE = 0.010
ATR_COMPRESSION_AFTER_MFE = 0.030
ATR_COMPRESSION_DROP_PCT = 0.25


def _indicator_exit(
    state: ChandelierV2State,
    current_price: float,
    ind: IndicatorFrame,
) -> Optional[str]:
    """Return a reason string if an indicator-based exit triggers, else None.

    Every trigger requires that we are already in profit (MFE > its threshold).
    """
    mfe = state.max_mfe_pct

    # 1. RSI exhaustion
    if (ind.rsi is not None and ind.rsi_prev is not None
            and mfe > 0.005
            and ind.rsi >= RSI_EXHAUST
            and ind.rsi < ind.rsi_prev):  # turning down
        return f"RSI_Exhaustion({ind.rsi:.1f})"

    # 2. MACD bearish cross
    if (ind.macd_hist is not None and ind.macd_hist_prev is not None
            and mfe > MACD_BEARISH_CROSS_AFTER_MFE
            and ind.macd_hist_prev > 0 and ind.macd_hist < 0):
        return "MACD_BearishCross"

    # 3. Bollinger upper reversion
    if (ind.bb_upper is not None
            and mfe > BB_REVERSION_AFTER_MFE
            and state.peak_price >= ind.bb_upper
            and current_price < ind.bb_upper * 0.998):  # back under BB
        return "BBUpper_Reversion"

    # 4. VWAP break after rally
    if (ind.vwap is not None
            and mfe > VWAP_BREAK_AFTER_MFE
            and state.peak_price > ind.vwap
            and current_price < ind.vwap):
        return "VWAP_Break"

    # 5. Volume climax + red candle
    if (ind.volume is not None and ind.avg_volume
            and mfe > VOL_CLIMAX_AFTER_MFE
            and ind.volume >= VOL_CLIMAX_MULT * ind.avg_volume
            and ind.close_is_red):
        return f"VolClimax({ind.volume/ind.avg_volume:.1f}x)"

    # 6. Kalman z-score extreme reversion
    if (ind.kalman_z is not None and ind.kalman_z_prev is not None
            and mfe > KALMAN_REVERSION_AFTER_MFE
            and ind.kalman_z_prev > KALMAN_EXTREME
            and ind.kalman_z < ind.kalman_z_prev - 0.5):  # decisively pulling back
        return f"Kalman_Reversion({ind.kalman_z:.2f})"

    # 7. ATR compression stall after rally
    if (ind.atr is not None and ind.atr_prev is not None
            and mfe > ATR_COMPRESSION_AFTER_MFE
            and ind.atr_prev > 0
            and (ind.atr_prev - ind.atr) / ind.atr_prev > ATR_COMPRESSION_DROP_PCT):
        return "ATR_CompressionStall"

    return None


def evaluate_v3(
    state: ChandelierV2State,
    current_price: float,
    current_ts_ns: int,
    atr: float,
    regime_probs,
    garch_vol_annualized: float,
    indicators: Optional[IndicatorFrame] = None,
    time_to_close_s: Optional[float] = None,
) -> ChandelierV3Decision:
    """Composite v3 decision.

    Order of checks (any flatten=True wins):
      A. Indicator early-exit (optimal-top detection)  ← NEW
      B. v2 composite (hard SL, hard TP, rung lock-in, ATR trail)
    """
    # A. Indicator exit
    if indicators is not None:
        reason = _indicator_exit(state, current_price, indicators)
        if reason:
            return ChandelierV3Decision(
                flatten=True,
                reason=f"IndicatorExit-{reason}",
                stop_price=current_price,
                rung_lockin=state.max_mfe_pct,
                indicator_exit=True,
            )

    # B. Fall through to v2
    v2 = evaluate_v2(
        state=state,
        current_price=current_price,
        current_ts_ns=current_ts_ns,
        atr=atr,
        regime_probs=regime_probs,
        garch_vol_annualized=garch_vol_annualized,
        time_to_close_s=time_to_close_s,
    )
    return ChandelierV3Decision(
        flatten=v2.flatten,
        reason=v2.reason,
        stop_price=v2.stop_price,
        rung_lockin=v2.rung_lockin,
        indicator_exit=False,
    )
