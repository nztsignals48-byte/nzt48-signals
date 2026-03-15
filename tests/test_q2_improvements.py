"""
Test Suite for Phase Q2 Performance & Risk Management Improvements
===================================================================

Tests all 5 Q2 deliverables:
1. Multi-Bar Confirmation Logic
2. Phantom Fill Detection
3. Margin Monitoring & Position Sizing
4. Parallel Universe Scanning
5. Quote Caching Layer
"""

import asyncio
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

# Import Q2 modules
from core.tier_based_entry_logic import TierBasedEntryDetector
from core.order_placement_engine import OrderPlacementEngine
from core.position_sizing_engine import PositionSizingEngine, MarginStatus
from core.quote_cache import QuoteCache
from core.universe_scanner import ParallelUniverseScanner, parallel_scan_wrapper

UTC = ZoneInfo("UTC")


# ─────────────────────────────────────────────────────────────────────────────
# Q2-1: Multi-Bar Confirmation Logic Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_multibar_rising_rvol_validation():
    """Test Q2-1: Multi-bar RVOL confirmation for Type B entries."""
    detector = TierBasedEntryDetector()

    # Pass: 2 of 3 bars above 2.0x
    assert detector.validate_multibar_rising_rvol([1.8, 2.1, 2.3], min_bars_threshold=2, rvol_threshold=2.0) is True

    # Pass: All 3 bars above 2.0x
    assert detector.validate_multibar_rising_rvol([2.5, 2.2, 2.8], min_bars_threshold=2, rvol_threshold=2.0) is True

    # Fail: Only 1 bar above 2.0x
    assert detector.validate_multibar_rising_rvol([1.5, 2.1, 1.9], min_bars_threshold=2, rvol_threshold=2.0) is False

    # Fail: None above threshold
    assert detector.validate_multibar_rising_rvol([1.2, 1.5, 1.8], min_bars_threshold=2, rvol_threshold=2.0) is False

    # Fail: Insufficient bars
    assert detector.validate_multibar_rising_rvol([2.5, 2.2], min_bars_threshold=2, rvol_threshold=2.0) is False

    # Fail: None provided
    assert detector.validate_multibar_rising_rvol(None, min_bars_threshold=2, rvol_threshold=2.0) is False


def test_type_a_recovery_bar_validation():
    """Test Q2-1: Type A recovery bar must be bullish (close > open)."""
    detector = TierBasedEntryDetector()

    # Pass: Bullish bar (close > open)
    assert detector.validate_type_a_recovery_bar(current_price=100.5, current_open=100.0) is True

    # Fail: Bearish bar (close < open)
    assert detector.validate_type_a_recovery_bar(current_price=99.5, current_open=100.0) is False

    # Fail: Doji bar (close == open)
    assert detector.validate_type_a_recovery_bar(current_price=100.0, current_open=100.0) is False


def test_type_b_early_runner_with_multibar():
    """Test Q2-1: Type B early runner requires multi-bar confirmation."""
    detector = TierBasedEntryDetector()

    # Valid Type B with multi-bar confirmation
    signal = detector.detect_type_b_early_runner(
        ticker="AAPL",
        current_price=155.0,
        rsi=55.0,
        rvol=2.5,
        daily_low=150.0,
        daily_high=156.0,
        volume_spike_factor=2.5,
        time_in_session_minutes=60,
        session_open_price=150.5,
        last_3_bars_rvols=[2.1, 2.3, 2.5],  # Valid: 3 bars above 2.0x
    )

    assert signal is not None
    assert signal.entry_type.value == "type_b"
    assert "Q2 multi-bar confirmed" in signal.rationale

    # Invalid: Multi-bar confirmation fails
    signal = detector.detect_type_b_early_runner(
        ticker="AAPL",
        current_price=155.0,
        rsi=55.0,
        rvol=2.5,
        daily_low=150.0,
        daily_high=156.0,
        volume_spike_factor=2.5,
        time_in_session_minutes=60,
        session_open_price=150.5,
        last_3_bars_rvols=[1.5, 2.1, 1.8],  # Invalid: Only 1 bar above 2.0x
    )

    assert signal is None


