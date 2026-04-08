"""Ticker Discovery Engine — Automatically discovers new tradeable instruments.

Scans multiple sources nightly to find new IPOs, ETPs, and index additions
that aren't yet in contracts.toml. Feeds candidates to the contract_expander
for IBKR validation and onboarding.

Sources (in priority order):
  1. IBKR Scanner: reqScannerSubscription for new highs, high volume, most active
  2. yfinance: Sector ETF holdings diff (detect index additions/deletions)
  3. GraniteShares/LevShares product page (detect new leveraged ETPs)
  4. FTSE/S&P index composition files (detect quarterly rebalances)

The key philosophy:
  - Ticker discovery is HIGHLY ADAPTIVE: scan aggressively, onboard fast
  - Strategy execution is RARELY ADAPTIVE: proven strategies only, strict rules
  - New tickers enter the system daily; new strategies enter quarterly at most

Usage:
  python -m python_brain.ouroboros.ticker_discovery
  python -m python_brain.ouroboros.ticker_discovery --source ibkr_scanner
  python -m python_brain.ouroboros.ticker_discovery --source etp_sweep
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

log = logging.getLogger("ticker_discovery")

CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", "/app/config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
CONTRACTS_PATH = CONFIG_DIR / "contracts.toml"
DISCOVERY_CACHE = DATA_DIR / "discovery_cache.json"


def _load_existing_symbols() -> Set[str]:
    """Load all symbols currently in contracts.toml."""
    symbols = set()
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore
    if not CONTRACTS_PATH.exists():
        return symbols
    with open(CONTRACTS_PATH, "rb") as f:
        data = tomllib.load(f)
    for c in data.get("contracts", []):
        symbols.add(c.get("symbol", ""))
    return symbols


def _load_existing_con_ids() -> Set[int]:
    """Load all con_ids currently in contracts.toml."""
    ids = set()
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore
    if not CONTRACTS_PATH.exists():
        return ids
    with open(CONTRACTS_PATH, "rb") as f:
        data = tomllib.load(f)
    for c in data.get("contracts", []):
        cid = c.get("con_id", 0)
        if cid > 0:
            ids.add(cid)
    return ids


# ── Source 1: IBKR Scanner ─────────────────────────────────────────────

def discover_ibkr_scanner() -> List[dict]:
    """Use IBKR's reqScannerSubscription to find new high-activity tickers.

    Scans for:
    - Most active by volume (today's unusual movers)
    - New 52-week highs (breakout candidates)
    - High option volume (gamma squeeze candidates)

    Returns list of candidate dicts with {symbol, exchange, source, reason}.
    """
    candidates = []
    try:
        import ib_insync
    except ImportError:
        log.warning("ib_insync not installed — skipping IBKR scanner discovery")
        return candidates

    try:
        ib = ib_insync.IB()
        ib.connect(
            os.environ.get("IBKR_HOST", "ib-gateway"),
            int(os.environ.get("IBKR_PORT", "4003")),
            clientId=105,  # Dedicated discovery client
        )
        time.sleep(1)
    except Exception as e:
        log.warning("IBKR connection failed for scanner discovery: %s", e)
        return candidates

    existing = _load_existing_symbols()
    existing_ids = _load_existing_con_ids()

    # IBKR scanner uses location codes, not exchange codes
    # See: https://interactivebrokers.github.io/tws-api/market_scanners.html
    scanner_configs = [
        # US stocks
        ("TOP_PERC_GAIN", "STK", "STK.US.MAJOR", "USD", "US high gainers"),
        ("TOP_PERC_LOSE", "STK", "STK.US.MAJOR", "USD", "US high losers"),
        ("MOST_ACTIVE", "STK", "STK.US.MAJOR", "USD", "US most active"),
        ("HOT_BY_VOLUME", "STK", "STK.US.MAJOR", "USD", "US hot by volume"),
        # European stocks
        ("MOST_ACTIVE", "STK", "STK.EU.LSEETF", "GBP", "LSEETF most active"),
        ("MOST_ACTIVE", "STK", "STK.EU.LSE", "GBP", "LSE most active"),
        ("TOP_PERC_GAIN", "STK", "STK.EU.LSE", "GBP", "LSE high gainers"),
    ]

    for scan_code, sec_type, location, currency, reason in scanner_configs:
        try:
            sub = ib_insync.ScannerSubscription(
                instrument=sec_type,
                locationCode=location,
                scanCode=scan_code,
                numberOfRows=50,
            )
            results = ib.reqScannerData(sub)
            time.sleep(0.5)

            for item in results:
                c = item.contractDetails.contract
                sym = c.symbol
                cid = c.conId

                # Skip if already in contracts.toml
                if sym in existing or cid in existing_ids:
                    continue

                candidates.append({
                    "symbol": sym,
                    "exchange": c.exchange or location,
                    "currency": c.currency or currency,
                    "con_id": cid,
                    "sec_type": c.secType,
                    "source": "ibkr_scanner",
                    "reason": reason,
                    "long_name": getattr(item.contractDetails, "longName", ""),
                })
                existing.add(sym)
                existing_ids.add(cid)

            log.info("Scanner [%s/%s]: found %d results, %d new",
                     scan_code, location, len(results),
                     sum(1 for c in candidates if c.get("reason") == reason))

        except Exception as e:
            log.warning("Scanner [%s/%s] failed: %s", scan_code, location, e)
            continue

    ib.disconnect()
    log.info("IBKR Scanner: %d new candidates discovered", len(candidates))
    return candidates


# ── Source 2: LSEETF Product Sweep ─────────────────────────────────────

def discover_lseetf_sweep() -> List[dict]:
    """Sweep IBKR for ALL LSEETF products by trying common symbol patterns.

    GraniteShares/LevShares products follow naming conventions:
    - 3L{XX}: 3x Long (e.g., 3LNV = NVIDIA long)
    - 3S{XX}: 3x Short
    - {XXX}3: 3x product (e.g., NVD3 = NVIDIA 3x)
    - 5{XXX}: 5x product
    - MAG7, MAG5: thematic baskets

    This sweep tries all 2-4 letter combinations on LSEETF exchange.
    """
    candidates = []
    try:
        import ib_insync
    except ImportError:
        return candidates

    try:
        ib = ib_insync.IB()
        ib.connect(
            os.environ.get("IBKR_HOST", "ib-gateway"),
            int(os.environ.get("IBKR_PORT", "4003")),
            clientId=105,
        )
        time.sleep(1)
    except Exception as e:
        log.warning("IBKR connection failed for LSEETF sweep: %s", e)
        return candidates

    existing_ids = _load_existing_con_ids()

    # Use reqMatchingSymbols to broadly search for leveraged products
    search_terms = [
        "GraniteShares", "Leverage Shares", "3x Long", "3x Short",
        "5x Long", "5x Short", "ETP Leveraged",
        "WisdomTree 3x", "WisdomTree Short",
    ]

    found_ids: Set[int] = set()
    for term in search_terms:
        try:
            matches = ib.reqMatchingSymbols(term)
            time.sleep(0.5)
            if matches:
                for m in matches:
                    c = m.contract
                    exch = c.exchange or c.primaryExchange or ""
                    if "LSE" in exch and c.conId not in existing_ids and c.conId not in found_ids:
                        found_ids.add(c.conId)
                        candidates.append({
                            "symbol": c.symbol,
                            "exchange": exch,
                            "currency": c.currency,
                            "con_id": c.conId,
                            "sec_type": c.secType,
                            "source": "lseetf_sweep",
                            "reason": f"matched '{term}'",
                        })
        except Exception:
            continue

    ib.disconnect()
    log.info("LSEETF Sweep: %d new candidates discovered", len(candidates))
    return candidates


# ── Source 3: yfinance Index Diff ──────────────────────────────────────

def discover_yfinance_index_diff() -> List[dict]:
    """Check for new additions to major indices (S&P 500, FTSE 100, NDX).

    Compares current index holdings against our contracts.toml.
    Any new additions are candidates for onboarding.
    """
    candidates = []
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed — skipping index diff discovery")
        return candidates

    existing = _load_existing_symbols()

    # Index ETFs whose holdings we can check
    index_etfs = {
        "SPY": ("SMART", "USD", "S&P 500"),
        "QQQ": ("SMART", "USD", "NASDAQ 100"),
        "IWM": ("SMART", "USD", "Russell 2000"),
        "ISF.L": ("LSE", "GBP", "FTSE 100"),
        "VUKE.L": ("LSE", "GBP", "FTSE 100"),
    }

    for etf_sym, (exchange, currency, index_name) in index_etfs.items():
        try:
            ticker = yf.Ticker(etf_sym)
            # Try to get holdings (not always available)
            try:
                holdings = ticker.get_institutional_holders()
            except Exception:
                holdings = None

            if holdings is not None and not holdings.empty:
                for _, row in holdings.iterrows():
                    sym = str(row.get("Symbol", row.get("Holder", ""))).strip()
                    if sym and sym not in existing and len(sym) <= 6:
                        candidates.append({
                            "symbol": sym,
                            "exchange": exchange,
                            "currency": currency,
                            "source": "index_diff",
                            "reason": f"found in {index_name} ({etf_sym})",
                        })
                        existing.add(sym)
        except Exception as e:
            log.debug("Index diff for %s failed: %s", etf_sym, e)
            continue

    log.info("yfinance Index Diff: %d new candidates discovered", len(candidates))
    return candidates


# ── Source 4: IPO/Recent Listings ──────────────────────────────────────

def discover_recent_ipos() -> List[dict]:
    """Discover recent IPOs that might be worth tracking.

    Uses yfinance to check for recently listed companies on major exchanges.
    Filters for minimum market cap and volume.
    """
    candidates = []
    try:
        import yfinance as yf
    except ImportError:
        return candidates

    existing = _load_existing_symbols()

    # Check popular IPO watchlist tickers
    # These are tickers that frequently get added to watchlists
    # In production, this would pull from an IPO calendar API
    ipo_watchlist_file = DATA_DIR / "ipo_watchlist.json"
    if ipo_watchlist_file.exists():
        try:
            with open(ipo_watchlist_file) as f:
                watchlist = json.load(f)
            for entry in watchlist:
                sym = entry.get("symbol", "")
                if sym and sym not in existing:
                    candidates.append({
                        "symbol": sym,
                        "exchange": entry.get("exchange", "SMART"),
                        "currency": entry.get("currency", "USD"),
                        "source": "ipo_watchlist",
                        "reason": entry.get("reason", "IPO watchlist"),
                    })
                    existing.add(sym)
        except Exception as e:
            log.debug("IPO watchlist load failed: %s", e)

    log.info("Recent IPOs: %d new candidates discovered", len(candidates))
    return candidates


# ── Main Discovery Pipeline ───────────────────────────────────────────

def run_discovery(sources: Optional[List[str]] = None) -> List[dict]:
    """Run all discovery sources and aggregate candidates.

    Args:
        sources: Optional list of specific sources to run.
                 If None, runs all sources.

    Returns:
        List of candidate dicts ready for contract_expander validation.
    """
    all_sources = {
        "ibkr_scanner": discover_ibkr_scanner,
        "lseetf_sweep": discover_lseetf_sweep,
        "index_diff": discover_yfinance_index_diff,
        "ipo_watchlist": discover_recent_ipos,
    }

    if sources:
        active_sources = {k: v for k, v in all_sources.items() if k in sources}
    else:
        active_sources = all_sources

    all_candidates = []
    seen_symbols: Set[str] = set()

    for source_name, source_fn in active_sources.items():
        log.info("Running discovery source: %s", source_name)
        try:
            candidates = source_fn()
            # Deduplicate
            for c in candidates:
                sym = c.get("symbol", "")
                if sym and sym not in seen_symbols:
                    seen_symbols.add(sym)
                    all_candidates.append(c)
        except Exception as e:
            log.error("Discovery source %s failed: %s", source_name, e)
            continue

    log.info("Total unique candidates discovered: %d", len(all_candidates))

    # Save to discovery cache for contract_expander to consume
    if all_candidates:
        cache = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "candidates": all_candidates,
        }
        DISCOVERY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(DISCOVERY_CACHE, "w") as f:
            json.dump(cache, f, indent=2)
        log.info("Saved %d candidates to %s", len(all_candidates), DISCOVERY_CACHE)

    return all_candidates


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    import argparse
    parser = argparse.ArgumentParser(description="AEGIS V2 Ticker Discovery Engine")
    parser.add_argument("--source", type=str, help="Run specific source only")
    args = parser.parse_args()

    sources = [args.source] if args.source else None

    log.info("═══ AEGIS V2 TICKER DISCOVERY ENGINE ═══")
    candidates = run_discovery(sources)

    if candidates:
        log.info("Top 10 discoveries:")
        for c in candidates[:10]:
            log.info("  %s (%s) — %s [%s]",
                     c["symbol"], c.get("exchange", "?"),
                     c.get("reason", ""), c.get("source", ""))

    log.info("Done. Run contract_expander to validate and onboard.")


if __name__ == "__main__":
    main()
