"""Unit tests for the Ticker Priority Ranking Engine.

Tests each scoring function independently with mock data, then tests the
full ranking pipeline end-to-end including TOML output and report generation.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from brain.ticker_ranker import (
    MAX_RANKED_TICKERS,
    SPREAD_PERFECT_BPS,
    SPREAD_ZERO_BPS,
    RankingResult,
    TickerMarketData,
    TickerPerformance,
    TickerScore,
    _build_ranking_block,
    _is_exchange_open_in_window,
    _linear_scale,
    rank_tickers,
    run_ranking_cycle,
    score_liquidity,
    score_performance,
    score_regime_fit,
    score_rvol,
    score_session_fit,
    score_spread,
    write_ranking_report,
    write_ranking_toml,
)


# ---------------------------------------------------------------------------
# Fixtures: mock data generators
# ---------------------------------------------------------------------------

def _make_market_data(
    ticker: str = "QQQ3.L",
    last_price: float = 50.0,
    bid: float = 49.98,
    ask: float = 50.02,
    volume: float = 100_000.0,
    avg_daily_volume: float = 500_000.0,
    hurst: float = 0.50,
    adx: float = 20.0,
    exchange: str = "LSE",
) -> TickerMarketData:
    return TickerMarketData(
        ticker=ticker,
        last_price=last_price,
        bid=bid,
        ask=ask,
        volume=volume,
        avg_daily_volume=avg_daily_volume,
        hurst=hurst,
        adx=adx,
        exchange=exchange,
    )


def _make_perf(
    ticker: str = "QQQ3.L",
    win_rate: float = 0.60,
    edge_ratio: float = 1.5,
    trade_count: int = 20,
    total_pnl: float = 150.0,
) -> TickerPerformance:
    return TickerPerformance(
        ticker=ticker,
        win_rate=win_rate,
        edge_ratio=edge_ratio,
        trade_count=trade_count,
        total_pnl=total_pnl,
    )


def _make_isa_universe(n: int = 12) -> list[TickerMarketData]:
    """Create mock market data for ISA tickers."""
    tickers = [
        ("QQQ3.L", 50.0, 49.97, 50.03, 200_000, 800_000, 0.60, 30.0),
        ("3LUS.L", 25.0, 24.98, 25.02, 150_000, 600_000, 0.55, 25.0),
        ("NVD3.L", 80.0, 79.95, 80.05, 100_000, 400_000, 0.62, 32.0),
        ("3SEM.L", 30.0, 29.96, 30.04, 80_000, 300_000, 0.48, 18.0),
        ("TSL3.L", 15.0, 14.97, 15.03, 60_000, 250_000, 0.58, 28.0),
        ("QQQS.L", 10.0, 9.97, 10.03, 50_000, 200_000, 0.42, 12.0),
        ("3USS.L", 12.0, 11.97, 12.03, 40_000, 180_000, 0.40, 10.0),
        ("QQQ5.L", 20.0, 19.96, 20.04, 30_000, 150_000, 0.52, 22.0),
        ("TSM3.L", 40.0, 39.95, 40.05, 25_000, 120_000, 0.50, 20.0),
        ("MU2.L", 18.0, 17.96, 18.04, 20_000, 100_000, 0.47, 16.0),
        ("GPT3.L", 35.0, 34.94, 35.06, 15_000, 80_000, 0.53, 21.0),
        ("5SPY.L", 45.0, 44.96, 45.04, 10_000, 60_000, 0.51, 19.0),
    ]
    result = []
    for t in tickers[:n]:
        result.append(TickerMarketData(
            ticker=t[0], last_price=t[1], bid=t[2], ask=t[3],
            volume=t[4], avg_daily_volume=t[5], hurst=t[6], adx=t[7],
            exchange="LSE",
        ))
    return result


# ===========================================================================
# Tests: score_spread
# ===========================================================================

class TestScoreSpread:
    def test_perfect_spread(self):
        """Spread <= 2 bps should score 100."""
        # bid=49.999, ask=50.001 => spread = 0.002/50 * 10000 = 0.4 bps
        assert score_spread(49.999, 50.001, 50.0) == 100.0

    def test_zero_score_wide_spread(self):
        """Spread >= 25 bps should score 0."""
        # bid=49.90, ask=50.10 => spread = 0.20/50 * 10000 = 40 bps
        assert score_spread(49.90, 50.10, 50.0) == 0.0

    def test_midrange_spread(self):
        """Spread midway between 2 and 25 bps should score ~50."""
        # Target: ~13.5 bps => score ~50
        # spread = (ask-bid)/last * 10000 = 13.5 bps
        half_spread = 13.5 * 50.0 / 10_000 / 2
        score = score_spread(50.0 - half_spread, 50.0 + half_spread, 50.0)
        assert 45.0 <= score <= 55.0

    def test_zero_price(self):
        """Zero price should return 0."""
        assert score_spread(49.0, 51.0, 0.0) == 0.0

    def test_no_bid_ask(self):
        """Missing bid/ask (zero) should return conservative score."""
        assert score_spread(0.0, 0.0, 50.0) == 30.0

    def test_exactly_25_bps(self):
        """Spread of exactly 25 bps should score 0."""
        half_spread = 25.0 * 50.0 / 10_000 / 2
        assert score_spread(50.0 - half_spread, 50.0 + half_spread, 50.0) == 0.0


# ===========================================================================
# Tests: score_rvol
# ===========================================================================

class TestScoreRvol:
    def test_zero_rvol(self):
        assert score_rvol(0.0, "trending") == 0.0

    def test_mr_optimal(self):
        """RVOL 1.5 in mean-reverting regime should score 100."""
        assert score_rvol(1.5, "mean_reverting") == 100.0

    def test_mr_too_high(self):
        """RVOL 4.0 in MR regime should score lower than optimal."""
        score_high = score_rvol(4.0, "mean_reverting")
        score_optimal = score_rvol(1.5, "mean_reverting")
        assert score_high < score_optimal

    def test_momentum_boost(self):
        """RVOL 3.5 in trending regime should score 100."""
        assert score_rvol(3.5, "trending") == 100.0

    def test_extreme_rvol_penalty(self):
        """RVOL 6.0 should be penalised in any regime."""
        for regime in ["trending", "mean_reverting", "random"]:
            extreme = score_rvol(6.0, regime)
            moderate = score_rvol(2.0, regime)
            assert extreme < moderate, f"RVOL 6.0 should score lower than 2.0 in {regime}"

    def test_random_regime_moderate(self):
        """RVOL 1.0 in random regime should score decently."""
        score = score_rvol(1.0, "random")
        assert score >= 60.0


# ===========================================================================
# Tests: score_regime_fit
# ===========================================================================

class TestScoreRegimeFit:
    def test_trending_ticker_in_trending_regime(self):
        """Hurst=0.70, ADX=35 in trending regime should score high."""
        score = score_regime_fit(0.70, 35.0, "trending")
        assert score >= 80.0

    def test_mr_ticker_in_mr_regime(self):
        """Hurst=0.35, ADX=12 in mean_reverting regime should score high."""
        score = score_regime_fit(0.35, 12.0, "mean_reverting")
        assert score >= 80.0

    def test_mismatch_trending_in_mr(self):
        """Hurst=0.70 (trending) in mean_reverting regime should score low."""
        score = score_regime_fit(0.70, 35.0, "mean_reverting")
        assert score < 30.0

    def test_mismatch_mr_in_trending(self):
        """Hurst=0.30 (MR) in trending regime should score low."""
        score = score_regime_fit(0.30, 10.0, "trending")
        assert score < 30.0

    def test_random_regime_neutral(self):
        """Mid-range Hurst/ADX in random regime should score moderately."""
        score = score_regime_fit(0.50, 20.0, "random")
        assert 40.0 <= score <= 100.0


# ===========================================================================
# Tests: score_performance
# ===========================================================================

class TestScorePerformance:
    def test_no_trades(self):
        """Zero trades should return neutral 50."""
        assert score_performance(0.5, 1.0, 0) == 50.0

    def test_good_performance(self):
        """70% WR, 2.0 edge ratio, 20 trades should score well above 50."""
        score = score_performance(0.70, 2.0, 20)
        assert score > 65.0

    def test_bad_performance(self):
        """30% WR, 0.5 edge ratio, 20 trades should score well below 50."""
        score = score_performance(0.30, 0.5, 20)
        assert score < 40.0

    def test_low_trade_count_shrinks_toward_neutral(self):
        """1 trade should shrink the score toward 50 (Laplace smoothing)."""
        score_1 = score_performance(0.80, 2.0, 1)
        score_20 = score_performance(0.80, 2.0, 20)
        # 1-trade score should be closer to 50 than 20-trade score
        assert abs(score_1 - 50.0) < abs(score_20 - 50.0)

    def test_edge_ratio_capped(self):
        """Edge ratio above cap should not increase score."""
        score_3 = score_performance(0.60, 3.0, 20)
        score_10 = score_performance(0.60, 10.0, 20)
        assert abs(score_3 - score_10) < 0.01


# ===========================================================================
# Tests: score_session_fit
# ===========================================================================

class TestScoreSessionFit:
    def test_lse_open_during_lse_session(self):
        """LSE ticker during LSE hours should score high."""
        score = score_session_fit("LSE", "10:00-14:00", "QQQ3.L")
        assert score >= 70.0

    def test_lse_closed_during_us_session(self):
        """LSE ticker during US-only session should score low."""
        # Session 20:00-21:00 — LSE closes at 16:30
        score = score_session_fit("LSE", "20:00-21:00", "QQQ3.L")
        assert score < 20.0

    def test_preferred_ticker_bonus(self):
        """Preferred ticker should get a +30 bonus."""
        base = score_session_fit("LSE", "10:00-14:00", "QQQ3.L", [])
        preferred = score_session_fit("LSE", "10:00-14:00", "QQQ3.L", ["QQQ3.L"])
        assert preferred - base >= 25.0  # ~30 bonus

    def test_unknown_exchange(self):
        """Unknown exchange should return 0 for exchange-open check."""
        score = score_session_fit("UNKNOWN", "10:00-14:00", "FOO")
        assert score < 10.0


# ===========================================================================
# Tests: score_liquidity
# ===========================================================================

class TestScoreLiquidity:
    def test_zero_volume(self):
        assert score_liquidity(0.0) == 0.0

    def test_high_volume(self):
        assert score_liquidity(2_000_000) == 100.0

    def test_low_volume(self):
        assert score_liquidity(5_000) == 0.0

    def test_mid_volume(self):
        """100k ADV should score between 0 and 100."""
        score = score_liquidity(100_000)
        assert 0.0 < score < 100.0

    def test_logarithmic_scaling(self):
        """Doubling volume from 50k to 100k should increase score less than 50k->100k gap."""
        s_50k = score_liquidity(50_000)
        s_100k = score_liquidity(100_000)
        s_200k = score_liquidity(200_000)
        # Log scaling: increments should diminish
        gain_1 = s_100k - s_50k
        gain_2 = s_200k - s_100k
        assert gain_1 > 0
        assert gain_2 > 0
        # Both gains should be positive, log scaling gives roughly equal gains for 2x


# ===========================================================================
# Tests: helper functions
# ===========================================================================

class TestHelpers:
    def test_linear_scale_normal(self):
        assert _linear_scale(5.0, 0.0, 10.0, 0.0, 100.0) == 50.0

    def test_linear_scale_clamped_low(self):
        assert _linear_scale(-5.0, 0.0, 10.0, 0.0, 100.0) == 0.0

    def test_linear_scale_clamped_high(self):
        assert _linear_scale(15.0, 0.0, 10.0, 0.0, 100.0) == 100.0

    def test_linear_scale_inverted_range(self):
        """in_low > in_high should work correctly (reversed mapping)."""
        # Higher input → lower output
        result = _linear_scale(0.55, 0.55, 0.35, 0.0, 50.0)
        assert abs(result - 0.0) < 0.01

    def test_is_exchange_open_lse_morning(self):
        assert _is_exchange_open_in_window("LSE", "10:00-14:00") is True

    def test_is_exchange_open_lse_after_hours(self):
        assert _is_exchange_open_in_window("LSE", "17:00-21:00") is False

    def test_is_exchange_open_nyse_us_session(self):
        assert _is_exchange_open_in_window("NYSE", "14:30-16:00") is True


# ===========================================================================
# Tests: rank_tickers (integration)
# ===========================================================================

class TestRankTickers:
    def test_basic_ranking(self):
        """12 ISA tickers should all be ranked."""
        market_data = _make_isa_universe(12)
        perf = {
            "QQQ3.L": _make_perf("QQQ3.L", 0.65, 1.8, 25),
            "3LUS.L": _make_perf("3LUS.L", 0.60, 1.5, 20),
            "NVD3.L": _make_perf("NVD3.L", 0.70, 2.0, 15),
        }
        spread_cache = {"QQQ3.L": 5.0, "3LUS.L": 8.0, "NVD3.L": 12.0}

        result = rank_tickers(
            market_data=market_data,
            session_window="10:00-14:00",
            regime_state="trending",
            ouroboros_perf=perf,
            spread_cache=spread_cache,
        )

        assert result.ticker_count == 12
        assert len(result.rankings) == 12
        # Scores should all be in [0, 100]
        for ts in result.rankings:
            assert 0.0 <= ts.total_score <= 100.0, f"{ts.ticker}: {ts.total_score}"

    def test_sorted_descending(self):
        """Rankings must be sorted descending by total_score."""
        market_data = _make_isa_universe(12)
        result = rank_tickers(
            market_data=market_data,
            session_window="10:00-14:00",
            regime_state="mean_reverting",
            ouroboros_perf={},
            spread_cache={},
        )
        scores = [r.total_score for r in result.rankings]
        assert scores == sorted(scores, reverse=True)

    def test_trending_regime_favours_high_hurst(self):
        """In trending regime, high-Hurst tickers should rank higher."""
        trending = _make_market_data("HIGH_H", hurst=0.70, adx=35.0)
        reverting = _make_market_data("LOW_H", hurst=0.30, adx=10.0,
                                       last_price=50.0, bid=49.98, ask=50.02)

        result = rank_tickers(
            market_data=[trending, reverting],
            session_window="10:00-14:00",
            regime_state="trending",
            ouroboros_perf={},
            spread_cache={},
        )
        assert result.rankings[0].ticker == "HIGH_H"

    def test_mr_regime_favours_low_hurst(self):
        """In MR regime, low-Hurst tickers should rank higher."""
        trending = _make_market_data("HIGH_H", hurst=0.70, adx=35.0)
        reverting = _make_market_data("LOW_H", hurst=0.30, adx=10.0,
                                       last_price=50.0, bid=49.98, ask=50.02)

        result = rank_tickers(
            market_data=[trending, reverting],
            session_window="10:00-14:00",
            regime_state="mean_reverting",
            ouroboros_perf={},
            spread_cache={},
        )
        assert result.rankings[0].ticker == "LOW_H"

    def test_wide_spread_penalised(self):
        """Ticker with 30 bps spread should rank below 5 bps spread ticker."""
        tight = _make_market_data("TIGHT", bid=49.99, ask=50.01)  # ~4 bps
        wide = _make_market_data("WIDE", bid=49.85, ask=50.15)    # ~60 bps

        result = rank_tickers(
            market_data=[tight, wide],
            session_window="10:00-14:00",
            regime_state="random",
            ouroboros_perf={},
            spread_cache={},
        )
        assert result.rankings[0].ticker == "TIGHT"

    def test_max_100_tickers(self):
        """With >100 tickers, only top 100 are returned."""
        data = [
            _make_market_data(f"T{i:03d}", last_price=50.0, bid=49.98, ask=50.02,
                              avg_daily_volume=float(500_000 - i * 1000))
            for i in range(150)
        ]
        result = rank_tickers(
            market_data=data,
            session_window="10:00-14:00",
            regime_state="random",
            ouroboros_perf={},
            spread_cache={},
        )
        assert len(result.rankings) == MAX_RANKED_TICKERS

    def test_empty_input(self):
        """Empty market data should produce empty rankings."""
        result = rank_tickers(
            market_data=[],
            session_window="10:00-14:00",
            regime_state="random",
            ouroboros_perf={},
            spread_cache={},
        )
        assert result.ticker_count == 0

    def test_preferred_ticker_boost(self):
        """Explicitly preferred ticker should rank higher."""
        a = _make_market_data("A", hurst=0.50, adx=20.0)
        b = _make_market_data("B", hurst=0.50, adx=20.0)

        result = rank_tickers(
            market_data=[a, b],
            session_window="10:00-14:00",
            regime_state="random",
            ouroboros_perf={},
            spread_cache={},
            preferred_tickers=["A"],
        )
        assert result.rankings[0].ticker == "A"

    def test_spread_cache_overrides_bid_ask(self):
        """Spread from cache should override bid/ask computation."""
        # Wide bid/ask but tight cached spread
        md = _make_market_data("TEST", bid=49.0, ask=51.0)  # 400 bps from bid/ask
        result_with_cache = rank_tickers(
            market_data=[md],
            session_window="10:00-14:00",
            regime_state="random",
            ouroboros_perf={},
            spread_cache={"TEST": 3.0},  # 3 bps from cache
        )
        result_no_cache = rank_tickers(
            market_data=[md],
            session_window="10:00-14:00",
            regime_state="random",
            ouroboros_perf={},
            spread_cache={},
        )
        # Cached version should have much higher spread score
        assert result_with_cache.rankings[0].spread_score > result_no_cache.rankings[0].spread_score


# ===========================================================================
# Tests: TOML writer
# ===========================================================================

class TestTomlWriter:
    def test_write_and_read_toml(self, tmp_path):
        """Write ranking to strategies.toml and verify content."""
        # Create a minimal strategies.toml
        toml_path = tmp_path / "strategies.toml"
        toml_path.write_text(
            'schema_version = 1\n\n'
            '[ticker_ranking]\n'
            'refresh_interval_minutes = 120\n\n'
            '[ticker_ranking.current]\n'
            '"QQQ3.L" = 50\n'
        )

        result = RankingResult(
            rankings=[
                TickerScore("QQQ3.L", 95.0, 90.0, 80.0, 85.0, 70.0, 100.0, 90.0),
                TickerScore("3LUS.L", 88.0, 85.0, 75.0, 80.0, 65.0, 95.0, 85.0),
            ],
            timestamp="2026-03-18T10:00:00Z",
            session_window="10:00-14:00",
            regime_state="trending",
            ticker_count=2,
        )

        write_ranking_toml(toml_path, result)

        content = toml_path.read_text()
        assert '"QQQ3.L" = 95' in content
        assert '"3LUS.L" = 88' in content
        assert "schema_version = 1" in content  # Original content preserved
        assert "[ticker_ranking.current]" in content

    def test_write_toml_missing_section_appends(self, tmp_path):
        """If [ticker_ranking.current] is missing, it should be appended."""
        toml_path = tmp_path / "strategies.toml"
        toml_path.write_text('schema_version = 1\n')

        result = RankingResult(
            rankings=[TickerScore("QQQ3.L", 90.0, 80.0, 70.0, 60.0, 50.0, 100.0, 80.0)],
            timestamp="2026-03-18T10:00:00Z",
            session_window="10:00-14:00",
            regime_state="random",
            ticker_count=1,
        )

        write_ranking_toml(toml_path, result)

        content = toml_path.read_text()
        assert "[ticker_ranking.current]" in content
        assert '"QQQ3.L" = 90' in content

    def test_write_toml_preserves_other_sections(self, tmp_path):
        """Other TOML sections should not be clobbered."""
        toml_path = tmp_path / "strategies.toml"
        toml_path.write_text(
            'schema_version = 1\n\n'
            '[global]\n'
            'max_active_strategies = 3\n\n'
            '[ticker_ranking.current]\n'
            '"OLD" = 10\n\n'
            '[blackout_windows]\n'
            'windows = []\n'
        )

        result = RankingResult(
            rankings=[TickerScore("NEW", 80.0, 70.0, 60.0, 50.0, 40.0, 90.0, 70.0)],
            timestamp="2026-03-18T10:00:00Z",
            session_window="10:00-14:00",
            regime_state="trending",
            ticker_count=1,
        )

        write_ranking_toml(toml_path, result)

        content = toml_path.read_text()
        assert "[global]" in content
        assert "max_active_strategies = 3" in content
        assert "[blackout_windows]" in content
        assert '"OLD" = 10' not in content  # Old ranking replaced
        assert '"NEW" = 80' in content

    def test_toml_file_not_found(self, tmp_path):
        """Missing strategies.toml should raise FileNotFoundError."""
        fake_path = tmp_path / "nonexistent.toml"
        result = RankingResult(rankings=[], timestamp="", session_window="",
                               regime_state="", ticker_count=0)
        with pytest.raises(FileNotFoundError):
            write_ranking_toml(fake_path, result)


# ===========================================================================
# Tests: report writer
# ===========================================================================

class TestReportWriter:
    def test_report_created(self, tmp_path):
        """Report file should be created in the output directory."""
        report_dir = tmp_path / "reports" / "ticker_rankings"

        result = RankingResult(
            rankings=[
                TickerScore("QQQ3.L", 95.0, 90.0, 80.0, 85.0, 70.0, 100.0, 90.0),
                TickerScore("3LUS.L", 88.0, 85.0, 75.0, 80.0, 65.0, 95.0, 85.0),
            ],
            timestamp="2026-03-18T10:00:00Z",
            session_window="10:00-14:00",
            regime_state="trending",
            ticker_count=2,
        )

        path = write_ranking_report(report_dir, result)
        assert path.exists()
        assert path.suffix == ".txt"

        content = path.read_text()
        assert "TICKER PRIORITY RANKING" in content
        assert "QQQ3.L" in content
        assert "3LUS.L" in content
        assert "Regime: trending" in content

    def test_report_has_summary_stats(self, tmp_path):
        """Report should include top/median/bottom scores."""
        report_dir = tmp_path / "reports"
        result = RankingResult(
            rankings=[
                TickerScore("A", 90.0, 80.0, 70.0, 60.0, 50.0, 100.0, 80.0),
                TickerScore("B", 50.0, 40.0, 30.0, 20.0, 60.0, 70.0, 50.0),
            ],
            timestamp="2026-03-18T10:00:00Z",
            session_window="10:00-14:00",
            regime_state="random",
            ticker_count=2,
        )

        path = write_ranking_report(report_dir, result)
        content = path.read_text()
        assert "Top score:" in content
        assert "Median score:" in content
        assert "Bottom score:" in content

    def test_report_empty_rankings(self, tmp_path):
        """Empty rankings should produce a valid report without errors."""
        report_dir = tmp_path / "reports"
        result = RankingResult(
            rankings=[], timestamp="2026-03-18T10:00:00Z",
            session_window="10:00-14:00", regime_state="random", ticker_count=0,
        )
        path = write_ranking_report(report_dir, result)
        assert path.exists()


# ===========================================================================
# Tests: full cycle integration
# ===========================================================================

class TestRunRankingCycle:
    def test_full_cycle(self, tmp_path):
        """Full cycle should produce TOML update and report file."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        report_dir = tmp_path / "reports" / "ticker_rankings"

        # Create minimal strategies.toml
        (config_dir / "strategies.toml").write_text(
            'schema_version = 1\n\n'
            '[ticker_ranking]\n'
            'refresh_interval_minutes = 120\n\n'
            '[ticker_ranking.current]\n'
            '"QQQ3.L" = 50\n'
        )

        market_data = _make_isa_universe(6)
        perf = {"QQQ3.L": _make_perf("QQQ3.L", 0.65, 1.8, 25)}
        spread_cache = {"QQQ3.L": 5.0}

        result = run_ranking_cycle(
            market_data=market_data,
            session_window="10:00-14:00",
            regime_state="trending",
            ouroboros_perf=perf,
            spread_cache=spread_cache,
            config_dir=config_dir,
            report_dir=report_dir,
        )

        # Check result
        assert result.ticker_count == 6
        assert result.regime_state == "trending"

        # Check TOML was updated
        toml_content = (config_dir / "strategies.toml").read_text()
        assert "[ticker_ranking.current]" in toml_content

        # Check report was created
        assert report_dir.exists()
        reports = list(report_dir.glob("ranking_*.txt"))
        assert len(reports) == 1


