"""
RVOL Scanner -- Full-exchange volume anomaly detection via IBKR snapshots.

Instead of using IBKR's Market Scanner API (which requires extra permissions
and has a 10-scanner limit), this module snapshots ALL tickers on open
exchanges using reqMktData(snapshot=True) and computes RVOL ourselves.

50 snapshots/second x 15-min cycle = can scan 45,000 tickers per cycle.
Actual liquid universe ~2,000-4,000 tickers per cycle = ~1-2 minutes per scan.

Architecture:
  - Historical volume cache: 20-day avg daily volume per ticker (built nightly).
  - Every 15 min: snapshot today's volume for all tickers on open exchanges.
  - Compute RVOL = today_cumulative_volume / (20d_avg * day_fraction).
  - Score each ticker for each entry type (TypeA/B/C/E/F).
  - Output top 40 to scanner_results.json (same format as ibkr_market_scanner.py).
  - Log all activity to rvol_scanner.ndjson for Ouroboros learning.

Usage:
  python3 -m python_brain.ouroboros.rvol_scanner --scan           # One scan + exit
  python3 -m python_brain.ouroboros.rvol_scanner --daemon         # Run every 15 min
  python3 -m python_brain.ouroboros.rvol_scanner --build-cache    # Build volume cache (nightly)
  python3 -m python_brain.ouroboros.rvol_scanner --sim            # Simulation mode (mock data)
  python3 -m python_brain.ouroboros.rvol_scanner --test           # Quick test with 10 tickers

Quarantine rules:
  - Read-only: never places orders, never modifies WAL or live state.
  - Uses client_id=105 to avoid conflict with engine (101), analytics (102),
    ibkr_scanner (103).
  - Graceful reconnection on IB Gateway restarts (daily 23:45 UTC).
  - All exceptions caught -- never crashes the container.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import signal
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RVOL-Scanner] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("rvol_scanner")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_CLIENT_ID = 105
DEFAULT_IB_HOST = "aegis-ib-gateway"
DEFAULT_IB_PORT = 4003
SNAPSHOT_BATCH_SIZE = 50          # IBKR rate: 50 requests/sec for snapshots
SNAPSHOT_BATCH_DELAY_SECS = 1.1   # Slightly over 1s to stay within rate limits
MIN_AVG_VOLUME = 500_000          # Minimum 20d avg volume to include in scan
TOP_RESULTS_PER_TYPE = 40         # Top N results per entry type
VOLUME_CACHE_FILE = "volume_cache.json"
RESULTS_FILE = "scanner_results.json"
LOG_FILE = "rvol_scanner.ndjson"
DAEMON_INTERVAL_SECS = 900        # 15 minutes
CONNECT_TIMEOUT_SECS = 15
YFINANCE_DOWNLOAD_WORKERS = 8     # Parallel yfinance downloads for cache build
YFINANCE_BATCH_SIZE = 50          # Tickers per yfinance batch

# ---------------------------------------------------------------------------
# Exchange session definitions (UTC hours)
# ---------------------------------------------------------------------------
# Each exchange has: (open_hour_utc, close_hour_utc, total_session_minutes)
# total_session_minutes is the full trading session length for RVOL day_fraction.

EXCHANGE_SESSIONS: Dict[str, Dict[str, Any]] = {
    "US": {
        "open_utc": (13, 30),      # 09:30 ET = 13:30 UTC (EST) / 13:30 UTC (EDT adjustment handled)
        "close_utc": (20, 0),      # 16:00 ET = 20:00 UTC (EST)
        "total_minutes": 390,       # 6.5 hours
        "session_hours": [(13, 21)],  # For open detection (slightly wider)
    },
    "TSE": {
        "open_utc": (0, 0),        # 09:00 JST = 00:00 UTC
        "close_utc": (6, 0),       # 15:00 JST = 06:00 UTC
        "total_minutes": 300,       # 5 hours (09:00-11:30, 12:30-15:00 with lunch break, net ~5h)
        "session_hours": [(0, 6)],
    },
    "HKEX": {
        "open_utc": (1, 30),       # 09:30 HKT = 01:30 UTC
        "close_utc": (8, 0),       # 16:00 HKT = 08:00 UTC
        "total_minutes": 330,       # 5.5 hours (09:30-12:00, 13:00-16:00 with lunch)
        "session_hours": [(1, 8)],
    },
    "LSE": {
        "open_utc": (8, 0),        # 08:00 London = 08:00 UTC (GMT) / 07:00 UTC (BST)
        "close_utc": (16, 30),     # 16:30 London
        "total_minutes": 510,       # 8.5 hours
        "session_hours": [(7, 17)],
    },
    "XETRA": {
        "open_utc": (8, 0),        # 09:00 CET = 08:00 UTC
        "close_utc": (16, 30),     # 17:30 CET = 16:30 UTC
        "total_minutes": 510,
        "session_hours": [(7, 17)],
    },
    "EURONEXT": {
        "open_utc": (8, 0),        # 09:00 CET = 08:00 UTC
        "close_utc": (16, 30),     # 17:30 CET = 16:30 UTC
        "total_minutes": 510,
        "session_hours": [(7, 17)],
    },
    "SGX": {
        "open_utc": (1, 0),        # 09:00 SGT = 01:00 UTC
        "close_utc": (9, 0),       # 17:00 SGT = 09:00 UTC
        "total_minutes": 420,       # 7 hours (09:00-12:00, 13:00-17:00 with lunch)
        "session_hours": [(1, 9)],
    },
    "ASX": {
        "open_utc": (0, 0),        # 10:00 AEST = 00:00 UTC
        "close_utc": (6, 0),       # 16:00 AEST = 06:00 UTC
        "total_minutes": 360,       # 6 hours
        "session_hours": [(0, 6)],
    },
}

# Map yfinance ticker suffixes to exchange names.
# Used to determine which exchange a ticker belongs to from universe_10k.txt.
YF_SUFFIX_TO_EXCHANGE: Dict[str, str] = {
    ".T": "TSE",
    ".HK": "HKEX",
    ".L": "LSE",
    ".DE": "XETRA",
    ".PA": "EURONEXT",
    ".AS": "EURONEXT",  # Amsterdam (Euronext)
    ".BR": "EURONEXT",  # Brussels (Euronext)
    ".LS": "EURONEXT",  # Lisbon (Euronext)
    ".SI": "SGX",
    ".AX": "ASX",
    ".KS": "KRX",
    ".KQ": "KRX",
    ".TW": "TWSE",
    ".NS": "NSE",
    ".BO": "NSE",       # Bombay
    ".SA": "B3",
    ".MC": "EURONEXT",  # Madrid
    ".HE": "EURONEXT",  # Helsinki
}

# Map yfinance ticker suffixes to IBKR exchange + currency for contract creation.
YF_SUFFIX_TO_IBKR: Dict[str, Dict[str, str]] = {
    ".T":  {"exchange": "TSE",      "currency": "JPY"},
    ".HK": {"exchange": "SEHK",     "currency": "HKD"},
    ".L":  {"exchange": "LSE",      "currency": "GBP"},  # Most are USD on LSEETF, but LSE for plain stocks
    ".DE": {"exchange": "IBIS",     "currency": "EUR"},
    ".PA": {"exchange": "SBF",      "currency": "EUR"},
    ".AS": {"exchange": "AEB",      "currency": "EUR"},
    ".SI": {"exchange": "SGX",      "currency": "SGD"},
    ".AX": {"exchange": "ASX",      "currency": "AUD"},
    ".KS": {"exchange": "KSE",      "currency": "KRW"},
    ".KQ": {"exchange": "KSE",      "currency": "KRW"},
    ".TW": {"exchange": "TWSE",     "currency": "TWD"},
    ".NS": {"exchange": "NSE",      "currency": "INR"},
    ".SA": {"exchange": "BOVESPA",  "currency": "BRL"},
}


def _get_active_exchanges(utc_hour: int) -> List[str]:
    """Return exchanges whose markets are currently open (by UTC hour)."""
    active = []
    for exchange, info in EXCHANGE_SESSIONS.items():
        for start_h, end_h in info["session_hours"]:
            if start_h <= utc_hour < end_h:
                active.append(exchange)
                break
    if not active:
        # Deep overnight: nothing open. Return empty -- don't waste snapshots.
        log.debug("No exchanges open at UTC hour %d", utc_hour)
    return active


def _day_fraction(exchange: str) -> float:
    """Compute fraction of trading day elapsed for the given exchange.

    Returns 0.0 before open, 1.0 after close, fractional during session.
    """
    now = datetime.now(timezone.utc)
    info = EXCHANGE_SESSIONS.get(exchange)
    if info is None:
        return 0.5  # Unknown exchange, assume midday

    open_h, open_m = info["open_utc"]
    close_h, close_m = info["close_utc"]
    total_minutes = info["total_minutes"]

    # Build open/close datetimes in UTC for today
    open_dt = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
    close_dt = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)

    # Handle exchanges that span midnight (e.g., TSE open=00:00 with Asian evening)
    if close_dt <= open_dt:
        # Session wraps midnight -- if we're before close, open was yesterday
        if now < close_dt:
            open_dt -= timedelta(days=1)

    if now <= open_dt:
        return 0.01  # Just opened / before open -- avoid division by zero
    if now >= close_dt:
        return 1.0   # After close

    elapsed_minutes = (now - open_dt).total_seconds() / 60.0
    fraction = elapsed_minutes / max(total_minutes, 1)
    return max(0.01, min(1.0, fraction))


def _yf_ticker_to_exchange(yf_ticker: str) -> Optional[str]:
    """Determine exchange from yfinance ticker suffix."""
    for suffix, exchange in YF_SUFFIX_TO_EXCHANGE.items():
        if yf_ticker.endswith(suffix):
            return exchange
    # No suffix = US stock
    if "." not in yf_ticker:
        return "US"
    return None


def _yf_ticker_to_ibkr_symbol(yf_ticker: str) -> str:
    """Convert yfinance ticker to IBKR symbol (strip suffixes)."""
    # Remove common suffixes for IBKR
    for suffix in YF_SUFFIX_TO_EXCHANGE:
        if yf_ticker.endswith(suffix):
            return yf_ticker[: -len(suffix)]
    return yf_ticker


# ---------------------------------------------------------------------------
# NDJSON logger (same pattern as ibkr_market_scanner.py)
# ---------------------------------------------------------------------------

class NDJSONLogger:
    """Append-only NDJSON log for scanner events."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **data,
        }
        try:
            line = json.dumps(record, default=str) + "\n"
            with self._lock:
                with open(self._path, "a") as f:
                    f.write(line)
        except Exception as e:
            log.debug("NDJSON log write failed: %s", e)


