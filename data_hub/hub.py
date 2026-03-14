"""
data_hub/hub.py
================
DataHub: orchestrates fetch, normalize, validate, and reliability scoring.

Architecture:
  1. Primary truth: IBKRSource (stub; degrades gracefully if unavailable)
  2. Fallback:      YFinanceSource (always available)
  3. Validator:     ValidatorSource (polygon/tiingo stub)

Usage:
    from data_hub.hub import DataHub
    hub = DataHub()
    result = hub.get_bars("QQQ3.L", period="5d")
    # result.df, result.reliability, result.source, result.validated
"""
from __future__ import annotations

import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from data_hub.sources.yfinance_source import YFinanceSource
from data_hub.sources.ibkr_source import IBKRSource
from data_hub.sources.validator_source import ValidatorSource
from data_hub.normalization.price_units import scale_bars, normalize_to_pounds
from data_hub.models import DataReliabilityScore

logger = logging.getLogger("nzt48.data_hub")


@dataclass
class BarResult:
    ticker:      str
    df:          Optional[pd.DataFrame]
    source:      str
    reliability: DataReliabilityScore
    pence_adjusted: bool = False
    validator_comparison: dict = field(default_factory=dict)


class DataHub:
    """
    Single point of access for all market data in NZT-48.
    Replaces direct yfinance calls with a validated, normalized pipeline.
    """

    def __init__(self):
        self._yf       = YFinanceSource()
        self._ibkr     = IBKRSource()
        self._validator = ValidatorSource()

    def get_bars(
        self,
        ticker:   str,
        period:   str = "5d",
        interval: str = "1h",
    ) -> BarResult:
        """
        Fetch OHLCV bars for a ticker.
        1. Try IBKR (truth) if available.
        2. Fall back to yfinance.
        3. Normalize pence.
        4. Run validator comparison (if available).
        5. Compute DataReliabilityScore.
        """
        df = None
        source = "none"
        pence_adjusted = False
        issues = []

        # 1. Try truth source
        if self._ibkr.IS_AVAILABLE:
            df = self._ibkr.fetch_bars(ticker, period, interval)
            if df is not None and not df.empty:
                source = "ibkr"

        # 2. Fallback: yfinance
        if df is None or (isinstance(df, pd.DataFrame) and df.empty):
            df = self._yf.fetch_bars(ticker, period, interval)
            source = "yfinance"

        if df is None or (isinstance(df, pd.DataFrame) and df.empty):
            rel = DataReliabilityScore(
                ticker=ticker, score=0.0, source=source,
                validated=False, issues=["no_data"],
                computed_at=datetime.now(timezone.utc).isoformat(),
            )
            return BarResult(ticker=ticker, df=None, source=source, reliability=rel)

        # 3. Normalize pence/pounds
        df, pence_adjusted = scale_bars(df, ticker)
        if pence_adjusted:
            issues.append("pence_normalized")

        # 4. Basic sanity checks
        if "close" in df.columns:
            closes = df["close"].dropna()
            if closes.empty:
                issues.append("all_nan_close")
            elif closes.min() <= 0:
                issues.append("negative_close")
            elif closes.std() / closes.mean() > 0.5:
                issues.append("high_volatility_data")

        n_bars = len(df)

        # 5. Validator comparison
        validator_compare = {}
        reliability_penalty = 0.0
        if n_bars > 0 and "close" in df.columns:
            last_close = float(df["close"].iloc[-1])
            last_volume = float(df["volume"].iloc[-1]) if "volume" in df.columns else 0.0
            validator_compare = self._validator.compare(ticker, last_close, last_volume, n_bars)
            reliability_penalty += validator_compare.get("reliability_penalty", 0.0)
            if validator_compare.get("unverified"):
                issues.append("validator_unavailable")

        # 6. Compute reliability score
        score = max(0.0, 1.0 - reliability_penalty - len([i for i in issues if "nan" in i or "negative" in i]) * 0.15)
        rel = DataReliabilityScore(
            ticker=ticker,
            score=round(score, 3),
            source=source,
            validated=not validator_compare.get("unverified", True),
            validator_agree=validator_compare.get("agree", False) or False,
            disagreement_pct=validator_compare.get("close_delta", 0.0) or 0.0,
            n_bars=n_bars,
            issues=issues,
            computed_at=datetime.now(timezone.utc).isoformat(),
        )

        return BarResult(
            ticker=ticker,
            df=df,
            source=source,
            reliability=rel,
            pence_adjusted=pence_adjusted,
            validator_comparison=validator_compare,
        )

    def get_bars_with_retry(
        self,
        ticker: str,
        period: str = "5d",
        interval: str = "1h",
        max_retries: int = 3,
    ) -> BarResult:
        """Fetch bars with exponential backoff retry on failure."""
        delays = [1, 2, 4]
        last_error = None
        for attempt in range(max_retries):
            try:
                result = self.get_bars(ticker, period, interval)
                if result.df is not None and not result.df.empty:
                    return result
                last_error = f"empty_data_attempt_{attempt + 1}"
            except Exception as e:
                last_error = str(e)
                logger.warning(f"DataHub retry {attempt + 1}/{max_retries} for {ticker}: {e}")
            if attempt < max_retries - 1:
                time.sleep(delays[min(attempt, len(delays) - 1)])
        # All retries failed
        logger.error(f"DataHub all {max_retries} retries failed for {ticker}: {last_error}")
        rel = DataReliabilityScore(
            ticker=ticker, score=0.0, source="retry_exhausted",
            validated=False, issues=[f"retry_exhausted: {last_error}"],
            computed_at=datetime.now(timezone.utc).isoformat(),
        )
        return BarResult(ticker=ticker, df=None, source="retry_exhausted", reliability=rel)

    def get_bars_batch(
        self,
        tickers: list[str],
        period: str = "5d",
        interval: str = "1h",
        max_retries: int = 2,
    ) -> dict[str, BarResult]:
        """Fetch bars for multiple tickers. Returns dict[ticker -> BarResult]."""
        results = {}
        for ticker in tickers:
            try:
                results[ticker] = self.get_bars_with_retry(ticker, period, interval, max_retries)
            except Exception as e:
                logger.error(f"DataHub batch fetch failed for {ticker}: {e}")
                rel = DataReliabilityScore(
                    ticker=ticker, score=0.0, source="batch_error",
                    validated=False, issues=[str(e)],
                    computed_at=datetime.now(timezone.utc).isoformat(),
                )
                results[ticker] = BarResult(ticker=ticker, df=None, source="batch_error", reliability=rel)
        return results

    def get_last_fetch_time(self) -> Optional[str]:
        """Return timestamp of last successful fetch."""
        return getattr(self, "_last_fetch_time", None)

    def get_source_health(self) -> dict:
        """Return per-source availability and latency estimate."""
        health = {}
        for name, src in [("ibkr", self._ibkr), ("yfinance", self._yf), ("validator", self._validator)]:
            avail = src.availability()
            health[name] = {
                "available": avail.get("available", False) if isinstance(avail, dict) else bool(avail),
                "status": avail,
            }
        return health

    def get_quote(self, ticker: str) -> Optional[dict]:
        """Get latest bid/ask quote. Falls back to proxy if IBKR unavailable."""
        if self._ibkr.IS_AVAILABLE:
            q = self._ibkr.fetch_quote(ticker)
            if q:
                return q
        return self._yf.fetch_quote(ticker)

    def get_source_status(self) -> dict:
        """Return status of all data sources."""
        return {
            "ibkr":      self._ibkr.availability(),
            "yfinance":  self._yf.availability(),
            "validator": self._validator.availability(),
        }

    def compare_truth_vs_validator(self, ticker: str) -> dict:
        """Full truth vs validator comparison for /api/data/compare/{symbol}."""
        result = self.get_bars(ticker, period="1d")
        return {
            "ticker":             ticker,
            "source":             result.source,
            "pence_adjusted":     result.pence_adjusted,
            "reliability_score":  result.reliability.score,
            "validated":          result.reliability.validated,
            "validator_compare":  result.validator_comparison,
            "issues":             result.reliability.issues,
            "n_bars":             result.reliability.n_bars,
        }

    # ===================================================================
    # DataFeedManager Compatibility Layer
    # ===================================================================
    # These methods match DataFeedManager's API (feeds/data_feeds.py) for
    # drop-in replacement in main.py. DataFeedManager returns title-case
    # columns (Open, High, Low, Close, Volume). DataHub returns lowercase.
    # These adapters normalize columns to title-case for backward compat.
    # ===================================================================

    @staticmethod
    def _normalize_to_title_case(df: pd.DataFrame) -> pd.DataFrame:
        """Convert lowercase OHLCV columns to title-case for DataFeedManager compat."""
        if df is None or df.empty:
            return df
        rename_map = {}
        for col in df.columns:
            lc = col.lower()
            if lc == "open":
                rename_map[col] = "Open"
            elif lc == "high":
                rename_map[col] = "High"
            elif lc == "low":
                rename_map[col] = "Low"
            elif lc == "close":
                rename_map[col] = "Close"
            elif lc == "volume":
                rename_map[col] = "Volume"
        return df.rename(columns=rename_map) if rename_map else df

    def get_intraday_bars(
        self, ticker: str, interval: str = "1m", period: str = "1d"
    ) -> pd.DataFrame:
        """DataFeedManager-compatible: intraday bars with title-case columns."""
        result = self.get_bars(ticker, period=period, interval=interval)
        if result.df is None:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        return self._normalize_to_title_case(result.df)

    def get_daily_bars(self, ticker: str, days: int = 60) -> pd.DataFrame:
        """DataFeedManager-compatible: daily bars with title-case columns."""
        if days <= 5:
            period = "5d"
        elif days <= 30:
            period = "1mo"
        elif days <= 90:
            period = "3mo"
        elif days <= 180:
            period = "6mo"
        else:
            period = "2y"  # Covers up to ~504 trading days
        result = self.get_bars(ticker, period=period, interval="1d")
        if result.df is None:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        df = self._normalize_to_title_case(result.df)
        if len(df) > days:
            df = df.iloc[-days:]
        return df

    def get_realtime_price(self, ticker: str) -> float:
        """DataFeedManager-compatible: best-effort real-time price.
        Priority: IBKR quote → yfinance quote → latest bar close."""
        try:
            quote = self.get_quote(ticker)
            if quote and quote.get("last", 0) > 0:
                return float(quote["last"])
        except Exception:
            pass
        # Fallback: latest close from 1-minute bars
        try:
            result = self.get_bars(ticker, period="1d", interval="1m")
            if result.df is not None and not result.df.empty:
                close_col = "close" if "close" in result.df.columns else "Close"
                if close_col in result.df.columns:
                    return float(result.df[close_col].dropna().iloc[-1])
        except Exception:
            pass
        return 0.0

    def get_batch_prices(self, tickers: list = None) -> dict[str, float]:
        """DataFeedManager-compatible: concurrent batch price fetch."""
        if not tickers:
            return {}
        results: dict[str, float] = {}

        def _fetch(t: str) -> tuple[str, float]:
            return t, self.get_realtime_price(t)

        max_workers = min(len(tickers), 12)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch, t): t for t in tickers}
            for f in as_completed(futures, timeout=30):
                try:
                    t, price = f.result(timeout=15)
                    if price > 0:
                        results[t] = price
                except Exception:
                    pass
        return results

    def get_premarket_data(self, ticker: str) -> dict:
        """DataFeedManager-compatible: premarket data.
        DataHub doesn't have premarket logic — delegate to DataFeedManager."""
        try:
            from feeds.data_feeds import DataFeedManager
            _dfm = DataFeedManager()
            return _dfm.get_premarket_data(ticker)
        except Exception:
            return {
                "price": self.get_realtime_price(ticker),
                "volume": 0.0,
                "change_pct": 0.0,
            }

    def get_dollar_volume(self, ticker: str) -> float:
        """DataFeedManager-compatible: price × volume for liquidity filter."""
        try:
            result = self.get_bars(ticker, period="5d", interval="1d")
            if result.df is not None and not result.df.empty:
                close_col = "close" if "close" in result.df.columns else "Close"
                vol_col = "volume" if "volume" in result.df.columns else "Volume"
                if close_col in result.df.columns and vol_col in result.df.columns:
                    last = result.df.iloc[-1]
                    return float(last[close_col]) * float(last[vol_col])
        except Exception:
            pass
        return 0.0
