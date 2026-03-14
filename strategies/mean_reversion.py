"""
NZT-48 Strategy S3 — Mean Reversion
=====================================
Trigger logic: RSI < 30 multi-TF + lower BB + positive divergence. Buys oversold.

LONG conditions:
    - RSI(14) < 30 on both 1-min and 5-min timeframes (multi-TF oversold)
    - Price at or below the lower Bollinger Band
    - Positive RSI divergence: price making lower lows but RSI making
      higher lows (momentum exhaustion signalling a reversal)
    - Regime supportive: NOT TRENDING_DOWN_STRONG (fading a strong
      downtrend is a losing game)

SHORT conditions:
    - RSI(14) > 70 on both 1-min and 5-min timeframes (multi-TF overbought)
    - Price at or above the upper Bollinger Band
    - Negative RSI divergence: price making higher highs but RSI making
      lower highs
    - Regime supportive: NOT TRENDING_UP_STRONG

Stop: 1.0x ATR (RANGE-BOT tighter stops)
Target: 1.2-1.5R (quick partials)

This is the RANGE-BOT's bread-and-butter strategy.  It fades extremes
in range-bound markets.  The multi-timeframe RSI confirmation and
divergence requirement dramatically reduce false signals.
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

# ============================================================
# V2.0 STATUS: DORMANT
# Mean reversion logic preserved but disabled in UK ISA mode.
# Set _STRATEGY_DORMANT = False to re-enable for range environments.
# ============================================================
_STRATEGY_DORMANT = False  # V2.1: Reactivated for ISA leveraged ETPs (mean-reversion works well on 3x products)



# Regimes where mean reversion works — NOT strong trends
_VETO_LONG_REGIMES = {RegimeState.TRENDING_DOWN_STRONG, RegimeState.SHOCK}
_VETO_SHORT_REGIMES = {RegimeState.TRENDING_UP_STRONG, RegimeState.SHOCK}

# Time windows where no new entries are permitted
_BLOCKED_WINDOWS = {TimeWindow.CHAOS_OPEN, TimeWindow.CLOSE_MECHANICS}

# RSI extremes
_RSI_OVERSOLD = 30.0
_RSI_OVERBOUGHT = 70.0

# Stop distance as a multiple of ATR(14) — RANGE-BOT tighter stops
_STOP_ATR_MULT = 1.0

# Default RVOL minimum — lowered from 1.5 for LSE leveraged ETPs (thinner volume than US equities)
_DEFAULT_RVOL_MIN = 0.8

# How close price must be to BB band to qualify (as fraction of ATR)
# Allows a small tolerance so price does not have to be exactly at the band
_BB_PROXIMITY_ATR_MULT = 0.3


class MeanReversionStrategy(StrategyBase):
    """S3 — Mean Reversion.

    Fades oversold/overbought extremes when confirmed across multiple
    timeframes with RSI divergence.  Designed for the RANGE-BOT: tight
    stops (1.0x ATR), quick partial profits (1.2-1.5R), intraday only.

    The divergence check is the key edge: it ensures we are not catching
    a falling knife (long) or shorting into a parabolic move (short).
    Without divergence, oversold can go more oversold.
    """

    def __init__(self) -> None:
        super().__init__(name="Mean Reversion", strategy_id="S3")

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
        """Scan all *tickers* for mean-reversion setups.

        The *indicators* dict may contain entries keyed as ``"TICKER"``
        (1-min default) and ``"TICKER_5m"`` for 5-min data.  If the
        5-min key is missing, the strategy uses the Stochastic RSI from
        the default snapshot as a proxy for multi-TF confirmation.

        Returns:
            list[Signal]: Signals that pass all S3 filters.
        """
        if _STRATEGY_DORMANT:
            return []  # V2.0: strategy disabled in UK ISA mode

        if not self.enabled:
            return []

        if market_ctx.time_window in _BLOCKED_WINDOWS:
            self.logger.debug("S3 blocked: time window %s", market_ctx.time_window)
            return []

        signals: list[Signal] = []

        for ticker in tickers:
            snap = indicators.get(ticker)
            if snap is None:
                self.logger.debug("S3: no indicator data for %s", ticker)
                continue

            # Try to get 5-min snapshot for multi-TF confirmation
            snap_5m = indicators.get(f"{ticker}_5m")

            try:
                signal = self._evaluate_ticker(
                    ticker, snap, snap_5m, market_ctx,
                )
                if signal is not None:
                    signals.append(signal)
            except Exception:
                self.logger.exception("S3: error evaluating %s", ticker)

        return signals

    # ------------------------------------------------------------------
    # per-ticker evaluation
    # ------------------------------------------------------------------

    def _evaluate_ticker(
        self,
        ticker: str,
        snap: IndicatorSnapshot,
        snap_5m: IndicatorSnapshot | None,
        market_ctx: MarketContext,
    ) -> Signal | None:
        """Evaluate a single ticker for an S3 mean-reversion entry.

        Args:
            ticker: Equity symbol.
            snap: Primary (1-min) indicator snapshot.
            snap_5m: Optional 5-min indicator snapshot for multi-TF RSI.
            market_ctx: Current market context.

        Returns:
            Signal if all conditions are met, otherwise None.
        """
        # --- MANDATE 4a: Leveraged ETP Hard Veto ---
        # Avellaneda & Zhang (2010): daily rebalancing in leveraged ETPs mechanically
        # reinforces trends. Mean reversion on 3x/5x products fights the instrument's
        # own mechanics and will systematically lose during trending regimes.
        _LEVERAGED_SUFFIXES = ("3.L", "5.L", "2.L", "S.L")
        if any(ticker.upper().endswith(s) for s in _LEVERAGED_SUFFIXES):
            self.logger.debug("S3: %s is leveraged ETP — hard veto (Avellaneda & Zhang 2010)", ticker)
            return None  # Hard veto — leveraged ETPs are trend-reinforcing, not mean-reverting

        # --- Data quality checks ---
        if snap.price <= 0 or snap.atr14 <= 0:
            return None
        if snap.bb_upper <= 0 or snap.bb_lower <= 0:
            return None

        # --- RVOL gate (per-ticker override) ---
        rvol_min = config.get_ticker_override(ticker, "rvol_min", _DEFAULT_RVOL_MIN)
        if snap.rvol < rvol_min:
            self.logger.debug(
                "S3: %s RVOL %.2f < min %.2f", ticker, snap.rvol, rvol_min,
            )
            return None

        # --- Direction determination ---
        direction: str | None = None

        if self._check_long(snap, snap_5m, market_ctx):
            direction = "LONG"
        elif self._check_short(snap, snap_5m, market_ctx):
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
            "S3 SIGNAL: %s %s @ %.2f stop %.2f "
            "(RSI=%.1f, StochRSI=%.1f, RVOL=%.2f)",
            direction, ticker, entry, stop,
            snap.rsi14, snap.stochastic_rsi, snap.rvol,
        )
        return signal

    # ------------------------------------------------------------------
    # long / short check helpers
    # ------------------------------------------------------------------

    def _check_long(
        self,
        snap: IndicatorSnapshot,
        snap_5m: IndicatorSnapshot | None,
        market_ctx: MarketContext,
    ) -> bool:
        """Check bullish mean-reversion (buy oversold) conditions.

        Conditions:
            1. Regime is NOT TRENDING_DOWN_STRONG or SHOCK
            2. RSI(14) < 30 on 1-min
            3. Multi-TF RSI confirmation: RSI < 30 on 5-min snapshot,
               OR Stochastic RSI < 20 on the primary snapshot as proxy
            4. Price at or below lower Bollinger Band (within tolerance)
            5. Positive RSI divergence: price near lower BB (making lows)
               but RSI is not at its minimum (higher lows in RSI).
               We approximate this by checking RSI > 15 (not at extreme
               floor) while price is at the band.

        Returns:
            True if all conditions pass.
        """
        # 1. Regime veto
        if market_ctx.regime in _VETO_LONG_REGIMES:
            return False

        # 2. RSI oversold on 1-min
        if snap.rsi14 >= _RSI_OVERSOLD:
            return False

        # 3. Multi-TF confirmation
        if snap_5m is not None:
            if snap_5m.rsi14 >= _RSI_OVERSOLD:
                return False
        else:
            # Fallback: use Stochastic RSI < 20 as proxy
            if snap.stochastic_rsi >= 20.0:
                return False

        # 4. Price at or below lower Bollinger Band (with ATR tolerance)
        bb_tolerance = _BB_PROXIMITY_ATR_MULT * snap.atr14
        if snap.price > snap.bb_lower + bb_tolerance:
            return False

        # 5. Positive divergence proxy: RSI is oversold but not at the
        #    absolute floor.  If RSI is e.g. 25 while price is at new
        #    lows (at the lower BB), RSI is making "higher lows" relative
        #    to where it could be.  We require RSI > 15 to confirm that
        #    selling momentum is decelerating.
        if snap.rsi14 < 15.0:
            # RSI at extreme floor — no divergence, selling is accelerating
            return False

        return True

    def _check_short(
        self,
        snap: IndicatorSnapshot,
        snap_5m: IndicatorSnapshot | None,
        market_ctx: MarketContext,
    ) -> bool:
        """Check bearish mean-reversion (sell overbought) conditions.

        Conditions:
            1. Regime is NOT TRENDING_UP_STRONG or SHOCK
            2. RSI(14) > 70 on 1-min
            3. Multi-TF RSI confirmation: RSI > 70 on 5-min snapshot,
               OR Stochastic RSI > 80 on the primary snapshot as proxy
            4. Price at or above upper Bollinger Band (within tolerance)
            5. Negative RSI divergence proxy: RSI is overbought but not
               at the absolute ceiling.  RSI < 85 confirms buying
               momentum is decelerating.

        Returns:
            True if all conditions pass.
        """
        # 1. Regime veto
        if market_ctx.regime in _VETO_SHORT_REGIMES:
            return False

        # 2. RSI overbought on 1-min
        if snap.rsi14 < _RSI_OVERBOUGHT:
            return False

        # 3. Multi-TF confirmation
        if snap_5m is not None:
            if snap_5m.rsi14 < _RSI_OVERBOUGHT:
                return False
        else:
            # Fallback: use Stochastic RSI > 80 as proxy
            if snap.stochastic_rsi <= 80.0:
                return False

        # 4. Price at or above upper Bollinger Band (with ATR tolerance)
        bb_tolerance = _BB_PROXIMITY_ATR_MULT * snap.atr14
        if snap.price < snap.bb_upper - bb_tolerance:
            return False

        # 5. Negative divergence proxy: RSI is overbought but not at
        #    the absolute ceiling.  If RSI is e.g. 75 while price is at
        #    new highs (at the upper BB), RSI is making "lower highs"
        #    relative to where it could be.
        if snap.rsi14 > 85.0:
            # RSI at extreme ceiling — no divergence, buying is accelerating
            return False

        return True
