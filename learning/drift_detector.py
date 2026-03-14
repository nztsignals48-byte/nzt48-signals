"""
Concept Drift Detector -- NZT-48 W12
Mouss et al. (2004): Page-Hinkley test for distribution shift detection.
Kirkpatrick et al. (2017): Exponential forgetting to downweight old regime data.
When drift detected: triggers exponential forgetting + Telegram alert.
"""

import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

STATE_FILE = "data/drift_detector_state.json"

# Page-Hinkley parameters
PH_DELTA = 0.005       # Minimum acceptable mean shift magnitude
PH_LAMBDA = 50.0       # Alert threshold (higher = less sensitive)
EWC_HALF_LIFE = 20     # Exponential forgetting half-life in trades


class DriftDetector:
    """
    Page-Hinkley test for win-rate distribution shift.

    Page-Hinkley test:
      m_T = sum(win_t - win_avg - delta)
      M_T = max(m_t for t <= T)
      Drift if M_T - m_T > lambda

    On drift: apply exponential forgetting to downweight stale data.
    """

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = self._load_state()
        self._cumsum = self.state.get("cumsum", 0.0)
        self._max_cumsum = self.state.get("max_cumsum", 0.0)
        self._n = self.state.get("n", 0)
        self._total_wins = self.state.get("total_wins", 0)
        self._drift_events = self.state.get("drift_events", [])

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "cumsum": 0.0, "max_cumsum": 0.0, "n": 0,
            "total_wins": 0, "drift_events": [],
        }

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self.state_file) if os.path.dirname(self.state_file) else ".", exist_ok=True)
        try:
            data = {
                "cumsum": self._cumsum,
                "max_cumsum": self._max_cumsum,
                "n": self._n,
                "total_wins": self._total_wins,
                "drift_events": self._drift_events[-20:],
            }
            with open(self.state_file, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.debug("DriftDetector: save failed: %s", e)

    def update(self, win: bool) -> bool:
        """
        Feed one trade result. Returns True if drift detected.
        Page-Hinkley: alerts when win_rate drops significantly below rolling mean.
        """
        self._n += 1
        if win:
            self._total_wins += 1

        win_avg = self._total_wins / self._n if self._n > 0 else 0.5
        obs = 1.0 if win else 0.0

        # PH cumulative sum
        self._cumsum += obs - win_avg - PH_DELTA
        if self._cumsum > self._max_cumsum:
            self._max_cumsum = self._cumsum

        drift = (self._max_cumsum - self._cumsum) > PH_LAMBDA

        if drift:
            event = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "n": self._n,
                "win_rate": round(win_avg, 3),
                "ph_score": round(self._max_cumsum - self._cumsum, 2),
            }
            self._drift_events.append(event)
            # Reset PH test after drift detected
            self._cumsum = 0.0
            self._max_cumsum = 0.0
            logger.warning(
                "DRIFT DETECTED: win_rate=%.3f n=%d PH=%.2f -- applying exponential forgetting",
                win_avg, self._n, event["ph_score"],
            )
            self._save_state()
            return True

        if self._n % 10 == 0:
            self._save_state()
        return False

    def get_sample_weights(self, outcomes: list) -> np.ndarray:
        """
        Exponential decay weights for LightGBM sample_weight param.
        w(age) = exp(-(age_in_trades / half_life))
        Most recent trade = weight 1.0; oldest = ~0.0
        """
        n = len(outcomes)
        if n == 0:
            return np.array([])
        ages = np.arange(n - 1, -1, -1)  # 0 = most recent
        weights = np.exp(-ages / EWC_HALF_LIFE)
        return weights / weights.sum() * n  # Normalize to sum to n (not 1)

    def recent_drift_count(self, last_n: int = 100) -> int:
        """Number of drift events in the last N trades."""
        return sum(
            1 for e in self._drift_events
            if self._n - e.get("n", 0) <= last_n
        )

    def get_status(self) -> dict:
        return {
            "n_trades": self._n,
            "current_win_rate": round(self._total_wins / self._n, 3) if self._n > 0 else None,
            "ph_score": round(self._max_cumsum - self._cumsum, 2),
            "drift_events_total": len(self._drift_events),
            "last_drift": self._drift_events[-1] if self._drift_events else None,
        }