# ---------------------------------------------------------------------------
# Atomic JSON writer (same pattern as ibkr_market_scanner.py)
# ---------------------------------------------------------------------------

def _atomic_write_json(path: str, data: Any) -> None:
    """Write JSON atomically (write to temp, then rename)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(target.parent),
            prefix=".rvol_tmp_",
            suffix=".json",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path, str(target))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        log.error("Atomic write to %s failed: %s", path, e)


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

def _load_config() -> Dict[str, Any]:
    """Load RVOL scanner config from config.toml [scanner] with sane defaults."""
    defaults: Dict[str, Any] = {
        "client_id": DEFAULT_CLIENT_ID,
        "host": os.environ.get("IB_HOST", DEFAULT_IB_HOST),
        "port": int(os.environ.get("IB_PORT", str(DEFAULT_IB_PORT))),
        "daemon_interval_secs": DAEMON_INTERVAL_SECS,
        "snapshot_batch_size": SNAPSHOT_BATCH_SIZE,
        "min_avg_volume": MIN_AVG_VOLUME,
        "top_results_per_type": TOP_RESULTS_PER_TYPE,
        "dark_horse_min_rvol": 3.0,
        "dark_horse_min_gap_pct": 1.5,
        "results_file": str(DATA_DIR / RESULTS_FILE),
        "log_file": str(DATA_DIR / LOG_FILE),
        "volume_cache_file": str(DATA_DIR / VOLUME_CACHE_FILE),
        "reconnect_backoff_secs": [1, 2, 4, 8, 16, 32, 60],
        "connect_timeout_sec": CONNECT_TIMEOUT_SECS,
    }
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]

        cfg_path = CONFIG_DIR / "config.toml"
        if not cfg_path.exists():
            return defaults
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)

        # Pull scanner-level defaults
        scanner_cfg = data.get("scanner", {})
        if "dark_horse_min_rvol" in scanner_cfg:
            defaults["dark_horse_min_rvol"] = scanner_cfg["dark_horse_min_rvol"]
        if "dark_horse_min_gap_pct" in scanner_cfg:
            defaults["dark_horse_min_gap_pct"] = scanner_cfg["dark_horse_min_gap_pct"]

        # Resolve Docker-absolute paths when running locally
        for path_key in ("results_file", "log_file", "volume_cache_file"):
            raw_path = defaults.get(path_key, "")
            if raw_path.startswith("/app/data/") and not Path("/app/data").exists():
                basename = raw_path.replace("/app/data/", "")
                defaults[path_key] = str(DATA_DIR / basename)

        return defaults
    except Exception as e:
        log.warning("Failed to load config.toml: %s -- using defaults", e)
        return defaults


# ---------------------------------------------------------------------------
# Universe loader
# ---------------------------------------------------------------------------

def load_universe(universe_path: Optional[str] = None) -> Dict[str, List[str]]:
    """Load ticker universe from universe_10k.txt, grouped by exchange.

    Returns: {"US": ["AAPL", "NVDA", ...], "TSE": ["7203.T", ...], ...}
    All tickers in yfinance format.
    """
    if universe_path is None:
        # Try Docker path first, then local
        candidates = [
            Path("/app/config/universe_10k.txt"),
            CONFIG_DIR / "universe_10k.txt",
        ]
        for p in candidates:
            if p.exists():
                universe_path = str(p)
                break

    if universe_path is None or not Path(universe_path).exists():
        log.warning("universe_10k.txt not found -- falling back to contracts.toml")
        return _load_universe_from_contracts()

    exchange_tickers: Dict[str, List[str]] = {}
    try:
        with open(universe_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                exchange = _yf_ticker_to_exchange(line)
                if exchange is not None:
                    exchange_tickers.setdefault(exchange, []).append(line)

        total = sum(len(v) for v in exchange_tickers.values())
        log.info(
            "Loaded %d tickers from %s across %d exchanges",
            total, universe_path, len(exchange_tickers),
        )
        return exchange_tickers
    except Exception as e:
        log.error("Failed to load universe: %s", e)
        return _load_universe_from_contracts()


def _load_universe_from_contracts() -> Dict[str, List[str]]:
    """Fallback: load universe from contracts.toml via contract_loader."""
    try:
        from python_brain.ouroboros.contract_loader import load_yfinance_symbols
        symbols = load_yfinance_symbols()
        exchange_tickers: Dict[str, List[str]] = {}
        for sym in symbols:
            exchange = _yf_ticker_to_exchange(sym)
            if exchange is not None:
                exchange_tickers.setdefault(exchange, []).append(sym)
        return exchange_tickers
    except Exception as e:
        log.error("Failed to load contracts.toml fallback: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Volume cache
# ---------------------------------------------------------------------------

def load_volume_cache(cache_path: str) -> Dict[str, float]:
    """Load 20-day average daily volume cache.

    Returns: {"AAPL": 65000000.0, "7203.T": 4500000.0, ...}
    """
    path = Path(cache_path)
    if not path.exists():
        log.warning("Volume cache not found at %s", cache_path)
        return {}
    try:
        with open(path, "r") as f:
            data = json.load(f)
        log.info("Loaded volume cache: %d tickers", len(data))
        return {k: float(v) for k, v in data.items()}
    except Exception as e:
        log.error("Failed to load volume cache: %s", e)
        return {}


def build_volume_cache(
    universe: Dict[str, List[str]],
    output_path: str,
    lookback_days: int = 30,
    avg_window: int = 20,
) -> Dict[str, float]:
    """Build 20-day average daily volume cache from yfinance.

    Downloads last 30 days of daily data and computes the 20-day average.
    Writes to volume_cache.json atomically.

    Args:
        universe: Exchange -> list of yfinance tickers.
        output_path: Where to write volume_cache.json.
        lookback_days: How many days of history to download.
        avg_window: Rolling average window for volume.

    Returns: The computed cache dict.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance not installed -- cannot build volume cache")
        return {}

    all_tickers = []
    for tickers in universe.values():
        all_tickers.extend(tickers)

    log.info("Building volume cache for %d tickers (lookback=%dd, avg=%dd)...",
             len(all_tickers), lookback_days, avg_window)

    cache: Dict[str, float] = {}
    errors = 0
    processed = 0

    # Process in batches to avoid yfinance rate limits and memory issues
    for batch_start in range(0, len(all_tickers), YFINANCE_BATCH_SIZE):
        batch = all_tickers[batch_start:batch_start + YFINANCE_BATCH_SIZE]
        batch_str = " ".join(batch)

        try:
            data = yf.download(
                batch_str,
                period=f"{lookback_days}d",
                progress=False,
                threads=True,
                group_by="ticker",
            )

            if data.empty:
                errors += len(batch)
                continue

            for ticker in batch:
                try:
                    if len(batch) == 1:
                        vol_series = data.get("Volume")
                    else:
                        vol_series = data.get((ticker, "Volume"))

                    if vol_series is None or vol_series.empty:
                        errors += 1
                        continue

                    vol_series = vol_series.dropna()
                    if len(vol_series) < 5:
                        errors += 1
                        continue

                    # Use the last avg_window days
                    recent = vol_series.tail(avg_window)
                    avg_vol = float(recent.mean())

                    if avg_vol > 0:
                        cache[ticker] = round(avg_vol, 0)

                except Exception:
                    errors += 1

            processed += len(batch)
            if processed % 500 == 0:
                log.info("  ... processed %d/%d tickers (%d cached, %d errors)",
                         processed, len(all_tickers), len(cache), errors)

        except Exception as e:
            log.warning("yfinance batch download failed: %s", e)
            errors += len(batch)

        # Brief pause between batches to be respectful to yfinance
        time.sleep(0.5)

    log.info(
        "Volume cache built: %d tickers cached, %d errors out of %d total",
        len(cache), errors, len(all_tickers),
    )

    # Write atomically
    _atomic_write_json(output_path, cache)
    log.info("Volume cache written to %s", output_path)
    return cache


