"""Universal Symbol Resolver — maps between IBKR, yfinance, Bloomberg, Reuters, and other formats.

Every data source uses different symbol conventions:
  IBKR:       AAPL (SMART), QQQ3.L (LSEETF), SAP.DE (IBIS), 7203.T (TSEJ), 9988.HK (SEHK)
  yfinance:   AAPL, QQQ3.L, SAP.DE, 7203.T, 9988.HK (mostly same, but IBKR drops .L for some LSEETF)
  Bloomberg:  AAPL US Equity, QQQ3 LN Equity, SAP GR Equity, 7203 JP Equity, 9988 HK Equity
  Reuters:    AAPL.O, QQQ3.L, SAPG.DE, 7203.T, 9988.HK

Key differences:
  - IBKR LSEETF: symbol WITHOUT .L suffix for new products (e.g., "3CNE", "CON3", "MST3")
    but WITH .L for legacy products (e.g., "QQQ3.L", "NVD3.L")
  - yfinance: ALWAYS needs .L suffix for LSE/LSEETF products
  - IBKR SMART: bare symbol (AAPL), yfinance: bare symbol (AAPL)
  - IBKR SEHK: 9988.HK, yfinance: 9988.HK (but may need zero-padding: 0700.HK)
  - IBKR TSEJ: 7203.T, yfinance: 7203.T
  - IBKR IBIS: SAP.DE, yfinance: SAP.DE

This module builds a bidirectional mapping from contracts.toml so that any part of the
system can convert: ibkr_symbol → {yfinance, bloomberg, reuters} and back.

Usage:
    from python_brain.ouroboros.symbol_resolver import SymbolResolver
    resolver = SymbolResolver.from_contracts_toml()

    # IBKR → yfinance
    yf_sym = resolver.to_yfinance("CON3", exchange="LSEETF")  # → "CON3.L"
    yf_sym = resolver.to_yfinance("AAPL", exchange="SMART")   # → "AAPL"

    # yfinance → IBKR
    ibkr_sym, exchange = resolver.from_yfinance("CON3.L")     # → ("CON3", "LSEETF")

    # Batch conversion
    yf_symbols = resolver.ibkr_to_yfinance_batch(["AAPL", "QQQ3.L", "SAP.DE", "CON3"])

    # Lookup by con_id (universal)
    info = resolver.by_con_id(265598)  # → full contract info for AAPL
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("symbol_resolver")

CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", "/app/config"))
CONTRACTS_PATH = CONFIG_DIR / "contracts.toml"


@dataclass
class ContractInfo:
    """Complete contract information for a single instrument."""
    ibkr_symbol: str        # Symbol as IBKR knows it (e.g., "CON3", "AAPL", "QQQ3.L")
    exchange: str           # IBKR exchange (SMART, LSEETF, LSE, NYSE, NASDAQ, IBIS, TSEJ, SEHK)
    currency: str           # Trading currency (USD, GBP, EUR, JPY, HKD)
    con_id: int             # IBKR contract ID (unique, immutable)
    leverage: int = 1       # Leverage factor (1, 2, 3, 5)
    sector: str = ""        # Sector classification
    # Derived symbols for other data sources
    yfinance: str = ""      # yfinance ticker (e.g., "CON3.L", "AAPL")
    bloomberg: str = ""     # Bloomberg ticker (e.g., "CON3 LN Equity")
    reuters: str = ""       # Reuters RIC (e.g., "CON3.L", "AAPL.O")


# Exchange → yfinance suffix mapping
# IBKR exchange codes → the suffix yfinance expects
_EXCHANGE_TO_YF_SUFFIX: Dict[str, str] = {
    "SMART": "",          # US stocks: bare symbol
    "NYSE": "",           # US stocks: bare symbol (yfinance doesn't distinguish NYSE/NASDAQ)
    "NASDAQ": "",         # US stocks: bare symbol
    "AMEX": "",           # US stocks: bare symbol
    "LSE": ".L",          # London Stock Exchange
    "LSEETF": ".L",       # LSE ETPs (leveraged products)
    "IBIS": ".DE",        # XETRA/Frankfurt
    "XETRA": ".DE",
    "TSEJ": ".T",         # Tokyo Stock Exchange
    "SEHK": ".HK",        # Hong Kong Stock Exchange
    "HKEX": ".HK",
    "KSE": ".KS",         # Korea
    "KRX": ".KS",
    "SGX": ".SI",         # Singapore
    "ASX": ".AX",         # Australia
    "EURONEXT_PA": ".PA", # Paris
    "EURONEXT_AS": ".AS", # Amsterdam
    "SIX": ".SW",         # Switzerland
}

# Exchange → Bloomberg exchange suffix
_EXCHANGE_TO_BBG: Dict[str, str] = {
    "SMART": "US", "NYSE": "US", "NASDAQ": "UQ", "AMEX": "UA",
    "LSE": "LN", "LSEETF": "LN",
    "IBIS": "GR", "XETRA": "GR",
    "TSEJ": "JP",
    "SEHK": "HK", "HKEX": "HK",
    "KSE": "KS", "KRX": "KS",
    "SGX": "SP",
    "ASX": "AU",
    "EURONEXT_PA": "FP",
    "EURONEXT_AS": "NA",
    "SIX": "SE",
}

# Exchange → Reuters exchange suffix
_EXCHANGE_TO_REUTERS: Dict[str, str] = {
    "SMART": ".O", "NYSE": ".N", "NASDAQ": ".O", "AMEX": ".A",
    "LSE": ".L", "LSEETF": ".L",
    "IBIS": ".DE", "XETRA": ".DE",
    "TSEJ": ".T",
    "SEHK": ".HK", "HKEX": ".HK",
}


class SymbolResolver:
    """Bidirectional symbol resolution across data sources."""

    def __init__(self):
        # Primary indices
        self._by_ibkr: Dict[str, ContractInfo] = {}       # "AAPL" → ContractInfo
        self._by_con_id: Dict[int, ContractInfo] = {}      # 265598 → ContractInfo
        self._by_yfinance: Dict[str, ContractInfo] = {}    # "AAPL" → ContractInfo
        self._by_bloomberg: Dict[str, ContractInfo] = {}   # "AAPL US Equity" → ContractInfo
        # For IBKR symbols that need exchange disambiguation
        self._by_ibkr_exchange: Dict[Tuple[str, str], ContractInfo] = {}  # ("AAPL","SMART") → ContractInfo

    @classmethod
    def from_contracts_toml(cls, path: Optional[Path] = None) -> "SymbolResolver":
        """Build resolver from contracts.toml."""
        resolver = cls()
        contracts_path = path or CONTRACTS_PATH

        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore

        if not contracts_path.exists():
            log.warning("contracts.toml not found at %s", contracts_path)
            return resolver

        with open(contracts_path, "rb") as f:
            data = tomllib.load(f)

        for contract in data.get("contracts", []):
            con_id = contract.get("con_id", 0)
            if con_id == 0:
                continue

            ibkr_sym = contract.get("symbol", "")
            exchange = contract.get("exchange", "")
            currency = contract.get("currency", "")
            leverage = contract.get("leverage", 1)
            sector = contract.get("sector", "")

            if not ibkr_sym:
                continue

            # Derive yfinance symbol
            yf_suffix = _EXCHANGE_TO_YF_SUFFIX.get(exchange, "")
            # Check if the IBKR symbol already has the suffix
            bare_sym = ibkr_sym
            if ibkr_sym.endswith(".L") or ibkr_sym.endswith(".T") or ibkr_sym.endswith(".HK") or ibkr_sym.endswith(".DE"):
                # Already has suffix — strip for bare, keep for yfinance
                yf_sym = ibkr_sym
                bare_sym = ibkr_sym.rsplit(".", 1)[0]
            elif yf_suffix:
                # Needs suffix for yfinance
                yf_sym = ibkr_sym + yf_suffix
            else:
                # US stocks: bare symbol works for both
                yf_sym = ibkr_sym

            # Derive Bloomberg ticker
            bbg_exch = _EXCHANGE_TO_BBG.get(exchange, "")
            bbg_sym = f"{bare_sym} {bbg_exch} Equity" if bbg_exch else bare_sym

            # Derive Reuters RIC
            reuters_suffix = _EXCHANGE_TO_REUTERS.get(exchange, "")
            reuters_sym = bare_sym + reuters_suffix if reuters_suffix else bare_sym

            info = ContractInfo(
                ibkr_symbol=ibkr_sym,
                exchange=exchange,
                currency=currency,
                con_id=con_id,
                leverage=leverage,
                sector=sector,
                yfinance=yf_sym,
                bloomberg=bbg_sym,
                reuters=reuters_sym,
            )

            # Index by all keys
            # Use ibkr_sym+exchange as primary key (handles duplicates like SOXL on LSEETF vs SMART)
            self_key = (ibkr_sym, exchange)
            resolver._by_ibkr_exchange[self_key] = info
            resolver._by_con_id[con_id] = info
            resolver._by_yfinance[yf_sym] = info
            resolver._by_bloomberg[bbg_sym] = info

            # Simple ibkr lookup (last one wins if duplicate bare symbols across exchanges)
            resolver._by_ibkr[ibkr_sym] = info

        log.info("SymbolResolver loaded: %d contracts, %d yfinance mappings",
                 len(resolver._by_con_id), len(resolver._by_yfinance))
        return resolver

    # ── IBKR → other sources ──────────────────────────────────────────

    def to_yfinance(self, ibkr_symbol: str, exchange: str = "") -> str:
        """Convert IBKR symbol to yfinance symbol."""
        if exchange:
            info = self._by_ibkr_exchange.get((ibkr_symbol, exchange))
            if info:
                return info.yfinance
        info = self._by_ibkr.get(ibkr_symbol)
        return info.yfinance if info else ibkr_symbol

    def to_bloomberg(self, ibkr_symbol: str, exchange: str = "") -> str:
        """Convert IBKR symbol to Bloomberg ticker."""
        if exchange:
            info = self._by_ibkr_exchange.get((ibkr_symbol, exchange))
            if info:
                return info.bloomberg
        info = self._by_ibkr.get(ibkr_symbol)
        return info.bloomberg if info else ibkr_symbol

    def to_reuters(self, ibkr_symbol: str, exchange: str = "") -> str:
        """Convert IBKR symbol to Reuters RIC."""
        if exchange:
            info = self._by_ibkr_exchange.get((ibkr_symbol, exchange))
            if info:
                return info.reuters
        info = self._by_ibkr.get(ibkr_symbol)
        return info.reuters if info else ibkr_symbol

    # ── Other sources → IBKR ──────────────────────────────────────────

    def from_yfinance(self, yf_symbol: str) -> Optional[ContractInfo]:
        """Resolve yfinance symbol to full contract info."""
        return self._by_yfinance.get(yf_symbol)

    def from_bloomberg(self, bbg_ticker: str) -> Optional[ContractInfo]:
        """Resolve Bloomberg ticker to full contract info."""
        return self._by_bloomberg.get(bbg_ticker)

    # ── Universal lookups ─────────────────────────────────────────────

    def by_con_id(self, con_id: int) -> Optional[ContractInfo]:
        """Lookup by IBKR contract ID (most reliable, immutable)."""
        return self._by_con_id.get(con_id)

    def by_ibkr(self, symbol: str, exchange: str = "") -> Optional[ContractInfo]:
        """Lookup by IBKR symbol, optionally disambiguated by exchange."""
        if exchange:
            return self._by_ibkr_exchange.get((symbol, exchange))
        return self._by_ibkr.get(symbol)

    # ── Batch operations ──────────────────────────────────────────────

    def ibkr_to_yfinance_batch(self, ibkr_symbols: List[str]) -> Dict[str, str]:
        """Convert a batch of IBKR symbols to yfinance symbols.

        Returns: {ibkr_symbol: yfinance_symbol} for all resolved symbols.
        """
        result = {}
        for sym in ibkr_symbols:
            yf = self.to_yfinance(sym)
            if yf:
                result[sym] = yf
        return result

    def yfinance_to_ibkr_batch(self, yf_symbols: List[str]) -> Dict[str, str]:
        """Convert a batch of yfinance symbols to IBKR symbols.

        Returns: {yfinance_symbol: ibkr_symbol} for all resolved symbols.
        """
        result = {}
        for sym in yf_symbols:
            info = self.from_yfinance(sym)
            if info:
                result[sym] = info.ibkr_symbol
        return result

    def all_yfinance_symbols(self) -> List[str]:
        """Get all yfinance symbols in the universe."""
        return list(self._by_yfinance.keys())

    def all_ibkr_symbols(self) -> List[Tuple[str, str]]:
        """Get all (ibkr_symbol, exchange) pairs."""
        return list(self._by_ibkr_exchange.keys())

    @property
    def count(self) -> int:
        """Total number of resolved contracts."""
        return len(self._by_con_id)

    # ── Debug / reporting ─────────────────────────────────────────────

    def format_table(self, symbols: Optional[List[str]] = None, limit: int = 20) -> str:
        """Format a comparison table showing all symbol formats."""
        lines = [f"{'IBKR':15s} {'Exchange':10s} {'yfinance':15s} {'Bloomberg':25s} {'Reuters':15s} {'ConID':>12s}"]
        lines.append("-" * 100)

        contracts = []
        if symbols:
            for sym in symbols:
                info = self._by_ibkr.get(sym)
                if info:
                    contracts.append(info)
        else:
            contracts = list(self._by_con_id.values())[:limit]

        for info in contracts:
            lines.append(
                f"{info.ibkr_symbol:15s} {info.exchange:10s} {info.yfinance:15s} "
                f"{info.bloomberg:25s} {info.reuters:15s} {info.con_id:>12d}"
            )
        return "\n".join(lines)


# ── Module-level singleton ────────────────────────────────────────────

_resolver: Optional[SymbolResolver] = None


def get_resolver() -> SymbolResolver:
    """Get or create the global SymbolResolver singleton."""
    global _resolver
    if _resolver is None:
        _resolver = SymbolResolver.from_contracts_toml()
    return _resolver


def reset_resolver() -> None:
    """Reset the singleton (e.g., after contracts.toml update)."""
    global _resolver
    _resolver = None


# ── CLI for testing ───────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    resolver = SymbolResolver.from_contracts_toml()
    print(f"\nLoaded {resolver.count} contracts\n")

    # Show sample conversions
    test_symbols = [
        "AAPL", "NVDA", "QQQ3.L", "NVD3.L", "CON3", "MST3", "3LNF",
        "SAP.DE", "7203.T", "9988.HK", "VXX", "TQQQ", "SOXL",
        "SPY", "HSBA.L", "BP.L", "3OIL",
    ]

    print(resolver.format_table(test_symbols))

    # If CLI arg provided, resolve it
    if len(sys.argv) > 1:
        query = sys.argv[1]
        print(f"\nLooking up: {query}")
        info = resolver.by_ibkr(query) or resolver.from_yfinance(query)
        if info:
            print(f"  IBKR:      {info.ibkr_symbol} ({info.exchange})")
            print(f"  yfinance:  {info.yfinance}")
            print(f"  Bloomberg: {info.bloomberg}")
            print(f"  Reuters:   {info.reuters}")
            print(f"  ConID:     {info.con_id}")
            print(f"  Currency:  {info.currency}")
            print(f"  Leverage:  {info.leverage}x")
        else:
            print(f"  NOT FOUND")
