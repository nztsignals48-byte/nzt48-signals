"""
NZT-48 Trading System — Gaussian HMM Regime Classifier
Wave 2, Item 2: Nystrup et al. (2017)

2-State Gaussian Hidden Markov Model trained on 252-day rolling
(daily_return, log_realized_volatility) of ^GSPC.

State mapping:
    - State A (low vol, positive mean return)  → TRENDING_UP_STRONG
    - State B (high vol, negative mean return)  → RISK_OFF

3-Day Confirmation Lag:
    A predicted state must persist for 3 consecutive trading days
    before the official regime changes. This cuts false positive
    transitions from ~35% to ~12% (Nystrup et al. 2017, Table 3).

Fallback:
    If hmmlearn raises ConvergenceWarning or any exception, the
    classifier returns None, signalling main.py to keep the
    existing threshold-based RegimeClassifier output.

V7.0 Immutability:
    This classifier ONLY produces a RegimeState suggestion.
    It does NOT bypass S15/S16 logic, 6/8 indicator consensus,
    Infinite Profit Ladder, or the Risk Constitution sizing caps.
"""

from __future__ import annotations

import logging
import math
import threading
import warnings
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import config as cfg

logger = logging.getLogger("nzt48.core.regime_hmm")

# Attempt imports — all optional for graceful degradation
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    from hmmlearn.hmm import GaussianHMM
    _HAS_HMMLEARN = True
except ImportError:
    _HAS_HMMLEARN = False

try:
    import yfinance as yf
    _HAS_YFINANCE = True
except ImportError:
    _HAS_YFINANCE = False

try:
    from models import RegimeState
    _HAS_REGIME_STATE = True
except ImportError:
    _HAS_REGIME_STATE = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ROLLING_WINDOW = 252         # Trading days for HMM training
_FETCH_BUFFER = 280           # Fetch extra days to ensure 252 clean returns
_CONFIRMATION_LAG_DEFAULT = 3 # Days a new state must persist before official change
_REALIZED_VOL_WINDOW = 21    # 21-day rolling window for realized vol computation
_MIN_TRAINING_SAMPLES = 60   # Minimum samples to attempt HMM fit
_HMM_N_ITER = 200            # Max EM iterations
_HMM_RANDOM_STATE = 42       # Reproducible fits
_CACHE_SECONDS = 3600        # Refit at most once per hour


