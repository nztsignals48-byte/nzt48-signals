"""
Cross-Impact OFI Model: Multi-Asset Order Flow Dynamics
Phase Q7-Q8 Implementation
"""

import logging
import numpy as np
from typing import Dict, List, Tuple
from dataclasses import dataclass
import time

logger = logging.getLogger("nzt48.cross_impact")

@dataclass
class ImpactMatrix:
    """Impact matrix snapshot"""
    timestamp: float
    source_ticker: str
    impacts: Dict[str, float]  # {target_ticker: impact_score}
    confidence: float
    lag_ms: int

class TensorDecomposition:
    """
    Tucker decomposition for impact tensors.
    Decomposes: Impact(asset_i, asset_j, lag_k) into lower-rank factors
    """
    
    def __init__(self, num_assets: int, num_lags: int = 60, rank: int = 5):
        """
        Args:
            num_assets: Number of assets in universe
            num_lags: Number of time lags (60 = 60 minutes)
            rank: Rank of decomposition (lower = more compression)
        """
        self.num_assets = num_assets
        self.num_lags = num_lags
        self.rank = rank
        
        # Tucker factors: A (assets), B (assets), C (lags)
        self.factor_a = np.random.normal(0, 0.1, (num_assets, rank))
        self.factor_b = np.random.normal(0, 0.1, (num_assets, rank))
        self.factor_c = np.random.normal(0, 0.1, (num_lags, rank))
        
        # Core tensor (low rank)
        self.core_tensor = np.random.normal(0, 0.05, (rank, rank, rank))
        
        logger.info(f"TensorDecomposition initialized: "
                   f"assets={num_assets}, lags={num_lags}, rank={rank}")
    
    def predict_impact(self, source_idx: int, target_idx: int, lag_idx: int) -> float:
        """
        Predict impact using Tucker decomposition:
        Impact ≈ sum_r sum_s sum_t A[i,r]*B[j,s]*C[k,t]*Core[r,s,t]
        """
        impact = 0.0
        for r in range(self.rank):
            for s in range(self.rank):
                for t in range(self.rank):
                    impact += (self.factor_a[source_idx, r] *
                              self.factor_b[target_idx, s] *
                              self.factor_c[lag_idx, t] *
                              self.core_tensor[r, s, t])
        return impact
    
    def update_factors(self, gradient_a, gradient_b, gradient_c, lr: float = 0.01):
        """Update Tucker factors via gradient descent"""
        self.factor_a -= lr * gradient_a
        self.factor_b -= lr * gradient_b
        self.factor_c -= lr * gradient_c

