"""
Sprint 7A: Dynamic contract loading from contracts.toml.
Replaces all hardcoded PRIMARY_TICKERS lists across the codebase.
"""

from pathlib import Path
from typing import Dict, List
import logging

log = logging.getLogger(__name__)

CONFIG_DIR = Path("/app/config")
_cache: Dict[str, list] = {}


def load_all_symbols() -> List[str]:
    """Load ALL contract symbols from contracts.toml (IBKR format). Cached after first call."""
    if "symbols" in _cache:
        return _cache["symbols"]
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        contracts_path = CONFIG_DIR / "contracts.toml"
        if not contracts_path.exists():
            contracts_path = Path(__file__).parent.parent.parent / "config" / "contracts.toml"
        if contracts_path.exists():
            with open(contracts_path, "rb") as f:
                data = tomllib.load(f)
            symbols = [c["symbol"] for c in data.get("contracts", []) if c.get("symbol")]
            _cache["symbols"] = symbols
            log.info("Loaded %d contract symbols from %s", len(symbols), contracts_path)
            return symbols
    except Exception as e:
        log.warning("Failed to load contracts.toml: %s", e)
    return []


def load_yfinance_symbols() -> List[str]:
    """Load ALL contracts and convert to yfinance-compatible symbols.

    IBKR symbols differ from yfinance:
      - LSE (LSEETF): QQQ3.L → already correct
      - US (SMART): AAPL → already correct
      - XETRA/EURONEXT: SAP, TTE → already correct
      - TSE: 7203 → 7203.T
      - HKEX: 0700 → 0700.HK (zero-padded to 4 digits)
      - SGX: D05 → D05.SI
    """
    if "yf_symbols" in _cache:
        return _cache["yf_symbols"]
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        contracts_path = CONFIG_DIR / "contracts.toml"
        if not contracts_path.exists():
            contracts_path = Path(__file__).parent.parent.parent / "config" / "contracts.toml"
        if not contracts_path.exists():
            return []
        with open(contracts_path, "rb") as f:
            data = tomllib.load(f)

        symbols = []
        for c in data.get("contracts", []):
            sym = c.get("symbol", "")
            exchange = c.get("exchange", "")
            if not sym:
                continue
            if exchange == "LSEETF":
                yf_sym = f"{sym}.L" if not sym.endswith(".L") else sym
            elif exchange == "TSE":
                yf_sym = f"{sym}.T"
            elif exchange == "HKEX":
                yf_sym = f"{sym:>04s}.HK"
            elif exchange == "SGX":
                yf_sym = f"{sym}.SI"
            else:
                yf_sym = sym  # US (SMART), XETRA, EURONEXT — work as-is
            if yf_sym not in symbols:
                symbols.append(yf_sym)

        _cache["yf_symbols"] = symbols
        log.info("Loaded %d yfinance symbols from %s", len(symbols), contracts_path)
        return symbols
    except Exception as e:
        log.warning("Failed to load contracts.toml for yfinance: %s", e)
    return []


def load_lse_symbols() -> List[str]:
    """Load only LSE (.L suffix) symbols."""
    return [s for s in load_all_symbols() if s.endswith(".L")]


def load_symbols_by_exchange(exchange: str) -> List[str]:
    """Load symbols for a specific exchange (from contracts.toml exchange field)."""
    if f"exchange_{exchange}" in _cache:
        return _cache[f"exchange_{exchange}"]
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        contracts_path = CONFIG_DIR / "contracts.toml"
        if not contracts_path.exists():
            contracts_path = Path(__file__).parent.parent.parent / "config" / "contracts.toml"
        if contracts_path.exists():
            with open(contracts_path, "rb") as f:
                data = tomllib.load(f)
            symbols = [
                c["symbol"] for c in data.get("contracts", [])
                if c.get("symbol") and c.get("exchange") == exchange
            ]
            _cache[f"exchange_{exchange}"] = symbols
            return symbols
    except Exception as e:
        log.warning("Failed to load contracts for exchange %s: %s", exchange, e)
    return []


def load_leverage_map() -> Dict[str, int]:
    """Load ticker → leverage factor mapping from contracts.toml."""
    if "leverage" in _cache:
        return _cache["leverage"]
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        contracts_path = CONFIG_DIR / "contracts.toml"
        if not contracts_path.exists():
            contracts_path = Path(__file__).parent.parent.parent / "config" / "contracts.toml"
        if contracts_path.exists():
            with open(contracts_path, "rb") as f:
                data = tomllib.load(f)
            lev_map = {}
            for c in data.get("contracts", []):
                sym = c.get("symbol")
                lev = c.get("leverage", 1)
                if sym:
                    lev_map[sym] = lev
            _cache["leverage"] = lev_map
            return lev_map
    except Exception as e:
        log.warning("Failed to load leverage map: %s", e)
    return {}
