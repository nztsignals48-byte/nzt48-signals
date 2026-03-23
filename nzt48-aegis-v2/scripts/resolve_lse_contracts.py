#!/usr/bin/env python3
"""Resolve all LSE equity contracts via IBKR reqContractDetails.

Connects to IB Gateway, iterates every LSE contract with con_id=0,
resolves the correct con_id + IBKR symbol, and writes updated contracts.toml.

Usage:
    docker exec aegis-v2 python3 /app/scripts/resolve_lse_contracts.py

Requires IB Gateway running on aegis-ib-gateway:4003.
"""
import json
import re
import sys
import time
import socket

# IBKR TWS API constants
CLIENT_ID = 199  # Different from engine (101) to avoid conflict


def resolve_via_ibkr_api():
    """Use the ibapi Python package to resolve contracts."""
    try:
        from ibapi.client import EClient
        from ibapi.wrapper import EWrapper
        from ibapi.contract import Contract
        import threading
    except ImportError:
        print("ERROR: ibapi not installed. Install with: pip install ibapi", file=sys.stderr)
        return None

    resolved = {}
    pending = {}
    done = threading.Event()

    class Resolver(EWrapper, EClient):
        def __init__(self):
            EClient.__init__(self, self)
            self.next_req_id = 1000

        def nextValidId(self, orderId):
            print(f"Connected. Next valid ID: {orderId}", file=sys.stderr)

        def contractDetails(self, reqId, contractDetails):
            cd = contractDetails
            c = cd.contract
            symbol = pending.get(reqId, {}).get("symbol", "?")
            resolved[symbol] = {
                "con_id": c.conId,
                "ibkr_symbol": c.symbol,
                "exchange": c.exchange or c.primaryExchange,
                "currency": c.currency,
                "long_name": cd.longName,
            }
            print(f"  RESOLVED: {symbol} → conId={c.conId} ibkr_sym={c.symbol} exchange={c.exchange}", file=sys.stderr)

        def contractDetailsEnd(self, reqId):
            symbol = pending.get(reqId, {}).get("symbol", "?")
            if symbol not in resolved:
                print(f"  FAILED: {symbol} — no contract details returned", file=sys.stderr)
                resolved[symbol] = None

        def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
            if errorCode == 200:  # No security definition
                symbol = pending.get(reqId, {}).get("symbol", "?")
                print(f"  FAILED: {symbol} — code {errorCode}: {errorString}", file=sys.stderr)
                resolved[symbol] = None
            elif errorCode in (2104, 2106, 2158):  # Market data farm messages
                pass
            else:
                print(f"  IBKR error reqId={reqId} code={errorCode}: {errorString}", file=sys.stderr)

    resolver = Resolver()

    # Connect to IB Gateway
    host = "aegis-ib-gateway" if _in_docker() else "localhost"
    port = 4003
    print(f"Connecting to IB Gateway at {host}:{port}...", file=sys.stderr)
    resolver.connect(host, port, CLIENT_ID)

    # Start message thread
    api_thread = threading.Thread(target=resolver.run, daemon=True)
    api_thread.start()
    time.sleep(3)  # Wait for connection

    if not resolver.isConnected():
        print("ERROR: Failed to connect to IB Gateway", file=sys.stderr)
        return None

    # Load contracts.toml
    contracts_path = "/app/config/contracts.toml" if _in_docker() else "config/contracts.toml"
    lse_contracts = _load_lse_contracts(contracts_path)
    print(f"Found {len(lse_contracts)} LSE contracts with con_id=0 to resolve", file=sys.stderr)

    # Resolve each contract
    for i, (symbol, exchange, currency) in enumerate(lse_contracts):
        req_id = resolver.next_req_id
        resolver.next_req_id += 1
        pending[req_id] = {"symbol": symbol}

        # Strip .L suffix for IBKR
        ibkr_sym = symbol.removesuffix(".L")

        contract = Contract()
        contract.symbol = ibkr_sym
        contract.secType = "STK"
        contract.exchange = exchange
        contract.currency = currency

        resolver.reqContractDetails(req_id, contract)

        # Rate limit: IBKR allows ~50 requests/sec
        if (i + 1) % 40 == 0:
            print(f"  Progress: {i+1}/{len(lse_contracts)} resolved, waiting...", file=sys.stderr)
            time.sleep(2)
        else:
            time.sleep(0.1)

    # Wait for all responses
    print("Waiting for all responses...", file=sys.stderr)
    time.sleep(10)

    resolver.disconnect()
    return resolved


def _in_docker():
    """Check if running inside Docker."""
    try:
        with open("/proc/1/cgroup") as f:
            return "docker" in f.read()
    except Exception:
        return False


def _load_lse_contracts(path):
    """Load LSE contracts with con_id=0 from contracts.toml."""
    contracts = []
    with open(path) as f:
        content = f.read()

    # Parse TOML blocks
    blocks = content.split("[[contracts]]")[1:]
    for block in blocks:
        symbol_m = re.search(r'symbol\s*=\s*"([^"]+)"', block)
        conid_m = re.search(r'con_id\s*=\s*(\d+)', block)
        exchange_m = re.search(r'exchange\s*=\s*"([^"]+)"', block)
        currency_m = re.search(r'currency\s*=\s*"([^"]+)"', block)

        if not all([symbol_m, conid_m, exchange_m, currency_m]):
            continue

        symbol = symbol_m.group(1)
        con_id = int(conid_m.group(1))
        exchange = exchange_m.group(1)
        currency = currency_m.group(1)

        # Only resolve LSE equities with con_id=0
        if exchange == "LSE" and con_id == 0:
            contracts.append((symbol, exchange, currency))

    return contracts


def update_contracts_toml(resolved, path):
    """Update contracts.toml with resolved con_ids."""
    with open(path) as f:
        content = f.read()

    updated = 0
    failed = 0
    for symbol, result in resolved.items():
        if result is None:
            failed += 1
            continue

        con_id = result["con_id"]
        # Replace con_id = 0 for this specific symbol
        pattern = rf'(symbol = "{re.escape(symbol)}".*?con_id = )0'
        replacement = rf'\g<1>{con_id}'
        new_content, count = re.subn(pattern, replacement, content, flags=re.DOTALL)
        if count > 0:
            content = new_content
            updated += 1

    with open(path, "w") as f:
        f.write(content)

    print(f"\nResults: {updated} resolved, {failed} failed, {len(resolved)} total", file=sys.stderr)
    return updated, failed


if __name__ == "__main__":
    print("=== LSE Contract Resolver ===", file=sys.stderr)
    resolved = resolve_via_ibkr_api()

    if resolved is None:
        print("FATAL: Resolution failed — no IBKR connection", file=sys.stderr)
        sys.exit(1)

    # Output results as JSON for inspection
    output = {}
    for symbol, result in sorted(resolved.items()):
        if result:
            output[symbol] = result
        else:
            output[symbol] = "FAILED"

    print(json.dumps(output, indent=2))

    # Update contracts.toml
    contracts_path = "/app/config/contracts.toml" if _in_docker() else "config/contracts.toml"
    updated, failed = update_contracts_toml(resolved, contracts_path)
    print(f"\ncontracts.toml updated: {updated} con_ids resolved, {failed} still unresolved", file=sys.stderr)
