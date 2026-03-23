#!/usr/bin/env python3
"""
build_universe.py — Build complete stock universe database from Wikipedia index constituents.

Downloads FTSE 100, FTSE 250, S&P 500, and NASDAQ-100 constituent lists
from Wikipedia, maps IBKR symbols, and saves as universe.json.

Idempotent and re-runnable. Uses pandas.read_html() for table parsing.

Usage:
    python3 scripts/build_universe.py
"""

import json
import logging
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Wikipedia URLs
URLS = {
    "ftse100": "https://en.wikipedia.org/wiki/FTSE_100_Index",
    "ftse250": "https://en.wikipedia.org/wiki/FTSE_250_Index",
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "nasdaq100": "https://en.wikipedia.org/wiki/Nasdaq-100",
}

# LSE 2-char TIDMs that need a trailing dot for IBKR
# IBKR requires these because 2-char symbols are ambiguous without the dot
LSE_DOTTED_TIDMS = {
    "AV", "BA", "BP", "JD", "NG", "RR", "SN", "TW", "UU",  # FTSE 100
    "AO", "QQ", "HL", "AG",  # FTSE 250 / other
}

# Known NASDAQ-listed S&P 500 stocks (IBKR exchange = ISLAND)
# These are the major ones; the rest default to NYSE (SMART routing handles it)
KNOWN_NASDAQ_SP500 = {
    "AAPL", "ABNB", "ADBE", "ADI", "ADP", "ADSK", "AEP", "ALGN", "AMAT",
    "AMD", "AMGN", "AMZN", "ANSS", "ASML", "ATVI", "AVGO", "AZN", "BIIB",
    "BKNG", "BKR", "CDNS", "CDW", "CEG", "CHTR", "CMCSA", "COST", "CPRT",
    "CRWD", "CSCO", "CSGP", "CSX", "CTAS", "CTSH", "DDOG", "DLTR", "DXCM",
    "EA", "EBAY", "ENPH", "EXC", "FANG", "FAST", "FCNCA", "FISV", "FTNT",
    "GEHC", "GFS", "GILD", "GOOG", "GOOGL", "HON", "IDXX", "ILMN", "INTC",
    "INTU", "ISRG", "KDP", "KHC", "KLAC", "LRCX", "LULU", "MAR", "MCHP",
    "MDLZ", "MELI", "META", "MNST", "MRNA", "MRVL", "MSFT", "MU", "NFLX",
    "NXPI", "ODFL", "ON", "ORLY", "PANW", "PAYX", "PCAR", "PDD", "PEP",
    "PYPL", "QCOM", "REGN", "ROST", "SBUX", "SIRI", "SNPS", "TEAM", "TMUS",
    "TSLA", "TTD", "TXN", "VRSK", "VRTX", "WBA", "WBD", "WDAY", "XEL",
    "ZS", "NVDA", "NDAQ", "DASH", "TTWO", "GEHC", "SMCI", "ARM", "APP",
    "PLTR", "MSTR", "COIN", "DDOG", "SNOW", "NET", "ZM", "OKTA",
    "WMT",  # Walmart moved to NASDAQ Jan 2026
}

# Output path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
OUTPUT_PATH = PROJECT_ROOT / "config" / "universe.json"


# ── Helper functions ──────────────────────────────────────────────────────────

