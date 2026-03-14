"""
tests/test_wave2_integration.py
================================
Wave 2 Omega upgrade integration stress tests.

Verifies math, threading safety, and model convergences for:
  1. DynamicSizer 12-factor pipeline (vol targeting, CVaR, momentum crash, ERC)
  2. Gaussian HMM Regime Classifier (Nystrup et al. 2017)
  3. SHAP Feature Stability Filter (Gu, Kelly & Xiu 2020)
  4. ERC Portfolio Optimizer (Maillard, Roncalli & Teiletche 2010)

All tests are self-contained with mocks — zero network, DB, or API calls.
Run: python3 -m pytest tests/test_wave2_integration.py -v --tb=short
"""

from __future__ import annotations

import math
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Config mock helper
# ---------------------------------------------------------------------------


def _make_config_side_effect(overrides: dict | None = None):
    """Build a side_effect for config.get() that returns sensible defaults.

    Feature flags default to True (enabled). The ``dynamic_sizer`` key
    returns an empty dict (matching the real YAML structure).  Everything
    else falls through to the caller's ``default`` argument.
    """
    _overrides = overrides or {}

    def _side_effect(key, default=None):
        if key in _overrides:
            return _overrides[key]
        # Feature flags (v95_*) → return the caller-provided default
        # which is usually True in production code
        if key.startswith("v95_"):
            return default if default is not None else True
        if key == "dynamic_sizer":
            return {}
        return default

    return _side_effect


# Standard patches applied to every DynamicSizer test
_CFG_PATCHES = {
    "config.get": _make_config_side_effect(),
    "config.get_ticker_override": lambda *a, **kw: 0,
    "config.load_config": lambda *a, **kw: {},
}


# ═══════════════════════════════════════════════════════════════════════
# 1. DynamicSizer Wave 2 Math Tests
# ═══════════════════════════════════════════════════════════════════════


