"""Ouroboros Pipeline — nightly analytics orchestrator (P8-hardened).

Runs the complete 10-step nightly pipeline:
  0. GARCH calibration (optional, skips if arch/yfinance unavailable)
  1. Timing guard (refuse during LSE hours)
  2. Ingest WAL (read finished day's journal)
  3. Bayesian WR + DSR
  4. Kelly Accelerator
  5. Exit Calibration
  6. Regime Hunting
  7. Alpha Sieve (universe reclassification)
  8. Generate dynamic_weights.toml
  9. Generate universe_classification.toml + FX refresh
  10. Archive to parameter_history/ + validation

Quarantine rules:
  - NEVER writes to live WAL
  - NEVER influences live decisions in-session
  - Reads ONLY the finished day's journal
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .alpha_sieve import AlphaSieveResult, sieve_universe
from .bayesian import BayesianResult, DSRResult, bayesian_win_rate, deflated_sharpe_ratio
from .config import (
    CHANDELIER_ATR_MULT_DEFAULT,
    COLD_START_DAYS,
    DYNAMIC_WEIGHTS_FILE,
    LSE_CLOSE_SECS,
    LSE_OPEN_SECS,
    UNIVERSE_CLASS_FILE,
)
from .exit_calibration import ExitCalibrationResult, calibrate_exit_multiplier
from .kelly_accelerator import KellyUpdate, compute_kelly_updates
from .regime_hunting import RegimeHuntResult, hunt_regimes
from .toml_writer import (
    archive_results,
    write_default_dynamic_weights,
    write_default_universe_classification,
    write_dynamic_weights,
    write_fx_rates,
    write_universe_classification,
)
from .wal_reader import DayJournal, read_day_journal


# P8: Default FX rates (GBP base). Updated nightly if yfinance available.
DEFAULT_FX_RATES: Dict[str, float] = {
    "USDGBP": 0.79,
    "EURGBP": 0.86,
    "JPYGBP": 0.0053,
    "HKDGBP": 0.10,
    "AUDGBP": 0.52,
    "CHFGBP": 0.89,
    "SEKGBP": 0.074,
    "SGDGBP": 0.59,
}


@dataclass
class PipelineResult:
    """Output of a complete Ouroboros pipeline run."""
    success: bool
    bayesian: Optional[BayesianResult] = None
    dsr: Optional[DSRResult] = None
    kelly_updates: Optional[Dict[int, KellyUpdate]] = None
    exit_cal: Optional[ExitCalibrationResult] = None
    regime: Optional[RegimeHuntResult] = None
    alpha: Optional[AlphaSieveResult] = None
    dynamic_weights_path: str = ""
    universe_class_path: str = ""
    archive_path: str = ""
    fx_rates_path: str = ""
    error: str = ""
    cold_start: bool = False
    garch_calibrated: bool = False
    validation_errors: List[str] = field(default_factory=list)


def is_lse_open(time_secs: int) -> bool:
    """Check if LSE is currently in continuous trading."""
    return LSE_OPEN_SECS <= time_secs < LSE_CLOSE_SECS


def _try_garch_calibration(config_dir: Path, ticker_ids: Optional[Dict[str, int]] = None) -> bool:
    """P8: Attempt GARCH calibration. Returns True if successful, False if skipped."""
    try:
        from .step_0_garch_calibration import calibrate_universe
    except (ImportError, TypeError):
        # TypeError: Python < 3.10 with `dict | None` syntax in step_0
        print("P8: GARCH calibration skipped (arch/numpy unavailable)", file=sys.stderr)
        return False

    if ticker_ids is None:
        ticker_ids = {
            "QQQ3.L": 1, "3LUS.L": 2, "3SEM.L": 3, "GPT3.L": 4,
            "NVD3.L": 5, "TSL3.L": 6, "TSM3.L": 7, "MU2.L": 8,
            "QQQS.L": 9, "3USS.L": 10, "QQQ5.L": 11, "SP5L.L": 12,
        }

    output_path = str(config_dir / "garch_params.json")
    try:
        calibrate_universe(ticker_ids, lookback_days=60, output_path=output_path)
        return True
    except Exception as e:
        print(f"P8: GARCH calibration failed (non-fatal): {e}", file=sys.stderr)
        return False


def _refresh_fx_rates(config_dir: Path) -> str:
    """P8: Refresh FX rates. Uses yfinance if available, else defaults."""
    rates = dict(DEFAULT_FX_RATES)

    try:
        import yfinance as yf
        pairs = {
            "GBPUSD=X": "USDGBP",
            "GBPEUR=X": "EURGBP",
            "GBPJPY=X": "JPYGBP",
            "GBPHKD=X": "HKDGBP",
            "GBPAUD=X": "AUDGBP",
            "GBPCHF=X": "CHFGBP",
            "GBPSEK=X": "SEKGBP",
            "GBPSGD=X": "SGDGBP",
        }
        for symbol, key in pairs.items():
            try:
                data = yf.download(symbol, period="1d", progress=False, auto_adjust=True)
                if not data.empty:
                    close_col = data["Close"]
                    if hasattr(close_col, "columns"):
                        close_col = close_col.iloc[:, 0]
                    val = float(close_col.iloc[-1])
                    if val > 0:
                        rates[key] = 1.0 / val
            except Exception:
                pass  # Keep default
    except ImportError:
        pass  # yfinance unavailable — use defaults

    path = write_fx_rates(config_dir, rates)
    return str(path)


def _validate_artifacts(config_dir: Path, dw_path: Path, uc_path: Path, arch_path: Path) -> List[str]:
    """P8: Validate all pipeline artifacts exist and are non-empty."""
    errors = []

    for label, path in [("dynamic_weights", dw_path), ("universe_class", uc_path)]:
        if not path.exists():
            errors.append(f"{label}: file missing")
        elif path.stat().st_size == 0:
            errors.append(f"{label}: file empty")
        else:
            content = path.read_text()
            if "schema_version" not in content:
                errors.append(f"{label}: missing schema_version")

    if not arch_path.exists():
        errors.append("archive: file missing")
    elif arch_path.stat().st_size == 0:
        errors.append("archive: file empty")
    else:
        try:
            data = json.loads(arch_path.read_text())
            if "dynamic_weights" not in data or "universe_classification" not in data:
                errors.append("archive: missing expected keys")
        except json.JSONDecodeError:
            errors.append("archive: invalid JSON")

    return errors


def run_pipeline(
    wal_path: Path,
    config_dir: Path,
    london_time_secs: int,
    prior_kellys: Optional[Dict[int, float]] = None,
    prior_tiers: Optional[Dict[int, int]] = None,
    prior_chandelier_mult: float = CHANDELIER_ATR_MULT_DEFAULT,
    spread_data: Optional[Dict[int, float]] = None,
    day_count: int = 1,
    skip_garch: bool = False,
    ticker_ids: Optional[Dict[str, int]] = None,
) -> PipelineResult:
    """Run the complete Ouroboros nightly pipeline."""
    # Step 0: GARCH calibration (P8)
    garch_ok = False
    if not skip_garch and day_count > COLD_START_DAYS:
        garch_ok = _try_garch_calibration(config_dir, ticker_ids)

    # Step 1: Timing guard
    if is_lse_open(london_time_secs):
        return PipelineResult(
            success=False,
            error="Refused: LSE is open (08:00-16:30 London)",
        )

    # Step 2: Cold start check
    if day_count <= COLD_START_DAYS:
        return _cold_start_pipeline(config_dir, day_count)

    # Step 3: Ingest WAL
    journal = read_day_journal(wal_path)
    if journal is None or journal.total_events == 0:
        return PipelineResult(
            success=False,
            error=f"WAL not found or empty: {wal_path}",
        )

    return _run_analytics(
        journal, config_dir, prior_kellys or {},
        prior_tiers or {}, prior_chandelier_mult, spread_data,
        garch_ok,
    )


def _run_analytics(
    journal: DayJournal,
    config_dir: Path,
    prior_kellys: Dict[int, float],
    prior_tiers: Dict[int, int],
    prior_chandelier_mult: float,
    spread_data: Optional[Dict[int, float]],
    garch_calibrated: bool = False,
) -> PipelineResult:
    """Run all analytics steps on ingested journal data."""
    trades = journal.closed_trades
    pnls = [t.final_pnl for t in trades]

    # Step 3: Bayesian WR
    bwr = bayesian_win_rate(pnls)

    # Step 4: DSR
    returns = _pnls_to_returns(trades)
    dsr = deflated_sharpe_ratio(returns)

    # Step 5: Kelly Accelerator
    kelly_updates = compute_kelly_updates(trades, prior_kellys)

    # Step 6: Exit Calibration
    exit_cal = calibrate_exit_multiplier(trades, prior_chandelier_mult)

    # Step 7: Regime Hunting
    regime = hunt_regimes(trades)

    # Step 8: Alpha Sieve
    alpha = sieve_universe(trades, prior_tiers, spread_data)

    # Step 9: Generate TOML outputs
    dw_path = write_dynamic_weights(
        config_dir, bwr, dsr, kelly_updates, exit_cal, regime,
    )
    uc_path = write_universe_classification(config_dir, alpha, prior_tiers)

    # Step 9b (P8): FX refresh
    fx_path = _refresh_fx_rates(config_dir)

    # Step 10: Archive
    arch_path = archive_results(config_dir, dw_path, uc_path)

    # Step 10b (P8): Validate all artifacts
    validation_errors = _validate_artifacts(config_dir, dw_path, uc_path, arch_path)

    return PipelineResult(
        success=True,
        bayesian=bwr,
        dsr=dsr,
        kelly_updates=kelly_updates,
        exit_cal=exit_cal,
        regime=regime,
        alpha=alpha,
        dynamic_weights_path=str(dw_path),
        universe_class_path=str(uc_path),
        archive_path=str(arch_path),
        fx_rates_path=fx_path,
        garch_calibrated=garch_calibrated,
        validation_errors=validation_errors,
    )


def _cold_start_pipeline(config_dir: Path, day_count: int) -> PipelineResult:
    """Handle first N days when insufficient data exists."""
    dw_path = config_dir / DYNAMIC_WEIGHTS_FILE
    uc_path = config_dir / UNIVERSE_CLASS_FILE
    write_default_dynamic_weights(dw_path)
    write_default_universe_classification(uc_path)
    return PipelineResult(
        success=True,
        cold_start=True,
        dynamic_weights_path=str(dw_path),
        universe_class_path=str(uc_path),
    )


def _pnls_to_returns(trades: list) -> List[float]:
    """Convert PnL to percentage returns."""
    returns = []
    for t in trades:
        if t.entry_price > 0 and t.qty > 0:
            notional = t.entry_price * t.qty
            returns.append(t.final_pnl / notional)
        elif t.final_pnl != 0:
            returns.append(0.01 if t.final_pnl > 0 else -0.01)
    return returns