# ---------------------------------------------------------------------------
# IBKR Snapshot Connection
# ---------------------------------------------------------------------------

class IBKRSnapshotConnection:
    """Manages IBKR connection for snapshot market data requests.

    Uses ib_insync with reqMktData(snapshot=True) for one-shot price/volume reads.
    """

    def __init__(self, host: str, port: int, client_id: int, timeout: int = 15):
        self._host = host
        self._port = port
        self._client_id = client_id
        self._timeout = timeout
        self._ib: Any = None
        self._connected = False
        self._lock = threading.Lock()
        self._reconnect_count = 0

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        """Connect to IB Gateway. Returns True on success."""
        with self._lock:
            if self._connected and self._ib is not None:
                try:
                    if self._ib.isConnected():
                        return True
                except Exception:
                    self._connected = False
                    self._ib = None

            try:
                from ib_insync import IB
                ib = IB()
                ib.connect(
                    self._host,
                    self._port,
                    clientId=self._client_id,
                    timeout=self._timeout,
                    readonly=True,
                )
                self._ib = ib
                self._connected = True
                self._reconnect_count += 1
                log.info(
                    "Connected to IB Gateway at %s:%d (client_id=%d, attempt=%d)",
                    self._host, self._port, self._client_id, self._reconnect_count,
                )
                return True
            except ImportError:
                log.error("ib_insync not installed -- cannot connect to IBKR")
                return False
            except Exception as e:
                log.warning("Connection failed: %s", e)
                return False

    def disconnect(self) -> None:
        """Disconnect from IB Gateway."""
        with self._lock:
            if self._ib is not None:
                try:
                    self._ib.disconnect()
                except Exception:
                    pass
                self._ib = None
            self._connected = False

    def is_healthy(self) -> bool:
        """Check if connection is alive."""
        if not self._connected or self._ib is None:
            return False
        try:
            return self._ib.isConnected()
        except Exception:
            return False

    def snapshot_batch(
        self,
        tickers: List[str],
        exchange_hint: str,
    ) -> List[Dict[str, Any]]:
        """Snapshot a batch of tickers via reqMktData(snapshot=True).

        Batches 50 requests, waits ~1s, then next 50 to stay within
        IBKR's 50 requests/sec rate limit.

        Args:
            tickers: List of yfinance-format tickers to snapshot.
            exchange_hint: Exchange name for contract construction.

        Returns: List of snapshot results (one per ticker that resolved).
        """
        if not self._connected or self._ib is None:
            return []

        from ib_insync import Stock, Contract

        results: List[Dict[str, Any]] = []
        pending: List[Tuple[str, Any]] = []  # (yf_ticker, ticker_obj)

        for batch_start in range(0, len(tickers), SNAPSHOT_BATCH_SIZE):
            batch = tickers[batch_start:batch_start + SNAPSHOT_BATCH_SIZE]
            batch_tickers: List[Tuple[str, Any]] = []

            for yf_ticker in batch:
                try:
                    contract = self._make_contract(yf_ticker, exchange_hint)
                    if contract is None:
                        continue
                    # Request snapshot -- non-blocking, returns Ticker object
                    ticker_obj = self._ib.reqMktData(contract, snapshot=True)
                    batch_tickers.append((yf_ticker, ticker_obj))
                except Exception as e:
                    log.debug("Snapshot request failed for %s: %s", yf_ticker, e)

            # Wait for IBKR to fill the snapshots
            if batch_tickers:
                self._ib.sleep(SNAPSHOT_BATCH_DELAY_SECS)

            # Harvest results
            for yf_ticker, ticker_obj in batch_tickers:
                try:
                    result = self._extract_snapshot(yf_ticker, ticker_obj, exchange_hint)
                    if result is not None:
                        results.append(result)
                except Exception as e:
                    log.debug("Snapshot extract failed for %s: %s", yf_ticker, e)
                finally:
                    # Cancel the market data subscription to free resources
                    try:
                        self._ib.cancelMktData(ticker_obj.contract)
                    except Exception:
                        pass

        return results

    def _make_contract(self, yf_ticker: str, exchange_hint: str) -> Any:
        """Create an IBKR Stock contract from a yfinance ticker."""
        from ib_insync import Stock

        ibkr_symbol = _yf_ticker_to_ibkr_symbol(yf_ticker)

        # Determine exchange and currency from suffix
        for suffix, ibkr_info in YF_SUFFIX_TO_IBKR.items():
            if yf_ticker.endswith(suffix):
                return Stock(
                    symbol=ibkr_symbol,
                    exchange=ibkr_info["exchange"],
                    currency=ibkr_info["currency"],
                )

        # No suffix = US stock
        if "." not in yf_ticker:
            return Stock(symbol=ibkr_symbol, exchange="SMART", currency="USD")

        # LSE ETPs ending in .L might be on LSEETF with USD
        if yf_ticker.endswith(".L"):
            return Stock(symbol=ibkr_symbol, exchange="LSEETF", currency="USD")

        return None

    def _extract_snapshot(
        self,
        yf_ticker: str,
        ticker_obj: Any,
        exchange_hint: str,
    ) -> Optional[Dict[str, Any]]:
        """Extract snapshot data from a filled Ticker object.

        Returns None if the snapshot didn't resolve (delisted, wrong symbol, etc.).
        """
        # Check if we got valid data
        last = getattr(ticker_obj, "last", None)
        if last is None or (isinstance(last, float) and math.isnan(last)):
            last = getattr(ticker_obj, "close", None)
        if last is None or (isinstance(last, float) and math.isnan(last)):
            return None

        volume = getattr(ticker_obj, "volume", None)
        if volume is None or (isinstance(volume, float) and math.isnan(volume)):
            volume = 0

        close = getattr(ticker_obj, "close", None)
        if close is None or (isinstance(close, float) and math.isnan(close)):
            close = last

        open_price = getattr(ticker_obj, "open", None)
        if open_price is None or (isinstance(open_price, float) and math.isnan(open_price)):
            open_price = close

        high = getattr(ticker_obj, "high", None)
        if high is None or (isinstance(high, float) and math.isnan(high)):
            high = last

        low = getattr(ticker_obj, "low", None)
        if low is None or (isinstance(low, float) and math.isnan(low)):
            low = last

        bid = getattr(ticker_obj, "bid", None)
        ask = getattr(ticker_obj, "ask", None)
        spread = 0.0
        if (bid is not None and ask is not None
                and not math.isnan(bid) and not math.isnan(ask)
                and bid > 0 and ask > 0):
            spread = (ask - bid) / ((ask + bid) / 2) * 100  # Spread as % of mid

        return {
            "symbol": yf_ticker,
            "last": float(last),
            "volume": int(volume),
            "close": float(close),      # Previous close
            "open": float(open_price),
            "high": float(high),
            "low": float(low),
            "spread_pct": round(spread, 4),
            "exchange": exchange_hint,
        }