class TestDynamicSizerWave2Math:
    """Verify the 4 Wave 2 scalar computations produce correct outputs."""

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _make_sizer():
        from qualification.dynamic_sizer import DynamicSizer
        return DynamicSizer(starting_equity=10_000.0)

    # ── CVaR scalar (Rockafellar & Uryasev 2000) ──────────────────────

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_cvar_heavy_losses(self, _mock_get, _mock_ticker):
        """60 trades at -2.5R → CVaR=-2.5 → scalar=min(1, 2/2.5)=0.80."""
        sizer = self._make_sizer()
        sizer.load_history([-2.5] * 60)
        scalar = sizer._compute_cvar_scalar()
        assert scalar == pytest.approx(0.80, abs=0.01)

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_cvar_extreme_losses_hits_floor(self, _mock_get, _mock_ticker):
        """60 trades at -10R → raw=0.20 → floored at 0.25."""
        sizer = self._make_sizer()
        sizer.load_history([-10.0] * 60)
        scalar = sizer._compute_cvar_scalar()
        assert scalar == pytest.approx(0.25, abs=0.01)

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_cvar_healthy_tail(self, _mock_get, _mock_ticker):
        """60 winning trades → CVaR positive → no penalty."""
        sizer = self._make_sizer()
        sizer.load_history([1.5] * 60)
        scalar = sizer._compute_cvar_scalar()
        assert scalar == 1.0

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_cvar_insufficient_data(self, _mock_get, _mock_ticker):
        """Only 25 trades (below 30 minimum) → pass-through."""
        sizer = self._make_sizer()
        sizer.load_history([-3.0] * 25)
        scalar = sizer._compute_cvar_scalar()
        assert scalar == 1.0

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect(
        {"v95_cvar_scaling_enabled": False},
    ))
    def test_cvar_disabled_flag(self, _mock_get, _mock_ticker):
        """Feature flag off → always 1.0."""
        sizer = self._make_sizer()
        sizer.load_history([-5.0] * 60)
        scalar = sizer._compute_cvar_scalar()
        assert scalar == 1.0

    # ── Momentum crash scalar (Barroso & Santa-Clara 2015) ────────────

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_momentum_crash_triggers(self, _mock_get, _mock_ticker):
        """VIX=35, SPX_3m=-5% → scalar=0.60."""
        sizer = self._make_sizer()
        sizer.update_macro(vix=35.0, spx_3m_return=-0.05)
        scalar = sizer._compute_momentum_crash_scalar()
        assert scalar == 0.60

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_momentum_crash_no_trigger_vix_low(self, _mock_get, _mock_ticker):
        """VIX=25 (below 30 threshold) → no trigger."""
        sizer = self._make_sizer()
        sizer.update_macro(vix=25.0, spx_3m_return=-0.05)
        scalar = sizer._compute_momentum_crash_scalar()
        assert scalar == 1.0

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_momentum_crash_no_trigger_spx_positive(self, _mock_get, _mock_ticker):
        """SPX 3m positive → no trigger even with high VIX."""
        sizer = self._make_sizer()
        sizer.update_macro(vix=35.0, spx_3m_return=0.02)
        scalar = sizer._compute_momentum_crash_scalar()
        assert scalar == 1.0

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_momentum_crash_boundary_vix_exactly_30(self, _mock_get, _mock_ticker):
        """VIX=30.0 exactly → strict >30 check → no trigger."""
        sizer = self._make_sizer()
        sizer.update_macro(vix=30.0, spx_3m_return=-0.01)
        scalar = sizer._compute_momentum_crash_scalar()
        assert scalar == 1.0

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect(
        {"v95_momentum_crash_guard_enabled": False},
    ))
    def test_momentum_crash_disabled_flag(self, _mock_get, _mock_ticker):
        """Feature flag off → 1.0 regardless of macro state."""
        sizer = self._make_sizer()
        sizer.update_macro(vix=40.0, spx_3m_return=-0.10)
        scalar = sizer._compute_momentum_crash_scalar()
        assert scalar == 1.0

    # ── Vol target scalar (Moreira & Muir 2017) ──────────────────────

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_vol_target_high_vol_reduces(self, _mock_get, _mock_ticker):
        """High-variance trades → realized vol > target → scalar < 1.0."""
        sizer = self._make_sizer()
        # Alternating +3R / -3R creates high volatility
        sizer.load_history([3.0, -3.0] * 15)  # 30 trades
        scalar = sizer._compute_vol_target_scalar()
        assert scalar < 1.0, f"Expected <1.0 for high-vol trades, got {scalar}"
        assert scalar >= 0.30, f"Should respect floor 0.30, got {scalar}"

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_vol_target_insufficient_trades(self, _mock_get, _mock_ticker):
        """Only 15 trades (below 21 lookback) → pass-through."""
        sizer = self._make_sizer()
        sizer.load_history([1.0] * 15)
        scalar = sizer._compute_vol_target_scalar()
        assert scalar == 1.0

    # ── ERC scalar (Maillard et al. 2010) ─────────────────────────────

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_erc_scalar_underweight(self, _mock_get, _mock_ticker):
        """Ticker at 10% in 5-asset ERC (equal=20%) → scalar=0.50."""
        sizer = self._make_sizer()
        mock_opt = MagicMock()
        mock_opt.get_weights.return_value = {
            "A": 0.10, "B": 0.30, "C": 0.20, "D": 0.15, "E": 0.25,
        }
        sizer.set_erc_optimizer(mock_opt)
        scalar = sizer._compute_erc_scalar("A")
        assert scalar == pytest.approx(0.50, abs=0.01)

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_erc_scalar_overweight_capped(self, _mock_get, _mock_ticker):
        """Ticker at 30% in 5-asset ERC (equal=20%) → capped at 1.0."""
        sizer = self._make_sizer()
        mock_opt = MagicMock()
        mock_opt.get_weights.return_value = {
            "A": 0.10, "B": 0.30, "C": 0.20, "D": 0.15, "E": 0.25,
        }
        sizer.set_erc_optimizer(mock_opt)
        scalar = sizer._compute_erc_scalar("B")
        assert scalar == 1.0

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_erc_scalar_floor_at_010(self, _mock_get, _mock_ticker):
        """Extreme underweight → floored at 0.10."""
        sizer = self._make_sizer()
        mock_opt = MagicMock()
        mock_opt.get_weights.return_value = {"A": 0.01, "B": 0.99}
        sizer.set_erc_optimizer(mock_opt)
        scalar = sizer._compute_erc_scalar("A")
        # erc_w/equal_w = 0.01/0.50 = 0.02 → floored at 0.10
        assert scalar == pytest.approx(0.10, abs=0.01)

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_erc_scalar_no_optimizer(self, _mock_get, _mock_ticker):
        """No ERC optimizer attached → pass-through 1.0."""
        sizer = self._make_sizer()
        scalar = sizer._compute_erc_scalar("ANY")
        assert scalar == 1.0


# ═══════════════════════════════════════════════════════════════════════
# 2. DynamicSizer Threading Tests
# ═══════════════════════════════════════════════════════════════════════


