"""
tests/test_paper_launch.py
==========================
Comprehensive test suite for NZT-48 Paper Launch.

Tests:
  1. System watchdog state machine
  2. Quality gate enforcement
  3. Data reliability scoring
  4. Gate funnel correctness
  5. Constitution enforcement
  6. RVOL N/A handling
  7. Regime contradiction detection
  8. Artifact writing (atomic)
  9. Pipeline runner smoke test
  10. Scheduled jobs structure
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


# ============================================================
# 1. System Watchdog Tests
# ============================================================

class TestSystemWatchdog:
    def test_initial_state_is_ok(self):
        from system_watchdog import SystemWatchdog, SystemState
        wd = SystemWatchdog()
        wd.record_tick()
        wd.record_data_fetch()
        report = wd.check_state(tick_count=10)
        assert report.state == SystemState.OK

    def test_kill_switch_halts(self):
        from system_watchdog import SystemWatchdog, SystemState
        wd = SystemWatchdog()
        wd.record_tick()
        report = wd.check_state(kill_switch_active=True)
        assert report.state == SystemState.HALTED
        assert "KILL_SWITCH_ACTIVE" in report.reasons

    def test_daily_loss_not_checked_by_watchdog(self):
        """AEGIS F-08: Daily loss is governed solely by L1/L2/L3 cascade
        in circuit_breakers.py. The watchdog should NOT halt on daily loss."""
        from system_watchdog import SystemWatchdog, SystemState
        wd = SystemWatchdog()
        wd.record_tick()
        wd.record_data_fetch()
        report = wd.check_state(daily_loss_pct=-0.04)  # 4% loss
        # Watchdog should NOT halt — circuit_breakers.py handles daily loss
        assert report.state == SystemState.OK
        assert not any("MAX_DAILY_LOSS" in r for r in report.reasons)

    def test_consecutive_losses_halt(self):
        from system_watchdog import SystemWatchdog, SystemState
        wd = SystemWatchdog()
        wd.record_tick()
        wd.record_data_fetch()
        report = wd.check_state(consecutive_losses=5)
        assert report.state == SystemState.HALTED
        assert any("CONSECUTIVE_LOSSES" in r for r in report.reasons)

    def test_excess_positions_degrades(self):
        from system_watchdog import SystemWatchdog, SystemState
        wd = SystemWatchdog()
        wd.record_tick()
        wd.record_data_fetch()
        report = wd.check_state(open_positions=5)  # > 3
        assert report.state == SystemState.DEGRADED

    def test_report_serializes(self):
        from system_watchdog import SystemWatchdog
        wd = SystemWatchdog()
        wd.record_tick()
        wd.record_data_fetch()
        report = wd.check_state()
        d = report.to_dict()
        assert "state" in d
        assert "mode" in d
        assert d["mode"] == os.environ.get("NZT48_MODE", "PAPER")
        # Should be JSON-serializable
        json.dumps(d)

    def test_git_hash_and_config_hash(self):
        from system_watchdog import SystemWatchdog
        wd = SystemWatchdog()
        wd.record_tick()
        report = wd.check_state()
        # Should have some value (even if "unknown")
        assert report.git_hash
        assert report.config_hash


# ============================================================
# 2. Quality Gate Tests
# ============================================================

class TestQualityGate:
    def _make_play(self, **kwargs):
        """Create a mock play object."""
        play = MagicMock()
        play.ticker = kwargs.get("ticker", "QQQ3.L")
        play.direction = kwargs.get("direction", "LONG")
        play.rvol = kwargs.get("rvol", 1.5)
        play.label = kwargs.get("label", "STRICT")
        play.execution_plan = kwargs.get("execution_plan", {"order_type": "LIMIT"})
        play.risk_officer_decision = kwargs.get("risk_officer_decision", "APPROVE")
        return play

    def test_clean_plays_pass(self):
        from system_watchdog import run_quality_gate
        plays = [self._make_play(), self._make_play(ticker="3LUS.L")]
        report = run_quality_gate(plays, regime="NEUTRAL", features_map={})
        assert report.passed
        assert report.checks_run == 2
        assert len(report.violations) == 0

    def test_rvol_zero_detected(self):
        from system_watchdog import run_quality_gate
        plays = [self._make_play(rvol=0.0)]
        report = run_quality_gate(plays, regime="NEUTRAL", features_map={})
        assert not report.passed
        assert report.rvol_placeholder_errors == 1

    def test_regime_contradiction_detected(self):
        from system_watchdog import run_quality_gate
        plays = [self._make_play(direction="LONG", label="STRICT")]
        report = run_quality_gate(plays, regime="RISK_OFF", features_map={})
        assert not report.passed
        assert report.regime_contradictions == 1

    def test_missing_execution_plan(self):
        from system_watchdog import run_quality_gate
        plays = [self._make_play(execution_plan=None)]
        report = run_quality_gate(plays, regime="NEUTRAL", features_map={})
        assert not report.passed
        assert report.missing_execution_plans == 1

    def test_watch_label_exempted_from_regime_check(self):
        from system_watchdog import run_quality_gate
        plays = [self._make_play(direction="LONG", label="WATCH")]
        report = run_quality_gate(plays, regime="RISK_OFF", features_map={})
        # WATCH should not trigger regime contradiction
        assert report.regime_contradictions == 0

    def test_report_serializes(self):
        from system_watchdog import run_quality_gate
        plays = [self._make_play()]
        report = run_quality_gate(plays, regime="NEUTRAL", features_map={})
        d = report.to_dict()
        json.dumps(d)
        assert "quality_passed" in d


# ============================================================
# 3. Data Reliability Tests
# ============================================================

class TestDataReliability:
    def test_empty_input(self):
        from system_watchdog import compute_data_reliability
        report = compute_data_reliability(None, {})
        assert report.score >= 0.0
        assert report.tickers_checked == 0

    def test_all_pass(self):
        from system_watchdog import compute_data_reliability
        health = MagicMock()
        r1 = MagicMock()
        r1.status = "PASS"
        r2 = MagicMock()
        r2.status = "PASS"
        health.results = {"QQQ3.L": r1, "3LUS.L": r2}

        feat1 = MagicMock()
        feat1.rvol = 1.5
        feat1.short_window = False
        feat1.reliability_penalty = 0.0
        feat2 = MagicMock()
        feat2.rvol = 2.0
        feat2.short_window = False
        feat2.reliability_penalty = 0.0

        report = compute_data_reliability(health, {"QQQ3.L": feat1, "3LUS.L": feat2})
        assert report.score > 0.8
        assert report.tickers_passed == 2
        assert report.rvol_available == 2

    def test_rvol_na_counted(self):
        from system_watchdog import compute_data_reliability
        feat = MagicMock()
        feat.rvol = None
        feat.short_window = False
        feat.reliability_penalty = 0.0
        report = compute_data_reliability(None, {"QQQ3.L": feat})
        assert report.rvol_na == 1

    def test_report_serializes(self):
        from system_watchdog import compute_data_reliability
        report = compute_data_reliability(None, {})
        d = report.to_dict()
        json.dumps(d)
        assert "data_reliability_score" in d


# ============================================================
# 4. Gate Funnel Tests
# ============================================================

class TestGates:
    def test_data_health_pass(self):
        from signal_engine.gates import gate_data_health, GateResult
        hr = MagicMock()
        hr.status = "PASS"
        outcome = gate_data_health("QQQ3.L", hr)
        assert outcome.result == GateResult.PASS

    def test_data_health_fail(self):
        from signal_engine.gates import gate_data_health, GateResult
        hr = MagicMock()
        hr.status = "FAIL"
        hr.exceptions = ["no data"]
        outcome = gate_data_health("QQQ3.L", hr)
        assert outcome.result == GateResult.FAIL

    def test_data_health_none(self):
        from signal_engine.gates import gate_data_health, GateResult
        outcome = gate_data_health("QQQ3.L", None)
        assert outcome.result == GateResult.FAIL

    def test_price_scale_pence_detection(self):
        from signal_engine.gates import gate_price_scale, GateResult
        # 18000 for a .L ticker looks like pence
        outcome = gate_price_scale(18000.0, "QQQ3.L")
        assert outcome.result == GateResult.FAIL

    def test_price_scale_normal(self):
        from signal_engine.gates import gate_price_scale, GateResult
        outcome = gate_price_scale(45.50, "QQQ3.L")
        assert outcome.result == GateResult.PASS

    def test_min_bars_short_window(self):
        from signal_engine.gates import gate_min_bars, GateResult
        outcome = gate_min_bars(10)  # 7-13 = SHORT_WINDOW
        assert outcome.result == GateResult.RELAXED
        assert "SHORT_WINDOW" in outcome.reason

    def test_min_bars_too_few(self):
        from signal_engine.gates import gate_min_bars, GateResult
        outcome = gate_min_bars(5)
        assert outcome.result == GateResult.FAIL

    def test_rvol_none_is_relaxed_not_fail(self):
        from signal_engine.gates import gate_volume_liquidity, GateResult
        outcome = gate_volume_liquidity(None)
        assert outcome.result == GateResult.RELAXED
        assert "N/A" in outcome.reason

    def test_regime_fit_risk_off_relaxes_long(self):
        """ISA buy-only: regime gate RELAXES (not FAIL) for LONG in RISK_OFF."""
        from signal_engine.gates import gate_regime_fit, GateResult
        outcome = gate_regime_fit("LONG", "RISK_OFF")
        assert outcome.result == GateResult.RELAXED

    def test_factor_cap(self):
        from signal_engine.gates import gate_factor_cap, GateResult
        outcome = gate_factor_cap("semiconductor", {"semiconductor": 3})
        assert outcome.result == GateResult.FAIL

    def test_full_funnel_strict(self):
        from signal_engine.gates import run_full_gate_funnel
        hr = MagicMock()
        hr.status = "PASS"
        report = run_full_gate_funnel(
            ticker="QQQ3.L",
            direction="LONG",
            atr_pct=2.5,
            close=45.0,
            n_bars=20,
            rvol=1.5,
            rr=2.0,
            momentum_score=0.7,
            regime="NEUTRAL",
            factor_group="index_leverage",
            group_counts={},
            health_result=hr,
            fallback_step=0,
        )
        assert report.all_passed


# ============================================================
# 5. Artifact Writing Tests
# ============================================================

class TestArtifacts:
    def test_watchdog_artifacts_write(self, tmp_path):
        from system_watchdog import (
            SystemStateReport, DataReliabilityReport, QualityReport,
            write_watchdog_artifacts, SystemState,
        )
        # Monkey-patch ARTIFACTS_ROOT
        import system_watchdog
        original_root = system_watchdog.ARTIFACTS_ROOT
        system_watchdog.ARTIFACTS_ROOT = tmp_path

        try:
            state = SystemStateReport(state=SystemState.OK)
            reliability = DataReliabilityReport(score=0.85)
            quality = QualityReport(passed=True)

            paths = write_watchdog_artifacts("test_session", state, reliability, quality)
            assert "system_state.json" in paths
            assert "reliability.json" in paths
            assert "quality_report.json" in paths
            assert "readiness.json" in paths

            # Verify files exist and are valid JSON
            for name, path in paths.items():
                assert Path(path).exists()
                with open(path) as f:
                    data = json.load(f)
                assert isinstance(data, dict)
        finally:
            system_watchdog.ARTIFACTS_ROOT = original_root


# ============================================================
# 6. Constitution Tests
# ============================================================

class TestConstitution:
    def test_risk_per_trade_limit(self):
        """Verify 0.75% risk per trade is configured."""
        import yaml
        config_path = _ROOT / "config" / "settings.yaml"
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            assert cfg["immutable_rules"]["risk_per_trade"] == 0.0075

    def test_max_daily_loss_limit(self):
        """Verify 3% max daily loss."""
        import yaml
        config_path = _ROOT / "config" / "settings.yaml"
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            assert cfg["immutable_rules"]["max_daily_loss"] == 0.03

    def test_paper_mode_configured(self):
        """Verify PAPER mode in settings."""
        import yaml
        config_path = _ROOT / "config" / "settings.yaml"
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            assert cfg["system"]["mode"] == "PAPER"


# ============================================================
# 7. Scoring & RVOL Tests
# ============================================================

class TestScoring:
    def test_rvol_none_not_zero(self):
        """Engine must return rvol=None (not 0.0) when volume unreliable."""
        # This tests the contract in engine.py _build_features
        # rvol should be None when vol_s.sum() == 0
        # The QualityGate will catch any 0.00 that slips through
        from system_watchdog import run_quality_gate
        play = MagicMock()
        play.ticker = "TEST.L"
        play.direction = "LONG"
        play.rvol = 0.0  # BAD — should be None
        play.label = "STRICT"
        play.execution_plan = {"order_type": "LIMIT"}
        play.risk_officer_decision = "APPROVE"

        report = run_quality_gate([play], regime="NEUTRAL", features_map={})
        assert report.rvol_placeholder_errors == 1


# ============================================================
# 8. Scheduled Jobs Tests
# ============================================================

class TestScheduledJobs:
    def test_pdf_type_mapping(self):
        from scheduled_jobs import _get_pdf_types_for_session
        assert _get_pdf_types_for_session("PRE_LSE") == ["momentum"]
        assert _get_pdf_types_for_session("PRE_NYSE") == ["momentum"]
        assert "momentum" in _get_pdf_types_for_session("EOD_INSTITUTIONAL")
        assert "risk" in _get_pdf_types_for_session("EOD_INSTITUTIONAL")
        assert "review" in _get_pdf_types_for_session("EOD_INSTITUTIONAL")

    def test_telegram_caption_format(self):
        from scheduled_jobs import _build_telegram_caption
        summary = {
            "pipeline": {
                "core_plays": 5,
                "strict_count": 3,
                "fallback_count": 2,
                "peer_plays": 4,
                "full_scan_cards": 10,
                "drought": False,
            },
            "watchdog": {
                "system_state": "OK",
                "data_reliability_score": 0.85,
            },
        }
        caption = _build_telegram_caption("PRE_LSE", summary, "momentum", False)
        assert "NZT-48" in caption
        assert "MOMENTUM" in caption
        assert "PRE_LSE" in caption
        assert "OK" in caption


# ============================================================
# 9. Kill Switch Tests
# ============================================================

class TestKillSwitch:
    def test_kill_switch_file_based(self, tmp_path):
        from delivery.telegram_bot import KillSwitch
        ks = KillSwitch()
        # Override KILL_FILE path
        original = KillSwitch.KILL_FILE
        KillSwitch.KILL_FILE = str(tmp_path / "KILL_SWITCH")
        try:
            assert not ks.is_killed()
            ks.activate("test")
            assert ks.is_killed()
            ks.deactivate()
            assert not ks.is_killed()
        finally:
            KillSwitch.KILL_FILE = original


# ============================================================
# Run
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
