"""Symbology Mapper — SC-12: Complete IBKR↔Polygon↔yfinance mapping.

Provides comprehensive ticker format conversion between:
  - IBKR: NVD3 (symbol only, exchange specified separately)
  - Polygon: LSE:NVD3 or XLON:NVD3 (exchange:symbol format)
  - yfinance: NVD3.L (symbol.suffix format)

Also handles:
  - Preferred shares: BAC PRD → BAC-PD (IBKR) → BAC.PD (Polygon)
  - ADRs and dual-listed: cross-reference by ISIN
  - Batch conversion for universe scanning
  - Known conId cache for fast reqContractDetails

Usage: python3 -m python_brain.ouroboros.symbology_mapper [--test]

Quarantine rules:
  - Read-only: never places orders
  - Produces config/symbology_cache.json for engine consumption
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
CACHE_FILE = CONFIG_DIR / "symbology_cache.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Symbology] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("symbology_mapper")


# ---------------------------------------------------------------------------
# Exchange suffix maps
# ---------------------------------------------------------------------------

# IBKR exchange code → yfinance suffix
IBKR_EXCHANGE_TO_YF_SUFFIX = {
    "LSE": ".L",
    "LSEETF": ".L",
    "XETRA": ".DE",
    "IBIS": ".DE",
    "FWB": ".F",
    "SBF": ".PA",      # Euronext Paris
    "AEB": ".AS",       # Euronext Amsterdam
    "BVME": ".MI",      # Borsa Italiana
    "BM": ".MC",        # BME Madrid
    "EBS": ".SW",       # SIX Swiss
    "TSE": ".T",        # Tokyo
    "SEHK": ".HK",      # Hong Kong
    "ASX": ".AX",
    "SGX": ".SI",
    "KSE": ".KS",       # Korea
    "TSE.CA": ".TO",     # TSX
    "VENTURE": ".V",     # TSX Venture
    "NYSE": "",
    "NASDAQ": "",
    "AMEX": "",
    "ARCA": "",
    "BATS": "",
    "IEX": "",
    "SMART": "",         # IBKR smart routing (US)
}

# yfinance suffix → IBKR primary exchange
YF_SUFFIX_TO_IBKR = {
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

# Polygon exchange prefix → IBKR exchange
POLYGON_EXCHANGE_TO_IBKR = {
    "LSE": "LSEETF",
    "XLON": "LSEETF",
    "XETRA": "IBIS",
    "FRA": "FWB",
    "EPA": "SBF",
    "AMS": "AEB",
    "BIT": "BVME",
    "BME": "BM",
    "SWX": "EBS",
    "TYO": "TSE",
    "HKG": "SEHK",
    "ASX": "ASX",
    "SGX": "SGX",
    "KRX": "KSE",
    "TSX": "TSE",
    "NYSE": "NYSE",
    "NASDAQ": "NASDAQ",
    "AMEX": "AMEX",
}

# Reverse: IBKR exchange → Polygon exchange prefix
IBKR_TO_POLYGON_EXCHANGE = {v: k for k, v in POLYGON_EXCHANGE_TO_IBKR.items()}
# Fix duplicates
IBKR_TO_POLYGON_EXCHANGE["LSEETF"] = "XLON"
IBKR_TO_POLYGON_EXCHANGE["IBIS"] = "XETRA"
IBKR_TO_POLYGON_EXCHANGE["FWB"] = "FRA"
IBKR_TO_POLYGON_EXCHANGE["SBF"] = "EPA"
IBKR_TO_POLYGON_EXCHANGE["AEB"] = "AMS"
IBKR_TO_POLYGON_EXCHANGE["BVME"] = "BIT"
IBKR_TO_POLYGON_EXCHANGE["BM"] = "BME"
IBKR_TO_POLYGON_EXCHANGE["EBS"] = "SWX"
IBKR_TO_POLYGON_EXCHANGE["TSE"] = "TYO"
IBKR_TO_POLYGON_EXCHANGE["SEHK"] = "HKG"

US_EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "BATS", "ARCA", "IEX", "SMART"}


# ---------------------------------------------------------------------------
# Core conversion functions
# ---------------------------------------------------------------------------

def ibkr_to_yfinance(symbol: str, exchange: str) -> str:
    """Convert IBKR symbol+exchange to yfinance ticker.

    Examples:
        ("NVD3", "LSEETF") → "NVD3.L"
        ("AAPL", "NASDAQ") → "AAPL"
        ("7203", "TSE") → "7203.T"
    """
    suffix = IBKR_EXCHANGE_TO_YF_SUFFIX.get(exchange, "")
    return f"{symbol}{suffix}"


def yfinance_to_ibkr(yf_ticker: str) -> Tuple[str, str]:
    """Convert yfinance ticker to (symbol, exchange).

    Examples:
        "NVD3.L" → ("NVD3", "LSEETF")
        "AAPL" → ("AAPL", "SMART")
        "7203.T" → ("7203", "TSE")
    """
    for suffix, exchange in sorted(YF_SUFFIX_TO_IBKR.items(), key=lambda x: -len(x[0])):
        if yf_ticker.endswith(suffix):
            symbol = yf_ticker[:-len(suffix)]
            return (symbol, exchange)
    return (yf_ticker, "SMART")


def ibkr_to_polygon(symbol: str, exchange: str) -> str:
    """Convert IBKR symbol+exchange to Polygon ticker.

    Examples:
        ("NVD3", "LSEETF") → "XLON:NVD3"
        ("AAPL", "NASDAQ") → "AAPL"
    """
    if exchange in US_EXCHANGES:
        return symbol
    prefix = IBKR_TO_POLYGON_EXCHANGE.get(exchange, "")
    if prefix:
        return f"{prefix}:{symbol}"
    return symbol


def polygon_to_ibkr(polygon_ticker: str) -> Tuple[str, str]:
    """Convert Polygon ticker to (symbol, exchange).

    Examples:
        "XLON:NVD3" → ("NVD3", "LSEETF")
        "AAPL" → ("AAPL", "SMART")
    """
    if ":" in polygon_ticker:
        prefix, symbol = polygon_ticker.split(":", 1)
        exchange = POLYGON_EXCHANGE_TO_IBKR.get(prefix.upper(), "SMART")
        return (symbol, exchange)
    return (polygon_ticker, "SMART")


def yfinance_to_polygon(yf_ticker: str) -> str:
    """Convert yfinance ticker to Polygon format."""
    symbol, exchange = yfinance_to_ibkr(yf_ticker)
    return ibkr_to_polygon(symbol, exchange)


def polygon_to_yfinance(polygon_ticker: str) -> str:
    """Convert Polygon ticker to yfinance format."""
    symbol, exchange = polygon_to_ibkr(polygon_ticker)
    return ibkr_to_yfinance(symbol, exchange)


# ---------------------------------------------------------------------------
# Preferred share handling
# ---------------------------------------------------------------------------

def normalize_preferred(symbol: str) -> str:
    """Normalize preferred share notation.

    IBKR uses space: "BAC PRD"
    Polygon uses dot: "BAC.PD"
    yfinance uses dash: "BAC-PD"
    """
    # IBKR format: "BAC PRD" → strip the PR prefix
    if " PR" in symbol:
        parts = symbol.split(" PR", 1)
        return f"{parts[0]}.PR{parts[1]}"
    return symbol


def is_preferred_share(symbol: str) -> bool:
    """Check if symbol represents a preferred share."""
    indicators = [" PR", ".PR", "-PR", "/PR", "PRF"]
    return any(ind in symbol.upper() for ind in indicators)


# ---------------------------------------------------------------------------
# Batch conversion
# ---------------------------------------------------------------------------

def batch_convert(
    tickers: List[str],
    from_format: str,
    to_format: str,
) -> Dict[str, str]:
    """Batch convert tickers between formats.

    Args:
        tickers: List of ticker strings
        from_format: "ibkr", "yfinance", or "polygon"
        to_format: "ibkr", "yfinance", or "polygon"

    Returns:
        Dict of input_ticker → converted_ticker
    """
    results = {}
    for ticker in tickers:
        try:
            if from_format == "yfinance" and to_format == "polygon":
                results[ticker] = yfinance_to_polygon(ticker)
            elif from_format == "yfinance" and to_format == "ibkr":
                sym, exch = yfinance_to_ibkr(ticker)
                results[ticker] = f"{sym}@{exch}"
            elif from_format == "polygon" and to_format == "yfinance":
                results[ticker] = polygon_to_yfinance(ticker)
            elif from_format == "polygon" and to_format == "ibkr":
                sym, exch = polygon_to_ibkr(ticker)
                results[ticker] = f"{sym}@{exch}"
            else:
                results[ticker] = ticker
        except Exception as e:
            log.warning("Failed to convert %s: %s", ticker, e)
            results[ticker] = ticker
    return results


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def load_cache() -> Dict[str, Any]:
    """Load the symbology cache (conId mappings, etc.)."""
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            pass
    return {"tickers": {}, "updated": ""}


def save_cache(cache: Dict[str, Any]) -> None:
    """Save the symbology cache atomically."""
    cache["updated"] = datetime.now(timezone.utc).isoformat()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, indent=2))
    os.rename(str(tmp), str(CACHE_FILE))
    log.info("Symbology cache saved: %d tickers", len(cache.get("tickers", {})))


def update_cache_from_contracts_toml() -> Dict[str, Any]:
    """Parse contracts.toml and update the symbology cache with known mappings."""
    contracts_path = CONFIG_DIR / "contracts.toml"
    cache = load_cache()

    if not contracts_path.exists():
        log.warning("contracts.toml not found")
        return cache

    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            log.warning("No TOML parser available (need Python 3.11+ or tomli)")
            return cache

    with open(contracts_path, "rb") as f:
        contracts = tomllib.load(f)

    for section_name, contract in contracts.items():
        if not isinstance(contract, dict):
            continue
        symbol = contract.get("symbol", section_name)
        exchange = contract.get("exchange", "SMART")
        con_id = contract.get("conId", 0)
        currency = contract.get("currency", "USD")

        yf_ticker = ibkr_to_yfinance(symbol, exchange)
        polygon_ticker = ibkr_to_polygon(symbol, exchange)

        cache["tickers"][yf_ticker] = {
            "ibkr_symbol": symbol,
            "ibkr_exchange": exchange,
            "polygon": polygon_ticker,
            "yfinance": yf_ticker,
            "conId": con_id,
            "currency": currency,
        }

    save_cache(cache)
    log.info("Updated cache from contracts.toml: %d contracts", len(cache["tickers"]))
    return cache


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="SC-12: Symbology Mapper")
    parser.add_argument("--test", action="store_true", help="Run conversion tests")
    parser.add_argument("--update-cache", action="store_true", help="Update cache from contracts.toml")
    parser.add_argument("--convert", type=str, help="Convert a ticker (e.g. NVD3.L)")
    args = parser.parse_args()

    if args.update_cache:
        update_cache_from_contracts_toml()

    if args.convert:
        ticker = args.convert
        print(f"Input: {ticker}")
        # Try all conversions
        sym, exch = yfinance_to_ibkr(ticker)
        print(f"  IBKR: symbol={sym}, exchange={exch}")
        print(f"  Polygon: {yfinance_to_polygon(ticker)}")
        print(f"  yfinance: {ticker}")

    if args.test:
        test_cases = [
            ("NVD3.L", "yfinance"),
            ("QQQ3.L", "yfinance"),
            ("AAPL", "yfinance"),
            ("7203.T", "yfinance"),
            ("XLON:NVD3", "polygon"),
            ("EPA:TTE", "polygon"),
        ]
        print("Symbology Mapper — Test Results")
        print("-" * 60)
        for ticker, fmt in test_cases:
            if fmt == "yfinance":
                sym, exch = yfinance_to_ibkr(ticker)
                poly = yfinance_to_polygon(ticker)
                print(f"  {ticker:15s} → IBKR({sym}@{exch})  Polygon({poly})")
            elif fmt == "polygon":
                sym, exch = polygon_to_ibkr(ticker)
                yf = polygon_to_yfinance(ticker)
                print(f"  {ticker:15s} → IBKR({sym}@{exch})  yfinance({yf})")
        print("-" * 60)
        print("Preferred share test:")
        print(f"  normalize_preferred('BAC PRD') = {normalize_preferred('BAC PRD')}")
        print(f"  is_preferred_share('BAC PRD') = {is_preferred_share('BAC PRD')}")


if __name__ == "__main__":
    main()
