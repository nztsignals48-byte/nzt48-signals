"""
J-08: Parameter Drift Monitor -- Constitutional Bounds.
Track parameter drift: +/-15% from baseline -> DEFENSIVE mode.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class DriftStatus:
    parameter: str
    baseline: float
    current: float
    drift_pct: float
    is_breached: bool


class ParameterDriftMonitor:
    """Monitor trading parameters for drift beyond constitutional bounds.
    If any parameter drifts >15% from baseline, enter DEFENSIVE mode.
    """
    MAX_DRIFT_PCT = 15.0

    def __init__(self):
        self._baselines: Dict[str, float] = {}
        self._current: Dict[str, float] = {}
        self._is_defensive = False
        self._breaches: list = []

    def set_baseline(self, name: str, value: float) -> None:
        self._baselines[name] = value
        self._current[name] = value

    def update(self, name: str, value: float) -> DriftStatus:
        baseline = self._baselines.get(name)
        if baseline is None or baseline == 0:
            return DriftStatus(name, 0, value, 0, False)
        drift = abs(value / baseline - 1) * 100
        breached = drift > self.MAX_DRIFT_PCT
        status = DriftStatus(name, baseline, value, drift, breached)
        self._current[name] = value
        if breached:
            logger.warning(
                "PARAMETER DRIFT: %s = %.4f (baseline=%.4f, drift=%.1f%% > %.1f%%)",
                name, value, baseline, drift, self.MAX_DRIFT_PCT
            )
            if name not in [b[0] for b in self._breaches]:
                self._breaches.append((name, datetime.utcnow(), drift))
        return status

    def check_all(self) -> bool:
        any_breach = any(self.update(n, v).is_breached for n, v in self._current.items())
        if any_breach and not self._is_defensive:
            self._is_defensive = True
            logger.error("DEFENSIVE MODE ACTIVATED: parameter drift detected")
        return not any_breach

    @property
    def is_defensive(self) -> bool:
        return self._is_defensive

    def clear_defensive(self) -> None:
        self._is_defensive = False
        self._breaches.clear()
        logger.info("DEFENSIVE MODE cleared")
