"""
NZT-48 Earnings Specialist Bot
Phase 2: Dedicated bot that only activates during earnings seasons.
Trades PEAD (Post-Earnings Announcement Drift) — the tendency for
stocks to continue drifting in the direction of earnings surprise.

Activation: 4 earnings windows per year
Core strategy: S5 (PEAD)
Entry: 15-45 minutes after open on earnings day
Risk: 0.5% per trade (reduced from 0.75%)
Stop: 2.0x ATR (wider for earnings volatility)
"""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, date, timezone
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    Signal, Direction, BotInstance, RegimeState,
    MarketContext, IndicatorSnapshot, Position,
)
import config as cfg


# Earnings season windows (month, day) tuples
EARNINGS_WINDOWS = [
    ((1, 15), (2, 28)),   # Q4 earnings: Jan 15 - Feb 28
    ((4, 15), (5, 31)),   # Q1 earnings: Apr 15 - May 31
    ((7, 15), (8, 31)),   # Q2 earnings: Jul 15 - Aug 31
    ((10, 15), (11, 30)), # Q3 earnings: Oct 15 - Nov 30
]


class EarningsSpecialist:
    """Dedicated earnings trading bot.

    Only activates during the 4 earnings season windows.
    Focuses exclusively on PEAD (Post-Earnings Announcement Drift).

    Key principles:
    1. Wait 15-45 min after open for initial volatility to settle
    2. Trade in direction of earnings surprise (beat = long, miss = short)
    3. Wider stops (2x ATR) because earnings moves are volatile
    4. Reduced risk (0.5%) because of gap risk
    5. Only trades tickers with market cap > $5B (no small-cap earnings plays)
    6. Max 3 simultaneous earnings positions
    7. Uses volume confirmation (RVOL > 2.0 on earnings day)
    """

    def __init__(self):
        self.logger = logging.getLogger("nzt48.bot.EARNINGS")
        self.instance = BotInstance.EARNINGS

        # Config
        bot_cfg = cfg.get("bots.earnings_bot", {})
        self.core_strategy = bot_cfg.get("core_strategy", "S5")
        self.risk_per_trade = bot_cfg.get("risk_per_trade", 0.005)
        self.stop_atr_mult = 2.0
        self.max_positions = bot_cfg.get("max_positions", 3)
        self.entry_timing = bot_cfg.get("entry_timing_minutes", [15, 45])
        self.blacklist_mcap_below = bot_cfg.get("blacklist_mcap_below", 5_000_000_000)
        self.min_rvol = 2.0
        self.min_confidence = 65

        # State
        self.active = False
        self.open_positions: list[Position] = []
        self._earnings_calendar: dict[str, dict] = {}  # ticker -> {date, surprise, direction}
        self._daily_trades = 0

    def is_earnings_season(self, d: Optional[date] = None) -> bool:
        """Check if current date falls within an earnings season window."""
        d = d or date.today()
        for (start_m, start_d), (end_m, end_d) in EARNINGS_WINDOWS:
            start = date(d.year, start_m, start_d)
            end = date(d.year, end_m, end_d)
            if start <= d <= end:
                return True
        return False

    def activate(self) -> None:
        """Check if the bot should be active and activate if so."""
        if self.is_earnings_season():
            self.active = True
            self.logger.info("EARNINGS BOT ACTIVATED — earnings season window")
        else:
            self.active = False
            self.logger.info("EARNINGS BOT DORMANT — outside earnings season")

    def update_earnings_calendar(self, calendar: dict[str, dict]) -> None:
        """Update the earnings calendar from the calendar feed.

        Args:
            calendar: Dict of ticker -> {
                'date': '2024-01-25',
                'time': 'AMC' or 'BMO',
                'estimate_eps': 1.23,
                'actual_eps': 1.45,
                'surprise_pct': 17.9,
            }
        """
        self._earnings_calendar = calendar
        self.logger.info("Earnings calendar updated: %d tickers", len(calendar))

    def is_earnings_day(self, ticker: str, d: Optional[date] = None) -> bool:
        """Check if today is an earnings day for this ticker."""
        d = d or date.today()
        entry = self._earnings_calendar.get(ticker)
        if not entry:
            return False
        try:
            earn_date = datetime.strptime(entry.get("date", ""), "%Y-%m-%d").date()
            return earn_date == d
        except (ValueError, AttributeError):
            return False

    def get_earnings_direction(self, ticker: str) -> Optional[Direction]:
        """Determine trade direction from earnings surprise."""
        entry = self._earnings_calendar.get(ticker)
        if not entry:
            return None

        surprise = entry.get("surprise_pct", 0)
        if surprise > 5.0:   # Strong beat: long
            return Direction.LONG
        elif surprise < -5.0: # Strong miss: short
            return Direction.SHORT
        return None  # Too close to estimates — skip

    def is_entry_window(self, minutes_since_open: float) -> bool:
        """Check if we're in the entry timing window (15-45 min after open)."""
        if len(self.entry_timing) >= 2:
            return self.entry_timing[0] <= minutes_since_open <= self.entry_timing[1]
        return 15 <= minutes_since_open <= 45

    def can_trade(self, ticker: str, indicators: IndicatorSnapshot) -> tuple[bool, str]:
        """Check if this ticker qualifies for an earnings trade."""
        if not self.active:
            return False, "Earnings bot not active"
        if len(self.open_positions) >= self.max_positions:
            return False, f"Max positions ({self.max_positions}) reached"
        if not self.is_earnings_day(ticker):
            return False, "Not an earnings day for this ticker"
        if indicators.rvol < self.min_rvol:
            return False, f"RVOL {indicators.rvol:.1f} < min {self.min_rvol}"
        if indicators.market_cap > 0 and indicators.market_cap < self.blacklist_mcap_below:
            return False, f"Market cap ${indicators.market_cap/1e9:.1f}B < ${self.blacklist_mcap_below/1e9:.0f}B minimum"
        return True, "OK"

    def generate_signal(
        self,
        ticker: str,
        indicators: IndicatorSnapshot,
        market_ctx: MarketContext,
        minutes_since_open: float,
    ) -> Optional[Signal]:
        """Generate an earnings trade signal if all conditions are met."""
        # Check basic eligibility
        can, reason = self.can_trade(ticker, indicators)
        if not can:
            return None

        # Check entry timing window
        if not self.is_entry_window(minutes_since_open):
            return None

        # Get direction from surprise
        direction = self.get_earnings_direction(ticker)
        if direction is None:
            self.logger.info("EARNINGS SKIP %s: surprise too small", ticker)
            return None

        price = indicators.price
        atr = indicators.atr14

        if price <= 0 or atr <= 0:
            return None

        # Compute levels
        stop_distance = atr * self.stop_atr_mult

        if direction == Direction.LONG:
            stop = price - stop_distance
            target_1r = price + stop_distance       # 1R
            target_2r = price + (stop_distance * 2)  # 2R — PEAD often runs
        else:
            stop = price + stop_distance
            target_1r = price - stop_distance
            target_2r = price - (stop_distance * 2)

        # Confidence based on earnings quality
        earnings_data = self._earnings_calendar.get(ticker, {})
        surprise_pct = abs(earnings_data.get("surprise_pct", 0))

        confidence = 60  # Baseline
        if surprise_pct > 20:
            confidence += 15
        elif surprise_pct > 10:
            confidence += 10
        elif surprise_pct > 5:
            confidence += 5

        # Volume confirmation bonus
        if indicators.rvol > 3.0:
            confidence += 5

        # Regime alignment bonus
        if direction == Direction.LONG and market_ctx.regime in [
            RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD
        ]:
            confidence += 5
        elif direction == Direction.SHORT and market_ctx.regime in [
            RegimeState.TRENDING_DOWN_STRONG, RegimeState.TRENDING_DOWN_MOD
        ]:
            confidence += 5

        if confidence < self.min_confidence:
            return None

        signal = Signal(
            id=f"EARN-{str(uuid.uuid4())[:8]}",
            timestamp=datetime.now(timezone.utc),
            ticker=ticker,
            direction=direction,
            strategy=self.core_strategy,
            entry=price,
            stop=stop,
            target_1r=target_1r,
            target_2r=target_2r,
            risk_pct=self.risk_per_trade,
            confidence=confidence,
            regime=market_ctx.regime,
            gex_regime=market_ctx.gex_regime,
            rvol=indicators.rvol,
            time_window=market_ctx.time_window,
            bot_instance=self.instance,
            timeframe_layer="EARNINGS",
        )

        signal.qualification_log.append(
            f"EARNINGS: surprise={earnings_data.get('surprise_pct', 0):.1f}%, "
            f"RVOL={indicators.rvol:.1f}, entry_window={minutes_since_open:.0f}min"
        )

        self.logger.info(
            "EARNINGS SIGNAL: %s %s @ %.2f (surprise %.1f%%, conf %d)",
            direction.value, ticker, price,
            earnings_data.get("surprise_pct", 0), confidence,
        )

        self._daily_trades += 1
        return signal

    def scan_earnings(
        self,
        tickers: list[str],
        indicators: dict[str, IndicatorSnapshot],
        market_ctx: MarketContext,
        minutes_since_open: float,
    ) -> list[Signal]:
        """Scan all tickers for earnings trade opportunities."""
        if not self.active:
            return []

        signals = []
        for ticker in tickers:
            ind = indicators.get(ticker)
            if not ind:
                continue
            signal = self.generate_signal(ticker, ind, market_ctx, minutes_since_open)
            if signal:
                signals.append(signal)

        return signals

    def get_status(self) -> dict:
        """Get current earnings bot status."""
        return {
            "instance": self.instance.value,
            "active": self.active,
            "is_earnings_season": self.is_earnings_season(),
            "positions": len(self.open_positions),
            "max_positions": self.max_positions,
            "tickers_with_earnings": len(self._earnings_calendar),
            "daily_trades": self._daily_trades,
        }

    def reset_daily(self) -> None:
        """Reset daily counters."""
        self._daily_trades = 0
