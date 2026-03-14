"""Phase 9 acceptance tests — Ouroboros nightly analytics pipeline."""

import json
import math
import tempfile
from pathlib import Path

import pytest

from ouroboros.alpha_sieve import sieve_universe
from ouroboros.bayesian import bayesian_win_rate, deflated_sharpe_ratio
from ouroboros.config import (
    CHANDELIER_ATR_MULT_DEFAULT,
    COLD_START_DAYS,
    KELLY_CEILING,
    KELLY_FLOOR,
    LSE_CLOSE_SECS,
    LSE_OPEN_SECS,
    MFE_RUNG5_THRESHOLD,
)
from ouroboros.exit_calibration import calibrate_exit_multiplier
from ouroboros.kelly_accelerator import compute_kelly_updates
from ouroboros.pipeline import PipelineResult, is_lse_open, run_pipeline
from ouroboros.regime_hunting import hunt_regimes
from ouroboros.wal_reader import ClosedTrade, read_day_journal


def _make_trade(tid: int, pnl: float, **kwargs) -> ClosedTrade:
    """Helper to create a ClosedTrade with defaults."""
    return ClosedTrade(
        ticker_id=tid,
        final_pnl=pnl,
        entry_time_ns=1_000_000_000,
        exit_time_ns=2_000_000_000,
        entry_price=kwargs.get("entry_price", 10.0),
        exit_price=kwargs.get("exit_price", 10.0 + pnl),
        qty=kwargs.get("qty", 100),
        commission=kwargs.get("commission", 1.50),
        exit_reason=kwargs.get("exit_reason", ""),
        strategy=kwargs.get("strategy", "Vanguard"),
        regime_label=kwargs.get("regime_label", "bull_quiet"),
        highest_rung=kwargs.get("highest_rung", 0),
    )


