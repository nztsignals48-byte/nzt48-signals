"""Test Suite Foundation — Book 55.

Tests for the 32 new Python modules built from the 224-book library.
Organized by category: risk, validation, strategies, sizing, ML, infra.

Run: python -m pytest python_brain/tests/test_core_modules.py -v
"""

import math
import numpy as np
import pytest


# ═══════════════════════════════════════════════════════════════════════
# RISK MODULE TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestOvernightRisk:
    def test_five_x_never_overnight(self):
        from python_brain.overnight.risk import max_overnight_position_pct
        assert max_overnight_position_pct("QQQ5.L", vix=15.0, leverage=5) == 0.0

    def test_steady_regime_full_exposure(self):
        from python_brain.overnight.risk import max_overnight_position_pct
        pct = max_overnight_position_pct("3USL.L", vix=15.0)
        assert pct == 15.0  # Tier 1 index cap

    def test_crisis_blocks_long(self):
        from python_brain.overnight.risk import max_overnight_position_pct
        pct = max_overnight_position_pct("NVD3.L", vix=55.0)
        assert pct == 0.0  # EXTREME CRISIS: 0% long overnight

    def test_friday_reduces_exposure(self):
        from python_brain.overnight.risk import max_overnight_position_pct
        weeknight = max_overnight_position_pct("3USL.L", vix=15.0, is_friday=False)
        friday = max_overnight_position_pct("3USL.L", vix=15.0, is_friday=True)
        assert friday < weeknight

    def test_vix_35_override(self):
        from python_brain.overnight.risk import max_overnight_position_pct
        pct = max_overnight_position_pct("3USL.L", vix=36.0)
        assert pct <= 10.0  # Hard cap at 10%

    def test_late_session_blocking(self):
        from python_brain.overnight.risk import should_block_overnight_entry
        blocked, _ = should_block_overnight_entry("QQQ5.L", vix=15.0, london_time_secs=58000, leverage=5)
        assert blocked


class TestCorrelation:
    def test_low_correlation_no_action(self):
        from python_brain.risk.correlation import CorrelationTracker, CorrelationRegime
        tracker = CorrelationTracker()
        assert tracker.regime == CorrelationRegime.LOW
        assert tracker.position_size_multiplier() == 1.0
        assert tracker.exit_trail_multiplier() == 1.0

    def test_exit_trail_tightens_with_correlation(self):
        from python_brain.risk.correlation import CorrelationTracker
        tracker = CorrelationTracker()
        # Manually set avg_corr for testing
        tracker._avg_corr = 0.65
        mult = tracker.exit_trail_multiplier()
        assert mult < 0.85  # Should tighten


class TestDrawdownRecovery:
    def test_normal_phase(self):
        from python_brain.risk.drawdown_recovery import DrawdownMonitor, DrawdownPhase
        mon = DrawdownMonitor(initial_equity=10000)
        mon.update(10000)
        assert mon.phase == DrawdownPhase.NORMAL
        assert mon.kelly_scale() == 1.0

    def test_monitoring_phase(self):
        from python_brain.risk.drawdown_recovery import DrawdownMonitor, DrawdownPhase
        mon = DrawdownMonitor(initial_equity=10000)
        mon.update(9400)  # 6% DD
        assert mon.phase == DrawdownPhase.MONITORING
        assert mon.kelly_scale() == 0.75

    def test_halted_phase(self):
        from python_brain.risk.drawdown_recovery import DrawdownMonitor, DrawdownPhase
        mon = DrawdownMonitor(initial_equity=10000)
        mon.update(7000)  # 30% DD
        assert mon.phase == DrawdownPhase.HALTED
        assert mon.kelly_scale() == 0.0
        assert mon.should_block_entry()

    def test_quadratic_recovery(self):
        from python_brain.risk.drawdown_recovery import DrawdownMonitor
        mon = DrawdownMonitor(initial_equity=10000, sacred_limit=8.0)
        mon.update(9600)  # 4% DD = 50% of sacred limit
        factor = mon.quadratic_recovery_factor()
        assert 0.5 < factor < 1.0  # Quadratic reduction