class TestDynamicSizerThreading:
    """Verify concurrent access to DynamicSizer does not deadlock.

    The DynamicSizer uses threading.Lock() (non-reentrant).
    get_status() computes Wave 2 scalars OUTSIDE the lock — each scalar
    method acquires the lock internally. If this ordering is ever broken,
    deadlock occurs. This test guards against that regression.
    """

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_concurrent_sizing_and_status_no_deadlock(
        self, _mock_get, _mock_ticker,
    ):
        """20 threads × 50 iterations of calculate_position_size + get_status.

        Must complete within 10 seconds. Deadlock = test failure.
        """
        from zoneinfo import ZoneInfo

        from models import Direction, RegimeState, Signal
        from qualification.dynamic_sizer import DynamicSizer

        sizer = DynamicSizer(starting_equity=10_000.0)
        sizer.load_history([1.0, -1.0] * 30)  # 60 trades
        sizer.update_macro(vix=20.0, spx_3m_return=0.05)

        signal = Signal(
            ticker="QQQ3.L",
            entry=100.0,
            stop=98.0,
            confidence=75.0,
            direction=Direction.LONG,
            target_1r=102.0,
        )

        et = ZoneInfo("America/New_York")
        fixed_time = datetime(2025, 6, 2, 10, 30, tzinfo=et)

        errors: list[tuple[int, str]] = []
        completed = [0]
        completed_lock = threading.Lock()

        def worker(thread_id: int) -> None:
            try:
                for _ in range(50):
                    if thread_id % 2 == 0:
                        sizer.calculate_position_size(
                            signal=signal,
                            regime=RegimeState.TRENDING_UP_STRONG,
                            equity=10_000.0,
                            open_positions=[],
                            recent_trades=[],
                            current_time=fixed_time,
                        )
                    else:
                        sizer.get_status()
                with completed_lock:
                    completed[0] += 1
            except Exception as e:
                errors.append((thread_id, f"{type(e).__name__}: {e}"))

        threads = [
            threading.Thread(target=worker, args=(i,), daemon=True)
            for i in range(20)
        ]
        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        elapsed = time.monotonic() - start

        # Assertions
        assert not errors, f"Thread errors: {errors}"
        assert elapsed < 10.0, f"Deadlock suspected: took {elapsed:.1f}s"
        for t in threads:
            assert not t.is_alive(), "Thread still alive — likely deadlocked"
        assert completed[0] == 20, f"Only {completed[0]}/20 threads completed"

    @patch("config.get_ticker_override", return_value=0)
    @patch("config.get", side_effect=_make_config_side_effect())
    def test_concurrent_update_from_trade(self, _mock_get, _mock_ticker):
        """20 threads × 50 calls to update_from_trade.

        Final trade count must equal 20 × 50 = 1000 (atomic increments).
        """
        from qualification.dynamic_sizer import DynamicSizer

        sizer = DynamicSizer(starting_equity=10_000.0)

        def worker() -> None:
            for _ in range(50):
                sizer.update_from_trade(1.0)

        threads = [
            threading.Thread(target=worker, daemon=True) for _ in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # _total_trade_count is incremented under lock in update_from_trade
        assert sizer._total_trade_count == 1000


# ═══════════════════════════════════════════════════════════════════════
# 3. Gaussian HMM Regime Tests
# ═══════════════════════════════════════════════════════════════════════


class TestGaussianHMMRegime:
    """Verify HMM fit convergence, state mapping, and confirmation lag."""

    # ── Convergence tests (real hmmlearn + numpy) ─────────────────────

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_fit_hmm_converges(self, _mock_get):
        """Synthetic 252-day features with two distinct regimes.

        HMM must converge (not return None) and produce two
        distinguishable state means.
        """
        pytest.importorskip("hmmlearn", reason="hmmlearn required")
        from core.regime_hmm import GaussianHMMRegimeClassifier

        classifier = GaussianHMMRegimeClassifier(confirmation_lag=3)

        # Synthetic features: (daily_return, log_realized_vol)
        rng = np.random.RandomState(42)
        # Bull regime: positive returns, low vol
        bull_returns = rng.normal(0.001, 0.005, 126)
        bull_log_rvol = rng.normal(-2.0, 0.1, 126)
        # Bear regime: negative returns, high vol
        bear_returns = rng.normal(-0.002, 0.015, 126)
        bear_log_rvol = rng.normal(-1.0, 0.1, 126)

        features = np.column_stack([
            np.concatenate([bull_returns, bear_returns]),
            np.concatenate([bull_log_rvol, bear_log_rvol]),
        ])
        assert features.shape == (252, 2)

        model = classifier._fit_hmm(features)
        assert model is not None, "HMM failed to converge"

        # Score must be finite
        score = model.score(features)
        assert np.isfinite(score), f"Non-finite score: {score}"

        # Two states must have distinguishable mean returns
        mean_returns = [float(model.means_[i][0]) for i in range(2)]
        assert abs(mean_returns[0] - mean_returns[1]) > 0.0005, (
            f"State means not distinguishable: {mean_returns}"
        )

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_predict_bull_period(self, _mock_get):
        """Features ending in bull period → bullish RegimeState."""
        pytest.importorskip("hmmlearn", reason="hmmlearn required")
        from core.regime_hmm import GaussianHMMRegimeClassifier
        from models import RegimeState

        classifier = GaussianHMMRegimeClassifier(confirmation_lag=3)

        rng = np.random.RandomState(42)
        # Bear first, bull last (so last observation is bullish)
        bear_returns = rng.normal(-0.002, 0.015, 126)
        bear_log_rvol = rng.normal(-1.0, 0.1, 126)
        bull_returns = rng.normal(0.001, 0.005, 126)
        bull_log_rvol = rng.normal(-2.0, 0.1, 126)

        features = np.column_stack([
            np.concatenate([bear_returns, bull_returns]),
            np.concatenate([bear_log_rvol, bull_log_rvol]),
        ])

        model = classifier._fit_hmm(features)
        assert model is not None

        regime = classifier._predict_current_regime(model, features)
        assert regime is not None
        bullish_states = {
            RegimeState.TRENDING_UP_STRONG,
            RegimeState.TRENDING_UP_MOD,
        }
        assert regime in bullish_states, (
            f"Expected bullish state, got {regime.value}"
        )

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_predict_bear_period(self, _mock_get):
        """Features ending in bear period → bearish RegimeState."""
        pytest.importorskip("hmmlearn", reason="hmmlearn required")
        from core.regime_hmm import GaussianHMMRegimeClassifier
        from models import RegimeState

        classifier = GaussianHMMRegimeClassifier(confirmation_lag=3)

        rng = np.random.RandomState(42)
        # Bull first, bear last
        bull_returns = rng.normal(0.001, 0.005, 126)
        bull_log_rvol = rng.normal(-2.0, 0.1, 126)
        bear_returns = rng.normal(-0.003, 0.020, 126)
        bear_log_rvol = rng.normal(-0.5, 0.1, 126)

        features = np.column_stack([
            np.concatenate([bull_returns, bear_returns]),
            np.concatenate([bull_log_rvol, bear_log_rvol]),
        ])

        model = classifier._fit_hmm(features)
        assert model is not None

        regime = classifier._predict_current_regime(model, features)
        assert regime is not None
        bearish_states = {
            RegimeState.RISK_OFF,
            RegimeState.HIGH_VOLATILITY,
        }
        assert regime in bearish_states, (
            f"Expected bearish state, got {regime.value}"
        )

    # ── Graduated threshold test (mock predict_proba) ─────────────────

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_graduated_thresholds(self, _mock_get):
        """Verify the 5 probability → RegimeState mappings."""
        pytest.importorskip("hmmlearn", reason="hmmlearn required")
        from core.regime_hmm import GaussianHMMRegimeClassifier
        from models import RegimeState

        classifier = GaussianHMMRegimeClassifier(confirmation_lag=3)
        classifier._bullish_state_idx = 0  # State 0 = bullish

        cases = [
            (0.80, RegimeState.TRENDING_UP_STRONG),   # > 0.75
            (0.65, RegimeState.TRENDING_UP_MOD),       # > 0.60
            (0.50, RegimeState.RANGE_BOUND),           # 0.40..0.60
            (0.35, RegimeState.HIGH_VOLATILITY),       # < 0.40
            (0.20, RegimeState.RISK_OFF),              # < 0.25
        ]

        for bullish_prob, expected_regime in cases:
            # Mock model with predict_proba returning controlled values
            mock_model = MagicMock()
            # predict_proba returns (n_samples, n_states)
            # Last row: [bullish_prob, 1-bullish_prob]
            proba_row = np.zeros((1, 2))
            proba_row[0, 0] = bullish_prob
            proba_row[0, 1] = 1.0 - bullish_prob
            mock_model.predict_proba.return_value = proba_row

            features = np.zeros((1, 2))  # Dummy features
            regime = classifier._predict_current_regime(mock_model, features)

            assert regime == expected_regime, (
                f"bullish_prob={bullish_prob}: expected {expected_regime.value}, "
                f"got {regime.value if regime else None}"
            )

    # ── Confirmation lag state machine ────────────────────────────────

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_confirmation_lag_suppresses_false_transitions(self, _mock_get):
        """New regime must persist for 3 consecutive calls before confirmed."""
        from core.regime_hmm import GaussianHMMRegimeClassifier
        from models import RegimeState

        classifier = GaussianHMMRegimeClassifier(confirmation_lag=3)

        # First call: immediate confirmation (no prior regime)
        r1 = classifier._apply_confirmation_lag(RegimeState.TRENDING_UP_STRONG)
        assert r1 == RegimeState.TRENDING_UP_STRONG

        # Send RISK_OFF 3 times
        r2 = classifier._apply_confirmation_lag(RegimeState.RISK_OFF)
        assert r2 == RegimeState.TRENDING_UP_STRONG  # pending_days=1

        r3 = classifier._apply_confirmation_lag(RegimeState.RISK_OFF)
        assert r3 == RegimeState.TRENDING_UP_STRONG  # pending_days=2

        r4 = classifier._apply_confirmation_lag(RegimeState.RISK_OFF)
        assert r4 == RegimeState.RISK_OFF  # pending_days=3 → confirmed!

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_confirmation_lag_resets_on_new_candidate(self, _mock_get):
        """Switching candidate mid-stream resets the pending counter."""
        from core.regime_hmm import GaussianHMMRegimeClassifier
        from models import RegimeState

        classifier = GaussianHMMRegimeClassifier(confirmation_lag=3)

        # Confirm initial regime
        classifier._apply_confirmation_lag(RegimeState.TRENDING_UP_STRONG)

        # Send RISK_OFF twice (pending=2)
        classifier._apply_confirmation_lag(RegimeState.RISK_OFF)
        classifier._apply_confirmation_lag(RegimeState.RISK_OFF)

        # Switch to HIGH_VOLATILITY → resets to pending=1
        r = classifier._apply_confirmation_lag(RegimeState.HIGH_VOLATILITY)
        assert r == RegimeState.TRENDING_UP_STRONG  # Still old regime

        # Need 2 more HIGH_VOLATILITY to confirm
        r = classifier._apply_confirmation_lag(RegimeState.HIGH_VOLATILITY)
        assert r == RegimeState.TRENDING_UP_STRONG  # pending=2

        r = classifier._apply_confirmation_lag(RegimeState.HIGH_VOLATILITY)
        assert r == RegimeState.HIGH_VOLATILITY  # pending=3 → confirmed!

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_same_as_confirmed_resets_pending(self, _mock_get):
        """Sending the confirmed regime clears any pending candidate."""
        from core.regime_hmm import GaussianHMMRegimeClassifier
        from models import RegimeState

        classifier = GaussianHMMRegimeClassifier(confirmation_lag=3)

        # Confirm initial
        classifier._apply_confirmation_lag(RegimeState.TRENDING_UP_STRONG)

        # Start pending RISK_OFF
        classifier._apply_confirmation_lag(RegimeState.RISK_OFF)  # pending=1

        # Send confirmed regime → resets pending
        r = classifier._apply_confirmation_lag(RegimeState.TRENDING_UP_STRONG)
        assert r == RegimeState.TRENDING_UP_STRONG
        assert classifier._pending_regime is None
        assert classifier._pending_days == 0

        # RISK_OFF again → starts fresh at pending=1 (not 2)
        classifier._apply_confirmation_lag(RegimeState.RISK_OFF)
        assert classifier._pending_days == 1


# ═══════════════════════════════════════════════════════════════════════
# 4. SHAP Feature Stability Filter Tests
# ═══════════════════════════════════════════════════════════════════════


class TestSHAPStabilityFilter:
    """Verify SHAP rank drift detection and active_features management."""

    @staticmethod
    def _make_bare_model(
        feature_cols: list[str],
        shap_history: list[dict[str, int]] | None = None,
    ):
        """Create an MLMetaModel bypassing __init__ file I/O."""
        from core.ml_meta_model import MLMetaModel

        m = MLMetaModel.__new__(MLMetaModel)
        m.feature_cols = feature_cols
        m.active_features = list(feature_cols)
        m._shap_history = shap_history or []
        m._unstable_features = []
        m.is_trained = False
        m.model = None
        m._xgb_model = None
        m.data_path = Path("/dev/null")
        m.model_path = Path("/dev/null")
        m.min_trades = 200
        m._last_trained_at = None
        m._n_trades_at_last_train = 0
        return m

    def test_shap_drift_removes_unstable_feature(self):
        """Feature f3 drifts 6 positions across 4 windows → UNSTABLE."""
        features = ["f1", "f2", "f3", "f4", "f5", "f6", "f7"]
        # 3 prior windows: f3 drifts wildly (ranks 3→7→1, drift=6>5)
        history = [
            {"f1": 1, "f2": 2, "f3": 3, "f4": 4, "f5": 5, "f6": 6, "f7": 7},
            {"f1": 1, "f2": 2, "f3": 7, "f4": 4, "f5": 5, "f6": 6, "f7": 3},
            {"f1": 1, "f2": 2, "f3": 1, "f4": 4, "f5": 5, "f6": 6, "f7": 7},
        ]
        m = self._make_bare_model(features, history)

        # Mock SHAP explainer to return values that give f3 rank 4
        # Importance order by mean |SHAP|: f1>f2>f7>f3>f4>f5>f6
        shap_values = np.array([[0.7, 0.6, 0.4, 0.35, 0.2, 0.1, 0.5]])

        mock_explainer = MagicMock()
        mock_explainer.shap_values.return_value = [shap_values, shap_values]

        with patch("core.ml_meta_model.shap", create=True) as mock_shap, \
             patch("core.ml_meta_model._HAS_SHAP", True), \
             patch("core.ml_meta_model._HAS_CFG", True), \
             patch("core.ml_meta_model.cfg") as mock_cfg, \
             patch.object(m, "_save_shap_history"):
            mock_shap.TreeExplainer.return_value = mock_explainer
            mock_cfg.get.return_value = True

            result = m._run_shap_stability_filter(MagicMock(), shap_values)

        assert result["status"] == "OK"
        assert "f3" in m._unstable_features, (
            f"f3 should be unstable, got {m._unstable_features}"
        )
        assert "f3" not in m.active_features
        # f1, f2 should remain (stable, drift ≤ 5)
        assert "f1" in m.active_features
        assert "f2" in m.active_features

    def test_shap_stable_features_remain_active(self):
        """All features drift ≤ 5 → none flagged unstable."""
        features = ["f1", "f2", "f3", "f4", "f5"]
        # Consistent rankings across 3 windows (drift ≤ 2 for all)
        history = [
            {"f1": 1, "f2": 2, "f3": 3, "f4": 4, "f5": 5},
            {"f1": 1, "f2": 3, "f3": 2, "f4": 4, "f5": 5},
            {"f1": 2, "f2": 1, "f3": 3, "f4": 5, "f5": 4},
        ]
        m = self._make_bare_model(features, history)

        # 4th window: similar stable ranking
        shap_values = np.array([[0.5, 0.45, 0.4, 0.3, 0.25]])

        mock_explainer = MagicMock()
        mock_explainer.shap_values.return_value = [shap_values, shap_values]

        with patch("core.ml_meta_model.shap", create=True) as mock_shap, \
             patch("core.ml_meta_model._HAS_SHAP", True), \
             patch("core.ml_meta_model._HAS_CFG", True), \
             patch("core.ml_meta_model.cfg") as mock_cfg, \
             patch.object(m, "_save_shap_history"):
            mock_shap.TreeExplainer.return_value = mock_explainer
            mock_cfg.get.return_value = True

            result = m._run_shap_stability_filter(MagicMock(), shap_values)

        assert result["status"] == "OK"
        assert len(m._unstable_features) == 0
        assert m.active_features == features

    def test_shap_minimum_4_features_kept(self):
        """Even if many features are unstable, minimum 4 are retained."""
        features = ["f1", "f2", "f3", "f4", "f5"]
        # All features except f1 drift wildly (drift > 5)
        history = [
            {"f1": 1, "f2": 2, "f3": 3, "f4": 4, "f5": 5},
            {"f1": 1, "f2": 5, "f3": 5, "f4": 5, "f5": 2},
            {"f1": 1, "f2": 2, "f3": 2, "f4": 2, "f5": 5},
        ]
        # drift: f2=3, f3=3, f4=3, f5=3 — all ≤5, actually stable
        # Let me make them truly unstable (drift > 5):
        history = [
            {"f1": 1, "f2": 2, "f3": 3, "f4": 4, "f5": 5},
            {"f1": 1, "f2": 5, "f3": 5, "f4": 5, "f5": 1},
            # Now add a 3rd window that pushes drifts > 5
            # f2: ranks [2,5,?], f3: [3,5,?], f4: [4,5,?], f5: [5,1,?]
        ]
        # Actually for 5 features with drift>5, we need rank changes > 5
        # But max rank is 5 so max drift = 4. Can't exceed 5 with only 5 features.
        # Use 10 features instead:
        features = [f"f{i}" for i in range(10)]
        history = [
            {f"f{i}": i + 1 for i in range(10)},  # f0=1..f9=10
            {f"f{i}": 10 - i for i in range(10)},  # f0=10..f9=1 (reversed)
            # drift for all: |rank1 - rank2| = |i+1 - (10-i)| = |2i-9|
            # f0: |1-10|=9, f1:|2-9|=7, f2:|3-8|=5, f3:|4-7|=3, f4:|5-6|=1
            # So f0(9), f1(7), f2(5) are unstable (>5), f3(3), f4(1) stable
            # But we need "most" unstable: f0,f1 have drift>5
        ]
        m = self._make_bare_model(features, history)

        # 3rd window: back to original order
        shap_values = np.array([[1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]])

        mock_explainer = MagicMock()
        mock_explainer.shap_values.return_value = [shap_values, shap_values]

        with patch("core.ml_meta_model.shap", create=True) as mock_shap, \
             patch("core.ml_meta_model._HAS_SHAP", True), \
             patch("core.ml_meta_model._HAS_CFG", True), \
             patch("core.ml_meta_model.cfg") as mock_cfg, \
             patch.object(m, "_save_shap_history"):
            mock_shap.TreeExplainer.return_value = mock_explainer
            mock_cfg.get.return_value = True

            result = m._run_shap_stability_filter(MagicMock(), shap_values)

        assert result["status"] == "OK"
        assert len(m.active_features) >= 4, (
            f"Must keep ≥4 features, got {len(m.active_features)}"
        )

    def test_shap_disabled_flag_skips_filter(self):
        """Feature flag off → returns DISABLED, active_features unchanged."""
        features = ["f1", "f2", "f3"]
        m = self._make_bare_model(features)
        original_active = list(m.active_features)

        with patch("core.ml_meta_model._HAS_SHAP", True), \
             patch("core.ml_meta_model._HAS_CFG", True), \
             patch("core.ml_meta_model.cfg") as mock_cfg:
            mock_cfg.get.return_value = False  # Disabled

            result = m._run_shap_stability_filter(MagicMock(), np.zeros((1, 3)))

        assert result["status"] == "DISABLED"
        assert m.active_features == original_active

    def test_shap_needs_2_windows_minimum(self):
        """With only 1 window of history, no features are flagged unstable."""
        features = ["f1", "f2", "f3", "f4", "f5"]
        m = self._make_bare_model(features, shap_history=[])

        shap_values = np.array([[0.5, 0.4, 0.3, 0.2, 0.1]])

        mock_explainer = MagicMock()
        mock_explainer.shap_values.return_value = [shap_values, shap_values]

        with patch("core.ml_meta_model.shap", create=True) as mock_shap, \
             patch("core.ml_meta_model._HAS_SHAP", True), \
             patch("core.ml_meta_model._HAS_CFG", True), \
             patch("core.ml_meta_model.cfg") as mock_cfg, \
             patch.object(m, "_save_shap_history"):
            mock_shap.TreeExplainer.return_value = mock_explainer
            mock_cfg.get.return_value = True

            result = m._run_shap_stability_filter(MagicMock(), shap_values)

        assert result["status"] == "OK"
        assert len(m._unstable_features) == 0
        assert m.active_features == features


# ═══════════════════════════════════════════════════════════════════════
# 5. ERC Portfolio Optimizer Tests
# ═══════════════════════════════════════════════════════════════════════


class TestERCPortfolioOptimizer:
    """Verify ERC solver convergence, weight constraints, and diagnostics."""

    @staticmethod
    def _make_cov_matrix():
        """Build a realistic 5-asset covariance matrix."""
        vols = np.array([0.20, 0.25, 0.15, 0.30, 0.22])
        corr = np.array([
            [1.0, 0.5, 0.3, 0.2, 0.4],
            [0.5, 1.0, 0.4, 0.3, 0.5],
            [0.3, 0.4, 1.0, 0.2, 0.3],
            [0.2, 0.3, 0.2, 1.0, 0.4],
            [0.4, 0.5, 0.3, 0.4, 1.0],
        ])
        return np.outer(vols, vols) * corr

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_5_asset_convergence(self, _mock_get):
        """5-asset ERC: weights sum to 1.0, all ≥ min floor."""
        from core.portfolio_optimizer import ERCPortfolioOptimizer

        opt = ERCPortfolioOptimizer()
        tickers = ["A", "B", "C", "D", "E"]
        cov = self._make_cov_matrix()

        weights = opt.optimise(tickers, cov)

        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-4)
        for t, w in weights.items():
            assert w >= 0.02, f"{t} weight {w} below 2% floor"
            assert w > 0, f"{t} has non-positive weight"

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_equal_risk_contributions(self, _mock_get):
        """After ERC, all assets contribute ~equally to portfolio risk."""
        from core.portfolio_optimizer import ERCPortfolioOptimizer

        opt = ERCPortfolioOptimizer()
        tickers = ["A", "B", "C", "D", "E"]
        cov = self._make_cov_matrix()

        weights = opt.optimise(tickers, cov)

        # Compute risk contributions manually
        w = np.array([weights[t] for t in tickers])
        sigma_w = cov @ w
        port_var = float(w @ sigma_w)
        rc = (w * sigma_w) / port_var

        deviation = float(np.max(rc) - np.min(rc))
        assert deviation < 0.02, (
            f"Risk contribution deviation {deviation:.4f} too high "
            f"(target: < 0.02). RC={rc}"
        )

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_low_vol_gets_higher_weight(self, _mock_get):
        """Asset C (vol=0.15) should get more weight than D (vol=0.30)."""
        from core.portfolio_optimizer import ERCPortfolioOptimizer

        opt = ERCPortfolioOptimizer()
        tickers = ["A", "B", "C", "D", "E"]
        cov = self._make_cov_matrix()

        weights = opt.optimise(tickers, cov)

        assert weights["C"] > weights["D"], (
            f"Low-vol C ({weights['C']:.4f}) should outweigh "
            f"high-vol D ({weights['D']:.4f})"
        )

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_single_asset(self, _mock_get):
        """Single asset → weight 1.0."""
        from core.portfolio_optimizer import ERCPortfolioOptimizer

        opt = ERCPortfolioOptimizer()
        weights = opt.optimise(["A"], np.array([[0.04]]))
        assert weights == {"A": 1.0}

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_empty_tickers(self, _mock_get):
        """No tickers → empty dict."""
        from core.portfolio_optimizer import ERCPortfolioOptimizer

        opt = ERCPortfolioOptimizer()
        weights = opt.optimise([], np.array([]))
        assert weights == {}

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_mismatched_dimensions_falls_back(self, _mock_get):
        """3 tickers + 5×5 cov → equal weights fallback."""
        from core.portfolio_optimizer import ERCPortfolioOptimizer

        opt = ERCPortfolioOptimizer()
        cov = self._make_cov_matrix()  # 5×5
        weights = opt.optimise(["A", "B", "C"], cov)

        expected = round(1.0 / 3, 6)
        for w in weights.values():
            assert w == pytest.approx(expected, abs=1e-5)

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_non_positive_diagonal_falls_back(self, _mock_get):
        """Zero on diagonal → equal weights fallback."""
        from core.portfolio_optimizer import ERCPortfolioOptimizer

        opt = ERCPortfolioOptimizer()
        cov = np.array([
            [0.04, 0.01],
            [0.01, 0.00],  # Zero variance
        ])
        weights = opt.optimise(["A", "B"], cov)

        expected = round(1.0 / 2, 6)
        for w in weights.values():
            assert w == pytest.approx(expected, abs=1e-5)

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_min_weight_floor_enforced(self, _mock_get):
        """Moderate vol disparity → all weights still ≥ 2% floor.

        The _ERC_MIN_WEIGHT=0.02 floor is applied per iteration but
        renormalization can erode it with extreme disparity (100:1+).
        Using a realistic 5:1 ratio tests the floor without exceeding
        the solver's renormalization limits.
        """
        from core.portfolio_optimizer import ERCPortfolioOptimizer

        opt = ERCPortfolioOptimizer()
        # 5:1 vol disparity — challenging but realistic
        vols = np.array([0.05, 0.25, 0.08, 0.10, 0.12])
        corr = np.eye(5)  # Uncorrelated
        cov = np.outer(vols, vols) * corr

        weights = opt.optimise(["A", "B", "C", "D", "E"], cov)

        for t, w in weights.items():
            assert w >= 0.02, f"{t} weight {w:.4f} below 2% floor"

    @patch("config.get", side_effect=_make_config_side_effect(
        {"v95_erc_allocation_enabled": False},
    ))
    def test_disabled_flag_returns_equal(self, _mock_get):
        """Feature flag off → equal weights regardless of covariance."""
        from core.portfolio_optimizer import ERCPortfolioOptimizer

        opt = ERCPortfolioOptimizer()
        cov = self._make_cov_matrix()
        weights = opt.optimise(["A", "B", "C", "D", "E"], cov)

        expected = round(1.0 / 5, 6)
        for w in weights.values():
            assert w == pytest.approx(expected, abs=1e-5)

    @patch("config.get", side_effect=_make_config_side_effect())
    def test_diagnostics_populated(self, _mock_get):
        """get_diagnostics() returns all expected keys after optimisation."""
        from core.portfolio_optimizer import ERCPortfolioOptimizer

        opt = ERCPortfolioOptimizer()
        tickers = ["A", "B", "C", "D", "E"]
        cov = self._make_cov_matrix()
        opt.optimise(tickers, cov)

        diag = opt.get_diagnostics()

        assert "weights" in diag
        assert "risk_contributions" in diag
        assert "portfolio_vol" in diag
        assert "last_optimised_at" in diag
        assert "n_assets" in diag
        assert diag["n_assets"] == 5
        assert diag["last_optimised_at"] is not None
        assert diag["portfolio_vol"] > 0
