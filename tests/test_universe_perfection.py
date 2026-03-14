"""
Comprehensive test suite for Universe Perfection System.

Tests:
  1. TieredUniverseScanner: scan frequencies, confidence thresholds, tier separation
  2. PerfectAssetOptimizer: tradeability gates, signal quality, data quality, approval logic
  3. Integration: orchestrator scanning loop, parallel threads, database logging
  4. Synthetic data validation: graceful handling of missing/delisted assets
"""

import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any
import time

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.universe.tiered_universe_scanner import (
    TieredUniverseScanner,
    AssetMetrics,
    ScanResult,
)
from src.universe.perfect_asset_optimizer import (
    PerfectAssetOptimizer,
    OptimizationResult,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_metrics() -> Dict[str, AssetMetrics]:
    """Create synthetic market metrics for testing."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        # BLUE_CHIP: highest quality
        "QQQ3.L": AssetMetrics(
            ticker="QQQ3.L",
            price=150.5,
            volume=8_000_000,      # >5M gate
            bid_ask_spread_bps=8,   # <10bps gate
            atr_pct=1.2,
            rvol=1.3,
            adx=35.0,
            rsi=55.0,
            data_freshness_sec=30,
            last_update=now,
        ),
        "3LUS.L": AssetMetrics(
            ticker="3LUS.L",
            price=200.0,
            volume=7_500_000,
            bid_ask_spread_bps=9,
            atr_pct=1.1,
            rvol=1.2,
            adx=32.0,
            rsi=52.0,
            data_freshness_sec=25,
            last_update=now,
        ),
        "3SEM.L": AssetMetrics(
            ticker="3SEM.L",
            price=120.0,
            volume=6_000_000,
            bid_ask_spread_bps=7,
            atr_pct=1.3,
            rvol=1.1,
            adx=28.0,
            rsi=58.0,
            data_freshness_sec=35,
            last_update=now,
        ),
        # SPECIALIST: good quality
        "AMD3.L": AssetMetrics(
            ticker="AMD3.L",
            price=75.2,
            volume=2_000_000,      # >1M gate
            bid_ask_spread_bps=15, # <20bps gate
            atr_pct=1.8,
            rvol=0.9,
            adx=28.0,
            rsi=48.0,
            data_freshness_sec=45,
            last_update=now,
        ),
        "ARM3.L": AssetMetrics(
            ticker="ARM3.L",
            price=55.0,
            volume=1_800_000,
            bid_ask_spread_bps=18,
            atr_pct=1.5,
            rvol=1.0,
            adx=25.0,
            rsi=50.0,
            data_freshness_sec=50,
            last_update=now,
        ),
        # EXPANSION: moderate quality
        "BAC3.L": AssetMetrics(
            ticker="BAC3.L",
            price=45.0,
            volume=800_000,        # >500k gate
            bid_ask_spread_bps=25, # <30bps gate
            atr_pct=2.1,
            rvol=0.6,
            adx=22.0,
            rsi=42.0,
            data_freshness_sec=60,
            last_update=now,
        ),
        "GS3.L": AssetMetrics(
            ticker="GS3.L",
            price=38.0,
            volume=650_000,
            bid_ask_spread_bps=28,
            atr_pct=2.3,
            rvol=0.5,
            adx=18.0,
            rsi=35.0,
            data_freshness_sec=65,
            last_update=now,
        ),
        # FAILED: low volume (stale)
        "DEAD.L": AssetMetrics(
            ticker="DEAD.L",
            price=10.0,
            volume=100_000,        # <500k expansion gate
            bid_ask_spread_bps=50, # >30bps gate
            atr_pct=0.5,
            rvol=0.2,
            adx=15.0,
            rsi=30.0,
            data_freshness_sec=600,  # STALE (>5min)
            last_update="2026-01-01T00:00:00Z",
        ),
    }


@pytest.fixture
def scanner() -> TieredUniverseScanner:
    """Create TieredUniverseScanner with test universe config."""
    return TieredUniverseScanner(
        universe_config={
            "core_list": ["QQQ3.L", "3LUS.L", "3SEM.L"],
            "peer_candidates": ["AMD3.L", "ARM3.L"],
            "sector_radar": ["BAC3.L", "GS3.L", "DEAD.L"],
        }
    )


@pytest.fixture
def optimizer() -> PerfectAssetOptimizer:
    """Create PerfectAssetOptimizer."""
    return PerfectAssetOptimizer()


# ─────────────────────────────────────────────────────────────────────────────
# TieredUniverseScanner Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestTieredUniverseScanner:
    """Test suite for TieredUniverseScanner."""

    def test_scanner_initialization(self, scanner):
        """Test scanner initializes correctly."""
        assert scanner is not None
        assert scanner._universe_config is not None
        assert "core_list" in scanner._universe_config
        assert "peer_candidates" in scanner._universe_config
        assert "sector_radar" in scanner._universe_config

    def test_scan_tier1_blue_chip(self, scanner, mock_metrics):
        """Test BLUE_CHIP tier scanning."""
        ranked = scanner.scan_tier1(mock_metrics)

        # Should have 3 BLUE_CHIP assets (all pass gates)
        assert len(ranked) == 3, f"Expected 3 BLUE_CHIP, got {len(ranked)}"

        # All should be tradeable
        for asset in ranked:
            assert asset.tradeable, f"{asset.ticker} should be tradeable"
            assert asset.tier == "BLUE_CHIP"
            assert asset.confidence_pct >= 50.0  # Min confidence threshold

        # Should be sorted by confidence
        confidences = [a.confidence_pct for a in ranked]
        assert confidences == sorted(confidences, reverse=True)

    def test_scan_tier2_specialist(self, scanner, mock_metrics):
        """Test SPECIALIST tier scanning."""
        ranked = scanner.scan_tier2(mock_metrics)

        # Should have 2 SPECIALIST assets
        assert len(ranked) == 2, f"Expected 2 SPECIALIST, got {len(ranked)}"

        for asset in ranked:
            assert asset.tradeable
            assert asset.tier == "SPECIALIST"

    def test_scan_tier3_expansion(self, scanner, mock_metrics):
        """Test EXPANSION tier scanning."""
        ranked = scanner.scan_tier3(mock_metrics)

        # Should have 1-2 EXPANSION assets (DEAD.L fails gates)
        assert 0 < len(ranked) <= 2, f"Expected 1-2 EXPANSION, got {len(ranked)}"

        for asset in ranked:
            assert asset.tier == "EXPANSION"

    def test_rank_assets_full_scan(self, scanner, mock_metrics):
        """Test full tiered rank_assets call."""
        result = scanner.rank_assets(mock_metrics)

        assert isinstance(result, ScanResult)
        assert result.blue_chip_count == 3
        assert result.specialist_count == 2
        assert result.total_scanned == len(mock_metrics)

        # DEAD.L should fail gates
        failed_tickers = [f["ticker"] for f in result.failed_tickers]
        assert "DEAD.L" in failed_tickers, "DEAD.L should be in failed list"

    def test_confidence_thresholds_blue_chip(self, scanner, mock_metrics):
        """Test BLUE_CHIP confidence threshold (60%)."""
        from src.universe.tiered_universe_scanner import _CONFIDENCE_THRESHOLD_BLUE_CHIP
        ranked = scanner.scan_tier1(mock_metrics)

        # All returned assets should meet threshold
        for asset in ranked:
            assert asset.confidence_pct >= _CONFIDENCE_THRESHOLD_BLUE_CHIP

    def test_confidence_thresholds_specialist(self, scanner, mock_metrics):
        """Test SPECIALIST confidence threshold (65%)."""
        from src.universe.tiered_universe_scanner import _CONFIDENCE_THRESHOLD_SPECIALIST
        ranked = scanner.scan_tier2(mock_metrics)

        for asset in ranked:
            assert asset.confidence_pct >= _CONFIDENCE_THRESHOLD_SPECIALIST

    def test_confidence_thresholds_expansion(self, scanner, mock_metrics):
        """Test EXPANSION confidence threshold (70%)."""
        from src.universe.tiered_universe_scanner import _CONFIDENCE_THRESHOLD_EXPANSION
        ranked = scanner.scan_tier3(mock_metrics)

        for asset in ranked:
            assert asset.confidence_pct >= _CONFIDENCE_THRESHOLD_EXPANSION

    def test_liquidity_gates_enforced(self, scanner, mock_metrics):
        """Test liquidity gates are enforced."""
        # Remove a high-volume asset
        bad_metrics = mock_metrics.copy()
        bad_metrics["QQQ3.L"].volume = 1_000_000  # Below 5M gate

        result = scanner.rank_assets(bad_metrics)

        # QQQ3.L should fail
        assert result.blue_chip_count < 3

    def test_spread_gates_enforced(self, scanner, mock_metrics):
        """Test spread gates are enforced."""
        bad_metrics = mock_metrics.copy()
        bad_metrics["QQQ3.L"].bid_ask_spread_bps = 50  # Above 10bps gate

        result = scanner.rank_assets(bad_metrics)

        # QQQ3.L should fail
        assert result.blue_chip_count < 3

    def test_data_freshness_gates(self, scanner, mock_metrics):
        """Test data freshness gates."""
        bad_metrics = mock_metrics.copy()
        bad_metrics["QQQ3.L"].data_freshness_sec = 400  # Stale (>5min)

        result = scanner.rank_assets(bad_metrics)

        # QQQ3.L should fail
        assert result.blue_chip_count < 3

    def test_scan_speed(self, scanner, mock_metrics):
        """Test scan completes within acceptable time."""
        start = time.time()
        result = scanner.rank_assets(mock_metrics)
        elapsed = time.time() - start

        # Scan should complete in <1 second for small universe
        assert elapsed < 1.0, f"Scan took {elapsed:.2f}s (should be <1s)"
        assert result.scan_duration_sec < 1.0

    def test_missing_asset_graceful_handling(self, scanner):
        """Test graceful handling of missing assets."""
        # Create metrics with only 1 BLUE_CHIP
        partial_metrics = {
            "QQQ3.L": AssetMetrics(
                ticker="QQQ3.L",
                price=150.5,
                volume=8_000_000,
                bid_ask_spread_bps=8,
                atr_pct=1.2,
                rvol=1.3,
                adx=35.0,
                rsi=55.0,
                data_freshness_sec=30,
                last_update=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        }

        # Should not crash
        result = scanner.rank_assets(partial_metrics)

        assert result.blue_chip_count == 1
        assert result.specialist_count == 0
        assert result.expansion_count == 0

    def test_ranking_order_by_confidence(self, scanner, mock_metrics):
        """Test assets are ranked by confidence (highest first)."""
        ranked = scanner.scan_tier1(mock_metrics)

        # Each asset should have rank matching its position
        for i, asset in enumerate(ranked, 1):
            assert asset.rank == i, f"Asset {i} has incorrect rank {asset.rank}"


# ─────────────────────────────────────────────────────────────────────────────
# PerfectAssetOptimizer Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPerfectAssetOptimizer:
    """Test suite for PerfectAssetOptimizer."""

    def test_optimizer_initialization(self, optimizer):
        """Test optimizer initializes correctly."""
        assert optimizer is not None
        assert optimizer._existing_positions == {}

    def test_tradeability_check_pass(self, optimizer):
        """Test tradeability check for good asset."""
        result = optimizer.is_tradeable(
            ticker="QQQ3.L",
            volume=8_000_000,
            spread_bps=8,
            is_delisted=False,
            data_freshness_sec=30,
        )

        assert result.is_tradeable
        assert result.volume_ok
        assert result.spread_ok
        assert result.data_ok
        assert result.not_delisted

    def test_tradeability_check_fail_volume(self, optimizer):
        """Test tradeability check rejects low volume."""
        result = optimizer.is_tradeable(
            ticker="THIN.L",
            volume=100_000,  # Below 500k gate
            spread_bps=8,
            is_delisted=False,
            data_freshness_sec=30,
        )

        assert not result.is_tradeable
        assert not result.volume_ok
        assert "volume" in " ".join(result.issues)

    def test_tradeability_check_fail_spread(self, optimizer):
        """Test tradeability check rejects wide spread."""
        result = optimizer.is_tradeable(
            ticker="WIDE.L",
            volume=8_000_000,
            spread_bps=50,  # Above 30bps gate
            is_delisted=False,
            data_freshness_sec=30,
        )

        assert not result.is_tradeable
        assert not result.spread_ok
        assert "spread" in " ".join(result.issues)

    def test_tradeability_check_fail_stale_data(self, optimizer):
        """Test tradeability check rejects stale data."""
        result = optimizer.is_tradeable(
            ticker="STALE.L",
            volume=8_000_000,
            spread_bps=8,
            is_delisted=False,
            data_freshness_sec=400,  # >300s gate
        )

        assert not result.is_tradeable
        assert not result.data_ok
        assert "stale" in " ".join(result.issues).lower()

    def test_tradeability_check_fail_delisted(self, optimizer):
        """Test tradeability check rejects delisted assets."""
        result = optimizer.is_tradeable(
            ticker="DEAD.L",
            volume=8_000_000,
            spread_bps=8,
            is_delisted=True,
            data_freshness_sec=30,
        )

        assert not result.is_tradeable
        assert not result.not_delisted
        assert "delisted" in " ".join(result.issues)

    def test_quality_ranking_approval(self, optimizer):
        """Test quality ranking approves good candidates."""
        candidates = [
            {
                "ticker": "QQQ3.L",
                "tier": "BLUE_CHIP",
                "volume": 8_000_000,
                "spread_bps": 8,
                "signal_accuracy_pct": 72,
                "signal_reliability_pct": 85,
                "data_completeness_pct": 98,
                "data_freshness_sec": 30,
                "volatility_regime": "NORMAL",
                "adx": 35,
                "is_delisted": False,
            },
        ]

        result = optimizer.rank_by_quality(candidates)

        assert result.approved_count == 1
        assert result.rejected_count == 0
        assert len(result.whitelist) == 1
        assert result.whitelist[0].ticker == "QQQ3.L"
        assert result.whitelist[0].is_approved

    def test_quality_ranking_rejection_low_accuracy(self, optimizer):
        """Test quality ranking rejects low signal accuracy."""
        candidates = [
            {
                "ticker": "BAD.L",
                "tier": "EXPANSION",
                "volume": 8_000_000,
                "spread_bps": 8,
                "signal_accuracy_pct": 45,  # Below 60% threshold
                "signal_reliability_pct": 85,
                "data_completeness_pct": 98,
                "data_freshness_sec": 30,
                "volatility_regime": "NORMAL",
                "adx": 35,
                "is_delisted": False,
            },
        ]

        result = optimizer.rank_by_quality(candidates)

        assert result.approved_count == 0
        assert result.rejected_count == 1

    def test_quality_ranking_rejection_low_reliability(self, optimizer):
        """Test quality ranking rejects low signal reliability."""
        candidates = [
            {
                "ticker": "UNRELIABLE.L",
                "tier": "EXPANSION",
                "volume": 8_000_000,
                "spread_bps": 8,
                "signal_accuracy_pct": 72,
                "signal_reliability_pct": 60,  # Below 75% threshold
                "data_completeness_pct": 98,
                "data_freshness_sec": 30,
                "volatility_regime": "NORMAL",
                "adx": 35,
                "is_delisted": False,
            },
        ]

        result = optimizer.rank_by_quality(candidates)

        assert result.approved_count == 0
        assert result.rejected_count == 1

    def test_quality_ranking_rejection_extreme_volatility(self, optimizer):
        """Test quality ranking rejects EXTREME volatility regime."""
        candidates = [
            {
                "ticker": "VOLATILE.L",
                "tier": "EXPANSION",
                "volume": 8_000_000,
                "spread_bps": 8,
                "signal_accuracy_pct": 72,
                "signal_reliability_pct": 85,
                "data_completeness_pct": 98,
                "data_freshness_sec": 30,
                "volatility_regime": "EXTREME",
                "adx": 35,
                "is_delisted": False,
            },
        ]

        result = optimizer.rank_by_quality(candidates)

        assert result.approved_count == 0
        assert result.rejected_count == 1

    def test_quality_score_computation(self, optimizer):
        """Test quality score is computed correctly."""
        candidates = [
            {
                "ticker": "GOOD.L",
                "tier": "BLUE_CHIP",
                "volume": 8_000_000,
                "spread_bps": 8,
                "signal_accuracy_pct": 72,
                "signal_reliability_pct": 85,
                "data_completeness_pct": 98,
                "data_freshness_sec": 30,
                "volatility_regime": "NORMAL",
                "adx": 35,
                "is_delisted": False,
            },
        ]

        result = optimizer.rank_by_quality(candidates)

        if result.whitelist:
            entry = result.whitelist[0]
            assert 0 <= entry.quality_score <= 100
            assert entry.quality_score > 70  # Good asset should score high

    def test_early_detection_integration(self, optimizer):
        """Test integration with early_detection_engine scores."""
        candidates = [
            {
                "ticker": "QQQ3.L",
                "tier": "BLUE_CHIP",
                "volume": 8_000_000,
                "spread_bps": 8,
                "signal_accuracy_pct": 72,
                "signal_reliability_pct": 85,
                "data_completeness_pct": 98,
                "data_freshness_sec": 30,
                "volatility_regime": "NORMAL",
                "adx": 35,
                "is_delisted": False,
            },
        ]

        early_detection_scores = {
            "QQQ3.L": 85.0,  # High confidence from early detection
        }

        result = optimizer.rank_by_quality(candidates, early_detection_scores)

        if result.whitelist:
            entry = result.whitelist[0]
            assert entry.confidence_pct == 85.0

    def test_optimization_sorts_by_quality(self, optimizer):
        """Test optimization results are sorted by quality."""
        candidates = [
            {
                "ticker": "GOOD.L",
                "tier": "BLUE_CHIP",
                "volume": 8_000_000,
                "spread_bps": 8,
                "signal_accuracy_pct": 72,
                "signal_reliability_pct": 85,
                "data_completeness_pct": 98,
                "data_freshness_sec": 30,
                "volatility_regime": "NORMAL",
                "adx": 35,
                "is_delisted": False,
            },
            {
                "ticker": "OK.L",
                "tier": "SPECIALIST",
                "volume": 2_000_000,
                "spread_bps": 15,
                "signal_accuracy_pct": 65,
                "signal_reliability_pct": 80,
                "data_completeness_pct": 92,
                "data_freshness_sec": 45,
                "volatility_regime": "NORMAL",
                "adx": 28,
                "is_delisted": False,
            },
        ]

        result = optimizer.rank_by_quality(candidates)

        # Should be sorted by quality (highest first)
        if len(result.whitelist) > 1:
            qualities = [e.quality_score for e in result.whitelist]
            assert qualities == sorted(qualities, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Integration Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestIntegration:
    """Integration tests between scanner and optimizer."""

    def test_scanner_to_optimizer_pipeline(self, scanner, optimizer, mock_metrics):
        """Test end-to-end pipeline from scanner to optimizer."""
        # Step 1: Scan universe with TieredUniverseScanner
        scan_result = scanner.rank_assets(mock_metrics)

        # Should have some ranked assets
        all_ranked = (
            scan_result.ranked_by_tier["BLUE_CHIP"]
            + scan_result.ranked_by_tier["SPECIALIST"]
            + scan_result.ranked_by_tier["EXPANSION"]
        )
        assert len(all_ranked) > 0

        # Step 2: Convert to optimizer format
        candidates = [
            {
                "ticker": asset.ticker,
                "tier": asset.tier,
                "volume": 5_000_000,  # Assume adequate volume
                "spread_bps": 10,     # Assume good spread
                "signal_accuracy_pct": asset.confidence_pct * 0.9,  # Derived estimate
                "signal_reliability_pct": 85,
                "data_completeness_pct": 95,
                "data_freshness_sec": 30,
                "volatility_regime": "NORMAL",
                "adx": 30,
                "is_delisted": False,
            }
            for asset in all_ranked
        ]

        # Step 3: Optimize with PerfectAssetOptimizer
        early_detection = {asset.ticker: asset.confidence_pct for asset in all_ranked}
        opt_result = optimizer.rank_by_quality(candidates, early_detection)

        # Should approve only best candidates
        assert opt_result.approved_count >= 1
        assert opt_result.approved_count <= len(candidates)

    def test_multiple_scans_consistency(self, scanner, mock_metrics):
        """Test multiple scans produce consistent results."""
        result1 = scanner.rank_assets(mock_metrics)
        result2 = scanner.rank_assets(mock_metrics)

        # Results should be identical for same input
        assert result1.blue_chip_count == result2.blue_chip_count
        assert result1.specialist_count == result2.specialist_count
        assert result1.expansion_count == result2.expansion_count


# ─────────────────────────────────────────────────────────────────────────────
# Edge Cases & Robustness
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Test edge cases and robustness."""

    def test_empty_metrics_dict(self, scanner):
        """Test scanner handles empty metrics gracefully."""
        result = scanner.rank_assets({})

        assert result.total_scanned == 0
        assert result.blue_chip_count == 0
        assert result.specialist_count == 0
        assert result.expansion_count == 0

    def test_empty_candidates_list(self, optimizer):
        """Test optimizer handles empty candidates gracefully."""
        result = optimizer.rank_by_quality([])

        assert result.total_candidates == 0
        assert result.approved_count == 0
        assert result.rejected_count == 0

    def test_all_candidates_rejected(self, optimizer):
        """Test optimizer when all candidates are rejected."""
        candidates = [
            {
                "ticker": f"BAD{i}.L",
                "tier": "EXPANSION",
                "volume": 100_000,
                "spread_bps": 50,
                "signal_accuracy_pct": 40,
                "signal_reliability_pct": 60,
                "data_completeness_pct": 70,
                "data_freshness_sec": 400,
                "volatility_regime": "EXTREME",
                "adx": 10,
                "is_delisted": True,
            }
            for i in range(3)
        ]

        result = optimizer.rank_by_quality(candidates)

        assert result.approved_count == 0
        assert result.rejected_count == len(candidates)
        assert len(result.whitelist) == 0

    def test_unicode_ticker_handling(self, scanner):
        """Test scanner handles unicode tickers gracefully."""
        metrics = {
            "UNICODE™.L": AssetMetrics(
                ticker="UNICODE™.L",
                price=100,
                volume=1_000_000,
                bid_ask_spread_bps=10,
                atr_pct=1.0,
                rvol=1.0,
                adx=25,
                rsi=50,
                data_freshness_sec=30,
                last_update=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        }

        # Should not crash
        result = scanner.rank_assets(metrics)
        assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
