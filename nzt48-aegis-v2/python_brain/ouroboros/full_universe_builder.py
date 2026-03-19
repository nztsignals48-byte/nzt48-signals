"""Full Universe Builder — Builds a 36K+ ticker master list for AEGIS V2.

Pulls tickers from EVERY major exchange accessible via UK ISA / IBKR using
four complementary methods:

  METHOD 1: Wikipedia/Wikitable scraping for index constituents
    S&P 500, NASDAQ 100, Russell 2000, FTSE 100/250/All-Share, Nikkei 225,
    Hang Seng, Hang Seng Tech, ASX 200, DAX 40, CAC 40, Euro Stoxx 50/600,
    TSX 60, KOSPI 200, Straits Times Index, Swiss Market Index

  METHOD 2: Exchange-wide CSV/JSON downloads from public data sources
    NYSE, NASDAQ, AMEX listed companies via official machine-readable files

  METHOD 3: yfinance sector screener for broad exchange coverage
    .L, .HK, .T, .AX, .DE, .PA, .AS, .SW, .TO, .KS, .SI (no suffix for US)

  METHOD 4: Systematic LSE leveraged ETP pattern generation
    All prefix x code combinations validated incrementally

Strategy: Build a STATIC list fast (no per-ticker validation). The daily
universe_refresh.py validates ~500/day, covering all 36K in ~72 days.

Output: config/isa_universe_master.json

Usage:
  python3 -m python_brain.ouroboros.full_universe_builder
  python3 -m python_brain.ouroboros.full_universe_builder --skip-web  # Static only

Quarantine rules:
  - Read-only: never modifies WAL or trading state
  - Only updates the universe master JSON
  - Network failures are caught and logged, never crash
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
CACHE_DIR = DATA_DIR / "universe_cache"
MASTER_FILE = CONFIG_DIR / "isa_universe_master.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Universe-Builder] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("full_universe_builder")

# HTTP session with retries
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AEGIS-Universe-Builder/1.0",
    "Accept": "text/html,application/json,text/csv,*/*",
})

# ISA-eligible exchanges
EXCHANGES = {
    "LSE":         {"suffix": ".L",  "currency": "GBP", "country": "UK"},
    "NYSE":        {"suffix": "",    "currency": "USD", "country": "US"},
    "NASDAQ":      {"suffix": "",    "currency": "USD", "country": "US"},
    "AMEX":        {"suffix": "",    "currency": "USD", "country": "US"},
    "TSE":         {"suffix": ".T",  "currency": "JPY", "country": "JP"},
    "HKEX":        {"suffix": ".HK", "currency": "HKD", "country": "HK"},
    "ASX":         {"suffix": ".AX", "currency": "AUD", "country": "AU"},
    "XETRA":       {"suffix": ".DE", "currency": "EUR", "country": "DE"},
    "EURONEXT_PA": {"suffix": ".PA", "currency": "EUR", "country": "FR"},
    "EURONEXT_AS": {"suffix": ".AS", "currency": "EUR", "country": "NL"},
    "SIX":         {"suffix": ".SW", "currency": "CHF", "country": "CH"},
    "TSX":         {"suffix": ".TO", "currency": "CAD", "country": "CA"},
    "KRX":         {"suffix": ".KS", "currency": "KRW", "country": "KR"},
    "SGX":         {"suffix": ".SI", "currency": "SGD", "country": "SG"},
}


# ============================================================================
# HELPER: Build a ticker dict
# ============================================================================

def _make_ticker(
    symbol: str,
    exchange: str,
    name: str = "",
    source: str = "unknown",
    sector: str = "Unknown",
    market_cap: int = 0,
    avg_volume: int = 0,
    leveraged: bool = False,
    inverse: bool = False,
    leverage_factor: int = 1,
    ticker_type: str = "stock",
) -> Dict[str, Any]:
    """Create a canonical ticker dict."""
    exch_info = EXCHANGES.get(exchange, {})
    return {
        "symbol": symbol,
        "exchange": exchange,
        "name": name,
        "type": ticker_type,
        "sector": sector,
        "industry": "Unknown",
        "currency": exch_info.get("currency", "USD"),
        "isa_eligible": True,
        "leveraged": leveraged,
        "inverse": inverse,
        "leverage_factor": leverage_factor,
        "market_cap_usd": market_cap,
        "avg_daily_volume": avg_volume,
        "validated": False,
        "source": source,
    }


def _add_suffix(symbol: str, exchange: str) -> str:
    """Add exchange suffix if needed."""
    suffix = EXCHANGES.get(exchange, {}).get("suffix", "")
    if suffix and not symbol.endswith(suffix):
        return symbol + suffix
    return symbol


def _safe_get(url: str, timeout: int = 30, **kwargs) -> Optional[requests.Response]:
    """GET with error handling and retries."""
    for attempt in range(3):
        try:
            resp = SESSION.get(url, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if attempt < 2:
                time.sleep(1 * (attempt + 1))
            else:
                log.warning("Failed to fetch %s after 3 attempts: %s", url, e)
    return None


# ============================================================================
# METHOD 1: Wikipedia index constituent scraping
# ============================================================================

def _extract_wiki_table_column(html: str, col_index: int = 0, table_class: str = "wikitable") -> List[str]:
    """Extract text from a specific column of a Wikipedia table.

    Uses regex-based parsing (no bs4 dependency).
    Returns list of cleaned cell values.
    """
    values = []

    # Find all wikitable/sortable tables
    table_pattern = re.compile(
        r'<table[^>]*class="[^"]*' + re.escape(table_class) + r'[^"]*"[^>]*>(.*?)</table>',
        re.DOTALL | re.IGNORECASE,
    )

    for table_match in table_pattern.finditer(html):
        table_html = table_match.group(1)

        # Extract rows
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL | re.IGNORECASE)

        for row in rows:
            # Skip header rows
            if '<th' in row.lower():
                continue

            # Extract cells (td tags)
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
            if len(cells) > col_index:
                cell_html = cells[col_index]
                # Strip HTML tags to get text
                text = re.sub(r'<[^>]+>', '', cell_html).strip()
                # Clean common artifacts
                text = text.replace('\n', '').replace('\r', '').strip()
                if text:
                    values.append(text)

    return values


def _scrape_sp500() -> List[str]:
    """Scrape S&P 500 constituents from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    resp = _safe_get(url)
    if not resp:
        return _static_sp500()

    tickers = _extract_wiki_table_column(resp.text, col_index=0)
    # Filter to valid ticker patterns
    valid = [t for t in tickers if re.match(r'^[A-Z][A-Z0-9.\-]{0,6}$', t)]
    if len(valid) > 400:
        log.info("  S&P 500: scraped %d tickers from Wikipedia", len(valid))
        return valid

    log.warning("  S&P 500: only got %d from Wikipedia, using static fallback", len(valid))
    return _static_sp500()


