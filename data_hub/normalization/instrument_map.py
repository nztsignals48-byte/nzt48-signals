"""
data_hub/normalization/instrument_map.py
==========================================
ISIN/FIGI/ticker mapping and cache.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.data_hub.instrument_map")

_MAP_PATH = Path(__file__).parent.parent.parent / "data" / "instrument_meta.json"

# Hardcoded minimal known ISINs for the ISA universe (partial, for reference)
_KNOWN_INSTRUMENTS = {
    "QQQ3.L": {"name": "WisdomTree NASDAQ 100 3x Daily Leveraged", "isin": "IE00BLRPRL42",
                "exchange": "LSE", "currency": "USD", "leverage": 3},
    "3LUS.L": {"name": "WisdomTree US Equities 3x Daily Leveraged", "isin": "IE00BLRPRL42",
                "exchange": "LSE", "currency": "USD", "leverage": 3},
    "NVD3.L": {"name": "WisdomTree NVIDIA 3x Daily Leveraged", "isin": "IE000WWARXM5",
                "exchange": "LSE", "currency": "USD", "leverage": 3},
    "TSL3.L": {"name": "GraniteShares 3x Long Tesla Daily ETP", "isin": "IE00BMF7G516",
                "exchange": "LSE", "currency": "USD", "leverage": 3},
    "QQQ5.L": {"name": "WisdomTree NASDAQ 100 5x Daily Leveraged", "isin": "IE00BLRPRM59",
                "exchange": "LSE", "currency": "USD", "leverage": 5},
    "SP5L.L": {"name": "WisdomTree S&P 500 5x Daily Leveraged", "isin": "IE00BLRPRL42",
                "exchange": "LSE", "currency": "USD", "leverage": 5},
    "MU2.L":  {"name": "GraniteShares 2x Long Micron Daily ETP", "isin": "IE00BG0J4271",
                "exchange": "LSE", "currency": "USD", "leverage": 2},
}


def get_instrument_meta(ticker: str) -> Optional[dict]:
    """Get metadata for a ticker. Checks cache first, then hardcoded."""
    # Try local cache
    try:
        if _MAP_PATH.exists():
            data = json.loads(_MAP_PATH.read_text())
            if ticker in data:
                return data[ticker]
    except Exception:
        pass
    # Fall back to hardcoded
    return _KNOWN_INSTRUMENTS.get(ticker)


def refresh_instrument_cache(tickers: list[str]) -> dict:
    """
    Attempt to enrich instrument metadata.
    Currently uses yfinance info as a stub.
    """
    data = {}
    for ticker in tickers:
        known = _KNOWN_INSTRUMENTS.get(ticker, {})
        data[ticker] = {
            "ticker":   ticker,
            "exchange": known.get("exchange", "LSE"),
            "currency": known.get("currency", "GBP"),
            "isin":     known.get("isin", ""),
            "name":     known.get("name", ticker),
            "leverage": known.get("leverage", 1),
            "asset_class": "ETP",
        }
    try:
        _MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        import tempfile, os
        fd, tmp = tempfile.mkstemp(dir=_MAP_PATH.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2))
        Path(tmp).replace(_MAP_PATH)
    except Exception as exc:
        logger.warning("[INSTRUMENT_MAP] cache write failed: %s", exc)
    return data
