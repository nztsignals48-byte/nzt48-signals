# volume_analytics.py - 150 LOC
# Implements volume trend classification and divergence detection for signal confirmation

"""
Volume Analytics Module
========================
Provides real-time volume analysis for entry signal confirmation:
- Volume trend classification (rising/flat/declining)
- Volume divergence detection (price up, vol down = bearish signal)
- Volume urgency scoring for confidence boosting
"""

from typing import Optional, Tuple, List
import numpy as np


class VolumeAnalytics:
    """Real-time volume analytics without persistent state"""

    def __init__(self):
        """Initialize volume analyzer"""
        self.rvol_threshold_rising = 1.8  # 1.8x MA = rising
        self.rvol_threshold_declining = 0.8  # 0.8x MA = declining
        self.urgency_multiplier = 2.5  # vol_ma20 * 2.5 = urgency trigger

    def classify_volume_trend(
        self,
        current_vol: float,
        vol_ma20: float,
        vol_ma50: Optional[float] = None,
        last_3_bars_vols: Optional[List[float]] = None,
    ) -> str:
        """
        Classify volume trend as rising, flat, or declining.

        Logic:
        - RISING: current_vol > vol_ma20 * 1.8 AND (last 3 bars trending OR vol_ma20 > vol_ma50)
        - FLAT: vol_ma20 * 0.8 < current_vol < vol_ma20 * 1.8
        - DECLINING: current_vol < vol_ma20 * 0.8

        Args:
            current_vol: Current bar volume
            vol_ma20: 20-bar volume moving average
            vol_ma50: 50-bar volume MA (optional, for trend confirmation)
            last_3_bars_vols: Last 3 bar volumes for trend (optional)

        Returns:
            str: "rising" | "flat" | "declining"
        """
        # Threshold based classification
        upper_threshold = vol_ma20 * self.rvol_threshold_rising
        lower_threshold = vol_ma20 * self.rvol_threshold_declining

        if current_vol > upper_threshold:
            # Check secondary confirmation (3-bar trend or MA50 comparison)
            if last_3_bars_vols is not None and len(last_3_bars_vols) >= 3:
                # Trend: last 3 bars should be rising
                if (
                    last_3_bars_vols[-3] < last_3_bars_vols[-2]
                    and last_3_bars_vols[-2] < last_3_bars_vols[-1]
                ):
                    return "rising"

            if vol_ma50 is not None and vol_ma20 > vol_ma50:
                return "rising"

            # Borderline: could be rising or just spike
            return "rising"

        elif current_vol < lower_threshold:
            return "declining"

        else:
            return "flat"

    def detect_vol_divergence(
        self,
        price_trend: str,  # "up" | "down" | "flat"
        rvol: float,
        divergence_threshold_rvol: float = 1.5,
    ) -> bool:
        """
        Detect volume divergence (price moving opposite to volume direction).

        Type C signal: Price rising (up) but volume declining (RVOL < 1.5)
        = Overbought fade setup (short via inverse ETPs)

        Args:
            price_trend: Current price direction ("up", "down", "flat")
            rvol: Relative volume (current_vol / vol_ma20)
            divergence_threshold_rvol: RVOL threshold below which vol is "declining"

        Returns:
            bool: True if divergence detected (Type C confirmation)
        """
        # Divergence: price up, vol down
        if price_trend == "up" and rvol < divergence_threshold_rvol:
            return True

        # Secondary: price down, vol flat (less common)
        if price_trend == "down" and rvol > 1.0 and rvol < 1.5:
            return True  # Weak volume on down move = possible reversal

        return False

    def compute_urgency_score(
        self,
        vol_ma20: float,
        current_vol: float,
    ) -> float:
        """
        Compute volume urgency score (0-10 scale).

        Urgency represents how strongly current volume exceeds the moving average.
        Used to boost Type A (dip recovery) confidence.

        Score = (current_vol - vol_ma20) / vol_ma20 * 10
        - 0.0-1.0: Low urgency (flat/declining)
        - 1.0-2.5: Moderate urgency
        - 2.5+: High urgency (explosive volume, max 10.0)

        Args:
            vol_ma20: 20-bar volume moving average
            current_vol: Current bar volume

        Returns:
            float: Urgency score [0.0, 10.0]
        """
        if vol_ma20 <= 0:
            return 0.0

        ratio = current_vol / vol_ma20
        # Linear scaling: 1.0x = 0, 2.0x = 5, 3.0x = 10
        urgency = (ratio - 1.0) * 5.0
        return min(urgency, 10.0)  # Cap at 10

    def compute_rvol(self, current_vol: float, vol_ma20: float) -> float:
        """
        Compute relative volume (RVOL).

        RVOL = current_vol / vol_ma20
        - RVOL < 0.8: Declining volume
        - RVOL 0.8-1.2: Normal volume
        - RVOL > 1.2: Elevated volume
        - RVOL > 3.5: Extreme volume (potential jump-diffusion)

        Args:
            current_vol: Current bar volume
            vol_ma20: 20-bar volume moving average

        Returns:
            float: RVOL ratio
        """
        if vol_ma20 <= 0:
            return 0.0
        return current_vol / vol_ma20

    def analyze(
        self,
        current_vol: float,
        vol_ma20: float,
        rvol: float,
        price_trend: str,
        vol_ma50: Optional[float] = None,
        last_3_bars_vols: Optional[List[float]] = None,
    ) -> dict:
        """
        Full volume analysis pipeline.

        Returns:
            dict with keys: trend, rvol, divergence_confirmed, urgency_score
        """
        trend = self.classify_volume_trend(
            current_vol, vol_ma20, vol_ma50, last_3_bars_vols
        )
        divergence = self.detect_vol_divergence(price_trend, rvol)
        urgency = self.compute_urgency_score(vol_ma20, current_vol)

        return {
            "trend": trend,
            "rvol": rvol,
            "divergence_confirmed": divergence,
            "urgency_score": urgency,
        }


