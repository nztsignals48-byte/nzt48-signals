# indicators.py - 45 LOC
# Implements Stochastic RSI for momentum confirmation

"""
Technical Indicators Module
============================
Provides Stochastic RSI for Type B (Early Runner) confirmation.
"""

from typing import List, Optional


class StochasticRSI:
    """Stochastic RSI calculator"""

    def __init__(self, period: int = 14):
        """
        Initialize Stochastic RSI calculator.

        Args:
            period: Window size for min/max calculation (default 14)
        """
        self.period = period

    def calculate(self, rsi_values: List[float]) -> Optional[float]:
        """
        Calculate Stochastic RSI from RSI series.

        Formula: StochRSI = (current_rsi - min_rsi_14) / (max_rsi_14 - min_rsi_14) * 100

        Interpretation:
        - <20: Oversold (momentum bottoming)
        - 40-70: Momentum without overbought (Type B sweet spot)
        - >80: Overbought (Type C setup, short via inverse ETPs)

        Args:
            rsi_values: List of RSI values (minimum period length)

        Returns:
            float [0, 100] or None if insufficient data
        """
        if len(rsi_values) < self.period:
            return None

        # Get last 'period' RSI values
        recent_rsis = rsi_values[-self.period :]

        # Find min and max
        min_rsi = min(recent_rsis)
        max_rsi = max(recent_rsis)

        # Current RSI (last value)
        current_rsi = rsi_values[-1]

        # Calculate Stochastic RSI
        range_rsi = max_rsi - min_rsi
        if range_rsi == 0:
            return 50.0  # Flat RSI -> neutral

        stoch_rsi = ((current_rsi - min_rsi) / range_rsi) * 100.0
        return stoch_rsi

    def is_type_b_eligible(self, stoch_rsi: Optional[float]) -> bool:
        """
        Check if Stochastic RSI confirms Type B (Early Runner) entry.

        Type B requires momentum without overbought conditions:
        - 40 <= StochRSI <= 70 (sweet spot)

        Args:
            stoch_rsi: Stochastic RSI value or None

        Returns:
            bool: True if Type B eligible
        """
        if stoch_rsi is None:
            return False

        return 40.0 <= stoch_rsi <= 70.0

    def is_overbought(self, stoch_rsi: Optional[float]) -> bool:
        """
        Check if market is overbought (Type C setup).

        Args:
            stoch_rsi: Stochastic RSI value or None

        Returns:
            bool: True if StochRSI > 80 (overbought)
        """
        if stoch_rsi is None:
            return False

        return stoch_rsi > 80.0

    def is_oversold(self, stoch_rsi: Optional[float]) -> bool:
        """
        Check if market is oversold (Type A setup).

        Args:
            stoch_rsi: Stochastic RSI value or None

        Returns:
            bool: True if StochRSI < 20 (oversold)
        """
        if stoch_rsi is None:
            return False

        return stoch_rsi < 20.0


# ============================================================================
# Unit Tests
# ============================================================================

def test_stoch_rsi_calculation():
    """Test basic Stochastic RSI calculation"""
    srsi = StochasticRSI()
    rsi_values = [30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 92]
    stoch = srsi.calculate(rsi_values)

    assert stoch is not None
    assert 0 <= stoch <= 100, f"StochRSI should be [0, 100], got {stoch}"


def test_stoch_rsi_insufficient_data():
    """Test StochRSI with insufficient data"""
    srsi = StochasticRSI()
    rsi_values = [50, 51, 52]
    stoch = srsi.calculate(rsi_values)

    assert stoch is None, "Should return None with insufficient data"


def test_stoch_rsi_flat():
    """Test StochRSI when RSI is flat"""
    srsi = StochasticRSI()
    rsi_values = [50.0] * 14  # Flat RSI at 50

    stoch = srsi.calculate(rsi_values)
    assert abs(stoch - 50.0) < 0.1, f"Flat RSI should give StochRSI ~50, got {stoch}"


def test_type_b_eligible():
    """Test Type B confirmation (40-70 range)"""
    srsi = StochasticRSI()
    assert srsi.is_type_b_eligible(45.0) is True
    assert srsi.is_type_b_eligible(70.0) is True
    assert srsi.is_type_b_eligible(39.9) is False
    assert srsi.is_type_b_eligible(70.1) is False


def test_overbought_detection():
    """Test overbought detection (>80)"""
    srsi = StochasticRSI()
    assert srsi.is_overbought(85.0) is True
    assert srsi.is_overbought(80.1) is True
    assert srsi.is_overbought(80.0) is False


def test_oversold_detection():
    """Test oversold detection (<20)"""
    srsi = StochasticRSI()
    assert srsi.is_oversold(15.0) is True
    assert srsi.is_oversold(19.9) is True
    assert srsi.is_oversold(20.0) is False


if __name__ == "__main__":
    # Run all tests
    test_stoch_rsi_calculation()
    test_stoch_rsi_insufficient_data()
    test_stoch_rsi_flat()
    test_type_b_eligible()
    test_overbought_detection()
    test_oversold_detection()
    print("✅ All indicators tests passed (6/6)")
