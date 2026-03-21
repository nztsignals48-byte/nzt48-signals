"""N4b — Win/Loss Indicator Delta Analysis.

Compares indicator distributions between winning and losing trades to
identify which indicators have the strongest predictive power.

Produces:
  - Win_Loss_Delta Sheets tab rows (pushed via sheets_sync)
  - JSON output for Claude nightly review consumption
  - Gate threshold recommendations based on observed separation

Cadence: Nightly (after indicator_intelligence.py completes).
Requires: 30+ trades minimum for statistical validity.

QUARANTINE: Read-only. Never writes to WAL, config, or live trading parameters.

Usage:
    python3 -m python_brain.ouroboros.win_loss_delta                    # Analyze and print
    python3 -m python_brain.ouroboros.win_loss_delta --push-sheets      # Push to Sheets
    python3 -m python_brain.ouroboros.win_loss_delta --days 60          # 60-day lookback
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

log = logging.getLogger("ouroboros.win_loss_delta")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))

# Indicators to compare between winners and losers
DELTA_INDICATORS = [
    "entry_rvol",
    "entry_hurst",
    "entry_adx",
    "entry_atr_pct",
    "entry_spread_pct",
    "confidence",
    "vol_slope_at_entry",
    "vwap_dist_at_entry_pct",
    "vix_at_entry",
]

# Minimum trades for meaningful statistics
MIN_TRADES = 10

# Predictive threshold: delta > 1 std dev of pooled distribution
PREDICTIVE_THRESHOLD_STD = 1.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class IndicatorDelta:
    """Comparison of one indicator between winners and losers."""
    indicator: str
    win_count: int = 0
    loss_count: int = 0
    win_mean: float = 0.0
    loss_mean: float = 0.0
    delta: float = 0.0            # win_mean - loss_mean
    win_p25: float = 0.0
    loss_p25: float = 0.0
    win_p75: float = 0.0
    loss_p75: float = 0.0
    pooled_std: float = 0.0       # Combined std for significance
    effect_size: float = 0.0      # Cohen's d = delta / pooled_std
    predictive: bool = False      # True if |effect_size| > threshold
    suggested_gate: str = ""      # e.g. "entry_adx > 18"
    direction_bias: str = ""      # "higher_wins", "lower_wins", "neutral"

    def to_sheets_row(self, date_str: str) -> List[Any]:
        """Convert to a row for Win_Loss_Delta Sheets tab."""
        return [
            date_str,
            self.indicator,
            round(self.win_mean, 4),
            round(self.loss_mean, 4),
            round(self.delta, 4),
            round(self.win_p25, 4),
            round(self.loss_p25, 4),
            "YES" if self.predictive else "NO",
            self.suggested_gate,
        ]


@dataclass
class WinLossDeltaReport:
    """Complete win/loss delta analysis output."""
    analysis_date: str
    lookback_days: int
    total_trades: int
    total_wins: int
    total_losses: int
    deltas: List[IndicatorDelta] = field(default_factory=list)
    # Top predictive indicators sorted by |effect_size| descending
    top_predictors: List[str] = field(default_factory=list)
    gate_recommendations: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)

    def to_sheets_rows(self) -> List[List[Any]]:
        """Convert to rows for Win_Loss_Delta tab."""
        return [d.to_sheets_row(self.analysis_date) for d in self.deltas]


# ---------------------------------------------------------------------------
# WAL loading (reuses indicator_intelligence loader)
# ---------------------------------------------------------------------------
def _load_trades_for_delta(wal_dir: Path, days: int) -> tuple:
    """Load trades and split into winners/losers with indicator values.

    Returns: (winners, losers) where each is a list of dicts with indicator fields.
    """
    try:
        from python_brain.ouroboros.indicator_intelligence import (
            _load_enriched_trades,
        )
        trades = _load_enriched_trades(wal_dir, days)
    except ImportError:
        log.error("Cannot import indicator_intelligence — ensure it exists")
        return [], []

    winners = []
    losers = []
    for t in trades:
        d = {
            "entry_rvol": t.entry_rvol,
            "entry_hurst": t.entry_hurst,
            "entry_adx": t.entry_adx,
            "entry_atr_pct": t.entry_atr_pct,
            "entry_spread_pct": t.entry_spread_pct,
            "confidence": t.confidence,
            "vol_slope_at_entry": getattr(t, "vol_slope_at_entry", None),
            "vwap_dist_at_entry_pct": getattr(t, "vwap_dist_at_entry_pct", None),
            "vix_at_entry": getattr(t, "vix_at_entry", None),
        }
        if t.is_winner:
            winners.append(d)
        else:
            losers.append(d)

    return winners, losers


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------
def _compute_delta(
    indicator: str,
    winners: List[Dict],
    losers: List[Dict],
) -> Optional[IndicatorDelta]:
    """Compute the win/loss delta for a single indicator."""
    w_vals = [w[indicator] for w in winners if w.get(indicator) is not None]
    l_vals = [l[indicator] for l in losers if l.get(indicator) is not None]

    if len(w_vals) < MIN_TRADES or len(l_vals) < MIN_TRADES:
        return None

    w_arr = np.array(w_vals, dtype=np.float64)
    l_arr = np.array(l_vals, dtype=np.float64)

    w_mean = float(np.mean(w_arr))
    l_mean = float(np.mean(l_arr))
    delta = w_mean - l_mean

    # Pooled standard deviation for Cohen's d
    w_std = float(np.std(w_arr, ddof=1)) if len(w_arr) > 1 else 0.0
    l_std = float(np.std(l_arr, ddof=1)) if len(l_arr) > 1 else 0.0
    nw, nl = len(w_arr), len(l_arr)
    pooled_std = np.sqrt(((nw - 1) * w_std**2 + (nl - 1) * l_std**2) / (nw + nl - 2))
    pooled_std = float(pooled_std) if pooled_std > 0 else 1.0

    effect_size = delta / pooled_std
    predictive = abs(effect_size) > PREDICTIVE_THRESHOLD_STD

    # Direction bias
    if delta > 0 and predictive:
        direction_bias = "higher_wins"
    elif delta < 0 and predictive:
        direction_bias = "lower_wins"
    else:
        direction_bias = "neutral"

    # Generate suggested gate
    suggested_gate = ""
    if predictive:
        if direction_bias == "higher_wins":
            # Winners have higher values — suggest minimum gate
            threshold = float(np.percentile(w_arr, 25))  # P25 of winners
            suggested_gate = f"{indicator} > {threshold:.2f}"
        elif direction_bias == "lower_wins":
            # Winners have lower values — suggest maximum gate
            threshold = float(np.percentile(w_arr, 75))  # P75 of winners
            suggested_gate = f"{indicator} < {threshold:.2f}"

    return IndicatorDelta(
        indicator=indicator,
        win_count=nw,
        loss_count=nl,
        win_mean=w_mean,
        loss_mean=l_mean,
        delta=delta,
        win_p25=float(np.percentile(w_arr, 25)),
        loss_p25=float(np.percentile(l_arr, 25)),
        win_p75=float(np.percentile(w_arr, 75)),
        loss_p75=float(np.percentile(l_arr, 75)),
        pooled_std=pooled_std,
        effect_size=effect_size,
        predictive=predictive,
        suggested_gate=suggested_gate,
        direction_bias=direction_bias,
    )


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------
def analyze_win_loss_deltas(
    wal_dir: Path = WAL_DIR,
    days: int = 30,
) -> WinLossDeltaReport:
    """Analyze indicator differences between winners and losers.

    Args:
        wal_dir: WAL events directory
        days: Lookback period in days

    Returns:
        WinLossDeltaReport with all deltas and recommendations.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("N4b: Win/Loss delta analysis starting (%d-day lookback)", days)

    winners, losers = _load_trades_for_delta(wal_dir, days)
    total = len(winners) + len(losers)

    if total < MIN_TRADES * 2:
        log.warning("Insufficient trades (%d) for delta analysis (need %d+)", total, MIN_TRADES * 2)
        return WinLossDeltaReport(
            analysis_date=today,
            lookback_days=days,
            total_trades=total,
            total_wins=len(winners),
            total_losses=len(losers),
        )

    log.info("Loaded %d trades: %d winners, %d losers", total, len(winners), len(losers))

    # Compute deltas for all indicators
    deltas: List[IndicatorDelta] = []
    for indicator in DELTA_INDICATORS:
        d = _compute_delta(indicator, winners, losers)
        if d is not None:
            deltas.append(d)
            status = "PREDICTIVE" if d.predictive else "neutral"
            log.info(
                "  %s: W=%.4f L=%.4f Δ=%.4f d=%.2f [%s] %s",
                indicator, d.win_mean, d.loss_mean, d.delta,
                d.effect_size, status, d.suggested_gate,
            )

    # Sort by |effect_size| descending
    deltas.sort(key=lambda d: -abs(d.effect_size))

    # Top predictors
    top_predictors = [d.indicator for d in deltas if d.predictive]

    # Gate recommendations (only for predictive indicators)
    gate_recs = []
    for d in deltas:
        if d.predictive and d.suggested_gate:
            gate_recs.append({
                "indicator": d.indicator,
                "gate": d.suggested_gate,
                "effect_size": round(d.effect_size, 3),
                "direction": d.direction_bias,
                "win_count": d.win_count,
                "loss_count": d.loss_count,
            })

    report = WinLossDeltaReport(
        analysis_date=today,
        lookback_days=days,
        total_trades=total,
        total_wins=len(winners),
        total_losses=len(losers),
        deltas=deltas,
        top_predictors=top_predictors,
        gate_recommendations=gate_recs,
    )

    log.info(
        "N4b complete: %d indicators analyzed, %d predictive, %d gate recommendations",
        len(deltas), len(top_predictors), len(gate_recs),
    )
    return report


