"""
Phase Q7-Q8: Cross-Impact OFI Tensors
Multi-asset order flow impact modeling using tensor decomposition
Models how order flow in one asset impacts price in correlated assets
"""

from .impact_model import CrossImpactModel, TensorDecomposition

__all__ = ["CrossImpactModel", "TensorDecomposition"]
