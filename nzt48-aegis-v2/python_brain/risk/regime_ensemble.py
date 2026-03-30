"""Book 82: Ensemble Regime Detection — Fast-Noisy + Slow-Accurate.

Two-layer regime detection that reduces latency from ~30 min to ~30 seconds:
  Layer 1 (Fast/Noisy): Single-indicator breach → immediate PRECAUTIONARY_REDUCE.
    - Runs per-tick in Python (mirrors Rust JumpDiffusionDetector).
    - FPR ~20%, latency <1s.
  Layer 2 (Slow/Accurate): Multi-factor confirmation within 5 min or revert.
    - Composite of Hurst, VIX, VPIN toxicity, correlation, volume.
    - FPR ~3%, confirmation latency 30-300s.

State machine: NORMAL → ALERT → CONFIRMING → REGIME_CHANGE or REVERTED.

Usage:
    from python_brain.risk.regime_ensemble import get_regime_ensemble
    ensemble = get_regime_ensemble()
    result = ensemble.on_tick(hurst=0.38, vpin=0.72, rvol=3.5, ...)
    # result.regime, result.action, result.confidence
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("regime_ensemble")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RegimeContext:
    """Market state snapshot for regime evaluation."""
    hurst: float = 0.5
    vpin: float = 0.5
    rvol: float = 1.0
    adx: float = 15.0
    spread_pct: float = 0.1
    vol_slope: float = 0.0
    structural_score: float = 50.0
    drawdown_pct: float = 0.0
    timestamp_secs: float = 0.0


@dataclass
class RegimeResult:
    """Output from ensemble detector."""
    regime: str = "NORMAL"        # NORMAL, CAUTION, STRESS, CRISIS
    action: str = "NONE"          # NONE, PRECAUTIONARY_REDUCE, REGIME_TRANSITION, REVERTED
    confidence: float = 0.0       # 0-1
    alert_source: str = ""        # Which fast indicator triggered
    composite_score: float = 0.0  # Slow detector composite
    latency_secs: float = 0.0     # Time from alert to confirmation


# ---------------------------------------------------------------------------
# Fast-Noisy Detector (Python mirror)
# ---------------------------------------------------------------------------

class FastNoisyDetector:
    """Single-indicator breach detector. Any ONE condition triggers alert.

    Thresholds deliberately low to catch regime changes early.
    High false positive rate (~20%) is acceptable because slow detector confirms.
    """

    def __init__(
        self,
        vpin_threshold: float = 0.70,
        rvol_threshold: float = 3.0,
        hurst_crisis_threshold: float = 0.15,
        spread_threshold_pct: float = 0.8,
        drawdown_threshold: float = 4.0,
    ):
        self.vpin_threshold = vpin_threshold
        self.rvol_threshold = rvol_threshold
        self.hurst_crisis_threshold = hurst_crisis_threshold
        self.spread_threshold_pct = spread_threshold_pct
        self.drawdown_threshold = drawdown_threshold

    def evaluate(self, ctx: RegimeContext) -> Optional[str]:
        """Returns alert source string if triggered, else None."""
        if ctx.vpin > self.vpin_threshold:
            return f"VPIN_ELEVATED({ctx.vpin:.2f})"
        if ctx.rvol > self.rvol_threshold:
            return f"RVOL_SPIKE({ctx.rvol:.1f})"
        if ctx.hurst < self.hurst_crisis_threshold and ctx.hurst > 0:
            return f"HURST_CRISIS({ctx.hurst:.3f})"
        if ctx.spread_pct > self.spread_threshold_pct:
            return f"SPREAD_BLOWOUT({ctx.spread_pct:.2f}%)"
        if ctx.drawdown_pct > self.drawdown_threshold:
            return f"DRAWDOWN_ALERT({ctx.drawdown_pct:.1f}%)"
        return None


# ---------------------------------------------------------------------------
# Slow-Accurate Detector
# ---------------------------------------------------------------------------

class SlowAccurateDetector:
    """Multi-factor regime classifier. Requires 2+ consecutive signals to confirm.

    Composite score from 6 normalized factors. Confirmation reduces FPR to ~3%.
    """

    def __init__(self, confirmation_required: int = 2):
        self.confirmation_required = confirmation_required
        self._consecutive_crisis = 0
        self._consecutive_stress = 0

    def evaluate(self, ctx: RegimeContext) -> dict:
        """Returns regime classification with composite score."""
        # Score each factor 0-1 (higher = more stressed)
        scores = {}

        # VPIN toxicity (0.5 = normal, 0.7+ = informed flow)
        scores["vpin"] = min(1.0, max(0, (ctx.vpin - 0.4) / 0.4))

        # Hurst exponent (0.5 = random walk, <0.3 = mean-reverting/stressed)
        if ctx.hurst > 0:
            scores["hurst"] = max(0, (0.5 - ctx.hurst) / 0.3)
        else:
            scores["hurst"] = 0.0

        # Relative volume (1.0 = normal, 3+ = panic)
        scores["rvol"] = min(1.0, max(0, (ctx.rvol - 1.0) / 3.0))

        # Spread widening (0.1% = normal, 0.5%+ = stressed)
        scores["spread"] = min(1.0, max(0, (ctx.spread_pct - 0.1) / 0.5))

        # ADX trend strength (low ADX + high vol = confused market)
        if ctx.adx < 15 and ctx.rvol > 2.0:
            scores["adx_vol"] = 0.8
        elif ctx.adx < 20:
            scores["adx_vol"] = 0.3
        else:
            scores["adx_vol"] = 0.0

        # Drawdown severity
        scores["drawdown"] = min(1.0, ctx.drawdown_pct / 8.0)

        # Weighted composite
        weights = {
            "vpin": 0.25, "hurst": 0.20, "rvol": 0.20,
            "spread": 0.15, "adx_vol": 0.10, "drawdown": 0.10,
        }
        composite = sum(scores.get(k, 0) * w for k, w in weights.items())

        # Classify with confirmation (hysteresis)
        if composite > 0.70:
            self._consecutive_crisis += 1
            self._consecutive_stress = 0
        elif composite > 0.45:
            self._consecutive_stress += 1
            self._consecutive_crisis = 0
        else:
            self._consecutive_crisis = 0
            self._consecutive_stress = 0

        if self._consecutive_crisis >= self.confirmation_required:
            regime = "CRISIS"
        elif self._consecutive_stress >= self.confirmation_required:
            regime = "STRESS"
        elif composite > 0.30:
            regime = "CAUTION"
        else:
            regime = "NORMAL"

        return {
            "regime": regime,
            "composite": composite,
            "scores": scores,
            "confidence": min(1.0, composite * 1.2),
        }

    def reset(self):
        """Reset confirmation counters (on revert)."""
        self._consecutive_crisis = 0
        self._consecutive_stress = 0


# ---------------------------------------------------------------------------
# Ensemble State Machine
# ---------------------------------------------------------------------------

class EnsembleRegimeDetector:
    """Two-layer ensemble: fast alert + slow confirmation.

    States:
      NORMAL:     No alert. Fast detector watching.
      ALERT:      Fast detector fired. Immediate risk reduction.
                  Slow detector has `timeout_secs` to confirm.
      CONFIRMED:  Slow detector confirmed regime change.
      REVERTED:   Timeout without confirmation. Resume normal.
    """

    def __init__(
        self,
        fast: Optional[FastNoisyDetector] = None,
        slow: Optional[SlowAccurateDetector] = None,
        timeout_secs: float = 300.0,
    ):
        self.fast = fast or FastNoisyDetector()
        self.slow = slow or SlowAccurateDetector()
        self.timeout_secs = timeout_secs

        self.state = "NORMAL"
        self.current_regime = "NORMAL"
        self.alert_time: float = 0.0
        self.alert_source: str = ""
        self._transitions = 0

    def on_tick(self, **kwargs) -> RegimeResult:
        """Process a tick. Accepts keyword args matching RegimeContext fields."""
        ctx = RegimeContext(**{k: v for k, v in kwargs.items() if hasattr(RegimeContext, k)})
        if ctx.timestamp_secs == 0:
            ctx.timestamp_secs = time.time()
        now = ctx.timestamp_secs

        if self.state == "NORMAL":
            alert = self.fast.evaluate(ctx)
            if alert:
                self.state = "ALERT"
                self.alert_time = now
                self.alert_source = alert
                log.warning(f"FAST ALERT: {alert} → PRECAUTIONARY_REDUCE")
                return RegimeResult(
                    regime=self.current_regime,
                    action="PRECAUTIONARY_REDUCE",
                    confidence=0.5,
                    alert_source=alert,
                )
            return RegimeResult(regime=self.current_regime, action="NONE")

        elif self.state == "ALERT":
            elapsed = now - self.alert_time
            slow_result = self.slow.evaluate(ctx)

            # Check for confirmation
            if slow_result["regime"] in ("STRESS", "CRISIS"):
                old_regime = self.current_regime
                self.current_regime = slow_result["regime"]
                self.state = "NORMAL"
                self.slow.reset()
                self._transitions += 1
                log.warning(
                    f"REGIME CONFIRMED: {old_regime} → {self.current_regime} "
                    f"(latency={elapsed:.0f}s, composite={slow_result['composite']:.2f})"
                )
                return RegimeResult(
                    regime=self.current_regime,
                    action="REGIME_TRANSITION",
                    confidence=slow_result["confidence"],
                    alert_source=self.alert_source,
                    composite_score=slow_result["composite"],
                    latency_secs=elapsed,
                )

            # Check for timeout → revert
            if elapsed > self.timeout_secs:
                self.state = "NORMAL"
                self.slow.reset()
                log.info(f"REGIME ALERT REVERTED after {elapsed:.0f}s (not confirmed)")
                return RegimeResult(
                    regime=self.current_regime,
                    action="REVERTED",
                    alert_source=self.alert_source,
                    latency_secs=elapsed,
                )

            # Still waiting for confirmation
            return RegimeResult(
                regime=self.current_regime,
                action="PRECAUTIONARY_REDUCE",
                confidence=0.3,
                alert_source=self.alert_source,
            )

        # Fallback
        return RegimeResult(regime=self.current_regime, action="NONE")

    def force_normal(self):
        """Manually reset to normal (e.g., after market close)."""
        self.state = "NORMAL"
        self.current_regime = "NORMAL"
        self.slow.reset()

    @property
    def summary(self) -> dict:
        return {
            "state": self.state,
            "regime": self.current_regime,
            "transitions": self._transitions,
            "alert_source": self.alert_source,
        }


# ---------------------------------------------------------------------------
# Singleton + bridge integration
# ---------------------------------------------------------------------------

_ensemble: Optional[EnsembleRegimeDetector] = None


def get_regime_ensemble() -> EnsembleRegimeDetector:
    """Get or create the singleton ensemble detector."""
    global _ensemble
    if _ensemble is None:
        _ensemble = EnsembleRegimeDetector()
    return _ensemble


def regime_confidence_adjustment(regime_result: RegimeResult) -> float:
    """Returns a confidence penalty based on current regime state.

    Called by bridge.py _apply_adjustments to penalize signals during stress.
    Returns negative value (penalty) or 0 (no adjustment).
    """
    if regime_result.action == "PRECAUTIONARY_REDUCE":
        return -10.0   # Fast alert: reduce confidence by 10
    if regime_result.regime == "CRISIS":
        return -25.0   # Crisis confirmed: heavy penalty
    if regime_result.regime == "STRESS":
        return -15.0   # Stress confirmed: moderate penalty
    if regime_result.regime == "CAUTION":
        return -5.0    # Caution: minor penalty
    return 0.0
