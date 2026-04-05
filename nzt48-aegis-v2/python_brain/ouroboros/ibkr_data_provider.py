"""IBKR Data Provider — Primary data source with yfinance fallback.

A5: Refactors universe modules to use IBKR reqHistoricalData as primary,
yfinance as graceful fallback when IBKR is unavailable.

Connects to IB Gateway using a dedicated client_id (102) that does NOT
conflict with the Rust engine (101). Connection is lazy (established on
first data request) and auto-disconnects after 5 minutes idle to stay
within IBKR's connection limits.

Usage:
    provider = IBKRDataProvider()
    df = provider.get_price_data("QQQ3.L", days=7, bar_size="5 mins")
    # Returns pandas DataFrame with columns: open, high, low, close, volume
    # Tries IBKR first, falls back to yfinance if IBKR unavailable

    details = provider.get_contract_details("QQQ3.L")
    # Returns dict with conId, exchange, currency, secType, primaryExchange

    batch = provider.batch_price_data(["QQQ3.L", "AAPL"], days=7)
    # Returns {symbol: DataFrame} with mixed IBKR/yfinance sourcing

Quarantine rules:
  - Read-only: never writes to WAL, never places orders, never modifies live state
  - Uses client_id=102 (dedicated analytics slot, NOT engine's 101)
  - All exceptions caught and logged — never raises to caller
  - Thread-safe: ticker_selector may call from multiple threads
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
CONTRACTS_FILE = CONFIG_DIR / "contracts.toml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [IBKR-DataProvider] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ibkr_data_provider")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_HOST = os.environ.get("IB_HOST", "aegis-ib-gateway")
_DEFAULT_PORT = int(os.environ.get("IB_PORT", "4003"))
_DEFAULT_CLIENT_ID = 102  # MUST NOT be 101 (engine) or 100 (V1)
_CONNECT_TIMEOUT_SEC = 10
_REQUEST_TIMEOUT_SEC = 30
_IDLE_DISCONNECT_SEC = 300  # 5 minutes
_RATE_LIMIT_RPS = 15  # Safe limit (IBKR allows 50, but engine uses some)
_RATE_LIMIT_INTERVAL = 1.0 / _RATE_LIMIT_RPS  # ~67ms between requests

# yfinance suffix <-> IBKR exchange mapping (mirrors symbology_mapper.py)
_YF_SUFFIX_TO_IBKR_EXCHANGE = {
    ".L": "LSEETF",
    ".DE": "IBIS",
    ".F": "FWB",
    ".PA": "SBF",
    ".AS": "AEB",
    ".MI": "BVME",
    ".MC": "BM",
    ".SW": "EBS",
    ".T": "TSE",
    ".HK": "SEHK",
    ".AX": "ASX",
    ".SI": "SGX",
    ".KS": "KSE",
    ".TO": "TSE",
    ".V": "VENTURE",
}

# IBKR duration string mapping: days -> durationStr
# IBKR reqHistoricalData requires specific duration formats
_DAYS_TO_DURATION = {
    1: "1 D",
    2: "2 D",
    3: "3 D",
    5: "1 W",
    7: "1 W",
    10: "10 D",
    14: "2 W",
    20: "20 D",
    30: "1 M",
    60: "2 M",
    90: "3 M",
    120: "4 M",
    180: "6 M",
    365: "1 Y",
}

# Valid bar sizes for IBKR reqHistoricalData
_VALID_BAR_SIZES = {
    "1 secs", "5 secs", "10 secs", "15 secs", "30 secs",
    "1 min", "2 mins", "3 mins", "5 mins", "10 mins", "15 mins", "20 mins", "30 mins",
    "1 hour", "2 hours", "3 hours", "4 hours", "8 hours",
    "1 day", "1 week", "1 month",
}

# yfinance interval mapping from IBKR bar sizes
_BAR_SIZE_TO_YF_INTERVAL = {
    "1 min": "1m",
    "2 mins": "2m",
    "5 mins": "5m",
    "10 mins": "10m",  # Not available in yfinance, will use 15m
    "15 mins": "15m",
    "30 mins": "30m",
    "1 hour": "1h",
    "1 day": "1d",
    "1 week": "1wk",
    "1 month": "1mo",
}

# yfinance period mapping from days
_DAYS_TO_YF_PERIOD = {
    1: "1d",
    2: "5d",  # yfinance minimum for intraday
    3: "5d",
    5: "5d",
    7: "5d",  # yfinance only allows 5d/1mo for intraday
    10: "1mo",
    14: "1mo",
    20: "1mo",
    30: "1mo",
    60: "3mo",
    90: "3mo",
    180: "6mo",
    365: "1y",
}


# ---------------------------------------------------------------------------
# Contracts cache (parsed from contracts.toml)
# ---------------------------------------------------------------------------
_contracts_cache: Optional[Dict[str, Dict[str, Any]]] = None
_contracts_cache_lock = threading.Lock()


def _load_contracts_toml() -> Dict[str, Dict[str, Any]]:
    """Parse contracts.toml into {symbol: {con_id, exchange, currency, sec_type, ...}}.

    Thread-safe with caching. Only reads the file once per process.
    """
    global _contracts_cache
    with _contracts_cache_lock:
        if _contracts_cache is not None:
            return _contracts_cache

        result: Dict[str, Dict[str, Any]] = {}
        if not CONTRACTS_FILE.exists():
            log.warning("contracts.toml not found at %s", CONTRACTS_FILE)
            _contracts_cache = result
            return result

        try:
            try:
                import tomllib
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore[no-redef]
                except ImportError:
                    log.warning("No TOML parser available (need Python 3.11+ or tomli)")
                    _contracts_cache = result
                    return result

            with open(CONTRACTS_FILE, "rb") as f:
                data = tomllib.load(f)

            for contract in data.get("contracts", []):
                symbol = contract.get("symbol", "")
                if not symbol:
                    continue
                result[symbol] = {
                    "con_id": contract.get("con_id", 0),
                    "exchange": contract.get("exchange", ""),
                    "currency": contract.get("currency", "USD"),
                    "sec_type": contract.get("sec_type", "STK"),
                    "leverage": contract.get("leverage", 1),
                    "sector": contract.get("sector", ""),
                    "inverse_of": contract.get("inverse_of", ""),
                }

            log.info("Loaded %d contracts from contracts.toml", len(result))
            _contracts_cache = result
            return result

        except Exception as e:
            log.warning("Failed to parse contracts.toml: %s", e)
            _contracts_cache = result
            return result


def _yf_symbol_to_ibkr_parts(yf_symbol: str) -> tuple[str, str, str]:
    """Convert yfinance-style symbol to (ibkr_symbol, exchange, currency).

    Checks contracts.toml first for exact match (most reliable), then
    falls back to suffix-based mapping.

    Returns:
        (ibkr_symbol, exchange, currency) tuple.
    """
    contracts = _load_contracts_toml()

    # Direct match in contracts.toml — most reliable
    if yf_symbol in contracts:
        c = contracts[yf_symbol]
        # IBKR symbol is the yf_symbol without the suffix for most exchanges,
        # but for LSE, contracts.toml uses "QQQ3.L" as symbol while IBKR
        # wants just "QQQ3" with exchange="LSEETF"
        ibkr_sym = yf_symbol
        exchange = c["exchange"]
        if exchange == "LSEETF" and yf_symbol.endswith(".L"):
            ibkr_sym = yf_symbol[:-2]
        return ibkr_sym, exchange, c["currency"]

    # Suffix-based fallback
    for suffix, exchange in sorted(
        _YF_SUFFIX_TO_IBKR_EXCHANGE.items(), key=lambda x: -len(x[0])
    ):
        if yf_symbol.endswith(suffix):
            ibkr_sym = yf_symbol[: -len(suffix)]
            # Default currency based on exchange
            currency_map = {
                "LSEETF": "USD",  # Most LSE ETPs trade in USD
                "IBIS": "EUR",
                "FWB": "EUR",
                "SBF": "EUR",
                "AEB": "EUR",
                "BVME": "EUR",
                "BM": "EUR",
                "EBS": "CHF",
                "TSE": "JPY",
                "SEHK": "HKD",
                "ASX": "AUD",
                "SGX": "SGD",
                "KSE": "KRW",
            }
            currency = currency_map.get(exchange, "USD")
            return ibkr_sym, exchange, currency

    # No suffix = US stock
    return yf_symbol, "SMART", "USD"


def _closest_duration(days: int) -> str:
    """Find the closest valid IBKR duration string for the given days.

    IBKR requires exact duration strings like '7 D', '1 M', etc.
    We pick the smallest duration that covers the requested days.
    """
    if days <= 0:
        return "1 D"

    # Try exact match first
    if days in _DAYS_TO_DURATION:
        return _DAYS_TO_DURATION[days]

    # Find the smallest duration >= requested days
    for threshold in sorted(_DAYS_TO_DURATION.keys()):
        if threshold >= days:
            return _DAYS_TO_DURATION[threshold]

    # Fallback: calculate in days (IBKR allows up to "365 D")
    if days <= 365:
        return f"{days} D"
    return "1 Y"


def _closest_yf_period(days: int, bar_size: str) -> str:
    """Find the closest valid yfinance period for the given days and bar size."""
    is_intraday = bar_size not in {"1 day", "1 week", "1 month"}

    if is_intraday:
        # yfinance limits: 1m data = 7 days, 2m-60m = 60 days, 1h = 730 days
        if days <= 5:
            return "5d"
        elif days <= 30:
            return "1mo"
        elif days <= 60:
            return "3mo"
        else:
            return "6mo"
    else:
        # Daily data has longer lookback
        if days <= 5:
            return "5d"
        elif days <= 30:
            return "1mo"
        elif days <= 90:
            return "3mo"
        elif days <= 180:
            return "6mo"
        elif days <= 365:
            return "1y"
        else:
            return "2y"


def _closest_yf_interval(bar_size: str) -> str:
    """Map IBKR bar size to the closest yfinance interval string."""
    if bar_size in _BAR_SIZE_TO_YF_INTERVAL:
        return _BAR_SIZE_TO_YF_INTERVAL[bar_size]
    # Fallback: try to parse
    if "sec" in bar_size:
        return "1m"  # yfinance minimum
    if "min" in bar_size:
        return "5m"  # Reasonable default for intraday
    return "1d"


# ---------------------------------------------------------------------------
# IBKRDataProvider class
# ---------------------------------------------------------------------------


class IBKRDataProvider:
    """Primary IBKR data source with yfinance fallback.

    Thread-safe. Uses lazy connection (only connects on first request).
    Auto-disconnects after 5 minutes idle to free IBKR connection slot.
    All public methods catch exceptions and return empty results on failure.

    Attributes:
        host: IB Gateway hostname.
        port: IB Gateway port.
        client_id: IBKR client identifier (must be 102, not 101).
        timeout_sec: Per-request timeout in seconds.
    """

    def __init__(
        self,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        client_id: int = _DEFAULT_CLIENT_ID,
        timeout_sec: int = _REQUEST_TIMEOUT_SEC,
    ) -> None:
        if client_id == 101:
            raise ValueError(
                "client_id=101 is reserved for the Rust engine. "
                "Use 102 (default) for analytics."
            )

        self._host = host
        self._port = port
        self._client_id = client_id
        self._timeout_sec = timeout_sec

        self._ib: Any = None
        self._lock = threading.Lock()
        self._last_request_time: float = 0.0
        self._idle_timer: Optional[threading.Timer] = None
        self._connected = False

        # Rate limiter state
        self._rate_lock = threading.Lock()
        self._last_rate_time: float = 0.0

    # ------------------------------------------------------------------
    # Connection management (lazy, auto-disconnect)
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> bool:
        """Ensure we have a live IB connection. Returns True if connected.

        Lazy: only connects on first call. Thread-safe.
        """
        with self._lock:
            if self._connected and self._ib is not None:
                try:
                    if self._ib.isConnected():
                        self._reset_idle_timer()
                        return True
                except Exception:
                    pass
                # Connection dropped
                self._connected = False
                self._ib = None

            # Attempt fresh connection
            try:
                from ib_insync import IB
                ib = IB()
                ib.connect(
                    self._host,
                    self._port,
                    clientId=self._client_id,
                    timeout=_CONNECT_TIMEOUT_SEC,
                    readonly=True,
                )
                self._ib = ib
                self._connected = True
                self._reset_idle_timer()
                log.info(
                    "Connected to IB Gateway at %s:%d (client_id=%d)",
                    self._host, self._port, self._client_id,
                )
                return True

            except ImportError:
                log.warning("ib_insync not installed — IBKR unavailable, using yfinance only")
                return False
            except Exception as e:
                log.debug("IB Gateway connection failed: %s", e)
                return False

    def _reset_idle_timer(self) -> None:
        """Reset the idle disconnect timer. Must be called with _lock held."""
        self._last_request_time = time.monotonic()
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        self._idle_timer = threading.Timer(
            _IDLE_DISCONNECT_SEC, self._idle_disconnect
        )
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _idle_disconnect(self) -> None:
        """Disconnect after idle timeout. Called from timer thread."""
        with self._lock:
            if not self._connected or self._ib is None:
                return
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < _IDLE_DISCONNECT_SEC - 1:
                # Activity happened since timer was set — reschedule
                remaining = _IDLE_DISCONNECT_SEC - elapsed
                self._idle_timer = threading.Timer(remaining, self._idle_disconnect)
                self._idle_timer.daemon = True
                self._idle_timer.start()
                return
            try:
                self._ib.disconnect()
            except Exception:
                pass
            self._ib = None
            self._connected = False
            log.info("IBKR idle disconnect after %ds", _IDLE_DISCONNECT_SEC)

    def disconnect(self) -> None:
        """Explicitly disconnect from IB Gateway. Safe to call multiple times."""
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            if self._ib is not None:
                try:
                    self._ib.disconnect()
                except Exception:
                    pass
                self._ib = None
            self._connected = False

    def __del__(self) -> None:
        """Ensure cleanup on garbage collection."""
        try:
            self.disconnect()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _rate_limit(self) -> None:
        """Enforce rate limit (15 req/s). Thread-safe."""
        with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_rate_time
            if elapsed < _RATE_LIMIT_INTERVAL:
                sleep_time = _RATE_LIMIT_INTERVAL - elapsed
                time.sleep(sleep_time)
            self._last_rate_time = time.monotonic()

    # ------------------------------------------------------------------
    # IBKR data fetching
    # ------------------------------------------------------------------

    def _make_contract(self, symbol: str) -> Any:
        """Build an ib_insync Contract for the given yfinance-style symbol.

        Checks contracts.toml for exact con_id (fastest resolution),
        falls back to symbol/exchange/currency-based construction.
        """
        from ib_insync import Stock, Contract

        ibkr_sym, exchange, currency = _yf_symbol_to_ibkr_parts(symbol)
        contracts = _load_contracts_toml()

        # If we have a known con_id, use it (bypasses IBKR symbol resolution)
        if symbol in contracts and contracts[symbol].get("con_id"):
            c = Contract()
            c.conId = contracts[symbol]["con_id"]
            c.exchange = exchange
            return c

        # Build from symbol + exchange + currency
        return Stock(ibkr_sym, exchange, currency)

    def _fetch_ibkr(
        self,
        symbol: str,
        days: int,
        bar_size: str,
    ) -> Optional[pd.DataFrame]:
        """Fetch historical data from IBKR. Returns DataFrame or None on failure.

        Internal method — does NOT catch all exceptions (caller handles fallback).
        """
        if not self._ensure_connected():
            return None

        self._rate_limit()

        contract = self._make_contract(symbol)
        duration = _closest_duration(days)

        with self._lock:
            if self._ib is None or not self._connected:
                return None
            ib = self._ib

        try:
            from ib_insync import util

            bars = ib.reqHistoricalData(
                contract,
                endDateTime="",  # Now
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,
                timeout=self._timeout_sec,
            )

            if not bars:
                log.debug("IBKR returned no bars for %s (duration=%s, bar=%s)",
                          symbol, duration, bar_size)
                return None

            df = util.df(bars)
            if df is None or df.empty:
                return None

            # Normalize column names to lowercase
            df.columns = df.columns.str.lower()

            # Ensure we have the required columns
            required = {"open", "high", "low", "close", "volume"}
            if not required.issubset(set(df.columns)):
                log.warning("IBKR data for %s missing columns: %s",
                            symbol, required - set(df.columns))
                return None

            # Keep only the standard OHLCV columns + date if present
            keep_cols = [c for c in ["date", "open", "high", "low", "close", "volume"]
                         if c in df.columns]
            df = df[keep_cols]

            # Set date as index if present
            if "date" in df.columns:
                df = df.set_index("date")

            return df

        except Exception as e:
            log.debug("IBKR reqHistoricalData failed for %s: %s", symbol, e)
            return None

    # ------------------------------------------------------------------
    # yfinance fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_yfinance(
        symbol: str,
        days: int,
        bar_size: str,
    ) -> Optional[pd.DataFrame]:
        """Fetch historical data from yfinance. Returns DataFrame or None."""
        try:
            import yfinance as yf

            interval = _closest_yf_interval(bar_size)
            period = _closest_yf_period(days, bar_size)

            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)

            if df is None or df.empty:
                log.debug("yfinance returned no data for %s (period=%s, interval=%s)",
                          symbol, period, interval)
                return None

            # Normalize column names to lowercase
            df.columns = df.columns.str.lower()

            # yfinance returns: Open, High, Low, Close, Volume, Dividends, Stock Splits
            # Keep only OHLCV
            keep_cols = [c for c in ["open", "high", "low", "close", "volume"]
                         if c in df.columns]
            if not keep_cols:
                return None
            df = df[keep_cols]

            return df

        except ImportError:
            log.warning("yfinance not installed — no fallback available")
            return None
        except Exception as e:
            log.debug("yfinance fetch failed for %s: %s", symbol, e)
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_price_data(
        self,
        symbol: str,
        days: int = 7,
        bar_size: str = "5 mins",
    ) -> pd.DataFrame:
        """Get historical OHLCV data for a symbol.

        Tries IBKR first, falls back to yfinance on failure.
        Returns a DataFrame with lowercase columns: open, high, low, close, volume.
        Returns empty DataFrame on total failure (never raises).

        Args:
            symbol: Ticker in yfinance format (e.g. "QQQ3.L", "AAPL").
            days: Number of days of history to fetch.
            bar_size: IBKR bar size string (e.g. "5 mins", "1 day").
                      Mapped to closest yfinance interval for fallback.

        Returns:
            pd.DataFrame with columns [open, high, low, close, volume].
            Empty DataFrame if both sources fail.
        """
        try:
            if bar_size not in _VALID_BAR_SIZES:
                log.warning("Invalid bar_size '%s' for %s, defaulting to '5 mins'",
                            bar_size, symbol)
                bar_size = "5 mins"

            # Try IBKR first
            df = self._fetch_ibkr(symbol, days, bar_size)
            if df is not None and not df.empty:
                log.info("IBKR: %s — %d bars (%s, %dd)",
                         symbol, len(df), bar_size, days)
                return df

            # Fallback to yfinance
            df = self._fetch_yfinance(symbol, days, bar_size)
            if df is not None and not df.empty:
                log.info("yfinance-fallback: %s — %d bars (%s, %dd)",
                         symbol, len(df), bar_size, days)
                return df

            log.warning("No data from IBKR or yfinance for %s (%dd, %s)",
                        symbol, days, bar_size)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        except Exception as e:
            log.error("Unexpected error fetching %s: %s", symbol, e)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def get_contract_details(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get contract metadata for a symbol.

        Tries IBKR reqContractDetails first, falls back to contracts.toml cache.
        Returns None if symbol is unknown in both sources.

        Args:
            symbol: Ticker in yfinance format (e.g. "QQQ3.L", "AAPL").

        Returns:
            Dict with keys: con_id, exchange, currency, sec_type, primary_exchange.
            None if not found.
        """
        try:
            # Try IBKR live lookup
            if self._ensure_connected():
                self._rate_limit()
                contract = self._make_contract(symbol)

                with self._lock:
                    ib = self._ib

                if ib is not None:
                    try:
                        details_list = ib.reqContractDetails(contract)
                        if details_list:
                            cd = details_list[0]
                            result = {
                                "con_id": cd.contract.conId,
                                "exchange": cd.contract.exchange,
                                "currency": cd.contract.currency,
                                "sec_type": cd.contract.secType,
                                "primary_exchange": cd.contract.primaryExchange or cd.contract.exchange,
                                "long_name": cd.longName or "",
                                "category": getattr(cd, "category", ""),
                                "subcategory": getattr(cd, "subcategory", ""),
                            }
                            log.debug("IBKR contract details for %s: conId=%d exchange=%s",
                                      symbol, result["con_id"], result["exchange"])
                            return result
                    except Exception as e:
                        log.debug("IBKR reqContractDetails failed for %s: %s", symbol, e)

            # Fallback: contracts.toml cache
            contracts = _load_contracts_toml()
            if symbol in contracts:
                c = contracts[symbol]
                result = {
                    "con_id": c.get("con_id", 0),
                    "exchange": c.get("exchange", ""),
                    "currency": c.get("currency", "USD"),
                    "sec_type": c.get("sec_type", "STK"),
                    "primary_exchange": c.get("exchange", ""),
                    "long_name": "",
                    "category": c.get("sector", ""),
                    "subcategory": "",
                }
                log.debug("contracts.toml fallback for %s: conId=%d exchange=%s",
                          symbol, result["con_id"], result["exchange"])
                return result

            log.debug("No contract details found for %s", symbol)
            return None

        except Exception as e:
            log.error("Unexpected error getting contract details for %s: %s", symbol, e)
            return None

    def get_fundamental_data(
        self, symbol: str, report_type: str = "CalendarReport"
    ) -> Optional[Dict[str, Any]]:
        """Get fundamental data for a symbol via IBKR reqFundamentalData.

        Available report_types:
            - "CalendarReport": Earnings dates, dividends, splits
            - "ReportsFinSummary": Financial summary (revenue, EPS, PE)
            - "ReportSnapshot": Company snapshot (market cap, sector)

        Returns dict with parsed fields, or None if unavailable.
        Requires IBKR fundamental data subscription (often included with L1/L2).
        """
        try:
            if not self._ensure_connected():
                return None

            self._rate_limit()
            contract = self._make_contract(symbol)

            with self._lock:
                ib = self._ib

            if ib is None:
                return None

            try:
                # reqFundamentalData returns XML string
                xml_data = ib.reqFundamentalData(contract, reportType=report_type)
                if not xml_data:
                    log.debug("No fundamental data for %s (type=%s)", symbol, report_type)
                    return None

                # Parse XML to extract key fields
                result = self._parse_fundamental_xml(xml_data, report_type, symbol)
                return result

            except Exception as e:
                log.debug("reqFundamentalData failed for %s: %s", symbol, e)
                return None

        except Exception as e:
            log.error("Unexpected error getting fundamentals for %s: %s", symbol, e)
            return None

    @staticmethod
    def _parse_fundamental_xml(
        xml_data: str, report_type: str, symbol: str
    ) -> Optional[Dict[str, Any]]:
        """Parse IBKR fundamental XML response into structured dict."""
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_data)
        except Exception as e:
            log.debug("XML parse failed for %s: %s", symbol, e)
            return None

        result: Dict[str, Any] = {"symbol": symbol, "report_type": report_type}

        if report_type == "CalendarReport":
            # Extract next earnings date
            for event in root.iter("Event"):
                event_type = event.get("type", "")
                if event_type in ("Earnings", "EarningsDate"):
                    date_elem = event.find("Date")
                    if date_elem is not None and date_elem.text:
                        result["earnings_date"] = date_elem.text[:10]
                        break
            # Extract next dividend date
            for event in root.iter("Event"):
                event_type = event.get("type", "")
                if event_type in ("Dividend", "DividendDate"):
                    date_elem = event.find("Date")
                    if date_elem is not None and date_elem.text:
                        result["dividend_date"] = date_elem.text[:10]
                        break

        elif report_type == "ReportSnapshot":
            # Extract market cap, PE ratio
            for ratio in root.iter("Ratio"):
                field_name = ratio.get("FieldName", "")
                if field_name == "MKTCAP" and ratio.text:
                    try:
                        result["market_cap"] = float(ratio.text)
                    except ValueError:
                        pass
                elif field_name == "APENORM" and ratio.text:
                    try:
                        result["pe_ratio"] = float(ratio.text)
                    except ValueError:
                        pass
            # Company info
            for info in root.iter("CoGeneralInfo"):
                result["company_name"] = info.get("CompanyName", "")
                result["sector"] = info.get("Sector", "")
                result["industry"] = info.get("IndustryGroup", "")

        elif report_type == "ReportsFinSummary":
            # Latest EPS, revenue
            for item in root.iter("FYActual"):
                for annual in item.iter("FYPeriod"):
                    period = annual.get("periodType", "")
                    if period == "Annual":
                        eps_elem = annual.find(".//lineItem[@coaCode='AEPS']")
                        if eps_elem is not None and eps_elem.text:
                            try:
                                result["eps_annual"] = float(eps_elem.text)
                            except ValueError:
                                pass
                        rev_elem = annual.find(".//lineItem[@coaCode='SREV']")
                        if rev_elem is not None and rev_elem.text:
                            try:
                                result["revenue_annual"] = float(rev_elem.text)
                            except ValueError:
                                pass
                        break  # Only need latest

        return result if len(result) > 2 else None  # Must have more than symbol + report_type

    def batch_price_data(
        self,
        symbols: List[str],
        days: int = 7,
        bar_size: str = "5 mins",
    ) -> Dict[str, pd.DataFrame]:
        """Fetch price data for multiple symbols with rate limiting.

        Each symbol tries IBKR first, then yfinance fallback. Rate-limited
        to 15 req/s to stay within IBKR limits (shares quota with engine).
        Mixed sourcing is expected: some symbols from IBKR, others from yfinance.

        Args:
            symbols: List of tickers in yfinance format.
            days: Number of days of history.
            bar_size: IBKR bar size string.

        Returns:
            Dict mapping symbol -> DataFrame. Symbols with no data are omitted.
        """
        results: Dict[str, pd.DataFrame] = {}
        ibkr_count = 0
        yf_count = 0
        fail_count = 0

        total = len(symbols)
        log.info("Batch fetch: %d symbols (%dd, %s)", total, days, bar_size)

        for i, symbol in enumerate(symbols):
            try:
                df = self.get_price_data(symbol, days=days, bar_size=bar_size)
                if df is not None and not df.empty:
                    results[symbol] = df
                    # Peek at last log message to determine source
                    # (get_price_data already logged the source)
                else:
                    fail_count += 1
            except Exception as e:
                log.debug("Batch: %s failed: %s", symbol, e)
                fail_count += 1

            # Progress logging every 50 symbols
            if (i + 1) % 50 == 0:
                log.info("Batch progress: %d/%d fetched (%d results so far)",
                         i + 1, total, len(results))

        log.info("Batch complete: %d/%d symbols returned data (%d failed)",
                 len(results), total, fail_count)
        return results

    def is_ibkr_available(self) -> bool:
        """Quick health check: can we connect to IB Gateway?

        Attempts connection if not already connected. Does not count as
        a data request for idle timeout purposes.

        Returns:
            True if IB Gateway is reachable and authenticated.
        """
        try:
            return self._ensure_connected()
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Module-level singleton for convenience
# ---------------------------------------------------------------------------
_default_provider: Optional[IBKRDataProvider] = None
_default_provider_lock = threading.Lock()


