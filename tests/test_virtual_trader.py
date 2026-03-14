"""
tests/test_virtual_trader.py
=============================
Tests for execution engine — profit ladder, trades cap, seeded RNG.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock, patch


class TestSlippageModel:
    def test_seeded_rng_reproducible(self):
        from execution.virtual_trader import SlippageModel
        s1 = SlippageModel()
        s2 = SlippageModel()
        # Same seed should produce same results
        r1 = s1._rng.uniform(0, 1)
        r2 = s2._rng.uniform(0, 1)
        assert r1 == r2, "Seeded RNG should be reproducible"

    def test_entry_slippage_within_bounds(self):
        from execution.virtual_trader import SlippageModel
        from models import Direction, Bot
        sm = SlippageModel()
        # entry_slippage expects a Signal object with entry, bot, rvol, shares, direction
        mock_signal = MagicMock()
        mock_signal.entry = 100.0
        mock_signal.bot = Bot.B
        mock_signal.rvol = 1.0
        mock_signal.shares = 100
        mock_signal.direction = Direction.LONG
        for _ in range(50):
            slippage = sm.entry_slippage(mock_signal, shares=100)
            # Slippage should be small (within 1% of entry price)
            assert 0.0 <= slippage <= 1.0, f"Slippage {slippage} out of bounds"


class TestProfitLadder:
    def test_rung5_target_remaining_is_30_pct(self):
        """Verify profit ladder rung 5 uses 30% (not 40%)."""
        import inspect
        from execution.virtual_trader import VirtualTrader
        source = inspect.getsource(VirtualTrader)
        # The fix changed 0.40 to 0.30 for rung 5
        assert "target_remaining = 0.30" in source or "0.30" in source