# ─────────────────────────────────────────────────────────────────────────────
# Q2-2: Phantom Fill Detection Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_verify_position_exists():
    """Test Q2-2: Phantom fill detection verifies position after order submission."""
    async def run_test():
        engine = OrderPlacementEngine(ibkr_gateway=None, redis_client=None)

        # Mock position verification (position not found)
        async def mock_get_position(ticker: str):
            return None  # Position missing → phantom fill

        engine._get_broker_position = mock_get_position

        # Verify position (should fail after retries)
        verified = await engine.verify_position_exists(
            trade_id="test_001",
            ticker="AAPL",
            expected_quantity=100,
            max_retries=2,
            retry_delay_seconds=0.1,
        )

        assert verified is False  # Position not found

    asyncio.run(run_test())


def test_verify_position_success():
    """Test Q2-2: Position verification succeeds when position found."""
    async def run_test():
        # Create mock IB gateway
        class MockIBKR:
            pass

        engine = OrderPlacementEngine(ibkr_gateway=MockIBKR(), redis_client=None)

        # Mock position verification (position found)
        async def mock_get_position(ticker: str):
            return {"ticker": ticker, "quantity": 100}

        engine._get_broker_position = mock_get_position

        # Verify position (should succeed immediately)
        verified = await engine.verify_position_exists(
            trade_id="test_002",
            ticker="AAPL",
            expected_quantity=100,
            max_retries=3,
            retry_delay_seconds=0.1,
        )

        assert verified is True

    asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────────
# Q2-3: Margin Monitoring & Position Sizing Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_aware_position_sizing():
    """Test Q2-3: Position sizing adjusts based on margin constraints."""
    async def run_test():
        engine = PositionSizingEngine(ibkr_gateway=None, redis_client=None)

        # Mock margin status
        async def mock_get_margin(force_refresh=False):
            return MarginStatus(
                total_equity=10000.0,
                available_margin=5000.0,
                maintenance_margin=2000.0,
                margin_utilization_pct=40.0,
                buying_power=15000.0,
            )

        engine.get_margin_status = mock_get_margin

        # Calculate position size (tier 1 = 4% = $400, 3x leverage = $133 margin)
        result = await engine.calculate_position_size(
            ticker="QQQ3.L",
            tier_base_pct=0.04,  # 4%
            current_price=100.0,
            account_equity=10000.0,
            leverage=3,
        )

        assert result.raw_size_pct == 0.04
        assert result.position_value_usd == 400.0
        assert result.margin_required == pytest.approx(133.33, rel=0.01)
        assert result.margin_constrained is False  # Plenty of margin available

    asyncio.run(run_test())


def test_margin_constrained_sizing():
    """Test Q2-3: Position sizing scales down when margin constrained."""
    async def run_test():
        engine = PositionSizingEngine(ibkr_gateway=None, redis_client=None)

        # Mock margin status (very limited margin)
        async def mock_get_margin(force_refresh=False):
            return MarginStatus(
                total_equity=10000.0,
                available_margin=100.0,  # Only $100 margin available
                maintenance_margin=9000.0,
                margin_utilization_pct=90.0,
                buying_power=300.0,
            )

        engine.get_margin_status = mock_get_margin

        # Calculate position size (tier 1 = 4% = $400, but margin limited)
        result = await engine.calculate_position_size(
            ticker="QQQ3.L",
            tier_base_pct=0.04,  # 4%
            current_price=100.0,
            account_equity=10000.0,
            leverage=3,
        )

        assert result.raw_size_pct == 0.04
        assert result.adjusted_size_pct < 0.04  # Scaled down
        assert result.margin_constrained is True
        assert "Margin constrained" in result.reason or "cushion" in result.reason

    asyncio.run(run_test())


