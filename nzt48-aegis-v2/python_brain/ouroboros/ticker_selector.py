"""Smart Ticker Selector — Tiered ranking for 36K+ ticker universe.

Runs daily after universe refresh (06:30 UTC) and selects the optimal
tickers for active monitoring. Handles massive universes efficiently
using a 4-tier scoring system:

  Tier 1 (HOT)   — Top 200 real-time 5s bar tickers. Live data from engine.
  Tier 2 (WARM)  — Next 800 daily-scanned tickers. Daily yfinance price fetch.
  Tier 3 (APEX)  — Next 2000 weekly-scanned. Weekly price fetch, cached.
  Tier 4 (COLD)  — Remaining 30K+. Static scoring only (market cap, sector,
                    leverage factor, exchange). NO price fetch.

Key design: only ~1500 tickers need daily yfinance calls (Tier 1+2).
Tier 3 uses a weekly cache. Tier 4 uses zero network calls.

Output:
  - config/active_watchlist.json  (Tier 1 + 2 + 3 + 4 metadata)
  - data/ouroboros_reports/watchlist_YYYY-MM-DD.json  (daily report)
  - data/universe_cache/price_cache.json  (weekly price data for Tier 3)

Usage: python3 -m python_brain.ouroboros.ticker_selector

Quarantine rules:
  - Read-only: never modifies WAL or trading state
  - Only updates the active_watchlist.json config file
  - yfinance calls are batched and rate-limited
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yfinance as yf
    import numpy as np
    import pytz
    import tomllib  # Python 3.11+
except ImportError as e:
    # Fallback for tomllib on Python < 3.11
    if "tomllib" in str(e):
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            tomllib = None  # type: ignore
    else:
        print(f"ERROR: Missing dependency: {e}", file=sys.stderr)
        sys.exit(1)

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
CACHE_DIR = DATA_DIR / "universe_cache"
MASTER_FILE = CONFIG_DIR / "isa_universe_master.json"
UNIVERSE_FILE = CONFIG_DIR / "universe.json"
WATCHLIST_FILE = CONFIG_DIR / "active_watchlist.json"
CONTRACTS_FILE = CONFIG_DIR / "contracts.toml"
PRICE_CACHE_FILE = CACHE_DIR / "price_cache.json"
REPORTS_DIR = DATA_DIR / "ouroboros_reports"


# ---------------------------------------------------------------------------
# Contract-awareness: only select tickers that have IBKR contract definitions
# ---------------------------------------------------------------------------

# Suffix → internal exchange mapping (yfinance symbol → contracts.toml exchange)
_YF_SUFFIX_TO_EXCHANGE = {
    ".L": "LSEETF",  # LSE ETPs
    ".T": "TSE",      # Tokyo
    ".HK": "HKEX",    # Hong Kong
    ".KS": "KRX",     # Korea KOSPI
    ".KQ": "KRX",     # Korea KOSDAQ
    ".DE": "XETRA",   # Frankfurt
    ".PA": "EURONEXT", # Paris
    ".AS": "AEB",      # Amsterdam
    ".MC": "XMAD",    # Madrid
    ".HE": "HEX",     # Helsinki
    ".AX": "ASX",     # Australia
    ".SI": "SGX",     # Singapore
}


def _yf_symbol_to_contract_key(yf_symbol: str) -> str:
    """Convert a yfinance symbol to the bare symbol used in contracts.toml.

    Examples:
        005930.KS → 005930   (KRX keeps leading zeros)
        7203.T   → 7203     (TSE bare numeric)
        0700.HK  → 0700     (HKEX keeps zero-padded)
        QQQ3.L   → QQQ3.L   (LSE KEEPS .L suffix — contracts.toml uses it)
        AAPL     → AAPL     (US has no suffix)
        SAP.DE   → SAP      (XETRA strip .DE)
        ASML.AS  → ASML     (Euronext strip .AS)
    """
    # LSE is special: contracts.toml KEEPS the .L suffix
    if yf_symbol.endswith(".L"):
        return yf_symbol

    # Strip all other known suffixes
    for suffix in _YF_SUFFIX_TO_EXCHANGE:
        if suffix == ".L":
            continue
        if yf_symbol.endswith(suffix):
            return yf_symbol[:-len(suffix)]

    # US stocks have no suffix — pass through
    return yf_symbol


def load_contract_symbols() -> set:
    """Load all contract symbols from contracts.toml.

    Returns a set of contract keys that can be matched against
    _yf_symbol_to_contract_key() output.
    """
    if not CONTRACTS_FILE.exists():
        return set()

    try:
        if tomllib is None:
            # Fallback: parse TOML manually for symbol field only
            symbols = set()
            with open(CONTRACTS_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("symbol"):
                        # symbol = "QQQ3.L"
                        parts = line.split("=", 1)
                        if len(parts) == 2:
                            sym = parts[1].strip().strip('"').strip("'")
                            if sym:
                                symbols.add(sym)
            return symbols

        with open(CONTRACTS_FILE, "rb") as f:
            data = tomllib.load(f)
        return {c["symbol"] for c in data.get("contracts", []) if c.get("symbol")}
    except Exception as e:
        logging.getLogger("ticker_selector").warning("Failed to load contracts.toml: %s", e)
        return set()

# ---------------------------------------------------------------------------
# universe.json loader — merges curated index tickers into the selection pool
# ---------------------------------------------------------------------------

# Map universe.json index field → isa_universe_master.json source field
_UNIVERSE_INDEX_TO_SOURCE = {
    "FTSE100": "FTSE 100",
    "FTSE250": "FTSE 250",
    "SP500": "S&P 500",
    "NDX100": "NASDAQ 100",
}

# Tier 1 indices (blue-chip, most liquid)
_TIER1_INDICES = {"FTSE100", "SP500"}
# Tier 2 indices (mid-cap / growth)
_TIER2_INDICES = {"FTSE250", "NDX100"}


def load_universe_json() -> List[Dict[str, Any]]:
    """Load tickers from universe.json and convert to master-format dicts.

    universe.json is organized by exchange:
        {"exchanges": {"LSE": {"tickers": [...]}, "NYSE": {...}, ...}}

    Each ticker has: symbol, ibkr_symbol, ibkr_exchange, name, sector, index, currency.

    We convert each into the same dict shape that isa_universe_master.json uses,
    mapping the `index` field to a `source` value that classify_into_tiers()
    recognizes as a major index.

    Returns empty list if universe.json doesn't exist or fails to parse.
    """
    if not UNIVERSE_FILE.exists():
        return []

    try:
        with open(UNIVERSE_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logging.getLogger("ticker_selector").warning(
            "Failed to load universe.json: %s", e
        )
        return []

    result = []
    exchanges = data.get("exchanges", {})
    for exch_name, exch_data in exchanges.items():
        for t in exch_data.get("tickers", []):
            symbol = t.get("symbol", "")
            if not symbol:
                continue

            # Map index field to source (for classify_into_tiers major_index_sources)
            raw_index = t.get("index", "")
            # Handle comma-separated indices like "SP500,NDX100"
            indices = [idx.strip() for idx in raw_index.split(",") if idx.strip()]
            # Pick the best source: prefer Tier 1 over Tier 2
            source = ""
            for idx in indices:
                mapped = _UNIVERSE_INDEX_TO_SOURCE.get(idx, "")
                if mapped:
                    source = mapped
                    if idx in _TIER1_INDICES:
                        break  # Tier 1 wins, stop looking

            # Determine exchange for the engine (use ibkr_exchange if available,
            # fall back to the top-level exchange key)
            exchange = t.get("ibkr_exchange", exch_name)
            # Normalize ISLAND → NASDAQ for exchange-hours matching
            if exchange == "ISLAND":
                exchange = "NASDAQ"

            result.append({
                "symbol": symbol,
                "exchange": exchange,
                "name": t.get("name", ""),
                "type": "stock",
                "sector": t.get("sector", "Unknown"),
                "currency": t.get("currency", "USD"),
                "isa_eligible": True,
                "leveraged": False,
                "inverse": False,
                "leverage_factor": 1,
                "market_cap_usd": 0,
                "avg_daily_volume": 0,
                "validated": True,  # Index constituents are validated by definition
                "source": source,
                "_from_universe": True,  # Tag for dedup/debug
            })

    return result


# Exchange-to-market-hours mapping: LOCAL hours + timezone.
# DST-aware: pytz handles summer/winter time shifts automatically.
# Format: (open_hour, open_min, close_hour, close_min, tz_name, lunch_start_h, lunch_start_m, lunch_end_h, lunch_end_m)
# Lunch fields are None if no lunch break.
EXCHANGE_LOCAL_HOURS = {
    # Asian markets
    "TSE":          (9, 0,  15, 0,  "Asia/Tokyo",        None, None, None, None),    # no DST
    "HKEX":         (9, 30, 16, 0,  "Asia/Hong_Kong",    12,   0,    13,   0),       # lunch 12:00-13:00 HKT, no DST
    "KRX":          (9, 0,  15, 30, "Asia/Seoul",         None, None, None, None),    # no DST
    "NZX":          (10, 0, 16, 45, "Pacific/Auckland",   None, None, None, None),    # DST (NZDT/NZST)
    "XNZE":         (10, 0, 16, 45, "Pacific/Auckland",   None, None, None, None),    # same as NZX
    "SGX":          (9, 0,  17, 0,  "Asia/Singapore",     None, None, None, None),    # no DST
    "ASX":          (10, 0, 16, 0,  "Australia/Sydney",   None, None, None, None),    # DST (AEDT/AEST)
    # European markets
    "LSE":          (8, 0,  16, 30, "Europe/London",      None, None, None, None),    # DST (BST/GMT)
    "XETRA":        (8, 0,  16, 30, "Europe/Berlin",      None, None, None, None),    # DST (CEST/CET)
    "EURONEXT_PA":  (9, 0,  17, 30, "Europe/Paris",       None, None, None, None),    # DST (CEST/CET)
    "EURONEXT_AS":  (9, 0,  17, 30, "Europe/Amsterdam",   None, None, None, None),    # DST (CEST/CET)
    "SIX":          (9, 0,  17, 30, "Europe/Zurich",      None, None, None, None),    # DST (CEST/CET)
    # US markets
    "NYSE":         (9, 30, 16, 0,  "America/New_York",   None, None, None, None),    # DST (EDT/EST)
    "NASDAQ":       (9, 30, 16, 0,  "America/New_York",   None, None, None, None),    # DST (EDT/EST)
    "AMEX":         (9, 30, 16, 0,  "America/New_York",   None, None, None, None),    # DST (EDT/EST)
}

# Pre-build pytz timezone objects (avoids repeated lookups)
_EXCHANGE_TZ_CACHE: Dict[str, Any] = {}

def _get_exchange_tz(tz_name: str):
    """Get cached pytz timezone object."""
    if tz_name not in _EXCHANGE_TZ_CACHE:
        _EXCHANGE_TZ_CACHE[tz_name] = pytz.timezone(tz_name)
    return _EXCHANGE_TZ_CACHE[tz_name]

# Legacy session filtering (kept for backwards compatibility, not used in unified mode)
SESSION_EXCHANGES = {
    "asian": {"HKEX", "TSE", "SGX", "KRX", "ASX", "NZX", "XNZE"},
    "european": {"LSE", "XETRA", "EURONEXT_PA", "EURONEXT_AS", "SIX"},
    "american": {"NYSE", "NASDAQ", "AMEX"},
}


def is_exchange_open(exchange: str, utc_hour: int, utc_minute: int) -> bool:
    """Check if an exchange is currently open using timezone-aware local time.

    Converts UTC time to the exchange's local timezone (handling DST automatically
    via pytz), then checks against local trading hours. This correctly handles
    BST, EDT, AEDT, NZDT, and all other DST transitions.

    Also handles lunch breaks (e.g., HKEX 12:00-13:00 HKT).

    Always returns True for unknown exchanges (safe default).
    """
    entry = EXCHANGE_LOCAL_HOURS.get(exchange)
    if entry is None:
        return True  # Unknown exchange → always include

    open_h, open_m, close_h, close_m, tz_name, lunch_start_h, lunch_start_m, lunch_end_h, lunch_end_m = entry

    # Convert UTC time to the exchange's local time (DST-aware).
    # Use today's date from the actual UTC clock for correct DST determination.
    utc_today = datetime.now(timezone.utc).date()
    utc_now = datetime(
        utc_today.year, utc_today.month, utc_today.day,
        utc_hour, utc_minute, 0,
        tzinfo=pytz.utc,
    )
    local_tz = _get_exchange_tz(tz_name)
    local_now = utc_now.astimezone(local_tz)
    local_mins = local_now.hour * 60 + local_now.minute

    open_mins = open_h * 60 + open_m
    close_mins = close_h * 60 + close_m

    # Check if within trading hours (all exchanges open and close on same local day)
    if not (open_mins <= local_mins < close_mins):
        return False

    # Check lunch break if applicable
    if lunch_start_h is not None:
        lunch_start_mins = lunch_start_h * 60 + lunch_start_m
        lunch_end_mins = lunch_end_h * 60 + lunch_end_m
        if lunch_start_mins <= local_mins < lunch_end_mins:
            return False

    return True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Ticker-Selector] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ticker_selector")

# ---------------------------------------------------------------------------
# Tier sizes
# ---------------------------------------------------------------------------
TIER1_VANGUARD = 50      # Real-time 5s bar monitoring (IBKR max=100, reserve 50 for Core 12 + buffer)
TIER2_WARM = 200         # Daily price fetch + scoring
TIER3_APEX = 500         # Weekly price fetch + scoring (cached)
# Tier 4 = everything else (static scoring only)

# How many tickers to price-fetch daily (Tier 1 + Tier 2 candidates)
MAX_DAILY_FETCH = 1500
# How many tickers to price-fetch weekly for Tier 3
MAX_WEEKLY_FETCH = 2500
# Price cache max age before refresh (days)
WEEKLY_CACHE_MAX_AGE_DAYS = 7

# Scoring lookbacks
VOLATILITY_LOOKBACK_DAYS = 20
MOMENTUM_LOOKBACK_DAYS = 10

# Scoring weights (used for Tier 1-3 with price data)
W_VOLATILITY = 0.35
W_VOLUME = 0.20
W_LEVERAGE = 0.25
W_MOMENTUM = 0.15
W_SPREAD_PROXY = 0.05

# Static scoring weights (Tier 4 — no price data)
W_STATIC_LEVERAGE = 0.40
W_STATIC_MCAP = 0.25
W_STATIC_VOLUME = 0.20
W_STATIC_EXCHANGE = 0.15

# Minimum thresholds
MIN_AVG_VOLUME = 100000       # 100K shares/day minimum (was 10K — too low, allowed penny stocks)
MIN_PRICE = 0.10              # 10p minimum (was 0.01 — sub-penny is untradeable)
MIN_NOTIONAL_USD = 200000     # $200K/day notional minimum (volume * price)
MAX_NEGATIVE_MOMENTUM = -0.03 # Disqualify tickers with worse than -3% momentum (chasing falling knives)

# Exchange priority scores for static scoring (higher = more liquid)
EXCHANGE_PRIORITY = {
    "NYSE": 1.0, "NASDAQ": 1.0, "AMEX": 0.8,
    "LSE": 0.9, "XETRA": 0.7, "EURONEXT_PA": 0.7, "EURONEXT_AS": 0.7,
    "TSE": 0.6, "HKEX": 0.6, "TSX": 0.6,
    "SIX": 0.5, "KRX": 0.5, "SGX": 0.5,
}


# ---------------------------------------------------------------------------
# Price cache management
# ---------------------------------------------------------------------------

def load_price_cache() -> Dict[str, Any]:
    """Load the weekly price cache if it exists and is fresh enough."""
    if not PRICE_CACHE_FILE.exists():
        return {}

    try:
        with open(PRICE_CACHE_FILE) as f:
            cache = json.load(f)

        cached_date = cache.get("cached_date", "")
        if cached_date:
            cache_dt = datetime.fromisoformat(cached_date)
            age_days = (datetime.now(timezone.utc) - cache_dt).days
            if age_days <= WEEKLY_CACHE_MAX_AGE_DAYS:
                log.info("Price cache loaded: %d entries, age=%d days",
                         len(cache.get("data", {})), age_days)
                return cache.get("data", {})
            else:
                log.info("Price cache expired (age=%d days), will refresh", age_days)
    except (json.JSONDecodeError, IOError, ValueError) as e:
        log.warning("Failed to load price cache: %s", e)

    return {}


def save_price_cache(data: Dict[str, Dict[str, Any]]) -> None:
    """Save price data to weekly cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = {
        "cached_date": datetime.now(timezone.utc).isoformat(),
        "total_entries": len(data),
        "data": data,
    }
    with open(PRICE_CACHE_FILE, "w") as f:
        json.dump(cache, f, default=str)
    log.info("Price cache saved: %d entries", len(data))


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _parse_yf_batch(batch: List[str], data, results: Dict[str, Dict[str, Any]]) -> int:
    """Parse yfinance download result into results dict. Returns count of new entries."""
    added = 0
    if data is None or data.empty:
        return 0

    if len(batch) == 1:
        sym = batch[0]
        closes = data["Close"].dropna().tolist()
        volumes = data["Volume"].dropna().tolist()
        if closes:
            results[sym] = {
                "closes": closes,
                "volumes": volumes,
                "last_price": closes[-1] if closes else 0,
            }
            added += 1
    else:
        for sym in batch:
            try:
                if sym in data["Close"].columns:
                    closes = data["Close"][sym].dropna().tolist()
                    volumes = data["Volume"][sym].dropna().tolist()
                    if closes:
                        results[sym] = {
                            "closes": closes,
                            "volumes": volumes,
                            "last_price": closes[-1] if closes else 0,
                        }
                        added += 1
            except (KeyError, TypeError):
                continue
    return added


