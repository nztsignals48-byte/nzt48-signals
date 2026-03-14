"""
tests/test_models.py
=====================
Tests for data models — RVOL Optional, safe defaults.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import IndicatorSnapshot, Signal
from datetime import datetime, timezone


class TestIndicatorSnapshot:
    def test_rvol_defaults_to_none(self):
        snap = IndicatorSnapshot(
            timestamp=datetime.now(timezone.utc),
            ticker="TEST.L",
        )
        assert snap.rvol is None

    def test_rvol_can_be_set(self):
        snap = IndicatorSnapshot(
            timestamp=datetime.now(timezone.utc),
            ticker="TEST.L",
            rvol=2.5,
        )
        assert snap.rvol == 2.5

    def test_rvol_none_is_not_zero(self):
        snap = IndicatorSnapshot(
            timestamp=datetime.now(timezone.utc),
            ticker="TEST.L",
        )
        # rvol=None means "no data", not "zero volume"
        assert snap.rvol is not 0.0
        assert snap.rvol is None


class TestSignal:
    def test_signal_rvol_defaults_to_none(self):
        sig = Signal(
            timestamp=datetime.now(timezone.utc),
            ticker="TEST.L",
        )
        assert sig.rvol is None
