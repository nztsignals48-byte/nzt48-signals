"""Phase 6C acceptance tests for 12-factor Kelly sizing."""

import math

import numpy as np
import pytest

from brain.sizing.kelly_12factor import (
    bayesian_win_rate,
    kelly_12factor,
    outlier_capped_avg_win,
)
from brain.config import KELLY_CLAMP_MAX, KELLY_FRACTION_CAP


def _default_kelly_args(**overrides):
    """Default args for kelly_12factor with sensible values."""
    defaults = dict(
        win_rate_raw=0.55,
        total_trades=100,
        avg_win=0.02,
        avg_loss=0.015,
        leverage_factor=3,
        realized_vol_annual=0.20,
        correlation_to_portfolio=0.3,
        current_drawdown_pct=0.5,
        amihud_illiq=0.3,
        regime="normal",
        spread_pct=0.002,
        time_of_day_fraction=0.3,
        confidence=80.0,
        portfolio_heat_pct=2.0,
        equity=10000.0,
        price=10.0,
    )
    defaults.update(overrides)
    return defaults


class TestKellyDeterminism:
    """Kelly determinism: identical inputs → identical output."""

    def test_identical_inputs_identical_output(self):
        args = _default_kelly_args()
        r1 = kelly_12factor(**args)
        r2 = kelly_12factor(**args)
        assert r1["kelly_fraction"] == r2["kelly_fraction"]
        assert r1["shares"] == r2["shares"]
        assert r1["factors"] == r2["factors"]

    def test_determinism_across_many_calls(self):
        args = _default_kelly_args()
        fractions = [kelly_12factor(**args)["kelly_fraction"] for _ in range(100)]
        assert all(f == fractions[0] for f in fractions)


class TestKellyCap:
    """Kelly cap: high confidence → capped at half-Kelly (0.5)."""

    def test_half_kelly_cap(self):
        # Even with perfect inputs, kelly capped at 0.5
        args = _default_kelly_args(
            win_rate_raw=0.90,
            total_trades=1000,
            avg_win=0.10,
            avg_loss=0.01,
            leverage_factor=1,
            confidence=100.0,
            portfolio_heat_pct=0.0,
        )
        result = kelly_12factor(**args)
        assert result["kelly_fraction"] <= KELLY_FRACTION_CAP


class TestKellyClamp:
    """Kelly clamp: even with cap=0.5, output ≤ 0.20 (H57)."""

    def test_clamp_at_020(self):
        args = _default_kelly_args(
            win_rate_raw=0.90,
            total_trades=1000,
            avg_win=0.10,
            avg_loss=0.01,
            leverage_factor=1,
            confidence=100.0,
            portfolio_heat_pct=0.0,
        )
        result = kelly_12factor(**args)
        assert result["kelly_fraction"] <= KELLY_CLAMP_MAX

    def test_clamp_always_applied(self):
        """No combination of inputs can exceed 0.20."""
        for wr in [0.5, 0.7, 0.9, 0.99]:
            for lev in [1, 3, 5]:
                args = _default_kelly_args(
                    win_rate_raw=wr,
                    total_trades=10000,
                    avg_win=0.10,
                    avg_loss=0.005,
                    leverage_factor=lev,
                    confidence=100.0,
                    portfolio_heat_pct=0.0,
                )
                result = kelly_12factor(**args)
                assert result["kelly_fraction"] <= KELLY_CLAMP_MAX, \
                    f"Clamp violated: wr={wr}, lev={lev}, kelly={result['kelly_fraction']}"


class TestPortfolioHeat:
    """Portfolio heat: 3 positions at 2.1% each → new order rejected (>6%)."""

    def test_heat_above_6pct_zero_sizing(self):
        # Portfolio heat at 6.3% (3 × 2.1%) → no room
        args = _default_kelly_args(portfolio_heat_pct=6.3)
        result = kelly_12factor(**args)
        assert result["kelly_fraction"] == 0.0
        assert result["shares"] == 0

    def test_heat_at_5pct_allows_small(self):
        # 5% heat → 1% remaining room
        args = _default_kelly_args(portfolio_heat_pct=5.0)
        result = kelly_12factor(**args)
        assert result["kelly_fraction"] <= 0.01  # max 1% (6% - 5%)

    def test_heat_at_zero_full_room(self):
        args = _default_kelly_args(portfolio_heat_pct=0.0)
        result = kelly_12factor(**args)
        assert result["kelly_fraction"] > 0.0


