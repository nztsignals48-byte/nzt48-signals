"""
NZT-48 Strategy S4 — Catalyst / Narrative
===========================================
Trigger logic: Headline keyword match + volume spike > 2x. News-driven moves.

Requirements:
    - News catalyst detected (from NewsFeed): earnings beat/miss, upgrade/
      downgrade, product launch, regulatory action, or macro event
    - Volume spike > 2x average (confirms market is reacting, not just noise)
    - Price moving in the catalyst direction (positive news = price up,
      negative news = price down)

LONG conditions:
    - Positive catalyst: upgrade, earnings beat, product launch
    - Volume spike confirmed (RVOL >= ticker-specific minimum)
    - Price above VWAP (buyers in control post-catalyst)

SHORT conditions:
    - Negative catalyst: downgrade, earnings miss, regulatory negative
    - Volume spike confirmed
    - Price below VWAP (sellers in control post-catalyst)

Stop: 1.5x ATR
Target: 1.5-2.0R

This strategy is unique because it relies on Layer 5 (Narrative) as the
PRIMARY trigger rather than pure price action.  The volume spike is the
critical confirmation — without institutional-level volume, the news is
noise.  Designed for the RANGE-BOT (quick scalps on catalyst pops) and
BULL-BOT (if catalyst aligns with trend).
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


# Time windows where no new entries are permitted
_BLOCKED_WINDOWS = {TimeWindow.CHAOS_OPEN, TimeWindow.CLOSE_MECHANICS}

# Regimes where catalyst trading is fully blocked
_BLOCKED_REGIMES = {RegimeState.SHOCK}

# Catalyst types classified as positive (LONG bias)
_POSITIVE_CATALYSTS = {
    "earnings_beat",
    "upgrade",
    "product_launch",
}

# Catalyst types classified as negative (SHORT bias)
_NEGATIVE_CATALYSTS = {
    "earnings_miss",
    "downgrade",
    "regulatory",
}

# Neutral / ambiguous catalyst types that require sentiment confirmation
_AMBIGUOUS_CATALYSTS = {
    "macro",
    "sector",
}

# Stop distance as a multiple of ATR(14)
_STOP_ATR_MULT = 1.5

# Default RVOL minimum — catalysts need even higher volume confirmation
_DEFAULT_RVOL_MIN = 1.5

# Volume spike multiplier: catalyst signals require stronger volume
_CATALYST_VOLUME_SPIKE_MIN = 2.0


class CatalystNarrativeStrategy(StrategyBase):
    """S4 — Catalyst / Narrative.

    Detects news-driven moves by combining headline catalyst classification
    with volume confirmation.  This is the only strategy where news is the
    primary trigger — all other strategies use news as a confidence layer.

    The key insight: news alone is not tradeable.  News + volume spike =
    institutions are reacting.  News + no volume = noise / already priced in.
    """

    def __init__(self) -> None:
        super().__init__(name="Catalyst Narrative", strategy_id="S4")

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
        """Scan all *tickers* for catalyst-driven setups.

        This strategy REQUIRES the *narratives* dict to be populated.
        Without narrative data, no signals are generated.

        Returns:
            list[Signal]: Signals that pass all S4 filters.
        """
        if not self.enabled:
            return []

        if market_ctx.time_window in _BLOCKED_WINDOWS:
            self.logger.debug("S4 blocked: time window %s", market_ctx.time_window)
            return []

        if market_ctx.regime in _BLOCKED_REGIMES:
            self.logger.debug("S4 blocked: regime %s", market_ctx.regime)
            return []

        if not narratives:
            self.logger.debug("S4 skipped: no narrative data available")
            return []

        signals: list[Signal] = []

        for ticker in tickers:
            snap = indicators.get(ticker)
            narrative = narratives.get(ticker)

            if snap is None:
                self.logger.debug("S4: no indicator data for %s", ticker)
                continue
            if narrative is None:
                self.logger.debug("S4: no narrative data for %s", ticker)
                continue

            try:
                signal = self._evaluate_ticker(
                    ticker, snap, narrative, market_ctx,
                )
                if signal is not None:
                    signals.append(signal)
            except Exception:
                self.logger.exception("S4: error evaluating %s", ticker)

        return signals

    # ------------------------------------------------------------------
    # per-ticker evaluation
    # ------------------------------------------------------------------

    def _evaluate_ticker(
        self,
        ticker: str,
        snap: IndicatorSnapshot,
        narrative: NarrativeContext,
        market_ctx: MarketContext,
    ) -> Signal | None:
        """Evaluate a single ticker for a catalyst-driven entry.

        Args:
            ticker: Equity symbol.
            snap: Indicator snapshot (1-min or 5-min).
            narrative: Narrative context from the NewsFeed.
            market_ctx: Current market context.

        Returns:
            Signal if all conditions are met, otherwise None.
        """
        # --- Data quality checks ---
        if snap.price <= 0 or snap.atr14 <= 0 or snap.vwap <= 0:
            return None

        # --- Catalyst detection gate ---
        if not narrative.catalyst_detected:
            return None

        # --- Crisis keyword veto ---
        if narrative.crisis_keyword:
            self.logger.warning(
                "S4: %s crisis keyword detected — VETO", ticker,
            )
            return None

        # --- RVOL gate (per-ticker override) ---
        rvol_min = config.get_ticker_override(ticker, "rvol_min", _DEFAULT_RVOL_MIN)
        if snap.rvol < rvol_min:
            self.logger.debug(
                "S4: %s RVOL %.2f < min %.2f", ticker, snap.rvol, rvol_min,
            )
            return None

        # --- Volume spike confirmation (must be >= 2x average) ---
        if not snap.volume_spike:
            self.logger.debug("S4: %s no volume spike", ticker)
            return None

        # Additional check: RVOL must be at least the catalyst minimum
        if snap.rvol < _CATALYST_VOLUME_SPIKE_MIN:
            self.logger.debug(
                "S4: %s RVOL %.2f below catalyst minimum %.1f",
                ticker, snap.rvol, _CATALYST_VOLUME_SPIKE_MIN,
            )
            return None

        # --- Determine direction from catalyst type + price confirmation ---
        direction = self._determine_direction(
            ticker, snap, narrative, market_ctx,
        )

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
            "S4 SIGNAL: %s %s @ %.2f stop %.2f "
            "(catalyst=%s, sentiment=%s, RVOL=%.2f, headline='%s')",
            direction, ticker, entry, stop,
            narrative.catalyst_type, narrative.sentiment,
            snap.rvol, narrative.headline[:80],
        )
        return signal

    # ------------------------------------------------------------------
    # direction determination
    # ------------------------------------------------------------------

    def _determine_direction(
        self,
        ticker: str,
        snap: IndicatorSnapshot,
        narrative: NarrativeContext,
        market_ctx: MarketContext,
    ) -> str | None:
        """Determine trade direction from catalyst type + price confirmation.

        The catalyst provides the directional bias; price relative to VWAP
        confirms that the market agrees with the bias.

        Args:
            ticker: Equity symbol.
            snap: Indicator snapshot.
            narrative: Narrative context.
            market_ctx: Market context.

        Returns:
            "LONG", "SHORT", or None if no confirmed direction.
        """
        cat_type = narrative.catalyst_type
        sentiment = narrative.sentiment

        # --- Positive catalyst → LONG if price above VWAP ---
        if cat_type in _POSITIVE_CATALYSTS:
            if snap.price > snap.vwap:
                # Regime filter: block longs in strong downtrend
                if market_ctx.regime == RegimeState.TRENDING_DOWN_STRONG:
                    self.logger.debug(
                        "S4: %s positive catalyst but TRENDING_DOWN_STRONG — blocked",
                        ticker,
                    )
                    return None
                return "LONG"
            else:
                self.logger.debug(
                    "S4: %s positive catalyst but price below VWAP — no confirm",
                    ticker,
                )
                return None

        # --- Negative catalyst → SHORT if price below VWAP ---
        if cat_type in _NEGATIVE_CATALYSTS:
            if snap.price < snap.vwap:
                # Regime filter: block shorts in strong uptrend
                if market_ctx.regime == RegimeState.TRENDING_UP_STRONG:
                    self.logger.debug(
                        "S4: %s negative catalyst but TRENDING_UP_STRONG — blocked",
                        ticker,
                    )
                    return None
                return "SHORT"
            else:
                self.logger.debug(
                    "S4: %s negative catalyst but price above VWAP — no confirm",
                    ticker,
                )
                return None

        # --- Ambiguous catalyst (macro, sector) → use sentiment + VWAP ---
        if cat_type in _AMBIGUOUS_CATALYSTS:
            if sentiment == "positive" and snap.price > snap.vwap:
                if market_ctx.regime != RegimeState.TRENDING_DOWN_STRONG:
                    return "LONG"
            elif sentiment == "negative" and snap.price < snap.vwap:
                if market_ctx.regime != RegimeState.TRENDING_UP_STRONG:
                    return "SHORT"
            self.logger.debug(
                "S4: %s ambiguous catalyst (type=%s, sentiment=%s) — no direction",
                ticker, cat_type, sentiment,
            )
            return None

        # --- Unknown catalyst type ---
        self.logger.debug(
            "S4: %s unknown catalyst type '%s'", ticker, cat_type,
        )
        return None
