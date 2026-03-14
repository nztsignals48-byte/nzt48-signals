"""
NZT-48 Trading System — Attention Exhaustion Detection Engine
Sprint 2, Feature #16 from research doc.

Tracks attention metrics (RVOL, news count, price gap magnitude) for each
ticker and detects attention exhaustion — the phase where volume/interest
has peaked and is declining, but the price move hasn't yet reversed.

This is the optimal contrarian entry window: the crowd's attention is fading
but the price hasn't caught up yet. Once attention fully fades, the move
typically reverses.

Attention lifecycle:
    NONE -> RISING -> PEAK -> EXHAUSTING -> FADED -> NONE (reset)

The key signal is the transition from PEAK to EXHAUSTING — this is when
the crowd is leaving but the price hasn't reversed yet.
"""

from __future__ import annotations

import logging
import time as _time_module
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("nzt48.attention_detector")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_OBSERVATIONS = 100

# Attention score contribution thresholds
_RVOL_TIERS = [
    (3.0, 40),   # RVOL > 3x -> 40 points
    (2.0, 25),   # 2-3x -> 25 points
    (1.0, 10),   # 1-2x -> 10 points
    (0.0, 0),    # <1 -> 0 points
]

_NEWS_TIERS = [
    (5, 30),     # > 5 news items -> 30 points
    (3, 20),     # 3-5 -> 20 points
    (1, 10),     # 1-2 -> 10 points
    (0, 0),      # 0 -> 0 points
]

_GAP_TIERS = [
    (5.0, 30),   # > 5% gap -> 30 points
    (3.0, 20),   # 3-5% -> 20 points
    (1.0, 10),   # 1-3% -> 10 points
    (0.0, 0),    # <1% -> 0 points
]

# Exhaustion detection parameters
_PEAK_DECLINE_THRESHOLD = 0.30   # RVOL must decline > 30% from peak
_RESET_RVOL_THRESHOLD = 1.5     # Reset tracking when RVOL drops below 1.5x
_RESET_DAYS = 3                  # Reset tracking after 3 days


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class _AttentionObservation:
    """Single attention metric snapshot for a ticker."""
    rvol: float
    news_count: int = 0
    gap_pct: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class _TickerAttentionState:
    """Tracks the attention lifecycle state for a single ticker."""
    observations: deque  # deque[_AttentionObservation]
    peak_rvol: float = 0.0
    peak_time: Optional[datetime] = None
    attention_direction: Optional[str] = None  # "UP" or "DOWN" (price direction when attention started)
    phase: str = "NONE"  # NONE, RISING, PEAK, EXHAUSTING, FADED

    def __init__(self) -> None:
        self.observations = deque(maxlen=_MAX_OBSERVATIONS)
        self.peak_rvol = 0.0
        self.peak_time = None
        self.attention_direction = None
        self.phase = "NONE"


# ---------------------------------------------------------------------------
# AttentionDetector
# ---------------------------------------------------------------------------

