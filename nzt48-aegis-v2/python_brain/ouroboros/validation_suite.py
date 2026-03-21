"""VAL-3 + VAL-4 — Walk-Forward CPCV Validation & Regime Stress Testing.

VAL-3: Combinatorial Purged Cross-Validation (CPCV)
    Reference: de Prado (2018) "Advances in Financial Machine Learning" Ch.12
    - k=5 time-ordered folds, purge embargo = 1 hour
    - All C(k, k-1) = 5 train/test splits
    - Per-split metrics: WR, PF, Sharpe, max DD
    - Overfit detection: train WR > test WR by >15%

VAL-4: Regime Stress Testing
    - Per-regime trade breakdown (VIX-inferred or WAL regime field)
    - Monte Carlo bootstrap (1000 samples) per regime
    - VaR(95%) and CVaR(95%) per regime
    - Profitable vs unprofitable regime flagging

QUARANTINE MODULE: read-only analytics, generates reports only.
No side effects on engine state, WAL, or risk parameters.

Usage:
    python3 -m python_brain.ouroboros.validation_suite --cpcv
    python3 -m python_brain.ouroboros.validation_suite --regime-stress
    python3 -m python_brain.ouroboros.validation_suite --all
    python3 -m python_brain.ouroboros.validation_suite --json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Path setup — works both locally and in Docker (/app)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
REPORTS_DIR = DATA_DIR / "validation_reports"

# CPCV defaults
DEFAULT_K_FOLDS = 5
EMBARGO_NS = 1 * 3600 * 1_000_000_000  # 1 hour in nanoseconds
OVERFIT_THRESHOLD_PCT = 15.0  # train WR - test WR > 15% => overfit warning

# Regime VIX thresholds (fallback when WAL has no regime field)
VIX_CALM_CEIL = 15.0
VIX_NORMAL_CEIL = 25.0
VIX_STRESSED_CEIL = 35.0
REGIME_VIX_LABELS = {
    "calm": f"VIX < {VIX_CALM_CEIL}",
    "normal": f"{VIX_CALM_CEIL} <= VIX < {VIX_NORMAL_CEIL}",
    "stressed": f"{VIX_NORMAL_CEIL} <= VIX < {VIX_STRESSED_CEIL}",
    "crisis": f"VIX >= {VIX_STRESSED_CEIL}",
}

# Monte Carlo
MC_BOOTSTRAP_SAMPLES = 1000
MC_RNG_SEED = 42

# Annualisation factor (252 trading days)
ANNUALISE_FACTOR = 252

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ValidationSuite] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("validation_suite")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class ClosedTrade:
    """A single closed trade extracted from WAL PositionClosed events."""
    ticker: str
    ticker_id: int
    entry_time_ns: int
    exit_time_ns: int
    entry_price: float
    exit_price: float
    confidence: float
    pnl: float
    trade_class: str
    regime_at_entry: str
    strategy: str
    hold_time_mins: int
    rung_achieved: int
    exchange: str


@dataclass
class FoldMetrics:
    """Metrics computed on a single fold (train or test)."""
    fold_id: str
    n_trades: int
    win_rate: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown: float
    total_pnl: float
    avg_pnl: float
    avg_confidence: float


@dataclass
class CPCVResult:
    """Aggregate result from all CPCV splits."""
    k_folds: int
    n_splits: int
    total_trades: int
    embargo_hours: float
    purged_count: int

    # Per-split results
    splits: List[Dict[str, Any]]

    # Aggregate test metrics (mean +/- std across splits)
    test_wr_mean: float
    test_wr_std: float
    test_pf_mean: float
    test_pf_std: float
    test_sharpe_mean: float
    test_sharpe_std: float
    test_max_dd_mean: float
    test_max_dd_std: float

    # Aggregate train metrics
    train_wr_mean: float
    train_wr_std: float

    # Overfit detection
    overfit_detected: bool
    overfit_gap_pct: float  # train_wr_mean - test_wr_mean
    overfit_warning: str

    generated_at: str


@dataclass
class RegimeMetrics:
    """Per-regime performance metrics."""
    regime: str
    regime_description: str
    n_trades: int
    win_rate: float
    profit_factor: float
    avg_pnl: float
    total_pnl: float
    max_drawdown: float
    avg_hold_time_mins: float
    avg_confidence: float
    is_profitable: bool

    # Monte Carlo bootstrap results
    var_95: float  # Value at Risk (95th percentile loss)
    cvar_95: float  # Conditional VaR (expected loss beyond VaR)
    mc_daily_return_mean: float
    mc_daily_return_std: float
    mc_daily_return_p5: float
    mc_daily_return_p50: float
    mc_daily_return_p95: float


@dataclass
class RegimeStressResult:
    """Full regime stress test output."""
    total_trades: int
    regimes_found: List[str]
    regime_source: str  # "wal_field" or "vix_inferred"
    per_regime: Dict[str, Dict[str, Any]]
    profitable_regimes: List[str]
    unprofitable_regimes: List[str]
    generated_at: str


# ---------------------------------------------------------------------------
# WAL trade loader — loads ALL PositionClosed from WAL + archives
# ---------------------------------------------------------------------------
def load_all_trades(wal_dir: Path | str = WAL_DIR) -> List[ClosedTrade]:
    """Load all PositionClosed trades from WAL ndjson files including archives.

    Follows the same pattern as nightly_v6.load_todays_trades() but without
    date filtering — loads everything for cross-validation analysis.

    Parameters
    ----------
    wal_dir : Path or str
        Directory containing WAL .ndjson files.

    Returns
    -------
    List[ClosedTrade]
        All closed trades, sorted by entry_time_ns ascending.
    """
    wal_path = Path(wal_dir)
    trades: List[ClosedTrade] = []

    # Collect all WAL files: current + archive
    wal_files = sorted(wal_path.glob("*.ndjson"))
    archive_dir = wal_path / "archive"
    if archive_dir.is_dir():
        wal_files.extend(sorted(archive_dir.glob("*.ndjson")))

    seen_keys: set = set()  # Deduplicate by (ticker_id, entry_time_ns, exit_time_ns)

    for wal_file in wal_files:
        try:
            text = wal_file.read_text()
        except (OSError, UnicodeDecodeError):
            log.warning("Could not read WAL file: %s", wal_file)
            continue

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            payload = event.get("payload", {})
            if "PositionClosed" not in payload:
                continue

            pc = payload["PositionClosed"]

            ticker_id = pc.get("ticker_id", -1)
            entry_time_ns = pc.get("entry_time_ns", 0)
            exit_time_ns = pc.get("exit_time_ns", 0)

            # Deduplicate (engine restarts can cause WAL overlap in archives)
            dedup_key = (ticker_id, entry_time_ns, exit_time_ns)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            trade = ClosedTrade(
                ticker=pc.get("symbol", f"TID_{ticker_id}"),
                ticker_id=ticker_id,
                entry_time_ns=entry_time_ns,
                exit_time_ns=exit_time_ns,
                entry_price=pc.get("entry_price", 0.0),
                exit_price=pc.get("exit_price", 0.0),
                confidence=pc.get("confidence", 0.0),
                pnl=pc.get("final_pnl", 0.0),
                trade_class=pc.get("trade_class", ""),
                regime_at_entry=pc.get("regime_at_entry", pc.get("regime", "")),
                strategy=pc.get("strategy", ""),
                hold_time_mins=pc.get("hold_time_mins", 0),
                rung_achieved=pc.get("highest_rung", 0),
                exchange=pc.get("exchange", ""),
            )
            trades.append(trade)

    # Sort by entry time (chronological order is critical for CPCV)
    trades.sort(key=lambda t: t.entry_time_ns)
    log.info("Loaded %d unique PositionClosed trades from WAL", len(trades))
    return trades


# ---------------------------------------------------------------------------
# Metric computation helpers
# ---------------------------------------------------------------------------
def _compute_metrics(trades: List[ClosedTrade], fold_id: str) -> FoldMetrics:
    """Compute standard performance metrics on a list of trades.

    Parameters
    ----------
    trades : list of ClosedTrade
    fold_id : str
        Label for this fold/split (e.g. "test_0", "train_2").

    Returns
    -------
    FoldMetrics
    """
    n = len(trades)
    if n == 0:
        return FoldMetrics(
            fold_id=fold_id, n_trades=0, win_rate=0.0, profit_factor=0.0,
            sharpe_ratio=0.0, max_drawdown=0.0, total_pnl=0.0, avg_pnl=0.0,
            avg_confidence=0.0,
        )

    pnls = [t.pnl for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / n

    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    profit_factor = gross_win / max(gross_loss, 1e-9)

    # Sharpe: mean(daily returns) / std(daily returns) * sqrt(252)
    # Here we use per-trade returns as proxy for daily returns
    pnl_arr = np.array(pnls, dtype=np.float64)
    mean_pnl = float(np.mean(pnl_arr))
    std_pnl = float(np.std(pnl_arr))
    sharpe = (mean_pnl / max(std_pnl, 1e-9)) * math.sqrt(ANNUALISE_FACTOR) if std_pnl > 1e-9 else 0.0

    # Max drawdown from cumulative PnL
    cum_pnl = np.cumsum(pnl_arr)
    running_max = np.maximum.accumulate(cum_pnl)
    drawdowns = running_max - cum_pnl
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    total_pnl = float(np.sum(pnl_arr))
    avg_pnl = mean_pnl
    avg_conf = float(np.mean([t.confidence for t in trades])) if trades else 0.0

    return FoldMetrics(
        fold_id=fold_id,
        n_trades=n,
        win_rate=round(win_rate, 4),
        profit_factor=round(profit_factor, 4),
        sharpe_ratio=round(sharpe, 4),
        max_drawdown=round(max_dd, 4),
        total_pnl=round(total_pnl, 4),
        avg_pnl=round(avg_pnl, 4),
        avg_confidence=round(avg_conf, 4),
    )


# ---------------------------------------------------------------------------
# VAL-3: Walk-Forward Combinatorial Purged Cross-Validation
# ---------------------------------------------------------------------------
class WalkForwardCPCV:
    """Combinatorial Purged Cross-Validation (de Prado 2018, Ch.12).

    Splits trades into k time-ordered folds, generates all C(k, k-1)
    train/test splits, purges overlapping trades using an embargo window,
    and computes performance metrics on each test fold.

    Detects overfitting by comparing train vs test win rates.

    Parameters
    ----------
    k_folds : int
        Number of time-ordered folds (default 5).
    embargo_ns : int
        Embargo/purge window in nanoseconds (default 1 hour).
    """

    def __init__(
        self,
        k_folds: int = DEFAULT_K_FOLDS,
        embargo_ns: int = EMBARGO_NS,
    ) -> None:
        self.k_folds = k_folds
        self.embargo_ns = embargo_ns
        self.trades: List[ClosedTrade] = []
        self.result: Optional[CPCVResult] = None

    def load_trades(self, wal_dir: Path | str = WAL_DIR) -> int:
        """Load trades from WAL.

        Returns
        -------
        int
            Number of trades loaded.
        """
        self.trades = load_all_trades(wal_dir)
        return len(self.trades)

    def _split_into_folds(self) -> List[List[ClosedTrade]]:
        """Split trades into k time-ordered folds.

        Trades are already sorted by entry_time_ns. We divide into k
        approximately equal-sized groups preserving temporal order.

        Returns
        -------
        list of list of ClosedTrade
            k folds, each containing a subset of trades.
        """
        n = len(self.trades)
        k = self.k_folds
        fold_size = n // k
        remainder = n % k

        folds: List[List[ClosedTrade]] = []
        start = 0
        for i in range(k):
            # Distribute remainder across first folds
            end = start + fold_size + (1 if i < remainder else 0)
            folds.append(self.trades[start:end])
            start = end

        return folds

    def _purge_trades(
        self,
        train_trades: List[ClosedTrade],
        test_trades: List[ClosedTrade],
    ) -> Tuple[List[ClosedTrade], int]:
        """Remove training trades that overlap with test fold boundaries.

        A training trade is purged if its exit_time_ns falls within the
        embargo window before the first test trade's entry_time_ns, or
        its entry_time_ns falls within the embargo window after the last
        test trade's exit_time_ns.

        This prevents information leakage from the training set into the
        test set at fold boundaries (de Prado 2018, Section 12.2).

        Parameters
        ----------
        train_trades : list of ClosedTrade
            Training fold trades.
        test_trades : list of ClosedTrade
            Test fold trades.

        Returns
        -------
        purged_train : list of ClosedTrade
            Training trades after purging.
        n_purged : int
            Number of trades removed.
        """
        if not test_trades:
            return train_trades, 0

        # Test fold time boundaries
        test_start_ns = test_trades[0].entry_time_ns
        test_end_ns = test_trades[-1].exit_time_ns

        # Embargo zones
        embargo_before_start = test_start_ns - self.embargo_ns
        embargo_after_end = test_end_ns + self.embargo_ns

        purged: List[ClosedTrade] = []
        n_purged = 0

        for t in train_trades:
            # Purge if trade's active period overlaps with embargo zones
            # around test fold boundaries
            overlaps_before = (
                t.exit_time_ns >= embargo_before_start
                and t.exit_time_ns <= test_start_ns
            )
            overlaps_after = (
                t.entry_time_ns >= test_end_ns
                and t.entry_time_ns <= embargo_after_end
            )
            # Also purge trades that temporally overlap with the test period itself
            overlaps_test = (
                t.entry_time_ns < test_end_ns
                and t.exit_time_ns > test_start_ns
            )

            if overlaps_before or overlaps_after or overlaps_test:
                n_purged += 1
            else:
                purged.append(t)

        return purged, n_purged

    def run(self) -> CPCVResult:
        """Execute the full CPCV analysis.

        Generates all C(k, k-1) combinatorial splits, purges overlapping
        trades, computes metrics on each train/test pair, and aggregates
        results with overfit detection.

        Returns
        -------
        CPCVResult
            Full validation results.

        Raises
        ------
        ValueError
            If fewer than k_folds trades are loaded.
        """
        n = len(self.trades)
        k = self.k_folds

        if n < k:
            raise ValueError(
                f"Need at least {k} trades for {k}-fold CPCV, got {n}. "
                f"Collect more paper trades before running validation."
            )

        log.info("Running CPCV: %d trades, k=%d folds, embargo=%d ns (%.1f hours)",
                 n, k, self.embargo_ns, self.embargo_ns / 3.6e12)

        folds = self._split_into_folds()

        # Log fold sizes
        for i, fold in enumerate(folds):
            if fold:
                log.info("  Fold %d: %d trades [%s .. %s]", i, len(fold),
                         _ns_to_iso(fold[0].entry_time_ns),
                         _ns_to_iso(fold[-1].exit_time_ns))
            else:
                log.warning("  Fold %d: EMPTY", i)

        # Generate all C(k, k-1) splits: choose k-1 folds for training, 1 for testing
        # This gives exactly k splits (each fold is the test set once)
        fold_indices = list(range(k))
        splits_results: List[Dict[str, Any]] = []
        total_purged = 0
        test_metrics_list: List[FoldMetrics] = []
        train_metrics_list: List[FoldMetrics] = []

        for test_fold_idx in fold_indices:
            train_fold_indices = [i for i in fold_indices if i != test_fold_idx]

            # Assemble train and test trades
            test_trades = folds[test_fold_idx]
            train_trades: List[ClosedTrade] = []
            for ti in train_fold_indices:
                train_trades.extend(folds[ti])

            # Sort train trades by time (they come from multiple folds)
            train_trades.sort(key=lambda t: t.entry_time_ns)

            # Purge overlapping trades
            purged_train, n_purged = self._purge_trades(train_trades, test_trades)
            total_purged += n_purged

            # Compute metrics
            train_m = _compute_metrics(purged_train, f"train_{test_fold_idx}")
            test_m = _compute_metrics(test_trades, f"test_{test_fold_idx}")

            train_metrics_list.append(train_m)
            test_metrics_list.append(test_m)

            split_info = {
                "test_fold": test_fold_idx,
                "train_folds": train_fold_indices,
                "n_train_before_purge": len(train_trades),
                "n_train_after_purge": len(purged_train),
                "n_purged": n_purged,
                "n_test": len(test_trades),
                "train_metrics": asdict(train_m),
                "test_metrics": asdict(test_m),
            }
            splits_results.append(split_info)

            log.info(
                "  Split test=%d: train=%d (purged %d), test=%d | "
                "train WR=%.1f%% test WR=%.1f%% | test PF=%.2f test Sharpe=%.2f",
                test_fold_idx,
                len(purged_train), n_purged, len(test_trades),
                train_m.win_rate * 100, test_m.win_rate * 100,
                test_m.profit_factor, test_m.sharpe_ratio,
            )

        # Aggregate metrics across splits
        test_wrs = [m.win_rate for m in test_metrics_list if m.n_trades > 0]
        test_pfs = [m.profit_factor for m in test_metrics_list if m.n_trades > 0]
        test_sharpes = [m.sharpe_ratio for m in test_metrics_list if m.n_trades > 0]
        test_max_dds = [m.max_drawdown for m in test_metrics_list if m.n_trades > 0]
        train_wrs = [m.win_rate for m in train_metrics_list if m.n_trades > 0]

        def _safe_mean(xs: List[float]) -> float:
            return float(np.mean(xs)) if xs else 0.0

        def _safe_std(xs: List[float]) -> float:
            return float(np.std(xs)) if len(xs) > 1 else 0.0

        test_wr_mean = _safe_mean(test_wrs)
        train_wr_mean = _safe_mean(train_wrs)
        overfit_gap = (train_wr_mean - test_wr_mean) * 100  # in percentage points

        overfit_detected = overfit_gap > OVERFIT_THRESHOLD_PCT
        if overfit_detected:
            overfit_warning = (
                f"OVERFIT WARNING: Train WR ({train_wr_mean:.1%}) exceeds "
                f"Test WR ({test_wr_mean:.1%}) by {overfit_gap:.1f}pp "
                f"(threshold: {OVERFIT_THRESHOLD_PCT:.0f}pp). "
                f"Strategy parameters may be curve-fitted to historical data."
            )
            log.warning(overfit_warning)
        else:
            overfit_warning = (
                f"No overfit detected. Train-Test WR gap: {overfit_gap:.1f}pp "
                f"(within {OVERFIT_THRESHOLD_PCT:.0f}pp threshold)."
            )
            log.info(overfit_warning)

        self.result = CPCVResult(
            k_folds=k,
            n_splits=len(splits_results),
            total_trades=n,
            embargo_hours=self.embargo_ns / 3.6e12,
            purged_count=total_purged,
            splits=splits_results,
            test_wr_mean=round(test_wr_mean, 4),
            test_wr_std=round(_safe_std(test_wrs), 4),
            test_pf_mean=round(_safe_mean(test_pfs), 4),
            test_pf_std=round(_safe_std(test_pfs), 4),
            test_sharpe_mean=round(_safe_mean(test_sharpes), 4),
            test_sharpe_std=round(_safe_std(test_sharpes), 4),
            test_max_dd_mean=round(_safe_mean(test_max_dds), 4),
            test_max_dd_std=round(_safe_std(test_max_dds), 4),
            train_wr_mean=round(train_wr_mean, 4),
            train_wr_std=round(_safe_std(train_wrs), 4),
            overfit_detected=overfit_detected,
            overfit_gap_pct=round(overfit_gap, 2),
            overfit_warning=overfit_warning,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

        return self.result

    def to_json(self) -> str:
        """Serialize result to JSON string."""
        if self.result is None:
            raise RuntimeError("Must call .run() before .to_json()")
        return json.dumps(asdict(self.result), indent=2)

    def print_report(self) -> None:
        """Print human-readable CPCV report to stdout."""
        if self.result is None:
            raise RuntimeError("Must call .run() before .print_report()")
        r = self.result

        print()
        print("=" * 72)
        print("  VAL-3: Walk-Forward CPCV Validation Report")
        print(f"  Generated: {r.generated_at}")
        print("=" * 72)
        print()
        print(f"  Total trades:     {r.total_trades}")
        print(f"  K-folds:          {r.k_folds}")
        print(f"  Splits tested:    {r.n_splits}")
        print(f"  Embargo window:   {r.embargo_hours:.1f} hours")
        print(f"  Trades purged:    {r.purged_count}")
        print()
        print("-" * 72)
        print("  Aggregate Test Metrics (mean +/- std across splits):")
        print("-" * 72)
        print(f"  Win Rate:         {r.test_wr_mean:.1%} +/- {r.test_wr_std:.1%}")
        print(f"  Profit Factor:    {r.test_pf_mean:.2f} +/- {r.test_pf_std:.2f}")
        print(f"  Sharpe Ratio:     {r.test_sharpe_mean:.2f} +/- {r.test_sharpe_std:.2f}")
        print(f"  Max Drawdown:     {r.test_max_dd_mean:.2f} +/- {r.test_max_dd_std:.2f}")
        print()
        print("-" * 72)
        print("  Overfit Detection:")
        print("-" * 72)
        print(f"  Train WR (mean):  {r.train_wr_mean:.1%}")
        print(f"  Test WR (mean):   {r.test_wr_mean:.1%}")
        print(f"  Gap:              {r.overfit_gap_pct:.1f}pp")
        print(f"  Status:           {'*** OVERFIT WARNING ***' if r.overfit_detected else 'OK'}")
        print(f"  {r.overfit_warning}")
        print()

        # Per-split detail
        print("-" * 72)
        print("  Per-Split Detail:")
        print("-" * 72)
        fmt = "  {:<10s} {:>6s} {:>6s} {:>8s} {:>8s} {:>8s} {:>8s} {:>10s}"
        print(fmt.format("Split", "nTrn", "nTst", "TrnWR", "TstWR", "TstPF", "TstSh", "TstMaxDD"))
        print("  " + "-" * 68)
        for s in r.splits:
            tm = s["train_metrics"]
            te = s["test_metrics"]
            print(fmt.format(
                f"test={s['test_fold']}",
                str(tm["n_trades"]),
                str(te["n_trades"]),
                f"{tm['win_rate']:.1%}",
                f"{te['win_rate']:.1%}",
                f"{te['profit_factor']:.2f}",
                f"{te['sharpe_ratio']:.2f}",
                f"{te['max_drawdown']:.2f}",
            ))
        print()
        print("=" * 72)


# ---------------------------------------------------------------------------
# VAL-4: Regime Stress Testing
# ---------------------------------------------------------------------------
class RegimeStressTester:
    """Per-regime performance analysis with Monte Carlo bootstrap.

    Classifies each trade by regime (from WAL field or VIX inference),
    computes per-regime metrics, and runs bootstrap simulations to
    estimate VaR and CVaR for each regime.

    Parameters
    ----------
    mc_samples : int
        Number of bootstrap samples per regime (default 1000).
    rng_seed : int
        Random seed for reproducibility.
    """

    def __init__(
        self,
        mc_samples: int = MC_BOOTSTRAP_SAMPLES,
        rng_seed: int = MC_RNG_SEED,
    ) -> None:
        self.mc_samples = mc_samples
        self.rng = np.random.RandomState(rng_seed)
        self.trades: List[ClosedTrade] = []
        self.result: Optional[RegimeStressResult] = None

    def load_trades(self, wal_dir: Path | str = WAL_DIR) -> int:
        """Load trades from WAL.

        Returns
        -------
        int
            Number of trades loaded.
        """
        self.trades = load_all_trades(wal_dir)
        return len(self.trades)

    def _classify_regime(self, trade: ClosedTrade) -> str:
        """Classify a trade into a regime.

        Strategy:
        1. If the trade has a regime_at_entry field from WAL, use it.
        2. Otherwise, infer from VIX level if a VIX proxy is available.
        3. Final fallback: "unknown".

        For VIX inference, we use a simple binning:
            VIX < 15  => calm
            15 <= VIX < 25 => normal
            25 <= VIX < 35 => stressed
            VIX >= 35 => crisis

        Parameters
        ----------
        trade : ClosedTrade

        Returns
        -------
        str
            Regime label.
        """
        regime = trade.regime_at_entry.strip().lower() if trade.regime_at_entry else ""

        if regime and regime != "unknown":
            # Normalise common regime labels from HMM
            if regime in ("low vol", "low_vol", "lowvol", "calm"):
                return "calm"
            elif regime in ("normal vol", "normal_vol", "normalvol", "normal"):
                return "normal"
            elif regime in ("high vol", "high_vol", "highvol", "stressed", "crisis"):
                # Distinguish stressed vs crisis if the raw label says so
                if "crisis" in regime:
                    return "crisis"
                return "stressed"
            # Return as-is if it's something else but not empty
            return regime

        # Fallback: no regime data available
        return "unknown"

    def _infer_regime_from_vix(self, vix_level: float) -> str:
        """Infer regime from a VIX level.

        Parameters
        ----------
        vix_level : float

        Returns
        -------
        str
            One of: calm, normal, stressed, crisis.
        """
        if vix_level < VIX_CALM_CEIL:
            return "calm"
        elif vix_level < VIX_NORMAL_CEIL:
            return "normal"
        elif vix_level < VIX_STRESSED_CEIL:
            return "stressed"
        else:
            return "crisis"

    def _compute_max_drawdown(self, pnls: List[float]) -> float:
        """Compute max drawdown from a sequence of PnLs.

        Parameters
        ----------
        pnls : list of float

        Returns
        -------
        float
            Maximum drawdown (positive number = peak-to-trough decline).
        """
        if not pnls:
            return 0.0
        cum = np.cumsum(pnls)
        running_max = np.maximum.accumulate(cum)
        drawdowns = running_max - cum
        return float(np.max(drawdowns))

    def _bootstrap_daily_returns(
        self, pnls: np.ndarray, n_samples: int
    ) -> np.ndarray:
        """Monte Carlo bootstrap: resample trade PnLs to simulate daily returns.

        For each sample:
        1. Draw N trades with replacement (N = original trade count)
        2. Compute the mean as the "daily return" for that sample

        Parameters
        ----------
        pnls : np.ndarray
            Array of trade PnLs.
        n_samples : int
            Number of bootstrap iterations.

        Returns
        -------
        np.ndarray
            Simulated daily returns, shape (n_samples,).
        """
        n_trades = len(pnls)
        if n_trades == 0:
            return np.zeros(n_samples)

        daily_returns = np.empty(n_samples, dtype=np.float64)
        for i in range(n_samples):
            sample_idx = self.rng.randint(0, n_trades, size=n_trades)
            daily_returns[i] = np.mean(pnls[sample_idx])

        return daily_returns

    def run(self) -> RegimeStressResult:
        """Execute the full regime stress test.

        Returns
        -------
        RegimeStressResult
            Per-regime metrics and Monte Carlo results.

        Raises
        ------
        ValueError
            If no trades are loaded.
        """
        if not self.trades:
            raise ValueError("No trades loaded. Call .load_trades() first.")

        log.info("Running regime stress test on %d trades", len(self.trades))

        # Classify trades by regime
        regime_trades: Dict[str, List[ClosedTrade]] = defaultdict(list)
        regime_source = "wal_field"

        for trade in self.trades:
            regime = self._classify_regime(trade)
            if regime == "unknown":
                regime_source = "vix_inferred"  # At least some trades lack regime data
            regime_trades[regime].append(trade)

        # If all trades are "unknown", we cannot do meaningful regime analysis
        # but we still report the single-regime statistics
        if len(regime_trades) == 1 and "unknown" in regime_trades:
            log.warning(
                "All %d trades have unknown regime. "
                "Regime stress test will produce a single 'unknown' bucket. "
                "Ensure WAL PositionClosed events include regime_at_entry field "
                "or provide VIX data for inference.",
                len(self.trades),
            )

        # Compute per-regime metrics
        per_regime: Dict[str, Dict[str, Any]] = {}
        profitable_regimes: List[str] = []
        unprofitable_regimes: List[str] = []

        for regime_name in sorted(regime_trades.keys()):
            trades = regime_trades[regime_name]
            n = len(trades)
            pnls = [t.pnl for t in trades]
            pnl_arr = np.array(pnls, dtype=np.float64)

            wins = sum(1 for p in pnls if p > 0)
            wr = wins / n if n > 0 else 0.0

            gross_win = sum(p for p in pnls if p > 0)
            gross_loss = abs(sum(p for p in pnls if p < 0))
            pf = gross_win / max(gross_loss, 1e-9)

            avg_pnl = float(np.mean(pnl_arr))
            total_pnl = float(np.sum(pnl_arr))
            max_dd = self._compute_max_drawdown(pnls)

            hold_times = [t.hold_time_mins for t in trades]
            avg_hold = float(np.mean(hold_times)) if hold_times else 0.0

            confidences = [t.confidence for t in trades]
            avg_conf = float(np.mean(confidences)) if confidences else 0.0

            is_profitable = total_pnl > 0
            if is_profitable:
                profitable_regimes.append(regime_name)
            else:
                unprofitable_regimes.append(regime_name)

            # Monte Carlo bootstrap
            mc_returns = self._bootstrap_daily_returns(pnl_arr, self.mc_samples)

            # VaR(95%): the 5th percentile of simulated daily returns (loss boundary)
            var_95 = float(np.percentile(mc_returns, 5))
            # CVaR(95%): expected value of returns below VaR (tail risk)
            tail_mask = mc_returns <= var_95
            cvar_95 = float(np.mean(mc_returns[tail_mask])) if np.any(tail_mask) else var_95

            regime_desc = REGIME_VIX_LABELS.get(regime_name, regime_name)

            metrics = RegimeMetrics(
                regime=regime_name,
                regime_description=regime_desc,
                n_trades=n,
                win_rate=round(wr, 4),
                profit_factor=round(pf, 4),
                avg_pnl=round(avg_pnl, 4),
                total_pnl=round(total_pnl, 4),
                max_drawdown=round(max_dd, 4),
                avg_hold_time_mins=round(avg_hold, 1),
                avg_confidence=round(avg_conf, 4),
                is_profitable=is_profitable,
                var_95=round(var_95, 4),
                cvar_95=round(cvar_95, 4),
                mc_daily_return_mean=round(float(np.mean(mc_returns)), 6),
                mc_daily_return_std=round(float(np.std(mc_returns)), 6),
                mc_daily_return_p5=round(float(np.percentile(mc_returns, 5)), 6),
                mc_daily_return_p50=round(float(np.percentile(mc_returns, 50)), 6),
                mc_daily_return_p95=round(float(np.percentile(mc_returns, 95)), 6),
            )

            per_regime[regime_name] = asdict(metrics)

            log.info(
                "  Regime %-12s: n=%3d WR=%.1f%% PF=%.2f PnL=%.2f MaxDD=%.2f "
                "VaR95=%.4f CVaR95=%.4f %s",
                regime_name, n, wr * 100, pf, total_pnl, max_dd,
                var_95, cvar_95,
                "PROFITABLE" if is_profitable else "UNPROFITABLE",
            )

        self.result = RegimeStressResult(
            total_trades=len(self.trades),
            regimes_found=sorted(regime_trades.keys()),
            regime_source=regime_source,
            per_regime=per_regime,
            profitable_regimes=profitable_regimes,
            unprofitable_regimes=unprofitable_regimes,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

        return self.result

    def to_json(self) -> str:
        """Serialize result to JSON string."""
        if self.result is None:
            raise RuntimeError("Must call .run() before .to_json()")
        return json.dumps(asdict(self.result), indent=2)

    def print_report(self) -> None:
        """Print human-readable regime stress report to stdout."""
        if self.result is None:
            raise RuntimeError("Must call .run() before .print_report()")
        r = self.result

        print()
        print("=" * 72)
        print("  VAL-4: Regime Stress Testing Report")
        print(f"  Generated: {r.generated_at}")
        print("=" * 72)
        print()
        print(f"  Total trades:       {r.total_trades}")
        print(f"  Regimes found:      {', '.join(r.regimes_found)}")
        print(f"  Regime source:      {r.regime_source}")
        print(f"  Monte Carlo sims:   {MC_BOOTSTRAP_SAMPLES}")
        print()

        # Per-regime table
        print("-" * 72)
        print("  Per-Regime Performance:")
        print("-" * 72)
        fmt = "  {:<12s} {:>5s} {:>7s} {:>7s} {:>10s} {:>8s} {:>9s} {:>9s} {:>7s}"
        print(fmt.format(
            "Regime", "N", "WR%", "PF", "TotalPnL", "MaxDD",
            "VaR95", "CVaR95", "Status",
        ))
        print("  " + "-" * 70)

        for regime_name in sorted(r.per_regime.keys()):
            m = r.per_regime[regime_name]
            status = "OK" if m["is_profitable"] else "LOSS"
            print(fmt.format(
                regime_name,
                str(m["n_trades"]),
                f"{m['win_rate']:.1%}",
                f"{m['profit_factor']:.2f}",
                f"{m['total_pnl']:.2f}",
                f"{m['max_drawdown']:.2f}",
                f"{m['var_95']:.4f}",
                f"{m['cvar_95']:.4f}",
                status,
            ))
        print()

        # Monte Carlo distribution
        print("-" * 72)
        print("  Monte Carlo Daily Return Distribution:")
        print("-" * 72)
        fmt2 = "  {:<12s} {:>10s} {:>10s} {:>10s} {:>10s} {:>10s}"
        print(fmt2.format("Regime", "Mean", "Std", "P5", "P50", "P95"))
        print("  " + "-" * 62)
        for regime_name in sorted(r.per_regime.keys()):
            m = r.per_regime[regime_name]
            print(fmt2.format(
                regime_name,
                f"{m['mc_daily_return_mean']:.6f}",
                f"{m['mc_daily_return_std']:.6f}",
                f"{m['mc_daily_return_p5']:.6f}",
                f"{m['mc_daily_return_p50']:.6f}",
                f"{m['mc_daily_return_p95']:.6f}",
            ))
        print()

        # Summary
        print("-" * 72)
        print("  Regime Profitability Summary:")
        print("-" * 72)
        if r.profitable_regimes:
            print(f"  Profitable:   {', '.join(r.profitable_regimes)}")
        else:
            print("  Profitable:   NONE")
        if r.unprofitable_regimes:
            print(f"  Unprofitable: {', '.join(r.unprofitable_regimes)}")
        else:
            print("  Unprofitable: NONE")
        print()
        print("=" * 72)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ns_to_iso(ns: int) -> str:
    """Convert nanosecond timestamp to ISO datetime string."""
    if ns <= 0:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return "N/A"


def _save_result(result_dict: Dict[str, Any], prefix: str) -> Path:
    """Save JSON result to REPORTS_DIR with timestamp.

    Parameters
    ----------
    result_dict : dict
        Serializable result dictionary.
    prefix : str
        Filename prefix (e.g., "cpcv", "regime_stress").

    Returns
    -------
    Path
        Path to the written JSON file.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = REPORTS_DIR / f"{prefix}_{ts}.json"
    out_path.write_text(json.dumps(result_dict, indent=2))
    log.info("Saved report to %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    """CLI entry point for the validation suite."""
    parser = argparse.ArgumentParser(
        description=(
            "VAL-3 + VAL-4 Validation Suite: "
            "Walk-Forward CPCV and Regime Stress Testing"
        ),
    )
    parser.add_argument(
        "--cpcv",
        action="store_true",
        help="Run VAL-3: Walk-Forward CPCV validation",
    )
    parser.add_argument(
        "--regime-stress",
        action="store_true",
        help="Run VAL-4: Regime stress testing",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all validations (VAL-3 + VAL-4)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON to stdout (in addition to reports)",
    )
    parser.add_argument(
        "--wal-dir",
        type=str,
        default=str(WAL_DIR),
        help=f"WAL directory (default: {WAL_DIR})",
    )
    parser.add_argument(
        "--k-folds",
        type=int,
        default=DEFAULT_K_FOLDS,
        help=f"Number of CV folds for CPCV (default: {DEFAULT_K_FOLDS})",
    )
    parser.add_argument(
        "--embargo-hours",
        type=float,
        default=1.0,
        help="Embargo/purge window in hours for CPCV (default: 1.0)",
    )
    parser.add_argument(
        "--mc-samples",
        type=int,
        default=MC_BOOTSTRAP_SAMPLES,
        help=f"Monte Carlo bootstrap samples for regime stress (default: {MC_BOOTSTRAP_SAMPLES})",
    )

    args = parser.parse_args()

    # Default: if no specific flag, run all
    run_cpcv = args.cpcv or args.all
    run_regime = args.regime_stress or args.all

    if not run_cpcv and not run_regime:
        # No flag specified at all — show help
        parser.print_help()
        return 1

    wal_dir = Path(args.wal_dir)
    if not wal_dir.is_dir():
        log.error("WAL directory not found: %s", wal_dir)
        log.error("Set AEGIS_WAL_DIR or pass --wal-dir to point to WAL ndjson files.")
        return 1

    embargo_ns = int(args.embargo_hours * 3600 * 1_000_000_000)
    combined_json: Dict[str, Any] = {}
    exit_code = 0

    # ---- VAL-3: CPCV ----
    if run_cpcv:
        log.info("=" * 60)
        log.info("VAL-3: Walk-Forward CPCV")
        log.info("=" * 60)
        try:
            cpcv = WalkForwardCPCV(k_folds=args.k_folds, embargo_ns=embargo_ns)
            n_trades = cpcv.load_trades(wal_dir)
            if n_trades < args.k_folds:
                log.error(
                    "Insufficient trades (%d) for %d-fold CPCV. "
                    "Need at least %d trades.",
                    n_trades, args.k_folds, args.k_folds,
                )
                exit_code = 1
            else:
                result = cpcv.run()
                if not args.json:
                    cpcv.print_report()
                result_dict = asdict(result)
                _save_result(result_dict, "cpcv")
                combined_json["cpcv"] = result_dict
        except Exception as e:
            log.error("CPCV failed: %s", e, exc_info=True)
            exit_code = 1

    # ---- VAL-4: Regime Stress ----
    if run_regime:
        log.info("=" * 60)
        log.info("VAL-4: Regime Stress Testing")
        log.info("=" * 60)
        try:
            stress = RegimeStressTester(mc_samples=args.mc_samples)
            n_trades = stress.load_trades(wal_dir)
            if n_trades == 0:
                log.error("No trades found in WAL for regime stress testing.")
                exit_code = 1
            else:
                result = stress.run()
                if not args.json:
                    stress.print_report()
                result_dict = asdict(result)
                _save_result(result_dict, "regime_stress")
                combined_json["regime_stress"] = result_dict
        except Exception as e:
            log.error("Regime stress test failed: %s", e, exc_info=True)
            exit_code = 1

    # ---- JSON output ----
    if args.json and combined_json:
        print(json.dumps(combined_json, indent=2))

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