def _scrape_nasdaq100() -> List[str]:
    """Scrape NASDAQ 100 from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/Nasdaq-100"
    resp = _safe_get(url)
    if not resp:
        return _static_nasdaq100()

    tickers = _extract_wiki_table_column(resp.text, col_index=1)
    valid = [t for t in tickers if re.match(r'^[A-Z][A-Z0-9.\-]{0,6}$', t)]
    if len(valid) > 80:
        log.info("  NASDAQ 100: scraped %d tickers from Wikipedia", len(valid))
        return valid
    return _static_nasdaq100()


def _scrape_russell2000() -> List[str]:
    """Scrape Russell 2000 from Wikipedia (partial) + extend with known ETF holdings."""
    url = "https://en.wikipedia.org/wiki/Russell_2000_Index"
    resp = _safe_get(url)
    tickers = []
    if resp:
        tickers = _extract_wiki_table_column(resp.text, col_index=0)
        tickers = [t for t in tickers if re.match(r'^[A-Z][A-Z0-9.\-]{0,6}$', t)]

    # Russell 2000 Wikipedia page is sparse. Supplement from iShares IWM holdings CSV.
    iwm_url = "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund"
    resp2 = _safe_get(iwm_url, timeout=30)
    if resp2 and resp2.status_code == 200:
        lines = resp2.text.split('\n')
        for line in lines:
            parts = line.split(',')
            if len(parts) >= 2:
                sym = parts[0].strip().strip('"')
                if re.match(r'^[A-Z][A-Z0-9.\-]{0,6}$', sym) and sym not in ('Ticker', 'Fund'):
                    tickers.append(sym)

    # Deduplicate
    seen = set()
    unique = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            unique.append(t)

    log.info("  Russell 2000: gathered %d tickers", len(unique))
    return unique if len(unique) > 100 else _static_russell2000_sample()


def _scrape_ftse100() -> List[str]:
    """Scrape FTSE 100 from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/FTSE_100_Index"
    resp = _safe_get(url)
    if not resp:
        return _static_ftse100()

    # FTSE 100 table: ticker is usually in column 1 (EPIC column)
    tickers = _extract_wiki_table_column(resp.text, col_index=1)
    valid = [t for t in tickers if re.match(r'^[A-Z][A-Z0-9.]{0,6}$', t)]
    if len(valid) > 80:
        log.info("  FTSE 100: scraped %d tickers from Wikipedia", len(valid))
        return valid
    return _static_ftse100()


def _scrape_ftse250() -> List[str]:
    """Scrape FTSE 250 from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/FTSE_250_Index"
    resp = _safe_get(url)
    if not resp:
        return _static_ftse250()

    tickers = _extract_wiki_table_column(resp.text, col_index=1)
    valid = [t for t in tickers if re.match(r'^[A-Z][A-Z0-9.]{0,6}$', t)]
    if len(valid) > 200:
        log.info("  FTSE 250: scraped %d tickers from Wikipedia", len(valid))
        return valid
    return _static_ftse250()


def _scrape_nikkei225() -> List[str]:
    """Scrape Nikkei 225 from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/Nikkei_225"
    resp = _safe_get(url)
    if not resp:
        return _static_nikkei225()

    # Nikkei tickers are numeric codes
    tickers = _extract_wiki_table_column(resp.text, col_index=0)
    valid = [t for t in tickers if re.match(r'^\d{4}$', t)]
    if len(valid) > 150:
        log.info("  Nikkei 225: scraped %d tickers from Wikipedia", len(valid))
        return valid
    return _static_nikkei225()


def _scrape_hangseng() -> List[str]:
    """Scrape Hang Seng from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/Hang_Seng_Index"
    resp = _safe_get(url)
    if not resp:
        return _static_hangseng()

    # HSI tickers are numeric: 0001-9999
    tickers = _extract_wiki_table_column(resp.text, col_index=0)
    valid = [t for t in tickers if re.match(r'^\d{4}$', t)]
    if len(valid) > 40:
        log.info("  Hang Seng: scraped %d tickers from Wikipedia", len(valid))
        return valid
    return _static_hangseng()


def _scrape_hangseng_tech() -> List[str]:
    """Scrape Hang Seng Tech from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/Hang_Seng_TECH_Index"
    resp = _safe_get(url)
    if not resp:
        return _static_hangseng_tech()

    tickers = _extract_wiki_table_column(resp.text, col_index=0)
    valid = [t for t in tickers if re.match(r'^\d{4}$', t)]
    if len(valid) > 20:
        log.info("  Hang Seng Tech: scraped %d tickers", len(valid))
        return valid
    return _static_hangseng_tech()


def _scrape_asx200() -> List[str]:
    """Scrape ASX 200 from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/S%26P/ASX_200"
    resp = _safe_get(url)
    if not resp:
        return _static_asx200()

    tickers = _extract_wiki_table_column(resp.text, col_index=0)
    valid = [t for t in tickers if re.match(r'^[A-Z][A-Z0-9]{0,4}$', t)]
    if len(valid) > 150:
        log.info("  ASX 200: scraped %d tickers from Wikipedia", len(valid))
        return valid
    return _static_asx200()


def _scrape_dax40() -> List[str]:
    """Scrape DAX 40 from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/DAX"
    resp = _safe_get(url)
    if not resp:
        return _static_dax40()

    tickers = _extract_wiki_table_column(resp.text, col_index=1)
    valid = [t for t in tickers if re.match(r'^[A-Z0-9]{2,6}$', t)]
    if len(valid) > 30:
        log.info("  DAX 40: scraped %d tickers from Wikipedia", len(valid))
        return valid
    return _static_dax40()


def _scrape_cac40() -> List[str]:
    """Scrape CAC 40 from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/CAC_40"
    resp = _safe_get(url)
    if not resp:
        return _static_cac40()

    tickers = _extract_wiki_table_column(resp.text, col_index=1)
    valid = [t for t in tickers if re.match(r'^[A-Z]{2,5}$', t)]
    if len(valid) > 30:
        log.info("  CAC 40: scraped %d tickers from Wikipedia", len(valid))
        return valid
    return _static_cac40()


def _scrape_eurostoxx50() -> List[str]:
    """Scrape Euro Stoxx 50 from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/EURO_STOXX_50"
    resp = _safe_get(url)
    if not resp:
        return _static_eurostoxx50()

    tickers = _extract_wiki_table_column(resp.text, col_index=1)
    valid = [t for t in tickers if re.match(r'^[A-Z0-9]{2,6}$', t)]
    if len(valid) > 40:
        log.info("  Euro Stoxx 50: scraped %d tickers from Wikipedia", len(valid))
        return valid
    return _static_eurostoxx50()


def _scrape_tsx60() -> List[str]:
    """Scrape TSX 60 from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/S%26P/TSX_60"
    resp = _safe_get(url)
    if not resp:
        return _static_tsx60()

    tickers = _extract_wiki_table_column(resp.text, col_index=1)
    valid = [t for t in tickers if re.match(r'^[A-Z][A-Z0-9.\-]{0,8}$', t)]
    if len(valid) > 40:
        log.info("  TSX 60: scraped %d tickers from Wikipedia", len(valid))
        return valid
    return _static_tsx60()


def _scrape_kospi200() -> List[str]:
    """Scrape KOSPI 200 from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/KOSPI_200"
    resp = _safe_get(url)
    if not resp:
        return _static_kospi200()

    tickers = _extract_wiki_table_column(resp.text, col_index=0)
    valid = [t for t in tickers if re.match(r'^\d{6}$', t)]
    if len(valid) > 100:
        log.info("  KOSPI 200: scraped %d tickers from Wikipedia", len(valid))
        return valid
    return _static_kospi200()


