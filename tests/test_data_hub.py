"""
tests/test_data_hub.py
=======================
Unit tests for DataHub, BarResult, DataReliabilityScore, sources, and normalization.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
from unittest.mock import patch, MagicMock
from dataclasses import fields

import pandas as pd
import numpy as np

from data_hub.hub import DataHub, BarResult
from data_hub.models import DataReliabilityScore
from data_hub.sources.yfinance_source import YFinanceSource
from data_hub.sources.validator_source import ValidatorSource
from data_hub.normalization.price_units import scale_bars


def _make_ohlcv_df(close=100.0, n=5):
    """Helper: create a minimal OHLCV DataFrame with lowercase columns."""
    idx = pd.date_range("2025-01-01", periods=n, freq="h")
    return pd.DataFrame(
        {
            "open": [close * 0.99] * n,
            "high": [close * 1.01] * n,
            "low": [close * 0.98] * n,
            "close": [close] * n,
            "volume": [1000] * n,
        },
        index=idx,
    )


class TestBarResultStructure(unittest.TestCase):
    """1. BarResult dataclass has the correct fields."""

    def test_fields_present(self):
        names = {f.name for f in fields(BarResult)}
        expected = {"ticker", "df", "source", "reliability", "pence_adjusted", "validator_comparison"}
        self.assertEqual(names, expected)

    def test_defaults(self):
        rel = DataReliabilityScore(ticker="X")
        br = BarResult(ticker="X", df=None, source="test", reliability=rel)
        self.assertFalse(br.pence_adjusted)
        self.assertEqual(br.validator_comparison, {})


class TestDataHubInstantiation(unittest.TestCase):
    """2. DataHub creates without error."""

    def test_creates_ok(self):
        hub = DataHub()
        self.assertIsNotNone(hub)
        self.assertIsInstance(hub, DataHub)


class TestDataReliabilityScore(unittest.TestCase):
    """3. DataReliabilityScore has score, source, issues, validated fields."""

    def test_required_fields(self):
        names = {f.name for f in fields(DataReliabilityScore)}
        for expected in ("score", "source", "issues", "validated"):
            self.assertIn(expected, names)

    def test_defaults(self):
        rel = DataReliabilityScore(ticker="QQQ3.L")
        self.assertEqual(rel.score, 1.0)
        self.assertEqual(rel.source, "yfinance")
        self.assertFalse(rel.validated)
        self.assertEqual(rel.issues, [])

    def test_to_dict(self):
        rel = DataReliabilityScore(ticker="QQQ3.L", score=0.85, issues=["pence_normalized"])
        d = rel.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["score"], 0.85)
        self.assertIn("pence_normalized", d["issues"])


class TestColumnNormalization(unittest.TestCase):
    """4. YFinanceSource returns lowercase columns."""

    def test_columns_lowercase(self):
        # Simulate yfinance returning uppercase tuple columns (multi-index style)
        idx = pd.date_range("2025-01-01", periods=3, freq="h")
        raw_df = pd.DataFrame(
            {
                ("Close", "QQQ3.L"): [100.0, 101.0, 102.0],
                ("Open", "QQQ3.L"): [99.0, 100.0, 101.0],
                ("High", "QQQ3.L"): [101.0, 102.0, 103.0],
                ("Low", "QQQ3.L"): [98.0, 99.0, 100.0],
                ("Volume", "QQQ3.L"): [1000, 1100, 1200],
            },
            index=idx,
        )

        mock_yf = MagicMock()
        mock_yf.download.return_value = raw_df

        src = YFinanceSource()
        with patch.dict("sys.modules", {"yfinance": mock_yf}):
            df = src.fetch_bars("QQQ3.L", period="5d", interval="1h")

        self.assertIsNotNone(df)
        for col in df.columns:
            self.assertEqual(col, col.lower(), f"Column '{col}' is not lowercase")


class TestIBKRFallback(unittest.TestCase):
    """5. When IBKR IS_AVAILABLE=False, DataHub falls back to yfinance."""

    @patch.object(YFinanceSource, "fetch_bars")
    def test_falls_back_to_yfinance(self, mock_yf_bars):
        mock_yf_bars.return_value = _make_ohlcv_df(close=50.0)
        hub = DataHub()
        # IBKR is unavailable by default (IS_AVAILABLE = False)
        self.assertFalse(hub._ibkr.IS_AVAILABLE)

        result = hub.get_bars("QQQ3.L", period="5d", interval="1h")
        mock_yf_bars.assert_called_once()
        self.assertEqual(result.source, "yfinance")
        self.assertIsNotNone(result.df)


class TestNoDataReturnsScoreZero(unittest.TestCase):
    """6. When both sources return None, reliability.score == 0.0."""

    @patch.object(YFinanceSource, "fetch_bars", return_value=None)
    def test_score_zero_on_no_data(self, mock_yf_bars):
        hub = DataHub()
        result = hub.get_bars("FAKE.L", period="5d")

        self.assertIsNone(result.df)
        self.assertEqual(result.reliability.score, 0.0)
        self.assertIn("no_data", result.reliability.issues)


class TestSourceStatus(unittest.TestCase):
    """7. get_source_status() returns dict with ibkr, yfinance, validator keys."""

    def test_status_keys(self):
        hub = DataHub()
        status = hub.get_source_status()

        self.assertIsInstance(status, dict)
        for key in ("ibkr", "yfinance", "validator"):
            self.assertIn(key, status)
            self.assertIsInstance(status[key], dict)

    def test_ibkr_unavailable_in_status(self):
        hub = DataHub()
        status = hub.get_source_status()
        self.assertFalse(status["ibkr"]["is_available"])

    def test_yfinance_available_in_status(self):
        hub = DataHub()
        status = hub.get_source_status()
        self.assertTrue(status["yfinance"]["is_available"])


class TestPenceScalingImport(unittest.TestCase):
    """8. scale_bars function exists and is callable."""

    def test_scale_bars_exists(self):
        self.assertTrue(callable(scale_bars))

    def test_scale_bars_noop_for_low_price(self):
        df = _make_ohlcv_df(close=50.0)
        result_df, was_scaled = scale_bars(df, "QQQ3.L")
        self.assertFalse(was_scaled)
        self.assertAlmostEqual(float(result_df["close"].iloc[0]), 50.0)

    def test_scale_bars_converts_pence(self):
        df = _make_ohlcv_df(close=5000.0)
        result_df, was_scaled = scale_bars(df, "QQQ3.L")
        self.assertTrue(was_scaled)
        self.assertAlmostEqual(float(result_df["close"].iloc[0]), 50.0)


class TestValidatorSource(unittest.TestCase):
    """9. ValidatorSource can instantiate and has expected interface."""

    def test_instantiation(self):
        vs = ValidatorSource()
        self.assertIsNotNone(vs)
        self.assertIsInstance(vs, ValidatorSource)

    def test_is_unavailable_by_default(self):
        vs = ValidatorSource()
        self.assertFalse(vs.IS_AVAILABLE)

    def test_compare_returns_unverified_when_unavailable(self):
        vs = ValidatorSource()
        result = vs.compare("QQQ3.L", 100.0, 1000.0, 5)
        self.assertTrue(result["unverified"])
        self.assertIsNone(result["agree"])

    def test_availability_returns_dict(self):
        avail = ValidatorSource.availability()
        self.assertIsInstance(avail, dict)
        self.assertEqual(avail["name"], "polygon")


class TestGetBarsReliabilityScoring(unittest.TestCase):
    """10. Reliability scoring integrates correctly on valid data."""

    @patch.object(YFinanceSource, "fetch_bars")
    def test_valid_data_has_positive_score(self, mock_yf_bars):
        mock_yf_bars.return_value = _make_ohlcv_df(close=50.0)
        hub = DataHub()
        result = hub.get_bars("QQQ3.L", period="5d")

        self.assertGreater(result.reliability.score, 0.0)
        self.assertLessEqual(result.reliability.score, 1.0)
        self.assertEqual(result.reliability.ticker, "QQQ3.L")
        self.assertIsInstance(result.reliability.computed_at, str)
        self.assertGreater(len(result.reliability.computed_at), 0)


if __name__ == "__main__":
    unittest.main()
