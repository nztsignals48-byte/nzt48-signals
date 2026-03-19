"""Gap Detector -- overnight gap analysis for mean-reversion and continuation signals.

Provides:
- Overnight gap detection: today's open vs yesterday's close
- Gap size as percentage
- Gap classification: "information" (RVOL > 5x), "liquidity" (RVOL < 2x), "uncertain"
- Gap fill target (75% of gap distance)
- Session fill tracking

PURE FUNCTION-style design. No I/O. No threading (H07).
Zero-division guards on ALL divisions (H61).
"""

from __future__ import annotations

from typing import NamedTuple, Optional


class GapInfo(NamedTuple):
    """Detected gap information."""

    direction: str  # "up" or "down"
    gap_pct: float  # Gap size as percentage (positive = gap up, negative = gap down)
    gap_type: str  # "information", "liquidity", or "uncertain"
    fill_target: float  # Price level for 75% gap fill
    is_filled: bool  # Whether the gap has been filled during the session
    previous_close: float  # Reference: yesterday's close
    today_open: float  # Reference: today's open


class GapDetector:
    """Overnight gap detector and fill tracker.

    Detects gaps by comparing today's open to yesterday's close,
    classifies them by relative volume, and tracks intraday fill progress.

    Usage:
        detector = GapDetector()
        gap = detector.detect(prev_close=100.0, today_open=103.5, rvol=6.2)
        # During session:
        gap = detector.check_fill(current_price=101.0)
    """

    def __init__(self, fill_pct: float = 0.75, min_gap_pct: float = 0.10) -> None:
        """Initialize gap detector.

        Args:
            fill_pct: Fraction of gap distance considered a "fill" (default 0.75 = 75%).
            min_gap_pct: Minimum gap size in percent to register (default 0.10%).
        """
        self.fill_pct = fill_pct
        self.min_gap_pct = min_gap_pct
        self._gap: Optional[GapInfo] = None

    @property
    def current_gap(self) -> Optional[GapInfo]:
        """Return the current detected gap, or None if no gap."""
        return self._gap

    def detect(
        self,
        prev_close: float,
        today_open: float,
        rvol: float = 1.0,
    ) -> Optional[GapInfo]:
        """Detect an overnight gap and classify it.

        Args:
            prev_close: Yesterday's closing price.
            today_open: Today's opening price.
            rvol: Relative volume at/near the open (vs 20-day average).
                  Used for gap classification.

        Returns:
            GapInfo if a gap is detected (>= min_gap_pct), else None.
        """
        # H61: zero-division guard
        if prev_close <= 0.0:
            return None

        gap_pct = ((today_open - prev_close) / prev_close) * 100.0

        # Filter tiny gaps
        if abs(gap_pct) < self.min_gap_pct:
            self._gap = None
            return None

        direction = "up" if gap_pct > 0.0 else "down"

        # Classify by relative volume
        gap_type = classify_gap(rvol)

        # Fill target: 75% retracement toward previous close
        gap_distance = today_open - prev_close
        fill_target = today_open - (self.fill_pct * gap_distance)

        self._gap = GapInfo(
            direction=direction,
            gap_pct=gap_pct,
            gap_type=gap_type,
            fill_target=fill_target,
            is_filled=False,
            previous_close=prev_close,
            today_open=today_open,
        )
        return self._gap

    def check_fill(self, current_price: float) -> Optional[GapInfo]:
        """Check if the gap has been filled by the current price.

        For gap up: filled when price drops to or below fill_target.
        For gap down: filled when price rises to or above fill_target.

        Args:
            current_price: Current intraday price.

        Returns:
            Updated GapInfo with is_filled status, or None if no gap detected.
        """
        if self._gap is None:
            return None

        # Already filled -- keep the flag
        if self._gap.is_filled:
            return self._gap

        filled = False
        if self._gap.direction == "up":
            # Gap up fills when price drops to/below fill target
            filled = current_price <= self._gap.fill_target
        else:
            # Gap down fills when price rises to/above fill target
            filled = current_price >= self._gap.fill_target

        if filled:
            self._gap = self._gap._replace(is_filled=True)

        return self._gap

    def reset(self) -> None:
        """Reset for a new session."""
        self._gap = None


def classify_gap(rvol: float) -> str:
    """Classify a gap by relative volume at the open.

    Args:
        rvol: Relative volume (current volume / 20-day average volume).

    Returns:
        "information" if RVOL > 5x (institutional, news-driven -- tends to continue)
        "liquidity" if RVOL < 2x (low participation -- tends to fill)
        "uncertain" if 2x <= RVOL <= 5x
    """
    if rvol > 5.0:
        return "information"
    elif rvol < 2.0:
        return "liquidity"
    else:
        return "uncertain"


def calculate_gap_pct(prev_close: float, today_open: float) -> float:
    """Calculate gap size as a percentage.

    Args:
        prev_close: Yesterday's closing price.
        today_open: Today's opening price.

    Returns:
        Gap percentage. Positive = gap up, negative = gap down.
        Returns 0.0 if prev_close is zero (H61).
    """
    if prev_close <= 0.0:  # H61
        return 0.0
    return ((today_open - prev_close) / prev_close) * 100.0


def calculate_fill_target(
    prev_close: float,
    today_open: float,
    fill_pct: float = 0.75,
) -> float:
    """Calculate the price level for a gap fill.

    A 75% fill target means price needs to retrace 75% of the gap distance
    back toward the previous close.

    Args:
        prev_close: Yesterday's closing price.
        today_open: Today's opening price.
        fill_pct: Fraction of gap to fill (default 0.75).

    Returns:
        Fill target price level.
    """
    gap_distance = today_open - prev_close
    return today_open - (fill_pct * gap_distance)