class CrossImpactModel:
    """
    Models how order flow in one asset impacts price in correlated assets.
    Uses tensor decomposition to capture multi-asset dynamics.
    
    Key insights:
    1. Large OFI in QQQ3.L (leveraged Nasdaq) impacts TSL3.L (Tesla 3x) via correlation
    2. Cross-asset impact decays exponentially with lag
    3. Regime matters: trend vs mean-reversion have opposite signs
    """
    
    def __init__(self, assets: List[str], decay_rate: float = 0.1):
        """
        Args:
            assets: List of ticker symbols (e.g., ["QQQ3.L", "TSL3.L", "NVD3.L"])
            decay_rate: Exponential decay of impact over time
        """
        self.assets = assets
        self.n_assets = len(assets)
        self.decay_rate = decay_rate
        
        # Correlation matrix between assets
        self.correlation_matrix = np.eye(self.n_assets) + np.random.normal(0, 0.1, (self.n_assets, self.n_assets))
        self.correlation_matrix = (self.correlation_matrix + self.correlation_matrix.T) / 2  # Symmetrize
        np.fill_diagonal(self.correlation_matrix, 1.0)  # Ensure diagonal = 1
        
        # Impact tensor: (source_asset, target_asset, lag_minute)
        # 60 minutes of history
        self.impact_tensor = np.random.normal(0, 0.01, (self.n_assets, self.n_assets, 60))
        
        # Regime-dependent impact multipliers
        self.regime_multipliers = {
            "TREND": 1.2,      # Strong cross-asset impact in trends
            "MEAN_REVERSION": 0.6,  # Weak cross-asset impact in mean reversion
            "CHOPPY": 0.8      # Moderate cross-asset impact when choppy
        }
        
        # Tensor decomposition for compression
        self.tensor_decomp = TensorDecomposition(self.n_assets, num_lags=60, rank=5)
        
        # Historical impact logs
        self.impact_history = []
        
        logger.info(f"CrossImpactModel initialized: {self.n_assets} assets "
                   f"({', '.join(assets)}), decay={decay_rate}")
    
    def predict_cross_impact(self, 
                            source_idx: int, 
                            ofi_shock: float,
                            regime: str = "TREND",
                            corr_weight: float = 0.6) -> Dict[str, float]:
        """
        Predict how OFI shock in source_idx impacts all other assets.
        
        Impact = (OFI_shock × tensor_weight × correlation × regime_mult) × decay
        
        Args:
            source_idx: Index of source asset in self.assets
            ofi_shock: Order flow imbalance magnitude
            regime: Current market regime (TREND, MEAN_REVERSION, CHOPPY)
            corr_weight: Weight of correlation in impact calculation (0-1)
            
        Returns:
            Dict mapping target ticker → impact_score
        """
        impacts = {}
        regime_mult = self.regime_multipliers.get(regime, 1.0)
        
        for target_idx in range(self.n_assets):
            # Base impact from tensor
            tensor_impact = self.impact_tensor[source_idx, target_idx, 0]
            
            # Correlation adjustment
            correlation = self.correlation_matrix[source_idx, target_idx]
            
            # Combined impact
            impact = (ofi_shock * tensor_impact * regime_mult)
            impact *= (corr_weight * correlation + (1 - corr_weight))
            
            impacts[self.assets[target_idx]] = impact
        
        return impacts
    
    def predict_impact_over_time(self,
                                source_idx: int,
                                ofi_shock: float,
                                target_idx: int,
                                horizon_minutes: int = 30) -> List[float]:
        """
        Predict impact trajectory over time (decay pattern).
        
        Returns:
            List of impact values for each minute into the future
        """
        impacts = []
        for lag_min in range(horizon_minutes):
            # Apply exponential decay: impact(t) = impact(0) * exp(-λ * t)
            decay_factor = np.exp(-self.decay_rate * lag_min)
            
            # Tensor impact (use decomposition)
            tensor_impact = self.tensor_decomp.predict_impact(source_idx, target_idx, min(lag_min, 59))
            
            impact = ofi_shock * tensor_impact * decay_factor
            impacts.append(impact)
        
        return impacts
    
    def estimate_cross_leverage(self, 
                               positions: Dict[str, float],
                               ofi_shocks: Dict[str, float]) -> float:
        """
        Estimate total portfolio leverage from cross-asset impacts.
        
        Args:
            positions: {ticker: size} current positions
            ofi_shocks: {ticker: ofi_value} order flow imbalances
            
        Returns:
            Estimated portfolio-wide impact (scalar)
        """
        total_impact = 0.0
        
        for source_ticker, ofi_shock in ofi_shocks.items():
            try:
                source_idx = self.assets.index(source_ticker)
            except ValueError:
                continue
            
            impacts = self.predict_cross_impact(source_idx, ofi_shock)
            
            for target_ticker, impact_score in impacts.items():
                if target_ticker in positions:
                    total_impact += positions[target_ticker] * impact_score
        
        return total_impact
    
    def update_impact_tensor(self, 
                            source_idx: int,
                            target_idx: int,
                            lag_idx: int,
                            observed_impact: float,
                            lr: float = 0.01):
        """
        Learn impact tensor from observed data.
        Use exponential moving average for online learning.
        """
        lag_idx = min(lag_idx, 59)
        old_impact = self.impact_tensor[source_idx, target_idx, lag_idx]
        self.impact_tensor[source_idx, target_idx, lag_idx] = (
            (1 - lr) * old_impact + lr * observed_impact
        )
        
        logger.debug(f"Updated impact: {self.assets[source_idx]} → "
                    f"{self.assets[target_idx]} (lag={lag_idx}min): "
                    f"{old_impact:.4f} → {self.impact_tensor[source_idx, target_idx, lag_idx]:.4f}")
    
    def update_correlation_matrix(self, returns: np.ndarray):
        """
        Update correlation matrix from asset returns.
        
        Args:
            returns: (num_assets, num_samples) array of returns
        """
        self.correlation_matrix = np.corrcoef(returns)
        np.fill_diagonal(self.correlation_matrix, 1.0)
        logger.debug(f"Correlation matrix updated from {returns.shape[1]} samples")
    
    def get_statistics(self) -> Dict:
        """Return model statistics"""
        return {
            "num_assets": self.n_assets,
            "assets": self.assets,
            "num_observations": len(self.impact_history),
            "decay_rate": self.decay_rate,
            "correlation_matrix_condition": float(np.linalg.cond(self.correlation_matrix)),
            "tensor_sparsity": float(np.sum(np.abs(self.impact_tensor) < 0.01) / self.impact_tensor.size),
            "regime_multipliers": self.regime_multipliers
        }
