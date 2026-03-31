"""Book 130: Vol Surface & Implied Volatility Signals.

Computes IV regime classification from VIX data (spot, 1-month, 3-month)
and provides a position sizing modifier based on the term structure shape.

Key concepts:
  - Term structure: contango (VIX < VIX_1M) = normal/complacent,
    backwardation (VIX > VIX_1M) = stress/fear
  - IV percentile: where current VIX sits relative to its 1-year range
  - Skew signal: put/call demand asymmetry derived from term structure slope

The sizing modifier reduces exposure in stress regimes (backwardation + high IV)
and allows modest size-up in calm, low-IV environments.

Usage (bridge.py sizing adjustment):
    from python_brain.analytics.iv_surface import compute_iv_regime, iv_sizing_modifier
    regime = compute_iv_regime(vix=18.5, vix_1m=17.2, vix_3m=16.8)
    size_mult = iv_sizing_modifier(regime)
    sig["kelly_fraction"] *= size_mult
    sig["shares"] = max(1, int(sig["shares"] * size_mult))

Usage (nightly reporting):
    from python_brain.analytics.iv_surface import compute_iv_regime, iv_regime_summary
    regime = compute_iv_regime(vix, vix_1m, vix_3m, vix_history=past_year_closes)
    summary = iv_regime_summary(regime)
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

log = logging.getLogger("iv_surface")

# ---------------------------------------------------------------------------
# IV percentile tracking (rolling 1-year window, stdlib only)
# ---------------------------------------------------------------------------

# Module-level state: rolling VIX history for percentile computation.
# Fed externally by bridge.py or nightly pipeline.
_vix_history: List[float] = []
_VIX_HISTORY_MAX = 252  # ~1 year of trading days


def feed_vix_close(vix_close: float) -> None:
    """Append a daily VIX close for percentile tracking.

    Call once per trading day (e.g. from nightly pipeline).
    """
    if vix_close > 0:
        _vix_history.append(vix_close)
        if len(_vix_history) > _VIX_HISTORY_MAX:
            _vix_history.pop(0)


def load_vix_history(closes: List[float]) -> None:
    """Bulk-load VIX history (e.g. on startup from a file).

    Args:
        closes: List of daily VIX closing values, oldest first.
    """
    global _vix_history
    valid = [v for v in closes if v > 0]
    _vix_history = valid[-_VIX_HISTORY_MAX:]


def _compute_percentile(value: float, history: List[float]) -> float:
    """Compute the percentile rank of value within history (0-100).

    Uses the "percentage of observations below" method.
    """
    if not history or len(history) < 5:
        return 50.0  # Insufficient data — assume median
    below = sum(1 for h in history if h < value)
    return round((below / len(history)) * 100, 1)


# ---------------------------------------------------------------------------
# Core IV regime computation
# ---------------------------------------------------------------------------

def compute_iv_regime(
    vix: float,
    vix_1m: float,
    vix_3m: float,
    vix_history: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """Classify the current IV regime from VIX term structure.

    Args:
        vix: Current VIX spot (e.g. CBOE VIX index).
        vix_1m: 1-month VIX futures or VIX 1-month moving average.
        vix_3m: 3-month VIX futures or VIX 3-month moving average.
        vix_history: Optional explicit history for percentile calc.
                     If None, uses module-level _vix_history.

    Returns:
        Dict with keys:
            term_structure: "contango" | "backwardation"
            iv_percentile: 0-100 (where current VIX sits vs 1-year)
            skew_signal: float (positive = put demand/bearish, negative = call demand/bullish)
            vix: float (echo back for logging)
            regime_label: str (human-readable summary)
    """
    # Clamp to sane ranges
    vix = max(0.01, vix)
    vix_1m = max(0.01, vix_1m)
    vix_3m = max(0.01, vix_3m)

    # Term structure: spot vs 1-month
    # Contango = spot < 1m (normal — market pricing near-term calm)
    # Backwardation = spot > 1m (stress — market pricing immediate fear)
    if vix > vix_1m:
        term_structure = "backwardation"
    else:
        term_structure = "contango"

    # IV percentile
    hist = vix_history if vix_history is not None else _vix_history
    iv_percentile = _compute_percentile(vix, hist)

    # Skew signal: slope of the term structure curve
    # Positive = front-loaded vol (put demand, bearish)
    # Negative = back-loaded vol (call demand/complacency, bullish)
    # Normalised by 3m level to make comparable across VIX regimes
    if vix_3m > 0.01:
        skew_signal = round((vix - vix_3m) / vix_3m, 4)
    else:
        skew_signal = 0.0

    # Human-readable regime label
    if term_structure == "backwardation" and iv_percentile >= 80:
        regime_label = "STRESS_HIGH"
    elif term_structure == "backwardation" and iv_percentile >= 50:
        regime_label = "STRESS_MODERATE"
    elif term_structure == "contango" and iv_percentile <= 20:
        regime_label = "CALM_LOW"
    elif term_structure == "contango" and iv_percentile <= 50:
        regime_label = "CALM_NORMAL"
    else:
        regime_label = "MIXED"

    result = {
        "term_structure": term_structure,
        "iv_percentile": iv_percentile,
        "skew_signal": skew_signal,
        "vix": round(vix, 2),
        "regime_label": regime_label,
    }

    log.debug(
        "IV_REGIME: vix=%.2f term=%s pctl=%.0f skew=%.4f label=%s",
        vix, term_structure, iv_percentile, skew_signal, regime_label,
    )

    return result


# ---------------------------------------------------------------------------
# Sizing modifier
# ---------------------------------------------------------------------------

def iv_sizing_modifier(iv_regime: Dict[str, Any]) -> float:
    """Compute position size multiplier based on IV regime.

    Returns a float in [0.5, 1.5] that should be multiplied into
    kelly_fraction and shares.

    Rules (Book 130, Chapter 7):
        Backwardation + IV percentile >= 80  -> 0.5x  (max defensive)
        Backwardation + IV percentile >= 60  -> 0.7x  (moderate defensive)
        Backwardation + IV percentile <  60  -> 0.85x (mild caution)
        Contango + IV percentile <= 20       -> 1.2x  (can size up in calm)
        Contango + IV percentile <= 40       -> 1.1x  (slightly favorable)
        Otherwise                            -> 1.0x  (neutral)

    The modifier never exceeds 1.2x to avoid over-leveraging in
    seemingly calm markets (vol compression can precede explosions).

    Args:
        iv_regime: Dict from compute_iv_regime().

    Returns:
        Sizing multiplier (0.5 - 1.2).
    """
    term = iv_regime.get("term_structure", "contango")
    pctl = iv_regime.get("iv_percentile", 50.0)

    if term == "backwardation":
        if pctl >= 80:
            modifier = 0.5
        elif pctl >= 60:
            modifier = 0.7
        else:
            modifier = 0.85
    elif term == "contango":
        if pctl <= 20:
            modifier = 1.2
        elif pctl <= 40:
            modifier = 1.1
        else:
            modifier = 1.0
    else:
        modifier = 1.0

    # Hard clamp to [0.5, 1.5] (spec allows up to 1.5, but we cap at 1.2
    # for safety — contango + low IV is NOT an invitation to lever up)
    modifier = max(0.5, min(1.5, modifier))

    log.debug("IV_SIZING: term=%s pctl=%.0f -> %.2fx", term, pctl, modifier)

    return modifier


# ---------------------------------------------------------------------------
# Nightly summary helper
# ---------------------------------------------------------------------------

def iv_regime_summary(iv_regime: Dict[str, Any]) -> str:
    """One-line human-readable summary for nightly reports.

    Args:
        iv_regime: Dict from compute_iv_regime().

    Returns:
        Summary string, e.g. "STRESS_HIGH: VIX 32.5, backwardation, 95th pctl, skew +0.18"
    """
    label = iv_regime.get("regime_label", "UNKNOWN")
    vix = iv_regime.get("vix", 0)
    term = iv_regime.get("term_structure", "?")
    pctl = iv_regime.get("iv_percentile", 0)
    skew = iv_regime.get("skew_signal", 0)
    return f"{label}: VIX {vix:.1f}, {term}, {pctl:.0f}th pctl, skew {skew:+.4f}"
