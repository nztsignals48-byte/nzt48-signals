"""MCP Integration — LSEG Data Pipeline — Book 196.

Model Context Protocol (MCP) server adapter for LSEG (London Stock
Exchange Group) market data.  Provides standardised tool definitions
that expose live quote and historical data to Claude/LLM agents.

Architecture:
  - LSEGDataConfig:       Configuration dataclass (API key env, endpoints)
  - LSEGMarketData:       Normalised market data record
  - LSEGDataFetcher:      HTTP client for LSEG API (placeholder — returns
                          None when API key is missing)
  - MCPToolHandler:       MCP tool implementations (get_quote, get_historical)
  - LSEGFeedIntegration:  Polling loop for periodic data refresh

NOTE: This is a PLACEHOLDER integration.  LSEG's enterprise MCP server
requires an enterprise license.  This module provides the interface so
that when API access becomes available, the system is ready.

Bridge.py integration:
    try:
        from python_brain.feeds.mcp_lseg_feed import (
            LSEGFeedIntegration, LSEGDataConfig,
            MCPToolHandler, LSEGDataFetcher,
        )
        _lseg_config = LSEGDataConfig()
        _lseg_feed = LSEGFeedIntegration(_lseg_config)
    except ImportError:
        _lseg_feed = None

Cross-references:
  - Book 173 (Command Station)
  - Book 193 (Claude-as-Trader)
  - Book 61 (AI Research Agent)
  - Book 142 (Agentic AI Orchestration)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("mcp_lseg_feed")

__all__ = [
    "LSEGDataConfig",
    "LSEGMarketData",
    "LSEGDataFetcher",
    "MCPToolHandler",
    "LSEGFeedIntegration",
]

# ---------------------------------------------------------------------------
# Paths (production)
# ---------------------------------------------------------------------------
_DATA_DIR = "/app/data"
_CACHE_DIR = os.path.join(_DATA_DIR, "lseg_cache")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_REFRESH_INTERVAL = 300  # 5 minutes
_DEFAULT_BASE_URL = "https://api.londonstockexchange.com/v1"
_DEFAULT_TIMEOUT_S = 10.0
_MAX_HISTORICAL_DAYS = 365


# ---------------------------------------------------------------------------
# LSEGDataConfig
# ---------------------------------------------------------------------------
@dataclass
class LSEGDataConfig:
    """Configuration for LSEG data feed.

    API key is read from the environment variable specified by
    api_key_env.  If not present, the feed runs in placeholder
    mode (all fetches return None).
    """

    api_key_env: str = "LSEG_API_KEY"
    base_url: str = _DEFAULT_BASE_URL
    instruments: List[str] = field(default_factory=list)
    refresh_interval_s: int = _DEFAULT_REFRESH_INTERVAL
    timeout_s: float = _DEFAULT_TIMEOUT_S
    cache_enabled: bool = True

    @property
    def api_key(self) -> Optional[str]:
        """Resolve API key from environment (never log this)."""
        return os.environ.get(self.api_key_env)

    @property
    def has_api_key(self) -> bool:
        """Check if API key is configured."""
        key = self.api_key
        return key is not None and len(key) > 0


# ---------------------------------------------------------------------------
# LSEGMarketData
# ---------------------------------------------------------------------------
@dataclass
class LSEGMarketData:
    """Normalised market data record from LSEG."""

    symbol: str = ""
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: int = 0
    timestamp: float = 0.0
    vwap: float = 0.0
    high: float = 0.0
    low: float = 0.0

    @property
    def mid(self) -> float:
        """Mid-price."""
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.last

    @property
    def spread(self) -> float:
        """Bid-ask spread."""
        if self.bid > 0 and self.ask > 0:
            return self.ask - self.bid
        return 0.0

    @property
    def spread_bps(self) -> float:
        """Spread in basis points relative to mid."""
        m = self.mid
        if m > 0:
            return (self.spread / m) * 10000.0
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to dictionary for MCP response."""
        return {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "mid": self.mid,
            "spread": self.spread,
            "spread_bps": self.spread_bps,
            "volume": self.volume,
            "timestamp": self.timestamp,
            "vwap": self.vwap,
            "high": self.high,
            "low": self.low,
        }


