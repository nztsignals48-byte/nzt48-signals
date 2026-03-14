"""
tests/test_daily_target.py
============================
Tests for S15 Daily Target strategy.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestDailyTargetConstants:
    def test_daily_target_is_2_pct(self):
        from strategies.daily_target import _DAILY_TARGET_PCT
        assert _DAILY_TARGET_PCT == 2.0

    def test_max_intraday_target_exists(self):
        from strategies.daily_target import _MAX_INTRADAY_TARGET
        assert _MAX_INTRADAY_TARGET == 6.0

    def test_runner_rvol_threshold(self):
        from strategies.daily_target import _USE_RUNNER_IF_RVOL_GT
        assert _USE_RUNNER_IF_RVOL_GT == 2.0

    def test_core_tickers_count(self):
        from strategies.daily_target import _CORE_TICKERS
        assert len(_CORE_TICKERS) == 12

    def test_min_rr_ratio(self):
        from strategies.daily_target import _MIN_RR_RATIO
        assert _MIN_RR_RATIO >= 1.5

    def test_inverse_etps_defined(self):
        from strategies.daily_target import _INVERSE_ETPS
        assert "QQQS.L" in _INVERSE_ETPS
        assert "3USS.L" in _INVERSE_ETPS
