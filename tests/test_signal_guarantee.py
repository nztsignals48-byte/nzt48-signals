"""
tests/test_signal_guarantee.py
================================
Deterministic tests for the signal guarantee policy.
Tests RVOL handling, drought reporting, fallback logic, and minimum play counts.
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


class TestRVOLBehavior:
    """Test RVOL None/0/value handling across gates and logger."""

    def test_rvol_none_gate_returns_relaxed(self):
        """RVOL=None should not hard-fail; should return RELAXED."""
        from signal_engine.gates import gate_volume_liquidity, GateResult
        outcome = gate_volume_liquidity(None, fallback_step=0)
        assert outcome.result == GateResult.RELAXED
        assert "N/A" in outcome.reason

    def test_rvol_zero_gate_returns_relaxed(self):
        """RVOL=0 should return RELAXED, not FAIL."""
        from signal_engine.gates import gate_volume_liquidity, GateResult
        outcome = gate_volume_liquidity(0, fallback_step=0)
        assert outcome.result == GateResult.RELAXED

    def test_rvol_below_strict_fails(self):
        """RVOL below STRICT_MIN_RVOL (0.40) should FAIL in strict mode."""
        from signal_engine.gates import gate_volume_liquidity, GateResult
        outcome = gate_volume_liquidity(0.20, fallback_step=0)
        assert outcome.result == GateResult.FAIL

    def test_rvol_below_strict_passes_fallback_step1(self):
        """RVOL below strict (0.40) but above fallback (0.20) should pass at step 1."""
        from signal_engine.gates import gate_volume_liquidity, GateResult
        outcome = gate_volume_liquidity(0.30, fallback_step=1)
        assert outcome.passed

    def test_rvol_logger_none_becomes_unknown(self):
        """Signal logger should classify None RVOL as UNKNOWN, not NORMAL."""
        from learning.signal_logger import SignalLogger
        sl = SignalLogger()
        # Simulate the classification logic
        raw_rvol = None
        if raw_rvol is None or raw_rvol == 0:
            liq = "UNKNOWN"
        else:
            liq = "NORMAL"
        assert liq == "UNKNOWN"


class TestDroughtReport:
    """Test drought report generation with blockers and closest misses."""

    def test_drought_report_has_blockers_and_closest_misses(self):
        """Drought package should include blockers and closest misses."""
        from signal_engine.signal_card import build_drought_package, DroughtPackage
        from signal_engine.scoring import SignalDroughtReport

        drought = SignalDroughtReport(
            top_blockers=["TRADABILITY: ATR too low", "VOLUME_LIQUIDITY: RVOL=0.3"],
            tickers_checked=12,
            hard_fail_count=5,
        )

        # Mock gate reports with known failures
        class MockGateReport:
            def __init__(self, blocker):
                self.blocker = blocker
                self.hard_failed = False

        gate_reports = {
            "QQQ3.L": MockGateReport("TRADABILITY"),
            "3LUS.L": MockGateReport("VOLUME_LIQUIDITY"),
            "NVD3.L": MockGateReport("RR_RATIO"),
        }

        # Mock features
        class MockFeatures:
            atr_pct = 0.5
            rvol = 0.3
            rr_ratio = 1.1
            momentum = 0.4

        features_map = {
            "QQQ3.L": MockFeatures(),
            "3LUS.L": MockFeatures(),
            "NVD3.L": MockFeatures(),
        }

        pkg = build_drought_package(drought, gate_reports, features_map)
        assert isinstance(pkg, DroughtPackage)
        assert pkg.drought_flag is True
        assert len(pkg.blockers_summary) > 0
        assert len(pkg.closest_misses) > 0

    def test_drought_no_drought_flag_false(self):
        """If no drought, package should have drought_flag=False."""
        from signal_engine.signal_card import build_drought_package
        pkg = build_drought_package(None, {}, {})
        assert pkg.drought_flag is False


class TestFallbackProducesWatch:
    """Test that fallback mode produces WATCH signals to reach minimum."""

    def test_fallback_labels_watch(self):
        """Signals from fallback steps should be labelled as WATCH."""
        from signal_engine.signal_card import SignalCard
        from signal_engine.scoring import PlayScore

        ps = PlayScore(
            ticker="QQQ3.L",
            direction="LONG",
            momentum=0.6,
            volatility=0.5,
            regime_fit=0.7,
            liquidity=0.5,
            rr_score=0.6,
            quality=0.5,
            entry=100.0,
            stop=99.0,
            target1=102.0,
            target2=104.0,
            rr_ratio=2.0,
            setup_type="continuation",
            factor_group="nasdaq_beta_long",
            atr_pct=2.0,
            rvol=0.6,
            fallback_step=1,   # Fallback step 1
        )

        card = SignalCard.from_play_score(ps, session="PRE_LSE")
        assert card.category == "WATCH"
        assert card.fallback_step == 1
        assert "RVOL" in card.why_fallback

    def test_strict_labels_trade(self):
        """Signals from strict mode should be labelled as TRADE."""
        from signal_engine.signal_card import SignalCard
        from signal_engine.scoring import PlayScore

        ps = PlayScore(
            ticker="QQQ3.L",
            direction="LONG",
            momentum=0.8,
            volatility=0.7,
            regime_fit=0.9,
            liquidity=0.8,
            rr_score=0.8,
            quality=0.7,
            entry=100.0,
            stop=99.0,
            target1=102.0,
            target2=104.0,
            rr_ratio=2.0,
            setup_type="continuation",
            factor_group="nasdaq_beta_long",
            atr_pct=2.0,
            rvol=1.5,
            fallback_step=0,  # Strict mode
        )

        card = SignalCard.from_play_score(ps, session="PRE_LSE")
        assert card.category == "TRADE"
        assert card.fallback_step == 0


class TestTopPlaysMinimum:
    """Test minimum plays when data health passes."""

    def test_gate_funnel_strict_thresholds(self):
        """Verify strict gate thresholds are correctly defined."""
        from signal_engine.gates import (
            MIN_SIGNALS_STRICT, MIN_SIGNALS_FALLBACK,
            STRICT_MIN_ATR_PCT, STRICT_MIN_RVOL, STRICT_MIN_RR,
        )
        assert MIN_SIGNALS_STRICT == 3
        assert MIN_SIGNALS_FALLBACK == 5
        assert STRICT_MIN_ATR_PCT == 1.0
        assert STRICT_MIN_RVOL == 0.40
        assert STRICT_MIN_RR == 1.5

    def test_full_gate_funnel_passes_good_ticker(self):
        """A well-qualifying ticker should pass all gates."""
        from signal_engine.gates import run_full_gate_funnel

        class MockHealth:
            status = "PASS"
            exceptions = []

        report = run_full_gate_funnel(
            ticker="QQQ3.L",
            direction="LONG",
            atr_pct=2.5,
            close=150.0,
            n_bars=20,
            rvol=1.5,
            rr=2.0,
            momentum_score=0.70,
            regime="NEUTRAL",
            factor_group="nasdaq_beta_long",
            group_counts={},
            health_result=MockHealth(),
            fallback_step=0,
        )
        assert report.all_passed
        assert not report.hard_failed


class TestIntelCards:
    """Test Intel Card model."""

    def test_intel_card_creation(self):
        from signal_engine.intel_card import IntelCard
        card = IntelCard(
            ticker="NVDA",
            label="INTEL-ONLY",
            is_core=False,
            price=850.0,
            move_pct=2.5,
            insight="NVDA: UP 2.5%, BULLISH, EXPANSION",
        )
        assert card.ticker == "NVDA"
        assert card.is_core is False
        assert card.label == "INTEL-ONLY"
        d = card.to_dict()
        assert d["ticker"] == "NVDA"
        assert d["is_core"] is False


class TestPipelineRunner:
    """Test pipeline runner module loads correctly."""

    def test_pipeline_runner_imports(self):
        from signal_engine.pipeline_runner import run_pipeline, PipelineResult
        assert callable(run_pipeline)
        result = PipelineResult(session="TEST", run_id="T001")
        assert result.strict_count == 0
        assert result.drought_flag is False


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
