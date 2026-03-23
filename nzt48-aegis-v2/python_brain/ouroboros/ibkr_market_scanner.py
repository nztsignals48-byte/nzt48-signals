"""IBKR Real-Time Market Scanner — Feeds dark horse streaming slots.

Connects to IB Gateway on port 4003 with client_id=103 (dedicated scanner
slot, separate from engine=101, analytics=102, weekly_scanner=102).

Uses reqScannerSubscription to scan ENTIRE exchanges in real-time,
discovering volume anomalies, momentum breakouts, oversold bounces, and
mean-reversion candidates across all open markets.

Architecture:
  - Runs as a long-lived daemon alongside the Rust engine.
  - Rotates scanner subscriptions across exchanges based on session.
  - Writes scanner_results.json atomically for the engine to consume.
  - Logs all activity to ibkr_market_scanner.ndjson for Ouroboros learning.

Usage:
  python3 -m python_brain.ouroboros.ibkr_market_scanner              # Production mode
  python3 -m python_brain.ouroboros.ibkr_market_scanner --test       # Print once and exit
  python3 -m python_brain.ouroboros.ibkr_market_scanner --sim        # Simulation mode (mock data)

Quarantine rules:
  - Read-only: never places orders, never modifies WAL or live state.
  - Uses client_id=103 to avoid conflict with engine (101) or analytics (102).
  - Graceful reconnection on IB Gateway restarts (daily 23:45 UTC).
  - All exceptions caught — never crashes the container.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import signal
import sys
import tempfile
import threading
import time
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
    format="%(asctime)s [IBKR-MktScanner] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ibkr_market_scanner")

# ---------------------------------------------------------------------------
# Configuration — loaded from config.toml [scanner.ibkr] with sane defaults
# ---------------------------------------------------------------------------

def _load_config() -> Dict[str, Any]:
    """Load scanner config from config.toml [scanner.ibkr] section."""
    defaults = {
        "enabled": True,
        "client_id": 103,
        "host": os.environ.get("IB_HOST", "aegis-ib-gateway"),
        "port": int(os.environ.get("IB_PORT", "4003")),
        "max_scanners": 10,
        "results_per_scanner": 50,
        "rotation_secs": 300,
        "results_file": str(DATA_DIR / "scanner_results.json"),
        "log_file": str(DATA_DIR / "ibkr_market_scanner.ndjson"),
        "reconnect_backoff_secs": [1, 2, 4, 8, 16, 32, 60],
        "connect_timeout_sec": 15,
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
        scanner_cfg = data.get("scanner", {}).get("ibkr", {})
        for key, val in scanner_cfg.items():
            if key in defaults:
                defaults[key] = val

        # Resolve Docker-absolute paths to local DATA_DIR when not in container.
        # config.toml uses /app/data/... which is the Docker mount point.
        # Outside Docker, rewrite to use the local DATA_DIR.
        for path_key in ("results_file", "log_file"):
            raw_path = defaults.get(path_key, "")
            if raw_path.startswith("/app/data/") and not Path("/app/data").exists():
                basename = raw_path.replace("/app/data/", "")
                defaults[path_key] = str(DATA_DIR / basename)

        return defaults
    except Exception as e:
        log.warning("Failed to load config.toml [scanner.ibkr]: %s — using defaults", e)
        return defaults


# ---------------------------------------------------------------------------
# Scanner definitions — 5 scanner types covering all entry strategies
# ---------------------------------------------------------------------------

SCANNER_CONFIGS: List[Dict[str, Any]] = [
    {
        "name": "TypeB_RVOL",
        "scan_code": "HOT_BY_VOLUME",
        "entry_type": "TypeB",
        "description": "Volume anomaly candidates -- RVOL spikes",
    },
    {
        "name": "TypeE_NearLow",
        "scan_code": "MOST_ACTIVE",
        "entry_type": "TypeE",
        "description": "IBS mean reversion candidates -- active near daily lows",
    },
    {
        "name": "TypeC_Oversold",
        "scan_code": "TOP_PERC_LOSE",
        "entry_type": "TypeC",
        "description": "Oversold bounce candidates -- biggest fallers with volume",
    },
    {
        "name": "TypeF_Divergence",
        "scan_code": "HOT_BY_VOLUME",
        "entry_type": "TypeF",
        "description": "OBV divergence candidates -- volume up while price down",
    },
    {
        "name": "TypeA_Momentum",
        "scan_code": "TOP_PERC_GAIN",
        "entry_type": "TypeA",
        "description": "Momentum breakout candidates -- biggest gainers with volume",
    },
]

# ---------------------------------------------------------------------------
# Exchange location codes and session mapping
# ---------------------------------------------------------------------------

# IBKR scanner location codes per exchange region.
# These are the literal strings IBKR's reqScannerSubscription accepts.
EXCHANGE_LOCATIONS: Dict[str, str] = {
    "US": "STK.US.MAJOR",       # NYSE + NASDAQ major listings
    "TSE": "STK.HK.TSE",        # Tokyo Stock Exchange (under HK region in IBKR)
    "HKEX": "STK.HK.SEHK",     # Hong Kong Stock Exchange
    "LSE": "STK.EU.LSE",        # London Stock Exchange (not LSEETF)
    "XETRA": "STK.EU.IBIS",    # Frankfurt/XETRA
    "EURONEXT": "STK.EU.SBF",  # Euronext Paris
    "SGX": "STK.HK.SGX",       # Singapore Exchange (under HK region in IBKR)
    "ASX": "STK.HK.ASX",       # Australian Securities Exchange
}

# Session windows (UTC hours). Markets that are open get scanner allocations.
# During overlap, both regions are scanned.
SESSION_WINDOWS: Dict[str, List[Tuple[int, int]]] = {
    # (start_hour_utc, end_hour_utc) — inclusive start, exclusive end
    "TSE":      [(0, 6)],         # 00:00-06:00 UTC (09:00-15:00 JST)
    "HKEX":     [(1, 8)],         # 01:30-08:00 UTC (09:30-16:00 HKT)
    "SGX":      [(1, 9)],         # 01:00-09:00 UTC (09:00-17:00 SGT)
    "LSE":      [(7, 17)],        # 07:00-16:30 UTC approx (08:00-16:30 London)
    "XETRA":    [(7, 17)],        # 07:00-17:30 UTC approx (08:00-17:30 Frankfurt)
    "EURONEXT": [(7, 17)],        # 07:00-17:30 UTC approx
    "US":       [(13, 21)],       # 13:30-21:00 UTC (09:30-16:00 ET)
}


def _get_active_exchanges(utc_hour: int) -> List[str]:
    """Return list of exchanges whose markets are currently open (by UTC hour)."""
    active = []
    for exchange, windows in SESSION_WINDOWS.items():
        for start_h, end_h in windows:
            if start_h <= utc_hour < end_h:
                active.append(exchange)
                break
    # If nothing is open (deep overnight), default to US pre-market + Asian
    if not active:
        active = ["US"]
    return active


# ---------------------------------------------------------------------------
# NDJSON logger for Ouroboros meta-learning
# ---------------------------------------------------------------------------

class NDJSONLogger:
    """Append-only NDJSON log for scanner events."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Append a single NDJSON event."""
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
# Atomic JSON writer
# ---------------------------------------------------------------------------

