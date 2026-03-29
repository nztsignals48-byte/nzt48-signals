"""ETP Decay & Holding Period Monitor — Book 46.

Leveraged ETPs lose value through volatility decay (vol drag).
This module monitors decay rates and enforces holding period limits.

Decay formula: decay_drag = L*(L-1)/2 * sigma^2
  For 3x at 2% daily vol: drag = 3*2/2 * 0.0004 = 0.12% per day

VIX-conditional max holding periods (days):
  VIX < 15:  3x ETP → 3-5 days
  VIX 15-20: 3x ETP → 1-3 days
  VIX 20-25: 3x ETP → 1 day (intraday preferred)
  VIX 25-35: 3x ETP → intraday only
  VIX > 35:  3x ETP → AVOID entirely

Usage:
    from python_brain.forensics.etp_decay_monitor import (
        ETPDecayMonitor, max_holding_days,
    )

    monitor = ETPDecayMonitor()
    monitor.check_position("NVD3.L", entry_date="2026-03-25", vix=22)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("etp_decay_monitor")


# VIX-conditional max holding periods (trading days)
MAX_HOLDING_DAYS: Dict[str, Dict[str, int]] = {
    # Instrument type → VIX bucket → max days
    "3x_index": {"<15": 5, "15-20": 3, "20-25": 1, "25-35": 0, ">35": 0},
    "3x_single": {"<15": 3, "15-20": 2, "20-25": 1, "25-35": 0, ">35": 0},
    "3x_commodity": {"<15": 5, "15-20": 3, "20-25": 2, "25-35": 1, ">35": 0},
    "3x_inverse": {"<15": 3, "15-20": 2, "20-25": 1, "25-35": 1, ">35": 0},
    "5x": {"<15": 0, "15-20": 0, "20-25": 0, "25-35": 0, ">35": 0},  # NEVER
}

# Ticker → instrument type
TICKER_TYPES: Dict[str, str] = {
    "3USL.L": "3x_index", "QQQ3.L": "3x_index", "3UKL.L": "3x_index",
    "NVD3.L": "3x_single", "TSL3.L": "3x_single", "AAP3.L": "3x_single",
    "MSF3.L": "3x_single", "GOO3.L": "3x_single", "AMD3.L": "3x_single",
    "MET3.L": "3x_single", "GPT3.L": "3x_single", "MS23.L": "3x_single",
    "COI3.L": "3x_single",
    "3LOI.L": "3x_commodity", "3LGD.L": "3x_commodity", "3LSV.L": "3x_commodity",
    "3USS.L": "3x_inverse", "QQQS.L": "3x_inverse", "NV3S.L": "3x_inverse",
    "TS3S.L": "3x_inverse", "3UKS.L": "3x_inverse",
    "VIXL.L": "3x_single",  # Treat as single stock for decay purposes
}


def _vix_bucket(vix: float) -> str:
    if vix < 15:
        return "<15"
    elif vix < 20:
        return "15-20"
    elif vix < 25:
        return "20-25"
    elif vix < 35:
        return "25-35"
    return ">35"


def max_holding_days(ticker: str, vix: float) -> int:
    """Get maximum holding period in trading days for a ticker at current VIX."""
    itype = TICKER_TYPES.get(ticker, "3x_single")
    bucket = _vix_bucket(vix)
    return MAX_HOLDING_DAYS.get(itype, MAX_HOLDING_DAYS["3x_single"]).get(bucket, 1)


def estimate_daily_decay(leverage: int, daily_vol: float) -> float:
    """Estimate daily volatility decay (drag) for a leveraged ETP.

    Formula: decay = L*(L-1)/2 * sigma^2
    Returns: Daily decay rate (decimal, e.g., 0.0012 for 0.12%)
    """
    return leverage * (leverage - 1) / 2 * daily_vol * daily_vol


@dataclass
class HoldingAlert:
    """Alert for a position exceeding its holding period."""
    ticker: str
    days_held: int
    max_days: int
    vix: float
    estimated_decay_pct: float
    action: str  # "WARNING", "FORCE_CLOSE"


class ETPDecayMonitor:
    """Monitor ETP positions for decay risk and holding period violations."""

    def __init__(self):
        self._alerts: List[HoldingAlert] = []

    def check_position(
        self,
        ticker: str,
        entry_date: str,
        vix: float,
        daily_vol: float = 0.02,
        leverage: int = 3,
    ) -> Optional[HoldingAlert]:
        """Check if a position has exceeded its maximum holding period.

        Args:
            ticker: ETP ticker
            entry_date: ISO date string (YYYY-MM-DD)
            vix: Current VIX level
            daily_vol: Daily realized volatility of the ETP
            leverage: Leverage factor

        Returns: HoldingAlert if position should be reduced/closed
        """
        try:
            entry = datetime.strptime(entry_date, "%Y-%m-%d").date()
        except ValueError:
            return None

        today = datetime.now(timezone.utc).date()
        days_held = (today - entry).days
        max_days = max_holding_days(ticker, vix)

        # Estimate cumulative decay
        daily_decay = estimate_daily_decay(leverage, daily_vol)
        cumulative_decay = daily_decay * days_held * 100  # As percentage

        if days_held > max_days:
            action = "FORCE_CLOSE" if days_held > max_days * 1.5 else "WARNING"
            alert = HoldingAlert(
                ticker=ticker,
                days_held=days_held,
                max_days=max_days,
                vix=vix,
                estimated_decay_pct=round(cumulative_decay, 3),
                action=action,
            )
            self._alerts.append(alert)

            log.warning(
                "ETP_DECAY: %s held %dd (max=%dd at VIX=%.0f), decay≈%.2f%% — %s",
                ticker, days_held, max_days, vix, cumulative_decay, action,
            )
            return alert

        return None

    def check_all_positions(
        self,
        positions: Dict[str, Dict],
        vix: float,
    ) -> List[HoldingAlert]:
        """Check all open positions for decay risk."""
        alerts = []
        for ticker, info in positions.items():
            entry_date = info.get("entry_date", "")
            if not entry_date:
                continue
            daily_vol = info.get("daily_vol", 0.02)
            leverage = info.get("leverage", 3)
            alert = self.check_position(ticker, entry_date, vix, daily_vol, leverage)
            if alert:
                alerts.append(alert)
        return alerts

    def break_even_vol(self, leverage: int, expected_return: float) -> float:
        """Compute break-even volatility for a leveraged ETP.

        Break-even: the vol level where decay exactly offsets expected return.
        sigma_BE = sqrt(2 * mu / (L*(L-1)))
        """
        if leverage <= 1:
            return float("inf")
        return (2 * expected_return / (leverage * (leverage - 1))) ** 0.5