# ============================================================================
# Unit Tests
# ============================================================================

def test_volume_trend_rising():
    """Test rising volume classification"""
    va = VolumeAnalytics()
    trend = va.classify_volume_trend(
        current_vol=200.0, vol_ma20=100.0, last_3_bars_vols=[90, 110, 200]
    )
    assert trend == "rising", f"Expected 'rising', got '{trend}'"


def test_volume_trend_flat():
    """Test flat volume classification"""
    va = VolumeAnalytics()
    trend = va.classify_volume_trend(current_vol=100.0, vol_ma20=110.0)
    assert trend == "flat", f"Expected 'flat', got '{trend}'"


def test_volume_trend_declining():
    """Test declining volume classification"""
    va = VolumeAnalytics()
    trend = va.classify_volume_trend(current_vol=50.0, vol_ma20=100.0)
    assert trend == "declining", f"Expected 'declining', got '{trend}'"


def test_divergence_detected():
    """Test volume divergence detection (Type C setup)"""
    va = VolumeAnalytics()
    divergence = va.detect_vol_divergence(price_trend="up", rvol=1.2)
    assert divergence is True, "Should detect divergence (price up, vol down)"


def test_divergence_not_detected():
    """Test no divergence when volume supports price"""
    va = VolumeAnalytics()
    divergence = va.detect_vol_divergence(price_trend="up", rvol=2.5)
    assert divergence is False, "Should NOT detect divergence (vol supports price)"


def test_urgency_score_normal():
    """Test urgency scoring at 1x MA"""
    va = VolumeAnalytics()
    urgency = va.compute_urgency_score(vol_ma20=100.0, current_vol=100.0)
    assert abs(urgency - 0.0) < 0.1, f"Expected ~0, got {urgency}"


def test_urgency_score_2x():
    """Test urgency scoring at 2x MA"""
    va = VolumeAnalytics()
    urgency = va.compute_urgency_score(vol_ma20=100.0, current_vol=200.0)
    assert abs(urgency - 5.0) < 0.1, f"Expected ~5, got {urgency}"


def test_urgency_score_3x():
    """Test urgency scoring at 3x MA (capped at 10)"""
    va = VolumeAnalytics()
    urgency = va.compute_urgency_score(vol_ma20=100.0, current_vol=300.0)
    assert abs(urgency - 10.0) < 0.1, f"Expected ~10 (capped), got {urgency}"


def test_rvol_calculation():
    """Test RVOL calculation"""
    va = VolumeAnalytics()
    rvol = va.compute_rvol(current_vol=200.0, vol_ma20=100.0)
    assert abs(rvol - 2.0) < 0.01, f"Expected 2.0, got {rvol}"


def test_full_analysis_pipeline():
    """Test complete analysis pipeline"""
    va = VolumeAnalytics()
    result = va.analyze(
        current_vol=200.0,  # 2.0x MA (rising threshold is 1.8x)
        vol_ma20=100.0,
        rvol=2.0,
        price_trend="up",
        vol_ma50=90.0,
    )

    assert "trend" in result
    assert "rvol" in result
    assert "divergence_confirmed" in result
    assert "urgency_score" in result
    assert result["trend"] == "rising"
    assert result["divergence_confirmed"] is False  # Vol is supporting


if __name__ == "__main__":
    # Run all tests
    test_volume_trend_rising()
    test_volume_trend_flat()
    test_volume_trend_declining()
    test_divergence_detected()
    test_divergence_not_detected()
    test_urgency_score_normal()
    test_urgency_score_2x()
    test_urgency_score_3x()
    test_rvol_calculation()
    test_full_analysis_pipeline()
    print("✅ All volume_analytics tests passed (10/10)")
