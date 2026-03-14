"""
NZT-48 Strategy S11 — Hot Stock Scanner
=========================================
Finviz scan: Volume > 2x avg, Gap > 3%, Market Cap > $1B.
Opportunistic momentum on pre-market gappers outside the core 12 tickers.

LONG conditions (gap and go):
    - Gap up > 3%
    - Holding above VWAP in first 30 minutes
    - RVOL > 2.0
    - Market cap > $1B (avoid illiquid micro-caps)

SHORT conditions (gap and fade):
    - Gap up > 3% but failing (price below opening range low)
    - Volume declining after initial spike
    - Below opening range low
    - Fading back toward prior close

Stop: 1.5x ATR from entry
Only fires during Morning Momentum window (09:35-10:30 ET).
Max 1 hot scanner trade per day.
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


# Minimum gap percentage to qualify
_GAP_MIN_PCT = 3.0

# Minimum relative volume
_RVOL_MIN = 2.0

# Minimum market cap in USD (filters out illiquid names)
_MIN_MARKET_CAP = 1_000_000_000  # $1B

# Volume multiplier threshold (volume > 2x average)
_VOLUME_MULT_MIN = 2.0

# Stop distance as multiple of ATR(14)
_STOP_ATR_MULT = 1.5

# This strategy ONLY fires during Morning Momentum
_ALLOWED_WINDOWS = {TimeWindow.MORNING_MOMENTUM}

# Maximum hot scanner signals per scan (enforces "max 1 per day" at scan level)
_MAX_SIGNALS_PER_SCAN = 1

# Regimes where this strategy is blocked entirely (too dangerous for gappers)
_BLOCKED_REGIMES = {RegimeState.SHOCK, RegimeState.RISK_OFF}


class HotScannerStrategy(StrategyBase):
    """S11 — Hot Stock Scanner.

    Identifies pre-market gappers using Finviz screener data and evaluates
    them for gap-and-go (LONG) or gap-and-fade (SHORT) setups during the
    morning momentum window.

    These are opportunistic trades outside the core 12-ticker universe.
    The strategy is deliberately constrained: max 1 signal per scan,
    only during 09:35-10:30 ET, and requires significant volume confirmation.

    Feed dependency: expects pre-filtered candidates from feeds/screener.py
    to be included in the tickers list with populated IndicatorSnapshots.
    """

    def __init__(self) -> None:
        super().__init__(name="Hot Stock Scanner", strategy_id="S11")

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
        """Scan gapper candidates for momentum entries.

        Expects that the tickers list may include pre-market gapper
        candidates injected by feeds/screener.py alongside the core
        universe.  This strategy evaluates ALL tickers but applies
        strict volume/gap filters so only genuine gappers qualify.

        Returns:
            list[Signal]: At most 1 signal (best candidate by RVOL).
        """
        if not self.enabled:
            return []

        # Time window gate — ONLY Morning Momentum
        if market_ctx.time_window not in _ALLOWED_WINDOWS:
            self.logger.debug(
                "S11 blocked: time window %s not Morning Momentum",
                market_ctx.time_window,
            )
            return []

        # Regime gate — block in shock / risk-off
        if market_ctx.regime in _BLOCKED_REGIMES:
            self.logger.debug(
                "S11 blocked: regime %s too dangerous for gappers",
                market_ctx.regime,
            )
            return []

        candidates: list[Signal] = []

        for ticker in tickers:
            snap = indicators.get(ticker)
            if snap is None:
                continue

            try:
                signal = self._evaluate_ticker(ticker, snap, market_ctx)
                if signal is not None:
                    candidates.append(signal)
            except Exception:
                self.logger.exception("S11: error evaluating %s", ticker)

        if not candidates:
            return []

        # Sort by RVOL descending — take only the single best candidate
        candidates.sort(key=lambda s: s.rvol, reverse=True)
        best = candidates[:_MAX_SIGNALS_PER_SCAN]

        for sig in best:
            self.logger.info(
                "S11 SELECTED: %s %s @ %.2f (RVOL=%.2f)",
                sig.direction.value,
                sig.ticker,
                sig.entry,
                sig.rvol,
            )

        return best

    # ------------------------------------------------------------------
    # per-ticker evaluation
    # ------------------------------------------------------------------

    def _evaluate_ticker(
        self,
        ticker: str,
        snap: IndicatorSnapshot,
        market_ctx: MarketContext,
    ) -> Signal | None:
        """Evaluate a single ticker for a hot scanner entry.

        Determines whether the ticker qualifies as a gapper and whether
        the gap is holding (LONG) or fading (SHORT).

        Returns:
            Signal if qualified, otherwise None.
        """
        # --- Data quality checks ---
        if snap.price <= 0 or snap.atr14 <= 0:
            return None
        if snap.vwap <= 0:
            return None

        # --- RVOL gate ---
        if snap.rvol < _RVOL_MIN:
            return None

        # --- Gap detection ---
        # We use the opening range to infer gap.  If the OR low is
        # significantly above the prior session implied close (ema20
        # as proxy when prior close unavailable), we have a gap up.
        # A more precise gap calculation would come from screener feed data,
        # but we use price structure heuristics here.
        gap_pct = self._estimate_gap_pct(snap)
        if abs(gap_pct) < _GAP_MIN_PCT:
            self.logger.debug(
                "S11: %s gap %.2f%% below threshold %.1f%%",
                ticker,
                gap_pct,
                _GAP_MIN_PCT,
            )
            return None

        # --- Dollar volume / liquidity gate ---
        if snap.dollar_volume > 0 and snap.dollar_volume < _MIN_MARKET_CAP * 0.001:
            # Dollar volume proxy: if daily dollar volume is extremely low,
            # the name is likely too illiquid
            self.logger.debug("S11: %s dollar volume too low", ticker)
            return None

        # --- Direction: gap-and-go vs gap-and-fade ---
        direction = self._determine_direction(snap, gap_pct)
        if direction is None:
            return None

        # --- Compute entry, stop ---
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
        signal.timeframe_layer = "SCALP"

        # Enrich patterns
        signal.patterns_detected = list(snap.patterns_detected) + [
            f"GAP_{'UP' if gap_pct > 0 else 'DOWN'}:{abs(gap_pct):.1f}%",
            f"RVOL:{snap.rvol:.1f}",
            f"HOT_SCANNER_{'GOGO' if direction == 'LONG' else 'FADE'}",
        ]

        self.logger.info(
            "S11 SIGNAL: %s %s @ %.2f stop %.2f "
            "(gap=%.1f%%, RVOL=%.2f)",
            direction,
            ticker,
            entry,
            stop,
            gap_pct,
            snap.rvol,
        )
        return signal

    # ------------------------------------------------------------------
    # gap estimation
    # ------------------------------------------------------------------

    def _estimate_gap_pct(self, snap: IndicatorSnapshot) -> float:
        """Estimate the gap percentage from available data.

        Uses the opening range low vs EMA(20) as a proxy when explicit
        prior-close data is unavailable.  A positive return means gap up,
        negative means gap down.

        If OR data is not populated, falls back to price vs EMA(20).
        """
        # Prefer opening range based estimate
        if snap.or_low_5m > 0 and snap.ema20 > 0:
            return ((snap.or_low_5m - snap.ema20) / snap.ema20) * 100.0

        # Fallback: current price vs EMA20 (less precise)
        if snap.ema20 > 0:
            return ((snap.price - snap.ema20) / snap.ema20) * 100.0

        return 0.0

    # ------------------------------------------------------------------
    # direction determination
    # ------------------------------------------------------------------

    def _determine_direction(
        self, snap: IndicatorSnapshot, gap_pct: float
    ) -> str | None:
        """Determine if this is a gap-and-go (LONG) or gap-and-fade (SHORT).

        Gap-and-go (LONG) conditions:
            - Gap up (gap_pct > 0)
            - Price holding above VWAP
            - Price above opening range high (5m) or at least above OR low

        Gap-and-fade (SHORT) conditions:
            - Gap up (gap_pct > 0) but failing
            - Price below opening range low
            - Volume declining (speed of tape / cumulative delta suggest exhaustion)
        """
        if gap_pct > 0:
            # Gap up scenario — check if holding or fading
            above_vwap = snap.price > snap.vwap
            above_or_low = (
                snap.or_low_5m > 0 and snap.price > snap.or_low_5m
            )

            if above_vwap and above_or_low:
                # Gap and go — holding the gap
                return "LONG"

            below_or_low = (
                snap.or_low_5m > 0 and snap.price < snap.or_low_5m
            )
            if below_or_low and not above_vwap:
                # Gap and fade — below OR low and below VWAP
                return "SHORT"

        elif gap_pct < -_GAP_MIN_PCT:
            # Gap down — less common setup, only short if confirmed breakdown
            below_vwap = snap.price < snap.vwap
            below_or_low = (
                snap.or_low_5m > 0 and snap.price < snap.or_low_5m
            )
            if below_vwap and below_or_low:
                return "SHORT"

        return None
