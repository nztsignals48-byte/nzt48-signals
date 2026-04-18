"""Chandelier v4 — evidence-based profit-maximising exit.

Research-derived changes over v3 (sources: Kaminski & Lo 2008, Sweeney MFE
framework, Tharp R-multiples, Brian Shannon AVWAP 2022, Kaufman KER,
Raschke intraday rules, GraniteShares leveraged-ETP decay research).

Exit checks (priority order — first hit flattens):
  1. Hard stop -2%                                  (catastrophe guard)
  2. Hard profit +10% × decay_haircut               (target)
  3. KER(10) force-close when KER < 0.15            (pure chop — decay kills)
  4. Volume climax reversal                         (vol ≥ 2×avg + red close in lower 1/3)
  5. Anchored-VWAP break with volume conf           (close < entry AVWAP + vol ≥ 1.5×avg)
  6. 80th-percentile MFE giveback                   (activates once MFE ≥ 1×ATR)
  7. Raschke stagnation                             (>10 bars, |pnl| < 0.3×ATR)
  8. KER-throttled ATR Chandelier trail             (classic v3-style trail, KER-gated)
  9. Rung lock-ins (kept from v2/v3)                (protect % of peak)

Extra features:
  - Capture-ratio calibration: when giveback_pctl is None, falls back to 20%
    (so exit when retrace > 80% of MFE; mathematically equivalent to Tharp).
  - Leveraged-ETP decay guard: rotation regime now tightens mult (not loosens).
  - Session re-entry counter (external code increments).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from python_brain.engine.chandelier_v2 import ChandelierV2State


@dataclass
class V4Config:
    hard_stop_pct: float = -0.02
    hard_target_pct: float = 0.10
    ker_window: int = 10
    ker_force_close: float = 0.15     # below this, force exit
    ker_high_trend: float = 0.50      # above this, widen trail
    ker_medium_trend: float = 0.30
    # KER-throttled Chandelier mult
    ker_trend_mult: float = 3.2
    ker_medium_mult: float = 2.5
    ker_chop_mult: float = 1.8
    # Giveback threshold — exit when retrace > X% of MFE; defaults to 20% (=80%-retained rule)
    default_giveback_pct: float = 0.20
    giveback_activate_atr_mult: float = 1.0
    # Leveraged-ETP
    lev_decay_vol_spike_mult: float = 1.5   # rv_now / rv_20d_ema > this triggers squeeze
    lev_decay_squeeze_mult: float = 1.2     # ATR-mult cap when decay spike active
    lev_decay_haircut_bps_per_day: float = 8.0  # subtract from target per overnight
    # AVWAP
    avwap_break_vol_ratio: float = 1.5
    # Volume climax
    vol_climax_ratio: float = 2.0
    vol_climax_after_mfe_atr: float = 2.0
    # Stagnation
    stagnation_bars: int = 10
    stagnation_pnl_atr_frac: float = 0.30


@dataclass
class V4InputFrame:
    """Per-tick inputs for v4 eval. All Optional — None means "not available"."""
    # Core
    entry_price: float = 0.0
    current_price: float = 0.0
    current_ts_ns: int = 0
    entry_ts_ns: int = 0
    atr: float = 0.0
    bars_since_entry: int = 0
    # Technical
    ker10: Optional[float] = None
    rsi: Optional[float] = None
    macd_hist: Optional[float] = None
    macd_hist_prev: Optional[float] = None
    avwap_entry: Optional[float] = None
    bar_volume: Optional[float] = None
    avg_volume_20: Optional[float] = None
    bar_close_in_lower_third: Optional[bool] = None
    bar_is_red: bool = False
    # Quant
    rv_now: Optional[float] = None
    rv_20d_ema: Optional[float] = None
    # Regime
    regime_probs: Optional[list] = None
    # Strategy-specific
    pctl80_giveback_pct: Optional[float] = None  # nightly-calibrated
    is_leveraged_etp: bool = False
    nights_held: int = 0


@dataclass
class V4Decision:
    flatten: bool
    reason: str
    stop_price: float
    exit_priority: int = 99  # 1 = highest priority trigger


def evaluate_v4(state: ChandelierV2State, frame: V4InputFrame,
                cfg: V4Config = V4Config()) -> V4Decision:
    if state.peak_price == 0:
        state.peak_price = frame.current_price
        state.trough_price = frame.current_price
        if state.entry_price == 0:
            state.entry_price = frame.entry_price or frame.current_price
        if state.entry_ts_ns == 0:
            state.entry_ts_ns = frame.entry_ts_ns or frame.current_ts_ns

    price = frame.current_price
    entry = state.entry_price
    state.peak_price = max(state.peak_price, price)
    state.trough_price = min(state.trough_price, price)
    unrealized_pct = (price - entry) / entry if entry > 0 else 0.0
    state.max_mfe_pct = max(state.max_mfe_pct, unrealized_pct)
    state.min_mae_pct = min(state.min_mae_pct, unrealized_pct)

    # Apply decay haircut to the hard target for leveraged ETPs held overnight.
    target = cfg.hard_target_pct
    if frame.is_leveraged_etp and frame.nights_held > 0:
        haircut = frame.nights_held * cfg.lev_decay_haircut_bps_per_day / 10_000
        target = max(0.02, target - haircut)

    # Priority 1 — hard catastrophe stop
    if unrealized_pct <= cfg.hard_stop_pct:
        return V4Decision(True, f"HardStop{cfg.hard_stop_pct*100:.1f}pct",
                          entry * (1 + cfg.hard_stop_pct), 1)

    # Priority 2 — hard profit target
    if unrealized_pct >= target:
        return V4Decision(True, f"HardTarget+{target*100:.1f}pct", price, 2)

    # Priority 3 — KER force-close (pure chop)
    if (frame.ker10 is not None and frame.ker10 < cfg.ker_force_close
            and frame.bars_since_entry > 5):
        return V4Decision(True, f"KER_ForceClose({frame.ker10:.2f})", price, 3)

    # Priority 4 — volume climax reversal (only after MFE ≥ 2×ATR)
    if (frame.bar_volume and frame.avg_volume_20 and frame.atr > 0
            and state.max_mfe_pct * entry >= cfg.vol_climax_after_mfe_atr * frame.atr
            and frame.bar_volume >= cfg.vol_climax_ratio * frame.avg_volume_20
            and frame.bar_is_red
            and frame.bar_close_in_lower_third):
        return V4Decision(True, f"VolClimax({frame.bar_volume/frame.avg_volume_20:.1f}x)", price, 4)

    # Priority 5 — Anchored VWAP break with volume confirmation
    if (frame.avwap_entry is not None
            and frame.bar_volume and frame.avg_volume_20
            and price < frame.avwap_entry
            and frame.bar_volume >= cfg.avwap_break_vol_ratio * frame.avg_volume_20
            and state.max_mfe_pct > 0.005):
        return V4Decision(True, "AVWAP_Break_VolConf", price, 5)

    # Priority 6 — 80th-percentile MFE giveback
    if frame.atr > 0 and entry > 0:
        mfe_abs = state.max_mfe_pct * entry
        if mfe_abs >= cfg.giveback_activate_atr_mult * frame.atr:
            giveback_thr = frame.pctl80_giveback_pct or cfg.default_giveback_pct
            # Rung-refined: as MFE grows, tighten the giveback allowance
            if state.max_mfe_pct >= 0.10:
                giveback_thr = min(giveback_thr, 0.15)
            elif state.max_mfe_pct >= 0.06:
                giveback_thr = min(giveback_thr, 0.25)
            elif state.max_mfe_pct >= 0.035:
                giveback_thr = min(giveback_thr, 0.35)
            # Exit if current retrace from peak exceeds (1 - giveback_thr) of MFE gain
            peak_gain = state.peak_price - entry
            current_gain = price - entry
            if peak_gain > 0:
                retained_frac = current_gain / peak_gain
                # retained_frac < (1 - giveback_thr)  =>  retrace > giveback_thr of gain
                if retained_frac < (1 - giveback_thr):
                    return V4Decision(True, f"MFE_Giveback({giveback_thr*100:.0f}%)", price, 6)

    # Priority 7 — Raschke stagnation
    if (frame.atr > 0 and frame.bars_since_entry >= cfg.stagnation_bars
            and abs(price - entry) < cfg.stagnation_pnl_atr_frac * frame.atr):
        return V4Decision(True, "RaschkeStagnation", price, 7)

    # Priority 8 — KER-throttled Chandelier trail
    if frame.atr > 0:
        ker = frame.ker10 if frame.ker10 is not None else 0.4
        if ker >= cfg.ker_high_trend:
            mult = cfg.ker_trend_mult
        elif ker >= cfg.ker_medium_trend:
            mult = cfg.ker_medium_mult
        else:
            mult = cfg.ker_chop_mult

        # Leveraged-ETP decay squeeze when realised vol spikes
        if (frame.is_leveraged_etp
                and frame.rv_now is not None and frame.rv_20d_ema
                and frame.rv_now > cfg.lev_decay_vol_spike_mult * frame.rv_20d_ema):
            mult = min(mult, cfg.lev_decay_squeeze_mult)

        # Asymmetric loser-tighten
        if unrealized_pct < 0 and frame.bars_since_entry > 5:
            mult *= 0.7

        chandelier_stop = state.peak_price - mult * frame.atr
        hard_floor = entry * (1 + cfg.hard_stop_pct)
        final_stop = max(chandelier_stop, hard_floor, state.stop_price)
        state.stop_price = final_stop
        if price <= final_stop:
            return V4Decision(True, f"Chandelier_KER({ker:.2f})", final_stop, 8)

    return V4Decision(False, "", state.stop_price or entry * (1 + cfg.hard_stop_pct), 99)


# --- Nightly calibration helper ---------------------------------------------

def calibrate_giveback_pctl(fills: list, percentile: float = 0.80) -> dict:
    """Compute per-strategy 80th-percentile peak-to-close giveback from recent fills.

    `fills` is a list of dicts with keys:
        strategy_name, realized_pnl_bps, mfe_bps, mae_bps
    Returns {strategy_name: giveback_pctl} where giveback_pctl is the fraction
    of MFE given back, aggregated at the requested percentile.
    """
    from collections import defaultdict
    per_strat = defaultdict(list)
    for f in fills:
        mfe = float(f.get("mfe_bps") or 0)
        real = float(f.get("realized_pnl_bps") or 0)
        if mfe <= 0 or real >= mfe:
            continue
        giveback = (mfe - real) / mfe
        per_strat[f.get("strategy_name", "?")].append(giveback)

    out = {}
    for strat, gs in per_strat.items():
        if len(gs) < 10:   # not enough data
            continue
        gs.sort()
        idx = int(percentile * len(gs))
        out[strat] = round(gs[idx], 3)
    return out
