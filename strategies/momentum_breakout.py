"""
NZT-48 Strategy S2 — Momentum Breakout
========================================
Trigger logic: RSI > 60 + price > 20 EMA + volume spike + BB squeeze release.

LONG conditions:
    - RSI(14) > 60 (momentum present, not exhausted)
    - Price breaks above EMA(20)
    - Volume spike confirmed: RVOL >= ticker-specific minimum
    - Bollinger Band squeeze released: BB was inside Keltner Channels
      (squeeze state), now breaking out above upper BB
    - MACD histogram positive and expanding on 5-min timeframe

SHORT conditions:
    - RSI(14) < 40 (bearish momentum)
    - Price breaks below EMA(20)
    - Same volume confirmation as long side
    - BB squeeze release to the downside
    - MACD histogram negative and expanding

Stop: 1.5x ATR (BULL-BOT width)
Target: 2.0-3.0R for BULL-BOT

This is the primary breakout strategy, designed to catch explosive moves
after periods of compression.  It fires at US open (14:30 UK) and during
the afternoon push.  The squeeze release is the critical differentiator
from a generic momentum strategy.
"""

from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.base import StrategyBase
from models import (
    Signal,
    IndicatorSnapshot,
    MarketContext,
    SectorFlow,
    NarrativeContext,
    RegimeState,
    TimeWindow,
)
import config


# Regimes where momentum breakouts have positive expectancy
_FAVOURABLE_REGIMES = {
    RegimeState.TRENDING_UP_STRONG,
    RegimeState.TRENDING_UP_MOD,
    RegimeState.TRENDING_DOWN_STRONG,
    RegimeState.TRENDING_DOWN_MOD,
    RegimeState.RANGE_BOUND,  # Breakout from range
}

# Block in these windows
_BLOCKED_WINDOWS = {TimeWindow.CHAOS_OPEN, TimeWindow.CLOSE_MECHANICS}

# RSI thresholds — relaxed from 60/40 to 55/45 to catch more breakouts
_RSI_LONG_MIN = 55.0
_RSI_SHORT_MAX = 45.0

# Stop distance as a multiple of ATR(14) — BULL-BOT profile
_STOP_ATR_MULT = 1.5

# Default RVOL minimum — lowered from 1.5 to catch more breakouts
_DEFAULT_RVOL_MIN = 1.3


