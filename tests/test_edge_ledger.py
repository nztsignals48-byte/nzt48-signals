"""
tests/test_edge_ledger.py
==========================
Unit tests for learning/edge_ledger.py — Wilson CI, EdgeLedger, bucket keys.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math
import pytest
from unittest.mock import MagicMock, patch

from learning.edge_ledger import _wilson_ci, _confidence_score, EdgeLedger, MIN_SAMPLE_CALIBRATE
from learning.schemas import EdgeBucketKey, OutcomeRecord


# ── Wilson CI tests ───────────────────────────────────────────────────────────

def test_wilson_ci_zero_wins():
    """Wilson CI with 0 wins out of n trials returns (0, ~1) when n=0."""
    lo, hi = _wilson_ci(0, 0)
    assert lo == 0.0
    assert hi == 1.0


def test_wilson_ci_all_wins():
    """Wilson CI with all wins returns lower bound near 1 and upper bound exactly 1."""
    lo, hi = _wilson_ci(100, 100)
    assert lo > 0.9, f"Expected lower bound > 0.9, got {lo}"
    assert hi == 1.0


def test_wilson_ci_fifty_percent():
    """Wilson CI with 50% win rate returns reasonable symmetric-ish bounds."""
    lo, hi = _wilson_ci(50, 100)
    # Centre should be near 0.5, bounds should be reasonable
    assert 0.3 < lo < 0.5, f"Lower bound {lo} not in expected range"
    assert 0.5 < hi < 0.7, f"Upper bound {hi} not in expected range"
    # Interval should contain 0.5
    assert lo < 0.5 < hi


# ── EdgeLedger instantiation ─────────────────────────────────────────────────

def test_edge_ledger_instantiation():
    """EdgeLedger can be instantiated without errors."""
    ledger = EdgeLedger()
    assert ledger is not None
    assert hasattr(ledger, "compute")
    assert hasattr(ledger, "load_outcomes")
    assert hasattr(ledger, "save")
    assert hasattr(ledger, "load")
    assert hasattr(ledger, "rebuild")


# ── Bucket key formation ─────────────────────────────────────────────────────

def test_bucket_key_to_str_format():
    """EdgeBucketKey.to_str() returns pipe-delimited string with all 5 fields."""
    key = EdgeBucketKey(
        strategy_tag="TREND_MOMENTUM_CTA",
        regime_tag="TRENDING_UP_STRONG",
        track="SCALP",
        time_window="MORNING_MOMENTUM",
        liquidity_bucket="NORMAL",
    )
    result = key.to_str()
    assert result == "TREND_MOMENTUM_CTA|TRENDING_UP_STRONG|SCALP|MORNING_MOMENTUM|NORMAL"
    # Round-trip: from_str should reconstruct
    reconstructed = EdgeBucketKey.from_str(result)
    assert reconstructed.strategy_tag == "TREND_MOMENTUM_CTA"
    assert reconstructed.regime_tag == "TRENDING_UP_STRONG"
    assert reconstructed.track == "SCALP"
    assert reconstructed.time_window == "MORNING_MOMENTUM"
    assert reconstructed.liquidity_bucket == "NORMAL"


# ── NEEDS_DATA status ────────────────────────────────────────────────────────

def test_needs_data_status_when_below_min_sample():
    """Compute returns NEEDS_DATA status when trades_count < MIN_SAMPLE_CALIBRATE (10)."""
    ledger = EdgeLedger()
    # Create fewer than MIN_SAMPLE_CALIBRATE outcome records
    records = []
    for i in range(MIN_SAMPLE_CALIBRATE - 1):  # 9 records, below threshold of 10
        records.append(OutcomeRecord(
            signal_id=f"NZT-{i:06d}",
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
            duration_minutes=30,
            closed_at=f"2025-01-0{i+1}T11:00:00+00:00",
        ))

    result = ledger.compute(records)
    assert len(result) == 1
    bucket_key = list(result.keys())[0]
    record = result[bucket_key]
    assert record.status == "NEEDS_DATA"
    assert record.trades_count == MIN_SAMPLE_CALIBRATE - 1


# ── Confidence score increases with sample size ──────────────────────────────

def test_confidence_score_increases_with_sample_size():
    """_confidence_score grows with larger sample size (stability held constant)."""
    stability = 0.8
    scores = [_confidence_score(n, stability) for n in [5, 20, 50, 100]]
    # Each successive score should be >= the previous
    for i in range(1, len(scores)):
        assert scores[i] >= scores[i - 1], (
            f"Confidence did not increase: n={[5,20,50,100][i]} -> {scores[i]} "
            f"vs n={[5,20,50,100][i-1]} -> {scores[i-1]}"
        )
    # Score at n=100 should be notably higher than n=5
    assert scores[-1] > scores[0], "Confidence at n=100 should exceed n=5"


# ── Empty outcomes returns empty ledger ──────────────────────────────────────

def test_empty_outcomes_returns_empty_ledger():
    """Passing an empty list of outcome records returns an empty ledger dict."""
    ledger = EdgeLedger()
    result = ledger.compute(records=[])
    assert result == {}
    assert isinstance(result, dict)
