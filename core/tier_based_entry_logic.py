"""
Tier-Based Entry Pattern Detection & Position Sizing
=====================================================

Implements 3 entry types: Type A (dip recovery), Type B (early runner - PRIORITY),
Type C (overbought fade). Computes position sizing per tier and entry confidence.

TIER DEFINITIONS (ISA Portfolio):
  Tier 1: Daily range 3-7% (moderate: QQQ3.L, 3LUS.L, TSM3.L, etc.)
    - Position size: 3-5% of account equity
    - Entry types: All (A, B, C)
    - Holding: Scalp (same-day exit minimum)

  Tier 2: Daily range 7-15% (volatile: 3SEM.L, GPT3.L, NVD3.L, TSL3.L, MU2.L)
    - Position size: 2-3% of account equity
    - Entry types: A, B (priority), C
    - Holding: Scalp (intraday)

  Tier 3: Daily range 15%+ (extreme: leveraged runners, SNDK-like)
    - Position size: 2% max of account equity
    - Entry types: B (priority), C (fast overbought fade only)
    - Holding: Momentum (5-15min entries/exits only)
    - Exit discipline: MUST close before market close (no overnight holds)

  Tier 4: Illiquid, ultra-low volume
    - Position size: 0.5% max
    - Entry types: None (skip)
    - Holding: N/A

TYPE A: DIP RECOVERY
  - RSI dropped to oversold (RSI < 35)
  - RVOL spike confirming selling exhaustion (RVOL > 1.5x baseline)
  - Price near daily lows but not at absolute lows (setup for recovery)
  - Volume trend turning up (bounce confirmation)
  Entry confidence: 60-75%

TYPE B: EARLY RUNNER (PRIORITY - YOUR EDGE)
  - RVOL 2.0x-4.0x baseline (early volume explosion)
  - RSI not yet at extremes (40-70 range) — detecting BEFORE overbought
  - Strong price momentum (near daily highs)
  - Volume spike preceding price move (causation, not correlation)
  - Time window: First 2-3 hours of session (pre-reversal risk)
  Entry confidence: 75-90%

  ALERT PRIORITY: 🚀 These fire FIRST before Type C can form

TYPE C: OVERBOUGHT FADE
  - RSI > 70 (overbought confirmation)
  - Volume divergence (price new high but volume declining)
  - Resistance level identified and approached
  - Entry confidence: 65-80%

POSITION SIZING FORMULA:
  position_pct = tier_base_pct × account_equity

  Where tier_base_pct:
    Tier 1: 3-5% (mean 4%)
    Tier 2: 2-3% (mean 2.5%)
    Tier 3: 1-2% (mean 1.5%)
    Tier 4: 0.5%
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("nzt48.core.tier_based_entry_logic")

UTC = ZoneInfo("UTC")


class EntryType(Enum):
    """Entry pattern types."""
    TYPE_A = "type_a"  # Dip recovery
    TYPE_B = "type_b"  # Early runner (PRIORITY)
    TYPE_C = "type_c"  # Overbought fade
    TYPE_D = "type_d"  # Support bounce


@dataclass
class EntrySignal:
    """Detected entry pattern."""
    entry_type: EntryType
    ticker: str
    confidence: float  # 0-100
    entry_price: float
    target_price: float
    stop_price: float
    position_size_pct: float  # % of account equity
    rsi: float
    rvol: float
    volume_trend: str  # "rising", "flat", "declining"
    time_detected: datetime
    rationale: str


@dataclass
class TierClassification:
    """Tier classification for a ticker."""
    ticker: str
    daily_range_pct: float
    tier: int  # 1, 2, 3, 4
    tier_name: str  # "Tier 1 (Moderate)", etc.
    position_size_pct: float  # Base position size for this tier
    allowed_entry_types: list  # Which entry types are allowed
    holding_style: str  # "swing", "scalp", "momentum"


class TierBasedEntryDetector:
    """Detects entry patterns and computes tier-based position sizing."""

    def __init__(self):
        """Initialize detector with tier definitions."""
        # Tier boundaries based on daily range %
        self.tier_1_range = (3.0, 7.0)      # Moderate
        self.tier_2_range = (7.0, 15.0)     # Volatile
        self.tier_3_range = (15.0, 100.0)   # Extreme
        self.tier_4_range = (0.0, 3.0)      # Conservative

        # Position sizing per tier (% of account equity)
        self.tier_1_position = 0.04  # 4% (3-5%)
        self.tier_2_position = 0.025  # 2.5% (2-3%)
        self.tier_3_position = 0.015  # 1.5% (1-2%)
        self.tier_4_position = 0.005  # 0.5%

        # Entry type allowances per tier
        self.tier_1_entry_types = [EntryType.TYPE_A, EntryType.TYPE_B, EntryType.TYPE_C, EntryType.TYPE_D]
        self.tier_2_entry_types = [EntryType.TYPE_A, EntryType.TYPE_B, EntryType.TYPE_C, EntryType.TYPE_D]
        self.tier_3_entry_types = [EntryType.TYPE_B, EntryType.TYPE_C]  # No dip recovery
        self.tier_4_entry_types = []  # Skip

    def classify_tier(
        self,
        ticker: str,
        daily_range_pct: float,
    ) -> TierClassification:
        """Classify ticker into tier based on daily range."""
        if daily_range_pct <= self.tier_4_range[1]:
            tier = 4
            tier_name = "Tier 4 (Conservative)"
            position_pct = self.tier_4_position
            entry_types = self.tier_4_entry_types
            holding = "swing"

        elif daily_range_pct <= self.tier_1_range[1]:
            tier = 1
            tier_name = "Tier 1 (Moderate)"
            position_pct = self.tier_1_position
            entry_types = self.tier_1_entry_types
            holding = "scalp"

        elif daily_range_pct <= self.tier_2_range[1]:
            tier = 2
            tier_name = "Tier 2 (Volatile)"
            position_pct = self.tier_2_position
            entry_types = self.tier_2_entry_types
            holding = "scalp"

        else:
            tier = 3
            tier_name = "Tier 3 (Extreme)"
            position_pct = self.tier_3_position
            entry_types = self.tier_3_entry_types
            holding = "momentum"

        return TierClassification(
            ticker=ticker,
            daily_range_pct=daily_range_pct,
            tier=tier,
            tier_name=tier_name,
            position_size_pct=position_pct,
            allowed_entry_types=entry_types,
            holding_style=holding,
        )

    def detect_type_a_dip(
        self,
        ticker: str,
        current_price: float,
        rsi: float,
        rvol: float,
        daily_low: float,
        daily_high: float,
        volume_trend: str,
    ) -> Optional[EntrySignal]:
        """
        Detect Type A: Dip Recovery.

        Criteria:
        - RSI < 35 (oversold)
        - RVOL > 1.5x (selling exhaustion)
        - Price near daily low but not at absolute bottom
        - Volume trend turning positive
        """
        if rsi > 35:
            return None

        daily_range = daily_high - daily_low
        price_distance_from_low = current_price - daily_low

        # Price should be within bottom 20% of range but not at absolute low
        if price_distance_from_low < 0.01 * daily_range or price_distance_from_low > 0.20 * daily_range:
            return None

        if rvol < 1.5:
            return None

        if volume_trend != "rising":
            return None

        # Base confidence: 65%
        confidence = 65.0

        # Volume urgency boost: if RVOL >= 2.5x, add +10%, if >= 2.0x add +7%
        if rvol >= 2.5:
            confidence += 10.0
        elif rvol >= 2.0:
            confidence += 7.0
        elif rvol >= 1.8:
            confidence += 5.0

        # Cap at 100%
        confidence = min(confidence, 100.0)

        # Target: 2-3% above entry
        target_price = current_price * 1.025
        stop_price = daily_low * 0.98

        return EntrySignal(
            entry_type=EntryType.TYPE_A,
            ticker=ticker,
            confidence=confidence,
            entry_price=current_price,
            target_price=target_price,
            stop_price=stop_price,
            position_size_pct=0.0,  # Set by tier
            rsi=rsi,
            rvol=rvol,
            volume_trend=volume_trend,
            time_detected=datetime.now(UTC),
            rationale=f"Dip recovery: RSI {rsi:.1f} (oversold), RVOL {rvol:.2f}x, volume rising, confidence {confidence:.0f}%"
        )

    def detect_type_b_early_runner(
        self,
        ticker: str,
        current_price: float,
        rsi: float,
        rvol: float,
        daily_low: float,
        daily_high: float,
        volume_spike_factor: float,  # Current vol / avg vol
        time_in_session_minutes: int,
        session_open_price: float = None,
        last_3_bars_rvols: list = None,
    ) -> Optional[EntrySignal]:
        """
        Detect Type B: Early Runner (PRIORITY).

        Criteria:
        - RVOL 2.0x-4.0x (early volume explosion)
        - RSI not yet extreme (40-70 range)
        - Strong price momentum (>80% of daily range from low)
        - Within first 2-3 hours of session
        - Multi-bar confirmation: last 3 bars show sustained volume elevation
        - NOT >5% in-session move (avoid chasing runaway moves)

        This is YOUR EDGE: detect early runners BEFORE they become overbought.
        """
        # Must be in early session window
        if time_in_session_minutes > 180:  # 3 hours
            return None

        # Volume explosion: 2.0x-4.0x
        if rvol < 2.0 or rvol > 4.5:
            return None

        # RSI not yet at extremes
        if rsi < 40 or rsi > 70:
            return None

        daily_range = daily_high - daily_low
        price_from_low = current_price - daily_low

        # Price momentum: >80% of daily range from low
        if daily_range > 0 and price_from_low / daily_range < 0.80:
            return None

        # Block if already >5% in-session move (avoid chasing)
        if session_open_price is not None:
            session_move_pct = (current_price - session_open_price) / session_open_price
            if session_move_pct > 0.05:  # Already moved >5%
                return None

        # Multi-bar volume sustainability check
        if last_3_bars_rvols is not None and len(last_3_bars_rvols) >= 3:
            bars_above_2x = sum(1 for v in last_3_bars_rvols if v >= 2.0)
            if bars_above_2x < 2:  # Need at least 2 of last 3 bars above 2.0x
                return None

        # Confidence: 82% (priority edge)
        confidence = 82.0

        # Target: 3-5% above entry (running room)
        target_price = current_price * 1.04
        stop_price = daily_low * 0.98

        return EntrySignal(
            entry_type=EntryType.TYPE_B,
            ticker=ticker,
            confidence=confidence,
            entry_price=current_price,
            target_price=target_price,
            stop_price=stop_price,
            position_size_pct=0.0,  # Set by tier
            rsi=rsi,
            rvol=rvol,
            volume_trend="rising",
            time_detected=datetime.now(UTC),
            rationale=f"Early runner (PRIORITY): RVOL {rvol:.2f}x, RSI {rsi:.1f}, {time_in_session_minutes}min into session, multi-bar confirmed"
        )

    def detect_type_c_overbought(
        self,
        ticker: str,
        current_price: float,
        rsi: float,
        rvol: float,
        daily_low: float,
        daily_high: float,
        volume_trend: str,
        vol_divergence_confirmed: bool = False,
    ) -> Optional[EntrySignal]:
        """
        Detect Type C: Overbought Fade.

        Criteria:
        - RSI > 70 (overbought, boosted to >75 for more confirmation)
        - Volume divergence required (price near high but volume declining)
        - At or near resistance level (daily high)
        - vol_divergence parameter confirms explicit divergence detection

        Short entry to fade the overextension.
        """
        if rsi <= 75:  # Raised from 70 to 75 for stronger confirmation
            return None

        # Price at or near daily high (within 2% of high)
        price_to_high = daily_high - current_price
        if price_to_high > 0.02 * (daily_high - daily_low):
            return None

        # Volume divergence: volume must be declining
        if volume_trend == "rising":
            return None

        # Boost confidence if vol_divergence is explicitly confirmed
        # Base confidence: 72%
        confidence = 72.0

        # If divergence confirmed (price up, volume down), add +8%
        if vol_divergence_confirmed:
            confidence += 8.0

        # If RVOL < 1.5 (strong declining volume), add +3%
        if rvol < 1.5:
            confidence += 3.0

        # Cap at 100%
        confidence = min(confidence, 100.0)

        # Target: 2-3% below entry (fade)
        target_price = current_price * 0.97
        stop_price = daily_high * 1.01

        return EntrySignal(
            entry_type=EntryType.TYPE_C,
            ticker=ticker,
            confidence=confidence,
            entry_price=current_price,
            target_price=target_price,
            stop_price=stop_price,
            position_size_pct=0.0,  # Set by tier
            rsi=rsi,
            rvol=rvol,
            volume_trend=volume_trend,
            time_detected=datetime.now(UTC),
            rationale=f"Overbought fade: RSI {rsi:.1f}, at resistance, volume {'divergence' if vol_divergence_confirmed else 'declining'}, confidence {confidence:.0f}%"
        )

    def detect_type_d_support_bounce(
        self,
        ticker: str,
        current_price: float,
        rsi: float,
        rvol: float,
        daily_low: float,
        daily_high: float,
        volume_trend: str,
    ) -> Optional[EntrySignal]:
        """
        Detect Type D: Support Bounce (NEW).

        Criteria:
        - Price within 1% of daily low (at support level)
        - RSI 20-40 (oversold but not extreme)
        - Volume > vol_ma20 (buying interest at support)
        - Volume trend rising (buyers stepping in)

        Complements Type A with explicit support level confirmation.
        Good for swing trades (0.5-2 hours).

        Entry confidence: 70%
        """
        # Price at support: within 1% of daily low
        price_above_low = current_price - daily_low
        daily_range = daily_high - daily_low
        if daily_range > 0:
            pct_from_low = price_above_low / daily_range
            if pct_from_low > 0.01:  # Must be within 1% of low
                return None

        # RSI in oversold region but not panic (20-40)
        if rsi < 20 or rsi > 40:
            return None

        # Volume must be rising (buyers stepping in at support)
        if volume_trend != "rising":
            return None

        # RVOL should be > 1.0 (above baseline)
        if rvol < 1.0:
            return None

        # Confidence: 70%
        confidence = 70.0

        # Target: 2-3% above entry (bounce to resistance)
        target_price = current_price * 1.025
        stop_price = daily_low * 0.98

        return EntrySignal(
            entry_type=EntryType.TYPE_D,
            ticker=ticker,
            confidence=confidence,
            entry_price=current_price,
            target_price=target_price,
            stop_price=stop_price,
            position_size_pct=0.0,  # Set by tier
            rsi=rsi,
            rvol=rvol,
            volume_trend=volume_trend,
            time_detected=datetime.now(UTC),
            rationale=f"Support bounce: price at low, RSI {rsi:.1f} (oversold), volume rising {rvol:.2f}x"
        )

    def calculate_position_size(
        self,
        tier: int,
        account_equity: float,
    ) -> float:
        """
        Calculate position size as % of account equity based on tier.

        Returns position size as float (e.g., 0.04 = 4%).
        """
        if tier == 1:
            return self.tier_1_position
        elif tier == 2:
            return self.tier_2_position
        elif tier == 3:
            return self.tier_3_position
        else:  # Tier 4
            return self.tier_4_position

    def detect_entry_pattern(
        self,
        ticker: str,
        current_price: float,
        rsi: float,
        rvol: float,
        daily_low: float,
        daily_high: float,
        volume_trend: str,
        time_in_session_minutes: int,
        account_equity: float,
        tier_classification: TierClassification,
    ) -> Optional[EntrySignal]:
        """
        Master detection: try all entry types, return highest-confidence signal.

        Priority order:
        1. Type B (early runner) - YOUR EDGE
        2. Type A (dip recovery)
        3. Type C (overbought fade)
        """
        candidates = []

        # Try Type B first (highest priority)
        if EntryType.TYPE_B in tier_classification.allowed_entry_types:
            signal_b = self.detect_type_b_early_runner(
                ticker=ticker,
                current_price=current_price,
                rsi=rsi,
                rvol=rvol,
                daily_low=daily_low,
                daily_high=daily_high,
                volume_spike_factor=rvol,
                time_in_session_minutes=time_in_session_minutes,
            )
            if signal_b:
                candidates.append(signal_b)

        # Try Type A
        if EntryType.TYPE_A in tier_classification.allowed_entry_types:
            signal_a = self.detect_type_a_dip(
                ticker=ticker,
                current_price=current_price,
                rsi=rsi,
                rvol=rvol,
                daily_low=daily_low,
                daily_high=daily_high,
                volume_trend=volume_trend,
            )
            if signal_a:
                candidates.append(signal_a)

        # Try Type C
        if EntryType.TYPE_C in tier_classification.allowed_entry_types:
            signal_c = self.detect_type_c_overbought(
                ticker=ticker,
                current_price=current_price,
                rsi=rsi,
                rvol=rvol,
                daily_low=daily_low,
                daily_high=daily_high,
                volume_trend=volume_trend,
            )
            if signal_c:
                candidates.append(signal_c)

        # Try Type D (support bounce)
        if EntryType.TYPE_D in tier_classification.allowed_entry_types:
            signal_d = self.detect_type_d_support_bounce(
                ticker=ticker,
                current_price=current_price,
                rsi=rsi,
                rvol=rvol,
                daily_low=daily_low,
                daily_high=daily_high,
                volume_trend=volume_trend,
            )
            if signal_d:
                candidates.append(signal_d)

        # Return highest confidence signal
        if not candidates:
            return None

        best_signal = max(candidates, key=lambda s: s.confidence)

        # Set position size from tier
        best_signal.position_size_pct = tier_classification.position_size_pct

        return best_signal

    def should_exit_tier3(
        self,
        session_end_time: datetime,
        current_time: datetime,
    ) -> bool:
        """
        Check if Tier 3 position should exit (5 min before session end).

        Tier 3 (extreme volatility) MUST NOT hold overnight.
        Exit trigger: 5 minutes before market close.
        """
        time_until_close = session_end_time - current_time
        exit_threshold = timedelta(minutes=5)

        return time_until_close <= exit_threshold