def fetch_price_data(symbols: List[str], days: int = 30) -> Dict[str, Dict[str, Any]]:
    """Fetch recent price data for a list of symbols via yfinance.

    Uses smaller batch sizes and exponential backoff to avoid rate limits.
    Failed batches are retried up to 3 times with increasing delays.

    Returns: {symbol: {"closes": [...], "volumes": [...], "last_price": float}}
    """
    results = {}
    period = f"{days}d"
    failed_symbols: List[str] = []

    # Use smaller batches (20) to reduce rate-limit impact per request.
    # yfinance rate-limits entire batch on 429, so smaller = less wasted.
    batch_size = 20
    base_delay = 1.0  # seconds between batches

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        batch_str = " ".join(batch)
        success = False

        for attempt in range(3):
            try:
                data = yf.download(
                    batch_str,
                    period=period,
                    interval="1d",
                    progress=False,
                    threads=True,
                    ignore_tz=True,
                )
                added = _parse_yf_batch(batch, data, results)
                if added > 0 or (data is not None and not data.empty):
                    success = True
                    break
                else:
                    # Empty data but no exception — symbols may be delisted
                    success = True
                    break

            except Exception as e:
                err_str = str(e)
                is_rate_limit = "rate" in err_str.lower() or "429" in err_str or "Too Many" in err_str
                if is_rate_limit and attempt < 2:
                    backoff = base_delay * (2 ** (attempt + 1))  # 2s, 4s
                    log.warning("Rate limited on batch %d-%d (attempt %d/3), retrying in %.0fs",
                                i, i + batch_size, attempt + 1, backoff)
                    time.sleep(backoff)
                else:
                    log.warning("Batch download failed for batch %d-%d (attempt %d/3): %s",
                                i, i + batch_size, attempt + 1, e)
                    break

        if not success:
            failed_symbols.extend(batch)

        # Adaptive delay: longer pause every 5 batches to stay under rate limit
        batch_num = i // batch_size
        if batch_num > 0 and batch_num % 5 == 0:
            time.sleep(base_delay * 3)  # 3s pause every 5 batches
        else:
            time.sleep(base_delay)

    # Retry failed symbols in even smaller batches with longer delays
    if failed_symbols:
        log.info("Retrying %d failed symbols in micro-batches...", len(failed_symbols))
        micro_batch = 5
        for i in range(0, len(failed_symbols), micro_batch):
            batch = failed_symbols[i:i + micro_batch]
            batch_str = " ".join(batch)
            try:
                data = yf.download(
                    batch_str,
                    period=period,
                    interval="1d",
                    progress=False,
                    threads=True,
                    ignore_tz=True,
                )
                _parse_yf_batch(batch, data, results)
            except Exception:
                pass
            time.sleep(2.0)

    log.info("Fetched price data for %d/%d symbols", len(results), len(symbols))
    return results


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def calculate_volatility(closes: List[float]) -> float:
    """Calculate annualised volatility from daily closes."""
    if len(closes) < 3:
        return 0.0
    arr = np.array(closes, dtype=float)
    log_returns = np.diff(np.log(arr))
    if len(log_returns) < 2:
        return 0.0
    daily_vol = np.std(log_returns, ddof=1)
    return float(daily_vol * np.sqrt(252))