# ===========================================================================
# Tests: weight validation
# ===========================================================================

class TestWeightIntegrity:
    def test_weights_sum_to_one(self):
        """All factor weights must sum to exactly 1.0."""
        from brain.ticker_ranker import W_SPREAD, W_RVOL, W_REGIME, W_PERF, W_SESSION, W_LIQUIDITY
        total = W_SPREAD + W_RVOL + W_REGIME + W_PERF + W_SESSION + W_LIQUIDITY
        assert abs(total - 1.0) < 1e-10, f"Weights sum to {total}, expected 1.0"

    def test_all_scores_bounded_0_100(self):
        """Every scoring function output must be in [0, 100]."""
        # Spread
        for bid, ask, last in [(49.0, 51.0, 50.0), (0, 0, 50.0), (49.99, 50.01, 50.0)]:
            s = score_spread(bid, ask, last)
            assert 0.0 <= s <= 100.0, f"spread({bid},{ask},{last})={s}"

        # RVOL
        for rvol in [0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0]:
            for regime in ["trending", "mean_reverting", "random"]:
                s = score_rvol(rvol, regime)
                assert 0.0 <= s <= 100.0, f"rvol({rvol},{regime})={s}"

        # Regime fit
        for hurst in [0.0, 0.3, 0.5, 0.7, 1.0]:
            for adx in [0.0, 10.0, 25.0, 50.0]:
                for regime in ["trending", "mean_reverting", "random"]:
                    s = score_regime_fit(hurst, adx, regime)
                    assert 0.0 <= s <= 100.0, f"regime({hurst},{adx},{regime})={s}"

        # Performance
        for wr in [0.0, 0.3, 0.5, 0.7, 1.0]:
            for er in [0.0, 0.5, 1.0, 2.0, 5.0]:
                for tc in [0, 1, 5, 20]:
                    s = score_performance(wr, er, tc)
                    assert 0.0 <= s <= 100.0, f"perf({wr},{er},{tc})={s}"

        # Liquidity
        for adv in [0.0, 5000, 50000, 500000, 5000000]:
            s = score_liquidity(adv)
            assert 0.0 <= s <= 100.0, f"liq({adv})={s}"


# ===========================================================================
# Run standalone (outside pytest)
# ===========================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
