"""
NZT-48 Trading System -- Primary Data Feed Module
===================================================
Fetches OHLCV market data for all Bot B and context tickers.

Data source priority (ticker-dependent):
  .L tickers (LSE/ISA):  TwelveData -> yfinance -> FMP -> Alpha Vantage
  US tickers (default):   yfinance -> TwelveData -> FMP -> Alpha Vantage

Design principles:
  - Never crash. Every public method returns a DataFrame (empty on failure).
  - Ticker-dependent priority chain: TwelveData is PRIMARY for .L tickers.
  - Data staleness detection: STALE warning if last price > 5 minutes old.
  - Alpha Vantage call budget tracked so we never silently exceed the free tier.
  - Type hints and docstrings on every public method.
  - Thread-safe concurrent price fetching for the full ticker universe.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ticker universes pulled from config (settings.yaml)
# ---------------------------------------------------------------------------
BOT_B_TICKERS: list[str] = config.get("bot_b_universe.tickers", [
    "NVDA", "TSLA", "MU", "SNDK", "AMD", "AVGO",
    "MRVL", "ARM", "TSM", "ASML", "SMCI", "VRT",
])

CONTEXT_TICKERS: list[str] = [
    "QQQ", "SMH", "SPY", "SOXX", "^VIX", "TLT", "UUP", "GLD",
]

# DXY has no direct ticker on yfinance; we proxy it via the UUP ETF.
# VIX is ^VIX on yfinance.
_TICKER_ALIAS: dict[str, str] = {
    "VIX": "^VIX",
    "DXY": "UUP",
}

# Alpha Vantage interval names differ from yfinance conventions.
_AV_INTERVAL_MAP: dict[str, str] = {
    "1m":  "1min",
    "5m":  "5min",
    "15m": "15min",
    "30m": "30min",
    "60m": "60min",
}

# ---------------------------------------------------------------------------
# Alpha Vantage rate-limit tracker (module-level singleton state)
# ---------------------------------------------------------------------------
_av_calls_today: int = 0
_av_calls_date: date = date.today()
_av_lock = threading.Lock()

# ---------------------------------------------------------------------------
# TwelveData rate-limit tracker (module-level singleton state)
# Free tier: 800 calls/day. Resets at midnight UTC.
# ---------------------------------------------------------------------------
_td_calls_today: int = 0
_td_calls_date: date = date.today()
_td_lock = threading.Lock()

# ---------------------------------------------------------------------------
# OHLCV dedup cache — prevents re-fetching the same bar data across fallback
# sources within a short window (C-24)
# ---------------------------------------------------------------------------
_ohlcv_cache: dict[str, tuple[float, pd.DataFrame]] = {}  # key=f"{ticker}:{interval}:{date}", value=(timestamp, df)
_CACHE_TTL_S = 300  # 5 minutes


def _get_cached_ohlcv(ticker: str, interval: str, date_key: str) -> pd.DataFrame | None:
    """Return cached OHLCV if fresh, else None."""
    key = f"{ticker}:{interval}:{date_key}"
    if key in _ohlcv_cache:
        ts, df = _ohlcv_cache[key]
        if time.time() - ts < _CACHE_TTL_S:
            return df.copy()
        del _ohlcv_cache[key]
    return None


def _set_cached_ohlcv(ticker: str, interval: str, date_key: str, df: pd.DataFrame) -> None:
    """Cache OHLCV data."""
    key = f"{ticker}:{interval}:{date_key}"
    _ohlcv_cache[key] = (time.time(), df.copy())
    # Prune old entries
    if len(_ohlcv_cache) > 500:
        cutoff = time.time() - _CACHE_TTL_S
        stale = [k for k, (ts, _) in _ohlcv_cache.items() if ts < cutoff]
        for k in stale:
            del _ohlcv_cache[k]


def _resolve_ticker(ticker: str) -> str:
    """Map user-friendly ticker names to yfinance-compatible symbols."""
    return _TICKER_ALIAS.get(ticker.upper(), ticker)


def _empty_ohlcv() -> pd.DataFrame:
    """Return an empty DataFrame with the canonical OHLCV columns."""
    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure the DataFrame has exactly the standard OHLCV column names.

    yfinance returns title-cased columns already, but Alpha Vantage uses
    numbered prefixed names like '1. open'.  This normalises both.

    yfinance >= 0.2.40 may return MultiIndex columns like ('Close', 'NVDA').
    We flatten those first before renaming.
    """
    # Handle MultiIndex columns from newer yfinance (e.g. ('Close', 'NVDA'))
    if isinstance(df.columns, pd.MultiIndex):
        # Take the first level (Price type) and drop the ticker level
        df.columns = df.columns.get_level_values(0)

    rename_map: dict[str, str] = {}
    for col in df.columns:
        lower = str(col).lower().strip()
        if "open" in lower:
            rename_map[col] = "Open"
        elif "high" in lower:
            rename_map[col] = "High"
        elif "low" in lower:
            rename_map[col] = "Low"
        elif "close" in lower and "adj" not in lower:
            rename_map[col] = "Close"
        elif "volume" in lower:
            rename_map[col] = "Volume"

    if rename_map:
        df = df.rename(columns=rename_map)

    # Keep only canonical columns that exist
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep].copy()

    # Coerce to float (Alpha Vantage returns strings)
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


