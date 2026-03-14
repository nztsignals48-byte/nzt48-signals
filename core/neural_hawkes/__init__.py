"""
Phase Q6: Neural Hawkes Exit Timing
Self-exciting point process for optimal exit timing
Uses order flow autocorrelation to predict reversal points
"""

from .exit_timing import NeuralHawkesExitTimer, HawkesState

__all__ = ["NeuralHawkesExitTimer", "HawkesState"]
