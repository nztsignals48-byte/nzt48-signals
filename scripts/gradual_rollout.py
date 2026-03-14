"""Gradual Rollout - Phases 1-3 with automatic gates"""

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class Phase(Enum):
    PHASE_1 = "25%"  # Days 1-3: WR≥55%, Sharpe≥0.5
    PHASE_2 = "50%"  # Days 4-7: WR≥55%, Sharpe≥0.5, no heat cap
    PHASE_3 = "100%"  # Day 8+: monitor, revert if drops


class GradualRollout:
    """Automatically scales position sizing through 3 phases"""

    def __init__(self, starting_phase: Phase = Phase.PHASE_1):
        self.logger = logging.getLogger("nzt48.gradual_rollout")
        self.current_phase = starting_phase
        self.position_multipliers = {Phase.PHASE_1: 0.25, Phase.PHASE_2: 0.50, Phase.PHASE_3: 1.00}

    def get_position_multiplier(self) -> float:
        """Get position sizing multiplier for current phase"""
        return self.position_multipliers[self.current_phase]

    def advance_phase(self, win_rate: float, sharpe: float, heat_cap_hit: bool = False) -> bool:
        """Check if ready to advance to next phase"""
        if self.current_phase == Phase.PHASE_1:
            if win_rate >= 0.55 and sharpe >= 0.5:
                self.current_phase = Phase.PHASE_2
                self.logger.info("✅ Advanced to Phase 2 (50% sizing)")
                return True
        elif self.current_phase == Phase.PHASE_2:
            if win_rate >= 0.55 and sharpe >= 0.5 and not heat_cap_hit:
                self.current_phase = Phase.PHASE_3
                self.logger.info("✅ Advanced to Phase 3 (100% sizing)")
                return True
        return False


if __name__ == "__main__":
    rollout = GradualRollout()
    mult = rollout.get_position_multiplier()
    print(f"✅ Current phase multiplier: {mult*100:.0f}%")