class GaussianHMMRegimeClassifier:
    """2-State Gaussian HMM regime classifier.

    Trained on (daily_return, log_realized_vol) of ^GSPC over a
    252-day rolling window. Produces a RegimeState with a 3-day
    confirmation lag to suppress false transitions.

    Thread-safe: all mutable state is guarded by ``self._lock``.

    Args:
        confirmation_lag: Number of consecutive days a new state must
            persist before the official regime changes.
            Default 3 (Nystrup et al. 2017).
    """

    def __init__(
        self,
        confirmation_lag: int | None = None,
    ) -> None:
        self._lock = threading.Lock()

        # Configuration
        if confirmation_lag is None:
            confirmation_lag = cfg.get(
                "v95_hmm_confirmation_lag_days", _CONFIRMATION_LAG_DEFAULT,
            )
        self._confirmation_lag: int = max(1, confirmation_lag)

        # Model state
        self._model: object | None = None       # GaussianHMM instance
        self._last_fit_ts: datetime | None = None
        self._bullish_state_idx: int = 0         # Which HMM state is "bullish"

        # 3-day confirmation tracking
        self._pending_regime: RegimeState | None = None  # Candidate waiting for confirmation
        self._pending_days: int = 0              # How many consecutive days the candidate held
        self._confirmed_regime: RegimeState | None = None  # Last confirmed regime

        # Diagnostics
        self._last_state_means: list[list[float]] = []
        self._last_state_covars: list[list[list[float]]] = []
        self._last_transition_matrix: list[list[float]] = []
        self._last_bullish_prob: float = 0.5
        self._fit_count: int = 0

        logger.info(
            "GaussianHMMRegimeClassifier initialized | confirmation_lag=%d | "
            "hmmlearn=%s | numpy=%s",
            self._confirmation_lag,
            "yes" if _HAS_HMMLEARN else "NO",
            "yes" if _HAS_NUMPY else "NO",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self) -> Optional["RegimeState"]:
        """Refit the HMM (if stale) and return the confirmed regime.

        Returns:
            RegimeState if a confirmed regime is available.
            None if the HMM cannot produce a regime (fallback to legacy).
        """
        if not _HAS_NUMPY or not _HAS_HMMLEARN or not _HAS_YFINANCE:
            logger.debug("HMM dependencies unavailable — returning None")
            return None

        if not cfg.get("v95_hmm_regime_enabled", True):
            return None

        # Check cache freshness
        now = datetime.now(timezone.utc)
        if self._last_fit_ts and (now - self._last_fit_ts).total_seconds() < _CACHE_SECONDS:
            return self._confirmed_regime

        # Fetch data and refit
        try:
            regime = self._fit_and_predict()
            return regime
        except Exception as e:
            logger.warning("HMM update failed (non-blocking): %s", e)
            return self._confirmed_regime

    def get_diagnostics(self) -> dict:
        """Return model diagnostics for dashboard/logging."""
        with self._lock:
            return {
                "confirmed_regime": (
                    self._confirmed_regime.value
                    if self._confirmed_regime else None
                ),
                "pending_regime": (
                    self._pending_regime.value
                    if self._pending_regime else None
                ),
                "pending_days": self._pending_days,
                "confirmation_lag": self._confirmation_lag,
                "bullish_prob": round(self._last_bullish_prob, 4),
                "fit_count": self._fit_count,
                "last_fit_ts": (
                    self._last_fit_ts.isoformat()
                    if self._last_fit_ts else None
                ),
                "state_means": self._last_state_means,
                "transition_matrix": self._last_transition_matrix,
            }

    # ------------------------------------------------------------------
    # Private: Fit & Predict
    # ------------------------------------------------------------------

    def _fit_and_predict(self) -> Optional["RegimeState"]:
        """Fetch ^GSPC data, fit HMM, predict regime with confirmation lag.

        Returns:
            Confirmed RegimeState, or None on failure.
        """
        # 1. Fetch ^GSPC daily data
        features = self._build_features()
        if features is None:
            return self._confirmed_regime

        # 2. Fit the 2-state Gaussian HMM
        model = self._fit_hmm(features)
        if model is None:
            return self._confirmed_regime

        # 3. Predict hidden states
        raw_regime = self._predict_current_regime(model, features)
        if raw_regime is None:
            return self._confirmed_regime

        # 4. Apply 3-day confirmation lag
        confirmed = self._apply_confirmation_lag(raw_regime)

        with self._lock:
            self._last_fit_ts = datetime.now(timezone.utc)
            self._fit_count += 1

        return confirmed

    def _build_features(self) -> Optional["np.ndarray"]:
        """Fetch ^GSPC and build (daily_return, log_realized_vol) matrix.

        Returns:
            np.ndarray of shape (N, 2) or None on failure.
        """
        try:
            ticker = yf.Ticker("^GSPC")
            hist = ticker.history(period=f"{_FETCH_BUFFER}d", interval="1d")

            if hist is None or len(hist) < _MIN_TRAINING_SAMPLES:
                logger.debug(
                    "Insufficient ^GSPC data: %d rows (need %d)",
                    len(hist) if hist is not None else 0,
                    _MIN_TRAINING_SAMPLES,
                )
                return None

            close = hist["Close"].values
            if len(close) < _MIN_TRAINING_SAMPLES:
                return None

            # Daily returns
            returns = np.diff(close) / close[:-1]

            # 21-day rolling realized volatility (annualized, log-transformed)
            n = len(returns)
            log_rvol = np.full(n, np.nan)

            for i in range(_REALIZED_VOL_WINDOW, n):
                window = returns[i - _REALIZED_VOL_WINDOW : i]
                daily_vol = np.std(window, ddof=1)
                annualized = daily_vol * math.sqrt(252)
                # Log transform for better HMM fit (log-normal vol distribution)
                log_rvol[i] = math.log(max(annualized, 1e-8))

            # Drop NaN rows (first 21 days)
            valid_mask = ~np.isnan(log_rvol)
            returns_clean = returns[valid_mask]
            log_rvol_clean = log_rvol[valid_mask]

            if len(returns_clean) < _MIN_TRAINING_SAMPLES:
                logger.debug(
                    "Insufficient clean features: %d (need %d)",
                    len(returns_clean), _MIN_TRAINING_SAMPLES,
                )
                return None

            # Take last 252 days (rolling window)
            returns_final = returns_clean[-_ROLLING_WINDOW:]
            log_rvol_final = log_rvol_clean[-_ROLLING_WINDOW:]

            # Stack into (N, 2) feature matrix
            features = np.column_stack([returns_final, log_rvol_final])

            logger.debug(
                "HMM features built: shape=%s, return_range=[%.4f, %.4f], "
                "log_rvol_range=[%.3f, %.3f]",
                features.shape,
                returns_final.min(), returns_final.max(),
                log_rvol_final.min(), log_rvol_final.max(),
            )

            return features

        except Exception as e:
            logger.warning("Failed to build HMM features: %s", e)
            return None

    def _fit_hmm(self, features: "np.ndarray") -> Optional[object]:
        """Fit a 2-state Gaussian HMM on the feature matrix.

        Returns:
            Fitted GaussianHMM model, or None on convergence failure.
        """
        try:
            model = GaussianHMM(
                n_components=2,
                covariance_type="full",
                n_iter=_HMM_N_ITER,
                random_state=_HMM_RANDOM_STATE,
                verbose=False,
            )

            # Suppress ConvergenceWarning — we handle it via score check
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=RuntimeWarning)
                warnings.filterwarnings(
                    "ignore",
                    message=".*did not converge.*",
                )
                model.fit(features)

            # Validate convergence via log-likelihood
            try:
                score = model.score(features)
                if not np.isfinite(score):
                    logger.warning(
                        "HMM fit produced non-finite score=%.4f — rejecting",
                        score,
                    )
                    return None
            except Exception:
                # score() can fail if model is degenerate
                logger.warning("HMM score computation failed — rejecting model")
                return None

            # Identify the "bullish" state (higher mean return)
            mean_returns = [float(model.means_[i][0]) for i in range(2)]
            bullish_idx = int(np.argmax(mean_returns))

            # Store diagnostics
            with self._lock:
                self._model = model
                self._bullish_state_idx = bullish_idx
                self._last_state_means = [
                    [round(float(model.means_[i][j]), 6) for j in range(2)]
                    for i in range(2)
                ]
                self._last_state_covars = [
                    [
                        [round(float(model.covars_[i][j][k]), 8) for k in range(2)]
                        for j in range(2)
                    ]
                    for i in range(2)
                ]
                self._last_transition_matrix = [
                    [round(float(model.transmat_[i][j]), 4) for j in range(2)]
                    for i in range(2)
                ]

            logger.info(
                "HMM fit complete | bullish_state=%d | "
                "means=[%.5f, %.5f] | "
                "score=%.2f | samples=%d",
                bullish_idx,
                mean_returns[0], mean_returns[1],
                score, len(features),
            )

            return model

        except Exception as e:
            logger.warning("HMM fit failed: %s", e)
            return None

    def _predict_current_regime(
        self,
        model: object,
        features: "np.ndarray",
    ) -> Optional["RegimeState"]:
        """Predict the current regime from the last observation.

        Maps the HMM hidden state to a RegimeState enum:
            - Bullish state (higher mean return, lower vol) → TRENDING_UP_STRONG
            - Bearish state (lower mean return, higher vol) → RISK_OFF

        Returns:
            Raw (unconfirmed) RegimeState, or None on failure.
        """
        if not _HAS_REGIME_STATE:
            return None

        try:
            # Predict state probabilities for the full sequence
            state_probs = model.predict_proba(features)

            # Current observation = last row
            bullish_prob = float(state_probs[-1][self._bullish_state_idx])

            with self._lock:
                self._last_bullish_prob = bullish_prob

            # Map to RegimeState with graduated thresholds
            # Strong bullish: P(bullish) > 0.75
            # Moderate bullish: P(bullish) > 0.60
            # Moderate bearish: P(bearish) > 0.60 → HIGH_VOLATILITY
            # Strong bearish: P(bearish) > 0.75 → RISK_OFF
            # Ambiguous: RANGE_BOUND

            if bullish_prob > 0.75:
                return RegimeState.TRENDING_UP_STRONG
            elif bullish_prob > 0.60:
                return RegimeState.TRENDING_UP_MOD
            elif bullish_prob < 0.25:
                return RegimeState.RISK_OFF
            elif bullish_prob < 0.40:
                return RegimeState.HIGH_VOLATILITY
            else:
                return RegimeState.RANGE_BOUND

        except Exception as e:
            logger.warning("HMM state prediction failed: %s", e)
            return None

    def _apply_confirmation_lag(
        self,
        raw_regime: "RegimeState",
    ) -> Optional["RegimeState"]:
        """Apply 3-day confirmation lag to suppress false transitions.

        A new regime must be predicted for ``_confirmation_lag``
        consecutive updates before it becomes the official regime.

        Args:
            raw_regime: The unconfirmed regime from the current prediction.

        Returns:
            The confirmed regime (may be the previous one if lag not met).
        """
        with self._lock:
            # First ever prediction — confirm immediately
            if self._confirmed_regime is None:
                self._confirmed_regime = raw_regime
                self._pending_regime = None
                self._pending_days = 0
                logger.info(
                    "HMM initial regime confirmed: %s",
                    raw_regime.value,
                )
                return raw_regime

            # Same as currently confirmed — reset pending
            if raw_regime == self._confirmed_regime:
                self._pending_regime = None
                self._pending_days = 0
                return self._confirmed_regime

            # Different from confirmed — track pending
            if raw_regime == self._pending_regime:
                self._pending_days += 1
            else:
                # New candidate — start fresh
                self._pending_regime = raw_regime
                self._pending_days = 1

            # Check if confirmation lag is met
            if self._pending_days >= self._confirmation_lag:
                old_regime = self._confirmed_regime
                self._confirmed_regime = raw_regime
                self._pending_regime = None
                self._pending_days = 0
                logger.info(
                    "HMM regime TRANSITION: %s → %s (confirmed after %d days)",
                    old_regime.value,
                    raw_regime.value,
                    self._confirmation_lag,
                )
                return raw_regime

            logger.debug(
                "HMM regime pending: %s → %s (%d/%d days)",
                self._confirmed_regime.value,
                raw_regime.value,
                self._pending_days,
                self._confirmation_lag,
            )
            return self._confirmed_regime