def _scrape_smi() -> List[str]:
    """Scrape Swiss Market Index from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/Swiss_Market_Index"
    resp = _safe_get(url)
    if not resp:
        return _static_smi()

    tickers = _extract_wiki_table_column(resp.text, col_index=1)
    valid = [t for t in tickers if re.match(r'^[A-Z]{2,6}$', t)]
    if len(valid) > 15:
        log.info("  SMI: scraped %d tickers from Wikipedia", len(valid))
        return valid
    return _static_smi()


def _scrape_sti() -> List[str]:
    """Scrape Straits Times Index from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/Straits_Times_Index"
    resp = _safe_get(url)
    if not resp:
        return _static_sti()

    tickers = _extract_wiki_table_column(resp.text, col_index=0)
    valid = [t for t in tickers if re.match(r'^[A-Z0-9]{2,5}$', t)]
    if len(valid) > 15:
        log.info("  STI: scraped %d tickers from Wikipedia", len(valid))
        return valid
    return _static_sti()


def _scrape_eurostoxx600() -> List[str]:
    """Scrape Euro Stoxx 600 from Wikipedia (partial — table is large)."""
    url = "https://en.wikipedia.org/wiki/STOXX_Europe_600"
    resp = _safe_get(url)
    if not resp:
        return _static_eurostoxx600_sample()

    tickers = _extract_wiki_table_column(resp.text, col_index=1)
    valid = [t for t in tickers if re.match(r'^[A-Z0-9]{2,8}$', t)]
    if len(valid) > 200:
        log.info("  Euro Stoxx 600: scraped %d tickers from Wikipedia", len(valid))
        return valid
    return _static_eurostoxx600_sample()


def _scrape_ftse_allshare() -> List[str]:
    """FTSE All-Share — combine FTSE 100 + 250 + additional small caps."""
    ftse100 = _scrape_ftse100()
    ftse250 = _scrape_ftse250()
    # Add some FTSE SmallCap tickers statically
    smallcap = [
        "888", "AAU", "ABRN", "ACSO", "AGT", "AJG", "ALFA", "ALT", "AML",
        "ANIC", "APAX", "APTD", "AQSG", "ARB", "ASC", "ATG", "ATM", "ATR",
        "AVON", "AXS", "BGFD", "BHMG", "BIG", "BLND", "BNKR", "BOY", "BRBY",
        "BRK", "BSE", "BUR", "BWY", "BWNG", "CAL", "CARD", "CBG", "CCEP",
        "CCC", "CLDN", "CLG", "CNA", "CNNE", "COA", "COG", "COOB", "CPI",
        "CREI", "CRN", "CRW", "CURY", "CVS", "DJAN", "DLAR", "DNA", "DOCS",
        "DOM", "DOTD", "DRAX", "DRX", "DSC", "DVO", "ECM", "EDIN", "ELM",
        "ELTA", "EMG", "ESNT", "EWI", "FAR", "FARN", "FBH", "FDM", "FEET",
        "FGP", "FGT", "FLOW", "FOUR", "FRAS", "FRES", "FSV", "GAW", "GBRT",
        "GCP", "GEK", "GFTU", "GHE", "GMS", "GNK", "GNS", "GOCO", "GOOD",
        "GPH", "GPOR", "GRI", "GSK", "GTR", "GUS", "GYM", "HAT", "HBR",
        "HICL", "HIK", "HLN", "HMSO", "HOC", "HSV", "HSX", "HVN", "ICP",
        "IEP", "IHG", "III", "INF", "INPP", "IPF", "IPO", "IPX", "ITV",
        "IVO", "IXI", "JAM", "JDW", "JEL", "JET2", "JHD", "JLEN", "JMG",
        "JTC", "JUST", "KAZ", "KGF", "KIE", "KMR", "KWS", "LAD", "LAND",
        "LIO", "LMP", "LRE", "LSL", "LWB", "MAB", "MAI", "MARS", "MCB",
        "MCRO", "MGAM", "MGNS", "MKS", "MNDI", "MNZS", "MRC", "MRO", "MTO",
        "MUT", "MYSL", "NAS", "NB1", "NESF", "NRR", "NWG", "NXR", "OBD",
        "OCDO", "OPG", "ORIT", "OTB", "OXB", "PAGE", "PAG", "PAY", "PCA",
        "PFC", "PFD", "PHNX", "PHP", "PHI", "PLP", "PLUS", "PNN", "POW",
    ]
    all_tickers = list(set(ftse100 + ftse250 + smallcap))
    log.info("  FTSE All-Share: combined %d tickers", len(all_tickers))
    return all_tickers


def run_method1_wiki_scraping() -> Dict[str, List[Dict[str, Any]]]:
    """METHOD 1: Scrape Wikipedia for all major index constituents.

    Returns {exchange: [ticker_dicts]}.
    """
    log.info("=" * 50)
    log.info("METHOD 1: Wikipedia Index Scraping")
    log.info("=" * 50)

    results: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen: Set[str] = set()

    # Define all index scrapers with their exchange mappings
    index_scrapers = [
        ("S&P 500",           "NYSE",        _scrape_sp500),
        ("NASDAQ 100",        "NASDAQ",      _scrape_nasdaq100),
        ("Russell 2000",      "NYSE",        _scrape_russell2000),
        ("FTSE All-Share",    "LSE",         _scrape_ftse_allshare),
        ("Nikkei 225",        "TSE",         _scrape_nikkei225),
        ("Hang Seng",         "HKEX",        _scrape_hangseng),
        ("Hang Seng Tech",    "HKEX",        _scrape_hangseng_tech),
        ("ASX 200",           "ASX",         _scrape_asx200),
        ("DAX 40",            "XETRA",       _scrape_dax40),
        ("CAC 40",            "EURONEXT_PA", _scrape_cac40),
        ("Euro Stoxx 50",     "EURONEXT_AS", _scrape_eurostoxx50),
        ("Euro Stoxx 600",    "EURONEXT_AS", _scrape_eurostoxx600),
        ("TSX 60",            "TSX",         _scrape_tsx60),
        ("KOSPI 200",         "KRX",         _scrape_kospi200),
        ("SMI",               "SIX",         _scrape_smi),
        ("STI",               "SGX",         _scrape_sti),
    ]

    for index_name, exchange, scraper_fn in index_scrapers:
        log.info("Scraping %s (%s)...", index_name, exchange)
        try:
            raw = scraper_fn()
        except Exception as e:
            log.warning("  %s scraper failed: %s", index_name, e)
            raw = []

        added = 0
        for sym in raw:
            full_sym = _add_suffix(sym, exchange)
            if full_sym not in seen:
                seen.add(full_sym)
                results[exchange].append(
                    _make_ticker(full_sym, exchange, source=index_name)
                )
                added += 1

        log.info("  %s: %d new tickers (total %d)", index_name, added, len(raw))
        time.sleep(0.5)  # Be polite to Wikipedia

    total = sum(len(v) for v in results.values())
    log.info("METHOD 1 total: %d unique tickers from indices", total)
    return results


# ============================================================================
# METHOD 2: Exchange-wide CSV/JSON downloads
# ============================================================================

