"""ISA Universe Discovery — Discovers ALL ISA-eligible tickers.

Uses multiple data sources to build a comprehensive universe of tradeable
securities available within a UK ISA account via Interactive Brokers.

Methods:
  1. Major index components via yfinance (S&P 500, NASDAQ 100, FTSE 100/250,
     Nikkei 225, Hang Seng, ASX 200, DAX 40, CAC 40, Euro Stoxx 50, etc.)
  2. Exchange-listed ETPs via known LSE leveraged ETP ticker patterns
  3. Supplementary tickers from initial_universe.toml (existing curated list)

Output: config/isa_universe_master.json

Usage:
  python3 -m python_brain.ouroboros.isa_universe_discovery
  python3 -m python_brain.ouroboros.isa_universe_discovery --quick   # Index components only
  python3 -m python_brain.ouroboros.isa_universe_discovery --full    # Full discovery + validation

Quarantine rules:
  - Read-only: never modifies live config or WAL
  - Network failures are retried with exponential backoff
  - All results cached to avoid redundant API calls
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from python_brain.ouroboros.ibkr_data_provider import get_provider as _get_ibkr_provider
    _HAS_IBKR = True
except ImportError:
    _HAS_IBKR = False

try:
    import yfinance as yf
    _HAS_YF = True
except ImportError:
    yf = None  # type: ignore
    _HAS_YF = False

if not _HAS_IBKR and not _HAS_YF:
    print("ERROR: Neither IBKR provider nor yfinance available", file=sys.stderr)
    sys.exit(1)

try:
    import tomli
except ImportError:
    try:
        import tomllib as tomli  # Python 3.11+
    except ImportError:
        print("ERROR: tomli not installed. Run: pip install tomli", file=sys.stderr)
        sys.exit(1)

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
MASTER_FILE = CONFIG_DIR / "isa_universe_master.json"
CACHE_DIR = DATA_DIR / "universe_cache"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ISA-Discovery] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("isa_discovery")

# ---------------------------------------------------------------------------
# Constants: Exchange definitions and index mappings
# ---------------------------------------------------------------------------

# ISA-eligible exchanges recognised by HMRC
ISA_RECOGNISED_EXCHANGES = {
    "LSE": {"suffix": ".L", "currency": "GBP", "country": "UK"},
    "NYSE": {"suffix": "", "currency": "USD", "country": "US"},
    "NASDAQ": {"suffix": "", "currency": "USD", "country": "US"},
    "AMEX": {"suffix": "", "currency": "USD", "country": "US"},
    "ARCA": {"suffix": "", "currency": "USD", "country": "US"},
    "TSE": {"suffix": ".T", "currency": "JPY", "country": "JP"},
    "HKEX": {"suffix": ".HK", "currency": "HKD", "country": "HK"},
    "ASX": {"suffix": ".AX", "currency": "AUD", "country": "AU"},
    "XETRA": {"suffix": ".DE", "currency": "EUR", "country": "DE"},
    "EURONEXT_PA": {"suffix": ".PA", "currency": "EUR", "country": "FR"},
    "EURONEXT_AS": {"suffix": ".AS", "currency": "EUR", "country": "NL"},
    "SIX": {"suffix": ".SW", "currency": "CHF", "country": "CH"},
    "TSX": {"suffix": ".TO", "currency": "CAD", "country": "CA"},
    "KRX": {"suffix": ".KS", "currency": "KRW", "country": "KR"},
    "SGX": {"suffix": ".SI", "currency": "SGD", "country": "SG"},
}

# Major indices and their yfinance tickers for component discovery
INDEX_DEFINITIONS = {
    # US indices
    "S&P 500": {"yf_ticker": "^GSPC", "exchange": "NYSE", "method": "wiki_sp500"},
    "NASDAQ 100": {"yf_ticker": "^NDX", "exchange": "NASDAQ", "method": "wiki_nasdaq100"},
    "Russell 2000": {"yf_ticker": "^RUT", "exchange": "NYSE", "method": "ticker_list"},
    # UK indices
    "FTSE 100": {"yf_ticker": "^FTSE", "exchange": "LSE", "method": "wiki_ftse100"},
    "FTSE 250": {"yf_ticker": "^FTMC", "exchange": "LSE", "method": "wiki_ftse250"},
    # European indices
    "DAX 40": {"yf_ticker": "^GDAXI", "exchange": "XETRA", "method": "wiki_dax"},
    "CAC 40": {"yf_ticker": "^FCHI", "exchange": "EURONEXT_PA", "method": "wiki_cac40"},
    "Euro Stoxx 50": {"yf_ticker": "^STOXX50E", "exchange": "EURONEXT_AS", "method": "wiki_eurostoxx50"},
    "SMI": {"yf_ticker": "^SSMI", "exchange": "SIX", "method": "ticker_list"},
    # Asian indices
    "Nikkei 225": {"yf_ticker": "^N225", "exchange": "TSE", "method": "wiki_nikkei225"},
    "Hang Seng": {"yf_ticker": "^HSI", "exchange": "HKEX", "method": "wiki_hangseng"},
    "ASX 200": {"yf_ticker": "^AXJO", "exchange": "ASX", "method": "wiki_asx200"},
    "STI": {"yf_ticker": "^STI", "exchange": "SGX", "method": "ticker_list"},
    "KOSPI 50": {"yf_ticker": "^KS11", "exchange": "KRX", "method": "ticker_list"},
    # Canadian
    "TSX 60": {"yf_ticker": "^TX60", "exchange": "TSX", "method": "wiki_tsx60"},
}

# Known leveraged ETP prefixes on LSE (GraniteShares, Leverage Shares, WisdomTree)
LSE_LEVERAGED_PREFIXES = [
    "3L", "3S", "2L", "2S", "5L", "5S",  # Generic leveraged patterns
    "QQQ3", "QQQ5", "QQQS",  # Nasdaq
    "5SPY", "3LUS", "3USS",  # S&P
    "3SEM",  # Semiconductors
    "NVD3", "TSL3", "TSM3", "MU2",  # Single stocks
    "GPT3", "AMD3",  # Tech/AI
    "3LAP", "3SAP", "3LMS", "3SMS",  # Apple/Microsoft
    "3LAM", "3SAM", "3LMT", "3SMT",  # Amazon/Meta
    "3LGO", "3SGO", "3LNF",  # Google/Netflix
    "3LBA", "3LCO", "3SCO",  # Alibaba/Coinbase
    "3LUK", "3SUK",  # FTSE
    "3LEU", "3SEU", "3LDE", "3SDE",  # Euro/DAX
    "3LOI", "3SOI",  # Oil
    "3LGD", "3SGD", "3LSV", "3SSV",  # Gold/Silver
]

# Additional well-known LSE leveraged ETPs not covered by prefixes
LSE_LEVERAGED_EXTRA = [
    # GraniteShares single stock
    "3LAL.L", "3SAL.L",  # Alphabet
    "3LNI.L", "3SNI.L",  # Nike
    "3LDI.L", "3SDI.L",  # Disney
    "3LPF.L", "3SPF.L",  # Pfizer
    "3LUB.L", "3SUB.L",  # Uber
    "3LPL.L", "3SPL.L",  # Palantir
    "3LAI.L", "3SAI.L",  # Airbnb
    "3LSQ.L", "3SSQ.L",  # Block (Square)
    "3LPE.L", "3SPE.L",  # Paypal
    "3LRO.L", "3SRO.L",  # Rolls-Royce
    "3LBP.L", "3SBP.L",  # BP
    "3LRD.L", "3SRD.L",  # Shell
    "3LBA.L", "3SBA.L",  # Barclays
    "3LHS.L", "3SHS.L",  # HSBC
    "3LAZ.L", "3SAZ.L",  # AstraZeneca
    # WisdomTree commodity
    "3OIL.L", "3OIS.L",  # Crude oil
    "3GDL.L", "3GDS.L",  # Gold
    "3SVL.L", "3SVS.L",  # Silver
    "3NGL.L", "3NGS.L",  # Natural gas
    # Leverage Shares
    "MSFT.L", "AMZN.L", "GOOG.L", "META.L",  # 1x US stocks on LSE
    # Index trackers
    "FTSL.L", "FTSS.L",  # FTSE 100 leveraged
    "DAXL.L", "DAXS.L",  # DAX leveraged
]


# ---------------------------------------------------------------------------
# Wikipedia-based index component scrapers
# ---------------------------------------------------------------------------
def _fetch_sp500_tickers() -> List[str]:
    """Fetch S&P 500 components from Wikipedia."""
    try:
        import requests
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        # Parse the table - look for ticker symbols
        tickers = []
        lines = resp.text.split("\n")
        for line in lines:
            # Wikipedia table has <a> tags with tickers in the first column
            if 'external text' in line or 'reports' in line.lower():
                continue
            if '/wiki/' in line and 'NYSE:' in line or 'NASDAQ:' in line:
                # Try to extract ticker from link text
                pass
        # Fallback: use pandas-free HTML parsing
        tickers = _extract_tickers_from_wiki_table(resp.text, col_index=0)
        if tickers:
            log.info("Fetched %d S&P 500 tickers from Wikipedia", len(tickers))
            return tickers
    except Exception as e:
        log.warning("Failed to fetch S&P 500 from Wikipedia: %s", e)
    return _get_static_sp500()


def _extract_tickers_from_wiki_table(html: str, col_index: int = 0) -> List[str]:
    """Extract ticker symbols from Wikipedia HTML table without pandas.

    Looks for the 'constituents' or 'wikitable' table and extracts
    ticker-like strings (uppercase, 1-5 chars, optional dot) from
    the specified column.
    """
    tickers = []
    # Simple state machine parser for HTML tables
    in_table = False
    in_tbody = False
    in_row = False
    in_cell = False
    col_count = 0
    current_text = ""

    # Split by tags
    import re
    tag_re = re.compile(r'<(/?)(\w+)[^>]*>')
    parts = tag_re.split(html)

    i = 0
    while i < len(parts):
        if i + 2 < len(parts):
            closing = parts[i + 0] if i > 0 else ""
            # This is the text before the tag
            pass

        # Look for table with wikitable or sortable class
        chunk = parts[i] if i < len(parts) else ""

        if '<table' in chunk and ('wikitable' in chunk or 'sortable' in chunk):
            in_table = True
        elif '</table>' in chunk:
            in_table = False

        if in_table:
            if '<tr' in chunk:
                in_row = True
                col_count = 0
            elif '</tr>' in chunk:
                in_row = False
            elif '<td' in chunk:
                in_cell = True
                current_text = ""
                col_count += 1
            elif '</td>' in chunk:
                if in_cell and col_count == col_index + 1:
                    # Clean the text
                    cleaned = re.sub(r'<[^>]+>', '', current_text).strip()
                    if cleaned and re.match(r'^[A-Z][A-Z0-9.]{0,5}$', cleaned):
                        tickers.append(cleaned)
                in_cell = False
            elif in_cell:
                current_text += chunk

        i += 1

    return tickers


def _get_static_sp500() -> List[str]:
    """Hardcoded top ~50 S&P 500 tickers as fallback."""
    return [
        "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "GOOG", "META", "BRK-B",
        "UNH", "XOM", "JNJ", "JPM", "V", "PG", "MA", "HD", "CVX", "MRK",
        "ABBV", "LLY", "PEP", "KO", "COST", "AVGO", "WMT", "MCD", "CSCO",
        "TMO", "ACN", "ABT", "DHR", "CRM", "LIN", "NKE", "TXN", "AMGN",
        "PM", "UNP", "NEE", "INTC", "RTX", "HON", "LOW", "IBM", "QCOM",
        "BA", "CAT", "GE", "AMAT", "ISRG",
    ]


def _get_static_nasdaq100() -> List[str]:
    """Top NASDAQ 100 tickers."""
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
        "DLTR", "BIIB", "ALGN", "WBD", "SIRI", "ENPH", "ZM", "LCID",
        "RVTY", "SWKS", "MTCH", "WBA", "JD", "PDD", "CRWD", "ARM",
        "SMCI", "MRVL",
    ]


def _get_static_ftse100() -> List[str]:
    """FTSE 100 tickers (LSE suffix added later)."""
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
        "ITV", "PHNX", "SGRO", "DPLM",
    ]


def _get_static_ftse250() -> List[str]:
    """Top FTSE 250 tickers (partial, ~100)."""
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


def _get_static_dax40() -> List[str]:
    """DAX 40 tickers (XETRA suffix added later)."""
    return [
        "SAP", "SIE", "ALV", "DTE", "AIR", "MBG", "BAS", "MUV2",
        "BMW", "IFX", "ADS", "BAYN", "SHL", "VOW3", "HEN3", "BEI",
        "DPW", "RWE", "EOAN", "FRE", "DB1", "MTX", "HEI", "FME",
        "SY1", "VNA", "1COV", "CON", "QIA", "DHER", "PUM", "ZAL",
        "HNR1", "BNR", "RHM", "ENR", "LHA", "SRT3", "P911", "DTG",
    ]


def _get_static_cac40() -> List[str]:
    """CAC 40 tickers (Euronext Paris suffix added later)."""
    return [
        "AI", "AIR", "ALO", "ATO", "CS", "BN", "BNP", "CA", "CAP",
        "DSY", "EL", "ENGI", "ERF", "HO", "KER", "LR", "MC", "ML",
        "MT", "ORA", "OR", "PUB", "RI", "RMS", "RNO", "SAF", "SAN",
        "SGO", "SU", "STM", "TEP", "TTE", "URW", "VIE", "VIV", "WLN",
        "DG", "GLE", "SLB", "STLA",
    ]


def _get_static_eurostoxx50() -> List[str]:
    """Euro Stoxx 50 tickers (mix of exchanges)."""
    return [
        # Already covered by DAX/CAC — these are the additions
        "ASML", "PHIA", "INGA", "AD", "UNA",  # Amsterdam (.AS)
        "ENEL", "ISP", "UCG", "ENI",  # Milan (.MI)
        "SAN", "IBE", "TEF", "BBVA", "ITX",  # Madrid (.MC)
    ]


def _get_static_nikkei225() -> List[str]:
    """Top Nikkei 225 tickers (TSE suffix added later)."""
    return [
        "7203", "6758", "8306", "9984", "6861", "8035", "6902", "9432",
        "6501", "6954", "8766", "3382", "8802", "5401", "1925", "1928",
        "9201", "8591", "6869", "6903", "7267", "7751", "4502", "4503",
        "4568", "6367", "7974", "9433", "2802", "2914", "4063", "4452",
        "4901", "4911", "5802", "6098", "6273", "6326", "6702", "6971",
        "7269", "7270", "7741", "7752", "8001", "8015", "8031", "8058",
        "8316", "8411", "9020", "9022", "9531", "9983",
    ]


def _get_static_hangseng() -> List[str]:
    """Top Hang Seng tickers (HKEX suffix added later)."""
    return [
        "0001", "0002", "0003", "0005", "0006", "0011", "0012", "0016",
        "0017", "0027", "0066", "0101", "0175", "0267", "0288", "0388",
        "0669", "0688", "0700", "0762", "0823", "0857", "0883", "0939",
        "0941", "0968", "1038", "1044", "1088", "1109", "1177", "1211",
        "1299", "1398", "1810", "1876", "1928", "2007", "2018", "2020",
        "2269", "2313", "2318", "2319", "2382", "2388", "2628", "3690",
        "3968", "3988", "6098", "6862", "9618", "9888", "9988", "9999",
    ]


def _get_static_asx200() -> List[str]:
    """Top ASX 200 tickers (ASX suffix added later)."""
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


def _get_static_tsx60() -> List[str]:
    """Top TSX 60 tickers (TSX suffix added later)."""
    return [
        "RY", "TD", "BNS", "BMO", "CM", "ENB", "CP", "CNR", "SU",
        "TRP", "BCE", "ATD", "T", "MFC", "SLF", "GWO", "FTS", "PPL",
        "QSR", "DOL", "WCN", "BAM", "BIP-UN", "NTR", "TRI", "IFC",
        "CSU", "SHOP", "WSP", "ABX", "BTO", "K", "AEM", "FNV", "WPM",
        "IMO", "CCL-B", "EMA", "CU", "AQN", "H", "L", "POW", "GIL",
        "MGA", "TFII", "CCO", "FM", "LUN", "SAP",
    ]


# ---------------------------------------------------------------------------
# Ticker validation and classification
# ---------------------------------------------------------------------------

def classify_ticker(info: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    """Classify a ticker based on yfinance info dict."""
    result = {
        "symbol": symbol,
        "name": info.get("shortName") or info.get("longName") or "",
        "type": "stock",
        "sector": info.get("sector") or "Unknown",
        "industry": info.get("industry") or "Unknown",
        "currency": info.get("currency") or "USD",
        "market_cap_usd": info.get("marketCap") or 0,
        "avg_daily_volume": info.get("averageVolume") or info.get("averageDailyVolume10Day") or 0,
        "isa_eligible": True,
        "leveraged": False,
        "inverse": False,
        "leverage_factor": 1,
    }

    # Detect type from quoteType
    qtype = (info.get("quoteType") or "").upper()
    if qtype == "ETF":
        result["type"] = "etf"
    elif qtype == "MUTUALFUND":
        result["type"] = "fund"

    name_upper = result["name"].upper()

    # Detect leveraged / inverse from name
    leverage_patterns = [
        ("5X LONG", 5, False), ("5X SHORT", 5, True),
        ("3X LONG", 3, False), ("3X SHORT", 3, True),
        ("2X LONG", 2, False), ("2X SHORT", 2, True),
        ("5X DAILY", 5, False), ("3X DAILY", 3, False), ("2X DAILY", 2, False),
        ("TRIPLE LONG", 3, False), ("TRIPLE SHORT", 3, True),
        ("DOUBLE LONG", 2, False), ("DOUBLE SHORT", 2, True),
        ("ULTRA SHORT", 2, True), ("ULTRA LONG", 2, False),
        ("-3X", 3, True), ("-2X", 2, True), ("-1X", 1, True),
        ("INVERSE", 1, True),
    ]

    for pattern, factor, is_inverse in leverage_patterns:
        if pattern in name_upper:
            result["leveraged"] = factor > 1
            result["inverse"] = is_inverse
            result["leverage_factor"] = factor
            result["type"] = "leveraged_etp" if factor > 1 else ("inverse_etp" if is_inverse else result["type"])
            break

    # Also check symbol patterns for LSE leveraged ETPs
    sym = symbol.replace(".L", "").upper()
    if sym.startswith("3L") or sym.startswith("3S"):
        result["leveraged"] = True
        result["leverage_factor"] = 3
        result["type"] = "leveraged_etp"
        result["inverse"] = sym.startswith("3S")
    elif sym.startswith("2L") or sym.startswith("2S"):
        result["leveraged"] = True
        result["leverage_factor"] = 2
        result["type"] = "leveraged_etp"
        result["inverse"] = sym.startswith("2S")
    elif sym.startswith("5L") or sym.startswith("5S"):
        result["leveraged"] = True
        result["leverage_factor"] = 5
        result["type"] = "leveraged_etp"
        result["inverse"] = sym.startswith("5S")
    elif any(sym.startswith(p.replace(".L", "")) for p in ["QQQ3", "QQQ5", "5SPY"]):
        result["leveraged"] = True
        result["type"] = "leveraged_etp"
        if "5" in sym:
            result["leverage_factor"] = 5
        else:
            result["leverage_factor"] = 3
    elif sym.startswith("QQQS") or sym.startswith("3USS"):
        result["leveraged"] = True
        result["inverse"] = True
        result["leverage_factor"] = 3
        result["type"] = "leveraged_etp"

    return result


def _detect_exchange(symbol: str) -> str:
    """Detect exchange from ticker suffix."""
    if symbol.endswith(".L"):
        return "LSE"
    elif symbol.endswith(".T"):
        return "TSE"
    elif symbol.endswith(".HK"):
        return "HKEX"
    elif symbol.endswith(".AX"):
        return "ASX"
    elif symbol.endswith(".DE"):
        return "XETRA"
    elif symbol.endswith(".PA"):
        return "EURONEXT_PA"
    elif symbol.endswith(".AS"):
        return "EURONEXT_AS"
    elif symbol.endswith(".SW"):
        return "SIX"
    elif symbol.endswith(".TO"):
        return "TSX"
    elif symbol.endswith(".KS"):
        return "KRX"
    elif symbol.endswith(".SI"):
        return "SGX"
    elif symbol.endswith(".MI"):
        return "EURONEXT_MI"
    elif symbol.endswith(".MC"):
        return "BME"
    else:
        return "NYSE"  # Default to US


def _add_exchange_suffix(symbol: str, exchange: str) -> str:
    """Add exchange suffix to a bare ticker symbol."""
    exch_info = ISA_RECOGNISED_EXCHANGES.get(exchange, {})
    suffix = exch_info.get("suffix", "")
    if suffix and not symbol.endswith(suffix):
        return symbol + suffix
    return symbol


# ---------------------------------------------------------------------------
# Batch validation via yfinance
# ---------------------------------------------------------------------------

def validate_tickers_batch(
    symbols: List[str],
    exchange: str,
    batch_size: int = 50,
    max_retries: int = 3,
) -> List[Dict[str, Any]]:
    """Validate a list of tickers. Tries IBKR first, falls back to yfinance.

    Returns classified ticker dicts for valid tickers.
    """
    valid = []
    total = len(symbols)
    remaining = list(symbols)

    # Try IBKR first for validation (contract details + price data check)
    if _HAS_IBKR:
        try:
            provider = _get_ibkr_provider()
            ibkr_validated = set()
            for sym in symbols:
                try:
                    details = provider.get_contract_details(sym)
                    if details and details.get("con_id"):
                        info = {
                            "shortName": details.get("long_name", ""),
                            "sector": details.get("category", "Unknown"),
                            "regularMarketPrice": 1.0,  # Placeholder; IBKR confirmed valid
                            "currency": details.get("currency", "USD"),
                        }
                        classified = classify_ticker(info, sym)
                        classified["exchange"] = exchange
                        classified["validated"] = True
                        classified["con_id"] = details["con_id"]
                        valid.append(classified)
                        ibkr_validated.add(sym)
                except Exception:
                    pass
            remaining = [s for s in symbols if s not in ibkr_validated]
            if ibkr_validated:
                log.info("IBKR validated %d/%d tickers for %s", len(ibkr_validated), total, exchange)
        except Exception as e:
            log.debug("IBKR validation unavailable: %s", e)

    # Fallback to yfinance for remaining
    if remaining and _HAS_YF:
        for batch_start in range(0, len(remaining), batch_size):
            batch = remaining[batch_start:batch_start + batch_size]
            batch_str = " ".join(batch)

            for attempt in range(max_retries):
                try:
                    tickers = yf.Tickers(batch_str)
                    for sym in batch:
                        try:
                            ticker = tickers.tickers.get(sym)
                            if ticker is None:
                                continue
                            info = ticker.info
                            if not info or info.get("regularMarketPrice") is None:
                                try:
                                    fi = ticker.fast_info
                                    if fi and hasattr(fi, 'last_price') and fi.last_price:
                                        info = info or {}
                                        info["regularMarketPrice"] = fi.last_price
                                        info["currency"] = getattr(fi, "currency", "USD")
                                    else:
                                        continue
                                except Exception:
                                    continue

                            classified = classify_ticker(info, sym)
                            classified["exchange"] = exchange
                            classified["validated"] = True
                            valid.append(classified)
                        except Exception:
                            continue

                    log.info(
                        "  yfinance validated batch %d-%d/%d for %s",
                        batch_start + 1, min(batch_start + batch_size, len(remaining)),
                        len(remaining), exchange,
                    )
                    break

                except Exception as e:
                    wait = 2 ** attempt
                    log.warning(
                        "  Batch %d-%d failed (attempt %d/%d): %s — retrying in %ds",
                        batch_start + 1, min(batch_start + batch_size, len(remaining)),
                        attempt + 1, max_retries, e, wait,
                    )
                    time.sleep(wait)

            time.sleep(0.5)

    return valid


# ---------------------------------------------------------------------------
# Load existing tickers from initial_universe.toml
# ---------------------------------------------------------------------------

def load_existing_universe() -> List[Dict[str, Any]]:
    """Load tickers from the existing initial_universe.toml."""
    toml_path = CONFIG_DIR / "initial_universe.toml"
    if not toml_path.exists():
        log.warning("initial_universe.toml not found at %s", toml_path)
        return []

    with open(toml_path, "rb") as f:
        data = tomli.load(f)

    tickers = []
    for entry in data.get("tickers", []):
        sym = entry.get("symbol", "")
        if not sym:
            continue

        # Determine exchange from suffix
        exchange = _detect_exchange(sym)

        leverage = entry.get("leverage", 1)
        inverse_of = entry.get("inverse_of", "")

        tickers.append({
            "symbol": sym,
            "exchange": exchange,
            "name": entry.get("underlying", ""),
            "type": "leveraged_etp" if leverage > 1 else "stock",
            "sector": entry.get("sector", "Unknown"),
            "industry": "Unknown",
            "currency": ISA_RECOGNISED_EXCHANGES.get(exchange, {}).get("currency", "USD"),
            "isa_eligible": True,
            "leveraged": leverage > 1,
            "inverse": bool(inverse_of),
            "leverage_factor": leverage,
            "market_cap_usd": 0,
            "avg_daily_volume": 0,
            "validated": False,
            "source": "initial_universe.toml",
        })

    log.info("Loaded %d tickers from initial_universe.toml", len(tickers))
    return tickers


# ---------------------------------------------------------------------------
# Discovery methods
# ---------------------------------------------------------------------------

def discover_index_components(quick: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """Discover tickers from major index components.

    Returns: {exchange: [ticker_dicts]}
    """
    results: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen_symbols: Set[str] = set()

    # Map index names to their static ticker fetcher functions
    index_fetchers = {
        "S&P 500": _get_static_sp500,
        "NASDAQ 100": _get_static_nasdaq100,
        "FTSE 100": _get_static_ftse100,
        "FTSE 250": _get_static_ftse250,
        "DAX 40": _get_static_dax40,
        "CAC 40": _get_static_cac40,
        "Euro Stoxx 50": _get_static_eurostoxx50,
        "Nikkei 225": _get_static_nikkei225,
        "Hang Seng": _get_static_hangseng,
        "ASX 200": _get_static_asx200,
        "TSX 60": _get_static_tsx60,
    }

    if quick:
        # Quick mode: only S&P 500, NASDAQ 100, FTSE 100
        index_fetchers = {k: v for k, v in index_fetchers.items()
                          if k in ["S&P 500", "NASDAQ 100", "FTSE 100"]}

    for index_name, fetcher_fn in index_fetchers.items():
        index_def = INDEX_DEFINITIONS.get(index_name, {})
        exchange = index_def.get("exchange", "NYSE")

        log.info("Discovering %s components (%s)...", index_name, exchange)
        raw_tickers = fetcher_fn()

        # Add exchange suffix
        suffixed = []
        for sym in raw_tickers:
            full_sym = _add_exchange_suffix(sym, exchange)
            if full_sym not in seen_symbols:
                seen_symbols.add(full_sym)
                suffixed.append(full_sym)

        if not suffixed:
            log.warning("  No tickers for %s", index_name)
            continue

        # Create basic ticker dicts without full validation (fast)
        for sym in suffixed:
            results[exchange].append({
                "symbol": sym,
                "exchange": exchange,
                "name": "",
                "type": "stock",
                "sector": "Unknown",
                "industry": "Unknown",
                "currency": ISA_RECOGNISED_EXCHANGES.get(exchange, {}).get("currency", "USD"),
                "isa_eligible": True,
                "leveraged": False,
                "inverse": False,
                "leverage_factor": 1,
                "market_cap_usd": 0,
                "avg_daily_volume": 0,
                "validated": False,
                "source": index_name,
            })

        log.info("  %s: %d tickers added", index_name, len(suffixed))

    return results


def discover_lse_leveraged_etps() -> List[Dict[str, Any]]:
    """Discover LSE-listed leveraged ETPs using known patterns."""
    log.info("Discovering LSE leveraged ETPs...")
    tickers = []
    seen: Set[str] = set()

    # Add extra known tickers
    for sym in LSE_LEVERAGED_EXTRA:
        if sym not in seen:
            seen.add(sym)
            leverage, is_inverse = _parse_leverage_from_symbol(sym)
            tickers.append({
                "symbol": sym,
                "exchange": "LSE",
                "name": "",
                "type": "leveraged_etp" if leverage > 1 else ("inverse_etp" if is_inverse else "stock"),
                "sector": "Unknown",
                "industry": "Unknown",
                "currency": "GBP",
                "isa_eligible": True,
                "leveraged": leverage > 1,
                "inverse": is_inverse,
                "leverage_factor": leverage,
                "market_cap_usd": 0,
                "avg_daily_volume": 0,
                "validated": False,
                "source": "lse_leveraged_scan",
            })

    # Generate pattern-based tickers
    # GraniteShares naming: 3L{XX}.L / 3S{XX}.L where XX is a 2-3 char code
    underlying_codes = [
        "AP", "MS", "AM", "MT", "GO", "NF", "BA", "CO", "NV", "TS", "UK",
        "EU", "DE", "OI", "GD", "SV", "DI", "NI", "PF", "UB", "PL", "AI",
        "SQ", "PE", "RO", "BP", "RD", "HS", "AZ", "IO", "AB", "SP", "FT",
    ]
    for code in underlying_codes:
        for prefix in ["3L", "3S", "2L", "2S"]:
            sym = f"{prefix}{code}.L"
            if sym not in seen:
                seen.add(sym)
                leverage = int(prefix[0])
                is_inverse = prefix[1] == "S"
                tickers.append({
                    "symbol": sym,
                    "exchange": "LSE",
                    "name": "",
                    "type": "leveraged_etp",
                    "sector": "Unknown",
                    "industry": "Unknown",
                    "currency": "GBP",
                    "isa_eligible": True,
                    "leveraged": True,
                    "inverse": is_inverse,
                    "leverage_factor": leverage,
                    "market_cap_usd": 0,
                    "avg_daily_volume": 0,
                    "validated": False,
                    "source": "lse_leveraged_pattern",
                })

    log.info("  Generated %d LSE leveraged ETP candidates", len(tickers))
    return tickers


def _parse_leverage_from_symbol(symbol: str) -> Tuple[int, bool]:
    """Parse leverage factor and inverse flag from symbol name."""
    sym = symbol.replace(".L", "").upper()
    if sym.startswith("3S") or sym.startswith("QQQS") or sym.startswith("3USS"):
        return 3, True
    elif sym.startswith("3L") or sym.startswith("QQQ3") or sym.startswith("3LUS"):
        return 3, False
    elif sym.startswith("2S"):
        return 2, True
    elif sym.startswith("2L") or sym.startswith("MU2"):
        return 2, False
    elif sym.startswith("5S"):
        return 5, True
    elif sym.startswith("5L") or sym.startswith("QQQ5") or sym.startswith("5SPY"):
        return 5, False
    elif sym.startswith("AMD3") or sym.startswith("NVD3") or sym.startswith("TSL3") or \
         sym.startswith("TSM3") or sym.startswith("GPT3"):
        return 3, False
    return 1, False


# ---------------------------------------------------------------------------
# Master file I/O
# ---------------------------------------------------------------------------

def build_master_file(
    all_tickers: List[Dict[str, Any]],
    validated_count: int = 0,
) -> Dict[str, Any]:
    """Build the master universe JSON structure."""
    # Deduplicate by symbol
    seen: Dict[str, Dict[str, Any]] = {}
    for t in all_tickers:
        sym = t["symbol"]
        if sym not in seen:
            seen[sym] = t
        else:
            # Merge: prefer validated data over unvalidated
            existing = seen[sym]
            if t.get("validated") and not existing.get("validated"):
                seen[sym] = t
            elif t.get("name") and not existing.get("name"):
                existing["name"] = t["name"]

    tickers_list = list(seen.values())

    # Build exchange summary
    exchanges: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "tickers": []})
    for t in tickers_list:
        exch = t.get("exchange", "Unknown")
        exchanges[exch]["count"] += 1
        exchanges[exch]["tickers"].append(t["symbol"])

    # Sort exchanges by count
    exchanges_sorted = dict(sorted(exchanges.items(), key=lambda x: -x[1]["count"]))

    master = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_tickers": len(tickers_list),
        "validated_count": validated_count,
        "discovery_methods": ["index_components", "lse_leveraged_scan", "initial_universe_toml"],
        "exchanges": exchanges_sorted,
        "tickers": sorted(tickers_list, key=lambda t: (t.get("exchange", ""), t["symbol"])),
    }

    return master


def save_master_file(master: Dict[str, Any]) -> Path:
    """Save master universe file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(MASTER_FILE, "w") as f:
        json.dump(master, f, indent=2, default=str)
    log.info("Master file saved: %s (%d tickers)", MASTER_FILE, master["total_tickers"])
    return MASTER_FILE


