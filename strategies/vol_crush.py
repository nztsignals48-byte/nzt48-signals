"""
S8 — Volatility Crush

Profits from volatility mean reversion after VIX spikes. When VIX spikes
above 25 and then starts declining, the strategy buys the highest-beta
names that dropped the most during the fear spike.

Trigger:
  - VIX spikes above 25 (fear event)
  - VIX then drops below its 5-day EMA (fear receding)
  - VIX makes a lower high (confirms rollover, not just a dip)

Direction: Always LONG (buying the dip after vol crush).

Target stocks: Highest beta names that dropped most (TSLA, SMCI, AMD, etc.)

Risk management:
  - 1.5x ATR stop
  - 0.75% risk per trade
  - Target 1.5-2.0R
  - Works best in RANGE_BOUND transitioning to TRENDING_UP
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

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
    BotInstance,
    RegimeState,
    GEXRegime,
    ConfidenceBreakdown,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VIX_SPIKE_THRESHOLD: float = 25.0     # VIX must have been above this
VIX_EMA_PERIOD: int = 5               # 5-day EMA for VIX rollover detection
STOP_ATR_MULT: float = 1.5            # Tighter stop — vol is compressing
RISK_PER_TRADE: float = 0.0075        # 0.75% risk
TARGET_LOW_R: float = 1.5             # Minimum target
TARGET_HIGH_R: float = 2.0            # Stretch target
MAX_POSITIONS: int = 4                # Max vol-crush trades at once

# Beta targets: high-beta names that drop hardest during VIX spikes
# These are scanned from the provided tickers list, but we prioritise
# known high-beta names when present.
PREFERRED_BETA_TICKERS: list[str] = [
    "TSLA", "SMCI", "AMD", "NVDA", "MARA", "COIN", "SHOP", "SQ", "PLTR", "MSTR",
]

# Minimum drop from recent high to qualify (proxy: price below EMA20)
MIN_DISCOUNT_FROM_EMA20_PCT: float = 3.0  # Must be 3%+ below 20 EMA

# Regime compatibility
IDEAL_REGIMES: set[RegimeState] = {
    RegimeState.RANGE_BOUND,
    RegimeState.HIGH_VOLATILITY,       # Transitioning out of this
    RegimeState.TRENDING_UP_MOD,       # Early recovery
}


class VolatilityCrush(StrategyBase):
    """S8 Volatility Crush strategy.

    Detects VIX rollover after a fear spike and generates LONG signals
    on high-beta names that were hit hardest. Profits from the rapid
    compression of implied volatility and the subsequent mean reversion
    in equity prices.
    """

    def __init__(self) -> None:
        super().__init__(
            name="Volatility Crush",
            strategy_id="S8",
        )
        self._vix_was_above_threshold: bool = False
        self._prev_vix: float = 0.0
        self._vix_high_water: float = 0.0   # Track the VIX peak
        self._active_tickers: list[str] = []
        self._crush_active: bool = False     # True once VIX drops below EMA

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(
        self,
        tickers: list[str],
        indicators: dict[str, IndicatorSnapshot],
        market_ctx: MarketContext,
        sector_flows: dict[str, SectorFlow],
        narratives: dict[str, NarrativeContext],
    ) -> list[Signal]:
        """Scan for vol-crush buy-the-dip setups.

        The scan proceeds in two phases:
        1. Monitor VIX for spike-then-rollover pattern
        2. Once confirmed, pick the best high-beta names to buy
        """
        if not self.enabled:
            return []

        signals: list[Signal] = []
        current_vix = market_ctx.vix

        if current_vix <= 0:
            return signals

        # --- Phase 1: Track VIX state machine ---
        self._update_vix_state(current_vix, indicators)

        if not self._crush_active:
            return signals

        # --- Phase 2: VIX crush confirmed — find high-beta buy candidates ---
        available_slots = MAX_POSITIONS - len(self._active_tickers)
        if available_slots <= 0:
            return signals

        # Regime filter: best in range-bound transitioning to uptrend
        if market_ctx.regime not in IDEAL_REGIMES:
            self.logger.debug(
                "Vol crush active but regime %s not ideal. Proceeding with caution.",
                market_ctx.regime.value,
            )

        # Score and rank candidates
        candidates = self._rank_candidates(tickers, indicators, market_ctx)

        for ticker, score in candidates[:available_slots]:
            snap = indicators.get(ticker)
            if snap is None:
                continue

            signal = self._build_crush_signal(ticker, snap, market_ctx, score)
            if signal is not None:
                signals.append(signal)
                self._active_tickers.append(ticker)

        # Reset crush state after generating signals (one-shot per VIX cycle)
        if signals:
            self.logger.info(
                "Vol crush fired: %d signals generated. VIX=%.1f (peak was %.1f)",
                len(signals),
                current_vix,
                self._vix_high_water,
            )
            self._crush_active = False

        return signals

    def release_ticker(self, ticker: str) -> None:
        """Call when a vol-crush position is closed."""
        if ticker in self._active_tickers:
            self._active_tickers.remove(ticker)

    # ------------------------------------------------------------------
    # VIX state machine
    # ------------------------------------------------------------------

    def _update_vix_state(
        self,
        current_vix: float,
        indicators: dict[str, IndicatorSnapshot],
    ) -> None:
        """Track VIX spike-then-rollover pattern.

        State transitions:
          IDLE -> SPIKED: VIX crosses above 25
          SPIKED -> CRUSH_ACTIVE: VIX drops below 5-day EMA AND makes lower high
          CRUSH_ACTIVE -> IDLE: After signals are generated (reset in scan)
        """
        # Track high-water mark
        if current_vix > self._vix_high_water:
            self._vix_high_water = current_vix

        # Phase 1: Detect spike above threshold
        if current_vix >= VIX_SPIKE_THRESHOLD:
            if not self._vix_was_above_threshold:
                self.logger.info("VIX spike detected: %.1f (above %.1f threshold)",
                                 current_vix, VIX_SPIKE_THRESHOLD)
            self._vix_was_above_threshold = True
            self._crush_active = False
            self._prev_vix = current_vix
            return

        # Phase 2: VIX was above threshold but now below — check for rollover
        if self._vix_was_above_threshold and not self._crush_active:
            # Confirm rollover: VIX below its 5-day EMA equivalent
            # Use the VIX snap if available, otherwise use a simple declining check
            vix_ema5 = self._estimate_vix_ema5(current_vix, indicators)

            is_below_ema = current_vix < vix_ema5
            is_lower_high = current_vix < self._prev_vix  # Declining

            if is_below_ema and is_lower_high:
                self._crush_active = True
                self.logger.info(
                    "VIX crush confirmed: VIX=%.1f < EMA5=%.1f, peak=%.1f",
                    current_vix,
                    vix_ema5,
                    self._vix_high_water,
                )

        # Reset if VIX drops back to normal and we never fired
        if current_vix < 18.0 and self._vix_was_above_threshold and not self._crush_active:
            self.logger.info("VIX normalised (%.1f) without crush signal. Resetting.", current_vix)
            self._reset_vix_state()

        self._prev_vix = current_vix

    def _reset_vix_state(self) -> None:
        """Reset VIX tracking to idle state."""
        self._vix_was_above_threshold = False
        self._vix_high_water = 0.0
        self._crush_active = False

    @staticmethod
    def _estimate_vix_ema5(
        current_vix: float,
        indicators: dict[str, IndicatorSnapshot],
    ) -> float:
        """Estimate the VIX 5-day EMA.

        If VIX indicator snapshot is available, use its EMA9 as a proxy.
        Otherwise, use a simple smoothed estimate from the spike threshold.
        """
        # Try to get VIX snap (some systems track VIX as a ticker)
        vix_snap = indicators.get("VIX") or indicators.get("^VIX")
        if vix_snap is not None and vix_snap.ema9 > 0:
            return vix_snap.ema9

        # Fallback: midpoint between threshold and current as a rough EMA proxy
        # This is conservative — biased toward confirming the crush
        return (VIX_SPIKE_THRESHOLD + current_vix) / 2.0

    # ------------------------------------------------------------------
    # Candidate ranking
    # ------------------------------------------------------------------

    def _rank_candidates(
        self,
        tickers: list[str],
        indicators: dict[str, IndicatorSnapshot],
        market_ctx: MarketContext,
    ) -> list[tuple[str, float]]:
        """Rank tickers by vol-crush attractiveness.

        Score factors:
          - Distance below EMA20 (bigger discount = better)
          - RVOL (higher = more institutional interest)
          - Preferred beta list membership
          - RSI oversold reading
        """
        scored: list[tuple[str, float]] = []

        for ticker in tickers:
            snap = indicators.get(ticker)
            if snap is None:
                continue

            score = self._score_candidate(ticker, snap)
            if score > 0:
                scored.append((ticker, score))

        # Sort by score descending (best candidates first)
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    @staticmethod
    def _score_candidate(ticker: str, snap: IndicatorSnapshot) -> float:
        """Score a single candidate for vol-crush eligibility.

        Returns 0 if the candidate does not qualify.
        """
        score: float = 0.0

        # --- Gate: must be trading below EMA20 (discounted) ---
        if snap.ema20 <= 0 or snap.price <= 0:
            return 0.0

        discount_pct = ((snap.ema20 - snap.price) / snap.ema20) * 100.0
        if discount_pct < MIN_DISCOUNT_FROM_EMA20_PCT:
            return 0.0

        # Score the discount (more discount = higher score)
        score += min(discount_pct, 20.0)  # Cap at 20% to avoid broken stocks

        # Bonus for preferred high-beta names
        if ticker in PREFERRED_BETA_TICKERS:
            score += 10.0

        # Bonus for elevated RVOL (institutional interest returning)
        if snap.rvol >= 2.0:
            score += 5.0
        elif snap.rvol >= 1.5:
            score += 2.0

        # Bonus for oversold RSI (mean reversion fuel)
        if snap.rsi14 < 30:
            score += 8.0
        elif snap.rsi14 < 40:
            score += 4.0

        # Bonus for positive cumulative delta (buyers stepping in)
        if snap.cumulative_delta > 0:
            score += 3.0

        # Penalty for extremely low volume (illiquid bounce)
        if snap.rvol < 0.5:
            score -= 10.0

        return max(0.0, score)

    # ------------------------------------------------------------------
    # Signal construction
    # ------------------------------------------------------------------

    def _build_crush_signal(
        self,
        ticker: str,
        snap: IndicatorSnapshot,
        market_ctx: MarketContext,
        candidate_score: float,
    ) -> Optional[Signal]:
        """Build a LONG signal for a vol-crush candidate."""
        entry = snap.price
        atr = snap.atr14

        if atr <= 0:
            self.logger.warning("Zero ATR for %s — cannot build vol-crush signal.", ticker)
            return None

        # Tighter stop (1.5x ATR) since vol is compressing
        stop = entry - (STOP_ATR_MULT * atr)
        risk = entry - stop

        if risk <= 0:
            return None

        target_1r = entry + (TARGET_LOW_R * risk)
        target_2r = entry + (TARGET_HIGH_R * risk)

        signal = self._create_signal(
            ticker=ticker,
            direction="LONG",
            entry=entry,
            stop=stop,
            indicators=snap,
            market_ctx=market_ctx,
        )

        signal.risk_pct = RISK_PER_TRADE
        signal.target_1r = round(target_1r, 4)
        signal.target_2r = round(target_2r, 4)
        signal.bot = Bot.B
        signal.bot_instance = BotInstance.BULL
        signal.timeframe_layer = "SWING"

        # Confidence scoring
        confidence = self._compute_crush_confidence(snap, market_ctx, candidate_score)
        signal.confidence = confidence.final_score
        signal.confidence_breakdown = confidence

        # Qualification log
        discount_pct = ((snap.ema20 - snap.price) / snap.ema20) * 100.0 if snap.ema20 > 0 else 0
        signal.qualification_log.append(
            f"VOL_CRUSH: VIX peak={self._vix_high_water:.1f} current={market_ctx.vix:.1f}"
        )
        signal.qualification_log.append(
            f"discount_from_ema20={discount_pct:.1f}% rsi={snap.rsi14:.0f} rvol={snap.rvol:.1f}"
        )
        signal.qualification_log.append(f"candidate_score={candidate_score:.1f}")

        self.logger.info(
            "Vol crush LONG %s | entry=%.2f stop=%.2f | discount=%.1f%% score=%.1f",
            ticker,
            entry,
            stop,
            discount_pct,
            candidate_score,
        )

        return signal

    @staticmethod
    def _compute_crush_confidence(
        snap: IndicatorSnapshot,
        market_ctx: MarketContext,
        candidate_score: float,
    ) -> ConfidenceBreakdown:
        """Compute confidence for a vol-crush signal.

        Primary drivers: VIX rollover confirmation + oversold depth + volume.
        """
        cb = ConfidenceBreakdown()

        # Layer 1: Price action — oversold bounce characteristics
        if snap.rsi14 < 30:
            cb.layer1_price_action = 30.0
        elif snap.rsi14 < 40:
            cb.layer1_price_action = 20.0
        else:
            cb.layer1_price_action = 12.0

        # Bonus for bullish divergence indicators
        if snap.macd_histogram > 0 and snap.rsi14 < 45:
            cb.layer1_price_action = min(45.0, cb.layer1_price_action + 8.0)

        # Layer 2: Regime — best in RANGE_BOUND transitioning up
        if market_ctx.regime == RegimeState.RANGE_BOUND:
            cb.layer2_regime = 18.0
        elif market_ctx.regime == RegimeState.TRENDING_UP_MOD:
            cb.layer2_regime = 15.0
        elif market_ctx.regime == RegimeState.HIGH_VOLATILITY:
            cb.layer2_regime = 10.0
        else:
            cb.layer2_regime = 5.0

        # Layer 3: Volume confirmation
        if snap.rvol >= 2.0:
            cb.layer3_sector_flow = 12.0
        elif snap.rvol >= 1.5:
            cb.layer3_sector_flow = 8.0
        else:
            cb.layer3_sector_flow = 4.0

        # Layer 4: Macro — VIX crush is the macro confirmation
        vix_crush_strength = max(0, 25 - market_ctx.vix)  # How far VIX has fallen from 25
        cb.layer4_macro = min(10.0, vix_crush_strength * 2.0)

        # Layer 5: Candidate quality score
        cb.layer5_narrative = min(10.0, candidate_score / 4.0)

        # Penalties
        if market_ctx.regime in (RegimeState.TRENDING_DOWN_STRONG, RegimeState.SHOCK):
            cb.penalties = 15.0  # Vol crush in a downtrend is dangerous
        elif market_ctx.regime == RegimeState.RISK_OFF:
            cb.penalties = 10.0
        if market_ctx.fomc_today or market_ctx.cpi_nfp_today:
            cb.penalties += 5.0

        cb.compute()
        return cb