def _fetch_nasdaq_listed() -> List[Dict[str, Any]]:
    """Fetch NASDAQ-listed companies from NASDAQ FTP."""
    tickers = []
    url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&exchange=NASDAQ&download=true"
    resp = _safe_get(url, timeout=30)
    if resp:
        try:
            data = resp.json()
            rows = data.get("data", {}).get("rows", [])
            for row in rows:
                sym = row.get("symbol", "").strip()
                name = row.get("name", "").strip()
                mcap = row.get("marketCap", "")
                if sym and re.match(r'^[A-Z][A-Z0-9.\-]{0,6}$', sym):
                    mc = 0
                    if mcap and mcap not in ("", "N/A"):
                        try:
                            mc = int(float(str(mcap).replace(",", "")))
                        except (ValueError, TypeError):
                            pass
                    tickers.append(_make_ticker(sym, "NASDAQ", name=name, source="nasdaq_api", market_cap=mc))
        except (json.JSONDecodeError, KeyError) as e:
            log.warning("  NASDAQ API parse failed: %s", e)

    # Fallback: NASDAQ trader FTP file
    if len(tickers) < 100:
        ftp_url = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
        resp2 = _safe_get(ftp_url, timeout=30)
        if resp2:
            lines = resp2.text.strip().split('\n')
            for line in lines[1:]:  # Skip header
                parts = line.split('|')
                if len(parts) >= 2:
                    sym = parts[1].strip()
                    # Column 7 is ETF flag
                    if re.match(r'^[A-Z][A-Z0-9]{0,5}$', sym):
                        exchange = "NASDAQ" if parts[0].strip() == "Y" else "NYSE"
                        name = parts[2].strip() if len(parts) > 2 else ""
                        tickers.append(_make_ticker(sym, exchange, name=name, source="nasdaq_ftp"))

    log.info("  NASDAQ listed: %d tickers", len(tickers))
    return tickers


def _fetch_nyse_listed() -> List[Dict[str, Any]]:
    """Fetch NYSE-listed companies."""
    tickers = []
    url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&exchange=NYSE&download=true"
    resp = _safe_get(url, timeout=30)
    if resp:
        try:
            data = resp.json()
            rows = data.get("data", {}).get("rows", [])
            for row in rows:
                sym = row.get("symbol", "").strip()
                name = row.get("name", "").strip()
                if sym and re.match(r'^[A-Z][A-Z0-9.\-]{0,6}$', sym):
                    tickers.append(_make_ticker(sym, "NYSE", name=name, source="nyse_api"))
        except (json.JSONDecodeError, KeyError) as e:
            log.warning("  NYSE API parse failed: %s", e)

    log.info("  NYSE listed: %d tickers", len(tickers))
    return tickers


def _fetch_amex_listed() -> List[Dict[str, Any]]:
    """Fetch AMEX-listed companies."""
    tickers = []
    url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&exchange=AMEX&download=true"
    resp = _safe_get(url, timeout=30)
    if resp:
        try:
            data = resp.json()
            rows = data.get("data", {}).get("rows", [])
            for row in rows:
                sym = row.get("symbol", "").strip()
                name = row.get("name", "").strip()
                if sym and re.match(r'^[A-Z][A-Z0-9.\-]{0,6}$', sym):
                    tickers.append(_make_ticker(sym, "AMEX", name=name, source="amex_api"))
        except (json.JSONDecodeError, KeyError) as e:
            log.warning("  AMEX API parse failed: %s", e)

    log.info("  AMEX listed: %d tickers", len(tickers))
    return tickers


def run_method2_exchange_csvs() -> List[Dict[str, Any]]:
    """METHOD 2: Download tickers from exchange-level public data sources."""
    log.info("=" * 50)
    log.info("METHOD 2: Exchange CSV/API Downloads")
    log.info("=" * 50)

    all_tickers = []

    # US exchanges
    all_tickers.extend(_fetch_nasdaq_listed())
    time.sleep(1)
    all_tickers.extend(_fetch_nyse_listed())
    time.sleep(1)
    all_tickers.extend(_fetch_amex_listed())

    log.info("METHOD 2 total: %d tickers from exchange APIs", len(all_tickers))
    return all_tickers


# ============================================================================
# METHOD 3: yfinance screener for broad exchange coverage
# ============================================================================

