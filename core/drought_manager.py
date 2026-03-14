"""
NZT-48 Core -- Drought State Machine (W5)
Manages drought state with escalation tiers.
Decision D3: 20 cycles (~20 min) threshold.
Feature flag: drought_escalation in settings.yaml
"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.core.drought_manager")

ARTIFACTS_ROOT = Path(__file__).parent.parent / "artifacts"


class DroughtState:
    NORMAL = "NORMAL"
    WATCH = "WATCH"        # 10+ empty cycles
    DROUGHT = "DROUGHT"    # 20+ empty cycles
    CRITICAL = "CRITICAL"  # 60+ empty cycles
    CLEARED = "CLEARED"    # Signal passed all gates and was sent


# Escalation thresholds (in empty scan cycles)
WATCH_THRESHOLD = 10
DROUGHT_THRESHOLD = 20
CRITICAL_THRESHOLD = 60


@dataclass
class DroughtSnapshot:
    state: str = DroughtState.NORMAL
    empty_cycles: int = 0
    last_signal_time: Optional[float] = None
    last_state_change: Optional[float] = None
    contradiction_detected: bool = False
    contradiction_msg: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class DroughtManager:
    """Manages drought state machine: NORMAL -> WATCH -> DROUGHT -> CRITICAL -> CLEARED."""

    def __init__(self):
        self._state = DroughtState.NORMAL
        self._empty_cycles = 0
        self._last_signal_time: Optional[float] = None
        self._last_state_change = time.time()
        self._contradiction = False
        self._contradiction_msg = ""

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_drought(self) -> bool:
        return self._state in (DroughtState.DROUGHT, DroughtState.CRITICAL)

    def record_empty_cycle(self, regime: str = "") -> str:
        """Record an empty scan cycle (no qualifying signals). Returns new state."""
        self._empty_cycles += 1
        old_state = self._state

        if self._empty_cycles >= CRITICAL_THRESHOLD:
            self._state = DroughtState.CRITICAL
        elif self._empty_cycles >= DROUGHT_THRESHOLD:
            self._state = DroughtState.DROUGHT
        elif self._empty_cycles >= WATCH_THRESHOLD:
            self._state = DroughtState.WATCH

        if self._state != old_state:
            self._last_state_change = time.time()
            logger.info("DROUGHT_TRANSITION: %s -> %s (empty_cycles=%d)", old_state, self._state, self._empty_cycles)

        # Check for regime contradiction
        from core.regime_mapping import detect_regime_contradiction
        is_contradiction, msg = detect_regime_contradiction(regime, self.is_drought)
        self._contradiction = is_contradiction
        self._contradiction_msg = msg

        self._write_artifact()
        return self._state

    def record_signal_sent(self) -> str:
        """Record that a qualifying signal passed all gates and was sent. Clears drought."""
        self._empty_cycles = 0
        old_state = self._state
        self._state = DroughtState.CLEARED if old_state != DroughtState.NORMAL else DroughtState.NORMAL
        self._last_signal_time = time.time()
        self._contradiction = False
        self._contradiction_msg = ""

        if old_state != self._state:
            self._last_state_change = time.time()
            logger.info("DROUGHT_CLEARED: %s -> %s", old_state, self._state)

        # Auto-transition CLEARED -> NORMAL after recording
        if self._state == DroughtState.CLEARED:
            self._state = DroughtState.NORMAL

        self._write_artifact()
        return self._state

    def get_snapshot(self) -> DroughtSnapshot:
        return DroughtSnapshot(
            state=self._state,
            empty_cycles=self._empty_cycles,
            last_signal_time=self._last_signal_time,
            last_state_change=self._last_state_change,
            contradiction_detected=self._contradiction,
            contradiction_msg=self._contradiction_msg,
        )

    def _write_artifact(self):
        """Write drought state to artifacts/drought.json."""
        try:
            ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)
            path = ARTIFACTS_ROOT / "drought.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.get_snapshot().to_dict(), indent=2))
            tmp.rename(path)
        except Exception as e:
            logger.warning("Failed to write drought artifact: %s", e)


# Module-level singleton
_drought_manager: Optional[DroughtManager] = None

def get_drought_manager() -> DroughtManager:
    global _drought_manager
    if _drought_manager is None:
        _drought_manager = DroughtManager()
    return _drought_manager
