"""Alpha Sieve — IC tracking and universe tier promotion/demotion.

Tracks Information Coefficient (IC) per ticker over a rolling window.
Tickers with decaying alpha get demoted; strong performers get promoted.

Spread monitoring: tickers with spreads > 0.5% are demoted from Vanguard.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .config import (
    ASER_DEMOTE_THRESHOLD,
    ASER_PROMOTE_THRESHOLD,
    IC_LOCK_THRESHOLD,
    IC_WARNING_THRESHOLD,
    SPREAD_WIDEN_THRESHOLD,
)
from .wal_reader import ClosedTrade


@dataclass(frozen=True)
class TickerAlpha:
    """Alpha assessment for a single ticker."""
    ticker_id: int
    ic: float
    trade_count: int
    avg_spread_pct: float
    prior_tier: int
    new_tier: int
    locked: bool
    demotion_reason: str


@dataclass(frozen=True)
class AlphaSieveResult:
    """Output of the alpha sieve analysis."""
    ticker_alphas: Dict[int, TickerAlpha]
    promotions: List[int]
    demotions: List[int]
    locked: List[int]


def sieve_universe(
    trades: List[ClosedTrade],
    prior_tiers: Dict[int, int],
    spread_data: Optional[Dict[int, float]] = None,
) -> AlphaSieveResult:
    """Reclassify universe tiers based on alpha and spread analysis.

    Args:
        trades: Closed trades from WAL.
        prior_tiers: Current tier assignment (1=Vanguard, 2=warm, 3=cold).
        spread_data: Optional avg spread % per ticker from today's data.

    Returns:
        AlphaSieveResult with new tier assignments.
    """
    # Group trades by ticker
    by_ticker: Dict[int, List[ClosedTrade]] = {}
    for t in trades:
        by_ticker.setdefault(t.ticker_id, []).append(t)

    alphas: Dict[int, TickerAlpha] = {}
    promotions: List[int] = []
    demotions: List[int] = []
    locked_list: List[int] = []

    # Evaluate all tickers with trades
    for tid, ticker_trades in by_ticker.items():
        prior = prior_tiers.get(tid, 2)
        spread = spread_data.get(tid, 0.0) if spread_data else 0.0
        alpha = _evaluate_ticker(tid, ticker_trades, prior, spread)
        alphas[tid] = alpha

        if alpha.locked:
            locked_list.append(tid)
        if alpha.new_tier < alpha.prior_tier:
            promotions.append(tid)
        elif alpha.new_tier > alpha.prior_tier:
            demotions.append(tid)

    # Include tickers with no trades but spread data (may demote)
    if spread_data:
        for tid, spread in spread_data.items():
            if tid not in alphas:
                prior = prior_tiers.get(tid, 2)
                alpha = _evaluate_no_trades(tid, prior, spread)
                alphas[tid] = alpha
                if alpha.new_tier > alpha.prior_tier:
                    demotions.append(tid)

    return AlphaSieveResult(
        ticker_alphas=alphas,
        promotions=promotions,
        demotions=demotions,
        locked=locked_list,
    )


def _evaluate_ticker(
    ticker_id: int,
    trades: List[ClosedTrade],
    prior_tier: int,
    avg_spread_pct: float,
) -> TickerAlpha:
    """Evaluate alpha for a ticker with trades."""
    n = len(trades)
    pnls = [t.final_pnl for t in trades]
    ic = _compute_ic(pnls)

    # Spread check
    if avg_spread_pct > SPREAD_WIDEN_THRESHOLD and prior_tier == 1:
        return TickerAlpha(
            ticker_id=ticker_id, ic=ic, trade_count=n,
            avg_spread_pct=avg_spread_pct, prior_tier=prior_tier,
            new_tier=2, locked=False,
            demotion_reason=f"spread {avg_spread_pct:.2f}% > {SPREAD_WIDEN_THRESHOLD}%",
        )

    # IC check
    locked = ic <= IC_LOCK_THRESHOLD and n >= 5
    if locked:
        return TickerAlpha(
            ticker_id=ticker_id, ic=ic, trade_count=n,
            avg_spread_pct=avg_spread_pct, prior_tier=prior_tier,
            new_tier=3, locked=True,
            demotion_reason=f"IC={ic:.4f} ≤ {IC_LOCK_THRESHOLD}",
        )

    # ASER-based promotion/demotion
    aser = _compute_aser(pnls)
    new_tier = prior_tier
    reason = ""
    if aser > ASER_PROMOTE_THRESHOLD and prior_tier > 1:
        new_tier = prior_tier - 1  # Promote
    elif aser < ASER_DEMOTE_THRESHOLD and prior_tier < 3:
        new_tier = prior_tier + 1  # Demote
        reason = f"ASER={aser:.4f} < {ASER_DEMOTE_THRESHOLD}"

    return TickerAlpha(
        ticker_id=ticker_id, ic=ic, trade_count=n,
        avg_spread_pct=avg_spread_pct, prior_tier=prior_tier,
        new_tier=new_tier, locked=False, demotion_reason=reason,
    )


def _evaluate_no_trades(
    ticker_id: int,
    prior_tier: int,
    avg_spread_pct: float,
) -> TickerAlpha:
    """Evaluate a ticker with no trades (spread-only demotion)."""
    new_tier = prior_tier
    reason = ""
    if avg_spread_pct > SPREAD_WIDEN_THRESHOLD and prior_tier == 1:
        new_tier = 2
        reason = f"spread {avg_spread_pct:.2f}% > {SPREAD_WIDEN_THRESHOLD}%"

    return TickerAlpha(
        ticker_id=ticker_id, ic=0.0, trade_count=0,
        avg_spread_pct=avg_spread_pct, prior_tier=prior_tier,
        new_tier=new_tier, locked=False, demotion_reason=reason,
    )


def _compute_ic(pnls: List[float]) -> float:
    """Compute simplified Information Coefficient from PnL sequence.

    IC = correlation between predicted direction (all +1 since we only go long)
    and actual return. Simplified: fraction_positive - fraction_negative.
    """
    if not pnls:
        return 0.0
    n = len(pnls)
    positive = sum(1 for p in pnls if p > 0)
    return (2.0 * positive / n) - 1.0


def _compute_aser(pnls: List[float]) -> float:
    """Compute simplified ASER (risk-adjusted return score) from PnL.

    ASER = mean(pnl) / std(pnl) if std > 0, else 0.
    Analogous to Sharpe ratio of trade-level returns.
    """
    if len(pnls) < 2:
        return 0.0
    mean_pnl = sum(pnls) / len(pnls)
    variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
    std_pnl = variance ** 0.5
    if std_pnl < 1e-10:
        return 0.0
    return mean_pnl / std_pnl
