"""
KRONOS: Confidence Blending Enhancement (Exponential Decay)

Enhanced confidence scoring with temporal decay: recent signals matter more than stale ones.
Part of Phase Q2 infrastructure upgrade.

Key improvements:
- Exponential decay weighting (recent signals valued higher)
- Lookback window configurable (default 30 minutes)
- Graceful handling of empty/zero-weight signals
- Thread-safe implementation
"""

import time
import math
from typing import List, Dict, Optional
import threading
from dataclasses import dataclass


@dataclass
class ConfidenceSignal:
    """Represents a single confidence signal with timestamp"""
    confidence: float  # 0-100
    timestamp: float   # Unix time
    source: str        # e.g., "meta_model", "regime_gate", "vol_filter"
    weight: float = 1.0  # Base weight before decay


class ConfidenceScorerV2:
    """Exponential decay confidence aggregator for multi-source signal blending"""
    
    def __init__(self, default_lookback_minutes: float = 30.0):
        """
        Initialize confidence scorer with decay parameters.
        
        Args:
            default_lookback_minutes: Time constant for exponential decay (e^(-t/tau))
        """
        self.default_lookback_minutes = default_lookback_minutes
        self.signal_buffer: List[ConfidenceSignal] = []
        self._lock = threading.Lock()
    
    def add_signal(
        self,
        confidence: float,
        source: str,
        weight: float = 1.0,
        timestamp: Optional[float] = None
    ) -> None:
        """
        Add a new confidence signal to the buffer.
        
        Args:
            confidence: Score 0-100
            source: Signal source identifier
            weight: Relative importance (1.0 = normal)
            timestamp: Unix time (default: now)
        """
        if timestamp is None:
            timestamp = time.time()
        
        signal = ConfidenceSignal(
            confidence=confidence,
            timestamp=timestamp,
            source=source,
            weight=weight
        )
        
        with self._lock:
            self.signal_buffer.append(signal)
    
    def compute_confidence_with_decay(
        self,
        lookback_minutes: Optional[float] = None
    ) -> float:
        """
        Compute weighted average confidence with exponential decay.
        
        Recent signals get higher weight. Signal weight decays as:
            weight_final = base_weight * e^(-age_minutes / lookback_minutes)
        
        Args:
            lookback_minutes: Decay time constant (default: instance default)
        
        Returns:
            Weighted average confidence (0-100), or 50 if no signals
        """
        if lookback_minutes is None:
            lookback_minutes = self.default_lookback_minutes
        
        with self._lock:
            signals = self.signal_buffer.copy()
        
        if not signals:
            return 50.0  # Neutral default
        
        now = time.time()
        total_weight = 0.0
        weighted_score = 0.0
        
        for signal in signals:
            age_minutes = (now - signal.timestamp) / 60.0
            
            # Exponential decay: weight *= e^(-age / tau)
            decay_factor = math.exp(-age_minutes / lookback_minutes)
            final_weight = signal.weight * decay_factor
            
            weighted_score += signal.confidence * final_weight
            total_weight += final_weight
        
        if total_weight > 0:
            return weighted_score / total_weight
        else:
            return 50.0
    
    def get_signal_contribution(
        self,
        source: str,
        lookback_minutes: Optional[float] = None
    ) -> Dict[str, float]:
        """
        Analyze contribution of a specific signal source.
        
        Returns:
            {
                'count': number of signals from this source,
                'avg_confidence': average confidence value,
                'latest_confidence': most recent signal value,
                'weighted_contribution': weighted average contribution,
                'decay_factor': average decay factor across signals
            }
        """
        if lookback_minutes is None:
            lookback_minutes = self.default_lookback_minutes
        
        with self._lock:
            signals = [s for s in self.signal_buffer if s.source == source]
        
        if not signals:
            return {
                'count': 0,
                'avg_confidence': None,
                'latest_confidence': None,
                'weighted_contribution': 0.0,
                'decay_factor': None
            }
        
        now = time.time()
        total_weight = 0.0
        weighted_confidence = 0.0
        decay_factors = []
        
        for signal in signals:
            age_minutes = (now - signal.timestamp) / 60.0
            decay_factor = math.exp(-age_minutes / lookback_minutes)
            final_weight = signal.weight * decay_factor
            
            weighted_confidence += signal.confidence * final_weight
            total_weight += final_weight
            decay_factors.append(decay_factor)
        
        return {
            'count': len(signals),
            'avg_confidence': sum(s.confidence for s in signals) / len(signals),
            'latest_confidence': signals[-1].confidence,
            'weighted_contribution': weighted_confidence / total_weight if total_weight > 0 else 0.0,
            'decay_factor': sum(decay_factors) / len(decay_factors) if decay_factors else 0.0
        }
    
    def prune_old_signals(self, minutes_cutoff: float = 120.0) -> int:
        """
        Remove signals older than cutoff time to prevent memory bloat.
        
        Args:
            minutes_cutoff: Remove signals older than this (default: 2 hours)
        
        Returns:
            Number of signals removed
        """
        now = time.time()
        cutoff_time = now - (minutes_cutoff * 60)
        
        with self._lock:
            original_count = len(self.signal_buffer)
            self.signal_buffer = [
                s for s in self.signal_buffer if s.timestamp > cutoff_time
            ]
            removed_count = original_count - len(self.signal_buffer)
        
        return removed_count
    
    def clear_signals(self) -> None:
        """Clear all buffered signals"""
        with self._lock:
            self.signal_buffer.clear()
    
    def get_buffer_stats(self) -> Dict[str, any]:
        """Get statistics about current signal buffer"""
        with self._lock:
            signals = self.signal_buffer.copy()
        
        if not signals:
            return {'count': 0, 'sources': {}, 'age_range': None}
        
        now = time.time()
        sources = {}
        ages = []
        
        for signal in signals:
            age_minutes = (now - signal.timestamp) / 60.0
            ages.append(age_minutes)
            
            if signal.source not in sources:
                sources[signal.source] = 0
            sources[signal.source] += 1
        
        return {
            'count': len(signals),
            'sources': sources,
            'age_range_minutes': {
                'min': min(ages),
                'max': max(ages),
                'mean': sum(ages) / len(ages)
            }
        }


# Convenience function for single-call use
def compute_confidence_with_decay(
    signals: List[Dict],
    lookback_minutes: float = 30.0
) -> float:
    """
    Standalone function for computing decay-weighted confidence.
    
    Args:
        signals: List of dicts with keys: 'confidence', 'timestamp', 'weight' (optional)
        lookback_minutes: Decay time constant
    
    Returns:
        Weighted average confidence (0-100)
    """
    if not signals:
        return 50.0
    
    now = time.time()
    total_weight = 0.0
    weighted_score = 0.0
    
    for signal in signals:
        age_minutes = (now - signal['timestamp']) / 60.0
        weight = signal.get('weight', 1.0)
        decay_factor = math.exp(-age_minutes / lookback_minutes)
        final_weight = weight * decay_factor
        
        weighted_score += signal['confidence'] * final_weight
        total_weight += final_weight
    
    return weighted_score / total_weight if total_weight > 0 else 50.0
