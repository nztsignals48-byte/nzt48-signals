"""
Neural Hawkes Process for Exit Timing
Predicts when order flow momentum will decay and optimal exit time approaches
"""

import logging
import numpy as np
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from collections import deque
import time

logger = logging.getLogger("nzt48.neural_hawkes")

@dataclass
class HawkesState:
    """State snapshot for Hawkes process"""
    timestamp: float
    order_volume_buy: float
    order_volume_sell: float
    ofi: float  # Order flow imbalance
    price: float
    vpin: float  # Volume-synchronized probability of informed trading
    trade_intensity: int  # Number of trades in last second

class NeuralHawkesExitTimer:
    """
    Self-exciting Hawkes process for order flow.
    When order flow shows declining self-excitement, reversal probability rises.
    
    Mathematical foundation:
    - λ(t) = μ + Σ α_i * exp(-β_i * (t - t_i))
    - μ = baseline intensity
    - α_i = excitement amplitude for event i
    - β_i = decay rate (typically 0.1 for financial data)
    - (t - t_i) = time since event i
    
    Theory: High intensity suggests momentum continuation (trend).
    Declining intensity suggests momentum decay (reversal incoming).
    """
    
    def __init__(self, 
                 baseline_intensity: float = 0.5,
                 decay_lambda: float = 0.1,
                 event_buffer_size: int = 50):
        """
        Args:
            baseline_intensity: Baseline event rate (μ)
            decay_lambda: Exponential decay rate (β)
            event_buffer_size: Keep last N events for intensity calculation
        """
        self.baseline_intensity = baseline_intensity
        self.decay_lambda = decay_lambda
        self.event_buffer_size = event_buffer_size
        
        # Event history: (timestamp, amplitude)
        self.event_times: deque = deque(maxlen=event_buffer_size)
        self.event_amplitudes: deque = deque(maxlen=event_buffer_size)
        
        # State history
        self.state_history: List[HawkesState] = []
        self.intensity_history: List[float] = []
        self.reversal_scores: List[float] = []
        
        logger.info(f"Neural Hawkes initialized: baseline={baseline_intensity}, "
                   f"decay={decay_lambda}, buffer={event_buffer_size}")
    
    def record_event(self, current_time: float, amplitude: float = 1.0):
        """
        Record an order flow event (large buy/sell volume spike).
        
        Args:
            current_time: Unix timestamp
            amplitude: Magnitude of event (default 1.0)
        """
        self.event_times.append(current_time)
        self.event_amplitudes.append(amplitude)
        logger.debug(f"Event recorded: t={current_time:.1f}, amplitude={amplitude:.2f}")
    
    def calculate_intensity(self, current_time: float) -> float:
        """
        Calculate Hawkes intensity λ(t) at current time.
        λ(t) = μ + Σ α_i * exp(-β * (t - t_i))
        
        High intensity (>1.5): Momentum continuing, reversal unlikely
        Medium intensity (0.5-1.5): Normal conditions
        Low intensity (<0.5): Momentum decaying, reversal likely
        
        Returns:
            Intensity scalar (0.0 to ~2.5)
        """
        intensity = self.baseline_intensity
        
        # Sum kernel values for all past events
        for i, event_time in enumerate(self.event_times):
            time_diff = current_time - event_time
            if time_diff > 0:
                # Exponential decay kernel: α_i * exp(-λ * Δt)
                amplitude = self.event_amplitudes[i]
                kernel_value = amplitude * np.exp(-self.decay_lambda * time_diff)
                intensity += kernel_value
        
        # Cap intensity at reasonable max
        intensity = min(intensity, 2.5)
        
        self.intensity_history.append(intensity)
        return intensity
    
    def get_intensity_trend(self, window: int = 10) -> Tuple[float, str]:
        """
        Calculate trend of intensity over recent window.
        Declining intensity = momentum decay = reversal signal
        
        Returns:
            (slope, trend_label)
            slope > 0: intensity rising (momentum strengthening)
            slope < 0: intensity falling (momentum weakening)
        """
        if len(self.intensity_history) < window:
            return 0.0, "INSUFFICIENT_DATA"
        
        recent = np.array(self.intensity_history[-window:])
        x = np.arange(len(recent))
        
        # Linear regression slope
        if len(recent) < 2:
            return 0.0, "INSUFFICIENT_DATA"
        
        slope = np.polyfit(x, recent, 1)[0]
        
        if slope > 0.02:
            return slope, "INTENSIFYING"
        elif slope < -0.02:
            return slope, "DECAYING"
        else:
            return slope, "STABLE"
    
    def should_exit(self, 
                   current_intensity: float,
                   intensity_trend: str,
                   position_pnl_pct: float,
                   time_in_trade: int) -> Optional[Dict]:
        """
        Recommend exit if Hawkes indicators suggest reversal approaching.
        
        Exit signals:
        1. DECAYING_INTENSITY + Profit: Reversal likely, lock gains
        2. EXTREME_DECAY: Emergency exit (momentum collapsing)
        3. REVERSAL_POINT: Intensity turning up after extended decay
        
        Args:
            current_intensity: Current Hawkes intensity
            intensity_trend: 'INTENSIFYING', 'DECAYING', 'STABLE'
            position_pnl_pct: Current unrealized P&L %
            time_in_trade: Seconds in position
            
        Returns:
            Dict with exit signal or None
        """
        signal = None
        confidence = 0.0
        
        # Signal 1: Decaying intensity with profit
        if intensity_trend == "DECAYING" and current_intensity < 0.8:
            if position_pnl_pct > 1.0:
                signal = "DECAYING_INTENSITY_LOCK_PROFIT"
                confidence = min(100, (position_pnl_pct * 20) + (1.0 - current_intensity) * 50)
            elif position_pnl_pct > 0.2:
                signal = "EARLY_EXIT_MOMENTUM_DECAY"
                confidence = min(100, (1.0 - current_intensity) * 80)
        
        # Signal 2: Extreme decay = momentum collapse
        if current_intensity < 0.3 and len(self.intensity_history) > 5:
            if position_pnl_pct > 0:
                signal = "EMERGENCY_EXIT_MOMENTUM_COLLAPSE"
                confidence = 95
            elif position_pnl_pct < -1.0:
                signal = "CUT_LOSSES_MOMENTUM_BROKEN"
                confidence = 90
        
        # Signal 3: Duration timeout (avoid holding too long)
        if time_in_trade > 3600 and intensity_trend == "DECAYING":  # 1 hour
            signal = "TIMEOUT_EXIT_DECAYING"
            confidence = 75
        
        if signal:
            logger.info(f"Exit signal: {signal} (confidence={confidence:.0f}%, "
                       f"intensity={current_intensity:.2f}, trend={intensity_trend})")
            return {
                "signal": signal,
                "confidence": confidence,
                "intensity": current_intensity,
                "trend": intensity_trend
            }
        
        return None
    
    def update_state(self, state: HawkesState) -> Dict:
        """
        Update with new market state and calculate signals.
        
        Returns:
            Dictionary with calculated metrics
        """
        self.state_history.append(state)
        
        # Calculate intensity
        intensity = self.calculate_intensity(state.timestamp)
        
        # Calculate trend
        trend_slope, trend_label = self.get_intensity_trend(window=10)
        
        # Record reversal score (0.0 = low reversal risk, 1.0 = high reversal risk)
        reversal_score = max(0, 1.0 - intensity / 2.0)  # Inverse of intensity
        self.reversal_scores.append(reversal_score)
        
        metrics = {
            "intensity": intensity,
            "trend_slope": trend_slope,
            "trend": trend_label,
            "reversal_score": reversal_score,
            "num_events_buffered": len(self.event_times),
            "vpin": state.vpin,
            "ofi": state.ofi
        }
        
        logger.debug(f"State update: intensity={intensity:.2f}, "
                    f"trend={trend_label}, reversal_score={reversal_score:.2f}")
        
        return metrics
    
    def get_statistics(self) -> Dict:
        """Return Hawkes process statistics"""
        if not self.intensity_history:
            return {
                "status": "no_data",
                "samples": 0,
                "events_buffered": len(self.event_times)
            }
        
        intensities = np.array(self.intensity_history)
        reversals = np.array(self.reversal_scores) if self.reversal_scores else np.array([])
        
        stats = {
            "samples": len(self.intensity_history),
            "intensity_mean": float(np.mean(intensities)),
            "intensity_std": float(np.std(intensities)),
            "intensity_current": float(intensities[-1]),
            "events_buffered": len(self.event_times)
        }
        
        if len(reversals) > 0:
            stats["reversal_score_mean"] = float(np.mean(reversals))
            stats["reversal_score_current"] = float(reversals[-1])
        
        stats["trend"] = self.get_intensity_trend()[1]
        
        return stats
