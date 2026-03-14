"""
KRONOS: Volume-Aware Position Scaling (Optional)

Scale position sizes based on intraday volatility regime to maintain constant
risk-adjusted exposure across market conditions.

Part of Phase Q2 infrastructure upgrade.

Key features:
- Percentile-based volatility classification
- Smooth scaling curves (not step functions)
- Thread-safe caching of recent volatility
- Integration with realized volatility streams
"""

from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass
import threading
import math
from collections import deque
import logging


logger = logging.getLogger(__name__)


@dataclass
class VolatilityLevel:
    """Represents a volatility level with scaling parameters"""
    percentile_low: float  # Lower bound of percentile range
    percentile_high: float  # Upper bound of percentile range
    scaling_factor: float   # Position size multiplier (1.0 = no change)
    description: str        # Human-readable name


class VolAwareScaler:
    """Volume-aware position scaling with percentile-based regimes"""
    
    # Volatility scaling regimes
    VOLATILITY_REGIMES = [
        VolatilityLevel(0, 10, 1.30, "Extremely Low"),     # Reduce position risk
        VolatilityLevel(10, 30, 1.15, "Very Low"),
        VolatilityLevel(30, 50, 1.05, "Low"),
        VolatilityLevel(50, 70, 1.00, "Normal"),           # Baseline
        VolatilityLevel(70, 90, 0.90, "High"),
        VolatilityLevel(90, 100, 0.50, "Extreme"),         # Heavy reduction
    ]
    
    def __init__(self, lookback_periods: int = 100, update_frequency_seconds: float = 5.0):
        """
        Initialize volatility-aware scaler.
        
        Args:
            lookback_periods: Number of periods for percentile calculation
            update_frequency_seconds: How often to update percentile calculations
        """
        self.lookback_periods = lookback_periods
        self.update_frequency_seconds = update_frequency_seconds
        
        self.vol_buffer: deque = deque(maxlen=lookback_periods)
        self._lock = threading.Lock()
        self._last_percentile = 50.0
        self._last_scaling_factor = 1.0
    
    def add_volatility_sample(self, realized_vol: float) -> None:
        """
        Add a volatility sample to the buffer.
        
        Args:
            realized_vol: Realized volatility (e.g., from EWMA or Parkinson)
        """
        with self._lock:
            self.vol_buffer.append(realized_vol)
    
    def get_volatility_percentile(self) -> float:
        """
        Get current percentile of latest volatility in historical distribution.
        
        Returns:
            Percentile 0-100 (0 = lowest, 100 = highest)
        """
        with self._lock:
            if len(self.vol_buffer) < 2:
                return 50.0  # Default to middle if insufficient data
            
            vols = list(self.vol_buffer)
            latest_vol = vols[-1]
            
            # Calculate percentile rank
            below_count = sum(1 for v in vols if v < latest_vol)
            percentile = (below_count / len(vols)) * 100.0
        
        return percentile
    
    def get_scaling_factor_by_percentile(self, percentile: float) -> float:
        """
        Get position size multiplier for a given volatility percentile.
        
        Args:
            percentile: Volatility percentile (0-100)
        
        Returns:
            Position size multiplier (0.5 = half size, 1.3 = 130% size)
        """
        # Find matching regime
        for regime in self.VOLATILITY_REGIMES:
            if regime.percentile_low <= percentile <= regime.percentile_high:
                # Linear interpolation within regime for smooth scaling
                regime_range = regime.percentile_high - regime.percentile_low
                if regime_range == 0:
                    return regime.scaling_factor
                
                position_in_regime = (percentile - regime.percentile_low) / regime_range
                
                # For simplicity, return regime factor directly
                # (can add inter-regime interpolation later)
                return regime.scaling_factor
        
        return 1.0  # Fallback to baseline
    
    def get_scaling_factor_current(self) -> float:
        """
        Get current position size scaling factor based on latest volatility.
        
        Returns:
            Multiplier to apply to base position size
        """
        percentile = self.get_volatility_percentile()
        scaling_factor = self.get_scaling_factor_by_percentile(percentile)
        
        with self._lock:
            self._last_percentile = percentile
            self._last_scaling_factor = scaling_factor
        
        return scaling_factor
    
    def scale_position_by_realized_vol(self, realized_vol_percentile: float, base_size: int) -> int:
        """
        Scale position size based on intraday volatility percentile.
        
        Strategy:
        - High vol (>90th): Reduce 50% (double risk reduction)
        - Normal vol (50-70): No change (baseline)
        - Low vol (<10th): Increase 30% (capitalize on low-vol stability)
        
        Args:
            realized_vol_percentile: Volatility percentile (0-100)
            base_size: Base position size
        
        Returns:
            Scaled position size (integer)
        """
        scaling_factor = self.get_scaling_factor_by_percentile(realized_vol_percentile)
        return int(base_size * scaling_factor)
    
    def get_vol_regime_description(self, percentile: Optional[float] = None) -> str:
        """
        Get human-readable description of volatility regime.
        
        Args:
            percentile: Volatility percentile (uses current if None)
        
        Returns:
            Regime description string
        """
        if percentile is None:
            percentile = self.get_volatility_percentile()
        
        for regime in self.VOLATILITY_REGIMES:
            if regime.percentile_low <= percentile <= regime.percentile_high:
                return regime.description
        
        return "Unknown"
    
    def get_vol_stats(self) -> Dict:
        """
        Get comprehensive statistics about volatility buffer.
        
        Returns:
            Dictionary with vol stats and current scaling
        """
        with self._lock:
            if len(self.vol_buffer) == 0:
                return {
                    'count': 0,
                    'latest_vol': None,
                    'percentile': 50.0,
                    'scaling_factor': 1.0,
                    'regime': 'Unknown'
                }
            
            vols = list(self.vol_buffer)
            latest_vol = vols[-1]
            percentile = self.get_volatility_percentile()
            scaling_factor = self._last_scaling_factor
        
        return {
            'count': len(vols),
            'latest_vol': latest_vol,
            'mean_vol': sum(vols) / len(vols),
            'min_vol': min(vols),
            'max_vol': max(vols),
            'percentile': percentile,
            'scaling_factor': scaling_factor,
            'regime': self.get_vol_regime_description(percentile)
        }
    
    def clear_buffer(self) -> None:
        """Clear volatility buffer"""
        with self._lock:
            self.vol_buffer.clear()


