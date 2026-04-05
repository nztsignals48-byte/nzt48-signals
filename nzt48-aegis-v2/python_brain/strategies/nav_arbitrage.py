"""ETP NAV Premium/Discount Arbitrage — Book 132.

Leveraged ETPs trade at a premium or discount to their theoretical NAV.
When this deviation exceeds statistical norms, mean reversion is likely
because Authorized Participants (APs) can create/redeem shares.

iNAV calculation:
  estimated_NAV_t = yesterday_NAV × (1 + L × intraday_return - daily_cost)

Signal:
  Premium z > +2.0 → ETP overpriced → sell (or long inverse)
  Discount z < -2.0 → ETP underpriced → buy

Exit: When premium/discount narrows to ±0.1% of NAV.

Note: Retail cannot create/redeem ETP shares directly. We rely on
APs doing so, which typically happens within 1-2 hours for liquid ETPs.

Usage:
    from python_brain.strategies.nav_arbitrage import (
        NAVTracker, NAVSignal,
    )

    tracker = NAVTracker()
    tracker.update("QQQ3.L", market_price=15.42, underlying_return=0.005)
    signal = tracker.check_signal("QQQ3.L")
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional

import numpy as np

log = logging.getLogger("nav_arbitrage")


@dataclass
class NAVState:
    """Tracking state for a single ETP's NAV deviation."""
    ticker: str
    yesterday_nav: float = 0.0
    estimated_nav: float = 0.0
    market_price: float = 0.0
    premium_pct: float = 0.0  # (market - nav) / nav * 100
    z_score: float = 0.0
    premium_history: Deque[float] = None
    leverage: int = 3
    daily_cost_pct: float = 0.0075 / 252  # TER / trading days

    def __post_init__(self):
        if self.premium_history is None:
            self.premium_history = deque(maxlen=120)


@dataclass
class NAVSignal:
    """Signal from NAV premium/discount detection."""
    ticker: str
    direction: str  # "buy" (discount) or "sell" (premium)
    premium_pct: float
    z_score: float
    estimated_nav: float
    market_price: float
    confidence: int
    target_premium_pct: float = 0.1  # Exit when premium narrows to 0.1%
    strategy: str = "NAVArbitrage"


# ETP metadata for NAV calculation
ETP_NAV_INFO: Dict[str, Dict] = {
    "3USL.L": {"underlying_proxy": "SPY", "leverage": 3, "currency": "GBP"},
    "QQQ3.L": {"underlying_proxy": "QQQ", "leverage": 3, "currency": "GBP"},
    "NVD3.L": {"underlying_proxy": "NVDA", "leverage": 3, "currency": "GBP"},
    "TSL3.L": {"underlying_proxy": "TSLA", "leverage": 3, "currency": "GBP"},
    "3USS.L": {"underlying_proxy": "SPY", "leverage": -3, "currency": "GBP"},
    "QQQS.L": {"underlying_proxy": "QQQ", "leverage": -3, "currency": "GBP"},
}


class NAVTracker:
    """Track NAV deviations across all ETPs."""

    def __init__(self, z_entry: float = 2.0, z_exit: float = 0.5):
        self.z_entry = z_entry
        self.z_exit = z_exit
        self._states: Dict[str, NAVState] = {}

    def initialize_nav(self, ticker: str, yesterday_nav: float, leverage: int = 3):
        """Set yesterday's closing NAV for an ETP."""
        state = self._states.get(ticker, NAVState(ticker=ticker))
        state.yesterday_nav = yesterday_nav
        state.leverage = leverage
        self._states[ticker] = state

    def update(
        self,
        ticker: str,
        market_price: float,
        underlying_intraday_return: float,
        ibkr_nav_last: float = 0.0,
        ibkr_nav_bid: float = 0.0,
        ibkr_nav_ask: float = 0.0,
        ibkr_nav_close: float = 0.0,
    ):
        """Update NAV estimate and premium/discount.

        Args:
            ticker: ETP ticker
            market_price: Current market price
            underlying_intraday_return: Decimal return of underlying since yesterday close
            ibkr_nav_last: Real-time ETF NAV from IBKR (tick type 96). 0 = unavailable.
            ibkr_nav_bid: ETF NAV bid from IBKR (tick type 94). 0 = unavailable.
            ibkr_nav_ask: ETF NAV ask from IBKR (tick type 95). 0 = unavailable.
            ibkr_nav_close: Yesterday's ETF NAV close from IBKR (tick type 92). 0 = unavailable.
        """
        state = self._states.get(ticker)
        if state is None:
            state = NAVState(ticker=ticker)
            self._states[ticker] = state

        # Use IBKR real NAV if available (tick types 92-99), else estimate
        if ibkr_nav_close > 0:
            state.yesterday_nav = ibkr_nav_close

        if state.yesterday_nav <= 0:
            state.yesterday_nav = market_price
            return

        # IBKR real-time NAV takes priority over estimation
        if ibkr_nav_last > 0:
            state.estimated_nav = ibkr_nav_last
        elif ibkr_nav_bid > 0 and ibkr_nav_ask > 0:
            state.estimated_nav = (ibkr_nav_bid + ibkr_nav_ask) / 2.0
        else:
            # Fallback: estimate NAV from underlying return
            L = state.leverage
            state.estimated_nav = state.yesterday_nav * (
                1 + L * underlying_intraday_return - state.daily_cost_pct
            )

        state.market_price = market_price

        # Premium/discount
        if state.estimated_nav > 0:
            state.premium_pct = (market_price - state.estimated_nav) / state.estimated_nav * 100
        else:
            state.premium_pct = 0.0

        state.premium_history.append(state.premium_pct)

        # Z-score vs rolling history
        if len(state.premium_history) >= 20:
            arr = np.array(list(state.premium_history))
            mean = np.mean(arr)
            std = np.std(arr, ddof=1)
            state.z_score = (state.premium_pct - mean) / max(std, 1e-6)
        else:
            state.z_score = 0.0

    def check_signal(self, ticker: str) -> Optional[NAVSignal]:
        """Check if NAV deviation warrants a trade."""
        state = self._states.get(ticker)
        if state is None or len(state.premium_history) < 20:
            return None

        z = state.z_score

        if abs(z) < self.z_entry:
            return None

        # ISA: can only go long
        # Discount (z < -2) → ETP cheap → BUY
        # Premium (z > +2) → ETP expensive → need inverse (can't short)
        if z < -self.z_entry:
            direction = "buy"
        elif z > self.z_entry:
            # Could buy inverse ETP instead
            return None  # Skip premium for now — ISA constraint

        # Confidence
        base_conf = 58
        z_bonus = min(15, int((abs(z) - self.z_entry) * 8))
        history_bonus = min(10, len(state.premium_history) // 12)
        confidence = min(85, base_conf + z_bonus + history_bonus)

        return NAVSignal(
            ticker=ticker,
            direction=direction,
            premium_pct=round(state.premium_pct, 4),
            z_score=round(z, 3),
            estimated_nav=round(state.estimated_nav, 4),
            market_price=round(state.market_price, 4),
            confidence=confidence,
        )

    def get_all_states(self) -> Dict[str, dict]:
        return {
            ticker: {
                "premium_pct": round(s.premium_pct, 4),
                "z_score": round(s.z_score, 3),
                "nav": round(s.estimated_nav, 4),
                "price": round(s.market_price, 4),
                "history_len": len(s.premium_history),
            }
            for ticker, s in self._states.items()
        }
