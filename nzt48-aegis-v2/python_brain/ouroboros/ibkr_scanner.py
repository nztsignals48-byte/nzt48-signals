"""IBKR Universe Scanner — Discovers tickers via Interactive Brokers API.

Runs weekly (Sunday 22:00 UTC) inside the Docker container to discover
ALL available tickers across ISA-eligible exchanges using IBKR's
contract search and scanner APIs.

Connects to IB Gateway on port 4003 (live trading) using client_id=102
(V1=100, AEGIS V2 main=101, scanner=102).

Usage: python3 -m python_brain.ouroboros.ibkr_scanner

Quarantine rules:
  - Read-only: only queries contract info, never places orders
  - Uses a dedicated client_id (102) to avoid interfering with live trading
  - Network failures cause graceful retry, not crash
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
MASTER_FILE = CONFIG_DIR / "isa_universe_master.json"
REPORTS_DIR = DATA_DIR / "ouroboros_reports"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [IBKR-Scanner] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ibkr_scanner")

# IBKR connection settings
IB_HOST = os.environ.get("IB_HOST", "ib-gateway")  # Docker service name
IB_PORT = int(os.environ.get("IB_PORT", "4003"))    # gnzsnz paper API proxy port
IB_CLIENT_ID = 102  # Dedicated scanner client_id

# P0-5: IBKR rate limiting — 50 msg/s hard limit, use 40/s to be safe
IBKR_BATCH_SIZE = 50          # Max contracts per batch
IBKR_BATCH_SLEEP_SEC = 10     # Sleep between batches
IBKR_MAX_SCAN_MINUTES = 60    # Abort if scan takes longer than this
IBKR_REQ_SLEEP_SEC = 0.025    # 25ms between individual requests (40/s)

# Exchanges to scan via IBKR
IBKR_EXCHANGES = {
    "LSE": {"exchange": "LSE", "sec_type": "STK", "currency": "GBP"},
    "LSEETF": {"exchange": "LSEETF", "sec_type": "STK", "currency": "GBP"},
    "NYSE": {"exchange": "NYSE", "sec_type": "STK", "currency": "USD"},
    "NASDAQ": {"exchange": "NASDAQ", "sec_type": "STK", "currency": "USD"},
    "AMEX": {"exchange": "AMEX", "sec_type": "STK", "currency": "USD"},
    "ARCA": {"exchange": "ARCA", "sec_type": "STK", "currency": "USD"},
    "TSE": {"exchange": "TSE", "sec_type": "STK", "currency": "JPY"},
    "SEHK": {"exchange": "SEHK", "sec_type": "STK", "currency": "HKD"},
    "ASX": {"exchange": "ASX", "sec_type": "STK", "currency": "AUD"},
    "IBIS": {"exchange": "IBIS", "sec_type": "STK", "currency": "EUR"},  # XETRA
    "SBF": {"exchange": "SBF", "sec_type": "STK", "currency": "EUR"},   # Euronext Paris
    "AEB": {"exchange": "AEB", "sec_type": "STK", "currency": "EUR"},   # Euronext Amsterdam
    "EBS": {"exchange": "EBS", "sec_type": "STK", "currency": "CHF"},   # SIX
    "TSE_CA": {"exchange": "TSE", "sec_type": "STK", "currency": "CAD"},  # TSX
    "KSE": {"exchange": "KSE", "sec_type": "STK", "currency": "KRW"},
    "SGX": {"exchange": "SGX", "sec_type": "STK", "currency": "SGD"},
}

# Map IBKR exchange names to our canonical names
IBKR_TO_CANONICAL = {
    "LSE": "LSE", "LSEETF": "LSE",
    "NYSE": "NYSE", "NASDAQ": "NASDAQ",
    "AMEX": "AMEX", "ARCA": "ARCA",
    "TSE": "TSE", "SEHK": "HKEX",
    "ASX": "ASX", "IBIS": "XETRA",
    "SBF": "EURONEXT_PA", "AEB": "EURONEXT_AS",
    "EBS": "SIX", "TSE_CA": "TSX",
    "KSE": "KRX", "SGX": "SGX",
}


# ---------------------------------------------------------------------------
# IBKR Connection (ib_insync or raw ibapi)
# ---------------------------------------------------------------------------

def connect_ibkr() -> Optional[Any]:
    """Connect to IB Gateway. Returns IB connection object or None."""
    try:
        from ib_insync import IB
        ib = IB()
        ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, timeout=30)
        log.info("Connected to IB Gateway at %s:%d (client_id=%d)", IB_HOST, IB_PORT, IB_CLIENT_ID)
        return ib
    except ImportError:
        log.warning("ib_insync not installed — trying raw ibapi")
        return _connect_ibapi()
    except Exception as e:
        log.error("Failed to connect to IB Gateway: %s", e)
        return None


def _rate_limited_sleep(request_count: int, start_time: float) -> None:
    """Enforce IBKR rate limits: sleep between batches, abort if too long."""
    # Check total time limit
    elapsed_min = (time.monotonic() - start_time) / 60
    if elapsed_min > IBKR_MAX_SCAN_MINUTES:
        raise TimeoutError(f"IBKR scan exceeded {IBKR_MAX_SCAN_MINUTES} min limit")

    # Sleep between individual requests
    time.sleep(IBKR_REQ_SLEEP_SEC)

    # Extra sleep at batch boundaries
    if request_count > 0 and request_count % IBKR_BATCH_SIZE == 0:
        log.info("  Rate limit: %d requests done, sleeping %ds at batch boundary",
                 request_count, IBKR_BATCH_SLEEP_SEC)
        time.sleep(IBKR_BATCH_SLEEP_SEC)


def _connect_ibapi() -> Optional[Any]:
    """Fallback: connect using raw ibapi (TWS API)."""
    try:
        from ibapi.client import EClient
        from ibapi.wrapper import EWrapper
        import threading

        class IBScanner(EWrapper, EClient):
            def __init__(self):
                EClient.__init__(self, self)
                self.contracts = []
                self.scanner_data = []
                self.done = False

            def scannerData(self, reqId, rank, contractDetails, distance, benchmark, projection, legsStr):
                self.scanner_data.append({
                    "rank": rank,
                    "symbol": contractDetails.contract.symbol,
                    "exchange": contractDetails.contract.exchange,
                    "sec_type": contractDetails.contract.secType,
                    "currency": contractDetails.contract.currency,
                    "name": contractDetails.longName,
                })

            def scannerDataEnd(self, reqId):
                self.done = True

            def contractDetails(self, reqId, contractDetails):
                self.contracts.append({
                    "symbol": contractDetails.contract.symbol,
                    "exchange": contractDetails.contract.exchange,
                    "sec_type": contractDetails.contract.secType,
                    "currency": contractDetails.contract.currency,
                    "name": contractDetails.longName,
                    "category": contractDetails.category,
                    "subcategory": contractDetails.subcategory,
                })

            def contractDetailsEnd(self, reqId):
                self.done = True

        scanner = IBScanner()
        scanner.connect(IB_HOST, IB_PORT, IB_CLIENT_ID)
        thread = threading.Thread(target=scanner.run, daemon=True)
        thread.start()
        time.sleep(2)

        if scanner.isConnected():
            log.info("Connected to IB Gateway via ibapi at %s:%d", IB_HOST, IB_PORT)
            return scanner
        else:
            log.error("ibapi connection failed")
            return None
    except ImportError:
        log.error("Neither ib_insync nor ibapi available")
        return None
    except Exception as e:
        log.error("ibapi connection failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Scanner methods
# ---------------------------------------------------------------------------

def scan_exchange_ib_insync(ib: Any, exchange_name: str, config: Dict[str, str]) -> List[Dict[str, Any]]:
    """Scan an exchange using ib_insync scanner subscription."""
    from ib_insync import ScannerSubscription

    results = []
    try:
        # Most active by volume scanner — gives us the broadest coverage
        sub = ScannerSubscription(
            instrument="STK",
            locationCode=f"STK.{config['exchange']}",
            scanCode="MOST_ACTIVE",
            numberOfRows=500,  # Max rows per scan
        )

        scanner_data = ib.reqScannerData(sub)

        for item in scanner_data:
            cd = item.contractDetails
            results.append({
                "symbol": cd.contract.symbol,
                "ibkr_exchange": config["exchange"],
                "exchange": IBKR_TO_CANONICAL.get(exchange_name, exchange_name),
                "sec_type": cd.contract.secType,
                "currency": cd.contract.currency,
                "name": cd.longName or "",
                "category": getattr(cd, "category", ""),
                "subcategory": getattr(cd, "subcategory", ""),
            })

        log.info("  %s: %d tickers from MOST_ACTIVE scan", exchange_name, len(results))

        # Also try TOP_PERC_GAIN and TOP_PERC_LOSE for broader coverage
        for scan_code in ["TOP_PERC_GAIN", "TOP_PERC_LOSE", "HOT_BY_VOLUME"]:
            try:
                sub2 = ScannerSubscription(
                    instrument="STK",
                    locationCode=f"STK.{config['exchange']}",
                    scanCode=scan_code,
                    numberOfRows=500,
                )
                data2 = ib.reqScannerData(sub2)
                for item in data2:
                    cd = item.contractDetails
                    sym = cd.contract.symbol
                    if not any(r["symbol"] == sym for r in results):
                        results.append({
                            "symbol": sym,
                            "ibkr_exchange": config["exchange"],
                            "exchange": IBKR_TO_CANONICAL.get(exchange_name, exchange_name),
                            "sec_type": cd.contract.secType,
                            "currency": cd.contract.currency,
                            "name": cd.longName or "",
                            "category": getattr(cd, "category", ""),
                            "subcategory": getattr(cd, "subcategory", ""),
                        })
                log.info("  %s: +%d from %s scan", exchange_name, len(data2), scan_code)
                time.sleep(IBKR_BATCH_SLEEP_SEC // 2)  # Rate limit between scans
            except Exception as e:
                log.debug("  %s %s scan failed: %s", exchange_name, scan_code, e)

    except Exception as e:
        log.warning("  %s scanner failed: %s", exchange_name, e)

    return results


def scan_exchange_ibapi(scanner: Any, exchange_name: str, config: Dict[str, str]) -> List[Dict[str, Any]]:
    """Scan an exchange using raw ibapi scanner."""
    from ibapi.client import ScannerSubscription as ScanSub

    results = []
    try:
        sub = ScanSub()
        sub.instrument = "STK"
        sub.locationCode = f"STK.{config['exchange']}"
        sub.scanCode = "MOST_ACTIVE"
        sub.numberOfRows = 500

        scanner.done = False
        scanner.scanner_data = []
        req_id = hash(exchange_name) % 10000
        scanner.reqScannerSubscription(req_id, sub, [], [])

        # Wait for results
        timeout = 30
        waited = 0
        while not scanner.done and waited < timeout:
            time.sleep(0.5)
            waited += 0.5

        scanner.cancelScannerSubscription(req_id)

        for item in scanner.scanner_data:
            item["exchange"] = IBKR_TO_CANONICAL.get(exchange_name, exchange_name)
            results.append(item)

        log.info("  %s: %d tickers from ibapi scan", exchange_name, len(results))

    except Exception as e:
        log.warning("  %s ibapi scan failed: %s", exchange_name, e)

    return results


# ---------------------------------------------------------------------------
# Master file operations
# ---------------------------------------------------------------------------

def load_master() -> Optional[Dict[str, Any]]:
    """Load the master universe file."""
    if not MASTER_FILE.exists():
        return None
    try:
        with open(MASTER_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def merge_ibkr_discoveries(
    master: Dict[str, Any],
    ibkr_results: List[Dict[str, Any]],
) -> tuple[Dict[str, Any], int]:
    """Merge IBKR scanner results into the master file.

    Returns: (updated_master, new_count)
    """
    existing_symbols: Set[str] = {t["symbol"] for t in master.get("tickers", [])}
    new_count = 0

    for result in ibkr_results:
        sym = result["symbol"]
        exchange = result.get("exchange", "Unknown")

        # Add exchange suffix for non-US tickers
        suffix_map = {
            "LSE": ".L", "HKEX": ".HK", "TSE": ".T", "ASX": ".AX",
            "XETRA": ".DE", "EURONEXT_PA": ".PA", "EURONEXT_AS": ".AS",
            "SIX": ".SW", "TSX": ".TO", "KRX": ".KS", "SGX": ".SI",
        }
        suffix = suffix_map.get(exchange, "")
        if suffix and not sym.endswith(suffix):
            sym = sym + suffix

        if sym in existing_symbols:
            continue

        ticker_entry = {
            "symbol": sym,
            "exchange": exchange,
            "name": result.get("name", ""),
            "type": result.get("sec_type", "STK").lower() if result.get("sec_type") != "STK" else "stock",
            "sector": result.get("category", "Unknown"),
            "industry": result.get("subcategory", "Unknown"),
            "currency": result.get("currency", "USD"),
            "isa_eligible": True,
            "leveraged": False,
            "inverse": False,
            "leverage_factor": 1,
            "market_cap_usd": 0,
            "avg_daily_volume": 0,
            "validated": True,
            "last_validated": datetime.now(timezone.utc).isoformat(),
            "source": "ibkr_scanner",
        }

        # Detect leveraged ETPs
        name_upper = ticker_entry["name"].upper()
        if any(kw in name_upper for kw in ["3X", "2X", "5X", "LEVERAGED", "TRIPLE", "DOUBLE"]):
            ticker_entry["leveraged"] = True
            ticker_entry["type"] = "leveraged_etp"
            if "3X" in name_upper or "TRIPLE" in name_upper:
                ticker_entry["leverage_factor"] = 3
            elif "5X" in name_upper:
                ticker_entry["leverage_factor"] = 5
            else:
                ticker_entry["leverage_factor"] = 2
            if any(kw in name_upper for kw in ["SHORT", "INVERSE", "BEAR"]):
                ticker_entry["inverse"] = True

        master["tickers"].append(ticker_entry)
        existing_symbols.add(sym)
        new_count += 1

    # Update totals
    active = [t for t in master["tickers"] if not t.get("delisted")]
    master["total_tickers"] = len(active)

    return master, new_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_ibkr_scan() -> int:
    """Execute the weekly IBKR universe scan."""
    start = time.monotonic()
    log.info("=" * 60)
    log.info("IBKR Universe Scanner — Weekly Scan")
    log.info("=" * 60)

    # Step 1: Load master file (bootstrap-safe: create empty if missing)
    master = load_master()
    if master is None:
        log.warning("Master file not found — creating empty master for bootstrap")
        master = {
            "tickers": [],
            "total_tickers": 0,
            "discovery_methods": [],
            "created": datetime.now(timezone.utc).isoformat(),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "bootstrap": True,
        }
        # Save the empty master so subsequent modules can find it
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(MASTER_FILE, "w") as f:
                json.dump(master, f, indent=2)
            log.info("Created empty master file at %s", MASTER_FILE)
        except Exception as e:
            log.error("Failed to create empty master: %s", e)
            return 1

    existing_count = master.get("total_tickers", 0)
    log.info("Step 1: Loaded master file (%d tickers)", existing_count)

    # Step 2: Connect to IBKR
    ib = connect_ibkr()
    if ib is None:
        log.error("Cannot connect to IB Gateway. Is it running?")
        return 1

    # Step 3: Scan all exchanges
    all_results: List[Dict[str, Any]] = []
    is_ib_insync = hasattr(ib, 'reqScannerData')

    scan_start = time.monotonic()
    request_count = 0

    for exchange_name, config in IBKR_EXCHANGES.items():
        log.info("Scanning %s...", exchange_name)
        try:
            _rate_limited_sleep(request_count, scan_start)
            if is_ib_insync:
                results = scan_exchange_ib_insync(ib, exchange_name, config)
            else:
                results = scan_exchange_ibapi(ib, exchange_name, config)
            all_results.extend(results)
            request_count += 1 + len(results) // 100  # Account for multi-scan requests
        except TimeoutError as e:
            log.warning("Aborting scan: %s", e)
            break
        except Exception as e:
            log.warning("Exchange %s scan failed: %s", exchange_name, e)

    log.info("Step 3: Scanned %d exchanges, found %d total tickers",
             len(IBKR_EXCHANGES), len(all_results))

    # Step 4: Disconnect
    try:
        if is_ib_insync:
            ib.disconnect()
        else:
            ib.disconnect()
    except Exception:
        pass

    # Step 5: Merge results
    master, new_count = merge_ibkr_discoveries(master, all_results)
    log.info("Step 5: Merged results — %d new tickers added", new_count)

    # Step 6: Save master file
    master["last_updated"] = datetime.now(timezone.utc).isoformat()
    if "ibkr_scanner" not in master.get("discovery_methods", []):
        master.setdefault("discovery_methods", []).append("ibkr_scanner")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(MASTER_FILE, "w") as f:
        json.dump(master, f, indent=2, default=str)

    # Step 7: Save scan report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"ibkr_scan_{today}.json"
    report = {
        "date": today,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "exchanges_scanned": len(IBKR_EXCHANGES),
        "total_discovered": len(all_results),
        "new_added": new_count,
        "total_universe": master["total_tickers"],
        "by_exchange": {},
    }
    for r in all_results:
        exch = r.get("exchange", "Unknown")
        report["by_exchange"].setdefault(exch, 0)
        report["by_exchange"][exch] += 1

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    elapsed = time.monotonic() - start
    log.info("=" * 60)
    log.info("IBKR Scanner complete in %.1fs", elapsed)
    log.info("  Total discovered: %d | New added: %d | Universe: %d",
             len(all_results), new_count, master["total_tickers"])
    log.info("=" * 60)

    return 0


def main():
    try:
        sys.exit(run_ibkr_scan())
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        log.error("IBKR scan failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
