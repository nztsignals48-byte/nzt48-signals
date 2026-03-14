"""
S5 — Post-Earnings Announcement Drift (PEAD)

Exploits the well-documented tendency for stocks to drift in the direction
of their earnings surprise for 3-10 trading days after reporting.

Entry criteria:
  - Earnings gap > 5% on report day
  - Volume > 2x average (confirms institutional participation)
  - Analyst revision detected in narrative context
  - Only fires AFTER earnings are reported (catalyst_type == "earnings")

Risk management:
  - 0.5% risk per trade (reduced due to gap risk)
  - 2.0x ATR stop (wider for earnings noise)
  - Max 3 simultaneous PEAD positions
  - Target 2.0-4.0R, trail after +1.5R
  - Entry window: 15-45 min after open (let initial volatility settle)

Universe: Bot B stocks reporting that week + any stock gapping > 5%.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
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
    TimeWindow,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_GAP_PCT: float = 5.0            # Minimum earnings gap (%)
MIN_RVOL: float = 2.0               # Minimum relative volume (2x)
RISK_PER_TRADE: float = 0.005       # 0.5% risk
STOP_ATR_MULT: float = 2.0          # Stop = 2.0x ATR from entry
MAX_POSITIONS: int = 3               # Hard cap on simultaneous PEAD trades
TARGET_LOW_R: float = 2.0           # Minimum target in R
TARGET_HIGH_R: float = 4.0          # Maximum target in R
TRAIL_TRIGGER_R: float = 1.5        # Start trailing after this R-multiple

# Sprint 2 research-backed multipliers (#14)
FRIDAY_DRIFT_MULTIPLIER: float = 1.5   # Friday earnings -> 50% longer drift
CONTRAST_EFFECT_MULTIPLIER: float = 1.3  # Surprise direction reversal from prior quarter

# Acceptable time windows for PEAD entry (15-45 min post-open)
ALLOWED_ENTRY_WINDOWS: set[TimeWindow] = {
    TimeWindow.MORNING_MOMENTUM,     # 09:35-10:30 — primary PEAD entry zone
}


class PEADEarningsDrift(StrategyBase):
    """S5 Post-Earnings Announcement Drift.

    Detects earnings-day gaps with strong volume confirmation and generates
    signals to ride the subsequent multi-day drift.
    """

    def __init__(self) -> None:
        super().__init__(
            name="PEAD Earnings Drift",
            strategy_id="S5",
        )
        self._active_pead_tickers: list[str] = []  # Track live PEAD positions

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
        """Scan universe for post-earnings drift setups.

        Only triggers on earnings-day when the initial volatility has
        settled (MORNING_MOMENTUM window) and the gap + volume + analyst
        revision conditions are met.
        """
        if not self.enabled:
            return []

        signals: list[Signal] = []

        # Gate: only scan during the valid entry window
        if market_ctx.time_window not in ALLOWED_ENTRY_WINDOWS:
            self.logger.debug(
                "PEAD scan skipped — outside entry window (%s)",
                market_ctx.time_window.value,
            )
            return signals

        available_slots = MAX_POSITIONS - len(self._active_pead_tickers)
        if available_slots <= 0:
            self.logger.info("PEAD at max positions (%d). No new scans.", MAX_POSITIONS)
            return signals

        for ticker in tickers:
            if available_slots <= 0:
                break

            snap = indicators.get(ticker)
            narrative = narratives.get(ticker)

            if snap is None or narrative is None:
                continue

            # Already tracking this ticker — skip
            if ticker in self._active_pead_tickers:
                continue

            signal = self._evaluate_ticker(ticker, snap, narrative, market_ctx)
            if signal is not None:
                signals.append(signal)
                self._active_pead_tickers.append(ticker)
                available_slots -= 1
                self.logger.info(
                    "PEAD signal: %s %s | gap implied by rvol=%.1f | entry=%.2f stop=%.2f",
                    signal.direction.value,
                    ticker,
                    snap.rvol,
                    signal.entry,
                    signal.stop,
                )

        return signals

    def release_ticker(self, ticker: str) -> None:
        """Call when a PEAD position is closed to free the slot."""
        if ticker in self._active_pead_tickers:
            self._active_pead_tickers.remove(ticker)
            self.logger.info("PEAD slot released: %s", ticker)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _evaluate_ticker(
        self,
        ticker: str,
        snap: IndicatorSnapshot,
        narrative: NarrativeContext,
        market_ctx: MarketContext,
    ) -> Optional[Signal]:
        """Evaluate a single ticker for PEAD eligibility.

        Returns a Signal if all conditions are met, otherwise None.
        """
        # --- Condition 1: Must be a post-earnings catalyst ---
        if not narrative.catalyst_detected:
            return None
        if narrative.catalyst_type != "earnings":
            return None

        # --- Condition 2: Gap > 5% ---
        gap_pct = self._estimate_gap_pct(snap)
        if abs(gap_pct) < MIN_GAP_PCT:
            return None

        # --- Condition 3: Volume confirmation (RVOL > 2x) ---
        if snap.rvol < MIN_RVOL:
            return None

        # --- Condition 4: Analyst revision or strong narrative ---
        if not self._has_analyst_revision(narrative):
            return None

        # --- Direction determination ---
        direction = self._determine_direction(gap_pct, narrative)
        if direction is None:
            return None

        # --- Compute entry, stop, targets ---
        entry = snap.price  # Current price in the MORNING_MOMENTUM window
        atr = snap.atr14

        if atr <= 0:
            self.logger.warning("ATR is zero for %s — cannot size stop.", ticker)
            return None

        if direction == Direction.LONG:
            stop = entry - (STOP_ATR_MULT * atr)
            target_1r = entry + (TARGET_LOW_R * (entry - stop))
            target_2r = entry + (TARGET_HIGH_R * (entry - stop))
            trail = entry + (TRAIL_TRIGGER_R * (entry - stop))
        else:
            stop = entry + (STOP_ATR_MULT * atr)
            target_1r = entry - (TARGET_LOW_R * (stop - entry))
            target_2r = entry - (TARGET_HIGH_R * (stop - entry))
            trail = entry - (TRAIL_TRIGGER_R * (stop - entry))

        # --- Sprint 2: Apply research-backed multipliers to targets ---
        friday_mult = self._friday_earnings_multiplier(snap.timestamp)
        contrast_mult = self._contrast_effect_multiplier(
            current_surprise_pct=gap_pct,
            prior_surprise_pct=getattr(snap, "prior_surprise_pct", None),
        )
        short_interest_boost = self._compute_short_interest_boost(
            short_interest_pct=getattr(snap, "short_interest_pct", 0.0),
        )

        combined_multiplier = friday_mult * contrast_mult * short_interest_boost

        if combined_multiplier != 1.0:
            risk_dist = abs(entry - stop)
            if direction == Direction.LONG:
                target_1r = entry + (TARGET_LOW_R * risk_dist * combined_multiplier)
                target_2r = entry + (TARGET_HIGH_R * risk_dist * combined_multiplier)
            else:
                target_1r = entry - (TARGET_LOW_R * risk_dist * combined_multiplier)
                target_2r = entry - (TARGET_HIGH_R * risk_dist * combined_multiplier)

            self.logger.info(
                "PEAD %s multipliers: friday=%.1f contrast=%.1f SI=%.1f => combined=%.2f",
                ticker,
                friday_mult,
                contrast_mult,
                short_interest_boost,
                combined_multiplier,
            )

        signal = self._create_signal(
            ticker=ticker,
            direction=direction.value,
            entry=entry,
            stop=stop,
            indicators=snap,
            market_ctx=market_ctx,
        )

        # Populate PEAD-specific fields
        signal.risk_pct = RISK_PER_TRADE
        signal.target_1r = round(target_1r, 4)
        signal.target_2r = round(target_2r, 4)
        signal.trail = round(trail, 4)
        signal.bot = Bot.B
        signal.bot_instance = BotInstance.EARNINGS
        signal.timeframe_layer = "SWING"
        signal.qualification_log.append(f"PEAD: gap={gap_pct:+.1f}% rvol={snap.rvol:.1f}x")
        signal.qualification_log.append(f"narrative={narrative.sentiment} catalyst={narrative.catalyst_type}")
        if combined_multiplier != 1.0:
            signal.qualification_log.append(
                f"multipliers: fri={friday_mult:.1f} contrast={contrast_mult:.1f} "
                f"SI={short_interest_boost:.1f} combined={combined_multiplier:.2f}"
            )

        return signal

    # ------------------------------------------------------------------
    # Sprint 2: Research-backed multipliers (#14)
    # ------------------------------------------------------------------

    def _friday_earnings_multiplier(self, earnings_date: datetime) -> float:
        """Compute drift duration multiplier for Friday earnings announcements.

        Research shows that earnings announced on Friday experience ~50% longer
        post-earnings drift, likely because weekend processing delays the
        market's full reaction.

        Args:
            earnings_date: The datetime of the earnings announcement.

        Returns:
            1.5 if Friday, 1.0 otherwise.
        """
        try:
            if earnings_date.weekday() == 4:  # 0=Mon ... 4=Fri
                self.logger.debug("Friday earnings detected — applying %.1fx drift multiplier.", FRIDAY_DRIFT_MULTIPLIER)
                return FRIDAY_DRIFT_MULTIPLIER
        except (AttributeError, TypeError):
            self.logger.warning("Invalid earnings_date for Friday check: %s", earnings_date)
        return 1.0

    def _contrast_effect_multiplier(
        self,
        current_surprise_pct: float,
        prior_surprise_pct: Optional[float],
    ) -> float:
        """Compute multiplier for contrast effect between consecutive earnings surprises.

        When the current surprise direction is OPPOSITE to the prior quarter's
        surprise, investors under-react even more (anchoring to the old direction),
        producing a 30% stronger drift.

        Args:
            current_surprise_pct: Current quarter's earnings surprise (+ = beat, - = miss).
            prior_surprise_pct: Prior quarter's earnings surprise (+ = beat, - = miss).

        Returns:
            1.3 if directions are opposite, 1.0 otherwise.
        """
        if prior_surprise_pct is None or prior_surprise_pct == 0.0:
            return 1.0
        if current_surprise_pct == 0.0:
            return 1.0

        # Opposite signs = contrast effect
        if (current_surprise_pct > 0) != (prior_surprise_pct > 0):
            self.logger.debug(
                "Contrast effect detected: current=%.1f%% vs prior=%.1f%% — applying %.1fx multiplier.",
                current_surprise_pct,
                prior_surprise_pct,
                CONTRAST_EFFECT_MULTIPLIER,
            )
            return CONTRAST_EFFECT_MULTIPLIER

        return 1.0

    def _compute_short_interest_boost(self, short_interest_pct: float) -> float:
        """Compute boost factor based on short interest.

        Higher short interest amplifies PEAD because:
          - Positive surprise: short covering adds buying pressure.
          - Negative surprise: shorts piling in extends the move.

        Args:
            short_interest_pct: Short interest as percentage of float (0-100).

        Returns:
            1.2 if SI > 20%, 1.1 if SI > 10%, 1.0 otherwise.
        """
        try:
            si = float(short_interest_pct)
        except (ValueError, TypeError):
            return 1.0

        if si > 20.0:
            self.logger.debug("High short interest (%.1f%%) — applying 1.2x boost.", si)
            return 1.2
        if si > 10.0:
            self.logger.debug("Moderate short interest (%.1f%%) — applying 1.1x boost.", si)
            return 1.1
        return 1.0

    # ------------------------------------------------------------------
    # Existing private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_gap_pct(snap: IndicatorSnapshot) -> float:
        """Estimate the overnight earnings gap percentage.

        Uses the distance from the previous-day close proxy (EMA9 as a
        rough approximation when true prev-close is unavailable) to the
        current price. A positive value means gap-up, negative means gap-down.

        In production this should be replaced with actual previous-close data.
        """
        if snap.ema9 <= 0:
            return 0.0
        return ((snap.price - snap.ema9) / snap.ema9) * 100.0

    @staticmethod
    def _has_analyst_revision(narrative: NarrativeContext) -> bool:
        """Check whether the narrative indicates an analyst revision
        or sufficiently strong post-earnings sentiment shift.

        Accepts:
          - Explicit 'upgrade'/'downgrade' catalyst types
          - Strong positive/negative sentiment with high narrative score
          - Headline containing revision keywords
        """
        revision_keywords = {"upgrade", "downgrade", "revision", "raised", "cut", "beat", "miss"}

        # Direct analyst action catalyst
        if narrative.catalyst_type in ("upgrade", "downgrade"):
            return True

        # Strong narrative score implies analyst/institutional reaction
        if abs(narrative.narrative_score) >= 5:
            return True

        # Keyword scan in headline
        headline_lower = narrative.headline.lower()
        if any(kw in headline_lower for kw in revision_keywords):
            return True

        return False

    @staticmethod
    def _determine_direction(
        gap_pct: float,
        narrative: NarrativeContext,
    ) -> Optional[Direction]:
        """Determine drift direction from gap and narrative.

        LONG drift: Beat + positive guidance + gap up
        SHORT drift: Miss + negative guidance + gap down
        Mixed signals: No trade (return None)
        """
        is_gap_up = gap_pct > 0
        is_positive_sentiment = narrative.sentiment == "positive"
        is_negative_sentiment = narrative.sentiment == "negative"

        # Clean long drift: gap up + positive narrative
        if is_gap_up and is_positive_sentiment:
            return Direction.LONG

        # Clean short drift: gap down + negative narrative
        if not is_gap_up and is_negative_sentiment:
            return Direction.SHORT

        # Mixed signals — sit out
        return None
