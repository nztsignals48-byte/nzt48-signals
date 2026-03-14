"""
tests/test_strategy_router.py
===============================
Unit tests for signal_engine/strategy_router.py — StrategyRouter, RouterResult.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock, patch

from signal_engine.strategy_router import StrategyRouter, RouterResult, StrategySpec


# ── Import works without error ────────────────────────────────────────────────

def test_strategy_router_import():
    """Module imports without error and exposes key classes."""
    import signal_engine.strategy_router as mod
    assert hasattr(mod, "StrategyRouter")
    assert hasattr(mod, "RouterResult")
    assert hasattr(mod, "StrategySpec")
    assert hasattr(mod, "get_router")
    assert hasattr(mod, "get_time_of_day_window")


# ── StrategyRouter instantiation ─────────────────────────────────────────────

def test_strategy_router_instantiation():
    """StrategyRouter can be instantiated without errors."""
    router = StrategyRouter()
    assert router is not None
    assert hasattr(router, "run")
    assert router._artifacts_root.name == "artifacts"


# ── RouterResult has required fields ─────────────────────────────────────────

def test_router_result_has_required_fields():
    """RouterResult dataclass contains active_strategies, sizing_mode, score_boost, allocation_weights."""
    result = RouterResult(
        regime_tag="TRENDING_UP_STRONG",
        regime_confidence=0.85,
        time_of_day_window="MORNING_MOMENTUM",
        active_strategies=[],
        overlay_tags=[],
        overlay_warnings=[],
        sizing_mode="NORMAL",
        score_boost=0.15,
        allocation_weights={"TREND_MOMENTUM_CTA": 0.6, "MOMENTUM_BREAKOUT": 0.4},
    )
    assert result.active_strategies == []
    assert result.sizing_mode == "NORMAL"
    assert result.score_boost == 0.15
    assert result.allocation_weights == {"TREND_MOMENTUM_CTA": 0.6, "MOMENTUM_BREAKOUT": 0.4}
    assert result.kill_switch is False  # default


# ── Weight normalization ─────────────────────────────────────────────────────

def test_weight_normalization():
    """When allocation_weights sum != 1.0, run() normalizes them to sum to 1.0."""
    router = StrategyRouter()
    result = router.run(
        regime="BULLISH",
        session="LSE",
        vix_level=18.0,
        spx_5d_return=1.5,
        hour_uk=9,
        minute_uk=0,
        isa_data={},
        write_artifact=False,
    )
    weights = result.allocation_weights
    if weights:
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.02, (
            f"Allocation weights should sum to ~1.0, got {total}"
        )


# ── Kill switch blocks strategies ────────────────────────────────────────────

def test_kill_switch_blocks_strategies():
    """SHOCK regime or VIX > 45 triggers kill_switch=True and DEFENSIVE sizing."""
    router = StrategyRouter()
    result = router.run(
        regime="SHOCK",
        session="LSE",
        vix_level=50.0,
        spx_5d_return=-5.0,
        hour_uk=10,
        minute_uk=0,
        isa_data={},
        write_artifact=False,
    )
    assert result.kill_switch is True
    assert result.sizing_mode == "DEFENSIVE"
    assert any("KILL" in tag for tag in result.overlay_tags), (
        f"Expected a KILL overlay tag, got {result.overlay_tags}"
    )


# ── Score boost application ──────────────────────────────────────────────────

def test_score_boost_application():
    """apply_score_boost multiplies composite by (1 + boost) and caps at 100."""
    result = RouterResult(
        regime_tag="TRENDING_UP_STRONG",
        regime_confidence=0.85,
        time_of_day_window="MORNING_MOMENTUM",
        active_strategies=[],
        overlay_tags=[],
        overlay_warnings=[],
        score_boost=0.20,
    )
    # Normal case: 80 * 1.20 = 96.0
    boosted = result.apply_score_boost(80.0)
    assert boosted == 96.0

    # Cap at 100: 95 * 1.20 = 114.0 -> capped to 100.0
    capped = result.apply_score_boost(95.0)
    assert capped == 100.0

    # Zero boost
    result_no_boost = RouterResult(
        regime_tag="RANGE_BOUND",
        regime_confidence=0.55,
        time_of_day_window="LUNCH_CHOP",
        active_strategies=[],
        overlay_tags=[],
        overlay_warnings=[],
        score_boost=0.0,
    )
    assert result_no_boost.apply_score_boost(50.0) == 50.0