# ---------------------------------------------------------------------------
# RVOL computation and scoring
# ---------------------------------------------------------------------------

def compute_rvol(
    snapshot: Dict[str, Any],
    avg_volume: float,
    exchange: str,
) -> float:
    """Compute RVOL = today_volume / (avg_daily_volume * day_fraction).

    Returns 0.0 if data is insufficient.
    """
    today_volume = snapshot.get("volume", 0)
    if today_volume <= 0 or avg_volume <= 0:
        return 0.0

    fraction = _day_fraction(exchange)
    expected = avg_volume * fraction
    if expected <= 0:
        return 0.0

    return today_volume / expected


def compute_metrics(
    snapshot: Dict[str, Any],
    avg_volume: float,
    exchange: str,
) -> Dict[str, Any]:
    """Compute all derived metrics for a single ticker snapshot.

    Returns the snapshot dict augmented with: rvol, gap_pct, change_pct,
    spread_pct, day_fraction, near_low_pct, near_high_pct.
    """
    rvol = compute_rvol(snapshot, avg_volume, exchange)

    close = snapshot.get("close", 0.0)
    last = snapshot.get("last", 0.0)
    open_price = snapshot.get("open", 0.0)
    high = snapshot.get("high", 0.0)
    low = snapshot.get("low", 0.0)

    # Gap% = (open - prev_close) / prev_close * 100
    gap_pct = 0.0
    if close > 0:
        gap_pct = ((open_price - close) / close) * 100

    # Change% = (last - open) / open * 100 (intraday move from open)
    change_pct = 0.0
    if open_price > 0:
        change_pct = ((last - open_price) / open_price) * 100

    # Total change from prev close
    total_change_pct = 0.0
    if close > 0:
        total_change_pct = ((last - close) / close) * 100

    # Near-low: how close is price to today's low (0 = at low, 100 = at high)
    near_low_pct = 50.0
    day_range = high - low
    if day_range > 0:
        near_low_pct = ((last - low) / day_range) * 100

    # Near-high: inverse
    near_high_pct = 100.0 - near_low_pct

    return {
        **snapshot,
        "rvol": round(rvol, 2),
        "gap_pct": round(gap_pct, 2),
        "change_pct": round(change_pct, 2),
        "total_change_pct": round(total_change_pct, 2),
        "near_low_pct": round(near_low_pct, 2),
        "near_high_pct": round(near_high_pct, 2),
        "avg_volume": round(avg_volume, 0),
        "day_fraction": round(_day_fraction(exchange), 3),
    }