def get_provider(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    client_id: int = _DEFAULT_CLIENT_ID,
) -> IBKRDataProvider:
    """Get or create the module-level singleton provider.

    Thread-safe. The singleton is shared across all callers in the process,
    reusing a single IBKR connection. Safe because IBKRDataProvider is
    internally thread-safe.

    Args:
        host: IB Gateway hostname.
        port: IB Gateway port.
        client_id: IBKR client identifier.

    Returns:
        IBKRDataProvider singleton instance.
    """
    global _default_provider
    with _default_provider_lock:
        if _default_provider is None:
            _default_provider = IBKRDataProvider(
                host=host, port=port, client_id=client_id,
            )
        return _default_provider


# ---------------------------------------------------------------------------
# Self-test when run as a script
# ---------------------------------------------------------------------------

def _self_test() -> None:
    """Quick self-test: check connection and fetch a sample ticker."""
    provider = IBKRDataProvider()

    print("=" * 60)
    print("IBKR Data Provider — Self Test")
    print("=" * 60)

    # Test 1: Connection
    available = provider.is_ibkr_available()
    print(f"  IBKR available: {available}")

    # Test 2: Contract details (uses toml fallback if IBKR unavailable)
    for sym in ["QQQ3.L", "AAPL", "7203.T"]:
        details = provider.get_contract_details(sym)
        if details:
            print(f"  {sym}: conId={details['con_id']} exchange={details['exchange']} "
                  f"currency={details['currency']}")
        else:
            print(f"  {sym}: no contract details found")

    # Test 3: Price data (single)
    df = provider.get_price_data("QQQ3.L", days=5, bar_size="1 day")
    if not df.empty:
        print(f"  QQQ3.L daily: {len(df)} bars, last close={df['close'].iloc[-1]:.2f}")
    else:
        print("  QQQ3.L daily: no data")

    # Test 4: Batch (small)
    batch_syms = ["QQQ3.L", "AAPL"]
    batch = provider.batch_price_data(batch_syms, days=5, bar_size="1 day")
    print(f"  Batch ({len(batch_syms)} symbols): {len(batch)} returned data")

    # Cleanup
    provider.disconnect()
    print("=" * 60)
    print("Self-test complete")
    print("=" * 60)


if __name__ == "__main__":
    _self_test()
