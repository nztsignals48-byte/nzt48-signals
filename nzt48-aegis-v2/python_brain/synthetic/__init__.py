"""Synthetic data generation module."""

try:
    from python_brain.synthetic.diffusion_models import (
        MarketDataGenerator, DiffusionConfig, NoiseScheduler,
        SimpleDenoisingNetwork,
    )
except ImportError:
    pass