def _atomic_write_json(path: str, data: Any) -> None:
    """Write JSON atomically (write to temp, then rename).

    Prevents the engine from reading a half-written file.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Write to temp file in the same directory (same filesystem for rename)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(target.parent),
            prefix=".scanner_tmp_",
            suffix=".json",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path, str(target))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        log.error("Atomic write to %s failed: %s", path, e)


# ---------------------------------------------------------------------------
# Simulation mode — mock scanner results when IBKR is not connected
# ---------------------------------------------------------------------------

_SIM_SYMBOLS: Dict[str, List[str]] = {
    "US": [
        "NVDA", "SMCI", "AAPL", "TSLA", "MSFT", "AMD", "GOOG", "AMZN",
        "META", "NFLX", "COIN", "PLTR", "ARM", "MRVL", "MU", "AVGO",
        "CRM", "INTC", "QCOM", "AMAT", "LRCX", "KLAC", "TXN", "ASML",
    ],
    "TSE": [
        "7203", "6758", "8306", "6861", "6902", "8035", "6954",
        "9984", "7267", "4502", "9432", "6501", "8766", "8591",
    ],
    "HKEX": [
        "9988", "0700", "1211", "0001", "0388", "2318", "1398",
        "0941", "0883", "0005", "9618", "9999", "0027", "1299",
    ],
    "LSE": [
        "QQQ3", "3LUS", "TSL3", "NVD3", "5SPY", "3LNV", "3LAP",
        "3LMS", "3LAM", "GPT3", "3SEM", "3LMT", "QQQ5", "AMD3",
    ],
    "XETRA": [
        "SAP", "SIE", "MBG", "BMW", "ADS", "DTE", "EOAN",
        "MUV2", "ALV", "BEI", "VOW3", "HEN3", "RWE", "DBK",
    ],
    "EURONEXT": [
        "MC", "OR", "TTE", "SAN", "BNP", "AI", "SU",
        "DG", "AIR", "CS", "BN", "RI", "STLAM", "SGO",
    ],
    "SGX": [
        "D05", "O39", "Z74", "U11", "BN4", "C6L", "G13",
        "S58", "V03", "A17U", "H78", "C38U", "N2IU", "ME8U",
    ],
}


def _generate_sim_results(
    scanner_name: str,
    exchange: str,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """Generate realistic mock scanner results for simulation mode."""
    symbols = _SIM_SYMBOLS.get(exchange, _SIM_SYMBOLS["US"])
    # Shuffle and take a subset
    shuffled = list(symbols)
    random.shuffle(shuffled)
    count = min(max_results, len(shuffled))

    results = []
    for rank, symbol in enumerate(shuffled[:count], start=1):
        # Generate plausible values based on scanner type
        if "RVOL" in scanner_name or "Divergence" in scanner_name:
            volume = random.randint(500_000, 50_000_000)
            change_pct = round(random.uniform(-8.0, 8.0), 2)
        elif "Momentum" in scanner_name:
            volume = random.randint(1_000_000, 30_000_000)
            change_pct = round(random.uniform(1.0, 15.0), 2)
        elif "Oversold" in scanner_name:
            volume = random.randint(500_000, 20_000_000)
            change_pct = round(random.uniform(-15.0, -1.0), 2)
        elif "NearLow" in scanner_name:
            volume = random.randint(2_000_000, 40_000_000)
            change_pct = round(random.uniform(-5.0, 2.0), 2)
        else:
            volume = random.randint(1_000_000, 25_000_000)
            change_pct = round(random.uniform(-5.0, 5.0), 2)

        results.append({
            "symbol": symbol,
            "rank": rank,
            "volume": volume,
            "change_pct": change_pct,
        })

    return results


# ---------------------------------------------------------------------------
# IBKR Scanner Connection (ib_insync primary, ibapi fallback, sim last resort)
# ---------------------------------------------------------------------------

class IBKRScannerConnection:
    """Manages the IBKR connection for real-time scanner subscriptions.

    Uses ib_insync (preferred) or raw ibapi as fallback.
    Handles reconnection gracefully on IB Gateway daily restarts.
    """

    def __init__(self, host: str, port: int, client_id: int, timeout: int = 15):
        self._host = host
        self._port = port
        self._client_id = client_id
        self._timeout = timeout
        self._ib: Any = None
        self._connected = False
        self._using_ib_insync = False
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
                    if self._using_ib_insync and self._ib.isConnected():
                        return True
                except Exception:
                    self._connected = False
                    self._ib = None

            # Try ib_insync first
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
                self._using_ib_insync = True
                self._reconnect_count += 1
                log.info(
                    "Connected to IB Gateway at %s:%d (client_id=%d, ib_insync, attempt=%d)",
                    self._host, self._port, self._client_id, self._reconnect_count,
                )
                return True
            except ImportError:
                log.info("ib_insync not available, trying ibapi fallback")
            except Exception as e:
                log.warning("ib_insync connect failed: %s", e)

            # ibapi fallback
            try:
                return self._connect_ibapi()
            except Exception as e:
                log.warning("ibapi connect failed: %s", e)
                return False

    def _connect_ibapi(self) -> bool:
        """Connect using raw ibapi (TWS API)."""
        from ibapi.client import EClient
        from ibapi.wrapper import EWrapper
        import threading as _thr

        class ScannerWrapper(EWrapper, EClient):
            def __init__(self):
                EClient.__init__(self, self)
                self._results: Dict[int, List[Dict[str, Any]]] = {}
                self._done_events: Dict[int, threading.Event] = {}
                self._lock = threading.Lock()

            def scannerData(self, reqId, rank, contractDetails, distance, benchmark, projection, legsStr):
                with self._lock:
                    if reqId not in self._results:
                        self._results[reqId] = []
                    self._results[reqId].append({
                        "symbol": contractDetails.contract.symbol,
                        "rank": rank,
                        "exchange": contractDetails.contract.exchange,
                        "currency": contractDetails.contract.currency,
                        "name": getattr(contractDetails, "longName", ""),
                    })

            def scannerDataEnd(self, reqId):
                with self._lock:
                    if reqId in self._done_events:
                        self._done_events[reqId].set()

            def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
                if errorCode not in (2104, 2106, 2158, 2119):
                    log.debug("IBKR error: reqId=%d code=%d msg=%s", reqId, errorCode, errorString)

        wrapper = ScannerWrapper()
        wrapper.connect(self._host, self._port, self._client_id)
        api_thread = _thr.Thread(target=wrapper.run, daemon=True)
        api_thread.start()
        time.sleep(2)

        if wrapper.isConnected():
            self._ib = wrapper
            self._connected = True
            self._using_ib_insync = False
            self._reconnect_count += 1
            log.info(
                "Connected to IB Gateway at %s:%d (client_id=%d, ibapi, attempt=%d)",
                self._host, self._port, self._client_id, self._reconnect_count,
            )
            return True
        else:
            log.error("ibapi connection failed")
            return False

    def disconnect(self) -> None:
        """Disconnect from IB Gateway. Safe to call multiple times."""
        with self._lock:
            if self._ib is not None:
                try:
                    self._ib.disconnect()
                except Exception:
                    pass
                self._ib = None
            self._connected = False

    def request_scanner(
        self,
        scan_code: str,
        location_code: str,
        max_results: int = 50,
        above_price: float = 1.0,
        below_price: float = 100000.0,
    ) -> List[Dict[str, Any]]:
        """Execute a scanner request and return results.

        Returns list of dicts with: symbol, rank, volume, change_pct, exchange, currency.
        Returns empty list on failure.
        """
        with self._lock:
            if not self._connected or self._ib is None:
                return []

        try:
            if self._using_ib_insync:
                return self._request_ib_insync(
                    scan_code, location_code, max_results, above_price, below_price
                )
            else:
                return self._request_ibapi(
                    scan_code, location_code, max_results, above_price, below_price
                )
        except Exception as e:
            log.warning("Scanner request failed (%s @ %s): %s", scan_code, location_code, e)
            return []

    def _request_ib_insync(
        self,
        scan_code: str,
        location_code: str,
        max_results: int,
        above_price: float,
        below_price: float,
    ) -> List[Dict[str, Any]]:
        """Execute scanner via ib_insync."""
        from ib_insync import ScannerSubscription

        sub = ScannerSubscription(
            instrument="STK",
            locationCode=location_code,
            scanCode=scan_code,
            numberOfRows=max_results,
            abovePrice=above_price,
            belowPrice=below_price,
        )

        scanner_data = self._ib.reqScannerData(sub)
        results = []
        for item in scanner_data:
            cd = item.contractDetails
            results.append({
                "symbol": cd.contract.symbol,
                "rank": item.rank,
                "volume": 0,  # reqScannerData doesn't always include volume
                "change_pct": 0.0,
                "exchange": cd.contract.exchange,
                "currency": cd.contract.currency,
            })
        return results

    def _request_ibapi(
        self,
        scan_code: str,
        location_code: str,
        max_results: int,
        above_price: float,
        below_price: float,
    ) -> List[Dict[str, Any]]:
        """Execute scanner via raw ibapi."""
        from ibapi.client import ScannerSubscription as ScanSub

        sub = ScanSub()
        sub.instrument = "STK"
        sub.locationCode = location_code
        sub.scanCode = scan_code
        sub.numberOfRows = max_results
        sub.abovePrice = above_price
        sub.belowPrice = below_price

        # Use a unique request ID
        req_id = int(time.time() * 1000) % 100000

        wrapper = self._ib
        done_event = threading.Event()
        with wrapper._lock:
            wrapper._results[req_id] = []
            wrapper._done_events[req_id] = done_event

        wrapper.reqScannerSubscription(req_id, sub, [], [])

        # Wait up to 30s for results
        done_event.wait(timeout=30)

        # Cancel to free the subscription slot
        try:
            wrapper.cancelScannerSubscription(req_id)
        except Exception:
            pass

        with wrapper._lock:
            raw_results = wrapper._results.pop(req_id, [])
            wrapper._done_events.pop(req_id, None)

        return raw_results

    def is_healthy(self) -> bool:
        """Check if the connection is alive."""
        if not self._connected or self._ib is None:
            return False
        if self._using_ib_insync:
            try:
                return self._ib.isConnected()
            except Exception:
                return False
        else:
            try:
                return self._ib.isConnected()
            except Exception:
                return False


# ---------------------------------------------------------------------------
# Main Scanner Engine
# ---------------------------------------------------------------------------

class MarketScanner:
    """Real-time IBKR market scanner engine.

    Lifecycle:
      1. Connect to IB Gateway (client_id=103).
      2. Determine which exchanges are open based on current UTC hour.
      3. For each scanner config, request scan data for active exchanges.
      4. Write consolidated results to scanner_results.json (atomic).
      5. Log all events to NDJSON for Ouroboros meta-learning.
      6. Sleep for rotation_secs, then repeat from step 2.
      7. On disconnect, attempt reconnection with exponential backoff.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        simulation_mode: bool = False,
    ):
        self._config = config
        self._sim_mode = simulation_mode
        self._conn: Optional[IBKRScannerConnection] = None
        self._ndjson = NDJSONLogger(config.get("log_file", str(DATA_DIR / "ibkr_market_scanner.ndjson")))
        self._results_path = config.get("results_file", str(DATA_DIR / "scanner_results.json"))
        self._max_results = config.get("results_per_scanner", 50)
        self._rotation_secs = config.get("rotation_secs", 300)
        self._running = False
        self._scan_count = 0
        self._last_scan_time = 0.0
        self._consecutive_failures = 0
        self._backoff_schedule = config.get("reconnect_backoff_secs", [1, 2, 4, 8, 16, 32, 60])

    def _connect(self) -> bool:
        """Establish IBKR connection."""
        if self._sim_mode:
            log.info("SIMULATION_MODE — no IBKR connection needed")
            return True

        if self._conn is None:
            self._conn = IBKRScannerConnection(
                host=self._config.get("host", "aegis-ib-gateway"),
                port=self._config.get("port", 4003),
                client_id=self._config.get("client_id", 103),
                timeout=self._config.get("connect_timeout_sec", 15),
            )
        return self._conn.connect()

    def _disconnect(self) -> None:
        """Clean disconnect."""
        if self._conn is not None:
            self._conn.disconnect()

    def _reconnect_with_backoff(self) -> bool:
        """Reconnect with exponential backoff. Returns True on success."""
        for attempt, delay in enumerate(self._backoff_schedule):
            log.info("Reconnect attempt %d (backoff=%ds)...", attempt + 1, delay)
            time.sleep(delay)
            if self._connect():
                self._consecutive_failures = 0
                self._ndjson.log_event("reconnected", {"attempt": attempt + 1})
                return True
        log.error("All reconnect attempts exhausted (%d)", len(self._backoff_schedule))
        return False

    def _run_scan_cycle(self) -> Dict[str, Any]:
        """Execute one full scan cycle across all active exchanges.

        Returns the consolidated scanner results dict.
        """
        now_utc = datetime.now(timezone.utc)
        utc_hour = now_utc.hour
        active_exchanges = _get_active_exchanges(utc_hour)

        log.info(
            "Scan cycle #%d — UTC hour=%d, active exchanges=%s",
            self._scan_count + 1, utc_hour, active_exchanges,
        )

        all_scanner_results: Dict[str, Any] = {}

        for scanner_cfg in SCANNER_CONFIGS:
            scanner_name = scanner_cfg["name"]
            scan_code = scanner_cfg["scan_code"]

            # Pick the best exchange for this scanner type based on session.
            # Scan each active exchange and merge results.
            best_results: List[Dict[str, Any]] = []
            best_exchange = ""

            for exchange in active_exchanges:
                location = EXCHANGE_LOCATIONS.get(exchange)
                if location is None:
                    continue

                if self._sim_mode:
                    results = _generate_sim_results(
                        scanner_name, exchange, self._max_results
                    )
                else:
                    results = self._conn.request_scanner(  # type: ignore[union-attr]
                        scan_code=scan_code,
                        location_code=location,
                        max_results=self._max_results,
                        above_price=scanner_cfg.get("above_price", 1.0),
                        below_price=scanner_cfg.get("below_price", 100000.0),
                    )

                if results and len(results) > len(best_results):
                    best_results = results
                    best_exchange = exchange

                # IBKR rate limit: ~10 scanner requests per 600 seconds.
                # Space them out to avoid error 162 ("Historical data farm query cancelled").
                time.sleep(1.0)

            if best_results:
                all_scanner_results[scanner_name] = {
                    "exchange": best_exchange,
                    "location": EXCHANGE_LOCATIONS.get(best_exchange, ""),
                    "scan_code": scan_code,
                    "entry_type": scanner_cfg["entry_type"],
                    "result_count": len(best_results),
                    "results": best_results,
                }
                log.info(
                    "  %s: %d results from %s (%s)",
                    scanner_name, len(best_results), best_exchange, scan_code,
                )
            else:
                log.debug("  %s: no results from any active exchange", scanner_name)

        # Build consolidated output
        output = {
            "timestamp": now_utc.isoformat(),
            "scan_cycle": self._scan_count + 1,
            "utc_hour": utc_hour,
            "active_exchanges": active_exchanges,
            "scanners": all_scanner_results,
            "mode": "simulation" if self._sim_mode else "live",
        }

        self._scan_count += 1
        return output

    def scan_once(self) -> Dict[str, Any]:
        """Run a single scan cycle. Used for --test mode."""
        if not self._connect():
            if not self._sim_mode:
                log.warning("Cannot connect to IBKR — falling back to simulation mode for test")
                self._sim_mode = True
        results = self._run_scan_cycle()
        self._disconnect()
        return results

    def run_forever(self) -> None:
        """Main daemon loop. Runs until SIGINT/SIGTERM."""
        self._running = True
        log.info("=" * 70)
        log.info("IBKR Market Scanner STARTING (mode=%s, rotation=%ds)",
                 "simulation" if self._sim_mode else "live", self._rotation_secs)
        log.info("  Results: %s", self._results_path)
        log.info("  Log: %s", self._config.get("log_file", ""))
        log.info("  Client ID: %d", self._config.get("client_id", 103))
        log.info("=" * 70)

        self._ndjson.log_event("started", {
            "mode": "simulation" if self._sim_mode else "live",
            "rotation_secs": self._rotation_secs,
            "client_id": self._config.get("client_id", 103),
        })

        # Initial connection
        if not self._connect():
            if not self._sim_mode:
                log.warning("Initial IBKR connect failed — will retry on next cycle")

        while self._running:
            try:
                cycle_start = time.monotonic()

                # Check connection health (reconnect if needed)
                if not self._sim_mode:
                    if self._conn is None or not self._conn.is_healthy():
                        log.warning("Connection unhealthy — attempting reconnect")
                        self._disconnect()
                        if not self._reconnect_with_backoff():
                            log.error("Reconnect failed — waiting %ds before next attempt",
                                      self._rotation_secs)
                            self._ndjson.log_event("reconnect_failed", {
                                "consecutive_failures": self._consecutive_failures,
                            })
                            self._consecutive_failures += 1
                            time.sleep(self._rotation_secs)
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
                    "active_exchanges": results.get("active_exchanges", []),
                    "scanner_count": len(results.get("scanners", {})),
                    "total_results": total_results,
                    "elapsed_secs": round(time.monotonic() - cycle_start, 2),
                })

                self._consecutive_failures = 0
                self._last_scan_time = time.monotonic()

                # Sleep until next rotation
                elapsed = time.monotonic() - cycle_start
                sleep_time = max(0, self._rotation_secs - elapsed)
                log.info(
                    "Scan cycle #%d complete (%d results). Next scan in %ds.",
                    self._scan_count, total_results, int(sleep_time),
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
                # Back off on repeated failures
                backoff = min(60 * self._consecutive_failures, 300)
                time.sleep(backoff)

        # Shutdown
        log.info("Scanner shutdown — %d cycles completed", self._scan_count)
        self._ndjson.log_event("stopped", {"total_cycles": self._scan_count})
        self._disconnect()

    def stop(self) -> None:
        """Signal the daemon to stop gracefully."""
        self._running = False


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------

_scanner_instance: Optional[MarketScanner] = None


def _signal_handler(signum: int, frame: Any) -> None:
    """Handle SIGINT/SIGTERM for graceful shutdown."""
    sig_name = signal.Signals(signum).name
    log.info("Received %s — shutting down scanner", sig_name)
    if _scanner_instance is not None:
        _scanner_instance.stop()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    global _scanner_instance

    parser = argparse.ArgumentParser(
        description="IBKR Real-Time Market Scanner for AEGIS dark horse slots",
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Run a single scan cycle, print results, and exit",
    )
    parser.add_argument(
        "--sim", action="store_true",
        help="Simulation mode — generate mock results without IBKR connection",
    )
    parser.add_argument(
        "--client-id", type=int, default=None,
        help="Override IBKR client ID (default: from config or 103)",
    )
    parser.add_argument(
        "--rotation", type=int, default=None,
        help="Override rotation interval in seconds (default: from config or 300)",
    )
    args = parser.parse_args()

    # Load config
    config = _load_config()

    # Apply CLI overrides
    if args.client_id is not None:
        config["client_id"] = args.client_id
    if args.rotation is not None:
        config["rotation_secs"] = args.rotation

    sim_mode = args.sim or os.environ.get("AEGIS_SCANNER_SIM", "0") == "1"

    if args.test:
        # Single scan cycle — print and exit
        scanner = MarketScanner(config, simulation_mode=sim_mode)
        results = scanner.scan_once()
        print(json.dumps(results, indent=2, default=str))

        # Summary
        total = sum(
            s.get("result_count", 0)
            for s in results.get("scanners", {}).values()
        )
        print(f"\n--- {len(results.get('scanners', {}))} scanners, {total} total results ---")
        for name, data in results.get("scanners", {}).items():
            print(f"  {name}: {data.get('result_count', 0)} from {data.get('exchange', '?')}")
        return 0

    # Daemon mode — register signal handlers
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    _scanner_instance = MarketScanner(config, simulation_mode=sim_mode)
    _scanner_instance.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
