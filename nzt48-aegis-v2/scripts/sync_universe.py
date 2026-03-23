#!/usr/bin/env python3
"""
sync_universe.py — Sync universe.json into contracts.toml

Reads universe.json (867 tickers across LSE/NYSE/NASDAQ) and generates
contracts.toml entries for any tickers NOT already present.

Rules:
  - LSE tickers:    exchange="LSE", currency="GBP", symbol keeps ".L" suffix
  - NYSE tickers:   exchange="SMART", currency="USD", symbol as-is
  - NASDAQ tickers: exchange="SMART", currency="USD", symbol as-is
  - Known dotted TIDMs (BP, AV, BA, JD, NG, RR, SN, TW, UU, AO, QQ, HL)
    get a trailing dot in their IBKR symbol after stripping .L
  - Never removes existing contracts — append only
  - con_id=0 for all new entries (engine resolves at runtime)

Usage:
  python scripts/sync_universe.py                    # dry run (default)
  python scripts/sync_universe.py --apply            # write to contracts.toml
  python scripts/sync_universe.py --output out.toml  # write to separate file
"""
import json
import sys
import os
import re
from datetime import datetime, timezone

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
UNIVERSE_PATH = os.path.join(PROJECT_ROOT, "config", "universe.json")
CONTRACTS_PATH = os.path.join(PROJECT_ROOT, "config", "contracts.toml")

# Known dotted TIDMs — these LSE stocks have a trailing dot in their IBKR symbol
# e.g., BP.L -> ibkr_symbol "BP." (not "BP")
DOTTED_TIDMS = {"BP", "AV", "BA", "JD", "NG", "RR", "SN", "TW", "UU", "AO", "QQ", "HL"}