def save_win_loss_delta(report: WinLossDeltaReport, output_dir: Path = DATA_DIR) -> Path:
    """Save win/loss delta report to JSON file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "win_loss_delta.json"
    tmp_path = output_path.with_suffix(".json.tmp")

    try:
        tmp_path.write_text(report.to_json(), encoding="utf-8")
        os.rename(str(tmp_path), str(output_path))
        log.info("Win/Loss delta saved: %s", output_path)
        return output_path
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def push_to_sheets(report: WinLossDeltaReport) -> bool:
    """Push Win_Loss_Delta rows to Google Sheets."""
    rows = report.to_sheets_rows()
    if not rows:
        log.info("No delta rows to push (insufficient data)")
        return True

    try:
        from python_brain.ouroboros.sheets_sync import _push_nightly_rows
        return _push_nightly_rows("Win_Loss_Delta", rows)
    except ImportError:
        log.warning("Cannot import sheets_sync — skipping Sheets push")
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [WinLossDelta] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="N4b — Win/Loss Indicator Delta Analysis"
    )
    parser.add_argument("--days", type=int, default=30, help="Lookback days (default: 30)")
    parser.add_argument("--wal-dir", type=str, default=str(WAL_DIR), help="WAL directory")
    parser.add_argument("--push-sheets", action="store_true", help="Push results to Sheets")
    parser.add_argument("--output-dir", type=str, default=str(DATA_DIR), help="Output directory")
    args = parser.parse_args()

    report = analyze_win_loss_deltas(
        wal_dir=Path(args.wal_dir),
        days=args.days,
    )

    # Save JSON
    output_path = save_win_loss_delta(report, Path(args.output_dir))
    print(f"\nAnalysis saved: {output_path}")
    print(f"  Trades: {report.total_trades} ({report.total_wins}W / {report.total_losses}L)")
    print(f"  Indicators analyzed: {len(report.deltas)}")
    print(f"  Predictive: {len(report.top_predictors)}")

    # Print deltas
    if report.deltas:
        print("\nIndicator Deltas (sorted by |effect size|):")
        print(f"  {'Indicator':<25} {'W Mean':>8} {'L Mean':>8} {'Delta':>8} {'d':>6} {'Pred':>5} {'Gate'}")
        print(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*5} {'-'*20}")
        for d in report.deltas:
            pred = "YES" if d.predictive else "no"
            print(
                f"  {d.indicator:<25} {d.win_mean:8.4f} {d.loss_mean:8.4f} "
                f"{d.delta:8.4f} {d.effect_size:6.2f} {pred:>5} {d.suggested_gate}"
            )

    # Print recommendations
    if report.gate_recommendations:
        print("\nGate Recommendations:")
        for rec in report.gate_recommendations:
            print(f"  {rec['gate']}  (d={rec['effect_size']:.2f}, {rec['direction']})")

    # Push to Sheets if requested
    if args.push_sheets:
        push_to_sheets(report)


if __name__ == "__main__":
    main()
