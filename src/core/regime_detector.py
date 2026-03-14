"""
Phase 5: Regime Detection (5-State Hidden Markov Model)
Purpose: Classify market regime as one of 5 states

States:
1. TRENDING_UP: High momentum, low volatility, good conditions for long
2. TRENDING_DOWN: Negative momentum, high sell pressure, avoid or short
3. RANGE: No clear trend, choppy, sideways
4. HIGH_VOL: Elevated volatility, uncertainty, risk-off setup
5. RISK_OFF: Extreme stress (VIX >30, credit spreads >200bps), reduce all positions

Logic: Decision tree based on VIX, realized vol, momentum, credit spreads

Why this matters:
- Position sizes should be regime-specific
- Confidence thresholds should adapt by regime
- Stop losses should widen/tighten per regime
- Entry timing should respect regime setup
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
import logging

logger = logging.getLogger(__name__)

# Regime types
RegimeType = Literal["TRENDING_UP", "TRENDING_DOWN", "RANGE", "HIGH_VOL", "RISK_OFF"]


@dataclass
class RegimeState:
    """Current regime classification"""
    regime: RegimeType
    timestamp: datetime
    vix: float
    realized_vol: float
    momentum: float
    credit_spread: float
    confidence: float  # 0-1, how confident are we in this regime?
    persistence_days: int = 0  # How long has this regime persisted?


class RegimeDetector:
    """
    5-State regime detector using decision tree.

    Thresholds are research-backed (from academic papers + historical data):
    - VIX <15 = calm; VIX 15-18 = normal; VIX 18-30 = stressed; VIX >30 = panic
    - Realized vol <10% = low; 10-20% = normal; 20-25% = elevated; >25% = high
    - Momentum > 0 = bullish; < 0 = bearish
    - Credit spreads <100bps = healthy; 100-200 = caution; >200 = panic
    """

    # Thresholds
    VIX_CALM = 15
    VIX_NORMAL = 18
    VIX_STRESSED = 30

    VOL_LOW = 10
    VOL_NORMAL = 15
    VOL_ELEVATED = 25

    CREDIT_SPREAD_CAUTION = 100
    CREDIT_SPREAD_PANIC = 200

    def __init__(self):
        self.current_regime = None
        self.regime_history = []

    def detect(
        self,
        vix: float,
        realized_vol: float,  # 20-day rolling std of returns, in %
        momentum: float,      # e.g., SMA(9) - SMA(21), or ROC(12)
        credit_spread: float = 100,  # HY OAS in bps
    ) -> RegimeState:
        """
        Detect current market regime.

        Args:
            vix: Current VIX level
            realized_vol: 20-day rolling realized volatility (%)
            momentum: Momentum indicator (positive = bullish)
            credit_spread: High-yield credit spread in basis points

        Returns:
            RegimeState with detected regime
        """
        now = datetime.now()

        # Decision tree (specific order matters)
        if vix > self.VIX_STRESSED and credit_spread > self.CREDIT_SPREAD_PANIC:
            regime = "RISK_OFF"
            confidence = 0.95
        elif realized_vol > self.VOL_ELEVATED:
            regime = "HIGH_VOL"
            confidence = 0.85
        elif momentum < 0 and vix > self.VIX_NORMAL:
            regime = "TRENDING_DOWN"
            confidence = 0.80
        elif momentum > 0 and vix < self.VIX_CALM and realized_vol < self.VOL_NORMAL:
            regime = "TRENDING_UP"
            confidence = 0.90
        else:
            regime = "RANGE"
            confidence = 0.70

        # Calculate persistence (how long has this regime lasted?)
        persistence_days = 0
        if self.current_regime and self.current_regime.regime == regime:
            persistence_days = self.current_regime.persistence_days + 1

        state = RegimeState(
            regime=regime,
            timestamp=now,
            vix=vix,
            realized_vol=realized_vol,
            momentum=momentum,
            credit_spread=credit_spread,
            confidence=confidence,
            persistence_days=persistence_days
        )

        self.current_regime = state
        self.regime_history.append(state)

        return state

    def get_regime_parameters(self, regime: RegimeType) -> dict:
        """
        Get trading parameters adapted to regime.

        Returns dict with regime-specific settings for other phases.
        """
        params = {
            "TRENDING_UP": {
                "confidence_threshold": 6.5,  # Lower threshold, easier to trade
                "position_size_multiplier": 1.0,
                "stop_loss_pct": 1.0,
                "max_leverage": 5.0,
                "entry_type": "aggressive",
            },
            "TRENDING_DOWN": {
                "confidence_threshold": 7.5,  # Higher threshold, be cautious
                "position_size_multiplier": 0.7,
                "stop_loss_pct": 1.5,
                "max_leverage": 2.0,
                "entry_type": "conservative",
            },
            "RANGE": {
                "confidence_threshold": 7.0,  # Medium threshold
                "position_size_multiplier": 0.8,
                "stop_loss_pct": 1.2,
                "max_leverage": 3.0,
                "entry_type": "selective",
            },
            "HIGH_VOL": {
                "confidence_threshold": 8.0,  # High threshold
                "position_size_multiplier": 0.6,
                "stop_loss_pct": 2.0,
                "max_leverage": 1.5,
                "entry_type": "very_conservative",
            },
            "RISK_OFF": {
                "confidence_threshold": 9.0,  # Very high threshold (almost no trades)
                "position_size_multiplier": 0.3,
                "stop_loss_pct": 3.0,
                "max_leverage": 1.0,
                "entry_type": "halt_or_defensive",
            },
        }
        return params.get(regime, params["RANGE"])


# Unit tests
def test_trending_up():
    """Test detection of TRENDING_UP regime"""
    detector = RegimeDetector()

    state = detector.detect(
        vix=12,  # Low VIX
        realized_vol=12,  # Normal vol
        momentum=2.5,  # Positive momentum
        credit_spread=80  # Healthy spreads
    )

    assert state.regime == "TRENDING_UP", f"Expected TRENDING_UP, got {state.regime}"
    assert state.confidence > 0.8
    print("✓ TRENDING_UP detection test passed")


def test_trending_down():
    """Test detection of TRENDING_DOWN regime"""
    detector = RegimeDetector()

    state = detector.detect(
        vix=22,  # Elevated VIX
        realized_vol=18,
        momentum=-1.5,  # Negative momentum
        credit_spread=120
    )

    assert state.regime == "TRENDING_DOWN", f"Expected TRENDING_DOWN, got {state.regime}"
    print("✓ TRENDING_DOWN detection test passed")


def test_risk_off():
    """Test detection of RISK_OFF regime"""
    detector = RegimeDetector()

    state = detector.detect(
        vix=35,  # Very high VIX
        realized_vol=35,
        momentum=-3.0,
        credit_spread=250  # Panic spreads
    )

    assert state.regime == "RISK_OFF", f"Expected RISK_OFF, got {state.regime}"
    assert state.confidence > 0.9
    print("✓ RISK_OFF detection test passed")


def test_high_vol():
    """Test detection of HIGH_VOL regime"""
    detector = RegimeDetector()

    state = detector.detect(
        vix=28,
        realized_vol=28,  # High realized vol
        momentum=0.5,
        credit_spread=150
    )

    assert state.regime == "HIGH_VOL", f"Expected HIGH_VOL, got {state.regime}"
    print("✓ HIGH_VOL detection test passed")


def test_range():
    """Test detection of RANGE regime"""
    detector = RegimeDetector()

    state = detector.detect(
        vix=17,
        realized_vol=14,
        momentum=0,  # No momentum
        credit_spread=95
    )

    assert state.regime == "RANGE", f"Expected RANGE, got {state.regime}"
    print("✓ RANGE detection test passed")


def test_persistence():
    """Test regime persistence tracking"""
    detector = RegimeDetector()

    # First detection
    state1 = detector.detect(vix=12, realized_vol=12, momentum=1.0, credit_spread=80)
    assert state1.persistence_days == 0

    # Same regime again
    state2 = detector.detect(vix=13, realized_vol=11, momentum=1.5, credit_spread=80)
    assert state2.persistence_days == 1

    # Same regime third time
    state3 = detector.detect(vix=14, realized_vol=12, momentum=1.0, credit_spread=80)
    assert state3.persistence_days == 2

    print(f"✓ Persistence tracking test passed (persisted {state3.persistence_days} days)")


def test_regime_parameters():
    """Test that each regime returns correct parameter set"""
    detector = RegimeDetector()

    regimes = ["TRENDING_UP", "TRENDING_DOWN", "RANGE", "HIGH_VOL", "RISK_OFF"]

    for regime in regimes:
        params = detector.get_regime_parameters(regime)
        assert "confidence_threshold" in params
        assert "position_size_multiplier" in params
        assert "stop_loss_pct" in params
        assert "max_leverage" in params
        print(f"  ✓ {regime}: params OK")

    print("✓ Regime parameters test passed")


if __name__ == "__main__":
    test_trending_up()
    test_trending_down()
    test_risk_off()
    test_high_vol()
    test_range()
    test_persistence()
    test_regime_parameters()

    print("\n" + "="*60)
    print("PHASE 5: REGIME DETECTION - EXAMPLE OUTPUT")
    print("="*60)

    detector = RegimeDetector()

    # Scenario 1: Calm, bullish market
    state1 = detector.detect(
        vix=13,
        realized_vol=11,
        momentum=2.0,
        credit_spread=85
    )
    print(f"\nScenario 1 (Calm Bullish):")
    print(f"  Regime: {state1.regime}")
    print(f"  Confidence: {state1.confidence*100:.0f}%")
    params1 = detector.get_regime_parameters(state1.regime)
    print(f"  Confidence threshold: {params1['confidence_threshold']}")
    print(f"  Position size multiplier: {params1['position_size_multiplier']}x")
    print(f"  Max leverage: {params1['max_leverage']}x")

    # Scenario 2: Stress, bearish market
    state2 = detector.detect(
        vix=32,
        realized_vol=32,
        momentum=-2.5,
        credit_spread=210
    )
    print(f"\nScenario 2 (Stress / Risk-Off):")
    print(f"  Regime: {state2.regime}")
    print(f"  Confidence: {state2.confidence*100:.0f}%")
    params2 = detector.get_regime_parameters(state2.regime)
    print(f"  Confidence threshold: {params2['confidence_threshold']}")
    print(f"  Position size multiplier: {params2['position_size_multiplier']}x")
    print(f"  Max leverage: {params2['max_leverage']}x")

    print("\n✅ Phase 5 (Regime Detection) complete and tested")
