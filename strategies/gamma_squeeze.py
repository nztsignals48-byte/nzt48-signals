"""
NZT-48 Strategy S14 — Gamma Squeeze
=====================================
Negative GEX + catalyst + breakout on volume.  Market maker forced flows.

When GEX is NEGATIVE, market makers are short gamma — they must buy into
rallies and sell into declines, AMPLIFYING the move rather than dampening it.
This creates explosive, self-reinforcing price action when combined with
a catalyst and volume.

LONG conditions:
    - GEX confirmed NEGATIVE
    - Bullish catalyst detected (earnings beat, upgrade, positive news)
    - Price breaking out above key level (OR high, prior day high, resistance)
    - Volume confirming (RVOL > 2.0)

SHORT conditions:
    - GEX confirmed NEGATIVE
    - Bearish catalyst detected (earnings miss, downgrade, negative news)
    - Price breaking down below key level
    - Volume confirming (RVOL > 2.0)

Stop: 1.0x ATR (tight — squeeze failures reverse fast)
Target: 2.5-4.0R (these can be massive moves)
Only fires when GEX is confirmed NEGATIVE.
BULL-BOT strategy primarily.
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


# GEX must be NEGATIVE for this strategy to fire
_REQUIRED_GEX = GEXRegime.NEGATIVE

# Minimum RVOL for confirmation of genuine volume surge
_RVOL_MIN = 2.0

# Stop distance — TIGHT because squeeze failures reverse violently
_STOP_ATR_MULT = 1.0

# Target R-multiples — wide because squeezes can be explosive
_TARGET_MIN_R = 2.5
_TARGET_MAX_R = 4.0

# Bullish catalyst sentiment values
_BULLISH_SENTIMENTS = {"positive"}
_BEARISH_SENTIMENTS = {"negative"}

# Bullish catalyst types that confirm the squeeze
_CATALYST_TYPES = {"earnings", "upgrade", "news", "macro", "downgrade"}

# Time windows where entries are blocked
_BLOCKED_WINDOWS = {TimeWindow.CHAOS_OPEN, TimeWindow.CLOSE_MECHANICS}

# Regimes where gamma squeeze is blocked (already in shock = too late)
_BLOCKED_REGIMES = {RegimeState.SHOCK}


class GammaSqueezeStrategy(StrategyBase):
    """S14 — Gamma Squeeze.

    Detects setups where negative gamma exposure (GEX) from market makers
    combines with a fundamental catalyst and a technical breakout to
    create explosive, self-reinforcing price moves.

    When market makers are short gamma, their delta-hedging activity
    AMPLIFIES directional moves: they must buy into rallies and sell
    into declines.  This is the opposite of the typical "market maker
    as dampener" regime seen under positive GEX.

    The strategy requires triple confirmation:
        1. Negative GEX (structural amplification)
        2. Catalyst (fundamental reason for the move)
        3. Breakout + volume (technical confirmation)

    Primarily a BULL-BOT strategy with tight stops (1.0x ATR) and
    wide targets (2.5-4.0R).
    """

    def __init__(self) -> None:
        super().__init__(name="Gamma Squeeze", strategy_id="S14")

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
        """Scan for gamma squeeze setups.

        The first gate is market-wide: GEX must be NEGATIVE.  If GEX is
        positive or flipping, no signals fire because the structural
        amplification mechanism is absent.

        Returns:
            list[Signal]: Signals for tickers with confirmed squeeze setups.
        """
        if not self.enabled:
            return []

        # --- GEX gate (market-wide) ---
        if market_ctx.gex_regime != _REQUIRED_GEX:
            self.logger.debug(
                "S14 skipped: GEX regime %s (need NEGATIVE)",
                market_ctx.gex_regime.value,
            )
            return []

        # Time window gate
        if market_ctx.time_window in _BLOCKED_WINDOWS:
            self.logger.debug(
                "S14 blocked: time window %s", market_ctx.time_window
            )
            return []

        # Regime gate
        if market_ctx.regime in _BLOCKED_REGIMES:
            self.logger.debug(
                "S14 blocked: regime %s — already in shock, too late",
                market_ctx.regime,
            )
            return []

        signals: list[Signal] = []

        for ticker in tickers:
            snap = indicators.get(ticker)
            if snap is None:
                continue

            narrative = narratives.get(ticker)

            try:
                signal = self._evaluate_ticker(
                    ticker, snap, market_ctx, narrative
                )
                if signal is not None:
                    signals.append(signal)
            except Exception:
                self.logger.exception("S14: error evaluating %s", ticker)

        return signals

    # ------------------------------------------------------------------
    # per-ticker evaluation
    # ------------------------------------------------------------------

    def _evaluate_ticker(
        self,
        ticker: str,
        snap: IndicatorSnapshot,
        market_ctx: MarketContext,
        narrative: NarrativeContext | None,
    ) -> Signal | None:
        """Evaluate a single ticker for a gamma squeeze entry.

        Triple confirmation required:
            1. Catalyst detected (from narrative context)
            2. Breakout above/below key level
            3. Volume confirmation (RVOL > 2.0)

        Returns:
            Signal if all conditions met, otherwise None.
        """
        # --- Data quality checks ---
        if snap.price <= 0 or snap.atr14 <= 0:
            return None

        # --- 1. Catalyst gate ---
        if narrative is None or not narrative.catalyst_detected:
            self.logger.debug("S14: %s no catalyst detected", ticker)
            return None

        if narrative.catalyst_type not in _CATALYST_TYPES:
            self.logger.debug(
                "S14: %s catalyst type '%s' not qualifying",
                ticker,
                narrative.catalyst_type,
            )
            return None

        # --- 2. RVOL gate ---
        if snap.rvol < _RVOL_MIN:
            self.logger.debug(
                "S14: %s RVOL %.2f < min %.1f",
                ticker,
                snap.rvol,
                _RVOL_MIN,
            )
            return None

        # --- 3. Direction from sentiment + breakout confirmation ---
        direction = self._determine_direction(snap, narrative)
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
        else:
            stop = entry + (stop_mult * snap.atr14)
            risk = stop - entry
            target_1r = entry - (_TARGET_MIN_R * risk)
            target_2r = entry - (_TARGET_MAX_R * risk)

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
        signal.timeframe_layer = "SCALP"

        # Enrich patterns with squeeze context
        breakout_level = self._get_breakout_level(snap, direction)
        signal.patterns_detected = list(snap.patterns_detected) + [
            "GAMMA_SQUEEZE",
            f"GEX:NEGATIVE",
            f"CATALYST:{narrative.catalyst_type.upper()}",
            f"RVOL:{snap.rvol:.1f}",
            f"BREAKOUT_LEVEL:{breakout_level:.2f}" if breakout_level else "BREAKOUT",
        ]

        self.logger.info(
            "S14 SIGNAL: %s %s @ %.2f stop %.2f "
            "target_1R=%.2f target_2R=%.2f "
            "(GEX=NEGATIVE, catalyst=%s, RVOL=%.2f)",
            direction,
            ticker,
            entry,
            stop,
            target_1r,
            target_2r,
            narrative.catalyst_type,
            snap.rvol,
        )
        return signal

    # ------------------------------------------------------------------
    # direction + breakout helpers
    # ------------------------------------------------------------------

    def _determine_direction(
        self, snap: IndicatorSnapshot, narrative: NarrativeContext
    ) -> str | None:
        """Determine direction from catalyst sentiment + price breakout.

        LONG: Bullish catalyst + price above a key resistance level.
        SHORT: Bearish catalyst + price below a key support level.

        Returns:
            "LONG", "SHORT", or None if conditions not met.
        """
        if narrative.sentiment in _BULLISH_SENTIMENTS:
            if self._is_bullish_breakout(snap):
                return "LONG"

        elif narrative.sentiment in _BEARISH_SENTIMENTS:
            if self._is_bearish_breakdown(snap):
                return "SHORT"

        return None

    def _is_bullish_breakout(self, snap: IndicatorSnapshot) -> bool:
        """Check if price is breaking out above a key resistance level.

        Key levels checked (in priority order):
            1. Opening range high (15-min) — intraday breakout
            2. Opening range high (5-min) — early breakout
            3. Bollinger Band upper — volatility breakout
            4. EMA9 (if price was below) — minor resistance reclaimed

        Returns True if price is above at least one key level.
        """
        # Check OR high breakout (strongest confirmation)
        if snap.or_high_15m > 0 and snap.price > snap.or_high_15m:
            return True

        if snap.or_high_5m > 0 and snap.price > snap.or_high_5m:
            return True

        # Bollinger Band upper breakout
        if snap.bb_upper > 0 and snap.price > snap.bb_upper:
            return True

        # EMA9 reclaim with price above VWAP (weaker but valid)
        if (
            snap.ema9 > 0
            and snap.vwap > 0
            and snap.price > snap.ema9
            and snap.price > snap.vwap
        ):
            return True

        return False

    def _is_bearish_breakdown(self, snap: IndicatorSnapshot) -> bool:
        """Check if price is breaking down below a key support level.

        Key levels checked:
            1. Opening range low (15-min) — intraday breakdown
            2. Opening range low (5-min) — early breakdown
            3. Bollinger Band lower — volatility breakdown
            4. EMA9 lost with price below VWAP

        Returns True if price is below at least one key level.
        """
        # Check OR low breakdown (strongest confirmation)
        if snap.or_low_15m > 0 and snap.price < snap.or_low_15m:
            return True

        if snap.or_low_5m > 0 and snap.price < snap.or_low_5m:
            return True

        # Bollinger Band lower breakdown
        if snap.bb_lower > 0 and snap.price < snap.bb_lower:
            return True

        # EMA9 lost with price below VWAP (weaker but valid)
        if (
            snap.ema9 > 0
            and snap.vwap > 0
            and snap.price < snap.ema9
            and snap.price < snap.vwap
        ):
            return True

        return False

    def _get_breakout_level(
        self, snap: IndicatorSnapshot, direction: str
    ) -> float | None:
        """Get the specific price level that was broken.

        Returns the most relevant breakout/breakdown level for logging
        and signal enrichment.
        """
        if direction == "LONG":
            if snap.or_high_15m > 0 and snap.price > snap.or_high_15m:
                return snap.or_high_15m
            if snap.or_high_5m > 0 and snap.price > snap.or_high_5m:
                return snap.or_high_5m
            if snap.bb_upper > 0 and snap.price > snap.bb_upper:
                return snap.bb_upper
        else:
            if snap.or_low_15m > 0 and snap.price < snap.or_low_15m:
                return snap.or_low_15m
            if snap.or_low_5m > 0 and snap.price < snap.or_low_5m:
                return snap.or_low_5m
            if snap.bb_lower > 0 and snap.price < snap.bb_lower:
                return snap.bb_lower
        return None
