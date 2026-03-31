"""Book 124: Volatility Regime Clustering — 5-State Market Classifier.

Extends the Hurst-only regime detection (3 states) with a richer 5-regime
model using multiple market features. No external dependencies (stdlib only).

5 Canonical Regimes:
  R1: LOW_VOL_GRIND   — Tight spreads, low vol, Hurst > 0.55. MR dominates.
  R2: NORMAL           — Balanced. Both trend and reversion work. 35% of days.
  R3: ELEVATED         — Fear entering, correlations rising, vol-of-vol high.
  R4: CRISIS           — Panic. Spreads >2%, correlations spike. Survival mode.
  R5: RECOVERY         — V-bounce. High Hurst, declining vol. Highest Sharpe.

Features used (all available in bridge.py):
  - Hurst exponent (persistence)
  - Realized volatility (RVOL)
  - VPIN toxicity (informed flow)
  - ADX (trend strength)
  - Spread percentage
  - Volume slope (vol trend)

Integration: Called per-tick in _apply_adjustments to provide regime-aware
    confidence scaling and strategy routing hints.

Usage:
    from python_brain.risk.vol_regime_cluster import get_vol_regime, VolRegimeResult
    result = get_vol_regime(hurst=0.38, rvol=2.5, vpin=0.65, adx=12, spread_pct=0.3)
    # result.regime = "ELEVATED", result.confidence = 0.72
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("vol_regime_cluster")


@dataclass
class VolRegimeResult:
    """Result from volatility regime classification."""
    regime: str = "NORMAL"        # LOW_VOL_GRIND, NORMAL, ELEVATED, CRISIS, RECOVERY
    confidence: float = 0.5       # How clearly we're in this regime (0-1)
    strategy_hint: str = ""       # Which strategy families work best
    sizing_mult: float = 1.0      # Position sizing multiplier (0.15-1.0)
    regime_score: float = 0.0     # Raw composite 0-1 (higher = more stressed)


# Regime centroid definitions (from Book 124 Table 1.3)
# Each is a dict of feature → expected value for that regime.
_CENTROIDS = {
    "LOW_VOL_GRIND": {
        "hurst": 0.58, "rvol": 0.6, "vpin": 0.40,
        "adx": 25.0, "spread_pct": 0.05, "vol_slope": -0.1,
    },
    "NORMAL": {
        "hurst": 0.50, "rvol": 1.0, "vpin": 0.50,
        "adx": 18.0, "spread_pct": 0.10, "vol_slope": 0.0,
    },
    "ELEVATED": {
        "hurst": 0.42, "rvol": 2.0, "vpin": 0.65,
        "adx": 12.0, "spread_pct": 0.25, "vol_slope": 0.3,
    },
    "CRISIS": {
        "hurst": 0.30, "rvol": 4.0, "vpin": 0.80,
        "adx": 8.0, "spread_pct": 0.80, "vol_slope": 0.6,
    },
    "RECOVERY": {
        "hurst": 0.57, "rvol": 2.5, "vpin": 0.55,
        "adx": 28.0, "spread_pct": 0.15, "vol_slope": -0.3,
    },
}

# Feature normalization ranges (for distance calculation)
_FEATURE_RANGES = {
    "hurst": (0.0, 1.0),
    "rvol": (0.0, 6.0),
    "vpin": (0.0, 1.0),
    "adx": (0.0, 50.0),
    "spread_pct": (0.0, 2.0),
    "vol_slope": (-1.0, 1.0),
}

# Strategy routing hints
_STRATEGY_HINTS = {
    "LOW_VOL_GRIND": "IBS_MeanReversion,VolCompression,NightRider",
    "NORMAL": "Momentum,Orchestrator,VolExpansion",
    "ELEVATED": "VolCompression(short),CrisisAlpha,LeadLag",
    "CRISIS": "InverseETPs,CrisisAlpha,FlattenAll",
    "RECOVERY": "Momentum(aggressive),LeadLag,TrendSurfer",
}

# Position sizing multipliers (Book 124 Table 6.2)
_SIZING_MULTS = {
    "LOW_VOL_GRIND": 0.90,  # Slightly reduced (complacency risk)
    "NORMAL": 1.00,
    "ELEVATED": 0.60,
    "CRISIS": 0.15,          # Survival mode
    "RECOVERY": 0.80,
}


def _normalize(value: float, feature: str) -> float:
    """Normalize feature value to [0, 1] range."""
    lo, hi = _FEATURE_RANGES.get(feature, (0.0, 1.0))
    if hi == lo:
        return 0.5
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def _distance_to_centroid(obs: dict, centroid: dict) -> float:
    """Weighted Euclidean distance between observation and centroid.

    Uses normalized features. Weights: Hurst and RVOL matter most.
    """
    weights = {
        "hurst": 2.0, "rvol": 2.0, "vpin": 1.5,
        "adx": 1.0, "spread_pct": 1.0, "vol_slope": 0.8,
    }
    dist_sq = 0.0
    total_weight = 0.0
    for feat, centroid_val in centroid.items():
        obs_val = obs.get(feat, centroid_val)
        w = weights.get(feat, 1.0)
        norm_obs = _normalize(obs_val, feat)
        norm_cent = _normalize(centroid_val, feat)
        dist_sq += w * (norm_obs - norm_cent) ** 2
        total_weight += w

    if total_weight > 0:
        dist_sq /= total_weight
    return math.sqrt(dist_sq)


def classify_vol_regime(
    hurst: float = 0.5,
    rvol: float = 1.0,
    vpin: float = 0.5,
    adx: float = 15.0,
    spread_pct: float = 0.1,
    vol_slope: float = 0.0,
) -> VolRegimeResult:
    """Classify current market state into one of 5 volatility regimes.

    Uses nearest-centroid classification with softmax confidence.
    """
    obs = {
        "hurst": hurst, "rvol": rvol, "vpin": vpin,
        "adx": adx, "spread_pct": spread_pct, "vol_slope": vol_slope,
    }

    # Compute distance to each centroid
    distances = {}
    for regime, centroid in _CENTROIDS.items():
        distances[regime] = _distance_to_centroid(obs, centroid)

    # Find nearest regime
    nearest = min(distances, key=distances.get)
    nearest_dist = distances[nearest]

    # Softmax confidence: confidence = exp(-d_nearest) / sum(exp(-d_i))
    # Temperature parameter controls sharpness
    temperature = 0.3
    exp_neg_dists = {}
    for regime, d in distances.items():
        exp_neg_dists[regime] = math.exp(-d / temperature)
    total = sum(exp_neg_dists.values())
    confidence = exp_neg_dists[nearest] / total if total > 0 else 0.5

    # Compute raw stress score (0 = calm, 1 = panic)
    # Weighted average of normalized stressed-direction features
    stress_score = (
        (1.0 - _normalize(hurst, "hurst")) * 0.25 +
        _normalize(rvol, "rvol") * 0.25 +
        _normalize(vpin, "vpin") * 0.20 +
        _normalize(spread_pct, "spread_pct") * 0.15 +
        (1.0 - _normalize(adx, "adx")) * 0.10 +
        max(0.0, _normalize(vol_slope, "vol_slope")) * 0.05
    )

    return VolRegimeResult(
        regime=nearest,
        confidence=round(confidence, 3),
        strategy_hint=_STRATEGY_HINTS.get(nearest, ""),
        sizing_mult=_SIZING_MULTS.get(nearest, 1.0),
        regime_score=round(stress_score, 3),
    )


# ---------------------------------------------------------------------------
# Hysteresis wrapper (prevents regime flapping)
# ---------------------------------------------------------------------------

class VolRegimeTracker:
    """Tracks regime with hysteresis to prevent rapid flapping.

    Requires `persistence_ticks` consecutive votes for a new regime
    before switching. This adds ~5 ticks of latency but eliminates
    false transitions that would whipsaw position sizing.
    """

    def __init__(self, persistence_ticks: int = 5):
        self.persistence = persistence_ticks
        self.current_regime = "NORMAL"
        self._candidate = "NORMAL"
        self._candidate_count = 0
        self._last_result: Optional[VolRegimeResult] = None

    def update(self, **kwargs) -> VolRegimeResult:
        """Classify and apply hysteresis."""
        result = classify_vol_regime(**kwargs)

        if result.regime != self.current_regime:
            if result.regime == self._candidate:
                self._candidate_count += 1
            else:
                self._candidate = result.regime
                self._candidate_count = 1

            if self._candidate_count >= self.persistence:
                old = self.current_regime
                self.current_regime = self._candidate
                self._candidate_count = 0
                log.info(f"VOL REGIME TRANSITION: {old} → {self.current_regime} "
                         f"(confidence={result.confidence:.2f})")
        else:
            self._candidate_count = 0

        # Override result with hysteresis-stable regime
        result.regime = self.current_regime
        result.sizing_mult = _SIZING_MULTS.get(self.current_regime, 1.0)
        result.strategy_hint = _STRATEGY_HINTS.get(self.current_regime, "")
        self._last_result = result
        return result

    @property
    def summary(self) -> dict:
        return {
            "regime": self.current_regime,
            "candidate": self._candidate,
            "candidate_count": self._candidate_count,
            "last_confidence": self._last_result.confidence if self._last_result else 0.0,
            "last_stress": self._last_result.regime_score if self._last_result else 0.0,
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_tracker: Optional[VolRegimeTracker] = None


def get_vol_regime(**kwargs) -> VolRegimeResult:
    """Get current vol regime with hysteresis. Singleton tracker."""
    global _tracker
    if _tracker is None:
        _tracker = VolRegimeTracker()
    return _tracker.update(**kwargs)


# ---------------------------------------------------------------------------
# Micro Regime — Tick-Level Microstructure (Book 124 extension)
# ---------------------------------------------------------------------------

@dataclass
class MicroRegime:
    """Tick-level microstructure regime classification."""
    regime: str = "NORMAL"        # NORMAL | TOXIC | THIN
    toxicity: float = 0.0         # 0-1, higher = more informed flow
    spread_z: float = 0.0         # Spread z-score vs recent history
    confidence: float = 0.5       # How clearly we're in this regime


def classify_micro_regime(
    vpin: float = 0.5,
    spread_pct: float = 0.1,
    quote_imbalance: float = 0.0,
) -> MicroRegime:
    """Classify tick-level microstructure regime.

    Args:
        vpin: Volume-synchronized probability of informed trading (0-1)
        spread_pct: Current bid-ask spread as % of mid
        quote_imbalance: (bid_size - ask_size) / (bid_size + ask_size), range [-1, 1]

    Returns: MicroRegime with NORMAL/TOXIC/THIN classification
    """
    # Toxicity composite: weighted VPIN + spread + imbalance magnitude
    toxicity = 0.5 * min(1.0, vpin) + 0.3 * min(1.0, spread_pct / 0.5) + 0.2 * abs(quote_imbalance)
    toxicity = max(0.0, min(1.0, toxicity))

    # Spread z-score approximation (vs typical 0.1% baseline)
    spread_baseline = 0.10
    spread_std = 0.08
    spread_z = (spread_pct - spread_baseline) / spread_std if spread_std > 0 else 0.0

    # Classification thresholds
    if toxicity > 0.7 or (vpin > 0.75 and spread_pct > 0.3):
        regime = "TOXIC"
        confidence = min(1.0, toxicity)
    elif spread_pct > 0.5 or spread_z > 3.0:
        regime = "THIN"
        confidence = min(1.0, 0.5 + spread_z / 10.0)
    else:
        regime = "NORMAL"
        confidence = max(0.5, 1.0 - toxicity)

    return MicroRegime(
        regime=regime,
        toxicity=round(toxicity, 4),
        spread_z=round(spread_z, 2),
        confidence=round(confidence, 3),
    )


# ---------------------------------------------------------------------------
# Macro Regime — Weekly/Monthly Economic Cycle (Book 124 extension)
# ---------------------------------------------------------------------------

@dataclass
class MacroRegime:
    """Weekly/monthly macro-economic regime classification."""
    cycle: str = "EXPANSION"        # EXPANSION | CONTRACTION | TRANSITION
    monetary: str = "NEUTRAL"       # TIGHTENING | EASING | NEUTRAL
    confidence: float = 0.5


def classify_macro_regime(
    vix: float = 20.0,
    yield_curve_slope: float = 0.5,
    credit_spread: float = 1.0,
) -> MacroRegime:
    """Classify weekly/monthly macro regime from market indicators.

    Args:
        vix: VIX index level
        yield_curve_slope: 10Y-2Y spread in percentage points (negative = inverted)
        credit_spread: HY-IG spread in percentage points (higher = stress)

    Returns: MacroRegime with cycle and monetary policy classification
    """
    # Economic cycle classification
    # Expansion: low VIX, positive yield curve, tight credit
    # Contraction: high VIX, inverted curve, wide credit
    stress_score = (
        0.35 * min(1.0, max(0.0, (vix - 12) / 30)) +
        0.35 * min(1.0, max(0.0, (0.5 - yield_curve_slope) / 2.0)) +
        0.30 * min(1.0, max(0.0, (credit_spread - 1.0) / 4.0))
    )

    if stress_score < 0.3:
        cycle = "EXPANSION"
    elif stress_score > 0.6:
        cycle = "CONTRACTION"
    else:
        cycle = "TRANSITION"

    # Monetary policy stance (simplified from yield curve slope)
    if yield_curve_slope < -0.2:
        monetary = "TIGHTENING"
    elif yield_curve_slope > 1.0:
        monetary = "EASING"
    else:
        monetary = "NEUTRAL"

    confidence = abs(stress_score - 0.5) * 2  # Higher when clearly in a regime
    confidence = max(0.3, min(0.95, confidence))

    return MacroRegime(
        cycle=cycle,
        monetary=monetary,
        confidence=round(confidence, 3),
    )


# ---------------------------------------------------------------------------
# Multi-Scale Regime Aggregator
# ---------------------------------------------------------------------------

# Severity ordering for conflict resolution (lower index = less severe)
_SEVERITY_ORDER = {
    # Micro
    "NORMAL": 0, "THIN": 1, "TOXIC": 2,
    # Meso (VolRegime)
    "LOW_VOL_GRIND": 0, "RECOVERY": 1, "ELEVATED": 3, "CRISIS": 4,
    # Macro cycle
    "EXPANSION": 0, "TRANSITION": 1, "CONTRACTION": 2,
}


class MultiScaleRegimeAggregator:
    """Combine micro + meso (VolRegime) + macro into a unified regime view.

    Hierarchy constraint: macro <= meso <= micro severity.
    The most conservative (highest severity) wins when scales conflict.
    """

    def aggregate(
        self,
        micro: MicroRegime,
        meso: VolRegimeResult,
        macro: MacroRegime,
    ) -> dict:
        """Aggregate three regime scales into a unified assessment.

        Returns dict with:
          - micro_regime, meso_regime, macro_cycle, macro_monetary
          - overall_severity: 0-4 (calm to panic)
          - sizing_mult: position sizing multiplier
          - action: "NORMAL_OPS" | "REDUCE_EXPOSURE" | "DEFENSIVE" | "SURVIVAL"
        """
        micro_sev = _SEVERITY_ORDER.get(micro.regime, 1)
        meso_sev = _SEVERITY_ORDER.get(meso.regime, 1)
        macro_sev = _SEVERITY_ORDER.get(macro.cycle, 1)

        # Most conservative wins
        overall_sev = max(micro_sev, meso_sev, macro_sev)

        # Sizing: product of individual multipliers (compounding caution)
        micro_mult = {0: 1.0, 1: 0.7, 2: 0.3}.get(micro_sev, 0.5)
        meso_mult = meso.sizing_mult
        macro_mult = {0: 1.0, 1: 0.8, 2: 0.5}.get(macro_sev, 0.6)
        sizing_mult = round(micro_mult * meso_mult * macro_mult, 3)
        sizing_mult = max(0.05, sizing_mult)  # Floor at 5%

        # Action mapping
        if overall_sev <= 1:
            action = "NORMAL_OPS"
        elif overall_sev == 2:
            action = "REDUCE_EXPOSURE"
        elif overall_sev == 3:
            action = "DEFENSIVE"
        else:
            action = "SURVIVAL"

        return {
            "micro_regime": micro.regime,
            "meso_regime": meso.regime,
            "macro_cycle": macro.cycle,
            "macro_monetary": macro.monetary,
            "overall_severity": overall_sev,
            "sizing_mult": sizing_mult,
            "action": action,
            "conflict": self.resolve_conflict(micro, meso, macro),
            "confidence": round(min(micro.confidence, meso.confidence, macro.confidence), 3),
        }

    def resolve_conflict(
        self,
        micro: MicroRegime,
        meso: VolRegimeResult,
        macro: MacroRegime,
    ) -> str:
        """Resolve conflicts between regime scales.

        Returns the regime label of the most conservative (highest severity) scale.
        """
        scales = [
            (micro.regime, _SEVERITY_ORDER.get(micro.regime, 1)),
            (meso.regime, _SEVERITY_ORDER.get(meso.regime, 1)),
            (macro.cycle, _SEVERITY_ORDER.get(macro.cycle, 1)),
        ]
        # Most severe wins
        most_severe = max(scales, key=lambda s: s[1])
        return most_severe[0]