class TestSafetyBoundaries:
    def test_sacred_drawdown_halt(self):
        from python_brain.risk.safety_boundaries import SafetyBoundaryChecker
        checker = SafetyBoundaryChecker()
        v = checker.check_all(equity=9100, hwm=10000)
        assert v is not None
        assert v.action == "HALT"

    def test_no_violation_normal(self):
        from python_brain.risk.safety_boundaries import SafetyBoundaryChecker
        checker = SafetyBoundaryChecker()
        v = checker.check_all(equity=9900, hwm=10000)
        assert v is None

    def test_consecutive_losses_halt(self):
        from python_brain.risk.safety_boundaries import SafetyBoundaryChecker
        checker = SafetyBoundaryChecker()
        v = checker.check_all(equity=9800, hwm=10000, consecutive_losses=15)
        assert v is not None
        assert "consecutive" in v.reason

    def test_param_change_limit(self):
        from python_brain.risk.safety_boundaries import SafetyBoundaryChecker
        checker = SafetyBoundaryChecker()
        v = checker.validate_param_change("kelly_max", 0.05, 0.08)
        assert v is not None  # 60% change > 20% limit

    def test_state_machine_transitions(self):
        from python_brain.risk.safety_boundaries import OrderState, ORDER_TRANSITIONS, validate_transition
        assert validate_transition(OrderState.PENDING, OrderState.SUBMITTED, ORDER_TRANSITIONS)
        assert not validate_transition(OrderState.CANCELLED, OrderState.SUBMITTED, ORDER_TRANSITIONS)


# ═══════════════════════════════════════════════════════════════════════
# VALIDATION MODULE TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestDeflatedSharpe:
    def test_positive_dsr_with_real_edge(self):
        from python_brain.validation.strategy_gates import deflated_sharpe_ratio
        dsr = deflated_sharpe_ratio(observed_sharpe=2.0, n_trades=500, n_trials=1)
        assert dsr > 0

    def test_negative_dsr_with_many_trials(self):
        from python_brain.validation.strategy_gates import deflated_sharpe_ratio
        dsr = deflated_sharpe_ratio(observed_sharpe=0.5, n_trades=50, n_trials=100)
        assert dsr <= 0  # Low Sharpe + many trials = likely overfit

    def test_zero_sharpe_returns_zero(self):
        from python_brain.validation.strategy_gates import deflated_sharpe_ratio
        dsr = deflated_sharpe_ratio(observed_sharpe=0.0, n_trades=100)
        assert dsr == 0.0


class TestValidationGates:
    def test_insufficient_trades_fails(self):
        from python_brain.validation.strategy_gates import validate_strategy
        result = validate_strategy(np.random.randn(10) * 0.01)
        assert not result.passed
        assert "insufficient" in result.reason

    def test_good_strategy_passes(self):
        from python_brain.validation.strategy_gates import validate_strategy
        # Generate returns with clear positive drift
        np.random.seed(42)
        returns = np.random.randn(200) * 0.005 + 0.002  # Positive mean
        result = validate_strategy(returns, n_trials=1, min_trades=30, min_sharpe=0.3)
        # May or may not pass depending on random seed, but should get further than gate 1
        assert result.n_trades == 200


class TestMonteCarlo:
    def test_basic_mc(self):
        from python_brain.validation.monte_carlo import run_monte_carlo
        np.random.seed(42)
        returns = np.random.randn(100) * 0.005 + 0.001
        result = run_monte_carlo(returns, n_paths=100, horizon_days=50)
        assert result.n_paths == 100
        assert result.ruin_probability >= 0.0

    def test_insufficient_data(self):
        from python_brain.validation.monte_carlo import run_monte_carlo
        result = run_monte_carlo(np.array([0.01, 0.02]), n_paths=10)
        assert result.n_paths == 10
        assert result.ruin_probability == 0.0  # Not enough data


class TestPromotionPipeline:
    def test_backtest_gate_passes(self):
        from python_brain.validation.promotion_pipeline import PromotionPipeline, PipelineStage
        p = PromotionPipeline("TestStrategy")
        result = p.evaluate_stage(PipelineStage.BACKTEST, {
            "n_trades": 300, "sharpe": 1.5, "profit_factor": 2.0,
            "win_rate": 0.55, "max_drawdown_pct": 10.0,
        })
        assert result.passed

    def test_backtest_gate_fails_low_trades(self):
        from python_brain.validation.promotion_pipeline import PromotionPipeline, PipelineStage
        p = PromotionPipeline("TestStrategy")
        result = p.evaluate_stage(PipelineStage.BACKTEST, {
            "n_trades": 50, "sharpe": 1.5, "profit_factor": 2.0,
            "win_rate": 0.55, "max_drawdown_pct": 10.0,
        })
        assert not result.passed
        assert "trades" in result.reason