def parse_existing_symbols(contracts_path: str) -> set:
    """
    Extract all existing symbols from contracts.toml.
    Parses [[contracts]] blocks and extracts symbol = "..." values.
    Returns a set of symbol strings (e.g. {"AAPL", "BP.L", "QQQ3.L"}).
    """
    symbols = set()
    with open(contracts_path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("symbol"):
                # Match: symbol = "XYZ" or symbol = "XYZ.L"
                match = re.match(r'^symbol\s*=\s*"([^"]+)"', line)
                if match:
                    symbols.add(match.group(1))
    return symbols


def lse_ibkr_symbol(yf_symbol: str) -> str:
    """
    Convert a yfinance LSE symbol to IBKR symbol.
    e.g. "AAL.L" -> "AAL", "BP.L" -> "BP." (dotted TIDM)
    """
    base = yf_symbol.replace(".L", "")
    if base in DOTTED_TIDMS:
        return base + "."
    return base


def format_contract_entry(
    symbol: str,
    exchange: str,
    currency: str,
    sector: str,
    con_id: int = 0,
    leverage: int = 1,
    sec_type: str = "STK",
    inverse_of: str = "",
) -> str:
    """Format a single [[contracts]] TOML entry."""
    return (
        f'[[contracts]]\n'
        f'symbol = "{symbol}"\n'
        f'con_id = {con_id}\n'
        f'exchange = "{exchange}"\n'
        f'sec_type = "{sec_type}"\n'
        f'currency = "{currency}"\n'
        f'leverage = {leverage}\n'
        f'sector = "{sector}"\n'
        f'inverse_of = "{inverse_of}"\n'
    )


def load_universe(universe_path: str) -> dict:
    """Load universe.json and return the parsed dict."""
    with open(universe_path, "r") as f:
        return json.load(f)


def build_new_contracts(universe: dict, existing_symbols: set) -> tuple:
    """
    Walk universe.json exchanges and build contract entries for tickers
    not already in contracts.toml.

    Returns (lse_entries, nyse_entries, nasdaq_entries) — each a list of
    formatted TOML strings.
    """
    lse_entries = []
    nyse_entries = []
    nasdaq_entries = []

    exchanges = universe.get("exchanges", {})

    # --- LSE ---
    lse_data = exchanges.get("LSE", {})
    for ticker in lse_data.get("tickers", []):
        symbol = ticker["symbol"]  # e.g. "AAL.L"
        if symbol in existing_symbols:
            continue
        sector = ticker.get("sector", "Unknown").replace(" ", "_")
        entry = format_contract_entry(
            symbol=symbol,
            exchange="LSE",
            currency="GBP",
            sector=sector,
        )
        lse_entries.append(entry)

    # --- NYSE ---
    nyse_data = exchanges.get("NYSE", {})
    for ticker in nyse_data.get("tickers", []):
        symbol = ticker["symbol"]  # e.g. "AAPL"
        if symbol in existing_symbols:
            continue
        sector = ticker.get("sector", "Unknown").replace(" ", "_")
        entry = format_contract_entry(
            symbol=symbol,
            exchange="SMART",
            currency="USD",
            sector=sector,
        )
        nyse_entries.append(entry)

    # --- NASDAQ ---
    nasdaq_data = exchanges.get("NASDAQ", {})
    for ticker in nasdaq_data.get("tickers", []):
        symbol = ticker["symbol"]  # e.g. "AAPL"
        if symbol in existing_symbols:
            continue
        sector = ticker.get("sector", "Unknown").replace(" ", "_")
        entry = format_contract_entry(
            symbol=symbol,
            exchange="SMART",
            currency="USD",
            sector=sector,
        )
        nasdaq_entries.append(entry)

    return lse_entries, nyse_entries, nasdaq_entries


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Sync universe.json → contracts.toml")
    parser.add_argument("--apply", action="store_true", help="Write changes to contracts.toml")
    parser.add_argument("--output", type=str, default=None, help="Write to a separate file instead")
    args = parser.parse_args()

    # Load data
    print(f"[sync_universe] Loading universe from {UNIVERSE_PATH}")
    universe = load_universe(UNIVERSE_PATH)
    meta = universe.get("metadata", {})
    print(f"[sync_universe] Universe: {meta.get('total', '?')} tickers, generated {meta.get('generated_date', '?')}")

    print(f"[sync_universe] Parsing existing contracts from {CONTRACTS_PATH}")
    existing_symbols = parse_existing_symbols(CONTRACTS_PATH)
    print(f"[sync_universe] Existing contracts: {len(existing_symbols)}")

    # Build new entries
    lse_new, nyse_new, nasdaq_new = build_new_contracts(universe, existing_symbols)
    total_new = len(lse_new) + len(nyse_new) + len(nasdaq_new)

    print(f"\n{'='*60}")
    print(f"  SYNC SUMMARY")
    print(f"{'='*60}")
    print(f"  Existing contracts:  {len(existing_symbols)}")
    print(f"  New LSE entries:     {len(lse_new)}")
    print(f"  New NYSE entries:    {len(nyse_new)}")
    print(f"  New NASDAQ entries:  {len(nasdaq_new)}")
    print(f"  Total new:           {total_new}")
    print(f"  Total after sync:    {len(existing_symbols) + total_new}")
    print(f"{'='*60}")

    if total_new == 0:
        print("\n[sync_universe] Nothing to add — contracts.toml already up to date.")
        return

    # Build the appendable block
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    block_parts = []
    block_parts.append(f"\n# {'='*75}")
    block_parts.append(f"# UNIVERSE SYNC — Auto-generated by sync_universe.py")
    block_parts.append(f"# Generated: {now}")
    block_parts.append(f"# Source: universe.json ({meta.get('total', '?')} tickers)")
    block_parts.append(f"# Added: {total_new} new contracts (LSE:{len(lse_new)} NYSE:{len(nyse_new)} NASDAQ:{len(nasdaq_new)})")
    block_parts.append(f"# {'='*75}\n")

    if lse_new:
        block_parts.append(f"# {'='*75}")
        block_parts.append(f"# LSE Equities — {len(lse_new)} new (FTSE100/FTSE250 from universe.json)")
        block_parts.append(f"# {'='*75}\n")
        block_parts.extend(lse_new)

    if nyse_new:
        block_parts.append(f"# {'='*75}")
        block_parts.append(f"# NYSE Equities — {len(nyse_new)} new (S&P500 from universe.json)")
        block_parts.append(f"# {'='*75}\n")
        block_parts.extend(nyse_new)

    if nasdaq_new:
        block_parts.append(f"# {'='*75}")
        block_parts.append(f"# NASDAQ Equities — {len(nasdaq_new)} new (NDX100 from universe.json)")
        block_parts.append(f"# {'='*75}\n")
        block_parts.extend(nasdaq_new)

    new_block = "\n".join(block_parts)

    # Show a sample of what will be added
    sample_symbols = []
    for entries, label in [(lse_new, "LSE"), (nyse_new, "NYSE"), (nasdaq_new, "NASDAQ")]:
        for entry in entries[:3]:
            match = re.search(r'symbol = "([^"]+)"', entry)
            if match:
                sample_symbols.append(f"  {label}: {match.group(1)}")
    if sample_symbols:
        print(f"\n  Sample new entries:")
        for s in sample_symbols:
            print(s)
        if total_new > 9:
            print(f"  ... and {total_new - len(sample_symbols)} more")

    # Write
    if args.apply:
        print(f"\n[sync_universe] Appending {total_new} entries to {CONTRACTS_PATH}")
        with open(CONTRACTS_PATH, "r") as f:
            existing_content = f.read()

        # Update the total count in the header
        updated_content = re.sub(
            r"# Total: \d+ contracts",
            f"# Total: {len(existing_symbols) + total_new} contracts",
            existing_content,
            count=1,
        )

        with open(CONTRACTS_PATH, "w") as f:
            f.write(updated_content.rstrip("\n"))
            f.write("\n")
            f.write(new_block)
            f.write("\n")

        print(f"[sync_universe] Done. contracts.toml now has {len(existing_symbols) + total_new} contracts.")

    elif args.output:
        output_path = args.output
        print(f"\n[sync_universe] Writing new entries to {output_path}")
        with open(output_path, "w") as f:
            f.write(new_block)
            f.write("\n")
        print(f"[sync_universe] Done. {total_new} entries written to {output_path}")

    else:
        print(f"\n[sync_universe] DRY RUN — no changes written.")
        print(f"[sync_universe] Use --apply to write to contracts.toml")
        print(f"[sync_universe] Use --output <file> to write to a separate file")


if __name__ == "__main__":
    main()
