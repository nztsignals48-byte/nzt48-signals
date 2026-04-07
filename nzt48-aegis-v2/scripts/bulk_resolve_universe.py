#!/usr/bin/env python3
"""Bulk-resolve IBKR contracts from universe.json.

Reads all tickers from config/universe.json, resolves con_ids via
IBKR reqContractDetails, and appends valid entries to contracts.toml.

Usage (inside Docker):
  python3 /app/scripts/bulk_resolve_universe.py [--max N] [--exchange LSE|US|ALL]
"""

import json
import os
import sys
import time
import argparse


def load_universe(path):
    """Load tickers from universe.json."""
    with open(path) as f:
        data = json.load(f)

    tickers = []
    exchanges = data.get("exchanges", {})
    for exch_name, exch_data in exchanges.items():
        ticker_list = exch_data.get("tickers", [])
        currency = exch_data.get("currency", "USD")
        for t in ticker_list:
            if isinstance(t, dict):
                tickers.append({
                    "yf_symbol": t.get("symbol", ""),
                    "ibkr_symbol": t.get("ibkr_symbol", ""),
                    "ibkr_exchange": t.get("ibkr_exchange", ""),
                    "currency": t.get("currency", currency),
                    "sector": t.get("sector", "Unknown"),
                    "name": t.get("name", ""),
                    "index": t.get("index", ""),
                    "source_exchange": exch_name,
                })
    return tickers


def load_existing(path):
    """Load existing symbols from contracts.toml."""
    existing = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                if line.strip().startswith("symbol"):
                    sym = line.split("=", 1)[1].strip().strip('"')
                    existing.add(sym)
    return existing


def resolve_and_append(tickers, contracts_path, max_resolve=500):
    from ib_insync import IB, Stock

    host = os.environ.get("IB_HOST", "ib-gateway")
    port = int(os.environ.get("IB_PORT", "4003"))
    client_id = int(os.environ.get("IB_CLIENT_ID", "104"))

    ib = IB()
    print(f"Connecting to {host}:{port} (client_id={client_id})...")
    ib.connect(host, port, clientId=client_id, timeout=15)
    print(f"Connected: {ib.isConnected()}")

    existing = load_existing(contracts_path)
    print(f"Existing contracts: {len(existing)}")

    # Filter out already-resolved tickers
    to_resolve = [t for t in tickers if t["yf_symbol"] not in existing]
    print(f"To resolve: {len(to_resolve)} (of {len(tickers)} total, {len(existing)} already exist)")

    if len(to_resolve) > max_resolve:
        to_resolve = to_resolve[:max_resolve]
        print(f"Capped to {max_resolve}")

    resolved = []
    failed = []

    for i, t in enumerate(to_resolve):
        yf_sym = t["yf_symbol"]
        ibkr_sym = t["ibkr_symbol"] or yf_sym
        exchange = t["ibkr_exchange"] or "SMART"
        currency = t["currency"] or "USD"
        sector = t["sector"] or "Unknown"

        try:
            contract = Stock(ibkr_sym, exchange, currency)
            details = ib.reqContractDetails(contract)
            time.sleep(0.12)  # IBKR pacing

            if details:
                cd = details[0]
                con_id = cd.contract.conId
                actual_exchange = cd.contract.exchange or exchange
                actual_currency = cd.contract.currency or currency
                if (i + 1) % 50 == 0 or i < 5:
                    print(f"  [{i+1}/{len(to_resolve)}] OK   {yf_sym}: con_id={con_id} exchange={actual_exchange}")
                resolved.append({
                    "yf_symbol": yf_sym,
                    "con_id": con_id,
                    "exchange": actual_exchange,
                    "currency": actual_currency,
                    "sec_type": "STK",
                    "leverage": 1,
                    "sector": sector,
                    "name": t.get("name", ""),
                })
            else:
                if (i + 1) % 50 == 0 or i < 5:
                    print(f"  [{i+1}/{len(to_resolve)}] FAIL {yf_sym}: no details")
                failed.append(yf_sym)
        except Exception as e:
            if (i + 1) % 50 == 0 or i < 5:
                print(f"  [{i+1}/{len(to_resolve)}] FAIL {yf_sym}: {e}")
            failed.append(yf_sym)

    # Append to contracts.toml
    if resolved:
        print(f"\nAppending {len(resolved)} new contracts...")
        with open(contracts_path, "a") as f:
            for c in resolved:
                f.write(f'\n[[contracts]]\n')
                f.write(f'symbol = "{c["yf_symbol"]}"\n')
                f.write(f'con_id = {c["con_id"]}\n')
                f.write(f'exchange = "{c["exchange"]}"\n')
                f.write(f'sec_type = "{c["sec_type"]}"\n')
                f.write(f'currency = "{c["currency"]}"\n')
                f.write(f'leverage = {c["leverage"]}\n')
                f.write(f'sector = "{c["sector"]}"\n')
                f.write(f'inverse_of = ""\n')
        print(f"Done.")

    total_now = len(existing) + len(resolved)
    print(f"\nSummary: {len(resolved)} resolved, {len(failed)} failed")
    print(f"Total contracts now: {total_now}")

    ib.disconnect()
    return len(resolved)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=500, help="Max tickers to resolve")
    parser.add_argument("--exchange", default="ALL", help="Filter by exchange (LSE, US, ALL)")
    args = parser.parse_args()

    universe_path = "/app/config/universe.json"
    contracts_path = "/app/config/contracts.toml"

    tickers = load_universe(universe_path)
    print(f"Universe: {len(tickers)} tickers")

    if args.exchange != "ALL":
        tickers = [t for t in tickers if t["source_exchange"] == args.exchange]
        print(f"Filtered to {args.exchange}: {len(tickers)} tickers")

    if not tickers:
        print("No tickers to resolve.")
        return

    resolve_and_append(tickers, contracts_path, max_resolve=args.max)


if __name__ == "__main__":
    main()
