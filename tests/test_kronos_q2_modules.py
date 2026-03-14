"""
Unit tests for KRONOS Phase Q2 modules.

Run with: pytest tests/test_kronos_q2_modules.py -v
"""

import pytest
import time
import math
from core.confidence_scorer_v2 import ConfidenceScorerV2, compute_confidence_with_decay
from core.regime_aware_gates import RegimeAwareGates, MarketRegime, get_confidence_threshold_by_regime
from core.vol_aware_scaler import VolAwareScaler, scale_position_by_realized_vol, get_vol_scaling_curve


class TestConfidenceScorerV2:
    """Tests for exponential decay confidence blending"""
    
    def test_add_signal(self):
        """Test adding confidence signals"""
        scorer = ConfidenceScorerV2()
        scorer.add_signal(75.0, source="meta_model", weight=1.0)
        assert len(scorer.signal_buffer) == 1
    
    def test_decay_weighting(self):
        """Test that recent signals are weighted higher than old signals"""
        scorer = ConfidenceScorerV2()
        now = time.time()
        
        # Recent signal: 1 minute ago
        scorer.add_signal(90.0, source="recent", weight=1.0, timestamp=now - 60)
        
        # Old signal: 30 minutes ago
        scorer.add_signal(70.0, source="old", weight=1.0, timestamp=now - 1800)
        
        avg = scorer.compute_confidence_with_decay(lookback_minutes=30)
        
        # Recent signal should dominate
        assert avg > 80.0, f"Expected avg > 80, got {avg}"
    
    def test_empty_buffer_returns_neutral(self):
        """Test that empty buffer returns neutral 50.0"""
        scorer = ConfidenceScorerV2()
        avg = scorer.compute_confidence_with_decay()
        assert avg == 50.0
    
    def test_signal_pruning(self):
        """Test automatic pruning of old signals"""
        scorer = ConfidenceScorerV2()
        now = time.time()
        
        # Add a very old signal
        scorer.add_signal(50.0, source="ancient", timestamp=now - 10000)
        assert len(scorer.signal_buffer) == 1
        
        # Prune signals older than 2 hours
        removed = scorer.prune_old_signals(minutes_cutoff=120)
        assert removed == 1
        assert len(scorer.signal_buffer) == 0


class TestRegimeAwareGates:
    """Tests for regime-based dynamic gating"""
    
    def test_regime_threshold_compression(self):
        """Test entry threshold in COMPRESSION regime"""
        gates = RegimeAwareGates()
        gates.set_regime(MarketRegime.COMPRESSION)
        assert gates.get_entry_threshold() == 60.0
    
    def test_regime_threshold_shock(self):
        """Test entry threshold in SHOCK regime"""
        gates = RegimeAwareGates()
        gates.set_regime(MarketRegime.SHOCK)
        assert gates.get_entry_threshold() == 75.0  # Higher bar in shock
    
    def test_should_enter_decision(self):
        """Test entry decision logic"""
        gates = RegimeAwareGates()
        gates.set_regime(MarketRegime.EXPANSION)  # Threshold 70
        
        # Above threshold
        should_enter, reason = gates.should_enter(72.0)
        assert should_enter is True
        
        # Below threshold
        should_enter, reason = gates.should_enter(65.0)
        assert should_enter is False
    
    def test_position_size_multiplier_by_regime(self):
        """Test position sizing varies by regime"""
        gates = RegimeAwareGates()
        
        gates.set_regime(MarketRegime.COMPRESSION)
        compression_mult = gates.get_position_size_multiplier()
        
        gates.set_regime(MarketRegime.EXPANSION)
        expansion_mult = gates.get_position_size_multiplier()
        
        assert expansion_mult > compression_mult  # Bigger positions in expansion
    
    def test_regime_confidence_dampening(self):
        """Test that low regime confidence reduces multiplier"""
        gates = RegimeAwareGates()
        gates.set_regime(MarketRegime.EXPANSION, confidence=1.0)
        full_confidence_mult = gates.get_position_size_multiplier()
        
        gates.set_regime(MarketRegime.EXPANSION, confidence=0.5)
        half_confidence_mult = gates.get_position_size_multiplier()
        
        assert half_confidence_mult < full_confidence_mult


class TestVolAwareScaler:
    """Tests for volatility-aware position scaling"""
    
    def test_scale_high_volatility(self):
        """Test reduced position size in high volatility"""
        scaled = scale_position_by_realized_vol(percentile=95.0, base_size=1000)
        assert scaled == 500  # 50% in extreme vol
    
    def test_scale_low_volatility(self):
        """Test increased position size in low volatility"""
        scaled = scale_position_by_realized_vol(percentile=5.0, base_size=1000)
        assert scaled == 1300  # 130% in extremely low vol
    
    def test_scale_normal_volatility(self):
        """Test baseline position size in normal volatility"""
        scaled = scale_position_by_realized_vol(percentile=50.0, base_size=1000)
        assert scaled == 1000  # 100% (no adjustment)
    
    def test_volatility_buffer_management(self):
        """Test vol buffer accumulation and stats"""
        scaler = VolAwareScaler(lookback_periods=10)
        
        # Add volatility samples
        for i in range(5):
            scaler.add_volatility_sample(0.04 + i * 0.01)
        
        stats = scaler.get_vol_stats()
        assert stats['count'] == 5
        assert stats['latest_vol'] == 0.08
    
    def test_scaling_curve_smoothness(self):
        """Test that scaling curve is monotonic (no discontinuities)"""
        curve = get_vol_scaling_curve()
        
        # Check that multipliers decrease as percentile increases
        for i in range(len(curve) - 1):
            assert curve[i][1] >= curve[i + 1][1], "Scaling should decrease with vol"


class TestIntegration:
    """Integration tests between modules"""
    
    def test_confidence_scorer_and_regime_gates(self):
        """Test confidence scorer working with regime gates"""
        scorer = ConfidenceScorerV2()
        gates = RegimeAwareGates()
        
        # Add signals
        scorer.add_signal(75.0, source="model", weight=1.0)
        avg_confidence = scorer.compute_confidence_with_decay()
        
        # Check if signal passes gate in different regimes
        gates.set_regime(MarketRegime.COMPRESSION)
        passes_compression, _ = gates.should_enter(avg_confidence)
        
        gates.set_regime(MarketRegime.SHOCK)
        passes_shock, _ = gates.should_enter(avg_confidence)
        
        # Same signal should fail in SHOCK but pass in COMPRESSION
        assert passes_compression and not passes_shock
    
    def test_all_modules_together(self):
        """Test all three Q2 modules working together"""
        scorer = ConfidenceScorerV2()
        gates = RegimeAwareGates()
        scaler = VolAwareScaler()
        
        # Simulate trading signal
        scorer.add_signal(72.0, source="meta_model", weight=1.5)
        confidence = scorer.compute_confidence_with_decay()
        
        # Check entry condition
        gates.set_regime(MarketRegime.EXPANSION, confidence=0.9)
        should_enter, _ = gates.should_enter(confidence)
        
        # Get position size
        if should_enter:
            base_size = 1000
            regime_multiplier = gates.get_position_size_multiplier()
            
            scaler.add_volatility_sample(0.035)
            vol_multiplier = scaler.get_scaling_factor_current()
            
            final_size = int(base_size * regime_multiplier * vol_multiplier)
            assert final_size > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