class AttentionDetector:
    """Tracks attention metrics and detects attention exhaustion.

    Monitors RVOL, news flow, and gap magnitude for each ticker to
    identify the attention lifecycle. The key trading signal is
    attention exhaustion — when volume/interest has peaked and is
    declining, but the underlying price hasn't yet reversed.

    Usage:
        detector = AttentionDetector()
        detector.update("NVDA", rvol=4.5, news_count=8, gap_pct=6.2)
        score = detector.compute_attention_score("NVDA")
        exhausted = detector.detect_attention_exhaustion("NVDA")
        phase = detector.get_attention_phase("NVDA")
    """

    def __init__(self) -> None:
        self._states: dict[str, _TickerAttentionState] = {}
        logger.info("AttentionDetector initialized")

    # ------------------------------------------------------------------
    # Public API: Data Ingestion
    # ------------------------------------------------------------------

    def update(
        self,
        ticker: str,
        rvol: float,
        news_count: int = 0,
        gap_pct: float = 0.0,
    ) -> None:
        """Update attention metrics for a ticker.

        Args:
            ticker: Stock ticker symbol.
            rvol: Relative volume (e.g. 3.5 = 3.5x average).
            news_count: Number of news items in the current period.
            gap_pct: Price gap magnitude as percentage (e.g. 4.2 = 4.2% gap).
        """
        try:
            if ticker not in self._states:
                self._states[ticker] = _TickerAttentionState()

            state = self._states[ticker]
            now = datetime.now(timezone.utc)

            observation = _AttentionObservation(
                rvol=rvol,
                news_count=news_count,
                gap_pct=abs(gap_pct),
                timestamp=now,
            )
            state.observations.append(observation)

            # Check for reset conditions
            self._check_reset(ticker, state, rvol, now)

            # Update peak tracking
            if rvol > state.peak_rvol:
                state.peak_rvol = rvol
                state.peak_time = now

            # Determine attention direction from gap
            if gap_pct > 0.5 and state.attention_direction is None:
                state.attention_direction = "UP"
            elif gap_pct < -0.5 and state.attention_direction is None:
                state.attention_direction = "DOWN"

            # Update phase
            self._update_phase(ticker, state)

            logger.debug(
                "ATTENTION [%s]: rvol=%.2f, news=%d, gap=%.2f%%, "
                "peak_rvol=%.2f, phase=%s",
                ticker, rvol, news_count, gap_pct,
                state.peak_rvol, state.phase,
            )

        except Exception:
            logger.exception("Failed to update attention for %s", ticker)

    # ------------------------------------------------------------------
    # Public API: Analysis
    # ------------------------------------------------------------------

    def compute_attention_score(self, ticker: str) -> float:
        """Compute composite attention score for a ticker.

        Combines RVOL, news count, and gap magnitude into a 0-100 score.

        Scoring:
        - RVOL > 3x -> 40 points, 2-3x -> 25, 1-2x -> 10, <1 -> 0
        - News > 5 -> 30 points, 3-5 -> 20, 1-2 -> 10, 0 -> 0
        - Gap > 5% -> 30 points, 3-5% -> 20, 1-3% -> 10, <1% -> 0

        Args:
            ticker: Stock ticker symbol.

        Returns:
            Attention score 0-100. Higher = more market attention on this name.
        """
        try:
            state = self._states.get(ticker)
            if not state or not state.observations:
                return 0.0

            latest = state.observations[-1]

            # RVOL contribution
            rvol_score = 0
            for threshold, points in _RVOL_TIERS:
                if latest.rvol > threshold:
                    rvol_score = points
                    break

            # News contribution
            news_score = 0
            for threshold, points in _NEWS_TIERS:
                if latest.news_count > threshold:
                    news_score = points
                    break

            # Gap contribution
            gap_score = 0
            for threshold, points in _GAP_TIERS:
                if latest.gap_pct > threshold:
                    gap_score = points
                    break

            total = rvol_score + news_score + gap_score
            return min(100.0, float(total))

        except Exception:
            logger.exception("Failed to compute attention score for %s", ticker)
            return 0.0

    def detect_attention_exhaustion(self, ticker: str) -> bool:
        """Detect if attention for a ticker is exhausting.

        True when:
        - RVOL has peaked and is declining (current RVOL < peak RVOL by > 30%)
        - But price has NOT yet reversed (still trending in the attention direction)

        This is the optimal contrarian entry signal — the crowd is leaving
        but the price hasn't caught up yet.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            True if attention exhaustion is detected (contrarian entry opportunity).
        """
        try:
            state = self._states.get(ticker)
            if not state or not state.observations or len(state.observations) < 3:
                return False

            # Check if we have a meaningful peak
            if state.peak_rvol < 2.0:
                return False

            latest = state.observations[-1]
            current_rvol = latest.rvol

            # Condition 1: RVOL has declined > 30% from peak
            decline_pct = 1.0 - (current_rvol / state.peak_rvol) if state.peak_rvol > 0 else 0.0
            rvol_declining = decline_pct > _PEAK_DECLINE_THRESHOLD

            if not rvol_declining:
                return False

            # Condition 2: Price has NOT reversed (still trending in attention direction)
            # We check the last few observations to see if price gaps are still
            # in the same direction
            recent_obs = list(state.observations)[-5:]
            if len(recent_obs) < 2:
                return False

            # Check if gap direction is consistent with attention_direction
            # If attention_direction is UP and gaps are still positive (or flat),
            # then price hasn't reversed
            price_not_reversed = True
            if state.attention_direction == "UP":
                # Price reversal = gap turning negative significantly
                avg_recent_gap = sum(o.gap_pct for o in recent_obs[-3:]) / min(3, len(recent_obs[-3:]))
                if avg_recent_gap < -1.0:
                    price_not_reversed = False
            elif state.attention_direction == "DOWN":
                avg_recent_gap = sum(o.gap_pct for o in recent_obs[-3:]) / min(3, len(recent_obs[-3:]))
                if avg_recent_gap > 1.0:
                    price_not_reversed = False

            is_exhausting = rvol_declining and price_not_reversed

            if is_exhausting:
                logger.info(
                    "ATTENTION_EXHAUSTION [%s]: peak_rvol=%.2f, current=%.2f, "
                    "decline=%.1f%%, direction=%s — CONTRARIAN ENTRY WINDOW",
                    ticker, state.peak_rvol, current_rvol,
                    decline_pct * 100, state.attention_direction,
                )

            return is_exhausting

        except Exception:
            logger.exception("Failed to detect attention exhaustion for %s", ticker)
            return False

    def get_attention_phase(self, ticker: str) -> str:
        """Get the current attention lifecycle phase for a ticker.

        Phases:
        - "NONE": No significant attention activity
        - "RISING": Attention is building (RVOL increasing)
        - "PEAK": Attention is at or near peak (RVOL at highest)
        - "EXHAUSTING": Attention fading but price hasn't reversed
        - "FADED": Attention has fully dissipated

        Args:
            ticker: Stock ticker symbol.

        Returns:
            Phase string.
        """
        try:
            state = self._states.get(ticker)
            if not state:
                return "NONE"
            return state.phase
        except Exception:
            logger.exception("Failed to get attention phase for %s", ticker)
            return "NONE"

    def get_status(self) -> dict:
        """Return current attention detector state for dashboard display.

        Returns:
            Dict with tracked tickers, their phases, and any active
            exhaustion signals.
        """
        try:
            ticker_phases: dict[str, str] = {}
            exhaustion_signals: list[str] = []

            for ticker, state in self._states.items():
                ticker_phases[ticker] = state.phase
                if state.phase == "EXHAUSTING":
                    exhaustion_signals.append(ticker)

            return {
                "engine": "AttentionDetector",
                "tracked_tickers": len(self._states),
                "ticker_phases": ticker_phases,
                "exhaustion_signals": exhaustion_signals,
                "active_exhaustions": len(exhaustion_signals),
            }
        except Exception:
            logger.exception("Failed to get attention detector status")
            return {"engine": "AttentionDetector", "error": "status_failed"}

    # ------------------------------------------------------------------
    # Private: Phase Management
    # ------------------------------------------------------------------

    def _update_phase(self, ticker: str, state: _TickerAttentionState) -> None:
        """Update the attention phase for a ticker based on current observations.

        Phase transitions:
            NONE -> RISING: RVOL crosses above 1.5x
            RISING -> PEAK: RVOL stops increasing (current <= peak)
            PEAK -> EXHAUSTING: RVOL declines > 30% from peak, price not reversed
            EXHAUSTING -> FADED: RVOL drops below 1.5x or price reverses
            FADED -> NONE: Reset after conditions met
        """
        try:
            if not state.observations:
                state.phase = "NONE"
                return

            latest = state.observations[-1]
            current_rvol = latest.rvol

            if state.phase == "NONE":
                if current_rvol > _RESET_RVOL_THRESHOLD:
                    state.phase = "RISING"
                    logger.debug("ATTENTION [%s]: NONE -> RISING (rvol=%.2f)", ticker, current_rvol)

            elif state.phase == "RISING":
                if len(state.observations) >= 2:
                    prev = list(state.observations)[-2]
                    if current_rvol < prev.rvol and current_rvol < state.peak_rvol:
                        state.phase = "PEAK"
                        logger.debug(
                            "ATTENTION [%s]: RISING -> PEAK (peak_rvol=%.2f)",
                            ticker, state.peak_rvol,
                        )

            elif state.phase == "PEAK":
                if state.peak_rvol > 0:
                    decline = 1.0 - (current_rvol / state.peak_rvol)
                    if decline > _PEAK_DECLINE_THRESHOLD:
                        state.phase = "EXHAUSTING"
                        logger.info(
                            "ATTENTION [%s]: PEAK -> EXHAUSTING "
                            "(peak=%.2f, current=%.2f, decline=%.1f%%)",
                            ticker, state.peak_rvol, current_rvol, decline * 100,
                        )

            elif state.phase == "EXHAUSTING":
                if current_rvol < _RESET_RVOL_THRESHOLD:
                    state.phase = "FADED"
                    logger.debug(
                        "ATTENTION [%s]: EXHAUSTING -> FADED (rvol=%.2f)",
                        ticker, current_rvol,
                    )

            elif state.phase == "FADED":
                # Will be reset by _check_reset
                pass

        except Exception:
            logger.exception("Failed to update phase for %s", ticker)

    def _check_reset(
        self,
        ticker: str,
        state: _TickerAttentionState,
        current_rvol: float,
        now: datetime,
    ) -> None:
        """Check whether to reset tracking for a ticker.

        Reset after:
        - 3 days since peak time
        - RVOL returns below 1.5x AND phase is FADED
        """
        try:
            should_reset = False

            # Time-based reset: 3 days since peak
            if state.peak_time is not None:
                elapsed = now - state.peak_time
                if elapsed > timedelta(days=_RESET_DAYS):
                    should_reset = True

            # RVOL-based reset when faded
            if state.phase == "FADED" and current_rvol < _RESET_RVOL_THRESHOLD:
                should_reset = True

            if should_reset:
                state.peak_rvol = 0.0
                state.peak_time = None
                state.attention_direction = None
                state.phase = "NONE"
                logger.debug("ATTENTION [%s]: RESET — peak cleared, phase=NONE", ticker)

        except Exception:
            logger.exception("Failed to check reset for %s", ticker)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def create_attention_detector() -> AttentionDetector:
    """Factory function for creating an AttentionDetector instance."""
    return AttentionDetector()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    detector = create_attention_detector()

    # Simulate attention lifecycle for NVDA
    # Phase 1: Rising attention
    detector.update("NVDA", rvol=1.8, news_count=2, gap_pct=2.0)
    print(f"Phase: {detector.get_attention_phase('NVDA')}")
    print(f"Score: {detector.compute_attention_score('NVDA')}")

    # Phase 2: Peak attention
    detector.update("NVDA", rvol=4.5, news_count=8, gap_pct=6.2)
    print(f"\nPhase: {detector.get_attention_phase('NVDA')}")
    print(f"Score: {detector.compute_attention_score('NVDA')}")

    # Phase 3: Declining (exhaustion)
    detector.update("NVDA", rvol=3.0, news_count=5, gap_pct=1.0)
    detector.update("NVDA", rvol=2.5, news_count=3, gap_pct=0.5)
    detector.update("NVDA", rvol=2.0, news_count=2, gap_pct=0.3)
    print(f"\nPhase: {detector.get_attention_phase('NVDA')}")
    print(f"Score: {detector.compute_attention_score('NVDA')}")
    print(f"Exhaustion: {detector.detect_attention_exhaustion('NVDA')}")

    # Phase 4: Faded
    detector.update("NVDA", rvol=1.2, news_count=0, gap_pct=0.1)
    print(f"\nPhase: {detector.get_attention_phase('NVDA')}")
    print(f"Score: {detector.compute_attention_score('NVDA')}")

    # Status
    print(f"\n--- Status ---")
    status = detector.get_status()
    for k, v in status.items():
        print(f"  {k}: {v}")
