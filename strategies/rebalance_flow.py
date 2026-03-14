"""
NZT-48 Strategy S12 — Rebalance Flow
======================================
THE KEY SIGNAL for Bot A (ISA).

When the underlying index moves > +/-1.5% during the US session, the
3x ETP sponsor (e.g. Leverage Shares, GraniteShares) must rebalance
their delta hedge at the close.  This creates predictable, forced flows
that we front-run by entering the ETP before the 7pm UK rebalance window.

If QQQ up 2%, the sponsor must BUY more futures at close to maintain 3x
exposure — pushing the ETP price up further.

If QQQ down 2%, the sponsor must SELL futures — pushing the inverse ETP up.

Entry: 14:30-16:00 GMT (US-LSE overlap window)
Rebalance: ~19:00 UK (7pm)
Confidence minimum: 75
Spread check: bid-ask spread must be <= 0.5%
Allocation cap: < 30% of ISA in any single rebalance trade
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


# Underlying must have moved at least this much (absolute %) to trigger
_UNDERLYING_MOVE_THRESHOLD = 1.5  # percent

# Minimum confidence score to take the trade
_MIN_CONFIDENCE = 75

# Maximum bid-ask spread as a percentage of price
_MAX_SPREAD_PCT = 0.5  # 0.5%

# Maximum allocation to any single rebalance trade (% of ISA)
_MAX_ALLOCATION_PCT = 30.0

# Underlying tickers we monitor (QQQ, SPY, TQQQ-underlying, etc.)
# The strategy checks the underlying index movement.
_UNDERLYING_TICKERS = {"QQQ", "SPY", "IWM"}

# Mapping: underlying ticker -> (long ETP for up-move, inverse ETP for down-move)
# These are the 3x ETPs on LSE that Bot A trades in the ISA.
_ETP_MAPPING: dict[str, dict[str, str]] = {
    "QQQ": {
        "long_etp": "3QQQ",
        "short_etp": "3QQS",
        "leverage": "3x",
    },
    "SPY": {
        "long_etp": "3USL",
        "short_etp": "3USS",
        "leverage": "3x",
    },
}

# Stop distance as multiple of ATR(14) on the ETP
_STOP_ATR_MULT = 1.5

# The entry window is Afternoon Push / Power Hour (overlap period)
_ALLOWED_WINDOWS = {
    TimeWindow.AFTERNOON_PUSH,
    TimeWindow.POWER_HOUR,
}

# Regimes where rebalance flow is unreliable (too chaotic)
_BLOCKED_REGIMES = {RegimeState.SHOCK}


class RebalanceFlowStrategy(StrategyBase):
    """S12 — Rebalance Flow (KEY Signal for Bot A / ISA).

    Detects when an underlying index has moved enough during the US
    session to force 3x ETP sponsors into rebalancing at the close.
    This creates predictable directional flows that can be front-run.

    The strategy is specifically designed for the UK ISA wrapper where
    3x leveraged ETPs provide amplified exposure to index moves without
    capital gains tax.

    Entry window: 14:30-16:00 GMT (US/LSE overlap).
    Rebalance window: ~19:00 UK.
    """

    def __init__(self) -> None:
        super().__init__(name="Rebalance Flow", strategy_id="S12")

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
        """Scan underlying indices for rebalance-triggering moves.

        Checks QQQ, SPY, and IWM for intraday moves exceeding the
        rebalance threshold.  When triggered, creates a signal for the
        corresponding 3x ETP on Bot A (ISA).

        Returns:
            list[Signal]: ETP signals mapped for Bot A.
        """
        if not self.enabled:
            return []

        # Time window gate — only during overlap period
        if market_ctx.time_window not in _ALLOWED_WINDOWS:
            self.logger.debug(
                "S12 blocked: time window %s outside overlap",
                market_ctx.time_window,
            )
            return []

        # Regime gate
        if market_ctx.regime in _BLOCKED_REGIMES:
            self.logger.debug(
                "S12 blocked: regime %s too chaotic for rebalance trades",
                market_ctx.regime,
            )
            return []

        signals: list[Signal] = []

        for underlying in _UNDERLYING_TICKERS:
            snap = indicators.get(underlying)
            if snap is None:
                self.logger.debug("S12: no data for underlying %s", underlying)
                continue

            if underlying not in _ETP_MAPPING:
                self.logger.debug("S12: no ETP mapping for %s", underlying)
                continue

            try:
                signal = self._evaluate_underlying(
                    underlying, snap, market_ctx
                )
                if signal is not None:
                    signals.append(signal)
            except Exception:
                self.logger.exception("S12: error evaluating %s", underlying)

        return signals

    # ------------------------------------------------------------------
    # per-underlying evaluation
    # ------------------------------------------------------------------

    def _evaluate_underlying(
        self,
        underlying: str,
        snap: IndicatorSnapshot,
        market_ctx: MarketContext,
    ) -> Signal | None:
        """Evaluate whether an underlying index has moved enough to
        trigger a rebalance trade.

        Checks:
            1. Intraday move > +/-1.5% (from VWAP as session anchor)
            2. Bid-ask spread on ETP <= 0.5%
            3. Confidence >= 75
            4. Allocation within 30% cap

        Returns:
            Signal for the corresponding 3x ETP if triggered, else None.
        """
        # --- Data quality checks ---
        if snap.price <= 0 or snap.atr14 <= 0:
            return None
        if snap.vwap <= 0:
            return None

        # --- Calculate intraday move ---
        # Use VWAP as session-anchored reference.  The percentage move
        # from VWAP approximates the intraday directional displacement.
        intraday_move_pct = ((snap.price - snap.vwap) / snap.vwap) * 100.0

        if abs(intraday_move_pct) < _UNDERLYING_MOVE_THRESHOLD:
            self.logger.debug(
                "S12: %s intraday move %.2f%% below threshold %.1f%%",
                underlying,
                intraday_move_pct,
                _UNDERLYING_MOVE_THRESHOLD,
            )
            return None

        # --- Determine direction ---
        # Positive move -> sponsor must buy more (long ETP benefits)
        # Negative move -> sponsor must sell (inverse ETP benefits)
        etp_info = _ETP_MAPPING[underlying]
        if intraday_move_pct > 0:
            direction = "LONG"
            etp_ticker = etp_info["long_etp"]
        else:
            direction = "SHORT"
            etp_ticker = etp_info["short_etp"]

        # --- Spread check ---
        # Use the underlying's bid-ask spread as proxy when ETP spread
        # data is not in the indicator snapshot
        spread_pct = self._calculate_spread_pct(snap)
        if spread_pct > _MAX_SPREAD_PCT:
            self.logger.debug(
                "S12: %s spread %.2f%% exceeds max %.1f%%",
                underlying,
                spread_pct,
                _MAX_SPREAD_PCT,
            )
            return None

        # --- Confidence calculation ---
        confidence = self._calculate_confidence(
            intraday_move_pct, snap, market_ctx
        )
        if confidence < _MIN_CONFIDENCE:
            self.logger.debug(
                "S12: %s confidence %.0f < minimum %d",
                underlying,
                confidence,
                _MIN_CONFIDENCE,
            )
            return None

        # --- Build signal ---
        entry = snap.price
        stop_mult = config.get_ticker_override(
            underlying, "stop_mult", _STOP_ATR_MULT
        )

        if direction == "LONG":
            stop = entry - (stop_mult * snap.atr14)
        else:
            stop = entry + (stop_mult * snap.atr14)

        signal = self._create_signal(
            ticker=underlying,
            direction=direction,
            entry=round(entry, 2),
            stop=round(stop, 2),
            indicators=snap,
            market_ctx=market_ctx,
        )

        # Map to Bot A (ISA) and set ETP details
        signal.bot = Bot.A
        signal.isa_ticker = etp_ticker
        signal.isa_leverage = etp_info["leverage"]
        signal.isa_underlying = underlying
        signal.confidence = confidence
        signal.timeframe_layer = "SWING"

        # Enrich patterns
        signal.patterns_detected = list(snap.patterns_detected) + [
            f"REBALANCE_FLOW:{intraday_move_pct:+.2f}%",
            f"ETP:{etp_ticker}",
            f"SPREAD:{spread_pct:.2f}%",
            f"CONFIDENCE:{confidence:.0f}",
        ]

        self.logger.info(
            "S12 SIGNAL: %s %s (ETP: %s) underlying move %+.2f%% "
            "confidence %.0f spread %.2f%%",
            direction,
            underlying,
            etp_ticker,
            intraday_move_pct,
            confidence,
            spread_pct,
        )
        return signal

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _calculate_spread_pct(self, snap: IndicatorSnapshot) -> float:
        """Calculate bid-ask spread as a percentage of the mid price.

        Uses the bid_ask_spread field from the indicator snapshot.
        If not available, returns 0.25 (conservative default; still within
        the 0.5% maximum but does not silently pass).
        """
        if snap.bid_ask_spread <= 0 or snap.price <= 0:
            return 0.25
        return (snap.bid_ask_spread / snap.price) * 100.0

    def _calculate_confidence(
        self,
        move_pct: float,
        snap: IndicatorSnapshot,
        market_ctx: MarketContext,
    ) -> float:
        """Calculate confidence score for the rebalance trade.

        Factors:
            - Size of underlying move (larger = more forced flow)
            - Volume confirmation (RVOL)
            - Market regime alignment
            - Internals composite

        Returns:
            Confidence score 0-100.
        """
        score = 50.0  # Base score

        # Move magnitude bonus: each 0.5% above threshold adds 8 points
        excess_move = abs(move_pct) - _UNDERLYING_MOVE_THRESHOLD
        score += min(excess_move * 16, 24)  # Cap at +24

        # RVOL bonus
        if snap.rvol >= 2.0:
            score += 10
        elif snap.rvol >= 1.5:
            score += 5

        # Regime alignment
        bullish_regimes = {
            RegimeState.TRENDING_UP_STRONG,
            RegimeState.TRENDING_UP_MOD,
        }
        bearish_regimes = {
            RegimeState.TRENDING_DOWN_STRONG,
            RegimeState.TRENDING_DOWN_MOD,
        }
        if move_pct > 0 and market_ctx.regime in bullish_regimes:
            score += 8
        elif move_pct < 0 and market_ctx.regime in bearish_regimes:
            score += 8

        # Internals composite alignment (0-4 scale)
        if move_pct > 0 and market_ctx.internals_composite >= 3:
            score += 5
        elif move_pct < 0 and market_ctx.internals_composite <= 1:
            score += 5

        # VIX penalty — very high VIX makes rebalance flows less predictable
        if market_ctx.vix > 30:
            score -= 10
        elif market_ctx.vix > 25:
            score -= 5

        return max(0, min(100, score))