def test_position_registry():
    """Test Q2-3: Position registry tracks active positions."""
    engine = PositionSizingEngine()

    # Register positions
    engine.register_position("AAPL", 0.04)
    engine.register_position("GOOGL", 0.03)
    engine.register_position("TSLA", 0.025)

    # Check utilization
    assert engine.get_portfolio_utilization() == pytest.approx(0.095, rel=0.01)
    assert len(engine.get_active_positions()) == 3

    # Unregister position
    engine.unregister_position("GOOGL")
    assert engine.get_portfolio_utilization() == pytest.approx(0.065, rel=0.01)
    assert len(engine.get_active_positions()) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Q2-4: Parallel Universe Scanning Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_parallel_scanner_speedup():
    """Test Q2-4: Parallel scanning achieves >2x speedup."""
    scanner = ParallelUniverseScanner(max_workers=4)

    # Mock scan function (simulate 0.5s per ticker)
    def mock_scan(ticker: str) -> dict:
        time.sleep(0.1)  # Simulate I/O delay
        return {"ticker": ticker, "price": 100.0}

    tickers = [f"TICK{i}" for i in range(20)]

    start_time = time.time()
    results = scanner.scan_universe(tickers=tickers, scan_function=mock_scan)
    total_time = time.time() - start_time

    # Check results
    assert len(results) == 20
    assert all(r.success for r in results)

    # Check speedup (should be ~4x with 4 workers)
    # Sequential: 20 tickers × 0.1s = 2.0s
    # Parallel (4 workers): 20 / 4 × 0.1s = 0.5s (4x speedup)
    sequential_baseline = 20 * 0.1
    speedup = sequential_baseline / total_time

    print(f"\nQ2-4 Speedup Test: {speedup:.1f}x (target 2-4x)")
    assert speedup >= 2.0, f"Expected ≥2x speedup, got {speedup:.1f}x"


def test_parallel_scanner_handles_failures():
    """Test Q2-4: Parallel scanner handles ticker failures gracefully."""
    scanner = ParallelUniverseScanner(max_workers=4)

    # Mock scan function (some tickers fail)
    def mock_scan(ticker: str) -> dict:
        if ticker in ["FAIL1", "FAIL2"]:
            raise ValueError(f"Simulated failure for {ticker}")
        return {"ticker": ticker, "price": 100.0}

    tickers = ["TICK1", "FAIL1", "TICK2", "FAIL2", "TICK3"]
    results = scanner.scan_universe(tickers=tickers, scan_function=mock_scan)

    # Check results
    assert len(results) == 5
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    assert len(successful) == 3  # TICK1, TICK2, TICK3
    assert len(failed) == 2  # FAIL1, FAIL2

    # Check error messages
    for r in failed:
        assert "Simulated failure" in r.error


# ─────────────────────────────────────────────────────────────────────────────
# Q2-5: Quote Caching Layer Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_quote_cache_basic():
    """Test Q2-5: Quote cache stores and retrieves quotes."""
    cache = QuoteCache(ttl_seconds=60, max_size=100)

    # Set quote
    cache.set("AAPL", price=150.0, bid=149.95, ask=150.05, volume=1000000)

    # Get quote (should be fresh)
    quote = cache.get("AAPL")
    assert quote is not None
    assert quote.ticker == "AAPL"
    assert quote.price == 150.0
    assert quote.bid == 149.95
    assert quote.ask == 150.05
    assert quote.volume == 1000000
    assert quote.is_stale(ttl_seconds=60) is False


def test_quote_cache_ttl():
    """Test Q2-5: Quote cache respects TTL."""
    cache = QuoteCache(ttl_seconds=1, max_size=100)

    # Set quote
    cache.set("AAPL", price=150.0)

    # Get immediately (fresh)
    quote = cache.get("AAPL")
    assert quote is not None

    # Wait for TTL to expire
    time.sleep(1.1)

    # Get again (stale)
    quote = cache.get("AAPL")
    assert quote is None  # Stale, so not returned


