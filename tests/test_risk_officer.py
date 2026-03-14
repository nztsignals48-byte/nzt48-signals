"""
tests/test_risk_officer.py
===========================
Unit tests for the RiskOfficer governance layer and its constituent rules.
Uses MagicMock for SignalCard and RouterResult objects.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock, patch

from risk_officer.officer import (
    RiskOfficer,
    RiskDecision,
    RiskOfficerReport,
    APPROVE,
    DOWNSIZE,
    VETO,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_card(**overrides) -> MagicMock:
    """Build a MagicMock SignalCard with sensible defaults."""
    defaults = {
        "ticker": "QQQ3.L",
        "direction": "LONG",
        "sizing_hint": "M",
        "atr_pct": 1.5,
        "rvol": 1.2,
        "factor_group": "tech_leverage",
        "data_reliability": 0.95,
        "short_window": False,
        "fallback_step": 0,
        "bars_available": 60,
        "execution_plan": {"spread_proxy_bps": 8.0},
    }
    defaults.update(overrides)
    card = MagicMock()
    for k, v in defaults.items():
        setattr(card, k, v)
    return card


def _make_router(**overrides) -> MagicMock:
    """Build a MagicMock RouterResult with sensible defaults."""
    defaults = {
        "kill_switch": False,
        "sizing_mode": "NORMAL",
        "max_factor_cap": 3,
    }
    defaults.update(overrides)
    router = MagicMock()
    for k, v in defaults.items():
        setattr(router, k, v)
    return router


# ---------------------------------------------------------------------------
# 1. RiskOfficer instantiation
# ---------------------------------------------------------------------------

class TestRiskOfficerInstantiation:
    def test_instantiates_without_error(self):
        officer = RiskOfficer()
        assert officer is not None
        assert hasattr(officer, "_stateless_rules")
        assert hasattr(officer, "_correlation_rule")
        assert len(officer._stateless_rules) == 5


# ---------------------------------------------------------------------------
# 2-3. VolShockRule
# ---------------------------------------------------------------------------

class TestVolShockRule:
    def test_vix_above_35_and_high_atr_produces_veto(self):
        """VIX > 35 + ATR% > 3.5% => VETO."""
        officer = RiskOfficer()
        card = _make_card(atr_pct=4.0)
        router = _make_router()
        context = {"vix": 40.0}
        results = officer.evaluate([card], router, {card.ticker: None}, context)

        assert len(results) == 1
        _, decision = results[0]
        assert decision.decision == VETO
        assert any("VIX" in r for r in decision.reasons)
        assert decision.risk_score >= 0.90

    def test_vix_below_20_no_trigger(self):
        """VIX < 20 with normal ATR should not trigger vol shock."""
        officer = RiskOfficer()
        card = _make_card(atr_pct=1.0)
        router = _make_router()
        context = {"vix": 15.0}
        results = officer.evaluate([card], router, {card.ticker: None}, context)

        _, decision = results[0]
        # Vol shock rule should not have contributed a VETO or DOWNSIZE
        vol_checks = [r for r in decision.rules_checked if r.startswith("VOL_SHOCK")]
        assert any("pass" in r for r in vol_checks)

    def test_vix_downsize_band(self):
        """VIX 25-35 + ATR% > 2.5% => DOWNSIZE (at minimum)."""
        officer = RiskOfficer()
        card = _make_card(atr_pct=3.0)
        router = _make_router()
        context = {"vix": 30.0}
        results = officer.evaluate([card], router, {card.ticker: None}, context)

        _, decision = results[0]
        assert decision.decision in (DOWNSIZE, VETO)  # could escalate from other rules


# ---------------------------------------------------------------------------
# 4-5. DrawdownRule
# ---------------------------------------------------------------------------

class TestDrawdownRule:
    def test_consecutive_losses_5_triggers_veto(self):
        """5 consecutive losses should trigger VETO."""
        officer = RiskOfficer()
        card = _make_card()
        router = _make_router()
        context = {"consecutive_losses": 5, "vix": 15.0}
        results = officer.evaluate([card], router, {card.ticker: None}, context)

        _, decision = results[0]
        assert decision.decision == VETO
        assert any("Consecutive losses" in r for r in decision.reasons)

    def test_zero_losses_no_trigger(self):
        """0 consecutive losses should not trigger drawdown rule."""
        officer = RiskOfficer()
        card = _make_card()
        router = _make_router()
        context = {"consecutive_losses": 0, "vix": 15.0}
        results = officer.evaluate([card], router, {card.ticker: None}, context)

        _, decision = results[0]
        drawdown_checks = [r for r in decision.rules_checked if r.startswith("DRAWDOWN")]
        assert any("pass" in r for r in drawdown_checks)

    def test_consecutive_losses_3_triggers_downsize(self):
        """3 consecutive losses (but < 5) should trigger DOWNSIZE."""
        officer = RiskOfficer()
        card = _make_card()
        router = _make_router()
        context = {"consecutive_losses": 3, "vix": 15.0}
        results = officer.evaluate([card], router, {card.ticker: None}, context)

        _, decision = results[0]
        assert decision.decision in (DOWNSIZE, VETO)
        assert any("Consecutive losses" in r or "reduce size" in r for r in decision.reasons)


# ---------------------------------------------------------------------------
# 6. CorrelationRule
# ---------------------------------------------------------------------------

class TestCorrelationRule:
    def test_factor_group_overload_triggers_downsize_then_veto(self):
        """3+ signals in same factor_group should get limited."""
        officer = RiskOfficer()
        cards = [
            _make_card(ticker="QQQ3.L", factor_group="tech_leverage"),
            _make_card(ticker="3LUS.L", factor_group="tech_leverage"),
            _make_card(ticker="NVD3.L", factor_group="tech_leverage"),
            _make_card(ticker="TSL3.L", factor_group="tech_leverage"),
        ]
        router = _make_router(max_factor_cap=3)
        features_map = {c.ticker: None for c in cards}
        context = {"vix": 15.0}
        results = officer.evaluate(cards, router, features_map, context)

        # First two should not be limited by correlation rule
        # Third (count == max_cap=3) should get DOWNSIZE from correlation
        # Fourth (count > max_cap=3) should get VETO from correlation
        decisions = [d.decision for _, d in results]

        # The 3rd card should be at least DOWNSIZEd
        assert decisions[2] in (DOWNSIZE, VETO)

        # The 4th card should be VETOed by correlation
        assert decisions[3] == VETO
        _, dec4 = results[3]
        assert any("Factor overload" in r for r in dec4.reasons)


# ---------------------------------------------------------------------------
# 7-8. DataReliabilityRule
# ---------------------------------------------------------------------------

class TestDataReliabilityRule:
    def test_low_reliability_triggers_veto(self):
        """data_reliability < 0.50 should produce VETO."""
        officer = RiskOfficer()
        card = _make_card(data_reliability=0.30)
        router = _make_router()
        context = {"vix": 15.0}
        results = officer.evaluate([card], router, {card.ticker: None}, context)

        _, decision = results[0]
        assert decision.decision == VETO
        assert any("data_reliability" in r for r in decision.reasons)

    def test_high_reliability_passes(self):
        """data_reliability >= 0.70 should not trigger the rule."""
        officer = RiskOfficer()
        card = _make_card(data_reliability=0.95)
        router = _make_router()
        context = {"vix": 15.0}
        results = officer.evaluate([card], router, {card.ticker: None}, context)

        _, decision = results[0]
        data_checks = [r for r in decision.rules_checked if r.startswith("DATA_RELIABILITY")]
        assert any("pass" in r for r in data_checks)


# ---------------------------------------------------------------------------
# 9. LiquidityRule
# ---------------------------------------------------------------------------

class TestLiquidityRule:
    def test_very_low_rvol_flags(self):
        """RVOL < 0.40 should trigger VETO for insufficient liquidity."""
        officer = RiskOfficer()
        card = _make_card(rvol=0.20)
        router = _make_router()
        context = {"vix": 15.0}
        results = officer.evaluate([card], router, {card.ticker: None}, context)

        _, decision = results[0]
        assert decision.decision == VETO
        assert any("RVOL" in r or "liquidity" in r.lower() for r in decision.reasons)

    def test_normal_rvol_passes(self):
        """RVOL > 0.60 with normal spread should not trigger liquidity rule."""
        officer = RiskOfficer()
        card = _make_card(rvol=1.5, execution_plan={"spread_proxy_bps": 5.0})
        router = _make_router()
        context = {"vix": 15.0}
        results = officer.evaluate([card], router, {card.ticker: None}, context)

        _, decision = results[0]
        liquidity_checks = [r for r in decision.rules_checked if r.startswith("LIQUIDITY")]
        assert any("pass" in r for r in liquidity_checks)


# ---------------------------------------------------------------------------
# 10. Overall evaluate method
# ---------------------------------------------------------------------------

class TestEvaluateMethod:
    def test_evaluate_returns_card_decision_tuples(self):
        """evaluate() must return a list of (card, RiskDecision) tuples."""
        officer = RiskOfficer()
        cards = [_make_card(ticker="QQQ3.L"), _make_card(ticker="3LUS.L")]
        router = _make_router()
        features_map = {c.ticker: None for c in cards}
        context = {"vix": 15.0}

        results = officer.evaluate(cards, router, features_map, context)

        assert len(results) == 2
        for card_out, decision in results:
            assert isinstance(decision, RiskDecision)
            assert decision.decision in (APPROVE, DOWNSIZE, VETO)
            assert isinstance(decision.reasons, list)
            assert isinstance(decision.rules_checked, list)
            assert isinstance(decision.risk_score, float)
            assert 0.0 <= decision.risk_score <= 1.0


# ---------------------------------------------------------------------------
# 11. Report generation
# ---------------------------------------------------------------------------

class TestReportGeneration:
    def test_build_report_returns_report(self):
        """build_report should return a RiskOfficerReport with correct counts."""
        officer = RiskOfficer()
        cards = [
            _make_card(ticker="QQQ3.L"),
            _make_card(ticker="3LUS.L", data_reliability=0.30),  # will get VETOed
        ]
        router = _make_router()
        features_map = {c.ticker: None for c in cards}
        context = {"vix": 15.0}

        evaluated = officer.evaluate(cards, router, features_map, context)
        report = officer.build_report("TEST_SESSION", evaluated)

        assert isinstance(report, RiskOfficerReport)
        assert report.session == "TEST_SESSION"
        assert report.generated_at  # not empty
        assert len(report.decisions) == 2
        assert report.veto_count + report.downsize_count + report.approve_count == 2
        assert report.veto_count >= 1  # the low-reliability card

        # Verify to_dict works
        report_dict = report.to_dict()
        assert isinstance(report_dict, dict)
        assert "decisions" in report_dict


# ---------------------------------------------------------------------------
# 12. APPROVE default with clean data
# ---------------------------------------------------------------------------

class TestApproveDefault:
    def test_clean_card_gets_approved(self):
        """A card with no risk flags should get APPROVE."""
        officer = RiskOfficer()
        card = _make_card(
            atr_pct=1.0,
            rvol=1.5,
            data_reliability=0.95,
            factor_group="unique_group",
            execution_plan={"spread_proxy_bps": 5.0},
        )
        router = _make_router()
        context = {"vix": 15.0, "consecutive_losses": 0}
        results = officer.evaluate([card], router, {card.ticker: None}, context)

        _, decision = results[0]
        assert decision.decision == APPROVE
        assert decision.reasons == []
        assert decision.final_sizing == card.sizing_hint
        assert decision.risk_score == 0.0
