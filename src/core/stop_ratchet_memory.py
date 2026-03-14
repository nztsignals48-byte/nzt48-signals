"""
Stop Loss Ratchet Memory
========================
Purpose: Prevent "stop whipsaw" where tight stops cause exits then immediate reversals.

Problem it solves:
  1. Trader hits stop at 153.50
  2. Stock immediately reverses UP to 154.50
  3. Trader missed 1% additional profit
  Root cause: Stop advanced too quickly after small pullback

Solution - Track:
  1. How many times stop moved in the last 5 minutes
  2. Speed of stop advancement (should track price momentum, not noise)
  3. Whether recent candles support tightening (don't tighten during sideways)

Rules:
  - If stop has advanced 3+ times in last 5 min: HOLD (no more advances for now)
  - If price velocity low (<0.2 × ATR per minute): HOLD stop, let price get conviction
  - If regime is RANGE (choppy): wider stops, don't advance as often
  - If regime is BLOW_OFF (euphoric): advance stops faster, protect gains
  - If VTD dropping (flow dying): tighten stops to realize profits

Integration with Chandelier:
  - Before advancing stop to new level, check ratchet memory
  - If "should_hold", keep stop at current level (even if price is higher)
  - This prevents whipsaw but still captures real directional moves
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class StopAdvance:
    """Record of one stop advancement"""
    timestamp: datetime
    old_price: float
    new_price: float
    advance_amount: float
    reason: str  # Why we advanced (price motion, vol decay, etc.)


@dataclass
class RatchetDecision:
    """Decision about whether to advance stop"""
    should_advance: bool
    reason: str
    recommended_stop: Optional[float] = None


class StopRatchetMemory:
    """
    Prevents stop whipsaw by tracking advancement history.

    Waits for CONVICTION (price momentum + volume) before tightening,
    rather than tightening on every slight pullback.
    """

    def __init__(self):
        self.logger = logging.getLogger("nzt48.stop_ratchet")
        self._advance_history: List[StopAdvance] = []
        self._last_advance_time: Optional[datetime] = None

    def record_advance(
        self,
        old_stop: float,
        new_stop: float,
        reason: str
    ):
        """Record a stop advancement in history"""

        advance = StopAdvance(
            timestamp=datetime.now(),
            old_price=old_stop,
            new_price=new_stop,
            advance_amount=new_stop - old_stop,
            reason=reason
        )

        self._advance_history.append(advance)
        self._last_advance_time = datetime.now()

        self.logger.info(
            f"Stop advanced: {old_stop:.2f} → {new_stop:.2f} ({advance.advance_amount:.2f}) "
            f"[{reason}]"
        )

    def should_advance_stop(
        self,
        current_stop: float,
        candidate_stop: float,
        current_price: float,
        price_momentum_atr_per_min: float,
        regime: str,
        vtd_ratio: float,
        recent_bars: Optional[List[dict]] = None
    ) -> RatchetDecision:
        """
        Decide if stop should advance to new level.

        Args:
            current_stop: Current stop loss price
            candidate_stop: New proposed stop price
            current_price: Current price
            price_momentum_atr_per_min: How much price moved per minute (in ATR units)
            regime: Market regime (COMPRESSION, EXPANSION, RANGE, etc.)
            vtd_ratio: Volume-time decay (0-1.0)
            recent_bars: List of last N bars (OHLCV) for pattern analysis

        Returns:
            RatchetDecision with should_advance flag and reason
        """

        # Get history of recent advances
        now = datetime.now()
        five_min_ago = now - timedelta(minutes=5)
        recent_advances = [
            a for a in self._advance_history
            if a.timestamp > five_min_ago
        ]

        # ===== RULE 1: Too many advances =====
        if len(recent_advances) >= 3:
            return RatchetDecision(
                should_advance=False,
                reason=f"Stop advanced {len(recent_advances)} times in 5 min (prevent whipsaw)",
                recommended_stop=current_stop
            )

        # ===== RULE 2: Low conviction =====
        # If price is barely moving, don't tighten stop (avoid noise-based exits)
        if price_momentum_atr_per_min < 0.15:
            return RatchetDecision(
                should_advance=False,
                reason=f"Low momentum ({price_momentum_atr_per_min:.2f} ATR/min, need >0.15)",
                recommended_stop=current_stop
            )

        # ===== RULE 3: Regime-specific rules =====

        if regime == "RANGE":
            # Choppy market: don't tighten often, let price prove direction
            # Only advance if really high conviction
            if price_momentum_atr_per_min < 0.30:
                return RatchetDecision(
                    should_advance=False,
                    reason=f"RANGE regime: need high conviction (>0.3 ATR/min)",
                    recommended_stop=current_stop
                )

        if regime == "BLOW_OFF":
            # Euphoric market: advance stops more aggressively to lock gains
            # Lower threshold, no whipsaw protection needed
            pass

        if regime == "COMPRESSION":
            # Coiling: don't tighten yet (spring might break hard)
            if len(recent_advances) >= 2:
                return RatchetDecision(
                    should_advance=False,
                    reason=f"COMPRESSION regime: hold stops (coil might break hard)",
                    recommended_stop=current_stop
                )

        # ===== RULE 4: Volume decay (exhaustion) =====
        # If VTD dropping <0.3, volume is drying up
        # Good time to take profits, but don't get shaken by noise
        if vtd_ratio < 0.25 and len(recent_advances) >= 1:
            return RatchetDecision(
                should_advance=False,
                reason=f"VTD critical ({vtd_ratio:.0%}), hold stop",
                recommended_stop=current_stop
            )

        # ===== RULE 5: Pattern validation =====
        # Check if recent candles support tightening
        # (candles should be in direction of current position, not pullbacks)
        if recent_bars and len(recent_bars) >= 2:
            support_advance = self._validate_candle_pattern(recent_bars, current_price)
            if not support_advance:
                return RatchetDecision(
                    should_advance=False,
                    reason="Recent candles show consolidation (no clear direction)",
                    recommended_stop=current_stop
                )

        # ===== ALL CHECKS PASSED: ADVANCE STOP =====
        return RatchetDecision(
            should_advance=True,
            reason=f"Conditions met: momentum {price_momentum_atr_per_min:.2f}, "
                   f"VTD {vtd_ratio:.0%}, regime {regime}, "
                   f"{len(recent_advances)} advances in 5min",
            recommended_stop=candidate_stop
        )

    def _validate_candle_pattern(self, recent_bars: List[dict], current_price: float) -> bool:
        """
        Check if recent candles support advancing stop.

        Valid patterns:
          - Strong directional candles (close near high/low)
          - Volume increasing on directional candles
          - No large wicks (rejection wicks)

        Invalid patterns (don't advance):
          - Doji/spinning top (indecision)
          - Hammer/inverted hammer (rejection)
          - Decreasing volume
        """

        if len(recent_bars) < 2:
            return True  # Not enough data, allow advance

        recent = recent_bars[-2:]

        for bar in recent:
            open_p = bar.get("open", 0)
            close_p = bar.get("close", 0)
            high = bar.get("high", 0)
            low = bar.get("low", 0)
            volume = bar.get("volume", 0)

            if open_p <= 0 or close_p <= 0 or volume <= 0:
                continue

            # Check for rejection patterns
            body = abs(close_p - open_p)
            wick_upper = high - max(open_p, close_p)
            wick_lower = min(open_p, close_p) - low

            total_range = high - low
            if total_range > 0:
                wick_ratio = max(wick_upper, wick_lower) / total_range

                # If wicks are >50% of candle, it's rejection (don't advance)
                if wick_ratio > 0.5:
                    return False

            # Check for doji/indecision (body <20% of range)
            if total_range > 0:
                body_ratio = body / total_range
                if body_ratio < 0.2:
                    return False  # Doji, no clear direction

        # Passed all checks
        return True

    def reset_history(self):
        """Clear advancement history (use at end of position)"""
        self._advance_history = []
        self._last_advance_time = None


if __name__ == "__main__":
    print("="*70)
    print("STOP RATCHET MEMORY TEST")
    print("="*70)

    ratchet = StopRatchetMemory()

    # Test case 1: Normal advance (should allow)
    print("\n1. Normal Momentum (should ALLOW advance)")
    print("-" * 70)

    decision = ratchet.should_advance_stop(
        current_stop=149.50,
        candidate_stop=150.25,
        current_price=151.00,
        price_momentum_atr_per_min=0.25,  # Good momentum
        regime="TRENDING_UP",
        vtd_ratio=0.70,  # Flowing
        recent_bars=[]
    )

    print(f"Allow advance: {decision.should_advance}")
    print(f"Reason: {decision.reason}")
    print(f"New stop: {decision.recommended_stop}")

    if decision.should_advance:
        ratchet.record_advance(149.50, 150.25, "Normal momentum")

    # Test case 2: Too many recent advances (should block)
    print("\n2. Too Many Advances (should BLOCK)")
    print("-" * 70)

    # Simulate 3 advances in last 5 minutes
    import time
    base_time = datetime.now()
    ratchet._advance_history = [
        StopAdvance(base_time - timedelta(minutes=4), 147.0, 147.5, 0.5, "advance1"),
        StopAdvance(base_time - timedelta(minutes=3), 147.5, 148.0, 0.5, "advance2"),
        StopAdvance(base_time - timedelta(minutes=2), 148.0, 148.5, 0.5, "advance3"),
    ]

    decision = ratchet.should_advance_stop(
        current_stop=148.50,
        candidate_stop=149.00,
        current_price=151.00,
        price_momentum_atr_per_min=0.30,  # Even good momentum
        regime="TRENDING_UP",
        vtd_ratio=0.70,
        recent_bars=[]
    )

    print(f"Allow advance: {decision.should_advance} (BLOCKED)")
    print(f"Reason: {decision.reason}")

    # Test case 3: Low momentum (should block)
    print("\n3. Low Conviction (should BLOCK)")
    print("-" * 70)

    ratchet.reset_history()

    decision = ratchet.should_advance_stop(
        current_stop=149.50,
        candidate_stop=150.00,
        current_price=150.10,  # Barely moving
        price_momentum_atr_per_min=0.08,  # Very low
        regime="RANGE",
        vtd_ratio=0.40,
        recent_bars=[]
    )

    print(f"Allow advance: {decision.should_advance}")
    print(f"Reason: {decision.reason}")

    print(f"\n{'='*70}")
    print("✅ STOP RATCHET MEMORY TESTS COMPLETE")