def calculate_momentum(closes: List[float], lookback: int = 10) -> float:
    """Calculate momentum as percentage change over lookback period."""
    if len(closes) < lookback + 1:
        return 0.0
    recent = closes[-lookback:]
    if recent[0] == 0:
        return 0.0
    return (recent[-1] - recent[0]) / recent[0]


def calculate_avg_volume(volumes: List[float], lookback: int = 20) -> float:
    """Calculate average daily volume over lookback period."""
    recent = volumes[-lookback:] if len(volumes) >= lookback else volumes
    if not recent:
        return 0.0
    return float(np.mean(recent))


def score_ticker_with_price(
    ticker_info: Dict[str, Any],
    price_data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Score a ticker using live price data (Tiers 1-3).

    Returns: scored dict with all metrics, or None if filtered out.
    """
    closes = price_data.get("closes", [])
    volumes = price_data.get("volumes", [])
    last_price = price_data.get("last_price", 0)

    if not closes or last_price < MIN_PRICE:
        return None

    avg_vol = calculate_avg_volume(volumes, VOLATILITY_LOOKBACK_DAYS)
    if avg_vol < MIN_AVG_VOLUME:
        return None

    # Notional volume filter: price * volume must exceed minimum to ensure liquidity
    notional_daily = avg_vol * last_price
    if notional_daily < MIN_NOTIONAL_USD:
        return None

    volatility = calculate_volatility(closes)
    momentum = calculate_momentum(closes, MOMENTUM_LOOKBACK_DAYS)
    abs_momentum = abs(momentum)

    # Negative momentum filter: don't include tickers in steep decline
    if momentum < MAX_NEGATIVE_MOMENTUM:
        return None

    leverage_factor = ticker_info.get("leverage_factor") or (3 if ticker_info.get("leveraged") else 1)
    spread_proxy = volatility / max(last_price, 0.01) if last_price > 0 else 999

    return {
        "symbol": ticker_info["symbol"],
        "exchange": ticker_info.get("exchange", "Unknown"),
        "name": ticker_info.get("name", ""),
        "type": ticker_info.get("type", "stock"),
        "sector": ticker_info.get("sector", "Unknown"),
        "currency": ticker_info.get("currency", "USD"),
        "leveraged": ticker_info.get("leveraged", False),
        "inverse": ticker_info.get("inverse", False),
        "leverage_factor": leverage_factor,
        "last_price": last_price,
        "volatility_ann": volatility,
        "avg_daily_volume": avg_vol,
        "momentum_pct": momentum * 100,
        "abs_momentum_pct": abs_momentum * 100,
        "spread_proxy": spread_proxy,
        "scoring_tier": "price",
        "composite_score": 0.0,
    }


def score_ticker_static(ticker_info: Dict[str, Any]) -> Dict[str, Any]:
    """Score a ticker using ONLY static data (Tier 4 — no price fetch).

    Uses market cap, sector, leverage factor, and exchange to produce
    a static composite score. This allows ranking 30K+ tickers without
    any network calls.
    """
    leverage_factor = ticker_info.get("leverage_factor") or (3 if ticker_info.get("leveraged") else 1)
    market_cap = ticker_info.get("market_cap_usd", 0)
    avg_volume = ticker_info.get("avg_daily_volume", 0)
    exchange = ticker_info.get("exchange", "Unknown")

    # Leverage score: 5x=1.0, 3x=0.8, 2x=0.6, 1x=0.0
    if leverage_factor >= 5:
        leverage_score = 1.0
    elif leverage_factor >= 3:
        leverage_score = 0.8
    elif leverage_factor >= 2:
        leverage_score = 0.6
    else:
        leverage_score = 0.0

    # Market cap score: log-scaled, bigger = more liquid
    if market_cap > 0:
        import math
        # Scale: $1B = 0.3, $10B = 0.5, $100B = 0.7, $1T = 0.9
        mcap_log = math.log10(max(market_cap, 1))
        mcap_score = min(1.0, max(0.0, (mcap_log - 6) / 6))  # 1M=0, 1T=1
    else:
        mcap_score = 0.0

    # Volume score: log-scaled
    if avg_volume > 0:
        import math
        vol_log = math.log10(max(avg_volume, 1))
        vol_score = min(1.0, max(0.0, (vol_log - 3) / 5))  # 1K=0, 100M=1
    else:
        vol_score = 0.0

    # Exchange score
    exch_score = EXCHANGE_PRIORITY.get(exchange, 0.3)

    # Composite static score
    static_score = (
        W_STATIC_LEVERAGE * leverage_score +
        W_STATIC_MCAP * mcap_score +
        W_STATIC_VOLUME * vol_score +
        W_STATIC_EXCHANGE * exch_score
    )

    return {
        "symbol": ticker_info["symbol"],
        "exchange": exchange,
        "name": ticker_info.get("name", ""),
        "type": ticker_info.get("type", "stock"),
        "sector": ticker_info.get("sector", "Unknown"),
        "currency": ticker_info.get("currency", "USD"),
        "leveraged": ticker_info.get("leveraged", False),
        "inverse": ticker_info.get("inverse", False),
        "leverage_factor": leverage_factor,
        "last_price": 0,
        "volatility_ann": 0,
        "avg_daily_volume": avg_volume,
        "momentum_pct": 0,
        "abs_momentum_pct": 0,
        "spread_proxy": 999,
        "scoring_tier": "static",
        "composite_score": static_score,
    }


def load_backfill_scores(report_dir: Path = None) -> Dict[str, Dict[str, float]]:
    """Load per-ticker backfill simulation scores from most recent report.

    Searches for backfill_sim_YYYY-MM-DD.json within the last 3 days.

    Returns:
        {ticker: {"win_rate": float, "profit_factor": float, "pnl_per_share": float}}
        Empty dict if no recent backfill available.
    """
    if report_dir is None:
        report_dir = REPORTS_DIR

    if not report_dir.exists():
        log.info("Backfill scores: report dir not found (%s)", report_dir)
        return {}

    # Find most recent backfill JSON within last 3 days
    now = datetime.now(timezone.utc)
    for days_ago in range(4):
        date_str = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        candidate = report_dir / f"backfill_sim_{date_str}.json"
        if candidate.exists():
            break
    else:
        log.info("Backfill scores: no backfill_sim JSON found within last 3 days in %s", report_dir)
        return {}

    try:
        with open(candidate) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.warning("Backfill scores: failed to read %s: %s", candidate, e)
        return {}

    per_ticker_raw = data.get("per_ticker", {})
    if not per_ticker_raw:
        log.info("Backfill scores: no per_ticker data in %s", candidate)
        return {}

    # Global profit factor as fallback
    global_pf = data.get("profit_factor", 0)

    result: Dict[str, Dict[str, float]] = {}
    for ticker, stats in per_ticker_raw.items():
        trades = stats.get("trades", 0)
        wins = stats.get("wins", 0)
        total_pnl = stats.get("total_pnl", stats.get("pnl_per_share", 0))

        win_rate = stats.get("win_rate", wins / max(trades, 1))
        pnl_per_share = stats.get("pnl_per_share", total_pnl / max(trades, 1))
        # Use per-ticker profit_factor if present, else global
        profit_factor = stats.get("profit_factor", global_pf)

        result[ticker] = {
            "win_rate": float(win_rate),
            "profit_factor": float(profit_factor),
            "pnl_per_share": float(pnl_per_share),
        }

    log.info("Backfill scores: loaded %d tickers from %s", len(result), candidate.name)
    return result


def apply_backfill_adjustment(
    scores: Dict[str, float],
    backfill: Dict[str, Dict[str, float]],
) -> Dict[str, float]:
    """Adjust ticker composite scores based on backfill simulation results.

    For each ticker present in both scores and backfill:
      - backfill win_rate > 0.60: boost score by +10%
      - backfill win_rate < 0.35: penalize score by -15%
      - backfill pnl_per_share < 0:  penalize score by -10%
    Adjustments stack multiplicatively. Final scores clamped to [0.0, 1.0].

    Args:
        scores: {ticker: composite_score}
        backfill: output from load_backfill_scores()

    Returns:
        Updated scores dict (same dict, mutated in place and returned).
    """
    if not backfill:
        return scores

    adjusted_count = 0
    for ticker, score in scores.items():
        if ticker not in backfill:
            continue

        bf = backfill[ticker]
        original = score
        multiplier = 1.0

        if bf["win_rate"] > 0.60:
            multiplier *= 1.10  # +10% boost
        elif bf["win_rate"] < 0.35:
            multiplier *= 0.85  # -15% penalty

        if bf["pnl_per_share"] < 0:
            multiplier *= 0.90  # -10% penalty

        if multiplier != 1.0:
            new_score = max(0.0, min(1.0, score * multiplier))
            scores[ticker] = new_score
            adjusted_count += 1
            log.debug(
                "Backfill adjustment: %s %.4f -> %.4f (wr=%.2f pnl/sh=%.4f mult=%.2f)",
                ticker, original, new_score,
                bf["win_rate"], bf["pnl_per_share"], multiplier,
            )

    if adjusted_count:
        log.info("Backfill adjustments applied to %d/%d tickers", adjusted_count, len(scores))
    return scores


def rank_and_score(scored_tickers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize metrics to 0-1 and compute composite score for price-scored tickers."""
    # Separate price-scored from static-scored
    price_scored = [t for t in scored_tickers if t.get("scoring_tier") == "price"]
    static_scored = [t for t in scored_tickers if t.get("scoring_tier") == "static"]

    n = len(price_scored)
    if n > 0:
        def rank_normalize(tickers: List[Dict[str, Any]], key: str, higher_is_better: bool = True) -> None:
            sorted_by_key = sorted(range(len(tickers)), key=lambda i: tickers[i].get(key, 0))
            for rank, idx in enumerate(sorted_by_key):
                norm = rank / max(len(tickers) - 1, 1)
                if not higher_is_better:
                    norm = 1.0 - norm
                tickers[idx][f"{key}_norm"] = norm

        rank_normalize(price_scored, "volatility_ann", higher_is_better=True)
        rank_normalize(price_scored, "avg_daily_volume", higher_is_better=True)
        rank_normalize(price_scored, "abs_momentum_pct", higher_is_better=True)
        rank_normalize(price_scored, "spread_proxy", higher_is_better=False)

        for t in price_scored:
            leverage = t.get("leverage_factor") or (3 if t.get("leveraged") else 1)
            if leverage >= 5:
                t["leverage_norm"] = 1.0
            elif leverage >= 3:
                t["leverage_norm"] = 0.8
            elif leverage >= 2:
                t["leverage_norm"] = 0.6
            else:
                t["leverage_norm"] = 0.0

        for t in price_scored:
            t["composite_score"] = (
                W_VOLATILITY * t.get("volatility_ann_norm", 0) +
                W_VOLUME * t.get("avg_daily_volume_norm", 0) +
                W_LEVERAGE * t.get("leverage_norm", 0) +
                W_MOMENTUM * t.get("abs_momentum_pct_norm", 0) +
                W_SPREAD_PROXY * t.get("spread_proxy_norm", 0)
            )

    # Sort price-scored first (they have real data), then static-scored
    price_scored.sort(key=lambda t: -t["composite_score"])
    static_scored.sort(key=lambda t: -t["composite_score"])

    # Static scores are inherently lower — scale them below the worst
    # price-scored ticker to ensure price data always wins
    if price_scored and static_scored:
        min_price_score = price_scored[-1]["composite_score"]
        max_static_score = static_scored[0]["composite_score"] if static_scored else 0
        if max_static_score > 0:
            scale_factor = (min_price_score * 0.9) / max_static_score
            for t in static_scored:
                t["composite_score"] *= scale_factor

    return price_scored + static_scored


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------

def classify_into_tiers(all_tickers: List[Dict[str, Any]]) -> Tuple[
    List[Dict[str, Any]],  # Tier 1+2 candidates (need daily price fetch)
    List[Dict[str, Any]],  # Tier 3 candidates (weekly price fetch)
    List[Dict[str, Any]],  # Tier 4 (static only)
]:
    """Classify tickers into scoring tiers based on priority.

    Priority order:
      1. Leveraged ETPs (always high priority)
      2. Validated tickers with known volume
      3. Tickers from major indices (S&P 500, NASDAQ 100, FTSE, etc.)
      4. Everything else
    """
    # Bucket tickers
    leveraged = []
    validated_high_vol = []
    validated_rest = []
    index_sourced = []
    rest = []

    major_index_sources = {
        "S&P 500", "NASDAQ 100", "FTSE 100", "FTSE 250", "FTSE All-Share",
        "Russell 2000", "DAX 40", "CAC 40", "Nikkei 225",
        "Hang Seng", "ASX 200", "Euro Stoxx 50",
    }

    for t in all_tickers:
        if t.get("leveraged"):
            leveraged.append(t)
        elif t.get("validated") and t.get("avg_daily_volume", 0) > MIN_AVG_VOLUME:
            validated_high_vol.append(t)
        elif t.get("validated"):
            validated_rest.append(t)
        elif t.get("source") in major_index_sources:
            index_sourced.append(t)
        else:
            rest.append(t)

    # Tier 1+2 candidates: leveraged first, then validated high-vol, then index
    tier12_pool = leveraged + validated_high_vol
    remaining_slots = MAX_DAILY_FETCH - len(tier12_pool)
    if remaining_slots > 0:
        tier12_pool += index_sourced[:remaining_slots]
        remaining_slots -= min(len(index_sourced), remaining_slots)
    if remaining_slots > 0:
        tier12_pool += validated_rest[:remaining_slots]
        remaining_slots -= min(len(validated_rest), remaining_slots)
    if remaining_slots > 0:
        tier12_pool += rest[:remaining_slots]

    # Track what went into tier12
    tier12_syms = {t["symbol"] for t in tier12_pool}

    # Tier 3 candidates: next best tickers not in tier 1+2
    tier3_pool = []
    tier3_candidates = (
        [t for t in index_sourced if t["symbol"] not in tier12_syms] +
        [t for t in validated_rest if t["symbol"] not in tier12_syms] +
        [t for t in rest if t["symbol"] not in tier12_syms]
    )
    tier3_pool = tier3_candidates[:MAX_WEEKLY_FETCH]
    tier3_syms = {t["symbol"] for t in tier3_pool}

    # Tier 4: everything else
    tier4_pool = [
        t for t in all_tickers
        if t["symbol"] not in tier12_syms and t["symbol"] not in tier3_syms
    ]

    log.info("Tier classification: T1+2=%d, T3=%d, T4=%d (total=%d)",
             len(tier12_pool), len(tier3_pool), len(tier4_pool), len(all_tickers))

    return tier12_pool, tier3_pool, tier4_pool


# ---------------------------------------------------------------------------
# Watchlist generation
# ---------------------------------------------------------------------------

def generate_watchlist(
    scored: List[Dict[str, Any]],
    tier1_n: int = 100,  # Flat 100 tickers — no tier hierarchy
    lse_is_open: bool = True,
) -> Dict[str, Any]:
    """Generate active watchlist from scored tickers.

    Pure ranking: top 100 by composite score. No forced tickers.
    During LSE hours, leveraged/inverse ETPs are already boosted by
    ticker_ranker's score_leverage_boost(), so they naturally float
    to the top without any hardcoded set.
    """
    # Hysteresis: load previous watchlist, give +5 bonus to tickers already in it
    # This prevents excessive churn between consecutive runs
    prev_tickers: set = set()
    try:
        if WATCHLIST_FILE.exists():
            with open(WATCHLIST_FILE) as f:
                prev = json.load(f)
            prev_tickers = set(prev.get("tickers", []))
    except Exception:
        pass

    for t in scored:
        if t.get("symbol") in prev_tickers:
            t["composite_score"] = t.get("composite_score", 0) + 5.0

    # Re-sort after hysteresis bonus
    scored.sort(key=lambda t: t.get("composite_score", 0), reverse=True)

    # Minimum notional volume gate: $500K daily
    scored = [t for t in scored if (t.get("avg_daily_volume", 0) * t.get("last_price", 0)) >= 500_000
              or t.get("avg_daily_volume", 0) == 0]  # Allow if volume unknown

    # Minimum score threshold
    scored = [t for t in scored if t.get("composite_score", 0) >= 10.0]

    # Take top 100
    top_100 = scored[:tier1_n]

    def clean(t: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in t.items()
                if not k.endswith("_norm") and k != "spread_proxy"}

    watchlist = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "total_scored": len(scored),
        "tickers": [t["symbol"] for t in top_100],
        "tier_counts": {
            "active": len(top_100),
        },
        "scoring_weights": {
            "volatility": W_VOLATILITY,
            "volume": W_VOLUME,
            "leverage": W_LEVERAGE,
            "momentum": W_MOMENTUM,
            "spread_proxy": W_SPREAD_PROXY,
        },
        "vanguard": [clean(t) for t in top_100],
    }
    return watchlist