# ---------------------------------------------------------------------------
# LSEGDataFetcher
# ---------------------------------------------------------------------------
class LSEGDataFetcher:
    """HTTP client for LSEG market data API.

    PLACEHOLDER implementation: when API key is not configured, all
    fetches return None.  When the key is available, this will make
    real HTTP requests to LSEG endpoints.
    """

    def __init__(self, config: LSEGDataConfig) -> None:
        self._config = config
        self._cache: Dict[str, LSEGMarketData] = {}
        self._hist_cache: Dict[str, np.ndarray] = {}
        log.info(
            "LSEGDataFetcher init | api_available=%s base=%s instruments=%d",
            self._config.has_api_key,
            self._config.base_url,
            len(self._config.instruments),
        )

    def is_available(self) -> bool:
        """Check if the LSEG API is accessible.

        Returns:
            True if API key is configured and connectivity test passes.
        """
        if not self._config.has_api_key:
            log.debug("is_available: no API key configured")
            return False

        # In production, would do a lightweight health-check GET here.
        # For now, key presence implies availability.
        return True

    def fetch_quote(self, symbol: str) -> Optional[LSEGMarketData]:
        """Fetch live quote for a single symbol.

        Args:
            symbol: e.g. '3LTS' (3x Long FTSE), 'XUKS' (FTSE 100 short)

        Returns:
            LSEGMarketData if available, None otherwise.
        """
        if not self.is_available():
            log.debug("fetch_quote(%s): API not available — returning None", symbol)
            return None

        # ---- PLACEHOLDER: real implementation would do HTTP GET ----
        # url = f"{self._config.base_url}/quote/{symbol}"
        # headers = {"Authorization": f"Bearer {self._config.api_key}"}
        # resp = urllib.request.urlopen(
        #     urllib.request.Request(url, headers=headers),
        #     timeout=self._config.timeout_s,
        # )
        # data = json.loads(resp.read())
        # return LSEGMarketData(symbol=symbol, bid=data["bid"], ...)
        # ---- END PLACEHOLDER ----

        log.info("fetch_quote(%s): placeholder — real API call not implemented", symbol)
        return None

    def fetch_historical(
        self,
        symbol: str,
        days: int = 30,
    ) -> Optional[np.ndarray]:
        """Fetch historical OHLCV data.

        Args:
            symbol: instrument symbol.
            days:   number of calendar days of history (max 365).

        Returns:
            np.ndarray of shape (T, 5) [open, high, low, close, volume]
            or None if not available.
        """
        days = min(days, _MAX_HISTORICAL_DAYS)

        if not self.is_available():
            log.debug(
                "fetch_historical(%s, %d): API not available", symbol, days,
            )
            return None

        # ---- PLACEHOLDER: real implementation would do HTTP GET ----
        # url = f"{self._config.base_url}/historical/{symbol}?days={days}"
        # ...parse JSON into numpy array...
        # ---- END PLACEHOLDER ----

        log.info(
            "fetch_historical(%s, %d): placeholder — not implemented",
            symbol, days,
        )
        return None

    def fetch_batch(
        self, symbols: List[str],
    ) -> Dict[str, Optional[LSEGMarketData]]:
        """Fetch quotes for multiple symbols.

        Args:
            symbols: list of instrument symbols.

        Returns:
            Dict mapping symbol -> LSEGMarketData (or None).
        """
        results: Dict[str, Optional[LSEGMarketData]] = {}
        for sym in symbols:
            results[sym] = self.fetch_quote(sym)
        return results