# ---------------------------------------------------------------------------
# Multi-type ranking
# ---------------------------------------------------------------------------

def _spread_tightness(spread_pct: float) -> float:
    """Convert spread% to a 0-1 tightness score. Tighter = higher."""
    # 0% spread -> 1.0, 1% spread -> 0.5, 2%+ spread -> ~0.2
    if spread_pct <= 0:
        return 1.0
    return 1.0 / (1.0 + spread_pct * 2.0)


def _volume_rank_score(rvol: float) -> float:
    """Normalize RVOL to a 0-1 score. Diminishing returns above 10x."""
    if rvol <= 0:
        return 0.0
    # Log-scale: RVOL 1 -> 0.0, RVOL 3 -> 0.48, RVOL 10 -> 1.0
    return min(1.0, math.log10(max(rvol, 0.1)) / math.log10(10))


def _abs_change_score(change_pct: float) -> float:
    """Absolute change normalized to 0-1 score."""
    abs_change = abs(change_pct)
    # 0% -> 0.0, 3% -> 0.5, 10%+ -> 1.0
    return min(1.0, abs_change / 10.0)


def _negative_change_score(change_pct: float) -> float:
    """Score for negative change (bigger drop = higher score). 0 for positive."""
    if change_pct >= 0:
        return 0.0
    return min(1.0, abs(change_pct) / 10.0)


def _positive_change_score(change_pct: float) -> float:
    """Score for positive change (bigger gain = higher score). 0 for negative."""
    if change_pct <= 0:
        return 0.0
    return min(1.0, change_pct / 10.0)


def _near_low_score(near_low_pct: float) -> float:
    """Score for proximity to daily low. 0% (at low) -> 1.0, 50% -> 0.5."""
    return max(0.0, 1.0 - (near_low_pct / 100.0))


def _price_down_vol_up_score(change_pct: float, rvol: float) -> float:
    """Score for price down + volume up divergence."""
    if change_pct >= 0 or rvol < 1.0:
        return 0.0
    # More negative change + higher RVOL = higher score
    down_score = min(1.0, abs(change_pct) / 5.0)
    vol_score = min(1.0, (rvol - 1.0) / 5.0)
    return down_score * vol_score


def score_ticker(metrics: Dict[str, Any]) -> Dict[str, float]:
    """Compute composite scores for each entry type.

    Returns: {"TypeB": 0.85, "TypeE": 0.42, "TypeC": 0.65, "TypeF": 0.30, "TypeA": 0.71}
    """
    rvol = metrics.get("rvol", 0.0)
    change_pct = metrics.get("total_change_pct", 0.0)
    near_low = metrics.get("near_low_pct", 50.0)
    spread = metrics.get("spread_pct", 1.0)

    vol_score = _volume_rank_score(rvol)
    tight = _spread_tightness(spread)

    # TypeB: Volume anomaly (RVOL spikes) -- primary dark horse type
    # RVOL 60%, absolute change 20%, spread tightness 20%
    type_b = (vol_score * 0.6) + (_abs_change_score(change_pct) * 0.2) + (tight * 0.2)

    # TypeE: Near daily low (IBS mean reversion)
    # Near-low 50%, RVOL 30%, spread tightness 20%
    type_e = (_near_low_score(near_low) * 0.5) + (vol_score * 0.3) + (tight * 0.2)

    # TypeC: Oversold bounce (biggest fallers with volume)
    # Negative change 50%, RVOL 30%, spread tightness 20%
    type_c = (_negative_change_score(change_pct) * 0.5) + (vol_score * 0.3) + (tight * 0.2)

    # TypeF: OBV divergence (price down, volume up)
    # Price-down-vol-up 60%, RVOL 40%
    type_f = (_price_down_vol_up_score(change_pct, rvol) * 0.6) + (vol_score * 0.4)

    # TypeA: Momentum breakout (biggest gainers with volume)
    # Positive change 50%, RVOL 30%, spread tightness 20%
    type_a = (_positive_change_score(change_pct) * 0.5) + (vol_score * 0.3) + (tight * 0.2)

    return {
        "TypeB": round(type_b, 4),
        "TypeE": round(type_e, 4),
        "TypeC": round(type_c, 4),
        "TypeF": round(type_f, 4),
        "TypeA": round(type_a, 4),
    }


def rank_results(
    all_metrics: List[Dict[str, Any]],
    top_n: int = TOP_RESULTS_PER_TYPE,
) -> Dict[str, Dict[str, Any]]:
    """Rank all scanned tickers by each entry type score.

    Returns scanner output dict matching ibkr_market_scanner.py format:
    {
        "TypeB_RVOL": {"results": [...top_n...], "entry_type": "TypeB", ...},
        "TypeE_NearLow": {"results": [...]},
        ...
    }
    """
    scanner_configs = [
        ("TypeB_RVOL", "TypeB", "Volume anomaly candidates -- RVOL spikes"),
        ("TypeE_NearLow", "TypeE", "IBS mean reversion candidates -- near daily low"),
        ("TypeC_Oversold", "TypeC", "Oversold bounce candidates -- biggest fallers"),
        ("TypeF_Divergence", "TypeF", "OBV divergence candidates -- price down, vol up"),
        ("TypeA_Momentum", "TypeA", "Momentum breakout candidates -- biggest gainers"),
    ]

    scanners: Dict[str, Dict[str, Any]] = {}

    for scanner_name, entry_type, description in scanner_configs:
        # Score and sort
        scored = []
        for m in all_metrics:
            scores = score_ticker(m)
            score = scores.get(entry_type, 0.0)
            if score <= 0.01:
                continue  # Skip near-zero scores
            scored.append({
                "symbol": m["symbol"],
                "rank": 0,  # Will be set after sorting
                "score": score,
                "rvol": m.get("rvol", 0.0),
                "volume": m.get("volume", 0),
                "change_pct": m.get("total_change_pct", 0.0),
                "gap_pct": m.get("gap_pct", 0.0),
                "spread_pct": m.get("spread_pct", 0.0),
                "last": m.get("last", 0.0),
                "near_low_pct": m.get("near_low_pct", 50.0),
                "avg_volume": m.get("avg_volume", 0),
                "exchange": m.get("exchange", ""),
                "day_fraction": m.get("day_fraction", 0.0),
            })

        # Sort by score descending
        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:top_n]

        # Assign ranks
        for i, item in enumerate(top, start=1):
            item["rank"] = i

        if top:
            scanners[scanner_name] = {
                "entry_type": entry_type,
                "description": description,
                "result_count": len(top),
                "results": top,
            }

    return scanners


# ---------------------------------------------------------------------------
# Simulation mode -- mock snapshot data
# ---------------------------------------------------------------------------