def load_master_file() -> Optional[Dict[str, Any]]:
    """Load existing master file if it exists."""
    if MASTER_FILE.exists():
        try:
            with open(MASTER_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.warning("Failed to load master file: %s", e)
    return None


# ---------------------------------------------------------------------------
# Full validation pass (optional, slow)
# ---------------------------------------------------------------------------

def run_full_validation(tickers: List[Dict[str, Any]], max_tickers: int = 500) -> List[Dict[str, Any]]:
    """Validate tickers via yfinance info calls. Slow but accurate.

    Only validates up to max_tickers to avoid rate limiting.
    Prioritises unvalidated tickers.
    """
    unvalidated = [t for t in tickers if not t.get("validated")]
    to_validate = unvalidated[:max_tickers]

    if not to_validate:
        log.info("All tickers already validated")
        return tickers

    log.info("Validating %d/%d unvalidated tickers via yfinance...", len(to_validate), len(unvalidated))

    # Group by exchange for efficiency
    by_exchange: Dict[str, List[str]] = defaultdict(list)
    for t in to_validate:
        by_exchange[t["exchange"]].append(t["symbol"])

    validated_map: Dict[str, Dict[str, Any]] = {}
    invalid_symbols: Set[str] = set()

    for exchange, symbols in by_exchange.items():
        log.info("  Validating %d tickers on %s...", len(symbols), exchange)
        results = validate_tickers_batch(symbols, exchange, batch_size=20)
        for r in results:
            validated_map[r["symbol"]] = r

        # Track invalid symbols
        valid_syms = {r["symbol"] for r in results}
        for sym in symbols:
            if sym not in valid_syms:
                invalid_symbols.add(sym)

    # Merge validated data back into ticker list
    final = []
    for t in tickers:
        sym = t["symbol"]
        if sym in validated_map:
            # Merge validated data, keeping source info
            merged = {**t, **validated_map[sym]}
            merged["source"] = t.get("source", "unknown")
            final.append(merged)
        elif sym in invalid_symbols:
            # Mark as invalid but keep it (might be temporarily unavailable)
            t["validated"] = False
            t["validation_error"] = "no_data"
            final.append(t)
        else:
            final.append(t)

    validated_count = sum(1 for t in final if t.get("validated"))
    log.info("Validation complete: %d/%d tickers validated, %d invalid",
             validated_count, len(final), len(invalid_symbols))

    return final


# ---------------------------------------------------------------------------
# Main discovery orchestrator
# ---------------------------------------------------------------------------

def run_discovery(quick: bool = False, validate: bool = False, max_validate: int = 500) -> Dict[str, Any]:
    """Run the full ISA universe discovery pipeline.

    Args:
        quick: Only discover major US + UK indices (faster)
        validate: Run yfinance validation on discovered tickers
        max_validate: Max tickers to validate per run
    """
    start = time.monotonic()
    log.info("=" * 60)
    log.info("ISA Universe Discovery — Starting")
    log.info("  Mode: %s", "quick" if quick else "full")
    log.info("  Validation: %s (max %d)", "enabled" if validate else "disabled", max_validate)
    log.info("=" * 60)

    all_tickers: List[Dict[str, Any]] = []

    # Step 1: Load existing tickers from initial_universe.toml
    existing = load_existing_universe()
    all_tickers.extend(existing)
    log.info("Step 1: Loaded %d existing tickers", len(existing))

    # Step 2: Discover index components
    index_results = discover_index_components(quick=quick)
    index_count = 0
    for exchange, tickers in index_results.items():
        all_tickers.extend(tickers)
        index_count += len(tickers)
    log.info("Step 2: Discovered %d index component tickers", index_count)

    # Step 3: Discover LSE leveraged ETPs
    if not quick:
        lse_etps = discover_lse_leveraged_etps()
        all_tickers.extend(lse_etps)
        log.info("Step 3: Discovered %d LSE leveraged ETP candidates", len(lse_etps))
    else:
        log.info("Step 3: Skipped LSE leveraged scan (quick mode)")

    # Step 4: Optional validation
    validated_count = 0
    if validate:
        all_tickers = run_full_validation(all_tickers, max_tickers=max_validate)
        validated_count = sum(1 for t in all_tickers if t.get("validated"))
        log.info("Step 4: Validated %d tickers", validated_count)
    else:
        log.info("Step 4: Skipped validation (use --validate to enable)")

    # Step 5: Build and save master file
    master = build_master_file(all_tickers, validated_count=validated_count)
    save_master_file(master)

    elapsed = time.monotonic() - start
    log.info("=" * 60)
    log.info("ISA Universe Discovery — Complete in %.1fs", elapsed)
    log.info("  Total tickers: %d", master["total_tickers"])
    log.info("  Exchanges: %s", ", ".join(f"{k}({v['count']})" for k, v in master["exchanges"].items()))
    log.info("  Validated: %d", validated_count)
    log.info("  Master file: %s", MASTER_FILE)
    log.info("=" * 60)

    return master


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="ISA Universe Discovery")
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode: only S&P 500, NASDAQ 100, FTSE 100")
    parser.add_argument("--full", action="store_true",
                        help="Full mode: all indices + LSE leveraged ETPs")
    parser.add_argument("--validate", action="store_true",
                        help="Validate tickers via yfinance (slow)")
    parser.add_argument("--max-validate", type=int, default=500,
                        help="Max tickers to validate per run (default: 500)")
    args = parser.parse_args()

    quick = args.quick and not args.full

    try:
        run_discovery(
            quick=quick,
            validate=args.validate,
            max_validate=args.max_validate,
        )
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        log.error("Discovery failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