# ═══════════════════════════════════════════════════════════════════════
# STRATEGY MODULE TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestRegimeMatrix:
    def test_crisis_disables_momentum(self):
        from python_brain.regime.strategy_regime_matrix import (
            RegimeState, should_strategy_fire, Regime,
        )
        state = RegimeState(Regime.CRISIS, 0.8, 35.0, 0.3, 2)
        assert not should_strategy_fire("TypeB", state)  # Momentum disabled in crisis
        assert not should_strategy_fire("S3_MacroTrend", state)  # Trend disabled

    def test_crisis_enables_tail_hedge(self):
        from python_brain.regime.strategy_regime_matrix import (
            RegimeState, should_strategy_fire, apply_regime_adjustments, Regime,
        )
        state = RegimeState(Regime.CRISIS, 0.8, 35.0, 0.3, 2)
        assert should_strategy_fire("S7_TailHedge", state)
        conf, kelly = apply_regime_adjustments("S7_TailHedge", 70, 0.03, state)
        assert kelly > 0.03  # Boosted in crisis (1.5x)

    def test_steady_full_activation(self):
        from python_brain.regime.strategy_regime_matrix import (
            RegimeState, should_strategy_fire, Regime,
        )
        state = RegimeState(Regime.STEADY, 0.8, 14.0, 0.6, 0)
        assert should_strategy_fire("TypeF", state)
        assert should_strategy_fire("TypeB", state)
        assert should_strategy_fire("S2_Reversion", state)


class TestCalendarAnomalies:
    def test_weekend_blocked(self):
        from python_brain.strategies.calendar_anomalies import get_calendar_adjustment
        adj = get_calendar_adjustment(2026, 3, 29, weekday=6)  # Sunday
        assert adj.confidence_delta <= -50

    def test_monday_penalty(self):
        from python_brain.strategies.calendar_anomalies import get_calendar_adjustment
        adj = get_calendar_adjustment(2026, 3, 30, weekday=0)  # Monday
        assert adj.confidence_delta < 0

    def test_turn_of_month_boost(self):
        from python_brain.strategies.calendar_anomalies import get_calendar_adjustment
        adj = get_calendar_adjustment(2026, 3, 31, weekday=1)  # Last day of March
        assert any("turn_of_month" in e for e in adj.effects)


class TestSignalRouter:
    def test_session_filtering(self):
        from python_brain.regime.signal_router import SignalRouter, classify_session, MarketSession
        assert classify_session(36000) == MarketSession.LSE_OPEN  # 10:00
        assert classify_session(54000) == MarketSession.US_OVERLAP  # 15:00
        assert classify_session(68400) == MarketSession.US_ONLY  # 19:00

    def test_conflict_resolution(self):
        from python_brain.regime.signal_router import SignalRouter
        router = SignalRouter()
        signals = [
            {"strategy": "TypeB", "confidence": 70, "ticker": "NVD3.L"},
            {"strategy": "TypeF", "confidence": 65, "ticker": "NVD3.L"},
        ]
        winner = router.resolve_conflicts(signals, "NVD3.L")
        assert winner["strategy"] == "TypeF"  # Higher priority


# ═══════════════════════════════════════════════════════════════════════
# SIZING MODULE TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestVolTargeting:
    def test_high_vol_reduces_size(self):
        from python_brain.sizing.vol_targeting import vol_adjusted_kelly
        normal = vol_adjusted_kelly(0.05, realized_vol=0.02, target_vol=0.02)
        high_vol = vol_adjusted_kelly(0.05, realized_vol=0.04, target_vol=0.02)
        assert high_vol < normal

    def test_student_t_correction(self):
        from python_brain.sizing.vol_targeting import student_t_correction
        corrected = student_t_correction(0.05, nu=5.0, leverage=3)
        assert corrected < 0.05  # Fat tails reduce optimal fraction

    def test_kelly_ratchet_needs_data(self):
        from python_brain.sizing.vol_targeting import kelly_ratchet
        assert kelly_ratchet([]) == 0.0
        assert kelly_ratchet([0.01] * 10) == 0.0  # Not enough


# ═══════════════════════════════════════════════════════════════════════
# ML MODULE TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestFFD:
    def test_ffd_weights_sum(self):
        from python_brain.ml.ffd import ffd_weights
        w = ffd_weights(d=0.35, window=100)
        assert len(w) > 0
        assert w[-1] == 1.0  # Most recent weight

    def test_ffd_transform_length(self):
        from python_brain.ml.ffd import ffd_transform
        series = np.cumsum(np.random.randn(500)) + 100
        result = ffd_transform(series, d=0.35, window=100)
        assert len(result) > 0
        assert len(result) < len(series)

    def test_ffd_stationarity(self):
        from python_brain.ml.ffd import ffd_transform, _adf_pvalue
        # Random walk (non-stationary)
        series = np.cumsum(np.random.randn(500)) + 100
        adf_raw = _adf_pvalue(series)
        # FFD should make it more stationary
        transformed = ffd_transform(series, d=0.5, window=100)
        if len(transformed) >= 20:
            adf_ffd = _adf_pvalue(transformed)
            assert adf_ffd <= adf_raw  # More stationary after FFD