class TestVolatilityDrag:
    """Volatility drag: 3x ETP → variance × 9 in calculation (H59)."""

    def test_3x_leverage_reduces_sizing(self):
        args_1x = _default_kelly_args(leverage_factor=1)
        args_3x = _default_kelly_args(leverage_factor=3)
        r_1x = kelly_12factor(**args_1x)
        r_3x = kelly_12factor(**args_3x)
        # 3x leverage has vol_decay = 1/9, so much smaller kelly
        assert r_3x["kelly_fraction"] < r_1x["kelly_fraction"]
        assert r_3x["factors"]["f02_vol_decay"] == pytest.approx(1.0 / 9.0)

    def test_5x_leverage_factor(self):
        args = _default_kelly_args(leverage_factor=5)
        result = kelly_12factor(**args)
        assert result["factors"]["f02_vol_decay"] == pytest.approx(1.0 / 25.0)

    def test_1x_no_drag(self):
        args = _default_kelly_args(leverage_factor=1)
        result = kelly_12factor(**args)
        assert result["factors"]["f02_vol_decay"] == pytest.approx(1.0)


class TestBayesianShrinkage:
    """Bayesian shrinkage: W=60% over 10 trades → adjusted downward (H58)."""

    def test_small_sample_shrinks_toward_50(self):
        # 6 wins / 10 trades = 60% raw
        wr_10 = bayesian_win_rate(6, 10)
        # Should be pulled toward 50%
        assert wr_10 < 0.60
        assert wr_10 > 0.50

    def test_large_sample_converges(self):
        # 600 wins / 1000 trades = 60% raw
        wr_1000 = bayesian_win_rate(600, 1000)
        # Should be very close to 60%
        assert abs(wr_1000 - 0.60) < 0.01

    def test_shrinkage_comparison(self):
        wr_small = bayesian_win_rate(6, 10)
        wr_large = bayesian_win_rate(600, 1000)
        # Small sample more shrunk than large
        assert abs(wr_small - 0.50) < abs(wr_large - 0.50)


class TestOutlierWinCap:
    """Outlier win cap: single trade at 5% → capped at 3% for Kelly avg (H62)."""

    def test_5pct_capped_to_3pct(self):
        returns = np.array([0.05, 0.01, 0.02, -0.01])
        avg = outlier_capped_avg_win(returns, cap_pct=3.0)
        # 0.05 capped to 0.03, others unchanged
        expected = np.mean([0.03, 0.01, 0.02, -0.01])
        assert abs(avg - expected) < 1e-9

    def test_no_cap_needed(self):
        returns = np.array([0.01, 0.02, 0.015])
        avg = outlier_capped_avg_win(returns, cap_pct=3.0)
        expected = np.mean(returns)
        assert abs(avg - expected) < 1e-9

    def test_empty_returns(self):
        assert outlier_capped_avg_win(np.array([])) == 0.0


class TestFractionalShares:
    """Fractional shares: always math.floor(), never round() (H64)."""

    def test_floor_not_round(self):
        # Kelly=0.10, equity=10000, price=7.50 → 133.33 → floor=133
        args = _default_kelly_args(equity=10000.0, price=7.50, portfolio_heat_pct=0.0)
        result = kelly_12factor(**args)
        # Verify shares is integer and uses floor
        assert isinstance(result["shares"], int)
        expected_max = math.floor(KELLY_CLAMP_MAX * 10000.0 / 7.50)
        assert result["shares"] <= expected_max

    def test_shares_always_integer(self):
        for price in [0.50, 1.00, 5.00, 10.00, 50.00, 100.00]:
            args = _default_kelly_args(price=price)
            result = kelly_12factor(**args)
            assert isinstance(result["shares"], int)
            assert result["shares"] >= 0


class TestRegimeScaling:
    """Regime scaling: reduce → 50%, flatten/halt → 0%."""

    def test_reduce_halves(self):
        args_normal = _default_kelly_args(regime="normal")
        args_reduce = _default_kelly_args(regime="reduce")
        r_normal = kelly_12factor(**args_normal)
        r_reduce = kelly_12factor(**args_reduce)
        if r_normal["kelly_fraction"] > 0:
            ratio = r_reduce["kelly_fraction"] / r_normal["kelly_fraction"]
            assert abs(ratio - 0.5) < 0.01

    def test_flatten_zero(self):
        args = _default_kelly_args(regime="flatten")
        result = kelly_12factor(**args)
        assert result["kelly_fraction"] == 0.0

    def test_halt_zero(self):
        args = _default_kelly_args(regime="halt")
        result = kelly_12factor(**args)
        assert result["kelly_fraction"] == 0.0


class TestAllTwelveFactors:
    """Verify all 12 factors are present in output."""

    def test_twelve_factors_documented(self):
        args = _default_kelly_args()
        result = kelly_12factor(**args)
        factors = result["factors"]
        expected_keys = [f"f{i:02d}" for i in range(1, 13)]
        for prefix in expected_keys:
            matching = [k for k in factors if k.startswith(prefix)]
            assert len(matching) == 1, f"Missing factor {prefix} in {factors.keys()}"
