#!/usr/bin/env python3
"""Bulk-resolve IBKR contracts for key tickers across all exchanges.

Connects to IBKR via ib_insync, resolves con_ids via reqContractDetails,
and appends valid entries to contracts.toml.

Usage (inside Docker):
  python3 /app/scripts/bulk_resolve_contracts.py

Environment:
  IB_HOST / IB_PORT / IB_CLIENT_ID (defaults: ib-gateway / 4003 / 104)
"""

import os
import sys
import time

# Key tickers per exchange — most liquid instruments AEGIS should trade.
# Format: (yf_symbol, ibkr_symbol, ibkr_exchange, currency, leverage, sec_type)
TICKERS = [
    # ── US (NYSE/NASDAQ) — needed for American session + lead-lag signals ──
    ("AAPL", "AAPL", "SMART", "USD", 1, "STK"),
    ("MSFT", "MSFT", "SMART", "USD", 1, "STK"),
    ("NVDA", "NVDA", "SMART", "USD", 1, "STK"),
    ("TSLA", "TSLA", "SMART", "USD", 1, "STK"),
    ("AMZN", "AMZN", "SMART", "USD", 1, "STK"),
    ("GOOGL", "GOOGL", "SMART", "USD", 1, "STK"),
    ("META", "META", "SMART", "USD", 1, "STK"),
    ("AMD", "AMD", "SMART", "USD", 1, "STK"),
    ("ARM", "ARM", "SMART", "USD", 1, "STK"),
    ("TSM", "TSM", "SMART", "USD", 1, "STK"),
    ("SPY", "SPY", "SMART", "USD", 1, "STK"),
    ("QQQ", "QQQ", "SMART", "USD", 1, "STK"),
    ("IWM", "IWM", "SMART", "USD", 1, "STK"),
    ("AVGO", "AVGO", "SMART", "USD", 1, "STK"),
    ("NFLX", "NFLX", "SMART", "USD", 1, "STK"),
    ("CRM", "CRM", "SMART", "USD", 1, "STK"),
    ("COIN", "COIN", "SMART", "USD", 1, "STK"),
    ("MSTR", "MSTR", "SMART", "USD", 1, "STK"),
    ("PLTR", "PLTR", "SMART", "USD", 1, "STK"),
    ("SMCI", "SMCI", "SMART", "USD", 1, "STK"),

    # ── XETRA (German) — European session ──
    ("SAP.DE", "SAP", "IBIS", "EUR", 1, "STK"),
    ("SIE.DE", "SIE", "IBIS", "EUR", 1, "STK"),
    ("ALV.DE", "ALV", "IBIS", "EUR", 1, "STK"),
    ("DTE.DE", "DTE", "IBIS", "EUR", 1, "STK"),
    ("BAS.DE", "BAS", "IBIS", "EUR", 1, "STK"),
    ("BMW.DE", "BMW", "IBIS", "EUR", 1, "STK"),

    # ── HKEX — Asian session ──
    ("0700.HK", "0700", "SEHK", "HKD", 1, "STK"),
    ("9988.HK", "9988", "SEHK", "HKD", 1, "STK"),
    ("1810.HK", "1810", "SEHK", "HKD", 1, "STK"),
    ("0005.HK", "0005", "SEHK", "HKD", 1, "STK"),
    ("0941.HK", "0941", "SEHK", "HKD", 1, "STK"),
    ("2318.HK", "2318", "SEHK", "HKD", 1, "STK"),

    # ── TSE (Japan) — Asian session ──
    ("7203.T", "7203", "TSEJ", "JPY", 1, "STK"),
    ("6758.T", "6758", "TSEJ", "JPY", 1, "STK"),
    ("9984.T", "9984", "TSEJ", "JPY", 1, "STK"),
    ("6861.T", "6861", "TSEJ", "JPY", 1, "STK"),
    ("8306.T", "8306", "TSEJ", "JPY", 1, "STK"),

    # ── KRX (Korea) — Asian session ──
    ("005930.KS", "005930", "KSE", "KRW", 1, "STK"),
    ("000660.KS", "000660", "KSE", "KRW", 1, "STK"),
    ("035420.KS", "035420", "KSE", "KRW", 1, "STK"),
]


def resolve_contracts():
    from ib_insync import IB, Stock, Contract

    host = os.environ.get("IB_HOST", "ib-gateway")
    port = int(os.environ.get("IB_PORT", "4003"))
    client_id = int(os.environ.get("IB_CLIENT_ID", "104"))

    ib = IB()
    print(f"Connecting to {host}:{port} (client_id={client_id})...")
    ib.connect(host, port, clientId=client_id, timeout=15)
    print(f"Connected: {ib.isConnected()}")

    contracts_path = "/app/config/contracts.toml"

    # Load existing symbols to avoid duplicates
    existing = set()
    if os.path.exists(contracts_path):
        with open(contracts_path) as f:
            for line in f:
                if line.strip().startswith("symbol"):
                    sym = line.split("=", 1)[1].strip().strip('"')
                    existing.add(sym)

    resolved = []
    failed = []

    for yf_sym, ibkr_sym, exchange, currency, leverage, sec_type in TICKERS:
        if yf_sym in existing:
            print(f"  SKIP {yf_sym} (already in contracts.toml)")
            continue

        try:
            contract = Stock(ibkr_sym, exchange, currency)
            details = ib.reqContractDetails(contract)
            time.sleep(0.15)  # IBKR pacing

            if details:
                cd = details[0]
                con_id = cd.contract.conId
                actual_exchange = cd.contract.exchange or exchange
                actual_currency = cd.contract.currency or currency
                print(f"  OK   {yf_sym}: con_id={con_id} exchange={actual_exchange} currency={actual_currency}")
                resolved.append({
                    "yf_symbol": yf_sym,
                    "con_id": con_id,
                    "exchange": actual_exchange,
                    "currency": actual_currency,
                    "sec_type": sec_type,
                    "leverage": leverage,
                })
            else:
                print(f"  FAIL {yf_sym}: no contract details returned")
                failed.append(yf_sym)
        except Exception as e:
            print(f"  FAIL {yf_sym}: {e}")
            failed.append(yf_sym)

    # Append to contracts.toml
    if resolved:
        print(f"\nAppending {len(resolved)} new contracts to {contracts_path}...")
        with open(contracts_path, "a") as f:
            for c in resolved:
                f.write(f'\n[[contracts]]\n')
                f.write(f'symbol = "{c["yf_symbol"]}"\n')
                f.write(f'con_id = {c["con_id"]}\n')
                f.write(f'exchange = "{c["exchange"]}"\n')
                f.write(f'sec_type = "{c["sec_type"]}"\n')
                f.write(f'currency = "{c["currency"]}"\n')
                f.write(f'leverage = {c["leverage"]}\n')
        print(f"Done. {len(resolved)} contracts appended.")
    else:
        print("No new contracts resolved.")

    print(f"\nSummary: {len(resolved)} resolved, {len(failed)} failed, {len(existing & {t[0] for t in TICKERS})} skipped")
    if failed:
        print(f"Failed: {', '.join(failed)}")

    ib.disconnect()


if __name__ == "__main__":
    resolve_contracts()
