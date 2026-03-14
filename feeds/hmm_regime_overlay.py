"""
NZT-48 Sprint 2 — HMM Regime Overlay (#18)
Research: Ang & Bekaert (2002); Nystrup, Madsen & Lindstrom (2020)

Provides a probabilistic regime detection layer using a Gaussian Hidden
Markov Model.  This overlays the existing rule-based 8-state regime
classifier with soft posterior probabilities.

Four HMM states map to the NZT-48 regime taxonomy:
  0 = bull   ->  TRENDING_UP_STRONG / TRENDING_UP_MOD
  1 = bear   ->  TRENDING_DOWN_STRONG / TRENDING_DOWN_MOD
  2 = high_vol -> HIGH_VOLATILITY / SHOCK / RISK_OFF
  3 = transition -> RANGE_BOUND

The overlay produces a -0.2 to +0.2 confidence adjustment based on
agreement or disagreement between the HMM posterior and the rule-based
regime classification.

Cold-start behaviour: returns neutral (0.0) adjustments and uniform
state probabilities until the HMM has been fitted on sufficient data.
"""
from __future__ import annotations

import logging
import os
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.feeds.hmm_regime")

# ---------------------------------------------------------------------------
# Optional heavy import — gracefully degrade if hmmlearn not installed
# ---------------------------------------------------------------------------
try:
    from hmmlearn.hmm import GaussianHMM
    _HAS_HMM = True
except ImportError:
    GaussianHMM = None  # type: ignore[misc,assignment]
    _HAS_HMM = False
    logger.warning("hmmlearn not installed — HMMRegimeOverlay will return neutral adjustments")

try:
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    pd = None  # type: ignore[assignment]
    _HAS_PANDAS = False


