"""
NZT-48 Timeframe Stacking — Scalp + Swing Layers
Phase 2: Independent timeframe layers that run simultaneously.
A scalp layer can fire while a swing position is open — different timeframes,
different risk budgets, different profit targets.
"""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    Signal, Direction, RegimeState,
    MarketContext, IndicatorSnapshot, TimeWindow, Position,
)
import config as cfg


class ScalpLayer:
    """Scalp layer: Ultra-short-term trades within the opening range.

    Rules:
    - Only active during MORNING_MOMENTUM and TREND_EXTENSION windows
    - Entry must be within 5-min OR range
    - Target: 0.3-0.5R (quick cash)
    - Max hold: 45 minutes
    - Stop: tight, below OR low (long) or above OR high (short)
    - Separate risk budget: 0.25% per scalp (1/3 of normal)
    - Max 3 scalps per session
    - Must have RVOL > 2.0
    - Regime: TRENDING_UP only (no scalps in chop)
    """

    ALLOWED_WINDOWS = [TimeWindow.MORNING_MOMENTUM, TimeWindow.TREND_EXTENSION]
    ALLOWED_REGIMES = [
        RegimeState.TRENDING_UP_STRONG,
        RegimeState.TRENDING_UP_MOD,
    ]

    def __init__(self):
        self.logger = logging.getLogger("nzt48.scalp_layer")
        self.risk_per_scalp = 0.0025  # 0.25% — 1/3 of normal
        self.target_r = 0.5           # Quick 0.5R target
        self.max_hold_minutes = 45
        self.max_scalps_per_session = 3
        self.min_rvol = 2.0
        self.scalps_today = 0
        self._active_scalps: list[Position] = []

    def can_scalp(self, market_ctx: MarketContext, indicators: IndicatorSnapshot) -> tuple[bool, str]:
        """Check if conditions allow a scalp entry."""
        if market_ctx.time_window not in self.ALLOWED_WINDOWS:
            return False, f"Wrong window: {market_ctx.time_window.value}"
        if market_ctx.regime not in self.ALLOWED_REGIMES:
            return False, f"Regime {market_ctx.regime.value} not suitable for scalps"
        if self.scalps_today >= self.max_scalps_per_session:
            return False, f"Max scalps ({self.max_scalps_per_session}) reached"
        if indicators.rvol < self.min_rvol:
            return False, f"RVOL {indicators.rvol:.1f} < min {self.min_rvol}"
        if indicators.or_high_5m == 0 or indicators.or_low_5m == 0:
            return False, "5-min OR not established"
        return True, "OK"

    def generate_scalp_signal(
        self,
        ticker: str,
        indicators: IndicatorSnapshot,
        market_ctx: MarketContext,
    ) -> Optional[Signal]:
        """Generate a scalp signal if conditions are met."""
        can, reason = self.can_scalp(market_ctx, indicators)
        if not can:
            return None

        price = indicators.price
        or_high = indicators.or_high_5m
        or_low = indicators.or_low_5m
        or_range = or_high - or_low

        if or_range <= 0:
            return None

        # Determine direction based on price position relative to OR
        signal = None

        # Long scalp: price breaks above OR high with volume
        if price > or_high and indicators.volume_spike:
            stop = or_low  # Stop below entire OR
            risk = price - stop
            if risk <= 0:
                return None
            target = price + (risk * self.target_r)

            signal = Signal(
                id=f"SCALP-{str(uuid.uuid4())[:8]}",
                timestamp=datetime.now(timezone.utc),
                ticker=ticker,
                direction=Direction.LONG,
                strategy="SCALP",
                entry=price,
                stop=stop,
                target_1r=target,
                target_2r=target,  # Scalps: single target
                risk_pct=self.risk_per_scalp,
                regime=market_ctx.regime,
                gex_regime=market_ctx.gex_regime,
                rvol=indicators.rvol,
                time_window=market_ctx.time_window,
                timeframe_layer="SCALP",
                confidence=70,  # Scalps start at 70 baseline
            )

        # Short scalp: price breaks below OR low with volume
        elif price < or_low and indicators.volume_spike:
            stop = or_high
            risk = stop - price
            if risk <= 0:
                return None
            target = price - (risk * self.target_r)

            signal = Signal(
                id=f"SCALP-{str(uuid.uuid4())[:8]}",
                timestamp=datetime.now(timezone.utc),
                ticker=ticker,
                direction=Direction.SHORT,
                strategy="SCALP",
                entry=price,
                stop=stop,
                target_1r=target,
                target_2r=target,
                risk_pct=self.risk_per_scalp,
                regime=market_ctx.regime,
                gex_regime=market_ctx.gex_regime,
                rvol=indicators.rvol,
                time_window=market_ctx.time_window,
                timeframe_layer="SCALP",
                confidence=70,
            )

        if signal:
            self.scalps_today += 1
            self.logger.info(
                "SCALP SIGNAL: %s %s @ %.2f stop %.2f target %.2f",
                signal.direction.value, ticker, price, signal.stop, signal.target_1r,
            )

        return signal

    def check_scalp_timeout(self, position: Position) -> bool:
        """Check if a scalp has exceeded max hold time."""
        elapsed = (datetime.now(timezone.utc) - position.entry_time).total_seconds() / 60
        if elapsed > self.max_hold_minutes:
            self.logger.warning(
                "SCALP TIMEOUT: %s held %.0f min > max %d",
                position.ticker, elapsed, self.max_hold_minutes,
            )
            return True
        return False

    def reset_daily(self) -> None:
        """Reset scalp counter for new session."""
        self.scalps_today = 0
        self._active_scalps.clear()