def test_quote_cache_stale_fallback():
    """Test Q2-5: get_stale() returns stale quotes as fallback."""
    cache = QuoteCache(ttl_seconds=1, max_size=100)

    # Set quote
    cache.set("AAPL", price=150.0)

    # Wait for TTL to expire
    time.sleep(1.1)

    # get() returns None (stale)
    quote = cache.get("AAPL")
    assert quote is None

    # get_stale() returns quote even if stale
    quote = cache.get_stale("AAPL")
    assert quote is not None
    assert quote.price == 150.0
    assert quote.is_stale(ttl_seconds=1) is True


def test_quote_cache_lru_eviction():
    """Test Q2-5: Quote cache evicts LRU when full."""
    cache = QuoteCache(ttl_seconds=60, max_size=3)

    # Fill cache
    cache.set("AAPL", price=150.0)
    cache.set("GOOGL", price=2800.0)
    cache.set("TSLA", price=180.0)

    # Cache full (3/3)
    assert cache.get_stats()["cache_size"] == 3

    # Add 4th quote (should evict LRU)
    cache.set("MSFT", price=380.0)

    # Cache still 3/3
    assert cache.get_stats()["cache_size"] == 3

    # AAPL was LRU, should be evicted
    quote = cache.get("AAPL")
    assert quote is None

    # Others should still be cached
    assert cache.get("GOOGL") is not None
    assert cache.get("TSLA") is not None
    assert cache.get("MSFT") is not None


def test_quote_cache_hit_rate():
    """Test Q2-5: Quote cache tracks hit rate."""
    cache = QuoteCache(ttl_seconds=60, max_size=100)

    # Set quotes
    cache.set("AAPL", price=150.0)
    cache.set("GOOGL", price=2800.0)

    # Hits
    cache.get("AAPL")
    cache.get("GOOGL")
    cache.get("AAPL")

    # Misses
    cache.get("TSLA")
    cache.get("MSFT")

    # Check stats
    stats = cache.get_stats()
    assert stats["hits"] == 3
    assert stats["misses"] == 2
    assert stats["hit_rate"] == 0.6  # 3/5


# ─────────────────────────────────────────────────────────────────────────────
# Integration Test: All Q2 Improvements Together
# ─────────────────────────────────────────────────────────────────────────────

def test_q2_integration():
    """Integration test: All Q2 improvements working together."""

    # Q2-5: Quote cache
    quote_cache = QuoteCache(ttl_seconds=60, max_size=100)
    quote_cache.set("QQQ3.L", price=50.0, volume=1000000)

    # Q2-4: Parallel scanner
    scanner = ParallelUniverseScanner(max_workers=4)

    def mock_scan(ticker: str) -> dict:
        # Use cached quote if available
        cached = quote_cache.get(ticker)
        if cached:
            return {"ticker": ticker, "price": cached.price, "cached": True}
        else:
            # Simulate API call
            time.sleep(0.05)
            quote_cache.set(ticker, price=100.0)
            return {"ticker": ticker, "price": 100.0, "cached": False}

    tickers = ["QQQ3.L", "3LUS.L", "AAPL", "GOOGL"]
    results = scanner.scan_universe(tickers=tickers, scan_function=mock_scan)

    # Check results
    assert len(results) == 4
    assert all(r.success for r in results)

    # QQQ3.L should be cached
    qqq_result = next(r for r in results if r.ticker == "QQQ3.L")
    assert qqq_result.data["cached"] is True
    assert qqq_result.data["price"] == 50.0

    # Q2-3: Position sizing
    pos_engine = PositionSizingEngine()
    pos_engine.register_position("QQQ3.L", 0.04)
    assert pos_engine.get_portfolio_utilization() == 0.04

    # Q2-1: Multi-bar confirmation
    detector = TierBasedEntryDetector()
    assert detector.validate_multibar_rising_rvol([2.1, 2.3, 2.5]) is True

    print("\n✅ Q2 Integration Test: All improvements working together")


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v", "-s"])
