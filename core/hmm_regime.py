"""
NZT-48 — Hamilton (1989) 2-State Gaussian HMM Regime Classifier
Hamilton (1989) "A New Approach to Economic Analysis of Nonstationary Time Series"
Ang & Timmermann (2012): momentum fails in choppy/high-vol regimes.

State 0: Trending/Low-Vol  → S15 permitted, full confidence
State 1: Choppy/High-Vol   → S15 halted, S16 fallback only

Trained on 60-day rolling QQQ daily returns. Retrained weekly Sunday 21:00.
"""
from __future__ import annotations
import json
import logging
import os
import pickle
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_PATH = "data/hmm_model.pkl"
_STATE_PATH = "data/hmm_state.json"


class HMMRegimeClassifier:
    """
    2-state Gaussian HMM. Cold-start returns 0.5 (never blocks trading).
    """

    def __init__(self):
        self._model = None
        self._current_state_prob: float = 0.5  # P(State=1 choppy)
        self._current_state: int = 0  # 0=trending, 1=choppy
        self._is_trained: bool = False
        self._choppy_state: int = 1  # default; overwritten after training
        self._load_model()
        self._load_state()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load_model(self) -> None:
        try:
            if os.path.exists(_MODEL_PATH):
                with open(_MODEL_PATH, "rb") as f:
                    self._model = pickle.load(f)
                self._is_trained = True
                logger.info("HMM model loaded from %s", _MODEL_PATH)
        except Exception as e:
            logger.debug("HMM model load failed: %s", e)

    def _save_model(self) -> None:
        os.makedirs("data", exist_ok=True)
        try:
            with open(_MODEL_PATH, "wb") as f:
                pickle.dump(self._model, f)
        except Exception:
            pass

    def _load_state(self) -> None:
        try:
            if os.path.exists(_STATE_PATH):
                with open(_STATE_PATH) as f:
                    state = json.load(f)
                self._current_state_prob = state.get("choppy_prob", 0.5)
                self._current_state = state.get("state", 0)
        except Exception:
            pass

    def _save_state(self) -> None:
        os.makedirs("data", exist_ok=True)
        try:
            with open(_STATE_PATH, "w") as f:
                json.dump({
                    "choppy_prob": self._current_state_prob,
                    "state": self._current_state,
                    "state_label": "CHOPPY" if self._current_state == 1 else "TRENDING",
                }, f, indent=2)
        except Exception:
            pass

    # ── Training ─────────────────────────────────────────────────────────────

    def train(self, lookback_days: int = 60) -> bool:
        """Train on QQQ 60-day rolling daily returns. Called weekly."""
        try:
            import yfinance as yf
            from hmmlearn.hmm import GaussianHMM

            # Fetch QQQ as regime proxy
            hist = yf.Ticker("QQQ").history(period=f"{lookback_days + 5}d")
            if hist is None or len(hist) < 20:
                logger.warning("HMM train: insufficient QQQ data")
                return False

            returns = hist["Close"].pct_change().dropna().values.reshape(-1, 1)

            model = GaussianHMM(
                n_components=2,
                covariance_type="full",
                n_iter=100,
                random_state=42,
            )
            model.fit(returns)

            # Identify which state is "choppy" (higher variance)
            vars_ = [model.covars_[i][0][0] for i in range(2)]
            self._choppy_state = int(np.argmax(vars_))  # higher variance = choppy

            self._model = model
            self._is_trained = True
            self._save_model()

            # Update current state
            self._update_state(returns)

            logger.info(
                "HMM trained: choppy_state=%d vars=%s current_state=%d",
                self._choppy_state, [round(v, 6) for v in vars_], self._current_state
            )
            return True

        except Exception as e:
            logger.warning("HMM training failed: %s", e)
            return False

    def _update_state(self, returns: np.ndarray) -> None:
        """Run Viterbi on recent returns and update current state."""
        try:
            states = self._model.predict(returns)
            # Get posterior probability of choppy state for last observation
            posteriors = self._model.predict_proba(returns)
            choppy_state = getattr(self, '_choppy_state', 1)
            self._current_state_prob = float(posteriors[-1][choppy_state])
            self._current_state = int(states[-1] == choppy_state)
            self._save_state()
        except Exception as e:
            logger.debug("HMM state update failed: %s", e)

    # ── Inference ────────────────────────────────────────────────────────────

    def predict_choppy_prob(self, recent_returns: Optional[np.ndarray] = None) -> float:
        """
        Returns P(State=1 choppy). Returns 0.5 if model unavailable.
        0.5 = neutral — never blocks trading on uncertainty.
        """
        if not self._is_trained or self._model is None:
            return 0.5

        if recent_returns is not None:
            try:
                self._update_state(recent_returns.reshape(-1, 1))
            except Exception:
                pass

        return self._current_state_prob

    def is_choppy(self, threshold: float = 0.60) -> bool:
        """True if P(choppy) > threshold. Threshold=0.60 per Mandate 4b."""
        return self.predict_choppy_prob() > threshold

    def get_regime_label(self) -> str:
        prob = self.predict_choppy_prob()
        if prob > 0.75:
            return "CHOPPY_HIGH"
        elif prob > 0.60:
            return "CHOPPY"
        elif prob < 0.25:
            return "TRENDING_STRONG"
        elif prob < 0.40:
            return "TRENDING"
        return "MODERATE"

    def refresh(self) -> None:
        """Fetch latest QQQ data and update state (call daily at market open)."""
        try:
            import yfinance as yf
            hist = yf.Ticker("QQQ").history(period="5d")
            if hist is not None and len(hist) >= 2:
                returns = hist["Close"].pct_change().dropna().values.reshape(-1, 1)
                self._update_state(returns)
        except Exception as e:
            logger.debug("HMM refresh failed: %s", e)