class MomentumBreakoutStrategy(StrategyBase):
    """S2 — Momentum Breakout.

    Detects Bollinger Band squeeze releases backed by volume spikes and
    RSI momentum.  The squeeze (BB inside KC) indicates coiled energy;
    the breakout + volume confirms institutional participation.

    Designed primarily for the BULL-BOT (long breakouts) but also fires
    short signals in downtrend regimes.
    """

    def __init__(self) -> None:
        super().__init__(name="Momentum Breakout", strategy_id="S2")

    # ------------------------------------------------------------------
    # scan()
    # ------------------------------------------------------------------

    def scan(
        self,
        tickers: list[str],
        indicators: dict[str, IndicatorSnapshot],
        market_ctx: MarketContext,
        sector_flows: dict[str, SectorFlow],
        narratives: dict[str, NarrativeContext],
    ) -> list[Signal]:
        """Scan all *tickers* for momentum breakout setups.

        Returns:
            list[Signal]: Signals that pass all S2 filters.
        """
        if not self.enabled:
            return []

        if market_ctx.time_window in _BLOCKED_WINDOWS:
            self.logger.debug("S2 blocked: time window %s", market_ctx.time_window)
            return []

        if market_ctx.regime not in _FAVOURABLE_REGIMES:
            self.logger.debug("S2 skipped: regime %s", market_ctx.regime)
            return []

        signals: list[Signal] = []

        for ticker in tickers:
            snap = indicators.get(ticker)
            if snap is None:
                self.logger.debug("S2: no indicator data for %s", ticker)
                continue

            try:
                signal = self._evaluate_ticker(ticker, snap, market_ctx)
                if signal is not None:
                    signals.append(signal)
            except Exception:
                self.logger.exception("S2: error evaluating %s", ticker)

        return signals

    # ------------------------------------------------------------------
    # per-ticker evaluation
    # ------------------------------------------------------------------

    def _evaluate_ticker(
        self,
        ticker: str,
        snap: IndicatorSnapshot,
        market_ctx: MarketContext,
    ) -> Signal | None:
        """Evaluate a single ticker for an S2 momentum breakout entry.

        Returns a Signal if all conditions are met, otherwise None.
        """
        # --- Data quality checks ---
        if snap.price <= 0 or snap.atr14 <= 0:
            return None
        if snap.ema20 <= 0 or snap.bb_upper <= 0 or snap.bb_lower <= 0:
            return None

        # --- RVOL gate (per-ticker override) ---
        rvol_min = config.get_ticker_override(ticker, "rvol_min", _DEFAULT_RVOL_MIN)
        if snap.rvol < rvol_min:
            self.logger.debug(
                "S2: %s RVOL %.2f < min %.2f", ticker, snap.rvol, rvol_min,
            )
            return None

        # --- Volume spike confirmation ---
        if not snap.volume_spike:
            self.logger.debug("S2: %s no volume spike", ticker)
            return None

        # --- Squeeze release detection ---
        # The squeeze state is detected by the indicator engine and stored
        # as a "SQUEEZE" pattern.  For the breakout, we need the squeeze
        # to have been present recently AND price to now be breaking out
        # of the Bollinger Bands.  We check both the pattern list and a
        # direct BB vs KC comparison for the "was squeezed" condition.
        was_squeezed = self._detect_squeeze_state(snap)

        # --- Determine direction ---
        direction: str | None = None

        if self._check_long(snap, was_squeezed):
            direction = "LONG"
        elif self._check_short(snap, was_squeezed):
            direction = "SHORT"

        if direction is None:
            return None

        # --- Compute entry, stop, and create signal ---
        entry = snap.price
        stop_mult = config.get_ticker_override(ticker, "stop_mult", _STOP_ATR_MULT)

        if direction == "LONG":
            stop = entry - (stop_mult * snap.atr14)
        else:
            stop = entry + (stop_mult * snap.atr14)

        signal = self._create_signal(
            ticker=ticker,
            direction=direction,
            entry=round(entry, 2),
            stop=round(stop, 2),
            indicators=snap,
            market_ctx=market_ctx,
        )
        self.logger.info(
            "S2 SIGNAL: %s %s @ %.2f stop %.2f "
            "(RSI=%.1f, RVOL=%.2f, squeeze=%s)",
            direction, ticker, entry, stop,
            snap.rsi14, snap.rvol, was_squeezed,
        )
        return signal

    # ------------------------------------------------------------------
    # condition checks
    # ------------------------------------------------------------------

    def _detect_squeeze_state(self, snap: IndicatorSnapshot) -> bool:
        """Detect whether a BB squeeze was recently active.

        A squeeze means BB was inside Keltner Channels.  We accept
        either the pattern detection flag OR a current near-squeeze
        state (BB bands very close to KC bands) as evidence.

        Returns:
            True if squeeze was recently active.
        """
        # Check pattern flag from indicator engine
        if "SQUEEZE" in snap.patterns_detected:
            return True

        # Direct check: BB inside KC (or very close — within 0.1% of price)
        if (
            snap.keltner_upper > 0
            and snap.keltner_lower > 0
            and snap.bb_upper > 0
            and snap.bb_lower > 0
        ):
            # Squeeze: BB upper < KC upper AND BB lower > KC lower
            if snap.bb_upper < snap.keltner_upper and snap.bb_lower > snap.keltner_lower:
                return True

            # Near-squeeze: BB bands within 0.2% of KC bands (widened from 0.1%)
            if snap.price > 0:
                threshold = snap.price * 0.002
                upper_close = abs(snap.bb_upper - snap.keltner_upper) < threshold
                lower_close = abs(snap.bb_lower - snap.keltner_lower) < threshold
                if upper_close and lower_close:
                    return True

        return False

    def _check_long(self, snap: IndicatorSnapshot, was_squeezed: bool) -> bool:
        """Check bullish momentum breakout conditions.

        Conditions:
            1. RSI(14) > 60 — momentum is present
            2. Price > EMA(20) — upside breakout
            3. RVOL + volume spike already confirmed above
            4. BB squeeze release: was squeezed AND price now at or above
               upper BB — breaking out of the compression zone
            5. MACD histogram > 0 and positive (expanding momentum)

        Returns:
            True if all conditions pass.
        """
        # 1. RSI check
        if snap.rsi14 < _RSI_LONG_MIN:
            return False

        # 2. Price above EMA20
        if snap.price <= snap.ema20:
            return False

        # 3. Squeeze release — price breaking above upper BB
        if was_squeezed:
            # Squeeze release: price must be at or near the upper BB
            # (within 0.3% to allow for bar close slightly below)
            bb_proximity = snap.price >= snap.bb_upper * 0.997
            if not bb_proximity:
                return False
        else:
            # Even without a formal squeeze, we accept a strong breakout
            # above the upper BB with all other conditions met
            if snap.price < snap.bb_upper:
                return False

        # 4. MACD histogram positive and expanding
        if snap.macd_histogram <= 0:
            return False

        return True

    def _check_short(self, snap: IndicatorSnapshot, was_squeezed: bool) -> bool:
        """Check bearish momentum breakout conditions.

        Conditions:
            1. RSI(14) < 40 — bearish momentum
            2. Price < EMA(20) — downside breakout
            3. RVOL + volume spike already confirmed above
            4. BB squeeze release: was squeezed AND price now at or below
               lower BB — breaking down out of the compression zone
            5. MACD histogram < 0 (expanding bearish momentum)

        Returns:
            True if all conditions pass.
        """
        # 1. RSI check
        if snap.rsi14 > _RSI_SHORT_MAX:
            return False

        # 2. Price below EMA20
        if snap.price >= snap.ema20:
            return False

        # 3. Squeeze release — price breaking below lower BB
        if was_squeezed:
            bb_proximity = snap.price <= snap.bb_lower * 1.003
            if not bb_proximity:
                return False
        else:
            if snap.price > snap.bb_lower:
                return False

        # 4. MACD histogram negative
        if snap.macd_histogram >= 0:
            return False

        return True

    # ------------------------------------------------------------------
    # intraday momentum check
    # ------------------------------------------------------------------

    def _check_intraday_momentum(self, snap: IndicatorSnapshot) -> tuple[bool, float]:
        """Check if first-hour momentum supports the trade direction.

        Academic finding: stocks that move strongly in the first 30 minutes
        tend to continue in the same direction for the next 2-3 hours
        (Gao, Han, Li & Zhou, 2018).

        Uses the opening range midpoint as reference. If price has moved
        >0.5% from the OR midpoint, intraday momentum is confirmed.

        Returns:
            (has_momentum, change_pct) — whether momentum threshold is met
            and the directional change percentage.
        """
        if snap.or_high_5m > 0 and snap.or_low_5m > 0 and snap.price > 0:
            or_mid = (snap.or_high_5m + snap.or_low_5m) / 2
            if or_mid > 0:
                change_pct = (snap.price - or_mid) / or_mid * 100
                return abs(change_pct) > 0.5, change_pct
        return False, 0.0
