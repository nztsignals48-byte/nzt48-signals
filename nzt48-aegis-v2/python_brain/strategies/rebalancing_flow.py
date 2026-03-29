"""ETP Rebalancing Flow Prediction — Book 36.

Leveraged ETPs rebalance daily at market close. When the underlying
moves significantly intraday (>1.5%), the ETP provider must buy/sell
additional exposure to maintain the target leverage.

This creates predictable order flow:
  - Underlying up 2% → 3x ETP provider must BUY more to maintain 3x
  - Underlying down 2% → 3x ETP provider must SELL to maintain 3x

The rebalancing window is 19:00-20:00 GMT (US session).
We enter the ETP in the DIRECTION of expected rebalancing flow.

Rebalancing notional formula:
  Notional ≈ AUM × L × (L-1) × |daily_return|

Usage:
    from python_brain.strategies.rebalancing_flow import (
        predict_rebalancing, RebalancingSignal,
    )

    signal = predict_rebalancing(
        underlying_intraday_return=0.025,  # +2.5%
        etp_ticker="QQQ3.L",
        current_time_utc_secs=68400,  # 19:00 UTC
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

log = logging.getLogger("rebalancing_flow")


# ETP → underlying mapping with estimated AUM
ETP_REBALANCING_MAP: Dict[str, Dict] = {
    "3USL.L": {"underlying": "SPX", "leverage": 3, "aum_mm": 500, "direction": "long"},
    "QQQ3.L": {"underlying": "NDX", "leverage": 3, "aum_mm": 300, "direction": "long"},
    "NVD3.L": {"underlying": "NVDA", "leverage": 3, "aum_mm": 100, "direction": "long"},
    "TSL3.L": {"underlying": "TSLA", "leverage": 3, "aum_mm": 80, "direction": "long"},
    "AMD3.L": {"underlying": "AMD", "leverage": 3, "aum_mm": 50, "direction": "long"},
    "GOO3.L": {"underlying": "GOOG", "leverage": 3, "aum_mm": 60, "direction": "long"},
    "AAP3.L": {"underlying": "AAPL", "leverage": 3, "aum_mm": 80, "direction": "long"},
    "MSF3.L": {"underlying": "MSFT", "leverage": 3, "aum_mm": 70, "direction": "long"},
    "GPT3.L": {"underlying": "NVDA", "leverage": 3, "aum_mm": 40, "direction": "long"},
    # Inverse ETPs — rebalancing flow is OPPOSITE
    "3USS.L": {"underlying": "SPX", "leverage": 3, "aum_mm": 200, "direction": "inverse"},
    "QQQS.L": {"underlying": "NDX", "leverage": 3, "aum_mm": 100, "direction": "inverse"},
}


@dataclass
class RebalancingSignal:
    """Signal from predicted ETP rebalancing flow."""
    etp_ticker: str
    underlying: str
    predicted_flow_direction: str  # "buy" or "sell"
    underlying_return_pct: float
    estimated_rebalancing_notional_mm: float
    confidence: int
    entry_window_start_utc: int  # seconds from midnight
    entry_window_end_utc: int
    strategy: str = "RebalancingFlow"


def predict_rebalancing(
    underlying_intraday_return: float,
    etp_ticker: str,
    current_time_utc_secs: int,
    min_underlying_move: float = 0.015,  # 1.5% minimum
) -> Optional[RebalancingSignal]:
    """Predict rebalancing flow direction and magnitude.

    Args:
        underlying_intraday_return: Decimal return (e.g., 0.025 for +2.5%)
        etp_ticker: ETP to trade
        current_time_utc_secs: Current time in seconds from midnight UTC
        min_underlying_move: Minimum underlying move to trigger signal

    Returns: RebalancingSignal if conditions met, None otherwise.
    """
    if abs(underlying_intraday_return) < min_underlying_move:
        return None

    info = ETP_REBALANCING_MAP.get(etp_ticker)
    if not info:
        return None

    # Must be in rebalancing window: 19:00-20:00 UTC (68400-72000 secs)
    if current_time_utc_secs < 66600 or current_time_utc_secs > 72000:
        # Allow entry 30 min before window starts (18:30) for positioning
        return None

    L = info["leverage"]
    aum = info["aum_mm"]
    is_inverse = info["direction"] == "inverse"

    # Rebalancing notional
    rebal_notional = aum * L * (L - 1) * abs(underlying_intraday_return)

    # Flow direction:
    # Long ETP + underlying up → provider BUYS more → bullish flow
    # Long ETP + underlying down → provider SELLS → bearish flow
    # Inverse: opposite
    if is_inverse:
        flow_dir = "sell" if underlying_intraday_return > 0 else "buy"
    else:
        flow_dir = "buy" if underlying_intraday_return > 0 else "sell"

    # ISA constraint: can only go long
    # If flow direction is "sell", we can't profit directly
    # (would need inverse ETP)
    if flow_dir == "sell":
        return None  # Skip for now — ISA can't short

    # Confidence based on move magnitude and AUM
    base_conf = 58
    move_bonus = min(15, int(abs(underlying_intraday_return) * 500))  # +15 max
    aum_bonus = min(10, int(aum / 50))  # Larger AUM = more flow
    confidence = min(85, base_conf + move_bonus + aum_bonus)

    return RebalancingSignal(
        etp_ticker=etp_ticker,
        underlying=info["underlying"],
        predicted_flow_direction=flow_dir,
        underlying_return_pct=round(underlying_intraday_return * 100, 2),
        estimated_rebalancing_notional_mm=round(rebal_notional, 1),
        confidence=confidence,
        entry_window_start_utc=66600,  # 18:30 UTC
        entry_window_end_utc=72000,    # 20:00 UTC
    )


def scan_all_rebalancing(
    underlying_returns: Dict[str, float],
    current_time_utc_secs: int,
) -> List[RebalancingSignal]:
    """Scan all ETPs for rebalancing opportunities.

    Args:
        underlying_returns: {underlying_ticker: intraday_return}
        current_time_utc_secs: Current time

    Returns: List of rebalancing signals, sorted by confidence.
    """
    signals = []
    for etp, info in ETP_REBALANCING_MAP.items():
        underlying = info["underlying"]
        ret = underlying_returns.get(underlying, 0.0)
        sig = predict_rebalancing(ret, etp, current_time_utc_secs)
        if sig:
            signals.append(sig)

    signals.sort(key=lambda s: s.confidence, reverse=True)
    return signals