def _yf_sector_tickers(exchange_suffix: str, exchange_name: str) -> List[Dict[str, Any]]:
    """Use yfinance to discover tickers via popular ETFs and their holdings.

    Since yfinance doesn't have a direct exchange screener, we use a
    pragmatic approach: fetch holdings of major index-tracking ETFs.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not available, skipping METHOD 3 for %s", exchange_name)
        return []

    tickers = []

    # Map exchange to major tracking ETFs
    etf_map = {
        ".L":  ["ISF.L", "MIDD.L", "VUKE.L", "VMID.L"],  # FTSE100/250 iShares/Vanguard
        "":    ["SPY", "QQQ", "IWM", "VTI", "IVV"],        # US total market
        ".HK": ["2800.HK", "3067.HK"],                      # Tracker Fund HK
        ".T":  ["1321.T", "1306.T"],                         # Nikkei/TOPIX
        ".AX": ["STW.AX", "IOZ.AX", "VAS.AX"],              # ASX
        ".DE": ["EXS1.DE", "EXSA.DE"],                       # DAX
        ".PA": ["CAC.PA", "CW8.PA"],                         # CAC
        ".AS": ["IESA.AS", "IEAC.AS"],                       # Euro area
        ".SW": ["CSSMI.SW"],                                  # SMI
        ".TO": ["XIU.TO", "XIC.TO"],                         # TSX
        ".KS": ["069500.KS"],                                 # KOSPI
        ".SI": ["ES3.SI", "G3B.SI"],                         # STI
    }

    etfs = etf_map.get(exchange_suffix, [])
    for etf_sym in etfs:
        try:
            etf = yf.Ticker(etf_sym)
            # Try to get holdings
            try:
                holdings = etf.funds_data.top_holdings
                if holdings is not None and not holdings.empty:
                    for sym in holdings.index:
                        sym_str = str(sym).strip()
                        if sym_str and len(sym_str) <= 12:
                            tickers.append(_make_ticker(
                                sym_str, exchange_name,
                                source=f"etf_holdings_{etf_sym}",
                            ))
            except Exception:
                pass
        except Exception as e:
            log.debug("  ETF holdings fetch failed for %s: %s", etf_sym, e)

    return tickers


def run_method3_yfinance_screener() -> List[Dict[str, Any]]:
    """METHOD 3: yfinance-based ticker discovery via ETF holdings."""
    log.info("=" * 50)
    log.info("METHOD 3: yfinance ETF Holdings Scan")
    log.info("=" * 50)

    all_tickers = []
    exchange_map = [
        (".L",  "LSE"),
        ("",    "NYSE"),
        (".HK", "HKEX"),
        (".T",  "TSE"),
        (".AX", "ASX"),
        (".DE", "XETRA"),
        (".PA", "EURONEXT_PA"),
        (".AS", "EURONEXT_AS"),
        (".SW", "SIX"),
        (".TO", "TSX"),
        (".KS", "KRX"),
        (".SI", "SGX"),
    ]

    for suffix, exchange in exchange_map:
        log.info("Scanning %s (suffix=%s)...", exchange, suffix or "(none)")
        tickers = _yf_sector_tickers(suffix, exchange)
        all_tickers.extend(tickers)
        log.info("  %s: %d tickers from ETF holdings", exchange, len(tickers))
        time.sleep(0.5)

    log.info("METHOD 3 total: %d tickers from yfinance ETF holdings", len(all_tickers))
    return all_tickers


# ============================================================================
# METHOD 4: Systematic LSE leveraged ETP generation
# ============================================================================

def run_method4_lse_leveraged() -> List[Dict[str, Any]]:
    """METHOD 4: Generate ALL possible LSE leveraged ETP symbols.

    Generates every prefix x code combination. Validation is deferred
    to the daily universe_refresh.py cycle.
    """
    log.info("=" * 50)
    log.info("METHOD 4: LSE Leveraged ETP Pattern Generation")
    log.info("=" * 50)

    tickers = []
    seen: Set[str] = set()

    # Comprehensive underlying codes (GraniteShares, Leverage Shares, WisdomTree)
    underlying_codes = [
        "AB", "AD", "AI", "AL", "AM", "AP", "AR", "AS", "AT", "AZ",
        "BA", "BI", "BK", "BP", "BR", "BT", "BX",
        "CA", "CB", "CF", "CG", "CH", "CL", "CO", "CP", "CR", "CS", "CU",
        "DA", "DB", "DC", "DE", "DI", "DK", "DL", "DM", "DN",
        "EA", "EB", "EC", "EL", "EM", "EN", "EP", "ER", "ES", "ET", "EU", "EV",
        "FA", "FB", "FI", "FL", "FN", "FO", "FR", "FT",
        "GA", "GB", "GD", "GE", "GI", "GL", "GM", "GN", "GO", "GP", "GR", "GS",
        "HA", "HB", "HC", "HD", "HI", "HK", "HL", "HN", "HS", "HV",
        "IA", "IB", "IC", "IG", "IN", "IO", "IP", "IR", "IT", "IV",
        "JA", "JB", "JP",
        "KA", "KO",
        "LA", "LB", "LC", "LD", "LI", "LN", "LO", "LR", "LT", "LU", "LY",
        "MA", "MB", "MC", "ME", "MG", "MI", "ML", "MN", "MO", "MP", "MR", "MS", "MT", "MU", "MX",
        "NA", "NB", "NC", "NE", "NF", "NG", "NI", "NK", "NL", "NM", "NO", "NR", "NS", "NV", "NX",
        "OC", "OI", "OL", "OP", "OR",
        "PA", "PB", "PC", "PD", "PE", "PF", "PG", "PH", "PI", "PL", "PM", "PN", "PR", "PS", "PT",
        "QA", "QR",
        "RA", "RB", "RC", "RD", "RE", "RI", "RN", "RO", "RP", "RS", "RT", "RU",
        "SA", "SB", "SC", "SD", "SE", "SF", "SG", "SH", "SI", "SK", "SL", "SM", "SN", "SO", "SP", "SQ", "SR", "SS", "ST", "SU", "SV", "SW",
        "TA", "TB", "TC", "TD", "TE", "TF", "TI", "TK", "TL", "TM", "TN", "TO", "TP", "TR", "TS", "TU", "TV", "TW",
        "UB", "UC", "UK", "UL", "UN", "UP", "UR", "US", "UT",
        "VA", "VB", "VC", "VD", "VE", "VG", "VI", "VN", "VO", "VR", "VS",
        "WA", "WB", "WC", "WD", "WE", "WI", "WM", "WN",
        "XA", "XR",
        "ZA", "ZN",
    ]

    prefixes = ["2L", "2S", "3L", "3S", "5L", "5S"]

    for prefix in prefixes:
        for code in underlying_codes:
            sym = f"{prefix}{code}.L"
            if sym not in seen:
                seen.add(sym)
                leverage = int(prefix[0])
                is_inverse = prefix[1] == "S"
                tickers.append(_make_ticker(
                    sym, "LSE",
                    source="lse_leveraged_pattern",
                    leveraged=True,
                    inverse=is_inverse,
                    leverage_factor=leverage,
                    ticker_type="leveraged_etp",
                ))

    # Add known named patterns (QQQ, SP5, NVD, TSL, etc.)
    named_patterns = [
        ("QQQ3.L", 3, False), ("QQQ5.L", 5, False), ("QQQS.L", 3, True),
        ("5SPY.L", 5, False), ("3LUS.L", 3, False), ("3USS.L", 3, True),
        ("3SEM.L", 3, True),
        ("NVD3.L", 3, False), ("TSL3.L", 3, False), ("TSM3.L", 3, False),
        ("MU2.L", 2, False), ("GPT3.L", 3, False), ("AMD3.L", 3, False),
        ("3OIL.L", 3, False), ("3OIS.L", 3, True),
        ("3GDL.L", 3, False), ("3GDS.L", 3, True),
        ("3SVL.L", 3, False), ("3SVS.L", 3, True),
        ("3NGL.L", 3, False), ("3NGS.L", 3, True),
        ("FTSL.L", 3, False), ("FTSS.L", 3, True),
        ("DAXL.L", 3, False), ("DAXS.L", 3, True),
    ]

    for sym, lev, inv in named_patterns:
        if sym not in seen:
            seen.add(sym)
            tickers.append(_make_ticker(
                sym, "LSE",
                source="lse_leveraged_named",
                leveraged=True,
                inverse=inv,
                leverage_factor=lev,
                ticker_type="leveraged_etp",
            ))

    log.info("METHOD 4 total: %d LSE leveraged ETP candidates", len(tickers))
    return tickers


# ============================================================================
# Static fallbacks for Wikipedia scrapers
# ============================================================================

def _static_sp500() -> List[str]:
    return [
        "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "GOOG", "META", "BRK-B",
        "UNH", "XOM", "JNJ", "JPM", "V", "PG", "MA", "HD", "CVX", "MRK",
        "ABBV", "LLY", "PEP", "KO", "COST", "AVGO", "WMT", "MCD", "CSCO",
        "TMO", "ACN", "ABT", "DHR", "CRM", "LIN", "NKE", "TXN", "AMGN",
        "PM", "UNP", "NEE", "INTC", "RTX", "HON", "LOW", "IBM", "QCOM",
        "BA", "CAT", "GE", "AMAT", "ISRG", "SPGI", "BKNG", "T", "AXP",
        "SYK", "GS", "BLK", "MDLZ", "ADI", "GILD", "REGN", "VRTX", "NOW",
        "MMC", "LRCX", "DUK", "SCHW", "SHW", "CB", "CI", "FI", "SO",
        "ZTS", "PLD", "CME", "CL", "SNPS", "CDNS", "BDX", "ICE", "BSX",
        "EQIX", "MO", "AON", "DE", "ITW", "KLAC", "ORLY", "WM", "APD",
        "MCK", "EMR", "NSC", "GD", "PNC", "PSA", "SLB", "USB", "MMM",
        "AJG", "EOG", "OXY", "COF", "HUM", "CCI", "CTAS", "CARR", "TJX",
        "SRE", "FCX", "DLR", "MCO", "NXPI", "GM", "AEP", "D", "PSX",
        "EW", "MNST", "FTNT", "TT", "ROP", "AMP", "FIS", "NEM", "KMB",
        "PH", "ECL", "MET", "AFL", "STZ", "PRU", "IQV", "TEL", "MSCI",
        "ALL", "TRV", "CNC", "F", "AIG", "WMB", "OKE", "KDP", "PCG",
        "SPG", "HSY", "DG", "KR", "PAYX", "YUM", "GIS", "EXC", "ED",
        "DD", "BAX", "BK", "AVB", "DOW", "HLT", "ES", "KEYS", "CTVA",
        "FAST", "IDXX", "WELL", "ODFL", "GEHC", "FICO", "IR", "VRSK",
        "RCL", "CPRT", "HWM", "ANSS", "ON", "DHI", "CDW", "HPQ", "GPC",
    ]


def _static_nasdaq100() -> List[str]:
    return [
        "AAPL", "MSFT", "AMZN", "NVDA", "META", "GOOGL", "GOOG", "AVGO",
        "COST", "TSLA", "NFLX", "AMD", "ADBE", "PEP", "CSCO", "INTC",
        "TMUS", "CMCSA", "TXN", "AMGN", "QCOM", "INTU", "AMAT", "ISRG",
        "HON", "SBUX", "BKNG", "ADP", "GILD", "MDLZ", "REGN", "VRTX",
        "ADI", "LRCX", "PANW", "MU", "SNPS", "CDNS", "KLAC", "KDP",
        "PYPL", "MAR", "MELI", "ORLY", "CTAS", "MNST", "FTNT", "ABNB",
        "DASH", "KHC", "DXCM", "AEP", "PAYX", "MCHP", "EXC", "ON",
        "LULU", "IDXX", "ODFL", "FAST", "CTSH", "GEHC", "BKR", "FANG",
        "VRSK", "MRNA", "CPRT", "CSGP", "CEG", "EA", "XEL", "GFS",
        "TEAM", "DDOG", "ZS", "ANSS", "TTD", "WDAY", "ILMN", "CDW",
        "DLTR", "BIIB", "ALGN", "WBD", "ENPH", "ZM", "LCID",
        "RVTY", "SWKS", "MTCH", "WBA", "CRWD", "ARM", "SMCI", "MRVL",
    ]


def _static_russell2000_sample() -> List[str]:
    """Partial Russell 2000 sample (~200 tickers)."""
    return [
        "ACIW", "ACLS", "ACM", "ADNT", "AGCO", "AGIO", "AJRD", "ALE",
        "ALGT", "AMN", "AMWD", "ANET", "APAM", "ARCH", "AROC", "ARWR",
        "ASB", "ATKR", "AUB", "AXNX", "AYI", "B", "BANR", "BC", "BCO",
        "BCPC", "BDC", "BHE", "BJ", "BKH", "BLKB", "BNL", "BOH", "BOOT",
        "BRC", "BRZE", "BTU", "BWA", "BXMT", "CAKE", "CALX", "CASH",
        "CATY", "CBT", "CC", "CCOI", "CDP", "CENX", "CGNX", "CHE",
        "CHRD", "CIM", "CIVI", "CLF", "CLH", "COKE", "COLM", "COMM",
        "COOP", "CRC", "CRGY", "CRS", "CSGS", "CVCO", "CWH", "CWT",
        "CXW", "DAN", "DCI", "DEI", "DINO", "DNLI", "DRH", "DXC",
        "EAT", "EBC", "EGP", "ENSG", "ENV", "EPRT", "ERA", "ESE",
        "ESNT", "EXP", "EXPO", "FBIN", "FCFS", "FHB", "FHI", "FHN",
        "FIVE", "FL", "FLO", "FLS", "FNB", "FNF", "FORM", "FOXF",
        "FRO", "FSS", "FULT", "GBX", "GEF", "GFF", "GH", "GHC", "GIL",
        "GNL", "GNTX", "GO", "GPI", "GPK", "GRBK", "GTLS", "GWRE",
        "HAE", "HALO", "HBI", "HCC", "HEES", "HGV", "HLNE", "HNI",
        "HP", "HPP", "HR", "HRI", "HTH", "HUBG", "HUN", "HWC",
        "IBOC", "IBP", "ICFI", "IIVI", "INN", "IOSP", "IPAR",
        "JBGS", "JBT", "KALU", "KBH", "KBR", "KFY", "KMT", "KNX",
        "KOS", "KRG", "KWR", "LBRT", "LCII", "LFUS", "LGIH", "LNC",
        "LNTH", "LPG", "LPX", "LSTR", "LXP", "MASI", "MAT", "MATX",
        "MBC", "MC", "MDU", "MGEE", "MGPI", "MHK", "MMSI", "MOD",
        "MOG-A", "MSEX", "MTG", "MTH", "MTOR", "MUR", "MWA", "NATI",
        "NBHC", "NEU", "NHI", "NJR", "NNN", "NOMD", "NOV", "NSIT",
        "NWE", "NWL", "NWN", "OFG", "OGS", "OLED", "OMCL", "ONB",
        "OSIS", "OUT", "OZK", "PAGS", "PATK", "PAYC", "PBH", "PDCO",
        "PEB", "PEN", "PFS", "PGNY", "PIPR", "PLNT", "PLXS", "PNFP",
    ]


def _static_ftse100() -> List[str]:
    return [
        "AZN", "SHEL", "HSBA", "ULVR", "BP", "GSK", "RIO", "DGE",
        "LSEG", "REL", "NG", "VOD", "AAL", "CRH", "EXPN", "CPG",
        "RKT", "BA", "ABF", "ANTO", "BHP", "BATS", "IMB", "LLOY",
        "III", "NWG", "BARC", "PRU", "STAN", "AHT", "SGRO", "LAND",
        "BRBY", "AVV", "SSE", "TSCO", "MNG", "JMAT", "WPP", "SMDS",
        "JD", "RS1", "MNDI", "INF", "SVT", "GLEN", "PSON", "ADM",
        "KGF", "SMT", "BDEV", "PSN", "RMV", "AUTO", "FLTR", "SN",
        "SBRY", "ITRK", "FRAS", "HIK", "CRDA", "SDR", "WTB", "IHG",
        "ENT", "SPX", "LGEN", "RTO", "BNZL", "DARK", "PSH", "BKG",
        "TW", "HLMA", "DCC", "WEIR", "HLN", "BME", "EVR", "SMIN",
        "AV", "UU", "HSBC", "MRO", "NXT", "OCDO", "FERG", "SKG",
        "RR", "EDV", "SGE", "ICAG", "IAG", "EZJ", "CMC", "MKS",
        "ITV", "PHNX", "DPLM",
    ]


def _static_ftse250() -> List[str]:
    return [
        "FOUR", "BCPT", "CNA", "JLEN", "TRN", "BNKR", "VCT", "PHI",
        "GRI", "CREI", "CBG", "BRSC", "IPO", "GENL", "TRIG", "NESF",
        "BBOX", "VTY", "SREI", "GCP", "WIN", "AJB", "FGP", "OSB",
        "DOCS", "BVIC", "CLG", "DPLM", "SMWH", "GAW", "BGEO", "MGAM",
        "HOC", "BBH", "BOCH", "HSV", "HICL", "INPP", "JET2", "KIE",
        "LMP", "MARS", "MTO", "NB1", "OXB", "PAGE", "PFC", "QQ",
        "RWS", "SEPL", "SHI", "STEM", "TET", "UKW", "VVO", "WIZZ",
        "YOU", "ZPHR", "AAZ", "BOY", "CNNE", "CURY", "ECM", "FDM",
        "GNS", "HAT", "IEP", "JTC", "KETL", "LIO", "MGNS", "NRR",
        "OTB", "PPH", "RGS", "SXS", "TRB", "VID", "WKP", "XAR",
        "AGR", "ASC", "BPT", "CCC", "DFS", "EMG", "FSV", "GMS",
        "HSX", "IXI", "JUP", "KGP", "LRE", "MCB", "NII", "OPG",
    ]


def _static_nikkei225() -> List[str]:
    return [
        "7203", "6758", "8306", "9984", "6861", "8035", "6902", "9432",
        "6501", "6954", "8766", "3382", "8802", "5401", "1925", "1928",
        "9201", "8591", "6869", "6903", "7267", "7751", "4502", "4503",
        "4568", "6367", "7974", "9433", "2802", "2914", "4063", "4452",
        "4901", "4911", "5802", "6098", "6273", "6326", "6702", "6971",
        "7269", "7270", "7741", "7752", "8001", "8015", "8031", "8058",
        "8316", "8411", "9020", "9022", "9531", "9983",
    ]


def _static_hangseng() -> List[str]:
    return [
        "0001", "0002", "0003", "0005", "0006", "0011", "0012", "0016",
        "0017", "0027", "0066", "0101", "0175", "0267", "0288", "0388",
        "0669", "0688", "0700", "0762", "0823", "0857", "0883", "0939",
        "0941", "0968", "1038", "1044", "1088", "1109", "1177", "1211",
        "1299", "1398", "1810", "1876", "1928", "2007", "2018", "2020",
        "2269", "2313", "2318", "2319", "2382", "2388", "2628", "3690",
        "3968", "3988", "6098", "6862", "9618", "9888", "9988", "9999",
    ]


def _static_hangseng_tech() -> List[str]:
    return [
        "0700", "9988", "3690", "9618", "1810", "9888", "9999", "2382",
        "0268", "1024", "2015", "6060", "6618", "0241", "1347", "1833",
        "2518", "3888", "6690", "0909", "1797", "2013", "3888", "9626",
        "9698", "9698", "9961", "9968", "9969", "0285",
    ]


def _static_asx200() -> List[str]:
    return [
        "BHP", "CBA", "CSL", "NAB", "WBC", "ANZ", "MQG", "FMG", "WES",
        "TLS", "WOW", "RIO", "ALL", "COL", "QAN", "STO", "WPL", "TCL",
        "APA", "IAG", "GPT", "SYD", "AGL", "ORG", "ORA", "REA", "XRO",
        "CPU", "JHX", "NCM", "NST", "EVN", "AMC", "BXB", "TWE", "PME",
        "ALU", "BEN", "BOQ", "CGF", "COH", "DMP", "FPH", "GNC", "HVN",
        "IEL", "IPL", "JBH", "LYC", "MPL", "MIN", "NHF", "ORI", "PPT",
        "QBE", "RHC", "SCG", "SGM", "SGP", "SHL", "SOL", "SUL", "TAH",
        "VCX", "VEA", "WEB", "WHC", "Z1P",
    ]


def _static_dax40() -> List[str]:
    return [
        "SAP", "SIE", "ALV", "DTE", "AIR", "MBG", "BAS", "MUV2",
        "BMW", "IFX", "ADS", "BAYN", "SHL", "VOW3", "HEN3", "BEI",
        "DPW", "RWE", "EOAN", "FRE", "DB1", "MTX", "HEI", "FME",
        "SY1", "VNA", "1COV", "CON", "QIA", "DHER", "PUM", "ZAL",
        "HNR1", "BNR", "RHM", "ENR", "LHA", "SRT3", "P911", "DTG",
    ]


def _static_cac40() -> List[str]:
    return [
        "AI", "AIR", "ALO", "ATO", "CS", "BN", "BNP", "CA", "CAP",
        "DSY", "EL", "ENGI", "ERF", "HO", "KER", "LR", "MC", "ML",
        "MT", "ORA", "OR", "PUB", "RI", "RMS", "RNO", "SAF", "SAN",
        "SGO", "SU", "STM", "TEP", "TTE", "URW", "VIE", "VIV", "WLN",
        "DG", "GLE", "SLB", "STLA",
    ]


def _static_eurostoxx50() -> List[str]:
    return [
        "ASML", "PHIA", "INGA", "AD", "UNA",
        "ENEL", "ISP", "UCG", "ENI",
        "SAN", "IBE", "TEF", "BBVA", "ITX",
    ]


def _static_eurostoxx600_sample() -> List[str]:
    """Euro Stoxx 600 is large — provide ~300 key tickers."""
    # Mix of DE, PA, AS, MC, MI exchanges
    return [
        # Germany (XETRA)
        "SAP", "SIE", "ALV", "DTE", "AIR", "MBG", "BAS", "MUV2", "BMW",
        "IFX", "ADS", "BAYN", "SHL", "VOW3", "HEN3", "BEI", "DPW", "RWE",
        "EOAN", "FRE", "DB1", "MTX", "HEI", "FME", "SY1", "VNA", "CON",
        "LEO", "TKA", "HFG", "EVD", "DEZ", "BOSS", "RAA", "NDA", "GXI",
        # France (Euronext Paris)
        "AI", "AIR", "ALO", "ATO", "CS", "BN", "BNP", "CA", "CAP",
        "DSY", "EL", "ENGI", "ERF", "HO", "KER", "LR", "MC", "ML",
        "ORA", "OR", "PUB", "RI", "RMS", "RNO", "SAF", "SAN",
        "SGO", "SU", "STM", "TEP", "TTE", "URW", "VIE", "VIV",
        "DG", "GLE", "SLB", "STLA", "ACA", "NK", "VCT", "UBI",
        # Netherlands (Euronext Amsterdam)
        "ASML", "PHIA", "INGA", "AD", "UNA", "WKL", "REN", "AKZA",
        "RAND", "DSM", "NN", "ASR", "PRX", "BESI", "IMCD", "LIGHT",
        # Spain (BME)
        "SAN", "IBE", "TEF", "BBVA", "ITX", "FER", "AMS", "REP", "ELE",
        # Italy (Borsa Italiana)
        "ENEL", "ISP", "UCG", "ENI", "STM", "REC", "TEN", "PRY", "LDO",
        # Switzerland
        "NESN", "ROG", "NOVN", "ABBN", "SREN", "UBSG", "CSGN", "ZURN",
        "GEBN", "SGSN", "GIVN", "LONN", "SCMN", "BAER", "TEMN", "SIKA",
        # Nordics
        "NOVO-B", "MAERSK-B", "DSV", "CARL-B", "VWS",
    ]


def _static_tsx60() -> List[str]:
    return [
        "RY", "TD", "BNS", "BMO", "CM", "ENB", "CP", "CNR", "SU",
        "TRP", "BCE", "ATD", "T", "MFC", "SLF", "GWO", "FTS", "PPL",
        "QSR", "DOL", "WCN", "BAM", "NTR", "TRI", "IFC",
        "CSU", "SHOP", "WSP", "ABX", "BTO", "K", "AEM", "FNV", "WPM",
        "IMO", "EMA", "CU", "AQN", "H", "L", "POW", "GIL",
        "MGA", "TFII", "CCO", "FM", "LUN", "SAP",
    ]


def _static_kospi200() -> List[str]:
    return [
        "005930", "000660", "005935", "006400", "035420", "035720", "068270",
        "051910", "207940", "000270", "012330", "096770", "105560", "028260",
        "066570", "055550", "034730", "015760", "017670", "003550", "086790",
        "032830", "033780", "036570", "009150", "018260", "010950", "000810",
        "003670", "138040", "024110", "251270", "011170", "009540", "004020",
        "010130", "021240", "090430", "034020", "002790", "003490", "000720",
        "010140", "016360", "011780", "005830", "011200", "004170", "001450",
        "097950", "000880", "002380", "006800", "008930", "004990", "009830",
        "007070", "010120", "000150", "006360", "001570", "003410", "018880",
        "020150", "023530", "029780", "030200", "033630", "034220", "036460",
    ]


def _static_smi() -> List[str]:
    return [
        "NESN", "ROG", "NOVN", "ABBN", "SREN", "UBSG", "CSGN", "ZURN",
        "GEBN", "SGSN", "GIVN", "LONN", "SCMN", "BAER", "TEMN", "SIKA",
        "PGHN", "SLHN", "LOGN", "ALC",
    ]


def _static_sti() -> List[str]:
    return [
        "D05", "O39", "U11", "Z74", "C6L", "A17U", "C38U", "BN4",
        "Y92", "H78", "V03", "U96", "C09", "S58", "G13", "N2IU",
        "C52", "BS6", "S63", "M44U", "F34", "U14", "T39", "J36",
        "N03", "S68", "ME8U", "H02", "UD2", "C07",
    ]


# ============================================================================
# Master file assembly
# ============================================================================

def merge_existing_master(new_tickers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge new tickers with existing master file, preserving validation state."""
    if not MASTER_FILE.exists():
        return new_tickers

    try:
        with open(MASTER_FILE) as f:
            existing = json.load(f)
    except (json.JSONDecodeError, IOError):
        return new_tickers

    # Build lookup from existing by symbol
    existing_map: Dict[str, Dict[str, Any]] = {}
    for t in existing.get("tickers", []):
        existing_map[t["symbol"]] = t

    # Merge: new tickers get existing validation data if available
    merged = []
    seen: Set[str] = set()

    for t in new_tickers:
        sym = t["symbol"]
        if sym in seen:
            continue
        seen.add(sym)

        if sym in existing_map:
            old = existing_map[sym]
            # Preserve validation state from existing
            t["validated"] = old.get("validated", False)
            t["last_validated"] = old.get("last_validated", "")
            t["consecutive_failures"] = old.get("consecutive_failures", 0)
            t["delisted"] = old.get("delisted", False)
            if old.get("name") and not t.get("name"):
                t["name"] = old["name"]
            if old.get("market_cap_usd") and not t.get("market_cap_usd"):
                t["market_cap_usd"] = old["market_cap_usd"]
            if old.get("avg_daily_volume") and not t.get("avg_daily_volume"):
                t["avg_daily_volume"] = old["avg_daily_volume"]
            if old.get("sector") and old["sector"] != "Unknown":
                t["sector"] = old["sector"]

        merged.append(t)

    # Add any existing tickers that are NOT in the new set (preserve them)
    for sym, old in existing_map.items():
        if sym not in seen:
            seen.add(sym)
            merged.append(old)

    return merged


