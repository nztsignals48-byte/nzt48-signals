"""
Perfect Entry Filter
====================
Purpose: Convert early detection confidence into position sizing percentage.

Takes raw confidence score (0-100%) from early_detection_engine and maps it to:
  100% of Kelly-sized position (very confident entry)
  75% of Kelly-sized position (moderately confident)
  50% of Kelly-sized position (marginal entry)
  0% (wait for better setup)

Philosophy: Better to enter smaller when less confident, than to miss entry entirely.
Also prevents overleveraging on marginal entries.

Integration:
  1. Early detection engine evaluates → confidence (0-100%)
  2. Perfect entry filter → entry_pct (0-100%)
  3. Position sizer calculates Kelly size → applies entry_pct
  4. Final position = kelly_size × entry_pct

Example:
  - Kelly says position should be £990 (3% of account)
  - Early detection says 72% confidence (good setup)
  - Perfect entry filter says 75% position
  - Actual trade: £990 × 0.75 = £742.50

Rationale:
  - Confidence 70-80% = solid setup, take full position
  - Confidence 65-70% = decent setup, take 75%
  - Confidence 60-65% = marginal, take 50% or skip
  - Confidence <60% = poor setup, skip entirely
"""

from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class EntryFilterResult:
    """Decision from perfect entry filter"""
    should_enter: bool  # True/False decision
    entry_pct: float  # Fraction of Kelly size (0-1.0)
    confidence_level: str  # "excellent", "good", "marginal", "skip"
    reason: str


class PerfectEntryFilter:
    """
    Filters entry based on confidence level.

    Scales position size from 0-100% based on setup quality.
    """

    def __init__(self):
        self.logger = logging.getLogger("nzt48.perfect_entry_filter")

    def is_perfect_entry(
        self,
        confidence_pct: float,
        direction: str = "BUY",
        entry_reason: str = "perfect_entry_timing"
    ) -> EntryFilterResult:
        """
        Convert confidence to entry decision and position sizing.

        Args:
            confidence_pct: Confidence score (0-100%) from early_detection_engine
            direction: "BUY" or "SELL" (informational)
            entry_reason: String describing why entry is considered

        Returns:
            EntryFilterResult with should_enter flag and entry_pct
        """

        # ===== CONFIDENCE THRESHOLDS =====

        if confidence_pct >= 75:
            # Excellent setup: clear signals across multiple tiers
            # Take full position
            entry_pct = 1.0
            confidence_level = "excellent"
            reason = f"{direction} at {confidence_pct:.0f}% confidence: excellent setup (full Kelly)"

        elif confidence_pct >= 70:
            # Very good setup: multiple strong signals
            # Take full position, very close to threshold
            entry_pct = 1.0
            confidence_level = "excellent"
            reason = f"{direction} at {confidence_pct:.0f}% confidence: very good setup (full Kelly)"

        elif confidence_pct >= 65:
            # Good setup: Tier 1 + Tier 2/3 present
            # This is the threshold for entry (from plan)
            # Take full position initially, but monitor closely
            entry_pct = 1.0
            confidence_level = "good"
            reason = f"{direction} at {confidence_pct:.0f}% confidence: meets minimum threshold"

        elif confidence_pct >= 62:
            # Decent setup: approaching threshold
            # Take 75% of position as precaution
            entry_pct = 0.75
            confidence_level = "marginal"
            reason = f"{direction} at {confidence_pct:.0f}% confidence: marginal, reduced position (75%)"

        elif confidence_pct >= 60:
            # Marginal setup: weak signals, below threshold
            # Take only 50% of position
            entry_pct = 0.50
            confidence_level = "marginal"
            reason = f"{direction} at {confidence_pct:.0f}% confidence: weak setup, small entry (50%)"

        elif confidence_pct >= 55:
            # Special case: Gap + Go exception (from early detection plan)
            # Lower threshold but still reduced position
            entry_pct = 0.75
            confidence_level = "special_case"
            reason = f"{direction} at {confidence_pct:.0f}% confidence: Gap+Go special case (75%)"

        else:
            # Poor setup: <55% confidence
            # Skip entirely, wait for better opportunity
            entry_pct = 0.0
            confidence_level = "skip"
            reason = f"{direction} at {confidence_pct:.0f}% confidence: insufficient conviction (SKIP)"

        should_enter = entry_pct > 0

        self.logger.info(
            f"Perfect Entry Filter: {direction} {confidence_pct:.0f}% → {confidence_level} "
            f"({entry_pct*100:.0f}%) — {reason}"
        )

        return EntryFilterResult(
            should_enter=should_enter,
            entry_pct=entry_pct,
            confidence_level=confidence_level,
            reason=reason
        )

    def apply_to_position_size(
        self,
        kelly_position_size: float,
        confidence_pct: float,
        direction: str = "BUY"
    ) -> float:
        """
        Apply entry filter to calculate actual position size.

        Args:
            kelly_position_size: Position size from Kelly criterion (in currency)
            confidence_pct: Confidence from early detection (0-100%)
            direction: "BUY" or "SELL"

        Returns:
            Actual position size after confidence filtering
        """

        filter_result = self.is_perfect_entry(confidence_pct, direction)

        actual_size = kelly_position_size * filter_result.entry_pct

        self.logger.info(
            f"Position sizing: Kelly £{kelly_position_size:.0f} × {filter_result.entry_pct*100:.0f}% "
            f"= £{actual_size:.0f} ({filter_result.confidence_level})"
        )

        return actual_size

    def confidence_to_confidence_level(self, confidence_pct: float) -> str:
        """Helper: convert numeric confidence to label"""

        if confidence_pct >= 75:
            return "excellent"
        elif confidence_pct >= 65:
            return "good"
        elif confidence_pct >= 60:
            return "marginal"
        elif confidence_pct >= 55:
            return "special_case"
        else:
            return "skip"


if __name__ == "__main__":
    print("="*70)
    print("PERFECT ENTRY FILTER TEST")
    print("="*70)

    filter_engine = PerfectEntryFilter()

    test_cases = [
        (78, "Excellent HIMS setup"),
        (72, "Very good QQQ momentum"),
        (68, "Good entry at threshold"),
        (63, "Marginal, reduced size"),
        (58, "Gap+Go special case"),
        (45, "Poor setup, skip"),
    ]

    for conf, desc in test_cases:
        print(f"\n{desc} (Confidence {conf}%)")
        print("-" * 70)

        result = filter_engine.is_perfect_entry(conf, "BUY", desc)

        print(f"Should enter: {result.should_enter}")
        print(f"Entry %: {result.entry_pct*100:.0f}%")
        print(f"Level: {result.confidence_level}")
        print(f"Reason: {result.reason}")

        # Simulate Kelly sizing
        kelly_size = 990.0  # 3% of £33k account
        actual_size = filter_engine.apply_to_position_size(kelly_size, conf, "BUY")

        print(f"Kelly: £{kelly_size:.0f} → Actual: £{actual_size:.0f}")

    print(f"\n{'='*70}")
    print("✅ PERFECT ENTRY FILTER TESTS COMPLETE")