def _merge_gemini_dark_horses(watchlist: Dict[str, Any]) -> int:
    """Merge Gemini dark horse tickers into the watchlist as Apex (Tier 3).

    Reads dark_horses_latest.json if fresh (< 30 min), deduplicates against
    existing vanguard tickers, and appends new ones as apex entries.
    Returns count of dark horses merged.
    """
    dark_path = DATA_DIR / "gemini" / "dark_horses_latest.json"
    if not dark_path.exists():
        return 0

    try:
        age_min = (time.time() - dark_path.stat().st_mtime) / 60.0
        if age_min > 30.0:
            log.debug("Gemini dark_horses_latest.json is %.0fmin old (>30min), skipping", age_min)
            return 0

        with open(dark_path) as f:
            dark_data = json.load(f)

        dark_tickers = dark_data.get("data", {}).get("tickers", [])
        if not dark_tickers:
            return 0

        # Deduplicate: skip tickers already in the vanguard list
        existing = set(watchlist.get("tickers", []))
        new_dark = [t for t in dark_tickers if t not in existing]

        if not new_dark:
            return 0

        # Append to tickers list
        watchlist.setdefault("tickers", []).extend(new_dark[:20])  # Cap at 20

        # Add minimal apex entries for the Rust engine
        for sym in new_dark[:20]:
            watchlist.setdefault("apex", []).append({
                "symbol": sym,
                "source": "gemini_dark_horse",
            })

        watchlist["dark_horse_count"] = len(new_dark[:20])
        log.info("Gemini dark horses: merged %d tickers (%.0fmin old)", len(new_dark[:20]), age_min)
        return len(new_dark[:20])

    except Exception as e:
        log.warning("Gemini dark horse merge failed (non-fatal): %s", e)
        return 0