# ---------------------------------------------------------------------------
# MCPToolHandler
# ---------------------------------------------------------------------------
class MCPToolHandler:
    """MCP tool handler for LSEG data.

    Implements the MCP tool interface: each tool has a name, description,
    input schema, and handler function.  These tools are registered with
    the MCP server and invoked by Claude.
    """

    def __init__(self, fetcher: LSEGDataFetcher) -> None:
        self._fetcher = fetcher
        log.info("MCPToolHandler init")

    def handle_get_quote(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """MCP tool: get_lseg_quote — fetch live quote.

        Args:
            params: {"symbol": str}

        Returns:
            MCP response dict with quote data or error.
        """
        symbol = params.get("symbol", "")
        if not symbol:
            return {
                "error": "Missing required parameter: symbol",
                "status": "error",
            }

        quote = self._fetcher.fetch_quote(symbol)
        if quote is None:
            return {
                "symbol": symbol,
                "status": "unavailable",
                "message": "LSEG API not configured or symbol not found.",
            }

        return {
            "status": "ok",
            "data": quote.to_dict(),
        }

    def handle_get_historical(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """MCP tool: get_lseg_historical — fetch historical OHLCV.

        Args:
            params: {"symbol": str, "days": int (optional, default 30)}

        Returns:
            MCP response dict with historical data or error.
        """
        symbol = params.get("symbol", "")
        days = int(params.get("days", 30))

        if not symbol:
            return {
                "error": "Missing required parameter: symbol",
                "status": "error",
            }

        hist = self._fetcher.fetch_historical(symbol, days=days)
        if hist is None:
            return {
                "symbol": symbol,
                "days": days,
                "status": "unavailable",
                "message": "LSEG API not configured or no data for symbol.",
            }

        return {
            "status": "ok",
            "symbol": symbol,
            "days": days,
            "n_bars": hist.shape[0],
            "columns": ["open", "high", "low", "close", "volume"],
            "data": hist.tolist(),
        }

    def list_tools(self) -> List[Dict[str, Any]]:
        """Return MCP tool registry.

        Returns:
            List of tool definitions conforming to MCP tool schema.
        """
        return [
            {
                "name": "get_lseg_quote",
                "description": (
                    "Fetch a live market quote from LSEG for a London Stock "
                    "Exchange instrument. Returns bid, ask, last, volume, "
                    "VWAP, and spread."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "LSE instrument symbol (e.g. '3LTS', 'XUKS')",
                        },
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "get_lseg_historical",
                "description": (
                    "Fetch historical OHLCV data from LSEG for a London "
                    "Stock Exchange instrument. Returns daily bars."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "LSE instrument symbol",
                        },
                        "days": {
                            "type": "integer",
                            "description": "Number of calendar days (default 30, max 365)",
                            "default": 30,
                        },
                    },
                    "required": ["symbol"],
                },
            },
            {
                "name": "lseg_status",
                "description": (
                    "Check whether the LSEG data feed is available and "
                    "configured. Returns connectivity status."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    def handle_status(self) -> Dict[str, Any]:
        """MCP tool: lseg_status — check feed availability."""
        available = self._fetcher.is_available()
        return {
            "status": "ok" if available else "unavailable",
            "api_configured": available,
            "base_url": self._fetcher._config.base_url,
            "n_instruments": len(self._fetcher._config.instruments),
        }

    def dispatch(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Route an MCP tool call to the appropriate handler.

        Args:
            tool_name: MCP tool name.
            params:    tool input parameters.

        Returns:
            MCP response dict.
        """
        handlers = {
            "get_lseg_quote": self.handle_get_quote,
            "get_lseg_historical": self.handle_get_historical,
            "lseg_status": lambda _: self.handle_status(),
        }

        handler = handlers.get(tool_name)
        if handler is None:
            log.warning("dispatch: unknown tool '%s'", tool_name)
            return {
                "error": f"Unknown tool: {tool_name}",
                "available_tools": list(handlers.keys()),
            }

        try:
            return handler(params)
        except Exception as exc:
            log.error("dispatch(%s) failed: %s", tool_name, exc, exc_info=True)
            return {
                "error": str(exc),
                "tool": tool_name,
                "status": "error",
            }


# ---------------------------------------------------------------------------
# LSEGFeedIntegration
# ---------------------------------------------------------------------------
class LSEGFeedIntegration:
    """Periodic polling integration for LSEG data.

    Runs a background thread that polls LSEG at a configured interval
    and invokes a callback with fresh data.  Thread-safe start/stop.
    """

    def __init__(self, config: LSEGDataConfig) -> None:
        self._config = config
        self._fetcher = LSEGDataFetcher(config)
        self._tool_handler = MCPToolHandler(self._fetcher)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_data: Dict[str, Optional[LSEGMarketData]] = {}
        log.info(
            "LSEGFeedIntegration init | interval=%ds instruments=%d",
            config.refresh_interval_s,
            len(config.instruments),
        )

    @property
    def fetcher(self) -> LSEGDataFetcher:
        """Access the underlying data fetcher."""
        return self._fetcher

    @property
    def tool_handler(self) -> MCPToolHandler:
        """Access the MCP tool handler."""
        return self._tool_handler

    @property
    def last_data(self) -> Dict[str, Optional[LSEGMarketData]]:
        """Most recently fetched data (thread-safe read)."""
        return dict(self._last_data)

    def start_polling(
        self,
        callback: Optional[Callable[[Dict[str, Optional[LSEGMarketData]]], None]] = None,
    ) -> None:
        """Start periodic data refresh in a background thread.

        Args:
            callback: optional function called with fresh data dict
                      after each polling cycle.
        """
        if self._thread is not None and self._thread.is_alive():
            log.warning("start_polling: already running")
            return

        if not self._fetcher.is_available():
            log.warning(
                "start_polling: LSEG API not available — "
                "polling will start but return empty data"
            )

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            args=(callback,),
            daemon=True,
            name="lseg-poll",
        )
        self._thread.start()
        log.info("start_polling: background thread started")

    def stop(self) -> None:
        """Stop the polling thread gracefully."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._config.refresh_interval_s + 5)
            if self._thread.is_alive():
                log.warning("stop: thread did not terminate in time")
            else:
                log.info("stop: polling thread stopped")
        self._thread = None

    def is_running(self) -> bool:
        """Check if the polling thread is active."""
        return self._thread is not None and self._thread.is_alive()

    # ---- private ----------------------------------------------------------

    def _poll_loop(
        self,
        callback: Optional[Callable[[Dict[str, Optional[LSEGMarketData]]], None]],
    ) -> None:
        """Background polling loop."""
        log.info("_poll_loop: started")
        while not self._stop_event.is_set():
            try:
                instruments = self._config.instruments
                if instruments:
                    data = self._fetcher.fetch_batch(instruments)
                    self._last_data = data
                    if callback is not None:
                        callback(data)
                    log.debug(
                        "_poll_loop: fetched %d instruments",
                        len(instruments),
                    )
            except Exception as exc:
                log.error("_poll_loop error: %s", exc, exc_info=True)

            # Wait for next cycle or stop signal
            self._stop_event.wait(timeout=self._config.refresh_interval_s)

        log.info("_poll_loop: stopped")
