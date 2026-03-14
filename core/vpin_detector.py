"""
VPIN Toxicity Detector (KRONOS Upgrade #1)
==========================================
Detects institutional order flow toxicity using Volume-Synchronized PIT metric.
Predicts when big funds are accumulating/distributing before public sees price move.
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class VPINResult:
    vpin_score: float  # 0-1
    toxicity_level: str  # "neutral", "toxic", "very_toxic"
    confidence: float
    signal: str


class VPINDetector:
    """
    VPIN = Volume-Synchronized Probability of Informed Trading
    
    High VPIN = Informed traders (big funds) are aggressively buying/selling
    Low VPIN = Normal retail trading
    """
    
    def __init__(self, lookback_bars: int = 20):
        self.lookback_bars = lookback_bars
        self.logger = logging.getLogger("nzt48.vpin_detector")
        self.bar_count = 0
        self.aggressive_buys = 0
        self.aggressive_sells = 0
    
    def calculate_vpin(
        self,
        ofi: float,  # Order flow imbalance (-1 to +1)
        volume: float,  # Current bar volume
        bid_ask_spread: float  # Current spread in %
    ) -> VPINResult:
        """
        Calculate VPIN score
        
        Args:
            ofi: Order flow imbalance score (-1 to +1)
            volume: Bar volume
            bid_ask_spread: Bid-ask spread (%)
        
        Returns:
            VPINResult with score and toxicity level
        """
        # VPIN calculation
        aggressive_volume = volume * abs(ofi)  # Aggressive vol = total vol × |OFI|
        
        if ofi > 0:
            self.aggressive_buys += aggressive_volume
        else:
            self.aggressive_sells += aggressive_volume
        
        self.bar_count += 1
        
        # Calculate imbalance ratio
        total_aggressive = self.aggressive_buys + self.aggressive_sells
        if total_aggressive == 0:
            vpin_score = 0.5
        else:
            imbalance_ratio = abs(self.aggressive_buys - self.aggressive_sells) / total_aggressive
            vpin_score = imbalance_ratio
        
        # Reset every N bars
        if self.bar_count >= self.lookback_bars:
            self.aggressive_buys = 0
            self.aggressive_sells = 0
            self.bar_count = 0
        
        # Classify toxicity
        if vpin_score > 0.75:
            toxicity_level = "very_toxic"
            confidence = 0.95
            signal = "CRITICAL: Informed traders in control"
        elif vpin_score > 0.60:
            toxicity_level = "toxic"
            confidence = 0.80
            signal = "HIGH: Institutional activity detected"
        elif vpin_score > 0.50:
            toxicity_level = "moderate"
            confidence = 0.60
            signal = "NORMAL: Balanced order flow"
        else:
            toxicity_level = "neutral"
            confidence = 0.40
            signal = "LOW: Mostly retail trading"
        
        self.logger.info(f"VPIN: {vpin_score:.2f} ({toxicity_level}) - {signal}")
        
        return VPINResult(
            vpin_score=vpin_score,
            toxicity_level=toxicity_level,
            confidence=confidence,
            signal=signal
        )
    
    def is_institutional_activity(self, vpin_score: float) -> bool:
        """Quick check: is there institutional activity?"""
        return vpin_score > 0.65


if __name__ == "__main__":
    detector = VPINDetector()
    
    # Test
    result = detector.calculate_vpin(ofi=0.45, volume=100000, bid_ask_spread=0.1)
    print(f"✅ VPIN Score: {result.vpin_score:.2f} ({result.toxicity_level})")
    print(f"   Confidence: {result.confidence:.0%}")
    print(f"   Signal: {result.signal}")
