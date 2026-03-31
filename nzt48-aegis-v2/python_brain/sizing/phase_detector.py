"""Book 11: Capital Phase Detector.

Detects capital phase transitions and automatically adjusts risk parameters
and strategy unlocks. Five phases based on account equity:

  Phase 1: £10K-£25K  — Core strategies only, max 3 positions, conservative
  Phase 2: £25K-£50K  — Unlock TypeB/TypeF, max 4 positions
  Phase 3: £50K-£100K — Full strategy suite, max 5 positions, add SIPP
  Phase 4: £100K-£500K — Multi-account, max 8 positions, reduced Kelly decay
  Phase 5: £500K+      — Institutional mode, max 12 positions

Wired into nightly pipeline Step 16.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

DATA_DIR = os.environ.get("AEGIS_DATA_DIR", "/app/data")
PHASE_FILE = os.path.join(DATA_DIR, "capital_phase.json")


@dataclass
class PhaseConfig:
    """Parameters for each capital phase."""
    phase: int
    label: str
    min_equity: float
    max_positions: int
    kelly_cap: float
    confidence_floor: int
    unlocked_strategies: List[str]
    max_sector_heat: float
    max_portfolio_heat: float


PHASES = [
    PhaseConfig(
        phase=1, label="SEED", min_equity=0,
        max_positions=3, kelly_cap=0.20, confidence_floor=55,
        unlocked_strategies=[
            "VanguardSniper", "ApexScout", "IBS_MeanReversion",
            "S1_Microstructure", "S2_Reversion", "S3_MacroTrend",
            "S7_TailHedge", "VolCompression", "RebalancingFlow",
            "NAVArbitrage", "AlphaFactory", "LeadLag",
        ],
        max_sector_heat=0.40, max_portfolio_heat=0.75,
    ),
    PhaseConfig(
        phase=2, label="GROWTH", min_equity=25_000,
        max_positions=4, kelly_cap=0.25, confidence_floor=52,
        unlocked_strategies=[
            "VanguardSniper", "ApexScout", "IBS_MeanReversion",
            "VolExpansion", "ORB_Breakout", "GapFade",
            "S1_Microstructure", "S2_Reversion", "S3_MacroTrend",
            "S4_VolPremium", "S7_TailHedge",
            "VolCompression", "RebalancingFlow", "NAVArbitrage",
            "AlphaFactory", "LeadLag", "CalendarAnomalies",
        ],
        max_sector_heat=0.35, max_portfolio_heat=0.80,
    ),
    PhaseConfig(
        phase=3, label="MOMENTUM", min_equity=50_000,
        max_positions=5, kelly_cap=0.28, confidence_floor=50,
        unlocked_strategies=[
            "VanguardSniper", "ApexScout", "IBS_MeanReversion",
            "VolExpansion", "ORB_Breakout", "GapFade",
            "S1_Microstructure", "S2_Reversion", "S3_MacroTrend",
            "S4_VolPremium", "S5_OvernightCarry", "S6_Catalyst", "S7_TailHedge",
            "VolCompression", "RebalancingFlow", "NAVArbitrage",
            "AlphaFactory", "LeadLag", "CalendarAnomalies", "PairsTrading",
        ],
        max_sector_heat=0.30, max_portfolio_heat=0.85,
    ),
    PhaseConfig(
        phase=4, label="SCALING", min_equity=100_000,
        max_positions=8, kelly_cap=0.30, confidence_floor=48,
        unlocked_strategies=["ALL"],
        max_sector_heat=0.25, max_portfolio_heat=0.85,
    ),
    PhaseConfig(
        phase=5, label="INSTITUTIONAL", min_equity=500_000,
        max_positions=12, kelly_cap=0.35, confidence_floor=45,
        unlocked_strategies=["ALL"],
        max_sector_heat=0.20, max_portfolio_heat=0.90,
    ),
]


class PhaseDetector:
    """Detects capital phase and manages transitions."""

    def __init__(self):
        self._current_phase: int = 1
        self._peak_equity: float = 0.0
        self._transition_history: List[Dict] = []
        self._loaded = False

    def current_phase(self, equity: float = 0.0) -> PhaseConfig:
        """Get current phase config based on equity."""
        if equity > self._peak_equity:
            self._peak_equity = equity

        # Use peak equity for phase (no demotion on drawdown)
        for phase_config in reversed(PHASES):
            if self._peak_equity >= phase_config.min_equity:
                return phase_config

        return PHASES[0]

    def check_transition(self, equity: float) -> Optional[Dict]:
        """Check if equity level triggers a phase transition.

        Returns transition dict if phase changed, None otherwise.
        """
        new_phase = self.current_phase(equity)

        if new_phase.phase != self._current_phase:
            old_phase = self._current_phase
            self._current_phase = new_phase.phase

            transition = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "old_phase": old_phase,
                "new_phase": new_phase.phase,
                "new_label": new_phase.label,
                "equity": equity,
                "peak_equity": self._peak_equity,
                "new_max_positions": new_phase.max_positions,
                "new_kelly_cap": new_phase.kelly_cap,
                "new_strategies": new_phase.unlocked_strategies,
            }
            self._transition_history.append(transition)
            return transition

        return None

    def strategy_unlock_list(self, equity: float = 0.0) -> List[str]:
        """Get list of strategies unlocked at current phase."""
        phase = self.current_phase(equity)
        return phase.unlocked_strategies

    def is_strategy_unlocked(self, strategy: str, equity: float = 0.0) -> bool:
        """Check if a specific strategy is unlocked."""
        unlocked = self.strategy_unlock_list(equity)
        return "ALL" in unlocked or strategy in unlocked

    def risk_params(self, equity: float = 0.0) -> Dict:
        """Get phase-appropriate risk parameters."""
        phase = self.current_phase(equity)
        return {
            "phase": phase.phase,
            "label": phase.label,
            "max_positions": phase.max_positions,
            "kelly_cap": phase.kelly_cap,
            "confidence_floor": phase.confidence_floor,
            "max_sector_heat": phase.max_sector_heat,
            "max_portfolio_heat": phase.max_portfolio_heat,
        }

    def save(self):
        try:
            os.makedirs(os.path.dirname(PHASE_FILE), exist_ok=True)
            with open(PHASE_FILE, "w") as f:
                json.dump({
                    "current_phase": self._current_phase,
                    "peak_equity": self._peak_equity,
                    "transitions": self._transition_history[-20:],
                }, f, indent=2)
        except Exception:
            pass

    def load(self):
        if self._loaded:
            return
        self._loaded = True
        if not os.path.exists(PHASE_FILE):
            return
        try:
            with open(PHASE_FILE) as f:
                data = json.load(f)
            self._current_phase = data.get("current_phase", 1)
            self._peak_equity = data.get("peak_equity", 0.0)
            self._transition_history = data.get("transitions", [])
        except Exception:
            pass


# Singleton
_detector: Optional[PhaseDetector] = None


def get_phase_detector() -> PhaseDetector:
    global _detector
    if _detector is None:
        _detector = PhaseDetector()
        _detector.load()
    return _detector


def run_phase_check() -> Dict:
    """Nightly: check for phase transitions and report."""
    detector = get_phase_detector()

    # Load current equity from persistent state
    equity = 10000.0  # Default
    pm_file = os.path.join(DATA_DIR, "persistent_memory.json")
    if os.path.exists(pm_file):
        try:
            with open(pm_file) as f:
                pm = json.load(f)
            equity = pm.get("equity", pm.get("current_equity", 10000.0))
        except Exception:
            pass

    transition = detector.check_transition(equity)
    params = detector.risk_params(equity)
    detector.save()

    result = {
        "status": "transition" if transition else "stable",
        "equity": equity,
        "phase": params,
    }

    if transition:
        result["transition"] = transition
        # Alert on transition
        try:
            from python_brain.ouroboros.claude_helper import send_telegram
            send_telegram(
                f"PHASE TRANSITION: Phase {transition['old_phase']} -> "
                f"{transition['new_phase']} ({transition['new_label']})\n"
                f"Equity: £{equity:,.0f}\n"
                f"Max positions: {transition['new_max_positions']}\n"
                f"Kelly cap: {transition['new_kelly_cap']}"
            )
        except Exception:
            pass

    return result


if __name__ == "__main__":
    result = run_phase_check()
    print(f"Phase check: {result['status']}, phase={result['phase']['label']}")