_SIM_TICKERS: Dict[str, List[str]] = {
    "US": [
        "NVDA", "SMCI", "AAPL", "TSLA", "MSFT", "AMD", "GOOG", "AMZN",
        "META", "NFLX", "COIN", "PLTR", "ARM", "MRVL", "MU", "AVGO",
        "CRM", "INTC", "QCOM", "AMAT", "LRCX", "KLAC", "TXN", "ASML",
    ],
    "TSE": [
        "7203.T", "6758.T", "8306.T", "6861.T", "6902.T", "8035.T", "6954.T",
        "9984.T", "7267.T", "4502.T", "9432.T", "6501.T", "8766.T", "8591.T",
    ],
    "HKEX": [
        "9988.HK", "0700.HK", "1211.HK", "0001.HK", "0388.HK", "2318.HK",
        "1398.HK", "0941.HK", "0883.HK", "0005.HK", "9618.HK", "9999.HK",
    ],
    "LSE": [
        "QQQ3.L", "3LUS.L", "TSL3.L", "NVD3.L", "5SPY.L", "3LNV.L", "3LAP.L",
        "3LMS.L", "3LAM.L", "GPT3.L", "3SEM.L", "3LMT.L", "QQQ5.L", "AMD3.L",
    ],
    "XETRA": [
        "SAP.DE", "SIE.DE", "MBG.DE", "BMW.DE", "ADS.DE", "DTE.DE", "EOAN.DE",
        "MUV2.DE", "ALV.DE", "BEI.DE", "VOW3.DE", "HEN3.DE", "RWE.DE", "DBK.DE",
    ],
    "EURONEXT": [
        "MC.PA", "OR.PA", "TTE.PA", "SAN.PA", "BNP.PA", "AI.PA", "SU.PA",
        "DG.PA", "AIR.PA", "CS.PA", "BN.PA", "RI.PA",
    ],
    "SGX": [
        "D05.SI", "O39.SI", "Z74.SI", "U11.SI", "BN4.SI", "C6L.SI",
        "G13.SI", "S58.SI", "V03.SI", "A17U.SI",
    ],
}


def _generate_sim_snapshot(yf_ticker: str, exchange: str) -> Dict[str, Any]:
    """Generate a realistic mock snapshot for simulation mode."""
    # Base price varies by exchange
    if exchange == "TSE":
        base_price = random.uniform(500, 50000)  # JPY
    elif exchange == "HKEX":
        base_price = random.uniform(5, 500)  # HKD
    else:
        base_price = random.uniform(10, 500)  # USD/GBP/EUR

    close = round(base_price, 2)
    gap = random.gauss(0, 2)  # % gap
    open_price = round(close * (1 + gap / 100), 2)
    change = random.gauss(0, 3)  # % intraday change
    last = round(open_price * (1 + change / 100), 2)
    high = round(max(open_price, last) * (1 + abs(random.gauss(0, 0.5)) / 100), 2)
    low = round(min(open_price, last) * (1 - abs(random.gauss(0, 0.5)) / 100), 2)
    volume = int(random.lognormvariate(14, 2))  # Log-normal volume
    spread = round(random.uniform(0.01, 0.5), 4)

    return {
        "symbol": yf_ticker,
        "last": last,
        "volume": volume,
        "close": close,
        "open": open_price,
        "high": high,
        "low": low,
        "spread_pct": spread,
        "exchange": exchange,
    }


def _generate_sim_volume_cache(tickers: List[str]) -> Dict[str, float]:
    """Generate mock volume cache for simulation mode."""
    cache = {}
    for t in tickers:
        cache[t] = float(int(random.lognormvariate(14, 2)))
    return cache


# ---------------------------------------------------------------------------
# Main Scanner Engine
# ---------------------------------------------------------------------------

