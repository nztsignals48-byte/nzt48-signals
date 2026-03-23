#!/usr/bin/env python3
"""Bulk-resolve LSE equity con_ids from IBKR's public stock search.

Scrapes https://www.interactivebrokers.com/cgi-pub/stock_search.pl for all
LSE stocks (A-Z), builds a symbol → IBKR mapping, then updates contracts.toml
with correct IBKR symbols and identifies which tickers IBKR actually supports.

Usage: python3 scripts/resolve_ibkr_lse.py
"""
import re
import sys
import time
import json
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
SEARCH_URL = "https://www.interactivebrokers.com/cgi-pub/stock_search.pl"


def fetch_all_lse_symbols():
    """Fetch ALL LSE symbols from IBKR's stock search (A-Z pages)."""
    all_symbols = {}  # ibkr_symbol → (long_name, currency)

    # IBKR search returns all symbols starting with the query letter
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        print(f"Fetching letter '{letter}'...", file=sys.stderr)
        try:
            resp = requests.get(
                SEARCH_URL,
                params={"symbol": letter, "LSE.html": "Submit"},
                headers=HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"  ERROR fetching {letter}: {e}", file=sys.stderr)
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        if not table:
            print(f"  No table found for {letter}", file=sys.stderr)
            continue

        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 3:
                ibkr_sym = cells[0].get_text(strip=True)
                long_name = cells[1].get_text(strip=True)
                currency = cells[2].get_text(strip=True)
                if ibkr_sym and long_name:
                    all_symbols[ibkr_sym] = (long_name, currency)

        count = len(all_symbols)
        print(f"  Running total: {count} symbols", file=sys.stderr)
        time.sleep(0.5)  # Rate limit

    return all_symbols


def build_mapping(ibkr_symbols, our_contracts):
    """Map our contracts.toml symbols to IBKR symbols.

    Our symbols use yfinance format: "HSBA.L", "BP..L", "AV..L"
    IBKR symbols: "HSBA", "BP.", "AV."

    Strategy:
    1. Strip ".L" from our symbol to get base: "HSBA", "BP.", "AV."
    2. Look up base in ibkr_symbols dict
    3. If found, that's our IBKR symbol
    """
    mapping = {}  # our_symbol → ibkr_symbol
    unresolved = []

    for our_sym, exchange, currency in our_contracts:
        if exchange != "LSE":
            continue

        # Strip .L suffix
        base = our_sym.removesuffix(".L") if our_sym.endswith(".L") else our_sym

        # Direct lookup
        if base in ibkr_symbols:
            mapping[our_sym] = {
                "ibkr_symbol": base,
                "long_name": ibkr_symbols[base][0],
                "currency": ibkr_symbols[base][1],
                "status": "RESOLVED",
            }
        else:
            # Try without trailing dot
            base_no_dot = base.rstrip(".")
            if base_no_dot in ibkr_symbols:
                mapping[our_sym] = {
                    "ibkr_symbol": base_no_dot,
                    "long_name": ibkr_symbols[base_no_dot][0],
                    "currency": ibkr_symbols[base_no_dot][1],
                    "status": "RESOLVED_STRIPPED_DOT",
                }
            else:
                unresolved.append(our_sym)
                mapping[our_sym] = {"status": "UNRESOLVED", "tried": [base, base_no_dot]}

    return mapping, unresolved


def load_our_contracts(path="config/contracts.toml"):
    """Load our contracts.toml entries."""
    with open(path) as f:
        content = f.read()

    contracts = []
    blocks = content.split("[[contracts]]")[1:]
    for block in blocks:
        symbol_m = re.search(r'symbol\s*=\s*"([^"]+)"', block)
        exchange_m = re.search(r'exchange\s*=\s*"([^"]+)"', block)
        currency_m = re.search(r'currency\s*=\s*"([^"]+)"', block)
        conid_m = re.search(r'con_id\s*=\s*(\d+)', block)

        if all([symbol_m, exchange_m, currency_m, conid_m]):
            contracts.append((
                symbol_m.group(1),
                exchange_m.group(1),
                currency_m.group(1),
            ))

    return contracts


if __name__ == "__main__":
    print("=== IBKR LSE Symbol Resolver (Large Scale) ===", file=sys.stderr)

    # Step 1: Fetch all IBKR LSE symbols
    print("\n--- Step 1: Fetching ALL IBKR LSE symbols ---", file=sys.stderr)
    ibkr_symbols = fetch_all_lse_symbols()
    print(f"\nTotal IBKR LSE symbols: {len(ibkr_symbols)}", file=sys.stderr)

    # Step 2: Load our contracts
    print("\n--- Step 2: Loading our contracts.toml ---", file=sys.stderr)
    our_contracts = load_our_contracts()
    lse_count = sum(1 for _, e, _ in our_contracts if e == "LSE")
    print(f"Our LSE contracts: {lse_count}", file=sys.stderr)

    # Step 3: Build mapping
    print("\n--- Step 3: Building symbol mapping ---", file=sys.stderr)
    mapping, unresolved = build_mapping(ibkr_symbols, our_contracts)

    resolved_count = sum(1 for v in mapping.values() if v["status"].startswith("RESOLVED"))
    print(f"\nResults:", file=sys.stderr)
    print(f"  Resolved: {resolved_count}", file=sys.stderr)
    print(f"  Unresolved: {len(unresolved)}", file=sys.stderr)

    if unresolved:
        print(f"\n  Unresolved symbols:", file=sys.stderr)
        for sym in sorted(unresolved)[:30]:
            print(f"    {sym}", file=sys.stderr)

    # Output full mapping as JSON
    print(json.dumps(mapping, indent=2))

    # Step 4: Report which symbols need fixing in contracts.toml
    needs_fix = []
    for our_sym, info in mapping.items():
        if info["status"] == "RESOLVED_STRIPPED_DOT":
            needs_fix.append((our_sym, info["ibkr_symbol"]))

    if needs_fix:
        print(f"\n--- Symbols where our .L stripping produces wrong IBKR symbol ---", file=sys.stderr)
        print(f"These need the Rust strip logic fixed (strip dots before .L):", file=sys.stderr)
        for our, ibkr in needs_fix:
            print(f"  {our} → should send '{ibkr}' to IBKR (currently sends '{our.removesuffix('.L')}')", file=sys.stderr)