class HMMRegimeOverlay:
    """Gaussian HMM providing probabilistic regime detection.

    Trained on three observables (daily returns, 20d rolling volatility,
    volume ratio vs 20d average).  Produces posterior state probabilities
    that serve as a soft overlay on the rule-based 8-state classifier.

    The overlay NEVER replaces the rule-based classifier — it only
    provides a confidence adjustment within [-0.2, +0.2].
    """

    # Number of hidden states
    N_STATES: int = 4

    # Human-readable labels for each HMM state index
    STATE_LABELS: list[str] = ["bull", "bear", "high_vol", "transition"]

    # How many daily observations are needed before training can proceed
    MIN_OBSERVATIONS: int = 120  # ~6 months of trading days

    # Rolling window length for training (trading days)
    TRAINING_WINDOW: int = 504  # ~2 years

    # Mapping from HMM state labels to NZT-48 RegimeState values
    _HMM_TO_NZT: dict[str, list[str]] = {
        "bull": ["TRENDING_UP_STRONG", "TRENDING_UP_MOD"],
        "bear": ["TRENDING_DOWN_STRONG", "TRENDING_DOWN_MOD"],
        "high_vol": ["HIGH_VOLATILITY", "SHOCK", "RISK_OFF"],
        "transition": ["RANGE_BOUND"],
    }

    # Reverse: NZT-48 regime -> best-matching HMM label
    _NZT_TO_HMM: dict[str, str] = {}
    for _label, _regimes in _HMM_TO_NZT.items():
        for _r in _regimes:
            _NZT_TO_HMM[_r] = _label

    def __init__(self) -> None:
        self._model: Optional[object] = None  # GaussianHMM once fitted
        self._is_fitted: bool = False
        self._last_fit_time: Optional[datetime] = None
        self._last_obs: Optional[np.ndarray] = None  # last observation for scoring

        # State ordering may differ after each fit — these maps translate
        # the model's internal state indices to our canonical labels.
        self._state_index_to_label: dict[int, str] = {}

        logger.info(
            "HMMRegimeOverlay initialised (hmmlearn available=%s, n_states=%d)",
            _HAS_HMM,
            self.N_STATES,
        )

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def fit(
        self,
        returns: np.ndarray,
        volatility: np.ndarray,
        volume_ratio: np.ndarray,
    ) -> None:
        """Train the HMM on historical daily data.

        Args:
            returns: 1-D array of daily log returns (or simple returns).
            volatility: 1-D array of rolling 20-day standard deviation.
            volume_ratio: 1-D array of daily volume / 20-day avg volume.

        All three arrays must be the same length.  Training uses up to the
        last ``TRAINING_WINDOW`` observations (expanding window capped at
        2 years of data).
        """
        if not _HAS_HMM:
            logger.warning("Cannot fit — hmmlearn not installed")
            return

        try:
            returns = np.asarray(returns, dtype=np.float64).ravel()
            volatility = np.asarray(volatility, dtype=np.float64).ravel()
            volume_ratio = np.asarray(volume_ratio, dtype=np.float64).ravel()

            n = min(len(returns), len(volatility), len(volume_ratio))
            if n < self.MIN_OBSERVATIONS:
                logger.info(
                    "Only %d observations (need %d) — skipping HMM fit",
                    n, self.MIN_OBSERVATIONS,
                )
                return

            # Trim to training window
            n = min(n, self.TRAINING_WINDOW)
            X = np.column_stack([
                returns[-n:],
                volatility[-n:],
                volume_ratio[-n:],
            ])

            # Replace NaN / Inf with 0 to prevent training failures
            X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

            model = GaussianHMM(
                n_components=self.N_STATES,
                covariance_type="full",
                n_iter=200,
                random_state=42,
                verbose=False,
            )
            model.fit(X)

            self._model = model
            self._is_fitted = True
            self._last_fit_time = datetime.now(timezone.utc)
            self._last_obs = X[-1:].copy()

            # Label assignment: map internal state indices to canonical labels
            # Heuristic: bull has highest mean return, bear lowest,
            # high_vol has highest mean volatility, transition is the remainder.
            self._assign_state_labels(model)

            logger.info(
                "HMM fitted on %d observations. State means (returns): %s",
                n,
                {
                    self._state_index_to_label.get(i, f"s{i}"): f"{model.means_[i][0]:.6f}"
                    for i in range(self.N_STATES)
                },
            )
        except Exception:
            logger.exception("HMM fit failed — overlay remains inactive")

    def _assign_state_labels(self, model: object) -> None:
        """Assign canonical labels to HMM states by inspecting fitted means.

        Strategy:
          1. State with highest mean daily return -> bull
          2. State with lowest mean daily return -> bear
          3. Of the remaining two, highest mean volatility -> high_vol
          4. The last one -> transition
        """
        means = model.means_  # type: ignore[union-attr]  # (n_states, 3)
        return_means = means[:, 0]
        vol_means = means[:, 1]

        assigned: dict[int, str] = {}
        remaining = set(range(self.N_STATES))

        # Bull: highest return
        bull_idx = int(np.argmax(return_means))
        assigned[bull_idx] = "bull"
        remaining.discard(bull_idx)

        # Bear: lowest return
        bear_idx = int(np.argmin(return_means))
        if bear_idx in remaining:
            assigned[bear_idx] = "bear"
            remaining.discard(bear_idx)
        else:
            # Edge case: same index as bull (degenerate) — pick next lowest
            sorted_idx = np.argsort(return_means)
            for idx in sorted_idx:
                if idx in remaining:
                    assigned[int(idx)] = "bear"
                    remaining.discard(int(idx))
                    break

        # Of remaining states, highest vol -> high_vol
        remaining_list = list(remaining)
        if len(remaining_list) >= 2:
            vol_vals = [(i, vol_means[i]) for i in remaining_list]
            vol_vals.sort(key=lambda x: x[1], reverse=True)
            assigned[vol_vals[0][0]] = "high_vol"
            assigned[vol_vals[1][0]] = "transition"
        elif len(remaining_list) == 1:
            assigned[remaining_list[0]] = "high_vol"

        self._state_index_to_label = assigned

    def retrain(self, data) -> None:
        """Retrain the HMM weekly using an expanding window.

        Args:
            data: pandas DataFrame with columns ``returns``, ``volatility``,
                  ``volume_ratio``.  Or a dict with those keys mapping to
                  array-like sequences.
        """
        try:
            if _HAS_PANDAS and hasattr(data, "columns"):
                returns = data["returns"].values
                volatility = data["volatility"].values
                volume_ratio = data["volume_ratio"].values
            elif isinstance(data, dict):
                returns = np.asarray(data["returns"])
                volatility = np.asarray(data["volatility"])
                volume_ratio = np.asarray(data["volume_ratio"])
            else:
                logger.warning("retrain: unsupported data type %s", type(data))
                return

            self.fit(returns, volatility, volume_ratio)
        except Exception:
            logger.exception("retrain failed")

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def get_state_probabilities(
        self,
        returns: float,
        volatility: float,
        volume_ratio: float,
    ) -> dict[str, float]:
        """Return posterior probabilities for each HMM state.

        Args:
            returns: Most recent daily return.
            volatility: Most recent 20-day rolling standard deviation.
            volume_ratio: Most recent daily volume / 20-day avg volume.

        Returns:
            Dict like ``{"bull": 0.7, "bear": 0.1, "high_vol": 0.1,
            "transition": 0.1}``.  Probabilities sum to 1.0.

        Returns uniform distribution when the model is not fitted.
        """
        uniform = {label: 1.0 / self.N_STATES for label in self.STATE_LABELS}

        if not self._is_fitted or self._model is None or not _HAS_HMM:
            return uniform

        try:
            obs = np.array([[
                float(returns),
                float(volatility),
                float(volume_ratio),
            ]])
            obs = np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0)

            # Use score_samples for log-likelihood, but we need posteriors.
            # hmmlearn's predict_proba returns posteriors given the full sequence.
            # For a single observation, we append it to the last known obs.
            if self._last_obs is not None:
                seq = np.vstack([self._last_obs, obs])
            else:
                seq = obs

            posteriors = self._model.predict_proba(seq)  # type: ignore[union-attr]
            # Take the last row (the new observation)
            probs = posteriors[-1]

            # Map internal indices to canonical labels
            result: dict[str, float] = {}
            for idx in range(self.N_STATES):
                label = self._state_index_to_label.get(idx, self.STATE_LABELS[idx])
                result[label] = float(probs[idx])

            # Ensure all labels present (paranoia)
            for label in self.STATE_LABELS:
                if label not in result:
                    result[label] = 0.0

            # Normalise to sum to 1 (should already be, but safety)
            total = sum(result.values())
            if total > 0 and abs(total - 1.0) > 1e-6:
                result = {k: v / total for k, v in result.items()}

            # Cache last observation for next call
            self._last_obs = obs.copy()

            return result

        except Exception:
            logger.exception("get_state_probabilities failed — returning uniform")
            return uniform

    def get_regime_confidence_adjustment(
        self,
        rule_based_regime: str,
        hmm_probs: dict[str, float],
    ) -> float:
        """Compute a confidence adjustment in [-0.2, +0.2].

        - If HMM agrees with rule-based regime: +0.1 to +0.2
        - If HMM disagrees: -0.1 to -0.2
        - Neutral / insufficient confidence: 0.0

        The magnitude scales with the HMM's conviction (posterior
        probability of the agreeing/disagreeing state).
        """
        if not self._is_fitted or not hmm_probs:
            return 0.0

        try:
            regime_upper = str(rule_based_regime).upper()
            expected_hmm_label = self._NZT_TO_HMM.get(regime_upper)

            if expected_hmm_label is None:
                # Unknown regime — return neutral
                return 0.0

            # Probability the HMM assigns to the state that matches rule-based
            agree_prob = hmm_probs.get(expected_hmm_label, 0.0)

            # Determine the highest-probability HMM state
            dominant_label = max(hmm_probs, key=hmm_probs.get)  # type: ignore[arg-type]
            dominant_prob = hmm_probs[dominant_label]

            if dominant_label == expected_hmm_label:
                # Agreement — scale +0.1 to +0.2 based on conviction
                # agree_prob in [0.5, 1.0] maps to [+0.1, +0.2]
                if agree_prob >= 0.5:
                    adjustment = 0.1 + 0.1 * min((agree_prob - 0.5) / 0.5, 1.0)
                else:
                    # Weak agreement — still positive but small
                    adjustment = 0.1 * (agree_prob / 0.5)
                return min(adjustment, 0.2)
            else:
                # Disagreement — the HMM's dominant state differs from rule-based
                # Scale -0.1 to -0.2 based on how strongly HMM disagrees
                if dominant_prob >= 0.5:
                    adjustment = -0.1 - 0.1 * min((dominant_prob - 0.5) / 0.5, 1.0)
                else:
                    # Mild disagreement
                    adjustment = -0.1 * (dominant_prob / 0.5)
                return max(adjustment, -0.2)

        except Exception:
            logger.exception("get_regime_confidence_adjustment failed — returning 0.0")
            return 0.0

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_state(self, path: str) -> None:
        """Persist the fitted HMM and metadata via pickle."""
        if not self._is_fitted or self._model is None:
            logger.warning("Cannot save — model not fitted")
            return
        try:
            state = {
                "model": self._model,
                "is_fitted": self._is_fitted,
                "last_fit_time": self._last_fit_time,
                "state_index_to_label": self._state_index_to_label,
                "last_obs": self._last_obs,
            }
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as f:
                pickle.dump(state, f)
            logger.info("HMMRegimeOverlay saved to %s", path)
        except Exception:
            logger.exception("Failed to save HMMRegimeOverlay to %s", path)

    def load_state(self, path: str) -> None:
        """Load a previously saved HMM from disk."""
        if not os.path.isfile(path):
            logger.warning("State file not found: %s", path)
            return
        try:
            with open(path, "rb") as f:
                state = pickle.load(f)
            self._model = state["model"]
            self._is_fitted = state.get("is_fitted", True)
            self._last_fit_time = state.get("last_fit_time")
            self._state_index_to_label = state.get("state_index_to_label", {})
            self._last_obs = state.get("last_obs")
            logger.info(
                "HMMRegimeOverlay loaded from %s (fitted=%s, last_fit=%s)",
                path,
                self._is_fitted,
                self._last_fit_time,
            )
        except Exception:
            logger.exception("Failed to load HMMRegimeOverlay from %s", path)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """True when the HMM is fitted and ready for inference."""
        return self._is_fitted and _HAS_HMM and self._model is not None

    def __repr__(self) -> str:
        return (
            f"HMMRegimeOverlay(active={self.is_active}, "
            f"n_states={self.N_STATES}, "
            f"last_fit={self._last_fit_time})"
        )
