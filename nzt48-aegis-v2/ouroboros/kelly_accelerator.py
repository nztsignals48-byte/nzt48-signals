"""Kelly Accelerator — recalibrate Kelly fractions from observed outcomes.

For each ticker, computes optimal Kelly fraction from:
  - Bayesian win rate (shrunk toward 50% for small samples)
  - Average win / average loss ratio
  - Exponential weighted average blending with prior

Output: per-ticker kelly_fraction clamped to [KELLY_FLOOR, KELLY_CEILING].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .bayesian import bayesian_win_rate
from .config import (
    KELLY_CEILING,
    KELLY_FLOOR,
    KELLY_HALF_CAP,
    KELLY_LEARNING_RATE,
)
from .wal_reader import ClosedTrade


@dataclass(frozen=True)
class KellyUpdate:
    """Updated Kelly fraction for a single ticker."""
    ticker_id: int
    new_kelly: float
    prior_kelly: float
    trade_count: int
    bayesian_wr: float
    win_loss_ratio: float


def compute_kelly_updates(
    trades: List[ClosedTrade],
    prior_kellys: Dict[int, float],
) -> Dict[int, KellyUpdate]:
    """Compute updated Kelly fractions for all traded tickers.

    Args:
        trades: Closed trades from today's WAL.
        prior_kellys: Previous Kelly fractions per ticker_id.

    Returns:
        Dict of ticker_id → KellyUpdate with new fractions.
    """
    # Group trades by ticker
    by_ticker: Dict[int, List[float]] = {}
    for t in trades:
        by_ticker.setdefault(t.ticker_id, []).append(t.final_pnl)

    updates: Dict[int, KellyUpdate] = {}
    for tid, pnls in by_ticker.items():
        prior = prior_kellys.get(tid, KELLY_FLOOR)
        update = _kelly_for_ticker(tid, pnls, prior)
        if update is not None:
            updates[tid] = update

    return updates


def _kelly_for_ticker(
    ticker_id: int,
    pnls: List[float],
    prior_kelly: float,
) -> Optional[KellyUpdate]:
    """Compute Kelly fraction for a single ticker."""
    if not pnls:
        return None

    bwr = bayesian_win_rate(pnls)
    p = bwr.bayesian_win_rate
    # Win/loss ratio: avoid division by zero
    avg_loss_abs = abs(bwr.avg_loss) if bwr.avg_loss != 0 else 1e-10
    b = bwr.avg_win / avg_loss_abs if bwr.avg_win > 0 else 0.0

    # Kelly criterion: f* = p - (1-p)/b
    if b > 0:
        raw_kelly = p - (1.0 - p) / b
    else:
        raw_kelly = 0.0

    # Apply half-Kelly cap
    half_kelly = raw_kelly * KELLY_HALF_CAP

    # Clamp to [FLOOR, CEILING]
    clamped = max(KELLY_FLOOR, min(KELLY_CEILING, half_kelly))

    # EWA blend with prior (learning rate α)
    blended = (
        KELLY_LEARNING_RATE * clamped
        + (1.0 - KELLY_LEARNING_RATE) * prior_kelly
    )
    final = max(KELLY_FLOOR, min(KELLY_CEILING, blended))

    return KellyUpdate(
        ticker_id=ticker_id,
        new_kelly=final,
        prior_kelly=prior_kelly,
        trade_count=bwr.trade_count,
        bayesian_wr=bwr.bayesian_win_rate,
        win_loss_ratio=b,
    )
