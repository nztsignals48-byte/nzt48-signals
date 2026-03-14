"""
tests/test_outcomes_engine.py
==============================
Unit tests for learning/outcomes_engine.py — OutcomeEngine, outcome types.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock, patch

from learning.schemas import OutcomeRecord


# ── Import works without error ────────────────────────────────────────────────

def test_outcomes_engine_import():
    """Module imports without error."""
    import learning.outcomes_engine
    assert hasattr(learning.outcomes_engine, "OutcomeEngine")
    assert hasattr(learning.outcomes_engine, "get_outcome_engine")


# ── OutcomeEngine instantiation ──────────────────────────────────────────────

def test_outcome_engine_instantiation(tmp_path):
    """OutcomeEngine can be instantiated; it creates the data directory."""
    with patch("learning.outcomes_engine._DATA", tmp_path / "data"):
        from learning.outcomes_engine import OutcomeEngine
        engine = OutcomeEngine()
        assert engine is not None
        assert (tmp_path / "data").exists()


# ── resolve_all_pending exists as method ─────────────────────────────────────

def test_resolve_all_pending_method_exists():
    """OutcomeEngine has a resolve_all_pending method."""
    from learning.outcomes_engine import OutcomeEngine
    engine = OutcomeEngine()
    assert hasattr(engine, "resolve_all_pending")
    assert callable(engine.resolve_all_pending)


# ── OutcomeRecord has correct fields ─────────────────────────────────────────

def test_outcome_record_has_correct_fields():
    """OutcomeRecord from schemas has all expected fields."""
    record = OutcomeRecord(
        signal_id="NZT-ABC123",
        ticker="QQQ3.L",
        direction="LONG",
        strategy_tag="TREND_MOMENTUM_CTA",
        regime_tag="TRENDING_UP_STRONG",
        time_window="MORNING_MOMENTUM",
        track="SCALP",
        session="LSE",
        entry=100.0,
        stop=98.0,
        target1=104.0,
        net_rr=2.0,
        generated_at="2025-01-01T10:00:00+00:00",
        outcome="HIT_TARGET",
        exit_price=104.0,
        pnl_r_gross=2.0,
        pnl_r_net=1.9,
        mfe_pct=4.5,
        mae_pct=-1.2,
        duration_minutes=30,
        cost_bps=8.0,
        closed_at="2025-01-01T10:30:00+00:00",
        resolution_method="PATH_BASED",
        bars_used=30,
    )
    assert record.signal_id == "NZT-ABC123"
    assert record.ticker == "QQQ3.L"
    assert record.outcome == "HIT_TARGET"
    assert record.pnl_r_net == 1.9
    assert record.duration_minutes == 30
    assert record.resolution_method == "PATH_BASED"
    assert record.mfe_pct == 4.5
    assert record.mae_pct == -1.2
    assert record.cost_bps == 8.0
    assert record.bars_used == 30
    # Default counterfactuals should be empty list
    assert record.counterfactuals == []


# ── HIT_TARGET is a valid outcome ────────────────────────────────────────────

def test_hit_target_is_valid_outcome():
    """HIT_TARGET is an accepted outcome string in OutcomeRecord."""
    record = OutcomeRecord(
        signal_id="NZT-000001",
        ticker="QQQ3.L",
        direction="LONG",
        strategy_tag="TREND_MOMENTUM_CTA",
        regime_tag="TRENDING_UP_STRONG",
        time_window="MORNING_MOMENTUM",
        track="SCALP",
        session="LSE",
        entry=100.0,
        stop=98.0,
        target1=104.0,
        net_rr=2.0,
        generated_at="2025-01-01T10:00:00+00:00",
        outcome="HIT_TARGET",
    )
    assert record.outcome == "HIT_TARGET"
    # Verify it survives round-trip serialisation
    d = record.to_dict()
    restored = OutcomeRecord.from_dict(d)
    assert restored.outcome == "HIT_TARGET"


# ── HIT_STOP is a valid outcome ──────────────────────────────────────────────

def test_hit_stop_is_valid_outcome():
    """HIT_STOP is an accepted outcome string in OutcomeRecord."""
    record = OutcomeRecord(
        signal_id="NZT-000002",
        ticker="3LUS.L",
        direction="LONG",
        strategy_tag="MOMENTUM_BREAKOUT",
        regime_tag="RANGE_BOUND",
        time_window="TREND_EXTENSION",
        track="INTRADAY_SWING",
        session="LSE",
        entry=50.0,
        stop=48.0,
        target1=53.0,
        net_rr=1.5,
        generated_at="2025-01-02T09:00:00+00:00",
        outcome="HIT_STOP",
        exit_price=48.0,
        pnl_r_gross=-1.0,
        pnl_r_net=-1.08,
    )
    assert record.outcome == "HIT_STOP"
    # Verify round-trip
    d = record.to_dict()
    restored = OutcomeRecord.from_dict(d)
    assert restored.outcome == "HIT_STOP"
    assert restored.pnl_r_net == -1.08