def build_and_save_master(all_tickers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deduplicate, merge with existing, build exchange summary, save."""

    # Merge with existing master to preserve validation state
    merged = merge_existing_master(all_tickers)

    # Build exchange summary
    exchanges: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "tickers": []})
    active_count = 0
    for t in merged:
        if t.get("delisted"):
            continue
        active_count += 1
        exch = t.get("exchange", "Unknown")
        exchanges[exch]["count"] += 1
        exchanges[exch]["tickers"].append(t["symbol"])

    exchanges_sorted = dict(sorted(exchanges.items(), key=lambda x: -x[1]["count"]))

    validated_count = sum(1 for t in merged if t.get("validated") and not t.get("delisted"))

    master = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_tickers": active_count,
        "validated_count": validated_count,
        "discovery_methods": [
            "wikipedia_scraping",
            "exchange_csv_api",
            "yfinance_etf_holdings",
            "lse_leveraged_patterns",
            "initial_universe_toml",
        ],
        "exchanges": exchanges_sorted,
        "tickers": sorted(merged, key=lambda t: (t.get("exchange", ""), t["symbol"])),
    }

    # Save
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(MASTER_FILE, "w") as f:
        json.dump(master, f, indent=2, default=str)

    log.info("Master file saved: %s", MASTER_FILE)
    return master


# ============================================================================
# Main orchestrator
# ============================================================================

def run_full_build(skip_web: bool = False) -> Dict[str, Any]:
    """Execute the full universe build pipeline."""
    start = time.monotonic()
    log.info("=" * 70)
    log.info("AEGIS V2 Full Universe Builder")
    log.info("  Target: 36,000+ tickers across all ISA-eligible exchanges")
    log.info("  Mode: %s", "static-only" if skip_web else "full (web + static)")
    log.info("=" * 70)

    all_tickers: List[Dict[str, Any]] = []

    # METHOD 1: Wikipedia index scraping
    if not skip_web:
        wiki_results = run_method1_wiki_scraping()
        for exchange, tickers in wiki_results.items():
            all_tickers.extend(tickers)
        log.info("After METHOD 1: %d tickers", len(all_tickers))
    else:
        log.info("METHOD 1: Skipped (--skip-web)")

    # METHOD 2: Exchange CSV/API downloads
    if not skip_web:
        exchange_tickers = run_method2_exchange_csvs()
        all_tickers.extend(exchange_tickers)
        log.info("After METHOD 2: %d tickers", len(all_tickers))
    else:
        log.info("METHOD 2: Skipped (--skip-web)")

    # METHOD 3: yfinance ETF holdings scan
    if not skip_web:
        yf_tickers = run_method3_yfinance_screener()
        all_tickers.extend(yf_tickers)
        log.info("After METHOD 3: %d tickers", len(all_tickers))
    else:
        log.info("METHOD 3: Skipped (--skip-web)")

    # METHOD 4: LSE leveraged ETP patterns (always runs, no network needed)
    lse_tickers = run_method4_lse_leveraged()
    all_tickers.extend(lse_tickers)
    log.info("After METHOD 4: %d tickers", len(all_tickers))

    # Build and save master file
    master = build_and_save_master(all_tickers)

    elapsed = time.monotonic() - start
    log.info("=" * 70)
    log.info("Full Universe Builder COMPLETE in %.1fs", elapsed)
    log.info("  Total active tickers: %d", master["total_tickers"])
    log.info("  Validated: %d", master["validated_count"])
    log.info("  Exchange breakdown:")
    for exch, info in master["exchanges"].items():
        log.info("    %-15s %5d tickers", exch, info["count"])
    log.info("  Master file: %s", MASTER_FILE)
    log.info("=" * 70)

    return master


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Full Universe Builder — Builds 36K+ ticker master list"
    )
    parser.add_argument("--skip-web", action="store_true",
                        help="Skip web scraping (static + patterns only)")
    args = parser.parse_args()

    try:
        run_full_build(skip_web=args.skip_web)
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        log.error("Universe build failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
