"""
Volume Analytics Module
=======================

Real-time volume tracking without persistent history. Computes:
1. volume_trend: Classification of volume activity (rising/flat/declining)
2. rvol: Current volume relative to 20-bar moving average
3. vol_divergence: Price rising while volume declining (for Type C confirmation)

Design: No persistent logging, memory-efficient, leverages intraday bars already in memory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("nzt48.core.volume_analytics")


@dataclass
class VolumeMetrics:
    """Current volume metrics for a ticker."""
    ticker: str
    volume_trend: str  # "rising", "flat", "declining"
    rvol: float  # current_vol / vol_ma20
    vol_divergence: bool  # volume declining while price rising (for Type C)
    confidence_boost_pct: float = 0.0  # Confidence adjustment from volume analysis


class VolumeAnalytics:
    """
    Real-time volume tracking (no persistent history).

    Computes volume metrics used for entry signal confirmation:
    - Type A: requires volume_trend == "rising" for confirmation
    - Type B: requires RVOL 2.0x-4.0x with rising volume
    - Type C: requires volume_trend != "rising" (divergence)
    """

    def __init__(self):
        """Initialize volume analytics with empty rolling windows."""
        self.vol_ma20_cache: Dict[str, float] = {}  # Per-ticker 20-bar MA
        self.last_volumes: Dict[str, List[float]] = {}  # Last 3-5 bars per ticker

    def compute_volume_trend(
        self,
        ticker: str,
        current_vol: float,
        vol_ma20: float,
        last_3_bars_vols: List[float],
    ) -> str:
        """
        Classify volume trend as "rising", "flat", or "declining".

        Logic:
        - RISING: current_vol > vol_ma20 × 1.8 AND last 2+ bars trending up
        - FLAT: vol_ma20 × 0.8 < current_vol < vol_ma20 × 1.8
        - DECLINING: current_vol < vol_ma20 × 0.8 AND last 2+ bars trending down

        Args:
            ticker: Stock ticker
            current_vol: Current bar volume
            vol_ma20: 20-bar moving average of volume
            last_3_bars_vols: List of last 3 bar volumes (most recent last)

        Returns:
            "rising" | "flat" | "declining"
        """
        if vol_ma20 <= 0:
            return "flat"

        vol_ratio = current_vol / vol_ma20

        # Analyze trend direction in last 3 bars
        bars_trending_up = 0
        bars_trending_down = 0

        if len(last_3_bars_vols) >= 3:
            # Compare consecutive bars: bars_trending_up counts upward transitions
            for i in range(1, len(last_3_bars_vols)):
                if last_3_bars_vols[i] > last_3_bars_vols[i-1]:
                    bars_trending_up += 1
                elif last_3_bars_vols[i] < last_3_bars_vols[i-1]:
                    bars_trending_down += 1

        # Classify based on vol_ratio + trend confirmation
        if vol_ratio > 1.8 and bars_trending_up >= 1:
            return "rising"
        elif vol_ratio < 0.8 and bars_trending_down >= 1:
            return "declining"
        else:
            return "flat"

    def compute_rvol(
        self,
        current_vol: float,
        vol_ma20: float,
    ) -> float:
        """
        Compute current volume relative to 20-bar MA.

        Returns:
            rvol: Current volume ÷ 20-bar MA (e.g., 2.5 = 2.5x baseline)
        """
        if vol_ma20 <= 0:
            return 0.3  # Conservative fallback if MA is zero

        return float(current_vol / vol_ma20)

    def compute_vol_divergence(
        self,
        current_close: float,
        previous_close: float,
        rvol: float,
    ) -> bool:
        """
        Detect volume divergence: price rising while volume declining.

        Used for Type C (overbought fade) confirmation.

        Logic:
        - Divergence detected when: price up AND volume declining (rvol < 1.5)
        - Example: New daily high but declining volume = exhaustion signal

        Args:
            current_close: Current bar close price
            previous_close: Previous bar close price
            rvol: Current RVOL value

        Returns:
            True if divergence detected (bullish price, bearish volume)
        """
        is_price_up = current_close > previous_close
        is_vol_declining = rvol < 1.5

        return is_price_up and is_vol_declining

    def compute_volume_urgency_score(
        self,
        rvol: float,
        vol_trend: str,
    ) -> float:
        """
        Compute urgency score for Type A entry confidence boost.

        Used to boost Type A confidence from 65% → 75% when volume is strongly confirming.

        Args:
            rvol: Current RVOL
            vol_trend: Volume trend classification

        Returns:
            Confidence boost in percentage points (0-10)
        """
        if vol_trend != "rising":
            return 0.0

        # RVOL 2.5x+ and rising trend = +10% confidence
        if rvol >= 2.5:
            return 10.0
        # RVOL 2.0x-2.5x and rising = +7% confidence
        elif rvol >= 2.0:
            return 7.0
        # RVOL 1.8x-2.0x and rising = +5% confidence
        elif rvol >= 1.8:
            return 5.0
        # RVOL 1.5x-1.8x and rising = +2% confidence
        else:
            return 2.0

    def compute_volume_confirmation_for_type_b(
        self,
        rvol: float,
        vol_trend: str,
        last_3_bars_rvols: List[float],
    ) -> tuple[bool, float]:
        """
        Multi-bar confirmation for Type B (Early Runner).

        Type B requires:
        1. RVOL 2.0x-4.0x (volume explosion)
        2. Last 3 bars showing rising RVOL (volume sustainability)

        Args:
            rvol: Current RVOL
            vol_trend: Volume trend classification
            last_3_bars_rvols: List of last 3 bar RVOL values

        Returns:
            (confirmed: bool, confidence_boost: float)
            - confirmed: True if multi-bar volume confirms the signal
            - confidence_boost: Additional confidence percentage if confirmed
        """
        # Check RVOL range
        if rvol < 2.0 or rvol > 4.5:
            return False, 0.0

        # Check trend
        if vol_trend != "rising":
            return False, 0.0

        # Check last 3 bars for sustained elevation
        if len(last_3_bars_rvols) < 3:
            # Not enough data, but if RVOL is in range and rising, give partial credit
            return True, 3.0

        # Count how many of last 3 bars exceed 2.0x
        bars_above_2x = sum(1 for v in last_3_bars_rvols if v >= 2.0)

        if bars_above_2x >= 2:
            # Strong multi-bar confirmation
            return True, 8.0
        elif bars_above_2x == 1:
            # Partial confirmation
            return True, 4.0
        else:
            # Single bar spike, not confirmed
            return False, 0.0

    def get_volume_metrics(
        self,
        ticker: str,
        current_vol: float,
        vol_ma20: float,
        current_close: float,
        previous_close: float,
        last_3_bars_vols: Optional[List[float]] = None,
    ) -> VolumeMetrics:
        """
        Compute all volume metrics in one call.

        Convenience method that computes volume_trend, rvol, and vol_divergence.

        Args:
            ticker: Stock ticker
            current_vol: Current bar volume
            vol_ma20: 20-bar moving average of volume
            current_close: Current bar close price
            previous_close: Previous bar close price
            last_3_bars_vols: Optional list of last 3 bar volumes

        Returns:
            VolumeMetrics dataclass with all computed values
        """
        if last_3_bars_vols is None:
            last_3_bars_vols = [current_vol]

        volume_trend = self.compute_volume_trend(
            ticker, current_vol, vol_ma20, last_3_bars_vols
        )
        rvol = self.compute_rvol(current_vol, vol_ma20)
        vol_divergence = self.compute_vol_divergence(
            current_close, previous_close, rvol
        )

        return VolumeMetrics(
            ticker=ticker,
            volume_trend=volume_trend,
            rvol=rvol,
            vol_divergence=vol_divergence,
            confidence_boost_pct=self.compute_volume_urgency_score(rvol, volume_trend),
        )

    def log_volume_analysis(
        self,
        ticker: str,
        metrics: VolumeMetrics,
        context: str = "",
    ) -> None:
        """Log volume analysis for debugging."""
        logger.debug(
            f"[{ticker}] {context} | "
            f"trend={metrics.volume_trend} "
            f"rvol={metrics.rvol:.2f}x "
            f"divergence={metrics.vol_divergence} "
            f"boost={metrics.confidence_boost_pct:.1f}%"
        )
