"""
NZT-48 Strategy S1 — Regime Trend Following
=============================================
Trigger logic: Price vs 50/200 EMA + ADX > 25. Entry on pullback in confirmed trend.

LONG conditions:
    - Price > EMA(50) (confirmed uptrend)
    - ADX(14) > 25 (trend has strength)
    - Pullback to EMA(9) or EMA(20) and bouncing (entry on dip)
    - EMA alignment bullish: EMA(9) > EMA(20) > EMA(50)

SHORT conditions:
    - Price < EMA(50) (confirmed downtrend)
    - ADX(14) > 25 (trend has strength)
    - Bounce to EMA(9) or EMA(20) and rejection (entry on rally)
    - EMA alignment bearish: EMA(9) < EMA(20) < EMA(50)

Stop: 1.5x ATR from entry
Target: 2.0-3.0R (BULL-BOT profile)

This is the primary trend-following strategy. It fires in TRENDING_UP or
TRENDING_DOWN regimes and avoids RANGE_BOUND, HIGH_VOLATILITY, RISK_OFF,
and SHOCK states unless overridden.
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


# Regimes where trend following is allowed
_BULLISH_REGIMES = {RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD}
_BEARISH_REGIMES = {RegimeState.TRENDING_DOWN_STRONG, RegimeState.TRENDING_DOWN_MOD}
_ALLOWED_REGIMES = _BULLISH_REGIMES | _BEARISH_REGIMES

# Time windows where no new entries are permitted
_BLOCKED_WINDOWS = {TimeWindow.CHAOS_OPEN, TimeWindow.CLOSE_MECHANICS}

# ADX threshold for confirming a trend
_ADX_THRESHOLD = 25.0

# Pullback proximity: price must be within this multiple of ATR from the
# target EMA to count as a "pullback touch"
_PULLBACK_ATR_MULT = 0.5

# Stop distance as a multiple of ATR(14)
_STOP_ATR_MULT = 1.5

# Default RVOL minimum (overridden per-ticker from config)
_DEFAULT_RVOL_MIN = 1.5


class RegimeTrendStrategy(StrategyBase):
    """S1 — Regime Trend Following.

    Identifies confirmed trends via ADX and EMA alignment, then enters on
    pullbacks to the fast or medium EMA.  This is a *patient* strategy:
    it waits for the pullback rather than chasing breakouts.

    Designed for the BULL-BOT (long bias) and BEAR-BOT (short bias) with
    wider stops (1.5x ATR) and 2.0-3.0R targets.
    """

    def __init__(self) -> None:
        super().__init__(name="Regime Trend Following", strategy_id="S1")

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
        """Scan all *tickers* for trend-following pullback entries.

        Returns:
            list[Signal]: Signals that pass all S1 filters.
        """
        if not self.enabled:
            return []

        # Block during forbidden time windows
        if market_ctx.time_window in _BLOCKED_WINDOWS:
            self.logger.debug("S1 blocked: time window %s", market_ctx.time_window)
            return []

        # Regime gate — only fire in trending regimes
        if market_ctx.regime not in _ALLOWED_REGIMES:
            self.logger.debug("S1 skipped: regime %s not trending", market_ctx.regime)
            return []

        signals: list[Signal] = []

        for ticker in tickers:
            snap = indicators.get(ticker)
            if snap is None:
                self.logger.debug("S1: no indicator data for %s", ticker)
                continue

            try:
                signal = self._evaluate_ticker(ticker, snap, market_ctx)
                if signal is not None:
                    signals.append(signal)
            except Exception:
                self.logger.exception("S1: error evaluating %s", ticker)

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
        """Evaluate a single ticker for an S1 entry.

        Returns a Signal if all conditions are met, otherwise None.
        """
        # --- Data quality checks ---
        if snap.price <= 0 or snap.atr14 <= 0:
            return None
        if snap.ema9 <= 0 or snap.ema20 <= 0 or snap.ema50 <= 0:
            return None

        # --- RVOL gate (per-ticker override) ---
        rvol_min = config.get_ticker_override(ticker, "rvol_min", _DEFAULT_RVOL_MIN)
        if snap.rvol < rvol_min:
            self.logger.debug(
                "S1: %s RVOL %.2f < min %.2f", ticker, snap.rvol, rvol_min,
            )
            return None

        # --- ADX gate ---
        if snap.adx14 < _ADX_THRESHOLD:
            self.logger.debug("S1: %s ADX %.1f < threshold", ticker, snap.adx14)
            return None

        # --- Direction determination based on regime + EMA alignment ---
        direction: str | None = None

        if market_ctx.regime in _BULLISH_REGIMES:
            direction = self._check_long(snap)
        elif market_ctx.regime in _BEARISH_REGIMES:
            direction = self._check_short(snap)

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
            "S1 SIGNAL: %s %s @ %.2f stop %.2f (ADX=%.1f, RVOL=%.2f)",
            direction, ticker, entry, stop, snap.adx14, snap.rvol,
        )
        return signal

    # ------------------------------------------------------------------
    # long / short check helpers
    # ------------------------------------------------------------------

    def _check_long(self, snap: IndicatorSnapshot) -> str | None:
        """Check bullish trend-following conditions.

        Conditions:
            1. Price > EMA(50) — confirmed uptrend
            2. EMA alignment bullish: EMA(9) > EMA(20) > EMA(50)
            3. Price has pulled back near EMA(9) or EMA(20) — within
               _PULLBACK_ATR_MULT * ATR of the EMA
            4. Price is bouncing (current bar close > EMA touched) — the
               fact that price is still above the EMA after the pullback
               confirms the bounce.

        Returns:
            "LONG" if all conditions are met, otherwise None.
        """
        # 1. Price above EMA50
        if snap.price <= snap.ema50:
            return None

        # 2. Bullish EMA alignment
        if not (snap.ema9 > snap.ema20 > snap.ema50):
            return None

        # 3. Pullback proximity to EMA9 or EMA20
        pullback_zone = _PULLBACK_ATR_MULT * snap.atr14
        near_ema9 = abs(snap.price - snap.ema9) <= pullback_zone
        near_ema20 = abs(snap.price - snap.ema20) <= pullback_zone

        if not (near_ema9 or near_ema20):
            return None

        # 4. Bouncing — price is at or above the EMA it pulled back to
        if near_ema9 and snap.price < snap.ema9:
            return None
        if near_ema20 and not near_ema9 and snap.price < snap.ema20:
            return None

        return "LONG"

    def _check_short(self, snap: IndicatorSnapshot) -> str | None:
        """Check bearish trend-following conditions.

        Conditions:
            1. Price < EMA(50) — confirmed downtrend
            2. EMA alignment bearish: EMA(9) < EMA(20) < EMA(50)
            3. Price has bounced up near EMA(9) or EMA(20) — within
               _PULLBACK_ATR_MULT * ATR of the EMA
            4. Price is rejecting (current bar close < EMA touched) — the
               fact that price is still below the EMA after the bounce
               confirms the rejection.

        Returns:
            "SHORT" if all conditions are met, otherwise None.
        """
        # 1. Price below EMA50
        if snap.price >= snap.ema50:
            return None

        # 2. Bearish EMA alignment
        if not (snap.ema9 < snap.ema20 < snap.ema50):
            return None

        # 3. Bounce proximity to EMA9 or EMA20
        pullback_zone = _PULLBACK_ATR_MULT * snap.atr14
        near_ema9 = abs(snap.price - snap.ema9) <= pullback_zone
        near_ema20 = abs(snap.price - snap.ema20) <= pullback_zone

        if not (near_ema9 or near_ema20):
            return None

        # 4. Rejecting — price is at or below the EMA it bounced into
        if near_ema9 and snap.price > snap.ema9:
            return None
        if near_ema20 and not near_ema9 and snap.price > snap.ema20:
            return None

        return "SHORT"
