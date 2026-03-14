"""Exit Calibration — MAE/MFE analysis for Chandelier multiplier tuning.

Analyzes Maximum Adverse Excursion (MAE) and Maximum Favorable Excursion
(MFE) from closed trades to adjust the Chandelier ATR multiplier.

If trades consistently reach Rung 5 (>60% of time), the multiplier
LOOSENS to let profits run longer. If trades consistently stop out
early, the multiplier TIGHTENS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .config import (
    CHANDELIER_ATR_MULT_DEFAULT,
    CHANDELIER_ATR_MULT_MAX,
    CHANDELIER_ATR_MULT_MIN,
    MFE_RUNG5_THRESHOLD,
)
from .wal_reader import ClosedTrade


@dataclass(frozen=True)
class ExitCalibrationResult:
    """Output of exit calibration analysis."""
    prior_multiplier: float
    new_multiplier: float
    trade_count: int
    rung5_rate: float
    early_stop_rate: float
    avg_mae_pct: float
    avg_mfe_pct: float


def calibrate_exit_multiplier(
    trades: List[ClosedTrade],
    prior_multiplier: float = CHANDELIER_ATR_MULT_DEFAULT,
) -> ExitCalibrationResult:
    """Calibrate the Chandelier ATR multiplier from trade outcomes.

    Args:
        trades: Closed trades with PnL and exit reason data.
        prior_multiplier: Current Chandelier multiplier.

    Returns:
        ExitCalibrationResult with new multiplier.
    """
    if not trades:
        return ExitCalibrationResult(
            prior_multiplier=prior_multiplier,
            new_multiplier=prior_multiplier,
            trade_count=0,
            rung5_rate=0.0,
            early_stop_rate=0.0,
            avg_mae_pct=0.0,
            avg_mfe_pct=0.0,
        )

    n = len(trades)

    # Classify trades by outcome
    rung5_count = sum(1 for t in trades if t.highest_rung >= 5)
    early_stop_count = sum(
        1 for t in trades if t.final_pnl < 0 and t.highest_rung <= 1
    )
    rung5_rate = rung5_count / n
    early_stop_rate = early_stop_count / n

    # Compute MAE/MFE from PnL (simplified — full impl needs tick data)
    entry_prices = [t.entry_price for t in trades if t.entry_price > 0]
    avg_mae_pct = _avg_adverse_excursion(trades)
    avg_mfe_pct = _avg_favorable_excursion(trades)

    # Adjustment logic
    adjustment = 0.0
    if rung5_rate > MFE_RUNG5_THRESHOLD:
        # Trades frequently hit Rung 5 → loosen to let profits run
        adjustment = 0.2
    elif early_stop_rate > MFE_RUNG5_THRESHOLD:
        # Trades frequently stop out early → tighten
        adjustment = -0.2

    new_mult = prior_multiplier + adjustment
    new_mult = max(CHANDELIER_ATR_MULT_MIN, min(CHANDELIER_ATR_MULT_MAX, new_mult))

    return ExitCalibrationResult(
        prior_multiplier=prior_multiplier,
        new_multiplier=new_mult,
        trade_count=n,
        rung5_rate=rung5_rate,
        early_stop_rate=early_stop_rate,
        avg_mae_pct=avg_mae_pct,
        avg_mfe_pct=avg_mfe_pct,
    )


def _avg_adverse_excursion(trades: List[ClosedTrade]) -> float:
    """Average MAE as percentage of entry price."""
    losses = [t for t in trades if t.final_pnl < 0 and t.entry_price > 0]
    if not losses:
        return 0.0
    maes = [abs(t.final_pnl) / (t.entry_price * max(t.qty, 1)) * 100.0
            for t in losses]
    return sum(maes) / len(maes)


def _avg_favorable_excursion(trades: List[ClosedTrade]) -> float:
    """Average MFE as percentage of entry price."""
    wins = [t for t in trades if t.final_pnl > 0 and t.entry_price > 0]
    if not wins:
        return 0.0
    mfes = [t.final_pnl / (t.entry_price * max(t.qty, 1)) * 100.0
            for t in wins]
    return sum(mfes) / len(mfes)