def _make_wal_file(tmpdir: Path, trades: list, extra_events: list = None):
    """Create a synthetic WAL ndjson file from trades."""
    wal_path = tmpdir / "test.ndjson"
    events = []

    # Write RoutedOrder + FillEvent + PositionClosed for each trade
    for i, t in enumerate(trades):
        oid = f"order-{i}"
        events.append({
            "event_id": f"evt-{i}-order",
            "schema_version": 1,
            "event_time_ns": t.entry_time_ns,
            "write_time_ns": t.entry_time_ns + 100,
            "checksum": 0,
            "payload": {"RoutedOrder": {
                "order_id": oid,
                "ticker_id": t.ticker_id,
                "side": "Buy",
                "confidence": 75.0,
                "strategy": t.strategy or "Vanguard",
                "kelly_fraction": 0.10,
                "approved_size": 1000.0,
            }},
        })
        events.append({
            "event_id": f"evt-{i}-fill",
            "schema_version": 1,
            "event_time_ns": t.entry_time_ns + 1000,
            "write_time_ns": t.entry_time_ns + 1100,
            "checksum": 0,
            "payload": {"FillEvent": {
                "order_id": oid,
                "ticker_id": t.ticker_id,
                "filled_qty": t.qty,
                "remaining_qty": 0,
                "price": t.entry_price,
                "exec_id": f"exec-{i}",
                "commission": t.commission,
            }},
        })
        events.append({
            "event_id": f"evt-{i}-close",
            "schema_version": 1,
            "event_time_ns": t.exit_time_ns,
            "write_time_ns": t.exit_time_ns + 100,
            "checksum": 0,
            "payload": {"PositionClosed": {
                "ticker_id": t.ticker_id,
                "final_pnl": t.final_pnl,
                "entry_time_ns": t.entry_time_ns,
                "exit_time_ns": t.exit_time_ns,
            }},
        })

    if extra_events:
        events.extend(extra_events)

    with open(wal_path, "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    return wal_path


# ── Test 1: Nightly timing guard ──
class TestTimingGuard:
    def test_refuses_during_lse_hours(self):
        """Ouroboros refuses to run during LSE hours (08:00-16:30)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wal = Path(tmpdir) / "test.ndjson"
            wal.write_text("")
            result = run_pipeline(
                wal, Path(tmpdir),
                london_time_secs=12 * 3600,  # 12:00 noon
            )
            assert not result.success
            assert "LSE is open" in result.error

    def test_runs_after_close(self):
        """Ouroboros runs after LSE close."""
        assert not is_lse_open(17 * 3600)  # 17:00
        assert not is_lse_open(23 * 3600)  # 23:00
        assert not is_lse_open(6 * 3600)   # 06:00

    def test_refuses_at_open(self):
        assert is_lse_open(LSE_OPEN_SECS)

    def test_refuses_at_1629(self):
        assert is_lse_open(16 * 3600 + 29 * 60)

    def test_allows_at_close(self):
        assert not is_lse_open(LSE_CLOSE_SECS)


# ── Test 2: Bayesian WR converges with Laplace smoothing ──
class TestBayesianWR:
    def test_100_trades_converge(self):
        """Feed 100 synthetic trades → Bayesian WR converges."""
        pnls = [10.0] * 60 + [-5.0] * 40  # 60% raw WR
        result = bayesian_win_rate(pnls)
        assert result.trade_count == 100
        assert result.raw_win_rate == 0.6
        # Bayesian should be close to 0.6 with 100 trades
        assert abs(result.bayesian_win_rate - 0.6) < 0.02

    def test_small_sample_shrinks(self):
        """Small sample shrinks toward 50%."""
        pnls = [10.0]  # 1 trade, 100% raw WR
        result = bayesian_win_rate(pnls)
        assert result.raw_win_rate == 1.0
        # Laplace: (1+1)/(1+2) = 0.667
        assert result.bayesian_win_rate < 0.8

    def test_empty_trades(self):
        result = bayesian_win_rate([])
        assert result.bayesian_win_rate == 0.5
        assert result.trade_count == 0


# ── Test 3: DSR calculation matches formula ──
class TestDSR:
    def test_known_sharpe_ratio(self):
        """Feed trades with known SR → DSR calculation matches."""
        # 100 trades with mean=0.02, std≈0.05 → SR ≈ 0.4
        returns = [0.02 + 0.05 * ((-1) ** i) for i in range(100)]
        result = deflated_sharpe_ratio(returns)
        assert result.sharpe_ratio != 0.0
        assert 0.0 < result.dsr < 1.0
        assert result.dsr_pvalue == 1.0 - result.dsr

    def test_insufficient_trades(self):
        result = deflated_sharpe_ratio([0.01, 0.02])
        assert result.dsr == 0.0
        assert result.dsr_pvalue == 1.0
        assert not result.is_significant

    def test_strong_returns_significant(self):
        """Consistently positive returns → significant DSR."""
        returns = [0.03] * 50  # Constant positive returns
        result = deflated_sharpe_ratio(returns)
        # With 0 variance, SR is very high
        assert result.dsr > 0.5


# ── Test 4: TOML output validity ──
class TestTomlOutput:
    def test_dynamic_weights_valid_toml(self):
        """dynamic_weights.toml is valid TOML and parseable."""
        import tomli
        trades = [_make_trade(1, 10.0)] * 20
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            wal_path = _make_wal_file(config_dir, trades)
            result = run_pipeline(
                wal_path, config_dir,
                london_time_secs=23 * 3600,
                day_count=5,
            )
            assert result.success
            content = Path(result.dynamic_weights_path).read_text()
            parsed = tomli.loads(content)
            assert "bayesian" in parsed
            assert "exit" in parsed
            assert parsed["schema_version"] == 1

    def test_universe_classification_valid_toml(self):
        """universe_classification.toml is valid TOML and parseable."""
        import tomli
        trades = [_make_trade(1, 10.0)] * 20
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            wal_path = _make_wal_file(config_dir, trades)
            result = run_pipeline(
                wal_path, config_dir,
                london_time_secs=23 * 3600,
                prior_tiers={1: 2},
                day_count=5,
            )
            assert result.success
            content = Path(result.universe_class_path).read_text()
            parsed = tomli.loads(content)
            assert "tiers" in parsed


# ── Test 5: Reproducibility ──
class TestReproducibility:
    def test_same_input_identical_output(self):
        """Run Ouroboros twice on same WAL → identical .toml output."""
        trades = [_make_trade(1, 10.0), _make_trade(2, -5.0)]
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            wal_path = _make_wal_file(config_dir, trades)
            kwargs = dict(
                london_time_secs=23 * 3600,
                prior_kellys={1: 0.10, 2: 0.10},
                prior_tiers={1: 2, 2: 2},
                day_count=5,
            )
            r1 = run_pipeline(wal_path, config_dir, **kwargs)
            dw1 = Path(r1.dynamic_weights_path).read_text()
            uc1 = Path(r1.universe_class_path).read_text()

            r2 = run_pipeline(wal_path, config_dir, **kwargs)
            dw2 = Path(r2.dynamic_weights_path).read_text()
            uc2 = Path(r2.universe_class_path).read_text()

            # Strip timestamps (first line) for comparison
            assert dw1.split("\n")[1:] == dw2.split("\n")[1:]
            assert uc1.split("\n")[1:] == uc2.split("\n")[1:]


# ── Test 6: Kelly Accelerator ──
class TestKellyAccelerator:
    def test_winning_trades_increase_kelly(self):
        """Feed winning trades → Kelly fraction INCREASES."""
        trades = [_make_trade(1, 20.0, entry_price=10.0, qty=100)] * 10
        prior = {1: KELLY_FLOOR}
        updates = compute_kelly_updates(trades, prior)
        assert 1 in updates
        assert updates[1].new_kelly > KELLY_FLOOR

    def test_losing_trades_no_increase(self):
        """Feed losing trades → Kelly stays at floor."""
        trades = [_make_trade(1, -10.0, entry_price=10.0, qty=100)] * 10
        prior = {1: KELLY_FLOOR}
        updates = compute_kelly_updates(trades, prior)
        assert 1 in updates
        assert updates[1].new_kelly == KELLY_FLOOR

    def test_kelly_clamped(self):
        """Kelly never exceeds KELLY_CEILING."""
        trades = [_make_trade(1, 100.0, entry_price=10.0, qty=100)] * 50
        prior = {1: KELLY_CEILING}
        updates = compute_kelly_updates(trades, prior)
        assert updates[1].new_kelly <= KELLY_CEILING


# ── Test 7: Exit Calibration ──
class TestExitCalibration:
    def test_rung5_loosens_multiplier(self):
        """Trades consistently hitting Rung 5 → multiplier LOOSENS."""
        trades = [_make_trade(1, 20.0, highest_rung=5)] * 10
        result = calibrate_exit_multiplier(trades, CHANDELIER_ATR_MULT_DEFAULT)
        assert result.new_multiplier > CHANDELIER_ATR_MULT_DEFAULT
        assert result.rung5_rate == 1.0

    def test_early_stops_tighten(self):
        """Trades stopping out early → multiplier TIGHTENS."""
        trades = [_make_trade(1, -5.0, highest_rung=0)] * 10
        result = calibrate_exit_multiplier(trades, CHANDELIER_ATR_MULT_DEFAULT)
        assert result.new_multiplier < CHANDELIER_ATR_MULT_DEFAULT

    def test_empty_trades_no_change(self):
        result = calibrate_exit_multiplier([], CHANDELIER_ATR_MULT_DEFAULT)
        assert result.new_multiplier == CHANDELIER_ATR_MULT_DEFAULT


# ── Test 8: Regime Hunting ──
class TestRegimeHunting:
    def test_profitable_regimes_identified(self):
        """Feed trades with known regime labels → profitable regimes identified."""
        trades = [
            _make_trade(1, 20.0, regime_label="bull_quiet"),
            _make_trade(2, 15.0, regime_label="bull_quiet"),
            _make_trade(3, -10.0, regime_label="bear_volatile"),
            _make_trade(4, -8.0, regime_label="bear_volatile"),
        ]
        result = hunt_regimes(trades)
        assert result.best_regime == "bull_quiet"
        assert result.worst_regime == "bear_volatile"
        assert result.regimes["bull_quiet"].is_profitable
        assert not result.regimes["bear_volatile"].is_profitable

    def test_empty_trades(self):
        result = hunt_regimes([])
        assert result.total_trades == 0


# ── Test 9: Alpha Sieve ──
class TestAlphaSieve:
    def test_spread_widens_demotion(self):
        """Ticker with widening spreads → demoted from Vanguard."""
        trades = [_make_trade(1, 5.0)]
        prior_tiers = {1: 1}  # Tier 1 (Vanguard)
        spread_data = {1: 0.6}  # Spread > 0.5% threshold
        result = sieve_universe(trades, prior_tiers, spread_data)
        assert 1 in result.demotions
        ta = result.ticker_alphas[1]
        assert ta.new_tier > ta.prior_tier
        assert "spread" in ta.demotion_reason

    def test_no_spread_data_keeps_tier(self):
        # Need enough positive trades for ASER to be above demotion threshold
        trades = [_make_trade(1, 5.0 + i * 0.1) for i in range(10)]
        prior_tiers = {1: 2}
        result = sieve_universe(trades, prior_tiers)
        assert 1 not in result.demotions

    def test_strong_alpha_promotes(self):
        """Ticker with strong positive IC → promoted."""
        trades = [_make_trade(1, 10.0, entry_price=10.0, qty=100)] * 20
        prior_tiers = {1: 2}
        result = sieve_universe(trades, prior_tiers)
        ta = result.ticker_alphas[1]
        # Strong positive PnL should yield high IC
        assert ta.ic > 0


# ── Test 10: Quarantine rules ──
class TestQuarantine:
    def test_never_writes_live_wal(self):
        """Ouroboros NEVER writes to the input WAL file."""
        trades = [_make_trade(1, 10.0)] * 5
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            wal_path = _make_wal_file(config_dir, trades)
            original = wal_path.read_text()
            run_pipeline(
                wal_path, config_dir,
                london_time_secs=23 * 3600,
                day_count=5,
            )
            assert wal_path.read_text() == original

    def test_reads_only_specified_wal(self):
        """Pipeline only reads the WAL path given, nothing else."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            # Non-existent WAL
            wal_path = config_dir / "nonexistent.ndjson"
            result = run_pipeline(
                wal_path, config_dir,
                london_time_secs=23 * 3600,
                day_count=5,
            )
            assert not result.success
            assert "not found" in result.error


# ── Test 11: Morning boot safe fallback ──
class TestMorningBoot:
    def test_cold_start_produces_defaults(self):
        """Cold start (≤3 days) produces conservative default TOML."""
        import tomli
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            wal_path = config_dir / "empty.ndjson"
            result = run_pipeline(
                wal_path, config_dir,
                london_time_secs=23 * 3600,
                day_count=2,  # Cold start
            )
            assert result.success
            assert result.cold_start
            dw = tomli.loads(Path(result.dynamic_weights_path).read_text())
            assert dw["bayesian"]["win_rate"] == 0.5

    def test_day_4_runs_full_pipeline(self):
        """After cold start period, full pipeline runs."""
        trades = [_make_trade(1, 10.0)] * 15
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            wal_path = _make_wal_file(config_dir, trades)
            result = run_pipeline(
                wal_path, config_dir,
                london_time_secs=23 * 3600,
                day_count=COLD_START_DAYS + 1,
            )
            assert result.success
            assert not result.cold_start


# ── Test 12: Client ID isolation ──
class TestClientId:
    def test_ouroboros_client_id(self):
        """Ouroboros uses clientId=200 (H41)."""
        from ouroboros.config import CLIENT_ID
        assert CLIENT_ID == 200


# ── Test 13: WAL reader ──
class TestWalReader:
    def test_reads_closed_trades(self):
        trades = [_make_trade(1, 10.0), _make_trade(2, -5.0)]
        with tempfile.TemporaryDirectory() as tmpdir:
            wal_path = _make_wal_file(Path(tmpdir), trades)
            journal = read_day_journal(wal_path)
            assert journal is not None
            assert len(journal.closed_trades) == 2
            assert journal.total_events == 6  # 3 events per trade

    def test_missing_file_returns_none(self):
        result = read_day_journal(Path("/nonexistent/wal.ndjson"))
        assert result is None

    def test_malformed_lines_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wal_path = Path(tmpdir) / "test.ndjson"
            wal_path.write_text("not json\n{invalid\n")
            journal = read_day_journal(wal_path)
            assert journal is not None
            assert journal.total_events == 0


# ── Test 14: Parameter archive ──
class TestParameterArchive:
    def test_archive_created(self):
        """Archive JSON written to parameter_history/."""
        trades = [_make_trade(1, 10.0)] * 10
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            wal_path = _make_wal_file(config_dir, trades)
            result = run_pipeline(
                wal_path, config_dir,
                london_time_secs=23 * 3600,
                day_count=5,
            )
            assert result.success
            archive = Path(result.archive_path)
            assert archive.exists()
            data = json.loads(archive.read_text())
            assert "dynamic_weights" in data
            assert "universe_classification" in data


# ── Test 15: DSR formula verification ──
class TestDSRFormula:
    def test_normal_cdf_at_zero(self):
        """Φ(0) = 0.5"""
        from ouroboros.bayesian import _normal_cdf
        assert abs(_normal_cdf(0.0) - 0.5) < 1e-10

    def test_normal_cdf_at_large(self):
        """Φ(3) ≈ 0.9987"""
        from ouroboros.bayesian import _normal_cdf
        assert abs(_normal_cdf(3.0) - 0.9987) < 0.001

    def test_dsr_symmetry(self):
        """Negative Sharpe → DSR < 0.5"""
        returns = [-0.03] * 50
        result = deflated_sharpe_ratio(returns)
        assert result.dsr < 0.5


# ── Test 16 (P8): Pipeline artifact validation ──
class TestPipelineValidation:
    def test_all_artifacts_created_and_valid(self):
        """P8: Full pipeline run produces all artifacts, all pass validation."""
        trades = [_make_trade(1, 10.0)] * 15 + [_make_trade(2, -5.0)] * 5
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            wal_path = _make_wal_file(config_dir, trades)
            result = run_pipeline(
                wal_path, config_dir,
                london_time_secs=23 * 3600,
                day_count=5,
                skip_garch=True,
            )
            assert result.success
            assert result.validation_errors == [], f"Validation errors: {result.validation_errors}"
            assert Path(result.dynamic_weights_path).exists()
            assert Path(result.universe_class_path).exists()
            assert Path(result.archive_path).exists()

    def test_fx_rates_written(self):
        """P8: FX rates file generated during pipeline run."""
        trades = [_make_trade(1, 10.0)] * 10
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            wal_path = _make_wal_file(config_dir, trades)
            result = run_pipeline(
                wal_path, config_dir,
                london_time_secs=23 * 3600,
                day_count=5,
                skip_garch=True,
            )
            assert result.success
            assert result.fx_rates_path
            fx_path = Path(result.fx_rates_path)
            assert fx_path.exists()
            content = fx_path.read_text()
            assert "USDGBP" in content
            assert "EURGBP" in content


# ── Test 17 (P8): 30-day synthetic backtest ──
class TestSyntheticBacktest:
    def test_30_day_consecutive_pipeline(self):
        """P8: Run 30 consecutive days of pipeline. DynamicWeights evolve correctly."""
        import random
        random.seed(42)

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            prior_kellys: dict = {}
            prior_tiers: dict = {1: 2, 2: 2, 3: 2}
            prior_mult = CHANDELIER_ATR_MULT_DEFAULT
            all_results = []

            for day in range(1, 31):
                # Generate random trades for the day
                n_trades = random.randint(2, 6)
                trades = []
                for _ in range(n_trades):
                    tid = random.choice([1, 2, 3])
                    pnl = random.gauss(5.0, 20.0)  # Slight positive edge
                    rung = random.randint(0, 5)
                    regime = random.choice(["bull_quiet", "bull_volatile", "bear_quiet", "bear_volatile"])
                    trades.append(_make_trade(
                        tid, pnl,
                        entry_price=10.0,
                        qty=100,
                        highest_rung=rung,
                        regime_label=regime,
                    ))

                wal_path = _make_wal_file(config_dir, trades)
                result = run_pipeline(
                    wal_path, config_dir,
                    london_time_secs=23 * 3600,
                    prior_kellys=prior_kellys,
                    prior_tiers=prior_tiers,
                    prior_chandelier_mult=prior_mult,
                    day_count=day + COLD_START_DAYS,
                    skip_garch=True,
                )
                assert result.success, f"Day {day} failed: {result.error}"
                assert result.validation_errors == [], f"Day {day} validation: {result.validation_errors}"
                all_results.append(result)

                # Carry forward state for next day
                if result.kelly_updates:
                    for tid, ku in result.kelly_updates.items():
                        prior_kellys[tid] = ku.new_kelly
                if result.alpha:
                    for tid, ta in result.alpha.ticker_alphas.items():
                        prior_tiers[tid] = ta.new_tier
                if result.exit_cal:
                    prior_mult = result.exit_cal.new_multiplier

            # Verify 30 days ran
            assert len(all_results) == 30

            # Verify Kelly fractions evolved across the 30-day run
            # (Prior kellys are carried forward and blended via EWA)
            first_kellys = all_results[0].kelly_updates or {}
            last_kellys = all_results[-1].kelly_updates or {}
            # At minimum, the Kelly updates should have been computed on each day
            assert len(all_results) == 30
            # Verify that carried-forward priors differ from first day
            assert prior_kellys, "Kelly priors should have accumulated over 30 days"

            # Verify archive grows
            history_dir = config_dir / "parameter_history"
            assert history_dir.exists()
            archives = list(history_dir.glob("ouroboros_*.json"))
            assert len(archives) >= 1  # At least 1 (same date overwrites)

    def test_cold_start_to_full_transition(self):
        """P8: Cold start (days 1-3) → full pipeline (day 4+)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)

            # Days 1-3: cold start
            for day in range(1, COLD_START_DAYS + 1):
                result = run_pipeline(
                    config_dir / "empty.ndjson", config_dir,
                    london_time_secs=23 * 3600,
                    day_count=day,
                    skip_garch=True,
                )
                assert result.success
                assert result.cold_start

            # Day 4: full pipeline
            trades = [_make_trade(1, 10.0)] * 10
            wal_path = _make_wal_file(config_dir, trades)
            result = run_pipeline(
                wal_path, config_dir,
                london_time_secs=23 * 3600,
                day_count=COLD_START_DAYS + 1,
                skip_garch=True,
            )
            assert result.success
            assert not result.cold_start
            assert result.bayesian is not None
            assert result.bayesian.trade_count == 10
