#!/usr/bin/env python3
"""
NZT-48 Trading System -- 5-Year Historical Data Backfill Script
================================================================

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!  STUB ONLY -- THIS SCRIPT DOES NOT RUN WITHOUT:            !!
!!  1. Explicit approval from the system operator              !!
!!  2. API keys configured in environment variables            !!
!!  3. The research_data/ directory structure created           !!
!!                                                             !!
!!  REQUIRES APPROVAL AND API KEYS TO RUN                      !!
!!  DO NOT EXECUTE IN PRODUCTION WITHOUT REVIEW                !!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

This script provides the framework for:
  1. Provider abstraction (abstract base class + provider implementations)
  2. Backfill orchestration logic (phased, with verification)
  3. Data validation functions
  4. Storage management (parquet file I/O)

All provider implementations are STUBS that raise NotImplementedError
or return empty DataFrames until properly configured.

See docs/HISTORICAL_DATA_BACKFILL_PLAN.md for the full plan.
See docs/DATA_VENDOR_MIGRATION_PLAN.md for the migration strategy.
See research_data/README.md for the storage schema.

Usage (when ready):
    python scripts/backfill_5y.py --dry-run              # Preview what would be fetched
    python scripts/backfill_5y.py --ticker QQQ3.L        # Backfill single ticker
    python scripts/backfill_5y.py --phase daily           # Backfill all tickers, daily only
    python scripts/backfill_5y.py --phase all --confirm   # Full backfill (requires --confirm)
"""

from __future__ import annotations

import abc
import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Safety check: refuse to run without explicit --confirm flag
# ---------------------------------------------------------------------------
_SAFETY_BANNER = """
============================================================
  NZT-48 HISTORICAL DATA BACKFILL -- STUB SCRIPT
============================================================
  This script is a SCAFFOLD / STUB.
  Provider implementations are placeholders only.

  To run the yfinance-based backfill (the only currently
  functional provider), use:

    python scripts/backfill_5y.py --phase daily --confirm

  To preview without fetching:

    python scripts/backfill_5y.py --dry-run

  For other providers (IBKR, Polygon, Stooq), the stubs
  must be implemented first. See:
    docs/DATA_VENDOR_MIGRATION_PLAN.md
============================================================
"""

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RESEARCH_DATA = _PROJECT_ROOT / "research_data"
_INDEX_DIR = _RESEARCH_DATA / "_index"
_ANALYTICS_DIR = _RESEARCH_DATA / "_analytics"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)-7s]  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("nzt48.backfill")

# ---------------------------------------------------------------------------
# Universe definition (mirrors uk_isa/isa_universe.py)
# ---------------------------------------------------------------------------
CORE_UNIVERSE: list[str] = [
    "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L",
    "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L",
]

EXTENDED_UNIVERSE: list[str] = CORE_UNIVERSE + [
    "AMD3.L", "ARM3.L", "NVDS.L", "TSLS.L",
    "3LDE.L", "3LEU.L", "3GOL.L", "3SIL.L", "3OIL.L",
]

INTEL_UNIVERSE: list[str] = [
    "QQQ", "SPY", "SMH", "SOXX", "^VIX", "TLT", "GLD", "USO",
    "DX-Y.NYB", "NVDA", "TSLA", "TSM", "MU", "AMD",
]


