"""Online Learning Engine with Drift Detection — Book 76.

Incremental parameter updates with concept drift detection.
Three drift detectors (DDM, ADWIN, Page-Hinkley) vote in an ensemble
to decide when the data-generating process has shifted.

When drift is confirmed (2/3 detectors agree), the engine proposes
parameter updates via Online Gradient Descent with safety constraints:
  - Max 10% parameter change per update
  - Max 3 updates per day
  - Auto-rollback if next 5 trades worsen performance

State persisted to /app/data/online_learning/{strategy_name}.json.

Bridge.py integration:
    from python_brain.ml.online_learning import OnlineLearningEngine, DriftType
    engine = OnlineLearningEngine("momentum_v2", initial_params)
    engine.on_trade_complete(trade_result)
    drift = engine.check_drift()
    if drift != DriftType.NONE:
        proposal = engine.propose_update()
        if proposal:
            engine.apply_update(proposal)

Usage:
    from python_brain.ml.online_learning import (
        OnlineLearningEngine, DriftDetectorDDM, DriftDetectorADWIN,
        EnsembleDriftDetector, OnlineGradientDescent, DriftType,
    )
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("online_learning")

__all__ = [
    "DriftType",
    "DriftDetectorDDM",
    "DriftDetectorADWIN",
    "EnsembleDriftDetector",
    "OnlineGradientDescent",
    "OnlineLearningEngine",
]

# ── Constants ──────────────────────────────────────────────────────────

MAX_PARAM_CHANGE_PCT = 0.10       # 10% max change per update
MAX_UPDATES_PER_DAY = 3
ROLLBACK_WINDOW = 5               # Trades to monitor after update
STATE_DIR = Path("/app/data/online_learning")

DDM_WARNING_LEVEL = 2.0           # mean + 2*std
DDM_DRIFT_LEVEL = 3.0             # mean + 3*std
ADWIN_DELTA = 0.002               # Hoeffding bound confidence
PAGE_HINKLEY_THRESHOLD = 50.0     # PH detection threshold
PAGE_HINKLEY_ALPHA = 0.005        # PH allowance for gradual change


# ── Drift Type ─────────────────────────────────────────────────────────

class DriftType(Enum):
    NONE = "none"
    SUDDEN = "sudden"
    GRADUAL = "gradual"
    RECURRING = "recurring"


# ── DDM Drift Detector ────────────────────────────────────────────────

class DriftDetectorDDM:
    """Drift Detection Method (Lu et al. 2004).

    Tracks error rate mean and standard deviation incrementally.
    WARNING when error > mean + 2*std, DRIFT when > mean + 3*std.
    """

    def __init__(self):
        self._n: int = 0
        self._p: float = 0.0      # running error rate
        self._s: float = 0.0      # running std = sqrt(p*(1-p)/n)
        self._p_min: float = float("inf")
        self._s_min: float = float("inf")
        self._in_warning: bool = False

    def update(self, prediction_correct: bool) -> DriftType:
        """Update with a new observation.

        Args:
            prediction_correct: True if the prediction was correct.

        Returns:
            DriftType indicating current state.
        """
        self._n += 1
        error = 0.0 if prediction_correct else 1.0

        # Incremental mean update
        self._p += (error - self._p) / self._n
        self._s = math.sqrt(self._p * (1.0 - self._p) / self._n) if self._n > 1 else 0.0

        # Track minimum error + std combination
        if self._p + self._s < self._p_min + self._s_min:
            self._p_min = self._p
            self._s_min = self._s

        # Need minimum observations before detecting
        if self._n < 30:
            return DriftType.NONE

        # Check drift conditions
        if self._s_min > 1e-10:
            if self._p + self._s > self._p_min + DDM_DRIFT_LEVEL * self._s_min:
                drift_type = DriftType.SUDDEN
                self.reset()
                return drift_type
            elif self._p + self._s > self._p_min + DDM_WARNING_LEVEL * self._s_min:
                self._in_warning = True
                return DriftType.GRADUAL

        self._in_warning = False
        return DriftType.NONE

    def reset(self) -> None:
        """Reset detector state after drift detected."""
        self._n = 0
        self._p = 0.0
        self._s = 0.0
        self._p_min = float("inf")
        self._s_min = float("inf")
        self._in_warning = False


# ── ADWIN Drift Detector ──────────────────────────────────────────────

class DriftDetectorADWIN:
    """Adaptive Windowing drift detector.

    Maintains a variable-length window that shrinks when a distribution
    change is detected via the Hoeffding bound.
    """

    def __init__(self, delta: float = ADWIN_DELTA, max_window: int = 2000):
        self._delta = delta
        self._max_window = max_window
        self._window: List[float] = []

    def update(self, value: float) -> DriftType:
        """Add a value and check for drift.

        Args:
            value: New observation (e.g. error, return, metric).

        Returns:
            DriftType.SUDDEN if distribution change detected, else NONE.
        """
        self._window.append(value)

        # Cap window size
        if len(self._window) > self._max_window:
            self._window = self._window[-self._max_window:]

        if len(self._window) < 10:
            return DriftType.NONE

        if self._check_split():
            return DriftType.SUDDEN

        return DriftType.NONE

    def _check_split(self) -> bool:
        """Test all possible split points using Hoeffding bound.

        Returns True if any split point shows significant distribution change.
        """
        n = len(self._window)
        if n < 10:
            return False

        arr = np.array(self._window)
        total_sum = np.sum(arr)

        # Test split points (subsample for efficiency)
        step = max(1, n // 50)
        running_sum = 0.0

        for i in range(step, n - step, step):
            running_sum = float(np.sum(arr[:i]))
            n0 = i
            n1 = n - i

            if n0 < 5 or n1 < 5:
                continue

            mean0 = running_sum / n0
            mean1 = (total_sum - running_sum) / n1

            # Hoeffding bound
            m = 1.0 / (1.0 / n0 + 1.0 / n1)
            epsilon = math.sqrt(math.log(4.0 / self._delta) / (2.0 * m))

            if abs(mean0 - mean1) >= epsilon:
                # Drift detected — shrink window to the more recent half
                self._window = self._window[i:]
                return True

        return False


# ── Page-Hinkley Drift Detector ───────────────────────────────────────

class _PageHinkleyDetector:
    """Page-Hinkley test for detecting mean shifts.

    Tracks cumulative deviation from running mean. Signals when
    the cumulative sum exceeds a threshold.
    """

    def __init__(self, threshold: float = PAGE_HINKLEY_THRESHOLD,
                 alpha: float = PAGE_HINKLEY_ALPHA):
        self._threshold = threshold
        self._alpha = alpha
        self._n: int = 0
        self._mean: float = 0.0
        self._sum: float = 0.0
        self._sum_min: float = float("inf")

    def update(self, value: float) -> DriftType:
        self._n += 1
        self._mean += (value - self._mean) / self._n
        self._sum += value - self._mean - self._alpha
        self._sum_min = min(self._sum_min, self._sum)

        if self._n < 30:
            return DriftType.NONE

        if self._sum - self._sum_min > self._threshold:
            self.reset()
            return DriftType.SUDDEN

        return DriftType.NONE

    def reset(self) -> None:
        self._n = 0
        self._mean = 0.0
        self._sum = 0.0
        self._sum_min = float("inf")


# ── Ensemble Drift Detector ───────────────────────────────────────────

class EnsembleDriftDetector:
    """Combines DDM + ADWIN + Page-Hinkley via majority voting.

    Requires 2/3 detectors to agree for a DRIFT signal.
    Returns (DriftType, confidence) where confidence is the fraction
    of detectors that fired.
    """

    def __init__(self):
        self._ddm = DriftDetectorDDM()
        self._adwin = DriftDetectorADWIN()
        self._ph = _PageHinkleyDetector()
        self._history: List[DriftType] = []

    def update(self, value: float) -> Tuple[DriftType, float]:
        """Update all detectors with a new value.

        For DDM, value is converted to a boolean (correct if value >= 0).

        Args:
            value: Observation (positive = correct/good, negative = error/bad).

        Returns:
            Tuple of (DriftType, confidence in [0, 1]).
        """
        ddm_result = self._ddm.update(value >= 0)
        adwin_result = self._adwin.update(value)
        ph_result = self._ph.update(value)

        votes = [ddm_result, adwin_result, ph_result]
        drift_votes = sum(1 for v in votes if v != DriftType.NONE)
        confidence = drift_votes / 3.0

        if drift_votes >= 2:
            # Majority says drift — classify type
            sudden_count = sum(1 for v in votes if v == DriftType.SUDDEN)
            gradual_count = sum(1 for v in votes if v == DriftType.GRADUAL)

            if sudden_count >= gradual_count:
                result = DriftType.SUDDEN
            else:
                result = DriftType.GRADUAL

            # Check for recurring pattern
            self._history.append(result)
            if len(self._history) >= 3 and all(
                h != DriftType.NONE for h in self._history[-3:]
            ):
                result = DriftType.RECURRING

            return result, confidence

        self._history.append(DriftType.NONE)
        # Keep history bounded
        if len(self._history) > 100:
            self._history = self._history[-50:]

        return DriftType.NONE, confidence


# ── Online Gradient Descent ────────────────────────────────────────────

class OnlineGradientDescent:
    """Online gradient descent with safety-constrained updates.

    Each update step clips the parameter change to max_change_pct of the
    current value, preventing wild swings from noisy gradients.
    """

    def __init__(self, params: Dict[str, float], learning_rate: float = 0.01,
                 decay: float = 0.999):
        self._params = dict(params)
        self._lr = learning_rate
        self._decay = decay
        self._step: int = 0

    @property
    def params(self) -> Dict[str, float]:
        return dict(self._params)

    def update(self, gradient: Dict[str, float]) -> Dict[str, float]:
        """Apply a single gradient step with safety clipping.

        Args:
            gradient: Parameter name -> gradient value.

        Returns:
            Updated parameter dictionary.
        """
        self._step += 1
        effective_lr = self._lr * (self._decay ** self._step)

        for name, grad in gradient.items():
            if name not in self._params:
                continue

            delta = -effective_lr * grad
            delta = self._clip_update(name, delta, MAX_PARAM_CHANGE_PCT)
            self._params[name] += delta

        return dict(self._params)

    def _clip_update(self, name: str, delta: float,
                     max_change_pct: float) -> float:
        """Clip update to max percentage of current parameter value.

        Args:
            name: Parameter name.
            delta: Proposed change.
            max_change_pct: Maximum allowed change as fraction of current value.

        Returns:
            Clipped delta.
        """
        current = self._params.get(name, 0.0)
        if abs(current) < 1e-10:
            # Near-zero parameter: use absolute clip
            max_abs = max_change_pct
        else:
            max_abs = abs(current) * max_change_pct

        return float(np.clip(delta, -max_abs, max_abs))


# ── Online Learning Engine ─────────────────────────────────────────────

class OnlineLearningEngine:
    """Main orchestrator for online learning with drift detection.

    Tracks trade outcomes, detects concept drift, proposes safe parameter
    updates, and supports rollback if performance degrades.

    Safety constraints:
      - Max 10% parameter change per update
      - Max 3 updates per day
      - Auto-rollback if next 5 trades are worse than pre-update
    """

    def __init__(self, strategy_name: str, initial_params: Dict[str, float]):
        self._strategy = strategy_name
        self._params = dict(initial_params)
        self._prev_params: Optional[Dict[str, float]] = None

        self._drift_detector = EnsembleDriftDetector()
        self._ogd = OnlineGradientDescent(initial_params)

        self._trade_results: List[Dict] = []
        self._post_update_trades: List[Dict] = []
        self._pre_update_mean_pnl: float = 0.0

        self._updates_today: int = 0
        self._last_update_date: Optional[str] = None
        self._last_drift: DriftType = DriftType.NONE

        # Load persisted state
        self._state_path = STATE_DIR / f"{strategy_name}.json"
        self._load_state()

    def on_trade_complete(self, trade_result: Dict) -> None:
        """Process a completed trade.

        Args:
            trade_result: Dict with at least 'pnl' and optionally
                          'predicted_direction', 'actual_direction'.
        """
        self._trade_results.append(trade_result)

        # Feed drift detector with trade PnL
        pnl = trade_result.get("pnl", 0.0)
        self._drift_detector.update(pnl)

        # Track post-update performance for rollback check
        if self._prev_params is not None:
            self._post_update_trades.append(trade_result)
            if len(self._post_update_trades) >= ROLLBACK_WINDOW:
                self._check_rollback()

        # Persist state periodically
        if len(self._trade_results) % 10 == 0:
            self._save_state()

    def check_drift(self) -> DriftType:
        """Run ensemble drift detection on recent trade data.

        Returns:
            The detected drift type.
        """
        if len(self._trade_results) < 30:
            return DriftType.NONE

        # Use recent window for focused detection
        recent = self._trade_results[-50:]
        pnl_values = [t.get("pnl", 0.0) for t in recent]

        drift_type = DriftType.NONE
        confidence = 0.0

        for val in pnl_values[-10:]:
            drift_type, confidence = self._drift_detector.update(val)

        self._last_drift = drift_type
        if drift_type != DriftType.NONE:
            log.info("Drift detected for %s: %s (conf=%.2f)",
                     self._strategy, drift_type.value, confidence)

        return drift_type

    def propose_update(self) -> Optional[Dict[str, float]]:
        """Propose parameter updates based on recent performance.

        Subject to daily update limits and parameter change constraints.

        Returns:
            New parameter dict if update proposed, None otherwise.
        """
        # Check daily update limit
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_update_date != today:
            self._updates_today = 0
            self._last_update_date = today

        if self._updates_today >= MAX_UPDATES_PER_DAY:
            log.info("Daily update limit reached for %s (%d/%d)",
                     self._strategy, self._updates_today, MAX_UPDATES_PER_DAY)
            return None

        if len(self._trade_results) < 30:
            return None

        # Compute gradient from recent trades
        gradient = self._estimate_gradient()
        if gradient is None:
            return None

        # Get updated params from OGD
        proposed = self._ogd.update(gradient)

        # Validate all changes are within bounds
        for name, new_val in proposed.items():
            old_val = self._params.get(name, 0.0)
            if abs(old_val) > 1e-10:
                change_pct = abs(new_val - old_val) / abs(old_val)
                if change_pct > MAX_PARAM_CHANGE_PCT:
                    log.warning("Proposed change for %s.%s exceeds limit: %.1f%%",
                                self._strategy, name, change_pct * 100)
                    # Clip to limit
                    direction = 1.0 if new_val > old_val else -1.0
                    proposed[name] = old_val * (1.0 + direction * MAX_PARAM_CHANGE_PCT)

        log.info("Proposed update for %s: %s", self._strategy, proposed)
        return proposed

    def apply_update(self, params: Dict[str, float]) -> None:
        """Apply approved parameter update.

        Args:
            params: New parameter values.
        """
        self._prev_params = dict(self._params)
        self._pre_update_mean_pnl = self._recent_mean_pnl()
        self._post_update_trades = []
        self._params = dict(params)
        self._updates_today += 1

        log.info("Applied update for %s (update %d/%d today)",
                 self._strategy, self._updates_today, MAX_UPDATES_PER_DAY)
        self._save_state()

    def rollback(self) -> None:
        """Revert to previous parameters."""
        if self._prev_params is None:
            log.warning("No previous params to rollback to for %s", self._strategy)
            return

        log.info("Rolling back %s to previous params", self._strategy)
        self._params = dict(self._prev_params)
        self._ogd = OnlineGradientDescent(self._params)
        self._prev_params = None
        self._post_update_trades = []
        self._save_state()

    @property
    def params(self) -> Dict[str, float]:
        return dict(self._params)

    @property
    def last_drift(self) -> DriftType:
        return self._last_drift

    # ── Private Methods ────────────────────────────────────────────────

    def _estimate_gradient(self) -> Optional[Dict[str, float]]:
        """Estimate parameter gradients from recent trade outcomes.

        Uses finite-difference approximation: parameters that correlate
        with negative PnL get positive gradient (should decrease).
        """
        recent = self._trade_results[-50:]
        if len(recent) < 20:
            return None

        pnl_values = np.array([t.get("pnl", 0.0) for t in recent])
        mean_pnl = np.mean(pnl_values)

        gradient = {}
        for name, val in self._params.items():
            # Simple heuristic: if PnL is negative, nudge params
            # toward reducing exposure; if positive, maintain
            if mean_pnl < 0:
                # Shrink parameter proportional to loss severity
                gradient[name] = -mean_pnl / max(abs(val), 1e-6)
            else:
                # Small gradient toward current direction
                gradient[name] = -0.01 * np.sign(val)

        return gradient

    def _check_rollback(self) -> None:
        """Check if post-update performance warrants rollback."""
        if self._prev_params is None or len(self._post_update_trades) < ROLLBACK_WINDOW:
            return

        post_pnl = np.mean([t.get("pnl", 0.0) for t in self._post_update_trades])

        if post_pnl < self._pre_update_mean_pnl:
            log.warning(
                "Post-update PnL (%.4f) worse than pre-update (%.4f) for %s — rolling back",
                post_pnl, self._pre_update_mean_pnl, self._strategy,
            )
            self.rollback()
        else:
            # Update accepted — clear rollback state
            log.info("Post-update performance OK for %s (%.4f vs %.4f)",
                     self._strategy, post_pnl, self._pre_update_mean_pnl)
            self._prev_params = None
            self._post_update_trades = []

    def _recent_mean_pnl(self, window: int = 20) -> float:
        """Compute mean PnL over recent trades."""
        recent = self._trade_results[-window:]
        if not recent:
            return 0.0
        return float(np.mean([t.get("pnl", 0.0) for t in recent]))

    def _save_state(self) -> None:
        """Persist engine state to disk."""
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            state = {
                "strategy": self._strategy,
                "params": self._params,
                "prev_params": self._prev_params,
                "updates_today": self._updates_today,
                "last_update_date": self._last_update_date,
                "last_drift": self._last_drift.value,
                "n_trades": len(self._trade_results),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with open(str(self._state_path), "w") as f:
                json.dump(state, f, indent=2, default=str)
        except Exception as e:
            log.warning("Failed to save online learning state for %s: %s",
                        self._strategy, e)

    def _load_state(self) -> None:
        """Load persisted state from disk."""
        if not self._state_path.exists():
            return

        try:
            with open(str(self._state_path), "r") as f:
                state = json.load(f)

            if state.get("params"):
                self._params = state["params"]
                self._ogd = OnlineGradientDescent(self._params)
            self._prev_params = state.get("prev_params")
            self._updates_today = state.get("updates_today", 0)
            self._last_update_date = state.get("last_update_date")
            drift_str = state.get("last_drift", "none")
            try:
                self._last_drift = DriftType(drift_str)
            except ValueError:
                self._last_drift = DriftType.NONE

            log.info("Loaded online learning state for %s (%d trades historic)",
                     self._strategy, state.get("n_trades", 0))
        except Exception as e:
            log.warning("Failed to load online learning state for %s: %s",
                        self._strategy, e)