def _send_engine_sighup():
    """Send SIGHUP to aegis engine for hot-reload (best-effort)."""
    import signal as sig
    import subprocess
    try:
        proc = subprocess.run(
            ["pgrep", "-x", "aegis"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [p for p in proc.stdout.strip().split("\n") if p.isdigit()]
        if not pids:
            log.debug("No aegis process found — SIGHUP skipped")
            return
        for pid_str in pids:
            os.kill(int(pid_str), sig.SIGHUP)
            log.info("Sent SIGHUP to aegis PID %s (dark horse hot-reload)", pid_str)
    except Exception as e:
        log.warning("Failed to send SIGHUP: %s (non-fatal)", e)


def save_watchlist(watchlist: Dict[str, Any]) -> Path:
    """Save the active watchlist and regenerate initial_universe.toml for Rust engine.

    SAFETY: Atomic write via tmp + os.replace to avoid partial reads by engine.
    If the watchlist has 0 vanguard tickers, we still save the JSON
    (for diagnostics) but skip overwriting initial_universe.toml to prevent
    the engine from starting with an empty universe on next restart.
    """
    # Merge Gemini dark horses before saving (Tier 3 rotating slots)
    dark_count = _merge_gemini_dark_horses(watchlist)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to .tmp then os.replace to avoid partial reads by engine
    tmp_path = WATCHLIST_FILE.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(watchlist, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp_path), str(WATCHLIST_FILE))
    log.info("Watchlist saved (atomic): %s", WATCHLIST_FILE)

    # Bridge: regenerate initial_universe.toml from top Vanguard tickers
    # so the Rust engine picks up the daily-ranked universe on restart.
    _regenerate_universe_toml(watchlist)

    # Hot-reload: send SIGHUP so the running engine picks up new tickers
    if dark_count > 0:
        _send_engine_sighup()

    return WATCHLIST_FILE


# Sector mapping from exchange/type to TOML sector field
_SECTOR_MAP = {
    "Technology": "Technology", "Semiconductors": "Semiconductors",
    "US_Broad": "US_Broad", "UK_Broad": "UK_Broad", "EU_Broad": "EU_Broad",
    "Asia_Broad": "Asia_Broad", "Commodities": "Commodities", "Energy": "Energy",
    "Healthcare": "Healthcare", "Financials": "Financials", "AI": "AI",
    "EV": "EV", "Crypto": "Crypto", "Automotive": "Automotive",
}


def _regenerate_universe_toml(watchlist: Dict[str, Any]) -> None:
    """Write top-ranked tickers to initial_universe.toml for Rust config_loader.

    Takes the Vanguard (top 200) tickers and writes them in the TOML format
    that config_loader.rs expects. This bridges the gap between the Python
    ticker_selector and the Rust engine's universe.

    Leveraged/inverse ETPs naturally appear in the vanguard when LSE is
    open (via score boost in ticker_ranker).

    SAFETY: Never overwrites the seed file with 0 tickers — this would kill
    the engine on restart. If scoring fails (e.g. after-hours, no data),
    the previous seed file is preserved.
    """
    toml_path = CONFIG_DIR / "initial_universe.toml"
    vanguard = watchlist.get("vanguard", [])

    if not vanguard:
        log.warning("Ticker selector scored 0 vanguard tickers — NOT overwriting %s", toml_path)
        return

    # Take vanguard as-is (already market-hours-aware — leveraged ETPs boosted when LSE open)
    seen = set()
    all_tickers = []

    for t in vanguard:
        if len(all_tickers) >= TIER1_VANGUARD:
            break
        sym = t.get("symbol", "")
        if sym and sym not in seen:
            all_tickers.append(t)
            seen.add(sym)

    lines = [
        "# AEGIS V2 — Active Universe (auto-generated by ticker_selector)",
        f"# Generated: {watchlist.get('generated', 'unknown')}",
        f"# Total ranked: {watchlist.get('total_scored', 0)} tickers",
        f"# Active: {len(all_tickers)} tickers (top Vanguard tier, max {TIER1_VANGUARD})",
        "# DO NOT EDIT — regenerated every 15 min by ticker_selector",
        "",
    ]

    for t in all_tickers:
        symbol = t.get("symbol", "")
        if not symbol:
            continue
        leverage = t.get("leverage_factor") or (3 if t.get("leveraged") else 1)
        sector = _SECTOR_MAP.get(t.get("sector", "Unknown"), "Unknown")
        inverse = t.get("inverse", False)
        leveraged = t.get("leveraged", False)

        # Determine underlying from name or default
        name = t.get("name", "")
        underlying = name[:40] if name else symbol

        # Find inverse pair symbol (empty if not inverse)
        inverse_of = ""
        if inverse and leveraged:
            # Convention: inverse ETP name usually contains the base symbol
            inverse_of = ""  # Will be filled by Ouroboros correlation engine

        lines.append("[[tickers]]")
        lines.append(f'symbol = "{symbol}"')
        lines.append(f"leverage = {leverage}")
        lines.append(f'underlying = "{underlying}"')
        lines.append(f'sector = "{sector}"')
        lines.append(f'inverse_of = "{inverse_of}"')
        lines.append("")

    with open(toml_path, "w") as f:
        f.write("\n".join(lines))

    log.info("Universe TOML regenerated: %s (%d tickers)", toml_path, len(all_tickers))


def generate_report(watchlist: Dict[str, Any], scored: List[Dict[str, Any]]) -> Path:
    """Generate the daily watchlist selection report."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"watchlist_{today}.json"

    exchange_dist = defaultdict(int)
    type_dist = defaultdict(int)
    tier_dist = defaultdict(int)
    for t in scored[:TIER1_VANGUARD + TIER2_WARM + TIER3_APEX]:
        exchange_dist[t.get("exchange", "Unknown")] += 1
        type_dist[t.get("type", "stock")] += 1
        tier_dist[t.get("scoring_tier", "unknown")] += 1

    top10 = []
    for t in scored[:10]:
        top10.append({
            "symbol": t["symbol"],
            "exchange": t.get("exchange", ""),
            "score": round(t["composite_score"], 4),
            "volatility": round(t.get("volatility_ann", 0), 4),
            "volume": int(t.get("avg_daily_volume", 0)),
            "momentum": round(t.get("momentum_pct", 0), 2),
            "leverage": t.get("leverage_factor") or (3 if t.get("leveraged") else 1),
            "tier": t.get("scoring_tier", "unknown"),
        })

    report = {
        "date": today,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_universe": len(scored),
        "total_scored": len(scored),
        "tier_counts": watchlist.get("tier_counts", {}),
        "exchange_distribution": dict(exchange_dist),
        "type_distribution": dict(type_dist),
        "tier_distribution": dict(tier_dist),
        "top_10": top10,
        "scoring_weights": watchlist["scoring_weights"],
    }

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    log.info("Report saved: %s", report_path)
    return report_path


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_selection(skip_fetch: bool = False, session: Optional[str] = None) -> int:
    """Execute the ticker selection pipeline with tiered scoring.

    Unified mode (default): Selects the best 100 tickers across ALL 6 markets,
    filtered by which exchanges are currently open. During LSE hours,
    leveraged/inverse ETPs get a score boost and naturally float to the top.

    The engine reads active_watchlist.json every 15 minutes and rotates
    its IBKR subscriptions to match (max 100 data lines).
    """
    start = time.monotonic()
    now_utc = datetime.now(timezone.utc)
    today = now_utc.strftime("%Y-%m-%d")
    utc_hour = now_utc.hour
    utc_minute = now_utc.minute
    log.info("=" * 60)
    log.info("Ticker Selector (Unified) — %s %02d:%02d UTC", today, utc_hour, utc_minute)
    log.info("=" * 60)

    # Step 1: Load master universe
    master = {"tickers": []}
    if MASTER_FILE.exists():
        with open(MASTER_FILE) as f:
            master = json.load(f)
        log.info("Step 1: Loaded isa_universe_master.json (%d tickers)",
                 len(master.get("tickers", [])))
    else:
        log.warning("Master file not found: %s — will use universe.json only", MASTER_FILE)

    all_tickers = [t for t in master.get("tickers", []) if not t.get("delisted")]

    # Step 1a: Merge tickers from universe.json (curated index constituents)
    universe_tickers = load_universe_json()
    if universe_tickers:
        existing_symbols = {t["symbol"] for t in all_tickers}
        merged_count = 0
        for ut in universe_tickers:
            if ut["symbol"] not in existing_symbols:
                all_tickers.append(ut)
                existing_symbols.add(ut["symbol"])
                merged_count += 1
        log.info("Step 1a: Merged %d new tickers from universe.json (%d total in universe.json, %d already present)",
                 merged_count, len(universe_tickers), len(universe_tickers) - merged_count)
    elif not all_tickers:
        log.error("No tickers from isa_universe_master.json or universe.json — cannot proceed")
        return 1

    # Market-hours-aware filtering: only include tickers from exchanges that are
    # currently OPEN. This maximises the value of each IBKR data line.
    # No forced tickers — leveraged/inverse ETPs get a score boost via
    # ticker_ranker.score_leverage_boost() when LSE is open, so they naturally
    # float to the top without any hardcoded set.
    before_filter = len(all_tickers)
    open_exchanges = set()
    for exch in EXCHANGE_LOCAL_HOURS:
        if is_exchange_open(exch, utc_hour, utc_minute):
            open_exchanges.add(exch)

    lse_is_open = "LSE" in open_exchanges
    log.info("LSE status: %s", "OPEN" if lse_is_open else "CLOSED")

    # Legacy session filtering (backwards compat — used only if --session is passed)
    if session and session in SESSION_EXCHANGES:
        session_exchanges = SESSION_EXCHANGES[session]
        all_tickers = [t for t in all_tickers if t.get("exchange", "Unknown") in session_exchanges]
        log.info("Step 1: Session=%s filter → %d tickers (exchanges: %s)",
                 session, len(all_tickers), sorted(session_exchanges))
    else:
        # Unified mode: ONLY include tickers from open exchanges.
        # When LSE is open: all LSE tickers included (leveraged ETPs boosted by ranker).
        # When LSE is closed: NO LSE tickers at all — all 100 slots for open markets.
        all_tickers = [
            t for t in all_tickers
            if t.get("exchange", "Unknown") in open_exchanges
        ]
        log.info("Step 1: Loaded %d tickers (%d open exchanges: %s, filtered from %d)",
                 len(all_tickers), len(open_exchanges), sorted(open_exchanges), before_filter)

    # Step 1b: Contract-awareness filter — only keep tickers that have IBKR
    # contract definitions in contracts.toml. Without a contract, the engine
    # can't subscribe to IBKR data for the ticker, making it useless.
    contract_symbols = load_contract_symbols()
    if contract_symbols:
        before_contract_filter = len(all_tickers)
        all_tickers = [
            t for t in all_tickers
            if _yf_symbol_to_contract_key(t.get("symbol", "")) in contract_symbols
        ]
        log.info("Step 1b: Contract filter → %d tickers (had contracts, filtered from %d, %d contract definitions)",
                 len(all_tickers), before_contract_filter, len(contract_symbols))
    else:
        log.warning("Step 1b: No contracts.toml found — skipping contract filter (ALL tickers eligible)")

    # Step 2: Classify into tiers
    tier12_pool, tier3_pool, tier4_pool = classify_into_tiers(all_tickers)

    # Step 3: Load weekly price cache for Tier 3
    price_cache = load_price_cache()
    cache_hit_count = sum(1 for t in tier3_pool if t["symbol"] in price_cache)
    cache_needs_refresh = cache_hit_count < len(tier3_pool) * 0.5
    log.info("Step 3: Price cache has %d/%d Tier 3 tickers (refresh=%s)",
             cache_hit_count, len(tier3_pool), cache_needs_refresh)

    # Step 4: Fetch price data for Tier 1+2 (daily)
    scored = []
    if skip_fetch:
        log.info("Step 4: Skipping price fetch (--skip-fetch)")
        price_data_t12 = {}
    else:
        symbols_t12 = [t["symbol"] for t in tier12_pool]
        log.info("Step 4: Fetching daily prices for %d Tier 1+2 candidates...",
                 len(symbols_t12))
        price_data_t12 = fetch_price_data(
            symbols_t12,
            days=max(VOLATILITY_LOOKBACK_DAYS, MOMENTUM_LOOKBACK_DAYS) + 5,
        )

    # Score Tier 1+2 with price data
    for t in tier12_pool:
        sym = t["symbol"]
        pd = price_data_t12.get(sym)
        if pd:
            result = score_ticker_with_price(t, pd)
            if result:
                scored.append(result)
        elif t.get("avg_daily_volume", 0) > MIN_AVG_VOLUME:
            # Fallback: use stored metrics
            scored.append({
                "symbol": sym,
                "exchange": t.get("exchange", "Unknown"),
                "name": t.get("name", ""),
                "type": t.get("type", "stock"),
                "sector": t.get("sector", "Unknown"),
                "currency": t.get("currency", "USD"),
                "leveraged": t.get("leveraged", False),
                "inverse": t.get("inverse", False),
                "leverage_factor": t.get("leverage_factor") or (3 if t.get("leveraged") else 1),
                "last_price": 0,
                "volatility_ann": 0,
                "avg_daily_volume": t.get("avg_daily_volume", 0),
                "momentum_pct": 0,
                "abs_momentum_pct": 0,
                "spread_proxy": 999,
                "scoring_tier": "price",
                "composite_score": 0,
            })

    log.info("Step 4: Scored %d Tier 1+2 tickers with price data", len(scored))

    # Step 5: Score Tier 3 with cached or fresh weekly data
    if not skip_fetch and cache_needs_refresh:
        log.info("Step 5: Refreshing Tier 3 weekly price cache (%d tickers)...",
                 len(tier3_pool))
        symbols_t3 = [t["symbol"] for t in tier3_pool]
        price_data_t3 = fetch_price_data(
            symbols_t3,
            days=max(VOLATILITY_LOOKBACK_DAYS, MOMENTUM_LOOKBACK_DAYS) + 5,
        )
        # Merge into cache
        for sym, pd in price_data_t3.items():
            price_cache[sym] = pd
        # Save updated cache
        save_price_cache(price_cache)
    else:
        log.info("Step 5: Using cached prices for Tier 3 (%d cache entries)",
                 cache_hit_count)

    # Score Tier 3 tickers
    t3_scored = 0
    for t in tier3_pool:
        sym = t["symbol"]
        pd = price_cache.get(sym)
        if pd:
            result = score_ticker_with_price(t, pd)
            if result:
                result["scoring_tier"] = "price"  # Has price data (from cache)
                scored.append(result)
                t3_scored += 1
        else:
            # No cache data — score statically
            scored.append(score_ticker_static(t))

    log.info("Step 5: Scored %d Tier 3 tickers (%d with price, %d static)",
             len(tier3_pool), t3_scored, len(tier3_pool) - t3_scored)

    # Step 6: Score Tier 4 statically (no network calls)
    t4_start = time.monotonic()
    for t in tier4_pool:
        scored.append(score_ticker_static(t))

    log.info("Step 6: Scored %d Tier 4 tickers statically in %.1fs",
             len(tier4_pool), time.monotonic() - t4_start)

    # Step 6b: Enrich with company names from master universe + hardcoded + yfinance
    name_lookup = {t["symbol"]: t.get("name", "") for t in master.get("tickers", []) if t.get("name")}
    # Display names for known instruments (cosmetic only — does not affect trading logic).
    # TODO(Sprint 7): Load names from contracts.toml `name` field or IBKR contractDetails.
    KNOWN_NAMES = {
        # LSE leveraged ETPs
        "QQQ3.L": "WisdomTree Nasdaq 100 3x Lev",
        "QQQS.L": "WisdomTree Nasdaq 100 3x Short",
        "3LUS.L": "WisdomTree S&P 500 3x Lev",
        "3USS.L": "WisdomTree S&P 500 3x Short",
        "QQQ5.L": "WisdomTree Nasdaq 100 5x Lev",
        "5SPY.L": "WisdomTree S&P 500 5x Lev",
        "3SEM.L": "WisdomTree Semiconductor 3x Lev",
        "NVD3.L": "GraniteShares NVIDIA 3x Lev",
        "TSL3.L": "GraniteShares Tesla 3x Lev",
        "GPT3.L": "GraniteShares AI 3x Lev",
        "TSM3.L": "GraniteShares TSMC 3x Lev",
        "MU2.L": "GraniteShares Micron 2x Lev",
        # TSE (Tokyo) — top 30 most liquid
        "7203.T": "Toyota Motor", "6902.T": "DENSO Corp",
        "8035.T": "Tokyo Electron", "6758.T": "Sony Group",
        "6861.T": "Keyence Corp", "8306.T": "MUFG",
        "6954.T": "Fanuc Corp", "9432.T": "NTT",
        "8591.T": "ORIX Corp", "9984.T": "SoftBank Group",
        "8766.T": "Tokio Marine", "3382.T": "Seven & i Holdings",
        "6869.T": "Sysmex Corp", "4502.T": "Takeda Pharma",
        "9201.T": "JAL", "8802.T": "Mitsubishi Estate",
        "5401.T": "Nippon Steel", "1925.T": "Daiwa House",
        "1928.T": "Sekisui House", "6501.T": "Hitachi",
        "6857.T": "Advantest Corp", "7974.T": "Nintendo",
        "8411.T": "Mizuho Financial", "8316.T": "SMFG",
        "8031.T": "Mitsui & Co", "6098.T": "Recruit Holdings",
        "4063.T": "Shin-Etsu Chemical", "7269.T": "Suzuki Motor",
        "6702.T": "Fujitsu", "5803.T": "Fujikura",
        "6326.T": "Kubota Corp", "5802.T": "Sumitomo Elec",
        "4543.T": "Terumo Corp", "6988.T": "Nitto Denko",
        "9983.T": "Fast Retailing", "6367.T": "Daikin Industries",
        "6273.T": "SMC Corp", "7741.T": "HOYA Corp",
        "4568.T": "Daiichi Sankyo", "9433.T": "KDDI",
        # HKEX — top 30 most liquid
        "0001.HK": "CK Hutchison", "0175.HK": "Geely Auto",
        "0700.HK": "Tencent", "0883.HK": "CNOOC",
        "1211.HK": "BYD Company", "1299.HK": "AIA Group",
        "1398.HK": "ICBC", "1088.HK": "China Shenhua",
        "9618.HK": "JD.com", "0288.HK": "WH Group",
        "0857.HK": "PetroChina", "0939.HK": "CCB",
        "0388.HK": "HKEX", "9988.HK": "Alibaba Group",
        "3690.HK": "Meituan", "2318.HK": "Ping An Insurance",
        "0005.HK": "HSBC Holdings", "0027.HK": "Galaxy Ent",
        "1810.HK": "Xiaomi Corp", "2007.HK": "Country Garden",
        "1177.HK": "Sino Biopharm", "0968.HK": "Xinyi Solar",
        "1347.HK": "Hua Hong Semi", "1797.HK": "China Oilfield",
        "0268.HK": "Kingdee Intl", "6862.HK": "Haidilao Intl",
        "2025.HK": "Weimob Inc", "9618.HK": "JD.com",
        "1969.HK": "CMOC Group", "0006.HK": "Power Assets",
        "0691.HK": "Shanshan Intl",
        # KRX (Korea) — top 30 most liquid
        "005930.KS": "Samsung Electronics",
        "005935.KS": "Samsung Electronics Pref",
        "000660.KS": "SK Hynix",
        "005380.KS": "Hyundai Motor",
        "000270.KS": "Kia Corp",
        "035420.KS": "NAVER Corp",
        "035720.KS": "Kakao Corp",
        "006400.KS": "Samsung SDI",
        "051910.KS": "LG Chem",
        "003550.KS": "LG Corp",
        "066570.KS": "LG Electronics",
        "034020.KS": "Doosan Enerbility",
        "034220.KS": "LG Display",
        "009830.KS": "Hanwha Solutions",
        "006800.KS": "Mirae Asset Sec",
        "000150.KS": "Doosan Corp",
        "000720.KS": "Hyundai E&C",
        "000810.KS": "Samsung Fire",
        "000880.KS": "Hanwha Corp",
        "001450.KS": "Hyundai Marine",
        "002380.KS": "KCC Corp",
        "010950.KS": "S-Oil Corp",
        "011780.KS": "Kumho Petrochemical",
        "024110.KS": "Industrial Bank Korea",
        "003550.KS": "LG Corp",
        "006400.KS": "Samsung SDI",
        "012330.KS": "Hyundai Mobis",
        "028260.KS": "Samsung C&T",
        "032830.KS": "Samsung Life",
        "055550.KS": "Shinhan Financial",
        "086790.KS": "Hana Financial",
        "001570.KS": "Kumho Industrial",
        "002790.KS": "Amorepacific Group",
    }
    name_lookup.update(KNOWN_NAMES)
    enriched = 0
    missing_names = []
    for t in scored:
        sym = t.get("symbol", "")
        master_name = name_lookup.get(sym, "")
        if master_name and (not t.get("name") or t["name"] == sym):
            t["name"] = master_name
            enriched += 1
        elif not t.get("name") or t["name"] == sym:
            missing_names.append(sym)
    # Batch-fetch missing names from yfinance (top 200 only to save time)
    if missing_names and not skip_fetch:
        fetch_names = missing_names[:200]
        log.info("Step 6b: Fetching names for %d tickers from yfinance...", len(fetch_names))
        name_cache_file = CACHE_DIR / "name_cache.json"
        # Load cached names
        cached_names = {}
        if name_cache_file.exists():
            try:
                cached_names = json.load(open(name_cache_file))
            except Exception:
                pass
        # Apply cached names first
        still_missing = []
        for sym in fetch_names:
            if sym in cached_names:
                for t in scored:
                    if t.get("symbol") == sym:
                        t["name"] = cached_names[sym]
                        enriched += 1
                        break
            else:
                still_missing.append(sym)
        # Fetch remaining from yfinance
        for sym in still_missing[:150]:
            try:
                info = yf.Ticker(sym).info
                short_name = info.get("shortName") or info.get("longName") or ""
                if short_name:
                    cached_names[sym] = short_name
                    for t in scored:
                        if t.get("symbol") == sym:
                            t["name"] = short_name
                            enriched += 1
                            break
            except Exception:
                pass
        # Save updated name cache
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(name_cache_file, "w") as f:
                json.dump(cached_names, f, indent=2)
        except Exception:
            pass
    log.info("Step 6b: Enriched %d tickers with company names (%d still missing)",
             enriched, len([t for t in scored if not t.get("name") or t["name"] == t.get("symbol", "")]))

    # Step 7: Rank and generate watchlist
    log.info("Step 7: Ranking %d total scored tickers...", len(scored))
    if scored:
        scored = rank_and_score(scored)

    # Step 7b: Apply backfill simulation adjustments (daily runs only, not 15-min refresh)
    if session is None and scored:
        backfill_data = load_backfill_scores()
        if backfill_data:
            score_map = {t["symbol"]: t["composite_score"] for t in scored}
            score_map = apply_backfill_adjustment(score_map, backfill_data)
            for t in scored:
                t["composite_score"] = score_map.get(t["symbol"], t["composite_score"])
            # Re-sort after adjustments
            scored.sort(key=lambda t: -t["composite_score"])
            log.info("Step 7b: Backfill adjustments applied, re-sorted %d tickers", len(scored))
        else:
            log.info("Step 7b: No backfill data available, skipping adjustments")

    # API failure fallback: if scored list is suspiciously small, keep previous watchlist
    if len(scored) < 10:
        log.error("SCANNER DEGRADED: Only %d tickers scored — keeping previous watchlist", len(scored))
        return 1

    watchlist = generate_watchlist(scored, lse_is_open=lse_is_open)

    # Always save to the unified active_watchlist.json.
    # The Rust engine reads this single file every 15 minutes.
    save_watchlist(watchlist)

    # Also save session-specific copy for backwards compat / debugging
    if session:
        session_file = CONFIG_DIR / f"active_watchlist_{session}.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        with open(session_file, "w") as f:
            json.dump(watchlist, f, indent=2, default=str)
        log.info("Session watchlist also saved: %s", session_file)

    # Step 8: Generate report
    generate_report(watchlist, scored)

    # Print summary
    elapsed = time.monotonic() - start
    log.info("=" * 60)
    log.info("Ticker Selector complete in %.1fs", elapsed)
    log.info("  Total universe: %d tickers", len(all_tickers))
    log.info("  Total scored: %d tickers", len(scored))
    log.info("  Active watchlist:  %d tickers", watchlist["tier_counts"]["active"])
    if scored:
        log.info("  Top 5 by composite score:")
        for i, t in enumerate(scored[:5], 1):
            log.info("    %d. %s (%s) — score=%.4f vol=%.2f%% mom=%.1f%% lev=%dx tier=%s",
                     i, t["symbol"], t.get("exchange", ""),
                     t["composite_score"],
                     t.get("volatility_ann", 0) * 100,
                     t.get("momentum_pct", 0),
                     t.get("leverage_factor") or (3 if t.get("leveraged") else 1),
                     t.get("scoring_tier", "?"))
    log.info("=" * 60)

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    global TIER1_VANGUARD, TIER2_WARM, TIER3_APEX

    parser = argparse.ArgumentParser(description="Smart Ticker Selector (Tiered)")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Skip price data fetching (use stored metrics only)")
    parser.add_argument("--tier1", type=int, default=TIER1_VANGUARD,
                        help=f"Tier 1 Vanguard count (default: {TIER1_VANGUARD})")
    parser.add_argument("--tier2", type=int, default=TIER2_WARM,
                        help=f"Tier 2 Warm count (default: {TIER2_WARM})")
    parser.add_argument("--tier3", type=int, default=TIER3_APEX,
                        help=f"Tier 3 Apex count (default: {TIER3_APEX})")
    parser.add_argument("--session", type=str, default=None,
                        choices=["asian", "european", "american"],
                        help="Filter to session-specific exchanges (for 15-min scans)")
    args = parser.parse_args()

    TIER1_VANGUARD = args.tier1
    TIER2_WARM = args.tier2
    TIER3_APEX = args.tier3

    try:
        sys.exit(run_selection(skip_fetch=args.skip_fetch, session=args.session))
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        log.error("Ticker selection failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