class Timeframe(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    HOURLY = "hourly"
    FIVE_MIN = "intraday_5m"
    ONE_MIN = "intraday_1m"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class BackfillRequest:
    """Specification for a single ticker/timeframe backfill."""
    ticker: str
    timeframe: Timeframe
    start_date: date
    end_date: date
    provider: str = "yfinance"
    priority: int = 0  # 0 = highest


@dataclass
class BackfillResult:
    """Result of a single backfill attempt."""
    ticker: str
    timeframe: str
    provider: str
    success: bool
    bars_fetched: int = 0
    start_date: str = ""
    end_date: str = ""
    file_path: str = ""
    errors: list[str] = field(default_factory=list)
    quality_score: float = 0.0


@dataclass
class ValidationResult:
    """Result of data quality validation."""
    ticker: str
    timeframe: str
    total_bars: int = 0
    gap_count: int = 0
    outlier_count: int = 0
    zero_volume_count: int = 0
    ohlc_violations: int = 0
    nan_count: int = 0
    quality_score: float = 0.0
    issues: list[str] = field(default_factory=list)


# ===========================================================================
# ABSTRACT BASE CLASS: DataProvider
# ===========================================================================

class DataProvider(abc.ABC):
    """
    Abstract base class for all data providers.

    Every data source (yfinance, IBKR, Polygon, Stooq, etc.) must implement
    this interface. This ensures the backfill orchestrator can work with any
    provider without knowing the implementation details.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""
        ...

    @property
    @abc.abstractmethod
    def is_available(self) -> bool:
        """Whether this provider is configured and ready to use."""
        ...

    @abc.abstractmethod
    def fetch_daily_bars(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """
        Fetch daily OHLCV bars for the given ticker and date range.

        Returns a DataFrame with columns:
            timestamp (index, datetime64[ns, UTC]),
            open, high, low, close, volume (all float64)

        Returns empty DataFrame on failure. Must never raise.
        """
        ...

    @abc.abstractmethod
    def fetch_intraday_bars(
        self,
        ticker: str,
        interval: str,  # "1m", "5m", "1h"
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """
        Fetch intraday OHLCV bars.

        Same return schema as fetch_daily_bars, plus 'bar_interval' column.
        Returns empty DataFrame on failure. Must never raise.
        """
        ...

    @abc.abstractmethod
    def fetch_weekly_bars(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Fetch weekly OHLCV bars. Same return schema as daily."""
        ...

    def supports_ticker(self, ticker: str) -> bool:
        """Check if this provider supports a given ticker. Override if needed."""
        return True

    def rate_limit_wait(self) -> float:
        """Seconds to wait between requests. Override per provider."""
        return 0.5


# ===========================================================================
# PROVIDER IMPLEMENTATIONS (STUBS)
# ===========================================================================

class YFinanceProvider(DataProvider):
    """
    yfinance provider -- the only currently functional implementation.

    IMPORTANT: This is the ONLY provider that can actually fetch data today.
    All other providers are stubs that raise NotImplementedError.

    Known limitations:
    - Volume data for .L tickers often unreliable
    - Pence/pounds confusion for LSE ETPs
    - 1m bars limited to 7 calendar days of history
    - 1h bars limited to ~730 days of history
    - No SLA, can break at any time
    """

    @property
    def name(self) -> str:
        return "yfinance"

    @property
    def is_available(self) -> bool:
        try:
            import yfinance  # noqa: F401
            return True
        except ImportError:
            return False

    def fetch_daily_bars(
        self, ticker: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        try:
            import yfinance as yf
            df = yf.download(
                ticker,
                start=start_date.isoformat(),
                end=(end_date + timedelta(days=1)).isoformat(),
                interval="1d",
                auto_adjust=True,
                progress=False,
                timeout=30,
            )
            return self._normalize(df)
        except Exception as exc:
            logger.warning("[yfinance] daily fetch failed for %s: %s", ticker, exc)
            return pd.DataFrame()

    def fetch_intraday_bars(
        self, ticker: str, interval: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        # yfinance intraday limitations:
        # 1m: max 7 calendar days
        # 5m: max 60 calendar days
        # 1h: max ~730 calendar days
        period_map = {"1m": "7d", "5m": "60d", "1h": "730d"}
        period = period_map.get(interval, "60d")
        try:
            import yfinance as yf
            df = yf.download(
                ticker,
                period=period,
                interval=interval,
                auto_adjust=True,
                progress=False,
                timeout=30,
            )
            df = self._normalize(df)
            if not df.empty:
                df["bar_interval"] = interval
            return df
        except Exception as exc:
            logger.warning("[yfinance] intraday fetch failed for %s (%s): %s", ticker, interval, exc)
            return pd.DataFrame()

    def fetch_weekly_bars(
        self, ticker: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        try:
            import yfinance as yf
            df = yf.download(
                ticker,
                start=start_date.isoformat(),
                end=(end_date + timedelta(days=1)).isoformat(),
                interval="1wk",
                auto_adjust=True,
                progress=False,
                timeout=30,
            )
            return self._normalize(df)
        except Exception as exc:
            logger.warning("[yfinance] weekly fetch failed for %s: %s", ticker, exc)
            return pd.DataFrame()

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        """Normalize yfinance output to standard schema."""
        if df is None or df.empty:
            return pd.DataFrame()

        # Handle MultiIndex columns from yfinance >= 0.2.40
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]

        # Ensure standard column names
        rename = {}
        for col in df.columns:
            cl = col.lower().strip()
            if "open" in cl and "adj" not in cl:
                rename[col] = "open"
            elif "high" in cl:
                rename[col] = "high"
            elif "low" in cl:
                rename[col] = "low"
            elif "close" in cl and "adj" not in cl:
                rename[col] = "close"
            elif "volume" in cl:
                rename[col] = "volume"
        if rename:
            df = df.rename(columns=rename)

        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep].copy()

        # Ensure numeric
        for col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Ensure timezone-aware UTC index
        df.index.name = "timestamp"
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        return df

    def rate_limit_wait(self) -> float:
        return 1.0  # Be polite to Yahoo


class IBKRProvider(DataProvider):
    """
    IBKR Historical Data provider via ib_insync.

    STUB -- requires:
    1. ib_insync installed (pip install ib_insync)
    2. TWS or IB Gateway running on localhost:7497
    3. LSE market data subscription on IBKR account

    This is the RECOMMENDED primary source for LSE leveraged ETPs.
    """

    @property
    def name(self) -> str:
        return "ibkr"

    @property
    def is_available(self) -> bool:
        # STUB: always returns False until ib_insync is configured
        return False

    def fetch_daily_bars(
        self, ticker: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        raise NotImplementedError(
            "IBKRProvider.fetch_daily_bars() is a STUB. "
            "Implement using ib_insync.IB().reqHistoricalData() "
            "with barSizeSetting='1 day' and whatToShow='TRADES'."
        )

    def fetch_intraday_bars(
        self, ticker: str, interval: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        raise NotImplementedError(
            "IBKRProvider.fetch_intraday_bars() is a STUB. "
            "Implement using ib_insync with appropriate barSizeSetting."
        )

    def fetch_weekly_bars(
        self, ticker: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        raise NotImplementedError(
            "IBKRProvider.fetch_weekly_bars() is a STUB. "
            "Implement using ib_insync with barSizeSetting='1 week'."
        )

    def rate_limit_wait(self) -> float:
        return 10.0  # IBKR is strict: ~60 requests per 10 minutes


class PolygonProvider(DataProvider):
    """
    Polygon.io data provider.

    STUB -- requires:
    1. POLYGON_API_KEY environment variable set
    2. polygon-api-client package installed

    Best for US equities (QQQ, SPY, NVDA, etc.).
    LSE .L ticker coverage is LIMITED for leveraged ETPs.
    """

    @property
    def name(self) -> str:
        return "polygon"

    @property
    def is_available(self) -> bool:
        return bool(os.environ.get("POLYGON_API_KEY"))

    def fetch_daily_bars(
        self, ticker: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        if not self.is_available:
            return pd.DataFrame()
        raise NotImplementedError(
            "PolygonProvider.fetch_daily_bars() is a STUB. "
            "Implement using polygon.RESTClient().get_aggs() "
            "with multiplier=1, timespan='day'."
        )

    def fetch_intraday_bars(
        self, ticker: str, interval: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        if not self.is_available:
            return pd.DataFrame()
        raise NotImplementedError(
            "PolygonProvider.fetch_intraday_bars() is a STUB."
        )

    def fetch_weekly_bars(
        self, ticker: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        if not self.is_available:
            return pd.DataFrame()
        raise NotImplementedError(
            "PolygonProvider.fetch_weekly_bars() is a STUB."
        )

    def supports_ticker(self, ticker: str) -> bool:
        # Polygon is best for US tickers, limited for LSE .L
        if ticker.endswith(".L"):
            logger.debug("[Polygon] LSE .L ticker %s -- coverage uncertain", ticker)
            return False  # Conservative: assume not available until verified
        return True

    def rate_limit_wait(self) -> float:
        return 0.1  # Polygon has generous limits on paid plans


class StooqProvider(DataProvider):
    """
    Stooq.com daily historical data provider.

    STUB -- uses pandas_datareader or direct CSV download.
    Free, no API key required.
    Covers some WisdomTree ETPs but NOT GraniteShares single-stock ETPs.
    Daily data ONLY (no intraday).
    """

    @property
    def name(self) -> str:
        return "stooq"

    @property
    def is_available(self) -> bool:
        return True  # Free, always available (subject to rate limits)

    def _ticker_to_stooq(self, ticker: str) -> str:
        """Convert Yahoo Finance ticker to Stooq format."""
        if ticker.endswith(".L"):
            return ticker.replace(".L", ".UK")
        return ticker

    def fetch_daily_bars(
        self, ticker: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        raise NotImplementedError(
            "StooqProvider.fetch_daily_bars() is a STUB. "
            "Implement using pandas_datareader.data.DataReader(ticker, 'stooq') "
            "or direct CSV: https://stooq.com/q/d/l/?s={stooq_ticker}&d1={start}&d2={end}&i=d"
        )

    def fetch_intraday_bars(
        self, ticker: str, interval: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        # Stooq does NOT provide intraday data
        logger.debug("[Stooq] Intraday not available for %s", ticker)
        return pd.DataFrame()

    def fetch_weekly_bars(
        self, ticker: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        raise NotImplementedError(
            "StooqProvider.fetch_weekly_bars() is a STUB. "
            "Implement using Stooq CSV with i=w parameter."
        )

    def supports_ticker(self, ticker: str) -> bool:
        # Stooq covers WisdomTree ETPs, uncertain for GraniteShares
        known_stooq_tickers = {
            "QQQ3.L", "3LUS.L", "QQQS.L", "3USS.L",
            "3LDE.L", "3LEU.L", "3GOL.L", "3SIL.L", "3OIL.L",
        }
        if ticker in known_stooq_tickers:
            return True
        if not ticker.endswith(".L"):
            return True  # US tickers generally available
        return False  # GraniteShares single-stock ETPs likely not on Stooq

    def rate_limit_wait(self) -> float:
        return 6.0  # Be very polite to Stooq (free service)


class AlphaVantageProvider(DataProvider):
    """
    Alpha Vantage data provider.

    STUB -- requires ALPHA_VANTAGE_KEY environment variable.
    Free tier: 25 calls/day. Premium: $49/mo for 75 calls/min.
    LSE .L coverage is unreliable for leveraged ETPs.
    """

    @property
    def name(self) -> str:
        return "alpha_vantage"

    @property
    def is_available(self) -> bool:
        return bool(os.environ.get("ALPHA_VANTAGE_KEY"))

    def fetch_daily_bars(
        self, ticker: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        if not self.is_available:
            return pd.DataFrame()
        raise NotImplementedError(
            "AlphaVantageProvider.fetch_daily_bars() is a STUB."
        )

    def fetch_intraday_bars(
        self, ticker: str, interval: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        if not self.is_available:
            return pd.DataFrame()
        raise NotImplementedError(
            "AlphaVantageProvider.fetch_intraday_bars() is a STUB."
        )

    def fetch_weekly_bars(
        self, ticker: str, start_date: date, end_date: date,
    ) -> pd.DataFrame:
        if not self.is_available:
            return pd.DataFrame()
        raise NotImplementedError(
            "AlphaVantageProvider.fetch_weekly_bars() is a STUB."
        )

    def rate_limit_wait(self) -> float:
        return 12.5  # 25 calls/day = ~1 per minute on free tier; be conservative


# ===========================================================================
# PROVIDER REGISTRY
# ===========================================================================

def get_all_providers() -> dict[str, DataProvider]:
    """Return all configured providers."""
    return {
        "yfinance": YFinanceProvider(),
        "ibkr": IBKRProvider(),
        "polygon": PolygonProvider(),
        "stooq": StooqProvider(),
        "alpha_vantage": AlphaVantageProvider(),
    }


def get_available_providers() -> dict[str, DataProvider]:
    """Return only providers that are currently available."""
    return {k: v for k, v in get_all_providers().items() if v.is_available}


# ===========================================================================
# DATA VALIDATION FUNCTIONS
# ===========================================================================

def validate_bars(ticker: str, df: pd.DataFrame, timeframe: str) -> ValidationResult:
    """
    Validate a DataFrame of OHLCV bars for data quality.

    Checks:
    1. No NaN or Inf values
    2. OHLC sanity (high >= low, high >= max(open, close), etc.)
    3. No negative prices or volumes
    4. No excessive gaps (missing trading days)
    5. No outlier daily returns (> 30% for leveraged ETPs)
    6. Volume plausibility (not all zeros)

    Returns a ValidationResult with quality score (0-100).
    """
    result = ValidationResult(ticker=ticker, timeframe=timeframe)

    if df is None or df.empty:
        result.issues.append("EMPTY: No data to validate")
        result.quality_score = 0.0
        return result

    result.total_bars = len(df)

    # 1. NaN / Inf check
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            result.issues.append(f"MISSING_COLUMN: {col}")
            continue
        nan_count = int(df[col].isna().sum())
        if nan_count > 0:
            result.nan_count += nan_count
            result.issues.append(f"NAN_{col.upper()}: {nan_count} NaN values")
        try:
            import numpy as np
            inf_count = int(np.isinf(df[col].values.astype(float)).sum())
            if inf_count > 0:
                result.issues.append(f"INF_{col.upper()}: {inf_count} Inf values")
        except (ValueError, TypeError):
            pass

    # 2. OHLC sanity
    if all(c in df.columns for c in ["open", "high", "low", "close"]):
        h = df["high"].values
        l = df["low"].values
        o = df["open"].values
        c = df["close"].values
        violations = int(((h < l) | (h < o) | (h < c) | (l > o) | (l > c)).sum())
        result.ohlc_violations = violations
        if violations > 0:
            result.issues.append(f"OHLC_VIOLATION: {violations} bars with H<L or H<O/C or L>O/C")

    # 3. Negative prices
    for col in ["open", "high", "low", "close"]:
        if col in df.columns:
            neg_count = int((df[col] <= 0).sum())
            if neg_count > 0:
                result.issues.append(f"NEGATIVE_{col.upper()}: {neg_count} bars with {col} <= 0")

    # 4. Zero volume
    if "volume" in df.columns:
        zero_vol = int((df["volume"] == 0).sum())
        result.zero_volume_count = zero_vol
        if zero_vol > result.total_bars * 0.5:
            result.issues.append(
                f"ZERO_VOLUME: {zero_vol}/{result.total_bars} bars "
                f"({zero_vol / result.total_bars * 100:.0f}%) have zero volume"
            )

    # 5. Outlier returns (daily only)
    if timeframe == "daily" and "close" in df.columns and len(df) >= 2:
        returns = df["close"].pct_change().dropna()
        outliers = int((returns.abs() > 0.30).sum())
        result.outlier_count = outliers
        if outliers > 0:
            result.issues.append(
                f"OUTLIER_RETURNS: {outliers} days with |return| > 30%"
            )

    # 6. Gap detection (daily only -- check for missing trading days)
    if timeframe == "daily" and len(df) >= 10:
        # Simple heuristic: if median gap between bars > 3 calendar days, flag it
        if hasattr(df.index, 'to_series'):
            gaps = df.index.to_series().diff().dt.days.dropna()
            large_gaps = int((gaps > 5).sum())  # > 5 calendar days = likely missing data
            result.gap_count = large_gaps
            if large_gaps > 0:
                result.issues.append(
                    f"DATA_GAPS: {large_gaps} gaps > 5 calendar days detected"
                )

    # Compute quality score
    score = 100.0
    if result.total_bars == 0:
        score = 0.0
    else:
        # Penalise for issues
        nan_penalty = min(30.0, (result.nan_count / result.total_bars) * 100)
        ohlc_penalty = min(15.0, (result.ohlc_violations / result.total_bars) * 100)
        volume_penalty = min(15.0, (result.zero_volume_count / result.total_bars) * 50)
        outlier_penalty = min(20.0, result.outlier_count * 5.0)
        gap_penalty = min(20.0, result.gap_count * 5.0)
        score = max(0.0, score - nan_penalty - ohlc_penalty - volume_penalty - outlier_penalty - gap_penalty)

    result.quality_score = round(score, 1)
    return result


# ===========================================================================
# PENCE / POUNDS NORMALISATION (mirrors data_hub/normalization/price_units.py)
# ===========================================================================

_PENCE_THRESHOLD = 200.0
_MAX_PLAUSIBLE_PENCE = 50_000.0

# Expected price ranges from isa_universe.py
_EXPECTED_RANGES: dict[str, tuple[float, float]] = {
    "QQQ3.L":  (0.5, 200.0), "3LUS.L":  (1.0, 200.0), "QQQ5.L":  (0.5, 100.0),
    "SP5L.L":  (1.0, 150.0), "QQQS.L":  (0.5, 80.0),  "3USS.L":  (0.5, 60.0),
    "3SEM.L":  (0.5, 80.0),  "GPT3.L":  (0.5, 80.0),  "NVD3.L":  (0.5, 100.0),
    "TSL3.L":  (0.1, 80.0),  "TSM3.L":  (0.5, 60.0),  "MU2.L":   (0.5, 50.0),
}


def normalise_pence_to_pounds(df: pd.DataFrame, ticker: str) -> tuple[pd.DataFrame, bool]:
    """
    Detect if prices are in pence (GBX) and convert to pounds (GBP).
    Returns (normalised_df, was_converted).
    """
    if not ticker.endswith(".L") or df.empty or "close" not in df.columns:
        return df, False

    last_close = float(df["close"].iloc[-1])
    _, max_gbp = _EXPECTED_RANGES.get(ticker, (0.01, 500.0))

    if last_close > max_gbp and last_close < _MAX_PLAUSIBLE_PENCE:
        logger.info(
            "[PENCE] %s close=%.2f exceeds max GBP %.1f -- converting pence to pounds",
            ticker, last_close, max_gbp,
        )
        df = df.copy()
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = df[col] / 100.0
        return df, True

    return df, False


# ===========================================================================
# STORAGE FUNCTIONS
# ===========================================================================

def ensure_ticker_dirs(ticker: str) -> dict[str, Path]:
    """Create the directory structure for a ticker. Returns path dict."""
    base = _RESEARCH_DATA / ticker
    dirs = {
        "daily": base / "daily",
        "weekly": base / "weekly",
        "hourly": base / "hourly",
        "intraday_5m": base / "intraday_5m",
        "intraday_1m": base / "intraday_1m",
        "meta": base / "meta",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def save_parquet(df: pd.DataFrame, path: Path, ticker: str, source: str) -> bool:
    """
    Save a DataFrame as a parquet file with standard metadata columns.
    Returns True on success.
    """
    if df is None or df.empty:
        logger.warning("[SAVE] Empty DataFrame for %s -- skipping", ticker)
        return False

    try:
        df = df.copy()
        df["source"] = source
        df["is_adjusted"] = True  # yfinance auto_adjust=True
        df.to_parquet(path, engine="pyarrow", index=True)
        logger.info("[SAVE] %s -> %s (%d bars)", ticker, path.name, len(df))
        return True
    except ImportError:
        logger.error("[SAVE] pyarrow not installed -- cannot write parquet. pip install pyarrow")
        return False
    except Exception as exc:
        logger.error("[SAVE] Failed to write %s: %s", path, exc)
        return False


def load_progress() -> dict:
    """Load backfill progress tracking file."""
    progress_file = _INDEX_DIR / "backfill_progress.json"
    if progress_file.exists():
        try:
            return json.loads(progress_file.read_text())
        except Exception:
            pass
    return {"tickers": {}, "last_run": None}


def save_progress(progress: dict) -> None:
    """Save backfill progress tracking file."""
    _INDEX_DIR.mkdir(parents=True, exist_ok=True)
    progress["last_run"] = datetime.now(timezone.utc).isoformat()
    progress_file = _INDEX_DIR / "backfill_progress.json"
    progress_file.write_text(json.dumps(progress, indent=2, default=str))


# ===========================================================================
# BACKFILL ORCHESTRATOR
# ===========================================================================

class BackfillOrchestrator:
    """
    Orchestrates the 5-year historical data backfill.

    Phases:
    1. daily   -- Daily OHLCV bars for all tickers (yfinance max period)
    2. weekly  -- Weekly bars for all tickers
    3. hourly  -- 1-hour bars (limited by provider constraints)
    4. 5min    -- 5-minute bars (limited history)
    5. 1min    -- 1-minute bars (very limited history)
    6. validate -- Run validation pipeline on all stored data
    """

    def __init__(self, providers: Optional[dict[str, DataProvider]] = None):
        self._providers = providers or get_available_providers()
        self._progress = load_progress()
        self._results: list[BackfillResult] = []

    @property
    def primary_provider(self) -> Optional[DataProvider]:
        """Get the best available provider (prefer IBKR > Polygon > yfinance)."""
        priority = ["ibkr", "polygon", "yfinance"]
        for name in priority:
            if name in self._providers:
                return self._providers[name]
        return None

    def run(
        self,
        tickers: Optional[list[str]] = None,
        phase: str = "daily",
        dry_run: bool = False,
    ) -> list[BackfillResult]:
        """
        Execute the backfill.

        Parameters
        ----------
        tickers : list[str] or None
            Tickers to backfill. None = full CORE + EXTENDED + INTEL universe.
        phase : str
            "daily", "weekly", "hourly", "5min", "1min", "all", "validate"
        dry_run : bool
            If True, print what would be done without fetching data.
        """
        if tickers is None:
            tickers = CORE_UNIVERSE + EXTENDED_UNIVERSE + INTEL_UNIVERSE
            # Deduplicate while preserving order
            tickers = list(dict.fromkeys(tickers))

        provider = self.primary_provider
        if provider is None:
            logger.error("No data provider available. Cannot proceed.")
            return []

        logger.info(
            "Backfill starting: %d tickers, phase=%s, provider=%s, dry_run=%s",
            len(tickers), phase, provider.name, dry_run,
        )

        end_date = date.today()
        start_date_5y = end_date - timedelta(days=5 * 365)

        phases_to_run = []
        if phase in ("daily", "all"):
            phases_to_run.append(("daily", start_date_5y, end_date, "1d"))
        if phase in ("weekly", "all"):
            phases_to_run.append(("weekly", start_date_5y, end_date, "1wk"))
        if phase in ("hourly", "all"):
            phases_to_run.append(("hourly", end_date - timedelta(days=730), end_date, "1h"))
        if phase in ("5min", "all"):
            phases_to_run.append(("intraday_5m", end_date - timedelta(days=60), end_date, "5m"))
        if phase in ("1min", "all"):
            phases_to_run.append(("intraday_1m", end_date - timedelta(days=7), end_date, "1m"))

        for phase_name, start, end, interval in phases_to_run:
            logger.info("--- Phase: %s (%s to %s) ---", phase_name, start, end)
            for ticker in tickers:
                if dry_run:
                    logger.info(
                        "[DRY RUN] Would fetch %s %s from %s (%s -> %s)",
                        ticker, phase_name, provider.name, start, end,
                    )
                    continue

                result = self._backfill_ticker(
                    ticker=ticker,
                    provider=provider,
                    timeframe=phase_name,
                    interval=interval,
                    start_date=start,
                    end_date=end,
                )
                self._results.append(result)

        if phase in ("validate", "all") and not dry_run:
            self._validate_all(tickers)

        save_progress(self._progress)
        return self._results

    def _backfill_ticker(
        self,
        ticker: str,
        provider: DataProvider,
        timeframe: str,
        interval: str,
        start_date: date,
        end_date: date,
    ) -> BackfillResult:
        """Backfill a single ticker/timeframe combination."""
        result = BackfillResult(
            ticker=ticker,
            timeframe=timeframe,
            provider=provider.name,
            success=False,
        )

        # Check if already completed
        progress_key = f"{ticker}_{timeframe}"
        if progress_key in self._progress.get("tickers", {}):
            prev = self._progress["tickers"][progress_key]
            if prev.get("status") == "complete":
                logger.debug("[SKIP] %s %s already complete", ticker, timeframe)
                result.success = True
                result.bars_fetched = prev.get("bars", 0)
                return result

        try:
            # Fetch data
            import time
            time.sleep(provider.rate_limit_wait())

            if timeframe == "daily":
                df = provider.fetch_daily_bars(ticker, start_date, end_date)
            elif timeframe == "weekly":
                df = provider.fetch_weekly_bars(ticker, start_date, end_date)
            else:
                df = provider.fetch_intraday_bars(ticker, interval, start_date, end_date)

            if df is None or df.empty:
                result.errors.append(f"No data returned from {provider.name}")
                self._update_progress(progress_key, "failed", 0)
                return result

            # Normalise pence/pounds for .L tickers
            df, pence_converted = normalise_pence_to_pounds(df, ticker)
            if pence_converted:
                logger.info("[PENCE->GBP] %s prices converted from pence to pounds", ticker)

            # Validate
            validation = validate_bars(ticker, df, timeframe)
            if validation.quality_score < 30:
                result.errors.append(
                    f"Quality score too low: {validation.quality_score}/100. "
                    f"Issues: {validation.issues}"
                )
                logger.warning(
                    "[QUALITY] %s %s score=%.1f -- issues: %s",
                    ticker, timeframe, validation.quality_score, validation.issues,
                )

            # Save to parquet
            dirs = ensure_ticker_dirs(ticker)
            if timeframe == "daily":
                file_path = dirs["daily"] / f"{ticker}_daily_adjusted.parquet"
            elif timeframe == "weekly":
                file_path = dirs["weekly"] / f"{ticker}_weekly_adjusted.parquet"
            elif timeframe == "hourly":
                file_path = dirs["hourly"] / f"{ticker}_1h_adjusted.parquet"
            elif timeframe == "intraday_5m":
                year = end_date.year
                file_path = dirs["intraday_5m"] / f"{ticker}_5m_{year}.parquet"
            elif timeframe == "intraday_1m":
                month = end_date.strftime("%Y_%m")
                file_path = dirs["intraday_1m"] / f"{ticker}_1m_{month}.parquet"
            else:
                file_path = dirs["daily"] / f"{ticker}_{timeframe}.parquet"

            saved = save_parquet(df, file_path, ticker, provider.name)
            if saved:
                result.success = True
                result.bars_fetched = len(df)
                result.start_date = str(df.index[0].date()) if len(df) > 0 else ""
                result.end_date = str(df.index[-1].date()) if len(df) > 0 else ""
                result.file_path = str(file_path)
                result.quality_score = validation.quality_score
                self._update_progress(progress_key, "complete", len(df))
            else:
                result.errors.append("Failed to save parquet file")
                self._update_progress(progress_key, "failed", 0)

        except NotImplementedError as exc:
            result.errors.append(f"Provider not implemented: {exc}")
            logger.warning("[STUB] %s %s: %s", ticker, timeframe, exc)
        except Exception as exc:
            result.errors.append(f"Unexpected error: {exc}")
            logger.exception("[ERROR] %s %s", ticker, timeframe)

        return result

    def _update_progress(self, key: str, status: str, bars: int) -> None:
        """Update the progress tracking dict."""
        if "tickers" not in self._progress:
            self._progress["tickers"] = {}
        self._progress["tickers"][key] = {
            "status": status,
            "bars": bars,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _validate_all(self, tickers: list[str]) -> None:
        """Run validation on all stored data and produce a quality report."""
        logger.info("--- Validation Phase ---")
        report = {}

        for ticker in tickers:
            daily_path = _RESEARCH_DATA / ticker / "daily" / f"{ticker}_daily_adjusted.parquet"
            if daily_path.exists():
                try:
                    df = pd.read_parquet(daily_path)
                    v = validate_bars(ticker, df, "daily")
                    report[ticker] = asdict(v)
                    if v.quality_score >= 80:
                        logger.info("[PASS] %s: quality=%.1f (%d bars)", ticker, v.quality_score, v.total_bars)
                    elif v.quality_score >= 60:
                        logger.warning("[WARN] %s: quality=%.1f -- %s", ticker, v.quality_score, v.issues[:2])
                    else:
                        logger.error("[FAIL] %s: quality=%.1f -- %s", ticker, v.quality_score, v.issues)
                except Exception as exc:
                    logger.error("[VALIDATE] Failed for %s: %s", ticker, exc)
                    report[ticker] = {"error": str(exc)}
            else:
                report[ticker] = {"error": "No daily data file found"}

        # Save report
        report_path = _INDEX_DIR / "data_quality_report.json"
        _INDEX_DIR.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, default=str))
        logger.info("Validation report saved to %s", report_path)

    def print_summary(self) -> None:
        """Print a summary of all backfill results."""
        if not self._results:
            print("\nNo results to summarise (dry run or no tickers processed).")
            return

        success = sum(1 for r in self._results if r.success)
        failed = sum(1 for r in self._results if not r.success)
        total_bars = sum(r.bars_fetched for r in self._results)

        print(f"\n{'=' * 60}")
        print(f"  BACKFILL SUMMARY")
        print(f"{'=' * 60}")
        print(f"  Total tasks:    {len(self._results)}")
        print(f"  Succeeded:      {success}")
        print(f"  Failed:         {failed}")
        print(f"  Total bars:     {total_bars:,}")
        print(f"{'=' * 60}")

        if failed > 0:
            print(f"\n  FAILURES:")
            for r in self._results:
                if not r.success:
                    print(f"    {r.ticker} ({r.timeframe}): {'; '.join(r.errors[:2])}")
            print()


# ===========================================================================
# CLI ENTRY POINT
# ===========================================================================

def main() -> None:
    print(_SAFETY_BANNER)

    parser = argparse.ArgumentParser(
        description="NZT-48 Historical Data Backfill (STUB SCRIPT)",
        epilog="See docs/HISTORICAL_DATA_BACKFILL_PLAN.md for full details.",
    )
    parser.add_argument(
        "--ticker", type=str, default=None,
        help="Backfill a single ticker only (e.g., QQQ3.L)",
    )
    parser.add_argument(
        "--phase", type=str, default="daily",
        choices=["daily", "weekly", "hourly", "5min", "1min", "all", "validate"],
        help="Which phase to run (default: daily)",
    )
    parser.add_argument(
        "--universe", type=str, default="core",
        choices=["core", "extended", "intel", "all"],
        help="Which ticker universe to backfill (default: core)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be fetched without actually fetching",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="Required to actually execute the backfill (safety check)",
    )
    parser.add_argument(
        "--provider", type=str, default=None,
        choices=["yfinance", "ibkr", "polygon", "stooq", "alpha_vantage"],
        help="Force a specific provider (default: auto-select best available)",
    )

    args = parser.parse_args()

    # Safety: require --confirm or --dry-run
    if not args.dry_run and not args.confirm:
        print("ERROR: You must specify --confirm to execute, or --dry-run to preview.")
        print("       This is a safety check to prevent accidental execution.")
        sys.exit(1)

    # Determine tickers
    if args.ticker:
        tickers = [args.ticker]
    elif args.universe == "core":
        tickers = CORE_UNIVERSE
    elif args.universe == "extended":
        tickers = EXTENDED_UNIVERSE
    elif args.universe == "intel":
        tickers = INTEL_UNIVERSE
    else:
        tickers = list(dict.fromkeys(CORE_UNIVERSE + EXTENDED_UNIVERSE + INTEL_UNIVERSE))

    # Determine providers
    providers = get_available_providers()
    if args.provider:
        all_providers = get_all_providers()
        if args.provider in all_providers:
            p = all_providers[args.provider]
            if p.is_available:
                providers = {args.provider: p}
            else:
                logger.error("Provider '%s' is not available (missing API key or dependency)", args.provider)
                sys.exit(1)

    if not providers:
        logger.error("No data providers are available. Install yfinance or configure API keys.")
        sys.exit(1)

    logger.info("Available providers: %s", list(providers.keys()))
    logger.info("Universe: %d tickers", len(tickers))
    logger.info("Phase: %s", args.phase)

    # Run backfill
    orchestrator = BackfillOrchestrator(providers=providers)
    orchestrator.run(
        tickers=tickers,
        phase=args.phase,
        dry_run=args.dry_run,
    )
    orchestrator.print_summary()


if __name__ == "__main__":
    main()
