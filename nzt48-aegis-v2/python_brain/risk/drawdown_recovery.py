"""Drawdown Monitor & Recovery Sizing — Book 42.

5-phase drawdown response with quadratic recovery sizing.
Progressively reduces position sizes as drawdown deepens,
preventing ruin while maintaining ability to recover.

Phases:
  NORMAL     (0-5% DD):  Full Kelly, all strategies active
  MONITORING (5-10% DD): 75% Kelly, tighten stops 25%
  RECOVERY   (10-20% DD): 50% Kelly, favor high-WR strategies
  CRITICAL   (20-25% DD): 25% Kelly, exit-only mode
  HALTED     (25%+ DD):  Close all positions, no new entries

Quadratic recovery sizing:
  recovery_factor = 1.0 - alpha * (DD / max_DD)^2
  effective_kelly = base_kelly * recovery_factor

Usage:
    from python_brain.risk.drawdown_recovery import (
        DrawdownMonitor, DrawdownPhase,
    )

    monitor = DrawdownMonitor(initial_equity=10000)
    monitor.update(current_equity=9200)
    phase = monitor.phase  # DrawdownPhase.MONITORING
    scale = monitor.kelly_scale()  # 0.75
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

log = logging.getLogger("drawdown_recovery")


class DrawdownPhase(Enum):
    NORMAL = "NORMAL"          # 0-5% DD
    MONITORING = "MONITORING"  # 5-10% DD
    RECOVERY = "RECOVERY"      # 10-20% DD
    CRITICAL = "CRITICAL"      # 20-25% DD
    HALTED = "HALTED"          # 25%+ DD


# Phase thresholds (drawdown % from HWM)
PHASE_THRESHOLDS = {
    DrawdownPhase.MONITORING: 5.0,
    DrawdownPhase.RECOVERY: 10.0,
    DrawdownPhase.CRITICAL: 20.0,
    DrawdownPhase.HALTED: 25.0,
}

# Kelly multipliers per phase
PHASE_KELLY_SCALE = {
    DrawdownPhase.NORMAL: 1.0,
    DrawdownPhase.MONITORING: 0.75,
    DrawdownPhase.RECOVERY: 0.50,
    DrawdownPhase.CRITICAL: 0.25,
    DrawdownPhase.HALTED: 0.0,
}

# Stop tightening per phase (multiplier on normal ATR trail)
PHASE_STOP_TIGHTNESS = {
    DrawdownPhase.NORMAL: 1.0,
    DrawdownPhase.MONITORING: 0.75,
    DrawdownPhase.RECOVERY: 0.60,
    DrawdownPhase.CRITICAL: 0.40,
    DrawdownPhase.HALTED: 0.0,
}

# Leverage reduction schedule (max effective leverage per phase)
PHASE_MAX_LEVERAGE = {
    DrawdownPhase.NORMAL: 3.0,
    DrawdownPhase.MONITORING: 2.5,
    DrawdownPhase.RECOVERY: 2.0,
    DrawdownPhase.CRITICAL: 1.5,
    DrawdownPhase.HALTED: 0.0,
}

# Confidence floor per phase
PHASE_MIN_CONFIDENCE = {
    DrawdownPhase.NORMAL: 50,
    DrawdownPhase.MONITORING: 55,
    DrawdownPhase.RECOVERY: 65,
    DrawdownPhase.CRITICAL: 80,
    DrawdownPhase.HALTED: 100,  # Nothing passes
}


class DrawdownMonitor:
    """Track drawdown from high-water mark and manage recovery sizing."""

    def __init__(self, initial_equity: float = 10000.0, sacred_limit: float = 8.0):
        self._initial_equity = initial_equity
        self._hwm = initial_equity  # High-water mark
        self._current_equity = initial_equity
        self._sacred_limit = sacred_limit  # Book 7: 8% sacred limit
        self._phase = DrawdownPhase.NORMAL
        self._dd_pct = 0.0
        self._peak_dd_pct = 0.0

    @property
    def phase(self) -> DrawdownPhase:
        return self._phase

    @property
    def drawdown_pct(self) -> float:
        return self._dd_pct

    @property
    def peak_drawdown_pct(self) -> float:
        return self._peak_dd_pct

    def update(self, current_equity: float) -> DrawdownPhase:
        """Update with new equity value. Returns current phase."""
        self._current_equity = current_equity

        # Update HWM
        if current_equity > self._hwm:
            self._hwm = current_equity

        # Compute drawdown
        if self._hwm > 0:
            self._dd_pct = (self._hwm - current_equity) / self._hwm * 100
        else:
            self._dd_pct = 0.0

        self._peak_dd_pct = max(self._peak_dd_pct, self._dd_pct)

        # Determine phase
        old_phase = self._phase
        if self._dd_pct >= PHASE_THRESHOLDS[DrawdownPhase.HALTED]:
            self._phase = DrawdownPhase.HALTED
        elif self._dd_pct >= PHASE_THRESHOLDS[DrawdownPhase.CRITICAL]:
            self._phase = DrawdownPhase.CRITICAL
        elif self._dd_pct >= PHASE_THRESHOLDS[DrawdownPhase.RECOVERY]:
            self._phase = DrawdownPhase.RECOVERY
        elif self._dd_pct >= PHASE_THRESHOLDS[DrawdownPhase.MONITORING]:
            self._phase = DrawdownPhase.MONITORING
        else:
            self._phase = DrawdownPhase.NORMAL

        if self._phase != old_phase:
            log.warning(
                "DRAWDOWN_PHASE: %s → %s (DD=%.1f%%, HWM=%.0f, equity=%.0f)",
                old_phase.value, self._phase.value,
                self._dd_pct, self._hwm, current_equity,
            )

        return self._phase

    def kelly_scale(self) -> float:
        """Get Kelly multiplier for current drawdown phase."""
        return PHASE_KELLY_SCALE[self._phase]

    def quadratic_recovery_factor(self, alpha: float = 1.0) -> float:
        """Quadratic recovery sizing (Book 42 Section 8).

        recovery_factor = 1.0 - alpha * (DD / max_DD)^2
        where max_DD = sacred limit (8%).
        """
        max_dd = self._sacred_limit
        if max_dd <= 0:
            return 1.0
        ratio = min(self._dd_pct / max_dd, 1.0)
        return max(0.0, 1.0 - alpha * ratio * ratio)

    def stop_tightness(self) -> float:
        """ATR trail multiplier for current phase."""
        return PHASE_STOP_TIGHTNESS[self._phase]

    def max_leverage(self) -> float:
        """Maximum effective leverage for current phase."""
        return PHASE_MAX_LEVERAGE[self._phase]

    def min_confidence(self) -> int:
        """Minimum signal confidence for current phase."""
        return PHASE_MIN_CONFIDENCE[self._phase]

    def should_block_entry(self) -> bool:
        """In HALTED phase, block all new entries."""
        return self._phase == DrawdownPhase.HALTED

    def should_flatten(self) -> bool:
        """In CRITICAL or HALTED, consider flattening positions."""
        return self._phase in (DrawdownPhase.CRITICAL, DrawdownPhase.HALTED)

    def strategy_weight_adjustments(self) -> Dict[str, float]:
        """Adjust strategy weights during recovery (Book 42 Section 13).

        Favor high-WR strategies during recovery phases.
        """
        if self._phase in (DrawdownPhase.NORMAL, DrawdownPhase.MONITORING):
            return {}  # No adjustment

        # During RECOVERY/CRITICAL: favor mean reversion, reduce momentum
        return {
            "S2_Reversion": 1.5,     # High WR, consistent
            "TypeE": 1.5,            # IBS mean reversion
            "TypeF": 1.0,            # OBV divergence (high WR)
            "VanguardSniper": 0.3,   # Momentum — reduce in recovery
            "TypeB": 0.3,            # EarlyRunner — reduce
            "S3_MacroTrend": 0.3,    # Trend following — reduce
            "S7_TailHedge": 1.5,     # Tail hedge — increase
        }

    def to_dict(self) -> dict:
        return {
            "phase": self._phase.value,
            "drawdown_pct": round(self._dd_pct, 2),
            "peak_drawdown_pct": round(self._peak_dd_pct, 2),
            "hwm": round(self._hwm, 2),
            "equity": round(self._current_equity, 2),
            "kelly_scale": round(self.kelly_scale(), 3),
            "quadratic_factor": round(self.quadratic_recovery_factor(), 3),
            "stop_tightness": round(self.stop_tightness(), 3),
            "max_leverage": self.max_leverage(),
            "min_confidence": self.min_confidence(),
        }