class SwingLayer:
    """Swing layer: Multi-day position trades.

    Rules:
    - Based on daily/4H chart signals
    - Entry on pullbacks to key levels (VWAP, EMA20, support)
    - Target: 2-5R (let winners run)
    - Max hold: 5-10 trading days
    - Stop: Below swing low / above swing high
    - Risk budget: 0.5% per swing (2/3 of normal)
    - Max 2 concurrent swings
    - Overnight holding allowed
    - Regime: Any trending regime (not RANGE_BOUND or SHOCK)
    - Requires EMA alignment on daily
    - Does NOT count toward PDT (held overnight)
    """

    ALLOWED_REGIMES = [
        RegimeState.TRENDING_UP_STRONG,
        RegimeState.TRENDING_UP_MOD,
        RegimeState.TRENDING_DOWN_STRONG,
        RegimeState.TRENDING_DOWN_MOD,
    ]

    def __init__(self):
        self.logger = logging.getLogger("nzt48.swing_layer")
        self.risk_per_swing = 0.005   # 0.5% per swing
        self.min_target_r = 2.0       # Minimum 2R target
        self.max_hold_days = 10
        self.max_concurrent = 2
        self.min_confidence = 75
        self.min_rvol = 1.5
        self._active_swings: list[Position] = []

    def can_swing(self, market_ctx: MarketContext, indicators: IndicatorSnapshot) -> tuple[bool, str]:
        """Check if conditions allow a swing entry."""
        if market_ctx.regime not in self.ALLOWED_REGIMES:
            return False, f"Regime {market_ctx.regime.value} not suitable for swings"
        if len(self._active_swings) >= self.max_concurrent:
            return False, f"Max concurrent swings ({self.max_concurrent}) reached"
        if indicators.rvol < self.min_rvol:
            return False, f"RVOL {indicators.rvol:.1f} < min {self.min_rvol}"
        # Check EMA alignment for trend confirmation
        if indicators.ema_alignment < 3:
            return False, f"EMA alignment {indicators.ema_alignment} < 3 (need daily trend)"
        return True, "OK"

    def generate_swing_signal(
        self,
        ticker: str,
        indicators: IndicatorSnapshot,
        market_ctx: MarketContext,
    ) -> Optional[Signal]:
        """Generate a swing signal on pullback to key level."""
        can, reason = self.can_swing(market_ctx, indicators)
        if not can:
            return None

        price = indicators.price
        atr = indicators.atr14

        if atr <= 0 or price <= 0:
            return None

        signal = None

        # Swing long: pullback to EMA20 in uptrend + bounce
        if market_ctx.regime in [RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD]:
            # Price near EMA20 (within 0.5 ATR) and bouncing
            ema_distance = abs(price - indicators.ema20) / atr
            if ema_distance < 0.5 and price > indicators.ema20:
                # Pullback bounce entry
                stop = indicators.ema50 - (atr * 0.5)  # Below EMA50
                risk = price - stop
                if risk <= 0:
                    return None

                target_1r = price + (risk * self.min_target_r)
                target_2r = price + (risk * 3.0)

                signal = Signal(
                    id=f"SWING-{str(uuid.uuid4())[:8]}",
                    timestamp=datetime.now(timezone.utc),
                    ticker=ticker,
                    direction=Direction.LONG,
                    strategy="SWING",
                    entry=price,
                    stop=stop,
                    target_1r=target_1r,
                    target_2r=target_2r,
                    risk_pct=self.risk_per_swing,
                    regime=market_ctx.regime,
                    gex_regime=market_ctx.gex_regime,
                    rvol=indicators.rvol,
                    time_window=market_ctx.time_window,
                    timeframe_layer="SWING",
                    confidence=75,
                )

        # Swing short: pullback to EMA20 in downtrend + rejection
        elif market_ctx.regime in [RegimeState.TRENDING_DOWN_STRONG, RegimeState.TRENDING_DOWN_MOD]:
            ema_distance = abs(price - indicators.ema20) / atr
            if ema_distance < 0.5 and price < indicators.ema20:
                stop = indicators.ema50 + (atr * 0.5)
                risk = stop - price
                if risk <= 0:
                    return None

                target_1r = price - (risk * self.min_target_r)
                target_2r = price - (risk * 3.0)

                signal = Signal(
                    id=f"SWING-{str(uuid.uuid4())[:8]}",
                    timestamp=datetime.now(timezone.utc),
                    ticker=ticker,
                    direction=Direction.SHORT,
                    strategy="SWING",
                    entry=price,
                    stop=stop,
                    target_1r=target_1r,
                    target_2r=target_2r,
                    risk_pct=self.risk_per_swing,
                    regime=market_ctx.regime,
                    gex_regime=market_ctx.gex_regime,
                    rvol=indicators.rvol,
                    time_window=market_ctx.time_window,
                    timeframe_layer="SWING",
                    confidence=75,
                )

        if signal:
            self.logger.info(
                "SWING SIGNAL: %s %s @ %.2f stop %.2f target1 %.2f target2 %.2f",
                signal.direction.value, ticker, price, signal.stop,
                signal.target_1r, signal.target_2r,
            )

        return signal

    def check_swing_expiry(self, position: Position) -> bool:
        """Check if a swing position has exceeded max hold time."""
        elapsed_days = (datetime.now(timezone.utc) - position.entry_time).days
        if elapsed_days > self.max_hold_days:
            self.logger.warning(
                "SWING EXPIRY: %s held %d days > max %d",
                position.ticker, elapsed_days, self.max_hold_days,
            )
            return True
        return False

    def reset_daily(self) -> None:
        """Daily reset — swings persist across days, just check expiry."""
        pass


