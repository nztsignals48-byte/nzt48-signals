"""Regime Hunting — identify profitable market regimes from trade data.

Classifies each trade into one of 4 regime labels based on
market conditions at entry time:
  - bull_quiet: positive trend + low vol
  - bull_volatile: positive trend + high vol
  - bear_quiet: negative trend + low vol
  - bear_volatile: negative trend + high vol

Output: per-regime statistics for next-day regime-aware sizing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .config import REGIME_LABELS
from .wal_reader import ClosedTrade


@dataclass(frozen=True)
class RegimeStats:
    """Performance statistics for a single regime."""
    label: str
    trade_count: int
    win_rate: float
    avg_pnl: float
    total_pnl: float
    is_profitable: bool


@dataclass(frozen=True)
class RegimeHuntResult:
    """Output of regime hunting analysis."""
    regimes: Dict[str, RegimeStats]
    best_regime: str
    worst_regime: str
    total_trades: int


def hunt_regimes(
    trades: List[ClosedTrade],
    regime_labels: Dict[int, str] | None = None,
) -> RegimeHuntResult:
    """Identify profitable regimes from closed trade data.

    Args:
        trades: Closed trades from WAL.
        regime_labels: Optional mapping of ticker_id to regime at entry.
            If None, uses the regime_label field on each trade.

    Returns:
        RegimeHuntResult with per-regime statistics.
    """
    if not trades:
        empty_regimes = {
            label: RegimeStats(
                label=label, trade_count=0, win_rate=0.0,
                avg_pnl=0.0, total_pnl=0.0, is_profitable=False,
            )
            for label in REGIME_LABELS
        }
        return RegimeHuntResult(
            regimes=empty_regimes,
            best_regime=REGIME_LABELS[0],
            worst_regime=REGIME_LABELS[-1],
            total_trades=0,
        )

    # Group trades by regime
    by_regime: Dict[str, List[ClosedTrade]] = {r: [] for r in REGIME_LABELS}
    for t in trades:
        label = ""
        if regime_labels and t.ticker_id in regime_labels:
            label = regime_labels[t.ticker_id]
        elif t.regime_label:
            label = t.regime_label

        if label in by_regime:
            by_regime[label].append(t)
        else:
            # Unknown regime → assign to bull_quiet as default
            by_regime[REGIME_LABELS[0]].append(t)

    # Compute per-regime stats
    regimes: Dict[str, RegimeStats] = {}
    for label in REGIME_LABELS:
        group = by_regime[label]
        regimes[label] = _compute_regime_stats(label, group)

    # Find best/worst
    best = max(regimes.values(), key=lambda r: r.avg_pnl)
    worst = min(regimes.values(), key=lambda r: r.avg_pnl)

    return RegimeHuntResult(
        regimes=regimes,
        best_regime=best.label,
        worst_regime=worst.label,
        total_trades=len(trades),
    )


def _compute_regime_stats(
    label: str,
    trades: List[ClosedTrade],
) -> RegimeStats:
    """Compute statistics for a single regime group."""
    n = len(trades)
    if n == 0:
        return RegimeStats(
            label=label,
            trade_count=0,
            win_rate=0.0,
            avg_pnl=0.0,
            total_pnl=0.0,
            is_profitable=False,
        )

    pnls = [t.final_pnl for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    total_pnl = sum(pnls)
    avg_pnl = total_pnl / n

    return RegimeStats(
        label=label,
        trade_count=n,
        win_rate=wins / n,
        avg_pnl=avg_pnl,
        total_pnl=total_pnl,
        is_profitable=avg_pnl > 0,
    )
