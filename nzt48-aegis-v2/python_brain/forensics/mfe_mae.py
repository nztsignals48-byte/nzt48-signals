"""MFE/MAE Calculator & R-Multiple Tracker — Book 39 Sections 12, 25.

Computes Maximum Favorable Excursion (MFE) and Maximum Adverse Excursion (MAE)
for closed trades from WAL event data. Also tracks R-multiples for trade
quality assessment.

R-multiple = (exit_price - entry_price) / initial_risk_per_share
  where initial_risk = entry_price - initial_stop_price

Exit efficiency = exit_R / peak_R (fraction of max profit captured)
  Target: > 0.5 for momentum strategies, > 0.7 for mean reversion

Usage:
    from python_brain.forensics.mfe_mae import (
        compute_trade_metrics, analyze_all_trades, TradeMetrics,
    )

    metrics = compute_trade_metrics(trade)
    report = analyze_all_trades(wal_dir, lookback_days=30)

Runs nightly as part of Ouroboros pipeline.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("mfe_mae")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))


@dataclass
class TradeMetrics:
    """Complete trade quality metrics for a single closed trade."""
    ticker: str = ""
    strategy: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    initial_stop: float = 0.0
    # MFE/MAE
    mfe_price: float = 0.0  # Highest price reached during trade
    mae_price: float = 0.0  # Lowest price reached during trade
    mfe_pct: float = 0.0    # (mfe_price - entry) / entry * 100
    mae_pct: float = 0.0    # (entry - mae_price) / entry * 100 (positive = adverse)
    # R-Multiples
    initial_r: float = 0.0       # entry - initial_stop (risk per share)
    exit_r_multiple: float = 0.0  # (exit - entry) / initial_r
    peak_r_multiple: float = 0.0  # (mfe - entry) / initial_r
    # Efficiency
    exit_efficiency: float = 0.0  # exit_r / peak_r (0-1, higher = captured more of the move)
    edge_ratio: float = 0.0       # mfe / mae (> 1.0 = favorable skew)
    # Timing
    hold_time_mins: float = 0.0
    mfe_time_mins: float = 0.0   # Minutes from entry to MFE
    mae_time_mins: float = 0.0   # Minutes from entry to MAE
    exit_rung: int = 0            # Which Chandelier rung triggered exit
    pnl: float = 0.0
    pnl_pct: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def compute_trade_metrics(trade: Dict[str, Any]) -> TradeMetrics:
    """Compute MFE/MAE and R-multiples from a WAL PositionClosed event.

    Expected trade dict keys:
        ticker, strategy, entry_price, exit_price, initial_stop,
        highest_price (or mfe_price), lowest_price (or mae_price),
        hold_time_mins, exit_rung, realized_pnl, quantity
    """
    m = TradeMetrics()
    m.ticker = trade.get("ticker", trade.get("symbol", ""))
    m.strategy = trade.get("strategy", "")
    m.entry_price = trade.get("entry_price", trade.get("avg_entry", 0.0))
    m.exit_price = trade.get("exit_price", 0.0)
    m.initial_stop = trade.get("initial_stop", trade.get("stop_price", 0.0))
    m.hold_time_mins = trade.get("hold_time_mins", 0.0)
    m.exit_rung = trade.get("exit_rung", trade.get("highest_rung", 0))
    m.pnl = trade.get("realized_pnl", trade.get("pnl", 0.0))

    # MFE/MAE from highest/lowest prices during the trade
    m.mfe_price = trade.get("mfe_price", trade.get("highest_price", trade.get("highest_high", m.entry_price)))
    m.mae_price = trade.get("mae_price", trade.get("lowest_price", trade.get("lowest_low", m.entry_price)))

    if m.entry_price > 0:
        m.mfe_pct = (m.mfe_price - m.entry_price) / m.entry_price * 100
        m.mae_pct = (m.entry_price - m.mae_price) / m.entry_price * 100
        m.pnl_pct = (m.exit_price - m.entry_price) / m.entry_price * 100

    # R-Multiples
    m.initial_r = abs(m.entry_price - m.initial_stop) if m.initial_stop > 0 else 0.0
    if m.initial_r > 0:
        m.exit_r_multiple = (m.exit_price - m.entry_price) / m.initial_r
        m.peak_r_multiple = (m.mfe_price - m.entry_price) / m.initial_r

    # Exit efficiency
    if m.peak_r_multiple > 0:
        m.exit_efficiency = m.exit_r_multiple / m.peak_r_multiple
    elif m.peak_r_multiple == 0 and m.exit_r_multiple == 0:
        m.exit_efficiency = 1.0  # No move = perfect capture of nothing

    # Edge ratio (MFE / MAE)
    if m.mae_pct > 0:
        m.edge_ratio = m.mfe_pct / m.mae_pct
    elif m.mfe_pct > 0:
        m.edge_ratio = float("inf")  # All upside, no downside

    return m


def analyze_all_trades(
    wal_dir: Optional[Path] = None,
    lookback_days: int = 30,
) -> Dict[str, Any]:
    """Analyze all closed trades from WAL files over lookback period.

    Returns summary report with per-strategy breakdowns.
    """
    wd = wal_dir or WAL_DIR
    today = datetime.now(timezone.utc).date()
    all_metrics: List[TradeMetrics] = []

    for d in range(lookback_days):
        date = today - timedelta(days=d)
        wal_path = wd / f"{date.strftime('%Y-%m-%d')}.ndjson"
        if not wal_path.exists():
            continue

        try:
            with open(wal_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        evt = json.loads(line)
                        if evt.get("event_type") == "PositionClosed":
                            m = compute_trade_metrics(evt)
                            all_metrics.append(m)
                    except (json.JSONDecodeError, KeyError):
                        continue
        except IOError:
            continue

    if not all_metrics:
        return {"total_trades": 0, "strategies": {}}

    # Per-strategy breakdown
    strategies: Dict[str, List[TradeMetrics]] = {}
    for m in all_metrics:
        strat = m.strategy or "unknown"
        strategies.setdefault(strat, []).append(m)

    report: Dict[str, Any] = {
        "total_trades": len(all_metrics),
        "lookback_days": lookback_days,
        "overall": _summarize_metrics(all_metrics),
        "strategies": {},
    }

    for strat, metrics in strategies.items():
        report["strategies"][strat] = _summarize_metrics(metrics)

    return report


def _summarize_metrics(metrics: List[TradeMetrics]) -> Dict[str, Any]:
    """Compute summary statistics for a list of trade metrics."""
    n = len(metrics)
    if n == 0:
        return {}

    avg = lambda vals: sum(vals) / len(vals) if vals else 0.0

    mfe_pcts = [m.mfe_pct for m in metrics]
    mae_pcts = [m.mae_pct for m in metrics]
    exit_rs = [m.exit_r_multiple for m in metrics if m.initial_r > 0]
    peak_rs = [m.peak_r_multiple for m in metrics if m.initial_r > 0]
    efficiencies = [m.exit_efficiency for m in metrics if m.peak_r_multiple > 0]
    edge_ratios = [m.edge_ratio for m in metrics if m.edge_ratio < float("inf")]

    winners = [m for m in metrics if m.pnl > 0]
    losers = [m for m in metrics if m.pnl <= 0]

    return {
        "count": n,
        "win_rate": len(winners) / n * 100,
        "avg_mfe_pct": round(avg(mfe_pcts), 3),
        "avg_mae_pct": round(avg(mae_pcts), 3),
        "avg_exit_r": round(avg(exit_rs), 3),
        "avg_peak_r": round(avg(peak_rs), 3),
        "avg_exit_efficiency": round(avg(efficiencies), 3),
        "avg_edge_ratio": round(avg(edge_ratios), 3),
        # Winner/Loser breakdown
        "winners_avg_mfe": round(avg([m.mfe_pct for m in winners]), 3) if winners else 0,
        "winners_avg_exit_eff": round(avg([m.exit_efficiency for m in winners if m.peak_r_multiple > 0]), 3) if winners else 0,
        "losers_avg_mae": round(avg([m.mae_pct for m in losers]), 3) if losers else 0,
        # Diagnostic: are we exiting too early or too late?
        "pct_exit_below_half_mfe": round(
            sum(1 for m in winners if m.exit_efficiency < 0.5) / max(len(winners), 1) * 100, 1
        ),
        "avg_hold_time_mins": round(avg([m.hold_time_mins for m in metrics]), 1),
    }


def save_mfe_mae_report(report: Dict[str, Any], output_dir: Optional[Path] = None) -> Path:
    """Save MFE/MAE analysis report to JSON."""
    out = output_dir or (DATA_DIR / "forensics")
    out.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = out / f"mfe_mae_{today}.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    log.info("MFE/MAE report saved: %s (%d trades)", path, report.get("total_trades", 0))
    return path


# ---------------------------------------------------------------------------
# Per-Strategy Exit Configuration (Book 39, Section 7)
# ---------------------------------------------------------------------------
# These parameters feed into the Rust exit engine via config.toml
# and are also used by the nightly Ouroboros pipeline for exit calibration.

PER_STRATEGY_EXIT_PARAMS: Dict[str, Dict[str, Any]] = {
    "VanguardSniper": {
        "initial_stop_atr_mult": 2.0,
        "rung_pcts": [0.0, 0.8, 1.5, 2.5, 4.0],
        "time_stop_max_minutes": 60,
        "exit_mode": "trailing",  # trailing only
    },
    "S2_Reversion": {
        "initial_stop_atr_mult": 1.5,
        "rung_pcts": [0.0, 0.5, 1.0, 1.5, 2.0],
        "time_stop_max_minutes": 30,
        "exit_mode": "hybrid",  # trailing + fixed target
        "fixed_target_pct": 1.5,  # Exit at +1.5%
    },
    "TypeF": {
        "initial_stop_atr_mult": 1.8,
        "rung_pcts": [0.0, 0.6, 1.2, 2.0, 3.5],
        "time_stop_max_minutes": 45,
        "exit_mode": "trailing",
    },
    "TypeB": {
        "initial_stop_atr_mult": 2.5,
        "rung_pcts": [0.0, 1.0, 2.0, 3.0, 5.0],
        "time_stop_max_minutes": 90,
        "exit_mode": "trailing",
    },
    "TypeA": {
        "initial_stop_atr_mult": 1.5,
        "rung_pcts": [0.0, 0.5, 1.0, 2.0, 3.0],
        "time_stop_max_minutes": 45,
        "exit_mode": "hybrid",
        "fixed_target_pct": 2.0,  # Dip recovery target +2%
    },
    "TypeE": {
        "initial_stop_atr_mult": 1.5,
        "rung_pcts": [0.0, 0.5, 1.0, 1.5, 2.0],
        "time_stop_max_minutes": 30,
        "exit_mode": "hybrid",
        "fixed_target_pct": 1.5,  # IBS mean reversion target +1.5%
    },
    "S5_OvernightCarry": {
        "initial_stop_atr_mult": 1.0,
        "rung_pcts": [0.0, 0.3, 0.6, 1.0, 1.5],
        "time_stop_max_minutes": 120,
        "exit_mode": "time_based",  # Exit at next open
    },
    "S7_TailHedge": {
        "initial_stop_atr_mult": 3.0,
        "rung_pcts": [0.0, 1.5, 3.0, 5.0, 8.0],
        "time_stop_max_minutes": 120,
        "exit_mode": "trailing",
    },
}

# Default for strategies not in the map
DEFAULT_EXIT_PARAMS: Dict[str, Any] = {
    "initial_stop_atr_mult": 1.5,
    "rung_pcts": [0.0, 0.8, 1.5, 2.5, 4.0],
    "time_stop_max_minutes": 45,
    "exit_mode": "trailing",
}


def get_exit_params(strategy: str) -> Dict[str, Any]:
    """Get per-strategy exit parameters, falling back to defaults."""
    return PER_STRATEGY_EXIT_PARAMS.get(strategy, DEFAULT_EXIT_PARAMS)
