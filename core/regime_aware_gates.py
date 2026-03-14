"""
KRONOS: Regime-Based Gating (Conditional Thresholds)

Dynamic confidence gating based on market regime. Different regimes have different
signal quality distributions, so thresholds adjust accordingly.

Part of Phase Q2 infrastructure upgrade.

Regime definitions:
- COMPRESSION: Low volatility, mean-reversion bias (range-bound)
- EXPANSION: Volatility rising, momentum bias (trending)
- TRENDING_UP: Strong uptrend with volatility expansion
- TRENDING_DOWN: Strong downtrend with volatility contraction
- SHOCK: Black swan event (VIX >40 or DXY >200% of ATR)
"""

from typing import Dict, Optional, Tuple
from enum import Enum
from dataclasses import dataclass
import logging


logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """Enumeration of market regimes for gate adjustment"""
    COMPRESSION = "COMPRESSION"
    EXPANSION = "EXPANSION"
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    SHOCK = "SHOCK"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeThresholds:
    """Confidence thresholds for a given regime"""
    entry_confidence: float  # Minimum confidence to enter trade
    exit_confidence: float   # Confidence threshold for exit decision
    position_size_multiplier: float  # Scale position by this factor
    max_leverage: float  # Maximum allowed leverage in this regime


class RegimeAwareGates:
    """Dynamic confidence gating based on market regime"""
    
    # Base thresholds for each regime
    BASE_THRESHOLDS = {
        MarketRegime.COMPRESSION: RegimeThresholds(
            entry_confidence=60.0,      # Reversion trades lower quality
            exit_confidence=55.0,
            position_size_multiplier=0.75,  # Reduce size in range-bound
            max_leverage=1.5
        ),
        MarketRegime.EXPANSION: RegimeThresholds(
            entry_confidence=70.0,      # Momentum trades higher quality
            exit_confidence=65.0,
            position_size_multiplier=1.25,  # Increase size in trending
            max_leverage=2.0
        ),
        MarketRegime.TRENDING_UP: RegimeThresholds(
            entry_confidence=65.0,
            exit_confidence=60.0,
            position_size_multiplier=1.1,
            max_leverage=1.75
        ),
        MarketRegime.TRENDING_DOWN: RegimeThresholds(
            entry_confidence=65.0,
            exit_confidence=60.0,
            position_size_multiplier=1.1,
            max_leverage=1.75
        ),
        MarketRegime.SHOCK: RegimeThresholds(
            entry_confidence=75.0,      # High confidence only during shocks
            exit_confidence=70.0,
            position_size_multiplier=0.5,  # Aggressive reduction
            max_leverage=1.0  # No leverage in black swan
        ),
        MarketRegime.UNKNOWN: RegimeThresholds(
            entry_confidence=68.0,      # Conservative default
            exit_confidence=63.0,
            position_size_multiplier=1.0,
            max_leverage=1.5
        )
    }
    
    def __init__(self, custom_thresholds: Optional[Dict[MarketRegime, RegimeThresholds]] = None):
        """
        Initialize regime-aware gates.
        
        Args:
            custom_thresholds: Optional override for specific regimes
        """
        self.thresholds = self.BASE_THRESHOLDS.copy()
        if custom_thresholds:
            self.thresholds.update(custom_thresholds)
        
        self.current_regime = MarketRegime.UNKNOWN
        self.regime_confidence = 0.5  # 0-1 confidence in regime classification
    
    def set_regime(
        self,
        regime: MarketRegime,
        confidence: float = 0.8
    ) -> None:
        """
        Update current market regime.
        
        Args:
            regime: Current regime
            confidence: Confidence in this regime classification (0-1)
        """
        self.current_regime = regime
        self.regime_confidence = max(0.0, min(1.0, confidence))
        logger.info(f"Regime update: {regime.value} (confidence: {confidence:.2f})")
    
    def get_entry_threshold(self) -> float:
        """Get minimum confidence threshold for entry in current regime"""
        return self.thresholds[self.current_regime].entry_confidence
    
    def get_exit_threshold(self) -> float:
        """Get confidence threshold for exit decision in current regime"""
        return self.thresholds[self.current_regime].exit_confidence
    
    def get_position_size_multiplier(self) -> float:
        """
        Get position size adjustment factor for current regime.
        
        Returns:
            Multiplier to apply to base position size (1.0 = no change)
        """
        multiplier = self.thresholds[self.current_regime].position_size_multiplier
        
        # Reduce multiplier if regime confidence is low
        uncertainty_penalty = (1.0 - self.regime_confidence) * 0.2
        adjusted_multiplier = multiplier * (1.0 - uncertainty_penalty)
        
        return adjusted_multiplier
    
    def get_max_leverage(self) -> float:
        """Get maximum allowed leverage in current regime"""
        return self.thresholds[self.current_regime].max_leverage
    
    def should_enter(self, signal_confidence: float) -> Tuple[bool, str]:
        """
        Determine if signal confidence is sufficient to enter trade.
        
        Args:
            signal_confidence: Signal confidence level (0-100)
        
        Returns:
            (should_enter: bool, reason: str)
        """
        threshold = self.get_entry_threshold()
        
        if signal_confidence >= threshold:
            return True, f"Signal {signal_confidence:.1f} >= threshold {threshold:.1f} in {self.current_regime.value}"
        else:
            shortfall = threshold - signal_confidence
            return False, f"Signal {signal_confidence:.1f} below threshold {threshold:.1f} (shortfall: {shortfall:.1f}) in {self.current_regime.value}"
    
    def should_exit(self, signal_confidence: float) -> Tuple[bool, str]:
        """
        Determine if position should be exited based on confidence.
        
        Args:
            signal_confidence: Signal confidence level (0-100)
        
        Returns:
            (should_exit: bool, reason: str)
        """
        threshold = self.get_exit_threshold()
        
        if signal_confidence <= threshold:
            return True, f"Signal {signal_confidence:.1f} <= exit threshold {threshold:.1f} in {self.current_regime.value}"
        else:
            return False, f"Signal {signal_confidence:.1f} above exit threshold {threshold:.1f} in {self.current_regime.value}"
    
    def adjust_confidence_for_regime(self, base_confidence: float) -> float:
        """
        Apply regime-specific adjustments to a base confidence score.
        
        In extreme regimes, add slight boost/penalty to confidence.
        
        Args:
            base_confidence: Original confidence (0-100)
        
        Returns:
            Adjusted confidence (0-100)
        """
        adjustment = 0.0
        
        if self.current_regime == MarketRegime.SHOCK:
            # In shocks, discount confidence slightly (favor caution)
            adjustment = -3.0
        elif self.current_regime == MarketRegime.EXPANSION:
            # In expansions, slight boost for trend-following signals
            adjustment = +2.0
        elif self.current_regime == MarketRegime.COMPRESSION:
            # In compressions, slight discount for trend following
            adjustment = -2.0
        
        # Apply regime confidence dampening
        adjusted = base_confidence + (adjustment * self.regime_confidence)
        return max(0.0, min(100.0, adjusted))
    
    def get_thresholds(self) -> RegimeThresholds:
        """Get full threshold set for current regime"""
        return self.thresholds[self.current_regime]
    
    def get_regime_summary(self) -> Dict:
        """Get comprehensive summary of current regime and thresholds"""
        thresholds = self.get_thresholds()
        return {
            'regime': self.current_regime.value,
            'regime_confidence': self.regime_confidence,
            'entry_threshold': thresholds.entry_confidence,
            'exit_threshold': thresholds.exit_confidence,
            'position_size_multiplier': self.get_position_size_multiplier(),
            'max_leverage': thresholds.max_leverage
        }


def get_confidence_threshold_by_regime(regime: MarketRegime) -> float:
    """
    Convenience function: get entry threshold for a specific regime.
    
    Args:
        regime: Market regime
    
    Returns:
        Minimum confidence threshold (0-100)
    """
    return RegimeAwareGates.BASE_THRESHOLDS[regime].entry_confidence


def scale_position_by_regime(
    regime: MarketRegime,
    base_size: int,
    regime_confidence: float = 0.8
) -> int:
    """
    Convenience function: scale position size by regime multiplier.
    
    Args:
        regime: Current market regime
        base_size: Base position size
        regime_confidence: Confidence in regime classification (0-1)
    
    Returns:
        Scaled position size
    """
    multiplier = RegimeAwareGates.BASE_THRESHOLDS[regime].position_size_multiplier
    
    # Apply confidence dampening
    uncertainty_penalty = (1.0 - regime_confidence) * 0.2
    adjusted_multiplier = multiplier * (1.0 - uncertainty_penalty)
    
    return int(base_size * adjusted_multiplier)
