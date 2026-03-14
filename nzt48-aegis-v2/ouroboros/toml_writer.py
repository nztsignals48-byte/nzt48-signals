"""TOML output writers for Ouroboros artifacts.

Generates dynamic_weights.toml, universe_classification.toml,
and archives to parameter_history/.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from .alpha_sieve import AlphaSieveResult
from .bayesian import BayesianResult, DSRResult
from .config import (
    CHANDELIER_ATR_MULT_DEFAULT,
    DYNAMIC_WEIGHTS_FILE,
    PARAMETER_HISTORY_DIR,
    SCHEMA_VERSION,
    UNIVERSE_CLASS_FILE,
)
from .exit_calibration import ExitCalibrationResult
from .kelly_accelerator import KellyUpdate
from .regime_hunting import RegimeHuntResult


# P2-B: Track all written files for flush_all() cleanup.
_written_files: List[Path] = []


def _write_and_track(path: Path, content: str) -> None:
    """Write content to file with fsync, tracking for flush_all()."""
    with open(path, "w") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    _written_files.append(path)


def flush_all() -> None:
    """P2-B: Ensure all TOML files written this session are fsynced.

    Called from cli.py finally block and atexit handler.
    Safe to call multiple times (idempotent).
    """
    for path in _written_files:
        try:
            if path.exists():
                fd = os.open(str(path), os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except OSError:
            pass  # Best-effort — file may have been removed
    _written_files.clear()


def write_dynamic_weights(
    config_dir: Path,
    bwr: BayesianResult,
    dsr: DSRResult,
    kelly_updates: Dict[int, KellyUpdate],
    exit_cal: ExitCalibrationResult,
    regime: RegimeHuntResult,
) -> Path:
    """Write dynamic_weights.toml."""
    path = config_dir / DYNAMIC_WEIGHTS_FILE
    lines = [
        f"# Ouroboros dynamic weights — generated {datetime.utcnow().isoformat()}Z",
        f"schema_version = {SCHEMA_VERSION}",
        "",
        "[bayesian]",
        f"win_rate = {bwr.bayesian_win_rate:.6f}",
        f"trade_count = {bwr.trade_count}",
        f"sharpe_ratio = {dsr.sharpe_ratio:.6f}",
        f"dsr = {dsr.dsr:.6f}",
        f"dsr_significant = {str(dsr.is_significant).lower()}",
        "",
        "[exit]",
        f"chandelier_atr_mult = {exit_cal.new_multiplier:.2f}",
        f"rung5_rate = {exit_cal.rung5_rate:.4f}",
        "",
        "[regime]",
        f'best = "{regime.best_regime}"',
        f'worst = "{regime.worst_regime}"',
    ]

    for label, stats in regime.regimes.items():
        scale = 1.0 if stats.is_profitable else 0.5
        lines.append(f'{label} = {scale:.2f}')

    if kelly_updates:
        lines.append("")
        lines.append("[kelly_fractions]")
        for tid, ku in sorted(kelly_updates.items()):
            lines.append(f"t{tid} = {ku.new_kelly:.6f}")

    lines.append("")
    _write_and_track(path, "\n".join(lines) + "\n")
    return path


def write_universe_classification(
    config_dir: Path,
    alpha: AlphaSieveResult,
    prior_tiers: Dict[int, int],
) -> Path:
    """Write universe_classification.toml."""
    path = config_dir / UNIVERSE_CLASS_FILE
    tier1, tier2, tier3, locked_ids = [], [], [], []

    all_tickers = set(prior_tiers.keys()) | set(alpha.ticker_alphas.keys())
    for tid in sorted(all_tickers):
        if tid in alpha.ticker_alphas:
            ta = alpha.ticker_alphas[tid]
            tier = ta.new_tier
            if ta.locked:
                locked_ids.append(tid)
        else:
            tier = prior_tiers.get(tid, 2)

        if tier == 1:
            tier1.append(tid)
        elif tier == 3:
            tier3.append(tid)
        else:
            tier2.append(tid)

    lines = [
        f"# Ouroboros universe — generated {datetime.utcnow().isoformat()}Z",
        f"schema_version = {SCHEMA_VERSION}",
        "",
        "[tiers]",
        f"tier1 = {tier1}",
        f"tier2 = {tier2}",
        f"tier3 = {tier3}",
        f"locked = {locked_ids}",
        "",
    ]
    _write_and_track(path, "\n".join(lines) + "\n")
    return path


def write_default_dynamic_weights(path: Path) -> None:
    """Write conservative default weights for cold start."""
    lines = [
        "# Ouroboros cold start — conservative defaults",
        f"schema_version = {SCHEMA_VERSION}",
        "",
        "[bayesian]",
        "win_rate = 0.500000",
        "trade_count = 0",
        "sharpe_ratio = 0.000000",
        "dsr = 0.000000",
        "dsr_significant = false",
        "",
        "[exit]",
        f"chandelier_atr_mult = {CHANDELIER_ATR_MULT_DEFAULT:.2f}",
        "rung5_rate = 0.0000",
        "",
        "[regime]",
        'best = "bull_quiet"',
        'worst = "bear_volatile"',
        "bull_quiet = 1.00",
        "bull_volatile = 0.50",
        "bear_quiet = 0.50",
        "bear_volatile = 0.50",
        "",
    ]
    _write_and_track(path, "\n".join(lines) + "\n")


def write_default_universe_classification(path: Path) -> None:
    """Write default universe classification for cold start."""
    lines = [
        "# Ouroboros cold start — all tickers in tier 2",
        f"schema_version = {SCHEMA_VERSION}",
        "",
        "[tiers]",
        "tier1 = []",
        "tier2 = []",
        "tier3 = []",
        "locked = []",
        "",
    ]
    _write_and_track(path, "\n".join(lines) + "\n")


def write_fx_rates(config_dir: Path, rates: Dict[str, float]) -> Path:
    """P8: Write fx_rates.toml for engine consumption."""
    path = config_dir / "fx_rates.toml"
    lines = [
        f"# FX rates (GBP base) — generated {datetime.utcnow().isoformat()}Z",
        "",
        "[rates]",
    ]
    for pair, rate in sorted(rates.items()):
        lines.append(f'{pair} = {rate:.6f}')
    lines.append("")
    _write_and_track(path, "\n".join(lines) + "\n")
    return path


def archive_results(
    config_dir: Path,
    dw_path: Path,
    uc_path: Path,
) -> Path:
    """Archive generated TOML files to parameter_history/."""
    history_dir = config_dir / PARAMETER_HISTORY_DIR
    history_dir.mkdir(exist_ok=True)
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    archive_path = history_dir / f"ouroboros_{date_str}.json"

    archive = {
        "date": date_str,
        "schema_version": SCHEMA_VERSION,
        "dynamic_weights": dw_path.read_text(),
        "universe_classification": uc_path.read_text(),
    }
    _write_and_track(archive_path, json.dumps(archive, indent=2) + "\n")
    return archive_path