def fetch_tables(url: str) -> list:
    """Fetch HTML from URL and parse all tables via pandas."""
    log.info(f"Fetching: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return pd.read_html(StringIO(resp.text))


def lse_ibkr_symbol(tidm: str) -> str:
    """
    Convert LSE TIDM to IBKR symbol.
    2-char TIDMs get a trailing dot (e.g., BP -> BP.)
    All others stay as-is (e.g., HSBA -> HSBA)
    """
    tidm = tidm.strip().upper()
    if len(tidm) <= 2 or tidm in LSE_DOTTED_TIDMS:
        return tidm + "."
    return tidm


def clean_sector(sector: str) -> str:
    """Normalize sector strings."""
    if pd.isna(sector):
        return "Unknown"
    return str(sector).strip()


def clean_symbol(sym: str) -> str:
    """Clean up a symbol string (remove footnotes, whitespace, etc.)."""
    if pd.isna(sym):
        return ""
    sym = str(sym).strip()
    # Remove Wikipedia footnote markers like [a], [1], etc.
    import re
    sym = re.sub(r'\[.*?\]', '', sym)
    return sym.strip()


def clean_name(name: str) -> str:
    """Clean up company name."""
    if pd.isna(name):
        return ""
    name = str(name).strip()
    import re
    name = re.sub(r'\[.*?\]', '', name)
    return name.strip()


# ── Index parsers ─────────────────────────────────────────────────────────────

def parse_ftse100() -> list:
    """Parse FTSE 100 constituents from Wikipedia."""
    tables = fetch_tables(URLS["ftse100"])

    # The constituents table has columns: Company, Ticker, sector
    # Find it by looking for a table with ~100 rows and a 'Ticker' column
    df = None
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        if any('ticker' in c for c in cols) and len(t) >= 90:
            df = t
            break

    if df is None:
        log.error("Could not find FTSE 100 constituents table")
        return []

    # Normalize column names
    col_map = {}
    for c in df.columns:
        cl = str(c).lower()
        if 'company' in cl:
            col_map[c] = 'company'
        elif 'ticker' in cl:
            col_map[c] = 'ticker'
        elif 'sector' in cl or 'benchmark' in cl or 'classification' in cl:
            col_map[c] = 'sector'
    df = df.rename(columns=col_map)

    results = []
    for _, row in df.iterrows():
        tidm = clean_symbol(row.get('ticker', ''))
        if not tidm:
            continue
        ibkr_sym = lse_ibkr_symbol(tidm)
        results.append({
            "symbol": f"{tidm}.L",
            "ibkr_symbol": ibkr_sym,
            "ibkr_exchange": "LSE",
            "name": clean_name(row.get('company', '')),
            "sector": clean_sector(row.get('sector', 'Unknown')),
            "index": "FTSE100",
            "currency": "GBP",
        })

    log.info(f"FTSE 100: parsed {len(results)} constituents")
    return results


def parse_ftse250() -> list:
    """Parse FTSE 250 constituents from Wikipedia."""
    tables = fetch_tables(URLS["ftse250"])

    # The constituents table has columns: Company, Ticker, sector
    df = None
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        if any('ticker' in c for c in cols) and len(t) >= 200:
            df = t
            break

    if df is None:
        log.error("Could not find FTSE 250 constituents table")
        return []

    # Normalize column names
    col_map = {}
    for c in df.columns:
        cl = str(c).lower()
        if 'company' in cl:
            col_map[c] = 'company'
        elif 'ticker' in cl:
            col_map[c] = 'ticker'
        elif 'sector' in cl or 'benchmark' in cl or 'classification' in cl:
            col_map[c] = 'sector'
    df = df.rename(columns=col_map)

    results = []
    for _, row in df.iterrows():
        tidm = clean_symbol(row.get('ticker', ''))
        if not tidm:
            continue
        ibkr_sym = lse_ibkr_symbol(tidm)
        results.append({
            "symbol": f"{tidm}.L",
            "ibkr_symbol": ibkr_sym,
            "ibkr_exchange": "LSE",
            "name": clean_name(row.get('company', '')),
            "sector": clean_sector(row.get('sector', 'Unknown')),
            "index": "FTSE250",
            "currency": "GBP",
        })

    log.info(f"FTSE 250: parsed {len(results)} constituents")
    return results


def parse_sp500() -> list:
    """Parse S&P 500 constituents from Wikipedia."""
    tables = fetch_tables(URLS["sp500"])

    # First table (index 0) is the main constituents table
    # Columns: Symbol, Security, GICS Sector, GICS Sub-Industry, ...
    df = tables[0]

    results = []
    for _, row in df.iterrows():
        sym = clean_symbol(row.get('Symbol', ''))
        if not sym:
            continue

        # Determine exchange: NASDAQ or NYSE
        # S&P 500 Wikipedia table doesn't have exchange column,
        # so we use our known NASDAQ set
        if sym in KNOWN_NASDAQ_SP500:
            exchange = "ISLAND"
            display_exchange = "NASDAQ"
        else:
            exchange = "NYSE"
            display_exchange = "NYSE"

        results.append({
            "symbol": sym,
            "ibkr_symbol": sym,
            "ibkr_exchange": exchange,
            "name": clean_name(row.get('Security', '')),
            "sector": clean_sector(row.get('GICS Sector', 'Unknown')),
            "index": "SP500",
            "currency": "USD",
            "display_exchange": display_exchange,
        })

    log.info(f"S&P 500: parsed {len(results)} constituents")
    return results


def parse_nasdaq100() -> list:
    """Parse NASDAQ-100 constituents from Wikipedia."""
    tables = fetch_tables(URLS["nasdaq100"])

    # Find the constituents table: has ~100 rows with Ticker and Company columns
    df = None
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        if any('ticker' in c for c in cols) and any('company' in c for c in cols) and len(t) >= 95:
            df = t
            break

    if df is None:
        log.error("Could not find NASDAQ-100 constituents table")
        return []

    # Normalize column names
    col_map = {}
    for c in df.columns:
        cl = str(c).lower()
        if 'ticker' in cl:
            col_map[c] = 'ticker'
        elif 'company' in cl:
            col_map[c] = 'company'
        elif 'industry' in cl or 'sector' in cl:
            if 'subsector' not in cl:
                col_map[c] = 'sector'

    # If no sector column found, try to find any industry-like column
    if 'sector' not in col_map.values():
        for c in df.columns:
            cl = str(c).lower()
            if 'industry' in cl or 'icb' in cl:
                col_map[c] = 'sector'
                break

    df = df.rename(columns=col_map)

    results = []
    for _, row in df.iterrows():
        sym = clean_symbol(row.get('ticker', ''))
        if not sym:
            continue
        results.append({
            "symbol": sym,
            "ibkr_symbol": sym,
            "ibkr_exchange": "ISLAND",
            "name": clean_name(row.get('company', '')),
            "sector": clean_sector(row.get('sector', 'Unknown')),
            "index": "NDX100",
            "currency": "USD",
            "display_exchange": "NASDAQ",
        })

    log.info(f"NASDAQ-100: parsed {len(results)} constituents")
    return results


# ── Main assembly ─────────────────────────────────────────────────────────────

def build_universe() -> dict:
    """Build the complete universe database."""

    # Parse all indices
    ftse100 = parse_ftse100()
    ftse250 = parse_ftse250()
    sp500 = parse_sp500()
    ndx100 = parse_nasdaq100()

    # ── Organize by exchange ──────────────────────────────────────────────────

    # LSE: combine FTSE 100 + FTSE 250, deduplicate by symbol
    lse_tickers = {}
    for stock in ftse100 + ftse250:
        sym = stock["symbol"]
        if sym in lse_tickers:
            # If in both indices, prefer FTSE100 label but note dual membership
            existing = lse_tickers[sym]
            if existing["index"] != stock["index"]:
                existing["index"] = f"{existing['index']},{stock['index']}"
        else:
            lse_tickers[sym] = stock

    # US: combine S&P 500 + NASDAQ-100, deduplicate, note dual membership
    nyse_tickers = {}
    nasdaq_tickers = {}

    # Process S&P 500 first
    for stock in sp500:
        sym = stock["symbol"]
        exchange = stock.get("display_exchange", "NYSE")
        if exchange == "NASDAQ":
            nasdaq_tickers[sym] = stock
        else:
            nyse_tickers[sym] = stock

    # Process NASDAQ-100 — these are all NASDAQ
    for stock in ndx100:
        sym = stock["symbol"]
        if sym in nasdaq_tickers:
            # Already there from S&P 500, note dual membership
            existing = nasdaq_tickers[sym]
            if "NDX100" not in existing["index"]:
                existing["index"] = f"{existing['index']},NDX100"
        elif sym in nyse_tickers:
            # Was classified as NYSE from S&P 500, but NASDAQ-100 says NASDAQ
            # NASDAQ-100 is authoritative for exchange — move it
            moved = nyse_tickers.pop(sym)
            moved["ibkr_exchange"] = "ISLAND"
            moved["display_exchange"] = "NASDAQ"
            moved["index"] = f"{moved['index']},NDX100"
            nasdaq_tickers[sym] = moved
        else:
            nasdaq_tickers[sym] = stock

    # ── Build output structure ────────────────────────────────────────────────

    # Clean up: remove display_exchange helper field
    def finalize(tickers_dict: dict) -> list:
        result = []
        for stock in sorted(tickers_dict.values(), key=lambda x: x["symbol"]):
            entry = {k: v for k, v in stock.items() if k != "display_exchange"}
            result.append(entry)
        return result

    lse_list = finalize(lse_tickers)
    nyse_list = finalize(nyse_tickers)
    nasdaq_list = finalize(nasdaq_tickers)

    total = len(lse_list) + len(nyse_list) + len(nasdaq_list)

    universe = {
        "metadata": {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generated_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "total": total,
            "sources": {
                "FTSE100": URLS["ftse100"],
                "FTSE250": URLS["ftse250"],
                "SP500": URLS["sp500"],
                "NDX100": URLS["nasdaq100"],
            },
        },
        "exchanges": {
            "LSE": {
                "count": len(lse_list),
                "currency": "GBP",
                "tickers": lse_list,
            },
            "NYSE": {
                "count": len(nyse_list),
                "currency": "USD",
                "tickers": nyse_list,
            },
            "NASDAQ": {
                "count": len(nasdaq_list),
                "currency": "USD",
                "tickers": nasdaq_list,
            },
        },
    }

    return universe


def print_summary(universe: dict) -> None:
    """Print summary statistics."""
    meta = universe["metadata"]
    exchanges = universe["exchanges"]

    print("\n" + "=" * 70)
    print("STOCK UNIVERSE DATABASE — SUMMARY")
    print("=" * 70)
    print(f"Generated:  {meta['generated']}")
    print(f"Total:      {meta['total']} tickers")
    print()

    for exch_name, exch_data in exchanges.items():
        tickers = exch_data["tickers"]
        print(f"  {exch_name:10s}  {exch_data['count']:4d} tickers  ({exch_data['currency']})")

        # Index breakdown
        index_counts = {}
        for t in tickers:
            for idx in t["index"].split(","):
                index_counts[idx] = index_counts.get(idx, 0) + 1
        for idx, cnt in sorted(index_counts.items()):
            print(f"    └─ {idx:12s} {cnt:4d}")

        # Sector breakdown
        sector_counts = {}
        for t in tickers:
            sector_counts[t["sector"]] = sector_counts.get(t["sector"], 0) + 1
        print(f"    └─ Sectors: {len(sector_counts)}")

    # Dual-listed summary
    dual = sum(
        1 for exch in exchanges.values()
        for t in exch["tickers"]
        if "," in t["index"]
    )
    print(f"\n  Dual-index memberships: {dual}")

    # IBKR dotted symbols (LSE)
    dotted = [
        t for t in exchanges["LSE"]["tickers"]
        if t["ibkr_symbol"].endswith(".")
    ]
    if dotted:
        print(f"\n  LSE dotted IBKR symbols ({len(dotted)}):")
        for t in dotted:
            print(f"    {t['symbol']:10s} → IBKR: {t['ibkr_symbol']}")

    print("\n" + "=" * 70)


def main():
    log.info("Building stock universe database...")

    universe = build_universe()

    # Save to JSON
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(universe, f, indent=2, ensure_ascii=False)
    log.info(f"Saved universe to: {OUTPUT_PATH}")

    print_summary(universe)

    # Also print file size
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"\nOutput file: {OUTPUT_PATH}")
    print(f"File size:   {size_kb:.1f} KB")

    return 0


if __name__ == "__main__":
    sys.exit(main())
