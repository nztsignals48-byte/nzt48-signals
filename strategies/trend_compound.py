"""
NZT-48 Strategy S13 — Trend Compounding
=========================================
Price above 10-week EMA + ADX > 25.  Multi-day swing holds (1-5 days).

LONG conditions:
    - Price above 10-week EMA on daily timeframe (weekly trend intact)
    - ADX(14) > 25 confirming trend strength
    - Pullback to 20-day EMA and bouncing (entry on the dip)

SHORT conditions:
    - Price below 10-week EMA (weekly downtrend)
    - ADX(14) > 25 confirming trend strength
    - Bounce rejected at 20-day EMA (entry on the failed rally)

This is a SWING strategy: holds 1-5 days.
Used by BULL-BOT and SWING layer.
Stop: 1.5x ATR from entry (wider for multi-day)
Target: 2.0-3.0R with trailing stop.
Trail trigger: at +1.0R.
Entry on daily chart confirmation, not intraday.

Bot A maps to 3x ETPs for leveraged trend compounding in ISA.
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
    Direction,
    Bot,
    RegimeState,
    GEXRegime,
    TimeWindow,
)
import config


# ADX threshold confirming trend strength
_ADX_THRESHOLD = 25.0

# Pullback proximity: price must be within this multiple of ATR from EMA20
# to qualify as a "pullback touch" (wider than S1 since this is daily/swing)
_PULLBACK_ATR_MULT = 0.75

# Stop distance as multiple of ATR(14) — wider for multi-day holds
_STOP_ATR_MULT = 1.5

# Target R-multiples
_TARGET_MIN_R = 2.0
_TARGET_MAX_R = 3.0

# Trail trigger: move stop to breakeven at +1.0R
_TRAIL_TRIGGER_R = 1.0

# Minimum RVOL (lower than intraday strategies since this is daily)
_DEFAULT_RVOL_MIN = 0.8

# Regimes where trend compounding works
_BULLISH_REGIMES = {RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD}
_BEARISH_REGIMES = {RegimeState.TRENDING_DOWN_STRONG, RegimeState.TRENDING_DOWN_MOD}
_ALLOWED_REGIMES = _BULLISH_REGIMES | _BEARISH_REGIMES

# Time windows — this is a daily-timeframe strategy so any window works
# except Chaos Open (too early, no daily bar yet)
_BLOCKED_WINDOWS = {TimeWindow.CHAOS_OPEN}


class TrendCompoundStrategy(StrategyBase):
    """S13 — Trend Compounding.

    A swing strategy that identifies multi-day trend continuation setups
    using the 10-week EMA as the macro trend filter and the 20-day EMA
    as the pullback entry level.

    Designed for BULL-BOT and SWING layer.  Holds are 1-5 days with
    trailing stops triggered at +1.0R.

    For Bot A (ISA), these signals map to 3x leveraged ETPs, creating
    a compounding effect when the trend persists across multiple days.
    """

    def __init__(self) -> None:
        super().__init__(name="Trend Compounding", strategy_id="S13")

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
        """Scan tickers for swing trend-compounding entries.

        Identifies pullbacks to the 20-day EMA within a confirmed weekly
        trend (price above/below 10-week EMA + ADX > 25).

        Returns:
            list[Signal]: Signals for tickers with valid pullback entries.
        """
        if not self.enabled:
            return []

        # Regime gate — must be trending
        if market_ctx.regime not in _ALLOWED_REGIMES:
            self.logger.debug(
                "S13 skipped: regime %s not trending", market_ctx.regime
            )
            return []

        # Time window gate
        if market_ctx.time_window in _BLOCKED_WINDOWS:
            self.logger.debug(
                "S13 blocked: time window %s", market_ctx.time_window
            )
            return []

        signals: list[Signal] = []

        for ticker in tickers:
            snap = indicators.get(ticker)
            if snap is None:
                self.logger.debug("S13: no indicator data for %s", ticker)
                continue

            try:
                signal = self._evaluate_ticker(ticker, snap, market_ctx)
                if signal is not None:
                    signals.append(signal)
            except Exception:
                self.logger.exception("S13: error evaluating %s", ticker)

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
        """Evaluate a single ticker for a trend compounding entry.

        Returns Signal if all conditions met, otherwise None.
        """
        # --- Data quality checks ---
        if snap.price <= 0 or snap.atr14 <= 0:
            return None
        if snap.ema10w <= 0 or snap.ema20 <= 0:
            self.logger.debug("S13: %s missing EMA data", ticker)
            return None

        # --- ADX gate ---
        if snap.adx14 < _ADX_THRESHOLD:
            self.logger.debug(
                "S13: %s ADX %.1f < threshold %d",
                ticker,
                snap.adx14,
                _ADX_THRESHOLD,
            )
            return None

        # --- RVOL gate ---
        rvol_min = config.get_ticker_override(ticker, "rvol_min", _DEFAULT_RVOL_MIN)
        if snap.rvol < rvol_min:
            self.logger.debug(
                "S13: %s RVOL %.2f < min %.2f", ticker, snap.rvol, rvol_min
            )
            return None

        # --- Direction determination ---
        direction: str | None = None

        if market_ctx.regime in _BULLISH_REGIMES:
            direction = self._check_long(snap)
        elif market_ctx.regime in _BEARISH_REGIMES:
            direction = self._check_short(snap)

        if direction is None:
            return None

        # --- Compute entry, stop, targets ---
        entry = snap.price
        stop_mult = config.get_ticker_override(ticker, "stop_mult", _STOP_ATR_MULT)

        if direction == "LONG":
            stop = entry - (stop_mult * snap.atr14)
            risk = entry - stop
            target_1r = entry + (_TARGET_MIN_R * risk)
            target_2r = entry + (_TARGET_MAX_R * risk)
            trail = entry + (_TRAIL_TRIGGER_R * risk)  # Trail trigger level
        else:
            stop = entry + (stop_mult * snap.atr14)
            risk = stop - entry
            target_1r = entry - (_TARGET_MIN_R * risk)
            target_2r = entry - (_TARGET_MAX_R * risk)
            trail = entry - (_TRAIL_TRIGGER_R * risk)

        signal = self._create_signal(
            ticker=ticker,
            direction=direction,
            entry=round(entry, 2),
            stop=round(stop, 2),
            indicators=snap,
            market_ctx=market_ctx,
        )
        signal.target_1r = round(target_1r, 2)
        signal.target_2r = round(target_2r, 2)
        signal.trail = round(trail, 2)
        signal.timeframe_layer = "SWING"

        # Enrich patterns
        signal.patterns_detected = list(snap.patterns_detected) + [
            "TREND_COMPOUND",
            f"ADX:{snap.adx14:.1f}",
            f"ABOVE_10W_EMA" if direction == "LONG" else "BELOW_10W_EMA",
            f"PULLBACK_TO_EMA20",
        ]

        self.logger.info(
            "S13 SIGNAL: %s %s @ %.2f stop %.2f trail-trigger %.2f "
            "(ADX=%.1f, 10wEMA=%.2f, EMA20=%.2f, RVOL=%.2f)",
            direction,
            ticker,
            entry,
            stop,
            trail,
            snap.adx14,
            snap.ema10w,
            snap.ema20,
            snap.rvol,
        )
        return signal

    # ------------------------------------------------------------------
    # long / short check helpers
    # ------------------------------------------------------------------

    def _check_long(self, snap: IndicatorSnapshot) -> str | None:
        """Check bullish trend compounding conditions.

        Conditions:
            1. Price above 10-week EMA (weekly trend is up)
            2. Price has pulled back near the 20-day EMA (entry zone)
            3. Price is bouncing off the 20-day EMA (holding above it)
            4. EMA20 is above EMA50 (intermediate trend confirmation)

        Returns:
            "LONG" if all conditions met, otherwise None.
        """
        # 1. Price above 10-week EMA
        if snap.price <= snap.ema10w:
            return None

        # 2. Intermediate trend confirmation: EMA20 > EMA50
        if snap.ema50 > 0 and snap.ema20 <= snap.ema50:
            return None

        # 3. Pullback proximity to 20-day EMA
        pullback_zone = _PULLBACK_ATR_MULT * snap.atr14
        distance_to_ema20 = abs(snap.price - snap.ema20)

        if distance_to_ema20 > pullback_zone:
            return None

        # 4. Bouncing — price must be at or above EMA20
        if snap.price < snap.ema20:
            return None

        return "LONG"

    def _check_short(self, snap: IndicatorSnapshot) -> str | None:
        """Check bearish trend compounding conditions.

        Conditions:
            1. Price below 10-week EMA (weekly trend is down)
            2. Price has bounced up near the 20-day EMA (rejection zone)
            3. Price is rejecting at the 20-day EMA (failing below it)
            4. EMA20 is below EMA50 (intermediate trend confirmation)

        Returns:
            "SHORT" if all conditions met, otherwise None.
        """
        # 1. Price below 10-week EMA
        if snap.price >= snap.ema10w:
            return None

        # 2. Intermediate trend confirmation: EMA20 < EMA50
        if snap.ema50 > 0 and snap.ema20 >= snap.ema50:
            return None

        # 3. Bounce proximity to 20-day EMA
        pullback_zone = _PULLBACK_ATR_MULT * snap.atr14
        distance_to_ema20 = abs(snap.price - snap.ema20)

        if distance_to_ema20 > pullback_zone:
            return None

        # 4. Rejecting — price must be at or below EMA20
        if snap.price > snap.ema20:
            return None

        return "SHORT"