class RVOLScanner:
    """RVOL snapshot scanner engine.

    Lifecycle:
      1. Load volume cache (or build from yfinance if missing).
      2. Load ticker universe from universe_10k.txt.
      3. Determine which exchanges are open.
      4. Connect to IB Gateway (client_id=105).
      5. Snapshot all tickers on open exchanges in batches of 50.
      6. Compute RVOL and entry-type scores for each ticker.
      7. Rank and output top 40 per type to scanner_results.json.
      8. Log all events to NDJSON for Ouroboros.
      9. Sleep 15 min, repeat from step 3.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        simulation_mode: bool = False,
    ):
        self._config = config
        self._sim_mode = simulation_mode
        self._conn: Optional[IBKRSnapshotConnection] = None
        self._ndjson = NDJSONLogger(config.get("log_file", str(DATA_DIR / LOG_FILE)))
        self._results_path = config.get("results_file", str(DATA_DIR / RESULTS_FILE))
        self._cache_path = config.get("volume_cache_file", str(DATA_DIR / VOLUME_CACHE_FILE))
        self._daemon_interval = config.get("daemon_interval_secs", DAEMON_INTERVAL_SECS)
        self._top_n = config.get("top_results_per_type", TOP_RESULTS_PER_TYPE)
        self._min_avg_volume = config.get("min_avg_volume", MIN_AVG_VOLUME)
        self._backoff_schedule = config.get("reconnect_backoff_secs", [1, 2, 4, 8, 16, 32, 60])
        self._running = False
        self._scan_count = 0
        self._consecutive_failures = 0

        # Loaded lazily
        self._universe: Optional[Dict[str, List[str]]] = None
        self._volume_cache: Optional[Dict[str, float]] = None

    def _load_universe(self) -> Dict[str, List[str]]:
        """Load and cache the ticker universe."""
        if self._universe is None:
            self._universe = load_universe()
        return self._universe

    def _load_volume_cache(self) -> Dict[str, float]:
        """Load and cache the volume cache. Build from yfinance if missing."""
        if self._volume_cache is None:
            self._volume_cache = load_volume_cache(self._cache_path)
            if not self._volume_cache:
                log.warning("Volume cache empty -- building from yfinance (this may take a while)...")
                universe = self._load_universe()
                self._volume_cache = build_volume_cache(universe, self._cache_path)
        return self._volume_cache

    def _connect(self) -> bool:
        """Establish IBKR connection."""
        if self._sim_mode:
            log.info("SIMULATION_MODE -- no IBKR connection needed")
            return True

        if self._conn is None:
            self._conn = IBKRSnapshotConnection(
                host=self._config.get("host", DEFAULT_IB_HOST),
                port=self._config.get("port", DEFAULT_IB_PORT),
                client_id=self._config.get("client_id", DEFAULT_CLIENT_ID),
                timeout=self._config.get("connect_timeout_sec", CONNECT_TIMEOUT_SECS),
            )
        return self._conn.connect()

    def _disconnect(self) -> None:
        if self._conn is not None:
            self._conn.disconnect()

    def _reconnect_with_backoff(self) -> bool:
        """Reconnect with exponential backoff."""
        for attempt, delay in enumerate(self._backoff_schedule):
            log.info("Reconnect attempt %d (backoff=%ds)...", attempt + 1, delay)
            time.sleep(delay)
            if self._connect():
                self._consecutive_failures = 0
                self._ndjson.log_event("reconnected", {"attempt": attempt + 1})
                return True
        log.error("All reconnect attempts exhausted (%d)", len(self._backoff_schedule))
        return False

    def _get_scan_tickers(
        self,
        active_exchanges: List[str],
    ) -> Dict[str, List[str]]:
        """Get tickers to scan, filtered by open exchanges and minimum volume.

        Returns: {exchange: [yf_tickers...]} only for active exchanges,
        filtered to tickers with avg_volume > min_avg_volume from the cache.
        """
        universe = self._load_universe()
        vol_cache = self._load_volume_cache()

        filtered: Dict[str, List[str]] = {}
        for exchange in active_exchanges:
            tickers = universe.get(exchange, [])
            if not tickers:
                continue

            # Filter by minimum average volume
            qualified = []
            for t in tickers:
                avg_vol = vol_cache.get(t, 0.0)
                if avg_vol >= self._min_avg_volume:
                    qualified.append(t)

            if qualified:
                filtered[exchange] = qualified

        return filtered

    def _run_scan_cycle(self) -> Dict[str, Any]:
        """Execute one full scan cycle across all active exchanges."""
        now_utc = datetime.now(timezone.utc)
        utc_hour = now_utc.hour
        active_exchanges = _get_active_exchanges(utc_hour)

        if not active_exchanges:
            log.info("No exchanges open at UTC hour %d -- skipping scan", utc_hour)
            return {
                "timestamp": now_utc.isoformat(),
                "scan_type": "rvol_snapshot",
                "scan_cycle": self._scan_count + 1,
                "tickers_scanned": 0,
                "scan_duration_secs": 0,
                "exchanges_scanned": [],
                "scanners": {},
                "mode": "simulation" if self._sim_mode else "live",
                "skipped_reason": "no_exchanges_open",
            }

        scan_tickers = self._get_scan_tickers(active_exchanges)
        total_tickers = sum(len(v) for v in scan_tickers.values())

        log.info(
            "Scan cycle #%d -- UTC hour=%d, exchanges=%s, tickers=%d",
            self._scan_count + 1, utc_hour, list(scan_tickers.keys()), total_tickers,
        )

        cycle_start = time.monotonic()
        vol_cache = self._load_volume_cache()
        all_metrics: List[Dict[str, Any]] = []

        for exchange, tickers in scan_tickers.items():
            log.info("  Scanning %s: %d tickers...", exchange, len(tickers))
            exchange_start = time.monotonic()

            if self._sim_mode:
                snapshots = [_generate_sim_snapshot(t, exchange) for t in tickers]
            else:
                snapshots = self._conn.snapshot_batch(tickers, exchange)  # type: ignore[union-attr]

            # Compute metrics for each snapshot
            for snap in snapshots:
                yf_ticker = snap["symbol"]
                avg_vol = vol_cache.get(yf_ticker, 0.0)
                if avg_vol <= 0:
                    # If not in cache, skip RVOL computation but still include
                    # with rvol=0 (won't rank high but won't be lost)
                    avg_vol = snap.get("volume", 1)  # Use today's volume as rough proxy

                metrics = compute_metrics(snap, avg_vol, exchange)
                all_metrics.append(metrics)

            exchange_elapsed = time.monotonic() - exchange_start
            log.info(
                "  %s: %d snapshots in %.1fs (%.0f tickers/sec)",
                exchange, len(snapshots), exchange_elapsed,
                len(snapshots) / max(exchange_elapsed, 0.001),
            )

        # Rank across all exchanges by entry type
        scanners = rank_results(all_metrics, self._top_n)

        scan_duration = time.monotonic() - cycle_start
        self._scan_count += 1

        output = {
            "timestamp": now_utc.isoformat(),
            "scan_type": "rvol_snapshot",
            "scan_cycle": self._scan_count,
            "tickers_scanned": total_tickers,
            "tickers_resolved": len(all_metrics),
            "scan_duration_secs": round(scan_duration, 1),
            "exchanges_scanned": list(scan_tickers.keys()),
            "scanners": scanners,
            "mode": "simulation" if self._sim_mode else "live",
        }

        return output

    def scan_once(self) -> Dict[str, Any]:
        """Run a single scan cycle. Used for --scan and --test modes."""
        if not self._connect():
            if not self._sim_mode:
                log.warning("Cannot connect to IBKR -- falling back to simulation mode")
                self._sim_mode = True
        results = self._run_scan_cycle()
        self._disconnect()
        return results

    def scan_test(self, n_tickers: int = 10) -> Dict[str, Any]:
        """Run a quick test scan with a small number of tickers."""
        if not self._connect():
            if not self._sim_mode:
                log.warning("Cannot connect to IBKR -- using simulation mode for test")
                self._sim_mode = True

        # Use a tiny universe for testing
        now_utc = datetime.now(timezone.utc)
        active_exchanges = _get_active_exchanges(now_utc.hour)
        if not active_exchanges:
            active_exchanges = ["US"]  # Default for test

        vol_cache = self._load_volume_cache()
        all_metrics: List[Dict[str, Any]] = []
        tickers_scanned = 0

        for exchange in active_exchanges[:2]:  # Max 2 exchanges for test
            test_tickers = _SIM_TICKERS.get(exchange, [])[:n_tickers]
            if not test_tickers:
                continue

            if self._sim_mode:
                snapshots = [_generate_sim_snapshot(t, exchange) for t in test_tickers]
            else:
                snapshots = self._conn.snapshot_batch(test_tickers, exchange)  # type: ignore[union-attr]

            for snap in snapshots:
                avg_vol = vol_cache.get(snap["symbol"], 1_000_000.0)
                metrics = compute_metrics(snap, avg_vol, exchange)
                all_metrics.append(metrics)

            tickers_scanned += len(test_tickers)

        scanners = rank_results(all_metrics, n_tickers)

        self._disconnect()
        return {
            "timestamp": now_utc.isoformat(),
            "scan_type": "rvol_snapshot_test",
            "tickers_scanned": tickers_scanned,
            "tickers_resolved": len(all_metrics),
            "exchanges_scanned": active_exchanges[:2],
            "scanners": scanners,
            "mode": "simulation" if self._sim_mode else "live",
        }

    def run_forever(self) -> None:
        """Main daemon loop. Runs until SIGINT/SIGTERM."""
        self._running = True
        log.info("=" * 70)
        log.info("RVOL Scanner STARTING (mode=%s, interval=%ds)",
                 "simulation" if self._sim_mode else "live", self._daemon_interval)
        log.info("  Results: %s", self._results_path)
        log.info("  Volume cache: %s", self._cache_path)
        log.info("  Log: %s", self._config.get("log_file", ""))
        log.info("  Client ID: %d", self._config.get("client_id", DEFAULT_CLIENT_ID))
        log.info("  Min avg volume: %s", f"{self._min_avg_volume:,.0f}")
        log.info("=" * 70)

        self._ndjson.log_event("started", {
            "mode": "simulation" if self._sim_mode else "live",
            "interval_secs": self._daemon_interval,
            "client_id": self._config.get("client_id", DEFAULT_CLIENT_ID),
        })

        # Initial connection
        if not self._connect():
            if not self._sim_mode:
                log.warning("Initial IBKR connect failed -- will retry on next cycle")

        while self._running:
            try:
                cycle_start = time.monotonic()

                # Check connection health
                if not self._sim_mode:
                    if self._conn is None or not self._conn.is_healthy():
                        log.warning("Connection unhealthy -- attempting reconnect")
                        self._disconnect()
                        if not self._reconnect_with_backoff():
                            log.error(
                                "Reconnect failed -- waiting %ds before next attempt",
                                self._daemon_interval,
                            )
                            self._ndjson.log_event("reconnect_failed", {
                                "consecutive_failures": self._consecutive_failures,
                            })
                            self._consecutive_failures += 1
                            time.sleep(self._daemon_interval)
                            continue

                # Run scan cycle
                results = self._run_scan_cycle()

                # Write results atomically
                _atomic_write_json(self._results_path, results)

                # Log scan event
                total_results = sum(
                    s.get("result_count", 0)
                    for s in results.get("scanners", {}).values()
                )
                self._ndjson.log_event("scan_complete", {
                    "cycle": self._scan_count,
                    "exchanges_scanned": results.get("exchanges_scanned", []),
                    "tickers_scanned": results.get("tickers_scanned", 0),
                    "tickers_resolved": results.get("tickers_resolved", 0),
                    "scanner_count": len(results.get("scanners", {})),
                    "total_top_results": total_results,
                    "elapsed_secs": round(time.monotonic() - cycle_start, 2),
                })

                self._consecutive_failures = 0

                # Sleep until next cycle
                elapsed = time.monotonic() - cycle_start
                sleep_time = max(0, self._daemon_interval - elapsed)
                log.info(
                    "Scan cycle #%d complete (%d tickers, %d top results). Next in %ds.",
                    self._scan_count,
                    results.get("tickers_resolved", 0),
                    total_results,
                    int(sleep_time),
                )

                # Interruptible sleep
                sleep_end = time.monotonic() + sleep_time
                while self._running and time.monotonic() < sleep_end:
                    time.sleep(min(1.0, sleep_end - time.monotonic()))

            except Exception as e:
                self._consecutive_failures += 1
                log.error(
                    "Scan cycle error (failure #%d): %s",
                    self._consecutive_failures, e, exc_info=True,
                )
                self._ndjson.log_event("scan_error", {
                    "error": str(e),
                    "consecutive_failures": self._consecutive_failures,
                })
                backoff = min(60 * self._consecutive_failures, 300)
                time.sleep(backoff)

        # Shutdown
        log.info("Scanner shutdown -- %d cycles completed", self._scan_count)
        self._ndjson.log_event("stopped", {"total_cycles": self._scan_count})
        self._disconnect()

    def stop(self) -> None:
        """Signal the daemon to stop gracefully."""
        self._running = False


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------

_scanner_instance: Optional[RVOLScanner] = None


def _signal_handler(signum: int, frame: Any) -> None:
    sig_name = signal.Signals(signum).name
    log.info("Received %s -- shutting down RVOL scanner", sig_name)
    if _scanner_instance is not None:
        _scanner_instance.stop()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    global _scanner_instance

    parser = argparse.ArgumentParser(
        description="RVOL Scanner -- full-exchange volume anomaly detection via IBKR snapshots",
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--scan", action="store_true",
        help="Run one full scan cycle and exit",
    )
    mode_group.add_argument(
        "--daemon", action="store_true",
        help="Run continuously every 15 minutes",
    )
    mode_group.add_argument(
        "--build-cache", action="store_true",
        help="Build volume cache from yfinance (run nightly)",
    )
    mode_group.add_argument(
        "--test", action="store_true",
        help="Quick test with ~10 tickers per exchange",
    )
    parser.add_argument(
        "--sim", action="store_true",
        help="Simulation mode -- generate mock data without IBKR connection",
    )
    parser.add_argument(
        "--client-id", type=int, default=None,
        help="Override IBKR client ID (default: 105)",
    )
    parser.add_argument(
        "--interval", type=int, default=None,
        help="Override daemon interval in seconds (default: 900 = 15 min)",
    )
    parser.add_argument(
        "--min-volume", type=int, default=None,
        help="Override minimum average volume filter (default: 500000)",
    )
    args = parser.parse_args()

    # Load config
    config = _load_config()

    # Apply CLI overrides
    if args.client_id is not None:
        config["client_id"] = args.client_id
    if args.interval is not None:
        config["daemon_interval_secs"] = args.interval
    if args.min_volume is not None:
        config["min_avg_volume"] = args.min_volume

    sim_mode = args.sim or os.environ.get("AEGIS_RVOL_SIM", "0") == "1"

    # --- Build cache mode ---
    if args.build_cache:
        log.info("Building volume cache from yfinance...")
        universe = load_universe()
        cache_path = config.get("volume_cache_file", str(DATA_DIR / VOLUME_CACHE_FILE))
        cache = build_volume_cache(universe, cache_path)
        log.info("Done. %d tickers cached to %s", len(cache), cache_path)
        return 0

    # --- Test mode ---
    if args.test:
        scanner = RVOLScanner(config, simulation_mode=sim_mode or True)
        results = scanner.scan_test(n_tickers=10)
        print(json.dumps(results, indent=2, default=str))

        # Summary
        total = sum(
            s.get("result_count", 0)
            for s in results.get("scanners", {}).values()
        )
        print(f"\n--- {len(results.get('scanners', {}))} scanners, {total} total results ---")
        for name, data in results.get("scanners", {}).items():
            top3 = data.get("results", [])[:3]
            symbols = ", ".join(r["symbol"] for r in top3)
            print(f"  {name}: {data.get('result_count', 0)} results (top: {symbols})")
        return 0

    # --- Scan once mode ---
    if args.scan:
        scanner = RVOLScanner(config, simulation_mode=sim_mode)
        results = scanner.scan_once()

        # Write results
        _atomic_write_json(config.get("results_file", str(DATA_DIR / RESULTS_FILE)), results)
        print(json.dumps(results, indent=2, default=str))

        # Summary
        total = sum(
            s.get("result_count", 0)
            for s in results.get("scanners", {}).values()
        )
        print(f"\n--- Scanned {results.get('tickers_scanned', 0)} tickers, "
              f"resolved {results.get('tickers_resolved', 0)}, "
              f"{len(results.get('scanners', {}))} scanners, {total} top results ---")
        for name, data in results.get("scanners", {}).items():
            top3 = data.get("results", [])[:3]
            symbols = ", ".join(f"{r['symbol']}({r['rvol']:.1f}x)" for r in top3)
            print(f"  {name}: {data.get('result_count', 0)} results (top: {symbols})")
        return 0

    # --- Daemon mode ---
    if args.daemon:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        _scanner_instance = RVOLScanner(config, simulation_mode=sim_mode)
        _scanner_instance.run_forever()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