class DataFeedManager:
    """Central market data gateway for the NZT-48 system.

    Provides a unified interface over yfinance (primary) and Alpha Vantage
    (backup).  Every method is designed to be called in a hot loop without
    risk of an unhandled exception taking down the signal pipeline.

    Usage::

        feeds = DataFeedManager()
        bars = feeds.get_bars("NVDA", interval="5m", period="5d")
        price = feeds.get_realtime_price("TSLA")
        prices = feeds.get_batch_prices(["NVDA", "AMD", "TSM"])
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def __init__(self) -> None:
        # Alpha Vantage key (optional -- only needed if yfinance fails)
        self._av_api_key: str = os.environ.get("ALPHA_VANTAGE_KEY", "")
        self._av_base_url: str = "https://www.alphavantage.co/query"

        # Pull max calls from config, default to 20 (conservative — API allows 25)
        # C-25: Use conservative limit to avoid silent exhaustion
        self._av_max_calls: int = int(
            config.get("data_feeds.alpha_vantage.max_calls_per_day", 20)
        )

        # Twelve Data (real-time quotes + intraday bars)
        self._td_api_key: str = os.environ.get("TWELVEDATA_API_KEY", "")
        self._td_base_url: str = "https://api.twelvedata.com"
        self._td_max_calls: int = int(
            config.get("data_feeds.twelvedata.max_calls_per_day", 800)
        )

        # Financial Modeling Prep (bulk quotes + profiles)
        self._fmp_api_key: str = os.environ.get("FMP_KEY", "")
        self._fmp_base_url: str = "https://financialmodelingprep.com/api/v3"

        # Session for connection pooling on API requests
        self._session: requests.Session = requests.Session()

        # --- TwelveData API key validation (startup check, no live call) ---
        # NOTE: TwelveData free tier does NOT support LSE leveraged ETPs (QQQ3.L etc).
        # These require the "Grow" plan (~$79/mo). Until then, yfinance is primary for .L.
        # Key is kept for US tickers and future plan upgrade.
        _td_key_len = len(self._td_api_key)
        if _td_key_len > 10:
            logger.info(
                "TwelveData API key PRESENT (len=%d). Active for US tickers. "
                "LSE .L tickers require Grow plan — falling back to yfinance for ISA tickers.",
                _td_key_len,
            )
        elif _td_key_len > 0:
            logger.warning(
                "TwelveData API key is too short (%d chars) — likely invalid. "
                ".L ticker feed will fall back to yfinance.",
                _td_key_len,
            )
        else:
            logger.warning(
                "TwelveData API key is MISSING (TWELVEDATA_API_KEY not set). "
                ".L ticker feed will fall back to yfinance/FMP.",
            )

        logger.info(
            "DataFeedManager initialised  |  AV key=%s  |  TD key=%s (len=%d, valid=%s)  |  FMP key=%s",
            bool(self._av_api_key),
            bool(self._td_api_key),
            _td_key_len,
            _td_key_len > 10,
            bool(self._fmp_api_key),
        )

    # ------------------------------------------------------------------
    # Internal: ticker-dependent priority chain
    # ------------------------------------------------------------------
    @staticmethod
    def _is_lse_ticker(ticker: str) -> bool:
        """Return True if this is an LSE (.L) ticker."""
        return ticker.upper().endswith(".L")

    def _check_staleness(self, df: pd.DataFrame, ticker: str) -> None:
        """Log a STALE warning if the last bar timestamp is > 5 minutes old."""
        try:
            if df is None or df.empty:
                return
            last_ts = df.index[-1]
            if not isinstance(last_ts, pd.Timestamp):
                last_ts = pd.Timestamp(last_ts)
            if last_ts.tzinfo is None:
                last_ts = last_ts.tz_localize("UTC")
            now_utc = pd.Timestamp.now(tz="UTC")
            age = now_utc - last_ts
            if age > pd.Timedelta(minutes=5):
                logger.warning(
                    "STALE DATA: %s last bar is %.1f minutes old (ts=%s)",
                    ticker, age.total_seconds() / 60, last_ts.isoformat(),
                )
        except Exception:
            pass  # Staleness check is advisory, never blocking

    # ------------------------------------------------------------------
    # Public: generic bar fetcher (primary entry point)
    # ------------------------------------------------------------------
    def get_bars(
        self,
        ticker: str,
        interval: str = "1d",
        period: str = "60d",
    ) -> pd.DataFrame:
        """Fetch OHLCV bars for *ticker* at the given *interval* and *period*.

        Priority chain is ticker-dependent:
          .L tickers (LSE/ISA): TwelveData -> yfinance -> FMP -> Alpha Vantage
          US tickers (default):  yfinance -> TwelveData -> FMP -> Alpha Vantage

        Also runs data staleness detection: logs a STALE warning if the
        last bar timestamp is more than 5 minutes old.

        Parameters
        ----------
        ticker : str
            Any Bot B or context ticker (e.g. ``"NVDA"``, ``"QQQ3.L"``).
        interval : str
            Bar interval -- ``"1m"``, ``"5m"``, ``"15m"``, ``"1h"``,
            ``"1d"``, ``"1wk"``, etc.  Must be yfinance-compatible.
        period : str
            Look-back window -- ``"1d"``, ``"5d"``, ``"1mo"``, ``"3mo"``,
            ``"6mo"``, ``"1y"``, ``"max"``.

        Returns
        -------
        pd.DataFrame
            Columns: Open, High, Low, Close, Volume.
            Empty DataFrame on total failure.
        """
        if self._is_lse_ticker(ticker):
            return self._get_bars_lse(ticker, interval, period)
        else:
            return self._get_bars_us(ticker, interval, period)

    def _get_bars_lse(
        self, ticker: str, interval: str, period: str,
    ) -> pd.DataFrame:
        """LSE (.L) priority: TwelveData -> yfinance -> FMP -> Alpha Vantage."""
        date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")

        # Check dedup cache before hitting any source (C-24)
        cached = _get_cached_ohlcv(ticker, interval, date_key)
        if cached is not None:
            logger.debug("OHLCV cache HIT for %s (%s)", ticker, interval)
            return cached

        # Primary: TwelveData (best for .L tickers — real-time LSE data)
        if interval in self._TD_INTERVAL_MAP:
            df = self._fetch_with_retry(
                ticker, "twelvedata", self._fetch_twelvedata,
                max_retries=3, interval=interval,
            )
            if df is not None and not df.empty:
                self._check_staleness(df, ticker)
                _set_cached_ohlcv(ticker, interval, date_key, df)
                return df
            logger.info(
                "TwelveData (PRIMARY for .L) failed for %s (%s/%s) — trying yfinance",
                ticker, interval, period,
            )

        # Fallback 1: yfinance
        df = self._fetch_with_retry(
            ticker, "yfinance", self._fetch_yfinance,
            max_retries=3, interval=interval, period=period,
        )
        if df is not None and not df.empty:
            self._check_staleness(df, ticker)
            _set_cached_ohlcv(ticker, interval, date_key, df)
            return df

        # Fallback 2: FMP
        df = self._fetch_with_retry(
            ticker, "fmp", self._fetch_fmp_bars,
            max_retries=2, interval=interval,
        )
        if df is not None and not df.empty:
            logger.info("FMP fallback succeeded for %s (%s)", ticker, interval)
            self._check_staleness(df, ticker)
            _set_cached_ohlcv(ticker, interval, date_key, df)
            return df

        # Fallback 3: Alpha Vantage (intraday only)
        if interval in _AV_INTERVAL_MAP:
            logger.info(
                "Trying Alpha Vantage fallback for %s (%s/%s)",
                ticker, interval, period,
            )
            df = self._fetch_alpha_vantage(ticker, interval=interval)
            if df is not None and not df.empty:
                self._check_staleness(df, ticker)
                _set_cached_ohlcv(ticker, interval, date_key, df)
                return df

        logger.warning(
            "All feeds exhausted for .L ticker %s (%s/%s) — returning empty DataFrame",
            ticker, interval, period,
        )
        self._alert_all_feeds_down()
        return _empty_ohlcv()

    def _get_bars_us(
        self, ticker: str, interval: str, period: str,
    ) -> pd.DataFrame:
        """US priority: yfinance -> TwelveData -> FMP -> Alpha Vantage."""
        date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")

        # Check dedup cache before hitting any source (C-24)
        cached = _get_cached_ohlcv(ticker, interval, date_key)
        if cached is not None:
            logger.debug("OHLCV cache HIT for %s (%s)", ticker, interval)
            return cached

        # Primary: yfinance
        df = self._fetch_with_retry(
            ticker, "yfinance", self._fetch_yfinance,
            max_retries=3, interval=interval, period=period,
        )
        if df is not None and not df.empty:
            self._check_staleness(df, ticker)
            _set_cached_ohlcv(ticker, interval, date_key, df)
            return df

        # Fallback 1: Twelve Data
        if interval in self._TD_INTERVAL_MAP:
            logger.info(
                "yfinance failed for %s (%s/%s) — trying Twelve Data",
                ticker, interval, period,
            )
            df = self._fetch_with_retry(
                ticker, "twelvedata", self._fetch_twelvedata,
                max_retries=2, interval=interval,
            )
            if df is not None and not df.empty:
                self._check_staleness(df, ticker)
                _set_cached_ohlcv(ticker, interval, date_key, df)
                return df

        # Fallback 2: FMP
        df = self._fetch_with_retry(
            ticker, "fmp", self._fetch_fmp_bars,
            max_retries=2, interval=interval,
        )
        if df is not None and not df.empty:
            logger.info("FMP fallback succeeded for %s (%s)", ticker, interval)
            self._check_staleness(df, ticker)
            _set_cached_ohlcv(ticker, interval, date_key, df)
            return df

        # Fallback 3: Alpha Vantage (intraday only)
        if interval in _AV_INTERVAL_MAP:
            logger.info(
                "Trying Alpha Vantage fallback for %s (%s/%s)",
                ticker, interval, period,
            )
            df = self._fetch_alpha_vantage(ticker, interval=interval)
            if df is not None and not df.empty:
                self._check_staleness(df, ticker)
                _set_cached_ohlcv(ticker, interval, date_key, df)
                return df

        logger.warning(
            "All feeds exhausted for %s (%s/%s) — returning empty DataFrame",
            ticker, interval, period,
        )
        self._alert_all_feeds_down()
        return _empty_ohlcv()

    # ------------------------------------------------------------------
    # Public: convenience wrappers
    # ------------------------------------------------------------------
    def get_realtime_price(self, ticker: str) -> float:
        """Return the latest trade price for *ticker*.

        Priority chain is ticker-dependent:
          .L tickers: TwelveData quote -> FMP quote -> yfinance fast_info -> yfinance 1m
          US tickers: yfinance fast_info -> TwelveData quote -> FMP quote -> yfinance 1m

        Returns 0.0 on total failure.
        """
        if self._is_lse_ticker(ticker):
            return self._get_realtime_price_lse(ticker)
        else:
            return self._get_realtime_price_us(ticker)

    def _get_realtime_price_lse(self, ticker: str) -> float:
        """LSE (.L) realtime price: TwelveData -> FMP -> yfinance -> yfinance 1m."""
        # Primary: TwelveData quote (best for .L tickers)
        price = self._fetch_twelvedata_quote(ticker)
        if price > 0:
            return price

        # Fallback 1: FMP quote
        price = self._fetch_fmp_quote(ticker)
        if price > 0:
            return price

        # Fallback 2: yfinance fast_info
        resolved = _resolve_ticker(ticker)
        try:
            info = yf.Ticker(resolved).fast_info
            price = float(getattr(info, "last_price", 0.0) or 0.0)
            if price > 0:
                return price
        except Exception:
            logger.debug("yfinance fast_info failed for %s", ticker)

        # Fallback 3: yfinance 1m bars
        try:
            df = self._fetch_yfinance(ticker, interval="1m", period="1d")
            if df is not None and not df.empty:
                return float(df["Close"].iloc[-1])
        except Exception:
            logger.debug("yfinance 1m fallback failed for %s", ticker)

        logger.error("get_realtime_price: ALL fallbacks failed for .L ticker %s", ticker)
        return 0.0

    def _get_realtime_price_us(self, ticker: str) -> float:
        """US realtime price: yfinance -> TwelveData -> FMP -> yfinance 1m."""
        resolved = _resolve_ticker(ticker)
        try:
            info = yf.Ticker(resolved).fast_info
            price = float(getattr(info, "last_price", 0.0) or 0.0)
            if price > 0:
                return price
        except Exception:
            logger.debug("yfinance fast_info failed for %s", ticker)

        # Fallback 1: Twelve Data quote
        price = self._fetch_twelvedata_quote(ticker)
        if price > 0:
            return price

        # Fallback 2: FMP quote
        price = self._fetch_fmp_quote(ticker)
        if price > 0:
            return price

        # Fallback 3: yfinance 1m bars
        try:
            df = self._fetch_yfinance(ticker, interval="1m", period="1d")
            if df is not None and not df.empty:
                return float(df["Close"].iloc[-1])
        except Exception:
            logger.debug("yfinance 1m fallback failed for %s", ticker)

        logger.error("get_realtime_price: ALL fallbacks failed for %s (yf, TD, FMP, 1m bars)", ticker)
        return 0.0

    def get_daily_bars(self, ticker: str, days: int = 60) -> pd.DataFrame:
        """Return daily OHLCV bars for the last *days* trading days.

        Uses ``period`` strings that yfinance understands.  For exact day
        counts beyond what period strings allow, we fetch extra and trim.
        """
        # Map rough day counts to yfinance period strings
        if days <= 5:
            period = "5d"
        elif days <= 30:
            period = "1mo"
        elif days <= 90:
            period = "3mo"
        elif days <= 180:
            period = "6mo"
        elif days <= 365:
            period = "1y"
        else:
            period = "max"

        df = self.get_bars(ticker, interval="1d", period=period)
        if not df.empty and len(df) > days:
            df = df.iloc[-days:]
        return df

    def get_weekly_bars(self, ticker: str, weeks: int = 52) -> pd.DataFrame:
        """Return weekly OHLCV bars for the last *weeks* weeks.

        Weekly data is useful for the 10-week EMA and longer-term regime
        classification.
        """
        if weeks <= 26:
            period = "6mo"
        elif weeks <= 52:
            period = "1y"
        elif weeks <= 104:
            period = "2y"
        else:
            period = "5y"

        df = self.get_bars(ticker, interval="1wk", period=period)
        if not df.empty and len(df) > weeks:
            df = df.iloc[-weeks:]
        return df

    def get_intraday_bars(
        self,
        ticker: str,
        interval: str = "1m",
        period: str = "1d",
    ) -> pd.DataFrame:
        """Return intraday OHLCV bars.

        By default fetches 1-minute bars for the current trading day.
        This is the workhorse for the indicator engine and all intraday
        strategies (S2, S3, S4, S8, etc.).

        Parameters
        ----------
        interval : str
            ``"1m"``, ``"5m"``, ``"15m"``, ``"30m"``, ``"60m"``
        period : str
            ``"1d"``, ``"5d"``, ``"1mo"`` — note yfinance caps 1m data to
            the last 7 calendar days.
        """
        return self.get_bars(ticker, interval=interval, period=period)

    def get_batch_prices(
        self,
        tickers: list[str] | None = None,
    ) -> dict[str, float]:
        """Fetch current prices for multiple tickers concurrently.

        Defaults to the full Bot B + context universe if *tickers* is not
        supplied.  Returns a dict mapping ticker -> price.  Tickers that
        fail are omitted from the result (not set to 0).

        Used by the regime classifier, overseer, and portfolio heat
        calculator to get a snapshot of the entire universe quickly.
        """
        if tickers is None:
            tickers = BOT_B_TICKERS + CONTEXT_TICKERS

        results: dict[str, float] = {}

        def _fetch_one(t: str) -> tuple[str, float]:
            return t, self.get_realtime_price(t)

        # Cap workers -- yfinance is I/O-bound, not CPU-bound
        max_workers = min(len(tickers), 12)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_one, t): t for t in tickers}
            for future in as_completed(futures):
                try:
                    t, price = future.result(timeout=15)
                    if price > 0:
                        results[t] = price
                except Exception:
                    t = futures[future]
                    logger.warning("Batch price fetch failed for %s", t)

        logger.info(
            "Batch prices fetched: %d/%d tickers succeeded", len(results), len(tickers)
        )
        return results

    def get_dollar_volume(self, ticker: str) -> float:
        """Return the most recent daily dollar volume (price x volume).

        Dollar volume is a key liquidity filter: Bot B requires > $500M/day
        (settings.yaml ``volume.dollar_min_bot_b``).  Returns 0.0 on failure.
        """
        try:
            df = self.get_daily_bars(ticker, days=1)
            if df.empty:
                return 0.0
            last = df.iloc[-1]
            return float(last["Close"] * last["Volume"])
        except Exception:
            logger.exception("get_dollar_volume failed for %s", ticker)
            return 0.0

    def get_premarket_data(self, ticker: str) -> dict[str, float]:
        """Return pre/post-market or latest available price data from yfinance.

        Fallback chain:
          1. yfinance .info preMarketPrice / postMarketPrice
          2. yfinance 1m bars with prepost=True (after-hours data)
          3. yfinance 5d daily bars (last close vs previous close)

        Always computes change_pct so the brief never shows 0.0% for
        instruments that traded recently.

        Returns
        -------
        dict
            ``{"price": float, "volume": float, "change_pct": float}``
            All values default to 0.0 on failure.
        """
        resolved = _resolve_ticker(ticker)
        result: dict[str, float] = {
            "price": 0.0,
            "volume": 0.0,
            "change_pct": 0.0,
        }
        try:
            tk = yf.Ticker(resolved)
            info = tk.info or {}

            prev_close = float(info.get("previousClose", 0) or info.get("regularMarketPreviousClose", 0) or 0)

            # Try 1: preMarketPrice
            pre_price = float(info.get("preMarketPrice", 0) or 0)
            pre_change = float(info.get("preMarketChangePercent", 0) or 0)
            if pre_price > 0:
                result["price"] = pre_price
                result["volume"] = float(info.get("preMarketVolume", 0) or 0)
                if pre_change != 0:
                    result["change_pct"] = round(pre_change * 100, 3)
                elif prev_close > 0:
                    result["change_pct"] = round((pre_price - prev_close) / prev_close * 100, 3)
                return result

            # Try 2: postMarketPrice (after-hours data)
            post_price = float(info.get("postMarketPrice", 0) or 0)
            post_change = float(info.get("postMarketChangePercent", 0) or 0)
            if post_price > 0:
                result["price"] = post_price
                if post_change != 0:
                    result["change_pct"] = round(post_change * 100, 3)
                elif prev_close > 0:
                    result["change_pct"] = round((post_price - prev_close) / prev_close * 100, 3)
                return result

            # Try 3: regularMarketPrice + regularMarketChangePercent (last session's move)
            reg_price = float(info.get("regularMarketPrice", 0) or info.get("currentPrice", 0) or 0)
            reg_change_pct = float(info.get("regularMarketChangePercent", 0) or 0)
            if reg_price > 0:
                result["price"] = reg_price
                result["volume"] = float(info.get("regularMarketVolume", 0) or 0)
                if reg_change_pct != 0:
                    # Use yfinance's own last-session change (e.g. NVDA closed +2.3%)
                    result["change_pct"] = round(reg_change_pct * 100, 3)
                elif prev_close > 0 and abs(reg_price - prev_close) > 0.001:
                    result["change_pct"] = round((reg_price - prev_close) / prev_close * 100, 3)
                else:
                    # regularMarketPrice == previousClose (off-hours, no change)
                    # Try regularMarketChange (absolute) as last resort
                    reg_change = float(info.get("regularMarketChange", 0) or 0)
                    if reg_change != 0 and prev_close > 0:
                        result["change_pct"] = round(reg_change / prev_close * 100, 3)
                if result["change_pct"] != 0:
                    return result

            # Try 4: 1m bars with prepost=True (catches extended-hours trades)
            df = self._fetch_yfinance(ticker, interval="1m", period="1d", prepost=True)
            if df is not None and not df.empty:
                latest_price = float(df["Close"].iloc[-1])
                result["price"] = latest_price
                result["volume"] = float(df["Volume"].sum())
                if prev_close > 0 and latest_price > 0:
                    result["change_pct"] = round((latest_price - prev_close) / prev_close * 100, 3)
                if result["change_pct"] != 0:
                    return result

            # Try 5: 5d daily bars -- last session's close vs prior close
            df = self._fetch_yfinance(ticker, interval="1d", period="5d")
            if df is not None and len(df) >= 2:
                last_close = float(df["Close"].iloc[-1])
                penult_close = float(df["Close"].iloc[-2])
                result["price"] = last_close
                result["volume"] = float(df["Volume"].iloc[-1])
                if penult_close > 0:
                    result["change_pct"] = round((last_close - penult_close) / penult_close * 100, 3)
                if result["change_pct"] != 0:
                    return result

            # Try 6: TwelveData quote (has change_pct in response)
            td_pct = self._fetch_twelvedata_change(ticker)
            if td_pct != 0 and result["price"] > 0:
                result["change_pct"] = td_pct
                return result

            # Try 7: FMP quote (has changesPercentage in response)
            fmp_pct = self._fetch_fmp_change(ticker)
            if fmp_pct != 0 and result["price"] > 0:
                result["change_pct"] = fmp_pct
                return result

        except Exception:
            logger.exception("get_premarket_data failed for %s", ticker)

        return result

    # ------------------------------------------------------------------
    # Private: yfinance fetch
    # ------------------------------------------------------------------
    def _fetch_yfinance(
        self,
        ticker: str,
        interval: str = "1d",
        period: str = "60d",
        prepost: bool = False,
    ) -> pd.DataFrame | None:
        """Low-level yfinance download with error handling.

        Returns ``None`` (not empty DF) on hard failure so the caller can
        distinguish "source unavailable" from "source returned no rows".
        """
        resolved = _resolve_ticker(ticker)
        try:
            df: pd.DataFrame = yf.download(
                resolved,
                period=period,
                interval=interval,
                prepost=prepost,
                progress=False,
                timeout=10,
            )
            if df is None or df.empty:
                logger.debug(
                    "yfinance returned empty for %s (%s/%s)", ticker, interval, period
                )
                return None

            df = _normalize_columns(df)

            # Drop any rows where Close is NaN (partial bars at market edges)
            if "Close" in df.columns:
                df = df.dropna(subset=["Close"])

            return df

        except Exception:
            logger.exception(
                "yfinance download error for %s (%s/%s)", ticker, interval, period
            )
            return None

    # ------------------------------------------------------------------
    # Private: Alpha Vantage fetch
    # ------------------------------------------------------------------
    def _can_call_alpha_vantage(self) -> bool:
        """Check whether we still have Alpha Vantage API budget today."""
        global _av_calls_today, _av_calls_date

        with _av_lock:
            today = date.today()
            if _av_calls_date != today:
                # New day -- reset the counter
                _av_calls_today = 0
                _av_calls_date = today

            if not self._av_api_key:
                logger.debug("Alpha Vantage API key not set -- skipping AV fallback")
                return False

            if _av_calls_today >= self._av_max_calls:
                logger.warning(
                    "Alpha Vantage daily limit reached (%d/%d)",
                    _av_calls_today,
                    self._av_max_calls,
                )
                return False

            return True

    def _increment_av_calls(self) -> None:
        global _av_calls_today
        with _av_lock:
            _av_calls_today += 1
            logger.debug(
                "Alpha Vantage call count: %d/%d", _av_calls_today, self._av_max_calls
            )

    # Private: TwelveData rate-limit guards
    # ------------------------------------------------------------------
    def _can_call_twelvedata(self) -> bool:
        """Check whether we still have TwelveData API budget today."""
        global _td_calls_today, _td_calls_date

        with _td_lock:
            today = date.today()
            if _td_calls_date != today:
                # New day — reset counter
                _td_calls_today = 0
                _td_calls_date = today

            if not self._td_api_key:
                return False

            if _td_calls_today >= self._td_max_calls:
                logger.warning(
                    "TwelveData daily limit reached (%d/%d) — skipping until midnight",
                    _td_calls_today,
                    self._td_max_calls,
                )
                return False

            return True

    def _increment_td_calls(self) -> None:
        global _td_calls_today
        with _td_lock:
            _td_calls_today += 1
            logger.debug(
                "TwelveData call count: %d/%d", _td_calls_today, self._td_max_calls
            )

    def _fetch_alpha_vantage(
        self,
        ticker: str,
        interval: str = "5m",
    ) -> pd.DataFrame | None:
        """Fetch intraday bars from the Alpha Vantage REST API.

        Only supports intraday intervals (1min-60min).  Daily/weekly data
        should go through yfinance exclusively.

        The free tier allows 25 requests per day.  This method checks the
        budget before making a call and returns ``None`` if exhausted.
        """
        if not self._can_call_alpha_vantage():
            return None

        av_interval = _AV_INTERVAL_MAP.get(interval)
        if av_interval is None:
            logger.debug(
                "Alpha Vantage does not support interval '%s' -- skipping", interval
            )
            return None

        resolved = _resolve_ticker(ticker)
        # AV doesn't understand Yahoo's ^VIX syntax
        if resolved.startswith("^"):
            resolved = resolved.lstrip("^")

        params: dict[str, str] = {
            "function": "TIME_SERIES_INTRADAY",
            "symbol": resolved,
            "interval": av_interval,
            "apikey": self._av_api_key,
            "outputsize": "compact",  # last 100 data points
            "datatype": "json",
        }

        try:
            resp = self._session.get(
                self._av_base_url,
                params=params,
                timeout=15,
            )
            self._increment_av_calls()
            resp.raise_for_status()

            data: dict[str, Any] = resp.json()

            # AV nests time-series under a key like
            # "Time Series (5min)" -- find it dynamically
            ts_key: str | None = None
            for key in data:
                if "Time Series" in key:
                    ts_key = key
                    break

            if ts_key is None:
                note = data.get("Note", data.get("Information", ""))
                if note:
                    logger.warning("Alpha Vantage rate-limit note: %s", note)
                else:
                    logger.warning(
                        "Alpha Vantage response has no Time Series key for %s: %s",
                        ticker,
                        list(data.keys()),
                    )
                return None

            ts_data: dict[str, dict[str, str]] = data[ts_key]
            df = pd.DataFrame.from_dict(ts_data, orient="index")
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            df = _normalize_columns(df)

            if df.empty:
                logger.debug("Alpha Vantage returned empty TS for %s", ticker)
                return None

            return df

        except requests.RequestException:
            logger.exception("Alpha Vantage HTTP error for %s", ticker)
            return None
        except (KeyError, ValueError):
            logger.exception("Alpha Vantage parse error for %s", ticker)
            return None

    # ------------------------------------------------------------------
    # Private: Twelve Data fetch
    # ------------------------------------------------------------------
    _TD_INTERVAL_MAP: dict[str, str] = {
        "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
        "60m": "1h", "1h": "1h", "1d": "1day", "1wk": "1week",
    }

    def _fetch_twelvedata(
        self,
        ticker: str,
        interval: str = "5m",
        outputsize: int = 100,
    ) -> pd.DataFrame | None:
        """Fetch OHLCV bars from Twelve Data REST API.

        Supports both intraday and daily/weekly intervals.
        Free tier: 800 calls/day, 8/min.
        """
        if not self._can_call_twelvedata():
            return None
        self._increment_td_calls()

        td_interval = self._TD_INTERVAL_MAP.get(interval)
        if td_interval is None:
            return None

        resolved = _resolve_ticker(ticker)
        if resolved.startswith("^"):
            resolved = resolved.lstrip("^")

        # TwelveData LSE symbol format: QQQ3.L → QQQ3:LSE
        # The .L suffix is yfinance convention; TwelveData uses :EXCHANGE format
        if resolved.endswith(".L"):
            td_symbol = resolved[:-2] + ":LSE"
        else:
            td_symbol = resolved

        try:
            resp = self._session.get(
                f"{self._td_base_url}/time_series",
                params={
                    "symbol": td_symbol,
                    "interval": td_interval,
                    "outputsize": outputsize,
                    "apikey": self._td_api_key,
                    "format": "JSON",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if "code" in data and data["code"] != 200:
                logger.debug("Twelve Data error for %s: %s", ticker, data.get("message", ""))
                return None

            values = data.get("values", [])
            if not values:
                return None

            df = pd.DataFrame(values)
            df = df.rename(columns={
                "datetime": "Datetime",
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
            })
            if "Datetime" in df.columns:
                df.index = pd.to_datetime(df["Datetime"])
                df = df.drop(columns=["Datetime"])
            df = df.sort_index()

            for col in ["Open", "High", "Low", "Close", "Volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
            return df[keep].copy()

        except requests.RequestException:
            logger.debug("Twelve Data HTTP error for %s", ticker)
            return None
        except (KeyError, ValueError):
            logger.debug("Twelve Data parse error for %s", ticker)
            return None

    def _td_resolve_symbol(self, ticker: str) -> str:
        """Resolve ticker to TwelveData symbol format.
        LSE tickers (QQQ3.L) → TwelveData format (QQQ3:LSE).
        US tickers passed through unchanged.
        """
        resolved = _resolve_ticker(ticker)
        if resolved.startswith("^"):
            resolved = resolved.lstrip("^")
        if resolved.endswith(".L"):
            return resolved[:-2] + ":LSE"
        return resolved

    def _fetch_twelvedata_quote(self, ticker: str) -> float:
        """Get a real-time quote from Twelve Data. Returns price or 0.0."""
        if not self._can_call_twelvedata():
            return 0.0
        self._increment_td_calls()

        td_symbol = self._td_resolve_symbol(ticker)

        try:
            resp = self._session.get(
                f"{self._td_base_url}/quote",
                params={"symbol": td_symbol, "apikey": self._td_api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            price = float(data.get("close", 0) or 0)
            return price
        except Exception:
            return 0.0

    def _fetch_twelvedata_change(self, ticker: str) -> float:
        """Get percent_change from Twelve Data quote. Returns 0.0 on failure."""
        if not self._can_call_twelvedata():
            return 0.0
        self._increment_td_calls()

        td_symbol = self._td_resolve_symbol(ticker)
        try:
            resp = self._session.get(
                f"{self._td_base_url}/quote",
                params={"symbol": td_symbol, "apikey": self._td_api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return round(float(data.get("percent_change", 0) or 0), 3)
        except Exception:
            return 0.0

    def _fetch_fmp_change(self, ticker: str) -> float:
        """Get changesPercentage from FMP quote. Returns 0.0 on failure."""
        if not self._fmp_api_key:
            return 0.0
        resolved = _resolve_ticker(ticker)
        if resolved.startswith("^"):
            resolved = resolved.lstrip("^")
        try:
            resp = self._session.get(
                f"{self._fmp_base_url}/quote/{resolved}",
                params={"apikey": self._fmp_api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return round(float(data[0].get("changesPercentage", 0) or 0), 3)
            return 0.0
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # Private: Financial Modeling Prep fetch
    # ------------------------------------------------------------------
    def _fetch_fmp_quote(self, ticker: str) -> float:
        """Get a real-time price from FMP. Returns price or 0.0."""
        if not self._fmp_api_key:
            return 0.0

        resolved = _resolve_ticker(ticker)
        if resolved.startswith("^"):
            resolved = resolved.lstrip("^")

        try:
            resp = self._session.get(
                f"{self._fmp_base_url}/quote/{resolved}",
                params={"apikey": self._fmp_api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return float(data[0].get("price", 0) or 0)
            return 0.0
        except Exception:
            return 0.0

    def _fetch_fmp_bars(
        self,
        ticker: str,
        interval: str = "5m",
    ) -> pd.DataFrame | None:
        """Fetch intraday or daily bars from FMP. Supports 1min-4hour and daily."""
        if not self._fmp_api_key:
            return None

        fmp_interval_map = {
            "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
            "60m": "1hour", "1h": "1hour",
        }

        resolved = _resolve_ticker(ticker)
        if resolved.startswith("^"):
            resolved = resolved.lstrip("^")

        fmp_interval = fmp_interval_map.get(interval)
        if fmp_interval is None:
            # FMP daily bars use a different endpoint
            if interval in ("1d", "1wk"):
                return self._fetch_fmp_daily(resolved)
            return None

        try:
            resp = self._session.get(
                f"{self._fmp_base_url}/historical-chart/{fmp_interval}/{resolved}",
                params={"apikey": self._fmp_api_key},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data or not isinstance(data, list):
                return None

            df = pd.DataFrame(data)
            df = df.rename(columns={
                "date": "Datetime",
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
            })
            if "Datetime" in df.columns:
                df.index = pd.to_datetime(df["Datetime"])
                df = df.drop(columns=["Datetime"])
            df = df.sort_index()

            for col in ["Open", "High", "Low", "Close", "Volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
            return df[keep].copy()

        except Exception:
            logger.debug("FMP intraday fetch error for %s", ticker)
            return None

    def _fetch_fmp_daily(self, ticker: str) -> pd.DataFrame | None:
        """Fetch daily bars from FMP historical endpoint."""
        try:
            resp = self._session.get(
                f"{self._fmp_base_url}/historical-price-full/{ticker}",
                params={"apikey": self._fmp_api_key, "serietype": "bar"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            historical = data.get("historical", [])
            if not historical:
                return None

            df = pd.DataFrame(historical)
            df = df.rename(columns={
                "date": "Datetime",
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
            })
            if "Datetime" in df.columns:
                df.index = pd.to_datetime(df["Datetime"])
                df = df.drop(columns=["Datetime"])
            df = df.sort_index()

            for col in ["Open", "High", "Low", "Close", "Volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
            return df[keep].copy()

        except Exception:
            logger.debug("FMP daily fetch error for %s", ticker)
            return None

    # ------------------------------------------------------------------
    # Retry logic with exponential backoff
    # ------------------------------------------------------------------
    def _fetch_with_retry(
        self,
        ticker: str,
        provider: str,
        fetch_fn,
        max_retries: int = 3,
        **kwargs,
    ) -> pd.DataFrame | None:
        """Fetch data from a provider with exponential backoff retry.

        Parameters
        ----------
        ticker : str
            The ticker symbol to fetch.
        provider : str
            Human-readable provider name for logging (e.g. ``"yfinance"``).
        fetch_fn : callable
            The actual fetch function to call. Must accept ``ticker`` as first
            arg plus any ``**kwargs``, and return ``pd.DataFrame | None``.
        max_retries : int
            Maximum number of attempts (default 3).
        **kwargs
            Extra keyword arguments forwarded to *fetch_fn*.

        Returns
        -------
        pd.DataFrame | None
            The fetched DataFrame, or ``None`` if all retries failed.
        """
        for attempt in range(max_retries):
            try:
                result = fetch_fn(ticker, **kwargs)
                if result is not None and not result.empty:
                    if attempt > 0:
                        logger.info(
                            "Feed %s recovered for %s on attempt %d/%d",
                            provider, ticker, attempt + 1, max_retries,
                        )
                    return result
            except Exception as e:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "Feed %s attempt %d/%d failed for %s: %s (retry in %ds)",
                    provider, attempt + 1, max_retries, ticker, e, wait,
                )
                if attempt < max_retries - 1:
                    # Retry backoff sleep — sync context, blocking is intentional.
                    time.sleep(wait)
        return None

    # ------------------------------------------------------------------
    # Emergency alerting
    # ------------------------------------------------------------------
    def _alert_all_feeds_down(self) -> None:
        """Send emergency alert when all data feeds are unavailable.
        Rate-limited: only fires once per 5 minutes to avoid log spam.
        """
        import time as _time
        now = _time.time()
        last = getattr(self, "_last_feed_alert", 0.0)
        if now - last < 300:  # 5-minute cooldown
            return
        self._last_feed_alert = now
        logger.error("SYSTEM_DOWN: ALL data feeds failed — trading suspended")
        try:
            from delivery.telegram_bot import TelegramDelivery
            tg = TelegramDelivery()
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(tg.send_alert(
                        "\U0001f6a8 SYSTEM_DOWN: ALL data feeds failed. Trading suspended."
                    ))
                else:
                    loop.run_until_complete(tg.send_alert(
                        "\U0001f6a8 SYSTEM_DOWN: ALL data feeds failed. Trading suspended."
                    ))
            except Exception:
                pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Utility / diagnostics
    # ------------------------------------------------------------------
    @property
    def av_calls_remaining(self) -> int:
        """How many Alpha Vantage calls remain for today."""
        global _av_calls_today, _av_calls_date
        if _av_calls_date != date.today():
            return self._av_max_calls
        return max(0, self._av_max_calls - _av_calls_today)

    def health_check(self) -> dict[str, Any]:
        """Quick diagnostic: can we reach all data sources?

        Returns a dict with boolean flags and latency info.  Used by the
        system start-up sequence to verify feeds before the first scan.
        """
        report: dict[str, Any] = {
            "yfinance_ok": False,
            "yfinance_latency_ms": 0,
            "alpha_vantage_ok": False,
            "alpha_vantage_latency_ms": 0,
            "twelvedata_ok": False,
            "twelvedata_latency_ms": 0,
            "fmp_ok": False,
            "fmp_latency_ms": 0,
            "av_calls_remaining": self.av_calls_remaining,
        }

        # Test yfinance with a lightweight fetch
        t0 = time.monotonic()
        try:
            df = yf.download("SPY", period="1d", interval="1d", progress=False, timeout=10)
            if df is not None and not df.empty:
                report["yfinance_ok"] = True
        except Exception:
            logger.exception("yfinance health check failed")
        report["yfinance_latency_ms"] = int((time.monotonic() - t0) * 1000)

        # Test Twelve Data
        if self._td_api_key:
            t0 = time.monotonic()
            try:
                resp = self._session.get(
                    f"{self._td_base_url}/quote",
                    params={"symbol": "SPY", "apikey": self._td_api_key},
                    timeout=10,
                )
                data = resp.json()
                report["twelvedata_ok"] = "close" in data and float(data["close"] or 0) > 0
            except Exception:
                logger.debug("Twelve Data health check failed")
            report["twelvedata_latency_ms"] = int((time.monotonic() - t0) * 1000)

        # Test FMP
        if self._fmp_api_key:
            t0 = time.monotonic()
            try:
                resp = self._session.get(
                    f"{self._fmp_base_url}/quote/SPY",
                    params={"apikey": self._fmp_api_key},
                    timeout=10,
                )
                data = resp.json()
                report["fmp_ok"] = isinstance(data, list) and len(data) > 0
            except Exception:
                logger.debug("FMP health check failed")
            report["fmp_latency_ms"] = int((time.monotonic() - t0) * 1000)

        # Test Alpha Vantage (only if key is set and budget allows)
        if self._av_api_key and self.av_calls_remaining > 0:
            t0 = time.monotonic()
            try:
                resp = self._session.get(
                    self._av_base_url,
                    params={
                        "function": "TIME_SERIES_INTRADAY",
                        "symbol": "SPY",
                        "interval": "5min",
                        "apikey": self._av_api_key,
                        "outputsize": "compact",
                    },
                    timeout=10,
                )
                self._increment_av_calls()
                data = resp.json()
                report["alpha_vantage_ok"] = any(
                    "Time Series" in k for k in data
                )
            except Exception:
                logger.exception("Alpha Vantage health check failed")
            report["alpha_vantage_latency_ms"] = int((time.monotonic() - t0) * 1000)

        report["av_calls_remaining"] = self.av_calls_remaining
        return report

    def verify_feeds(self) -> dict[str, bool]:
        """Test each configured feed provider on startup and log results.

        Calls ``health_check()`` internally, then logs a clear summary of
        which providers are reachable.  If zero providers are reachable,
        fires the SYSTEM_DOWN alert.

        Returns
        -------
        dict[str, bool]
            Mapping of provider name to reachability status.
        """
        report = self.health_check()

        status: dict[str, bool] = {
            "yfinance": report["yfinance_ok"],
            "twelvedata": report["twelvedata_ok"],
            "fmp": report["fmp_ok"],
            "alpha_vantage": report["alpha_vantage_ok"],
        }

        active = [name for name, ok in status.items() if ok]
        inactive = [name for name, ok in status.items() if not ok]

        if active:
            logger.info(
                "Feed verification complete — ACTIVE providers: %s  |  latencies: yf=%dms td=%dms fmp=%dms av=%dms",
                ", ".join(active),
                report["yfinance_latency_ms"],
                report["twelvedata_latency_ms"],
                report["fmp_latency_ms"],
                report["alpha_vantage_latency_ms"],
            )
        if inactive:
            logger.warning(
                "Feed verification — INACTIVE providers: %s",
                ", ".join(inactive),
            )
        if not active:
            logger.critical(
                "Feed verification FAILED — NO providers are reachable"
            )
            self._alert_all_feeds_down()

        return status

    def get_active_providers(self) -> list[str]:
        """Return a list of provider names that are currently functional.

        Performs a lightweight health check and returns only the providers
        that responded successfully.  Useful for dashboards and diagnostics.

        Returns
        -------
        list[str]
            E.g. ``["yfinance", "twelvedata", "fmp"]``
        """
        report = self.health_check()
        providers: list[str] = []
        if report["yfinance_ok"]:
            providers.append("yfinance")
        if report["twelvedata_ok"]:
            providers.append("twelvedata")
        if report["fmp_ok"]:
            providers.append("fmp")
        if report["alpha_vantage_ok"]:
            providers.append("alpha_vantage")
        return providers

    def __repr__(self) -> str:
        return (
            f"<DataFeedManager  "
            f"av={'SET' if self._av_api_key else '-'}  "
            f"td={'SET' if self._td_api_key else '-'}  "
            f"fmp={'SET' if self._fmp_api_key else '-'}  "
            f"av_remaining={self.av_calls_remaining}>"
        )
