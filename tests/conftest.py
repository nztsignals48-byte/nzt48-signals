"""
tests/conftest.py
==================
Shared test fixtures for NZT-48 test suite.
"""
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set test environment
os.environ.setdefault("NZT48_API_KEY", "test-key-12345")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("TELEGRAM_CHAT_ID", "")


@pytest.fixture
def mock_config():
    """Mock config module."""
    cfg = MagicMock()
    cfg.get.return_value = "10.0"
    return cfg


@pytest.fixture
def sample_indicator_snapshot():
    """Create a sample IndicatorSnapshot for testing."""
    from models import IndicatorSnapshot
    return IndicatorSnapshot(
        timestamp=datetime.now(timezone.utc),
        ticker="QQQ3.L",
        price=45.50,
        atr14=1.2,
        rsi14=62.0,
        macd_histogram=0.15,
        ema9=45.2,
        ema20=44.8,
        ema50=43.5,
        vwap=45.0,
        stochastic_rsi=55.0,
        obv=1000000,
        bb_upper=47.0,
        bb_middle=45.0,
        bb_lower=43.0,
        adx14=28.0,
        rvol=1.5,
    )


@pytest.fixture
def sample_market_context():
    """Create a sample MarketContext for testing."""
    from models import MarketContext
    return MarketContext(
        timestamp=datetime.now(timezone.utc),
    )
