"""Analytics module — market microstructure and signal analysis."""

try:
    from python_brain.analytics.vpin_calculator import (
        VPINCalculator, VPINConfig, VolumeBucket,
    )
except ImportError:
    pass