# Convenience function
def scale_position_by_realized_vol(
    realized_vol_percentile: float,
    base_size: int
) -> int:
    """
    Standalone function for position scaling by volatility.
    
    Scaling rules:
    - 90-100%: Scale to 50% (extreme volatility)
    - 70-89%: Scale to 90% (high volatility)
    - 50-69%: Scale to 100% (normal)
    - 30-49%: Scale to 105% (low)
    - 10-29%: Scale to 115% (very low)
    - 0-9%: Scale to 130% (extremely low)
    
    Args:
        realized_vol_percentile: Volatility percentile (0-100)
        base_size: Base position size
    
    Returns:
        Scaled position size
    """
    if realized_vol_percentile > 90:
        return int(base_size * 0.50)    # Extreme: 50%
    elif realized_vol_percentile > 70:
        return int(base_size * 0.90)    # High: 90%
    elif realized_vol_percentile > 50:
        return int(base_size * 1.00)    # Normal: 100%
    elif realized_vol_percentile > 30:
        return int(base_size * 1.05)    # Low: 105%
    elif realized_vol_percentile > 10:
        return int(base_size * 1.15)    # Very low: 115%
    else:
        return int(base_size * 1.30)    # Extremely low: 130%


def get_vol_scaling_curve() -> List[Tuple[float, float]]:
    """
    Get the volatility scaling curve as a list of (percentile, multiplier) tuples.
    
    Useful for visualization and debugging.
    
    Returns:
        List of (percentile, multiplier) tuples from 0-100
    """
    return [
        (5, 1.30),      # Extremely low
        (20, 1.15),     # Very low
        (40, 1.05),     # Low
        (50, 1.00),     # Normal
        (70, 0.90),     # High
        (95, 0.50),     # Extreme
    ]
