"""
NZT-48 Strategy S10 — AI Thematic
===================================
SOXX/SMH/BOTZ relative strength vs SPY + price above 10-week EMA.
AI-specific momentum within the semiconductor / AI inference theme.

LONG conditions:
    - Sector ETF (SOXX or SMH) RS vs SPY > 1.05 (AI sector leading)
    - Price above 10-week EMA on the individual ticker (trend intact)
    - Individual ticker showing relative strength within the sector
      (ticker RS vs SPY > sector ETF RS — outperforming even the hot sector)

Focus tickers: NVDA, AVGO, MRVL, ARM (custom ASIC / AI inference plays)

Stop: 1.5x ATR from entry
Target: 2.0-3.0R (trend following within AI theme)

Works best in TRENDING_UP regime.
Pre-market scan at 06:00 UK.
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


# AI / semiconductor focus tickers
_AI_TICKERS = {"NVDA", "AVGO", "MRVL", "ARM"}

# Sector ETFs we monitor for AI theme strength
_AI_SECTOR_ETFS = {"SOXX", "SMH", "BOTZ"}

# Sector ETF RS threshold vs SPY — sector must be leading
_SECTOR_RS_THRESHOLD = 1.05

# Individual ticker RS must exceed this fraction of the sector ETF RS
# to confirm the name is outperforming within an already-hot sector.
_TICKER_RS_PREMIUM = 0.0  # ticker RS > sector RS (any premium counts)

# Regimes where this strategy fires
_ALLOWED_REGIMES = {
    RegimeState.TRENDING_UP_STRONG,
    RegimeState.TRENDING_UP_MOD,
}

# Time windows where no new entries are permitted
_BLOCKED_WINDOWS = {TimeWindow.CHAOS_OPEN, TimeWindow.CLOSE_MECHANICS}

# Stop distance as multiple of ATR(14)
_STOP_ATR_MULT = 1.5

# Target R-multiples
_TARGET_MIN_R = 2.0
_TARGET_MAX_R = 3.0

# Minimum RVOL for entry
_DEFAULT_RVOL_MIN = 1.0


class AIThematicStrategy(StrategyBase):
    """S10 — AI Thematic Momentum.

    Monitors the AI / semiconductor theme by tracking sector ETFs
    (SOXX, SMH, BOTZ) for relative strength vs SPY.  When the sector
    is leading AND an individual AI name is trending above its 10-week
    EMA with its own relative strength, a LONG signal fires.

    This is a pre-market scan strategy (06:00 UK) designed to identify
    multi-day trend setups in the AI infrastructure buildout theme.
    """

    def __init__(self) -> None:
        super().__init__(name="AI Thematic", strategy_id="S10")

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
        """Scan AI-focused tickers for thematic momentum entries.

        Only fires when the AI sector as a whole is showing relative
        strength AND the individual ticker is trending.

        Returns:
            list[Signal]: LONG signals for AI names passing all filters.
        """
        if not self.enabled:
            return []

        # Regime gate — only fire in uptrending regimes
        if market_ctx.regime not in _ALLOWED_REGIMES:
            self.logger.debug(
                "S10 skipped: regime %s not trending up", market_ctx.regime
            )
            return []

        # Block during forbidden time windows
        if market_ctx.time_window in _BLOCKED_WINDOWS:
            self.logger.debug("S10 blocked: time window %s", market_ctx.time_window)
            return []

        # --- Check sector-level RS threshold ---
        sector_rs = self._get_best_sector_rs(sector_flows)
        if sector_rs is None or sector_rs < _SECTOR_RS_THRESHOLD:
            self.logger.debug(
                "S10 skipped: AI sector RS %.3f < %.3f threshold",
                sector_rs or 0.0,
                _SECTOR_RS_THRESHOLD,
            )
            return []

        self.logger.info(
            "S10: AI sector RS %.3f exceeds threshold — scanning individual names",
            sector_rs,
        )

        # Filter tickers to AI focus names present in the input list
        ai_candidates = [t for t in tickers if t in _AI_TICKERS]
        if not ai_candidates:
            self.logger.debug("S10: no AI tickers in scan universe")
            return []

        signals: list[Signal] = []

        for ticker in ai_candidates:
            snap = indicators.get(ticker)
            if snap is None:
                self.logger.debug("S10: no indicator data for %s", ticker)
                continue

            flow = sector_flows.get(ticker)

            try:
                signal = self._evaluate_ticker(
                    ticker, snap, market_ctx, flow, sector_rs
                )
                if signal is not None:
                    signals.append(signal)
            except Exception:
                self.logger.exception("S10: error evaluating %s", ticker)

        return signals

    # ------------------------------------------------------------------
    # sector RS helper
    # ------------------------------------------------------------------

    def _get_best_sector_rs(
        self, sector_flows: dict[str, SectorFlow]
    ) -> float | None:
        """Find the highest RS vs SPY among the AI sector ETFs.

        We check SOXX, SMH, and BOTZ and return the maximum RS reading.
        If none of the sector ETFs have flow data, returns None.
        """
        best: float | None = None
        for etf in _AI_SECTOR_ETFS:
            flow = sector_flows.get(etf)
            if flow is None:
                continue
            rs = flow.rs_vs_spy
            if best is None or rs > best:
                best = rs
        return best

    # ------------------------------------------------------------------
    # per-ticker evaluation
    # ------------------------------------------------------------------

    def _evaluate_ticker(
        self,
        ticker: str,
        snap: IndicatorSnapshot,
        market_ctx: MarketContext,
        flow: SectorFlow | None,
        sector_rs: float,
    ) -> Signal | None:
        """Evaluate a single AI ticker for a thematic momentum entry.

        Conditions:
            1. Price above 10-week EMA (trend intact on weekly timeframe)
            2. Individual ticker RS vs SPY exceeds the sector ETF RS
               (outperforming within the hot sector)
            3. RVOL above minimum (participation confirmation)

        Returns:
            Signal if all conditions met, otherwise None.
        """
        # --- Data quality checks ---
        if snap.price <= 0 or snap.atr14 <= 0:
            return None
        if snap.ema10w <= 0:
            self.logger.debug("S10: %s missing 10-week EMA data", ticker)
            return None

        # --- 1. Price above 10-week EMA ---
        if snap.price <= snap.ema10w:
            self.logger.debug(
                "S10: %s price %.2f below 10w EMA %.2f",
                ticker,
                snap.price,
                snap.ema10w,
            )
            return None

        # --- 2. Individual ticker RS vs SPY exceeds sector RS ---
        if flow is not None:
            ticker_rs = flow.rs_vs_spy
            if ticker_rs <= sector_rs + _TICKER_RS_PREMIUM:
                self.logger.debug(
                    "S10: %s ticker RS %.3f not exceeding sector RS %.3f",
                    ticker,
                    ticker_rs,
                    sector_rs,
                )
                return None
        else:
            # If we have no flow data for the individual ticker, skip it
            self.logger.debug("S10: %s no sector flow data available", ticker)
            return None

        # --- 3. RVOL gate ---
        rvol_min = config.get_ticker_override(ticker, "rvol_min", _DEFAULT_RVOL_MIN)
        if snap.rvol < rvol_min:
            self.logger.debug(
                "S10: %s RVOL %.2f < min %.2f", ticker, snap.rvol, rvol_min
            )
            return None

        # --- Compute entry, stop, targets ---
        entry = snap.price
        stop_mult = config.get_ticker_override(ticker, "stop_mult", _STOP_ATR_MULT)
        stop = entry - (stop_mult * snap.atr14)  # LONG only

        risk = entry - stop
        target_1r = entry + (_TARGET_MIN_R * risk)
        target_2r = entry + (_TARGET_MAX_R * risk)

        signal = self._create_signal(
            ticker=ticker,
            direction="LONG",
            entry=round(entry, 2),
            stop=round(stop, 2),
            indicators=snap,
            market_ctx=market_ctx,
        )
        signal.target_1r = round(target_1r, 2)
        signal.target_2r = round(target_2r, 2)
        signal.timeframe_layer = "SWING"

        # Add thematic context to patterns
        signal.patterns_detected = list(snap.patterns_detected) + [
            f"AI_THEMATIC_RS:{ticker_rs:.3f}",
            f"SECTOR_RS:{sector_rs:.3f}",
            f"ABOVE_10W_EMA",
        ]

        self.logger.info(
            "S10 SIGNAL: LONG %s @ %.2f stop %.2f "
            "(ticker_RS=%.3f, sector_RS=%.3f, RVOL=%.2f)",
            ticker,
            entry,
            stop,
            ticker_rs,
            sector_rs,
            snap.rvol,
        )
        return signal