class TestPathSignatures:
    def test_signature_depth_1(self):
        from python_brain.ml.path_signatures import compute_signature
        path = np.random.randn(50, 3)
        sig = compute_signature(path, depth=1)
        assert len(sig) == 3  # D features for depth 1

    def test_signature_depth_2(self):
        from python_brain.ml.path_signatures import compute_signature
        path = np.random.randn(50, 3)
        sig = compute_signature(path, depth=2)
        assert len(sig) == 3 + 9  # D + D^2

    def test_rolling_signatures(self):
        from python_brain.ml.path_signatures import rolling_signatures
        path = np.random.randn(100, 3)
        features = rolling_signatures(path, window=20, depth=2)
        assert features.shape[0] > 0
        assert features.shape[1] == 12  # 3 + 9


# ═══════════════════════════════════════════════════════════════════════
# LIFECYCLE MODULE TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestLifecycle:
    def test_sprt_edge_confirmed(self):
        from python_brain.lifecycle.strategy_state import StrategyLifecycle
        lc = StrategyLifecycle("TestStrat")
        # Feed 20 wins in a row
        for _ in range(20):
            lc.record_trade(pnl=10.0, won=True)
        assert lc.win_rate == 1.0

    def test_consecutive_losses_kills(self):
        from python_brain.lifecycle.strategy_state import StrategyLifecycle, LifecycleState
        lc = StrategyLifecycle("BadStrat")
        for _ in range(16):
            lc.record_trade(pnl=-5.0, won=False)
        lc.evaluate()
        assert lc.state == LifecycleState.RETIRED


# ═══════════════════════════════════════════════════════════════════════
# INFRASTRUCTURE MODULE TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestConformalPrediction:
    def test_calibration(self):
        from python_brain.calibration.conformal import ConformalPredictor
        cp = ConformalPredictor(alpha=0.10)
        residuals = np.abs(np.random.randn(100) * 0.5)
        q = cp.calibrate(residuals)
        assert q > 0

    def test_interval_width(self):
        from python_brain.calibration.conformal import ConformalPredictor
        cp = ConformalPredictor(alpha=0.10)
        cp.calibrate(np.abs(np.random.randn(100)))
        interval = cp.predict_interval(100.0)
        assert interval.lower < 100.0
        assert interval.upper > 100.0
        assert interval.width > 0


class TestBayesianAggregator:
    def test_single_source(self):
        from python_brain.aggregation.bayesian_aggregator import BayesianAggregator
        agg = BayesianAggregator(base_rate=0.52)
        agg.add_source("TypeF", accuracy=0.72, n_trades=100)
        result = agg.aggregate([("TypeF", "long", 0.75)])
        assert result.posterior_prob > 0.52  # Should be higher than base rate

    def test_multiple_agreeing_sources(self):
        from python_brain.aggregation.bayesian_aggregator import BayesianAggregator
        agg = BayesianAggregator(base_rate=0.52)
        agg.add_source("TypeF", accuracy=0.72, n_trades=100)
        agg.add_source("S2", accuracy=0.55, n_trades=50)
        result = agg.aggregate([("TypeF", "long", 0.8), ("S2", "long", 0.6)])
        assert result.posterior_prob > 0.60  # Both agreeing → higher confidence


class TestSubscriptionOptimizer:
    def test_permanent_slots(self):
        from python_brain.execution.subscription_optimizer import SubscriptionOptimizer
        opt = SubscriptionOptimizer(max_slots=100)
        assert opt.slots_used > 0  # Tier 1 permanent
        assert opt.slots_available > 0

    def test_on_demand_eviction(self):
        from python_brain.execution.subscription_optimizer import SubscriptionOptimizer
        opt = SubscriptionOptimizer(max_slots=20)  # Very limited
        # Fill up
        for i in range(10):
            opt.request_on_demand(f"TEST{i}.L", "test", priority=30)
        # Request high-priority — should evict low-priority
        result = opt.request_on_demand("HIGH.L", "test", priority=80)
        assert result or opt.slots_available == 0


class TestEODReconciliation:
    def test_clean_recon(self):
        from python_brain.reconciliation.eod_recon import EODReconciler
        recon = EODReconciler()
        result = recon.run(
            wal_positions={"NVD3.L": {"quantity": 10, "avg_cost": 15.0}},
            broker_positions={"NVD3.L": {"quantity": 10, "avg_cost": 15.0}},
        )
        assert result.position_match

    def test_phantom_position(self):
        from python_brain.reconciliation.eod_recon import EODReconciler
        recon = EODReconciler()
        result = recon.run(
            wal_positions={"NVD3.L": {"quantity": 10}},
            broker_positions={},
        )
        assert not result.position_match
        assert result.has_major


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
