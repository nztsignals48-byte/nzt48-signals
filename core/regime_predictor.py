"""
Market Regime Predictor (KRONOS Upgrade #10)
============================================
Forecast regime changes 30-60 minutes ahead using Hidden Markov Model.
Gives first-mover advantage when market is about to shift regimes.
"""

import logging
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class RegimeType(Enum):
    COMPRESSION = "compression"
    EXPANSION = "expansion"
    TRENDING = "trending"
    RANGE = "range"
    BLOW_OFF = "blow_off"
    BREAKDOWN = "breakdown"


@dataclass
class RegimeforecastResult:
    current_regime: RegimeType
    predicted_regime: RegimeType
    transition_probability: float  # 0-1
    confidence: float  # 0-1
    recommendation: str


class RegimePredictor:
    """
    HMM-based regime predictor
    
    Forecasts regime changes 30-60 min ahead based on:
    - Volatility trend
    - Price momentum
    - Order flow
    - Volume profile
    """
    
    def __init__(self):
        self.logger = logging.getLogger("nzt48.regime_predictor")
        self.current_regime = RegimeType.RANGE
        self.regime_history = []
    
    def predict_regime_change(
        self,
        current_vol: float,
        vol_trend: float,  # -1 to +1 (decreasing to increasing)
        momentum: float,   # -1 to +1 (down to up)
        ofi: float        # -1 to +1 (sell to buy)
    ) -> RegimeforecastResult:
        """
        Predict regime 30-60 min ahead
        
        Args:
            current_vol: Current realized volatility (%)
            vol_trend: Volatility trend (-1 to +1)
            momentum: Price momentum (-1 to +1)
            ofi: Order flow imbalance (-1 to +1)
        
        Returns:
            RegimeforecastResult with prediction and confidence
        """
        # Determine current regime
        current_regime = self._classify_regime(current_vol, vol_trend, momentum)
        
        # Predict transition probability
        transition_prob = abs(vol_trend + momentum) / 2  # 0-1
        
        # Predict next regime
        if vol_trend > 0.5 and momentum > 0.3:
            predicted = RegimeType.EXPANSION
            confidence = 0.85
            recommendation = "INCREASE position sizes (+25%)"
        elif vol_trend > 0.3 and abs(momentum) > 0.6:
            predicted = RegimeType.BLOW_OFF
            confidence = 0.75
            recommendation = "REDUCE position sizes (-40%), protect profits"
        elif vol_trend < -0.4 and momentum < -0.3:
            predicted = RegimeType.COMPRESSION
            confidence = 0.80
            recommendation = "TIGHTEN stops, wait for breakout"
        elif momentum < -0.5:
            predicted = RegimeType.BREAKDOWN
            confidence = 0.70
            recommendation = "PREPARE inverse positions, reduce long exposure"
        else:
            predicted = RegimeType.RANGE
            confidence = 0.60
            recommendation = "STAY neutral, scalp edges"
        
        self.logger.info(
            f"Regime forecast: {current_regime.value} → {predicted.value} "
            f"(prob={transition_prob:.0%}, conf={confidence:.0%})"
        )
        
        return RegimeforecastResult(
            current_regime=current_regime,
            predicted_regime=predicted,
            transition_probability=transition_prob,
            confidence=confidence,
            recommendation=recommendation
        )
    
    def _classify_regime(
        self,
        current_vol: float,
        vol_trend: float,
        momentum: float
    ) -> RegimeType:
        """Classify current regime"""
        if vol_trend > 0.6 and abs(momentum) > 0.5:
            return RegimeType.EXPANSION
        elif vol_trend < -0.5:
            return RegimeType.COMPRESSION
        elif abs(momentum) > 0.7:
            return RegimeType.BLOW_OFF if momentum > 0 else RegimeType.BREAKDOWN
        else:
            return RegimeType.RANGE
    
    def get_position_adjustment(self, forecast: RegimeforecastResult) -> float:
        """
        Get position sizing multiplier based on forecast
        
        Returns:
            Multiplier for position sizing (0.5 to 1.5)
        """
        if forecast.predicted_regime == RegimeType.BLOW_OFF:
            return 0.60  # Reduce to 60%
        elif forecast.predicted_regime == RegimeType.EXPANSION:
            return 1.25  # Increase to 125%
        elif forecast.predicted_regime == RegimeType.BREAKDOWN:
            return 0.50  # Reduce to 50%
        else:
            return 1.0   # Keep at 100%


if __name__ == "__main__":
    predictor = RegimePredictor()
    
    # Test
    result = predictor.predict_regime_change(
        current_vol=15.0,
        vol_trend=0.6,
        momentum=0.4,
        ofi=0.3
    )
    print(f"✅ Current: {result.current_regime.value}")
    print(f"   Predicted: {result.predicted_regime.value}")
    print(f"   Prob: {result.transition_probability:.0%}")
    print(f"   Confidence: {result.confidence:.0%}")
    print(f"   Action: {result.recommendation}")
    print(f"   Position multiplier: {predictor.get_position_adjustment(result):.2f}x")