class TimeframeStackingEngine:
    """Coordinates scalp and swing layers.

    Both layers can fire independently — a scalp can open while
    a swing position is active. Risk budgets are independent.
    """

    def __init__(self):
        self.logger = logging.getLogger("nzt48.timeframe_stacking")
        self.scalp = ScalpLayer()
        self.swing = SwingLayer()

    def scan_all_layers(
        self,
        tickers: list[str],
        indicators: dict[str, IndicatorSnapshot],
        market_ctx: MarketContext,
    ) -> list[Signal]:
        """Run both layers across all tickers."""
        signals = []

        for ticker in tickers:
            ind = indicators.get(ticker)
            if not ind:
                continue

            # Try scalp
            scalp_signal = self.scalp.generate_scalp_signal(ticker, ind, market_ctx)
            if scalp_signal:
                signals.append(scalp_signal)

            # Try swing
            swing_signal = self.swing.generate_swing_signal(ticker, ind, market_ctx)
            if swing_signal:
                signals.append(swing_signal)

        self.logger.info(
            "Timeframe stacking: %d scalp + %d swing signals",
            sum(1 for s in signals if s.timeframe_layer == "SCALP"),
            sum(1 for s in signals if s.timeframe_layer == "SWING"),
        )
        return signals

    def check_timeouts(self, positions: list[Position]) -> list[Position]:
        """Check all layer-tagged positions for timeouts."""
        expired = []
        for pos in positions:
            sig_id = getattr(pos, 'signal_id', '') or ''
            if 'SCALP' in sig_id:
                if self.scalp.check_scalp_timeout(pos):
                    expired.append(pos)
            elif 'SWING' in sig_id:
                if self.swing.check_swing_expiry(pos):
                    expired.append(pos)
        return expired

    def reset_daily(self) -> None:
        """Reset for new session."""
        self.scalp.reset_daily()
        self.swing.reset_daily()

    def get_layer_status(self) -> dict:
        """Get status of both layers."""
        return {
            "scalp": {
                "scalps_today": self.scalp.scalps_today,
                "max_per_session": self.scalp.max_scalps_per_session,
                "active_scalps": len(self.scalp._active_scalps),
            },
            "swing": {
                "active_swings": len(self.swing._active_swings),
                "max_concurrent": self.swing.max_concurrent,
            },
        }
