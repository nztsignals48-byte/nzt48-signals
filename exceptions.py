"""
exceptions.py
=============
Exception hierarchy for NZT-48 gate classification.

HARD gates: signal MUST be rejected on failure (safety-critical).
SOFT gates: log warning and continue (informational, enrichment).
"""


class NZTGateError(Exception):
    """Base class for gate failures."""
    pass


class HardGateError(NZTGateError):
    """Hard gate failure — signal MUST be rejected.

    Used by: portfolio_risk, immutable_rules, firewall, data_health.
    """
    pass


class SoftGateError(NZTGateError):
    """Soft gate failure — log warning, continue processing.

    Used by: confluence, smart_router, edge_decay, adaptive_intel.
    """
    pass
