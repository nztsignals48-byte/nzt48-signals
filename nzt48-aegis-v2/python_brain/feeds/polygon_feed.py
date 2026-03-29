"""Polygon.io WebSocket Market Data Feed — Real-time redundancy layer.

Provides sub-second market data as backup when IBKR is down or delayed.
Uses Polygon.io WebSocket API for streaming trades, quotes, and aggregates.

Architecture:
  - WebSocket connection to wss://socket.polygon.io/stocks
  - Subscribes to T.* (trades), Q.* (quotes), AM.* (minute aggs)
  - Writes to shared Redis queue for engine consumption
  - Automatic reconnection with exponential backoff
  - Health monitoring via /health endpoint

Config: POLYGON_API_KEY env var or config.toml [feeds.polygon]
Books: 8 (infrastructure redundancy), 53 (data quality), 58 (failover)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

log = logging.getLogger("polygon_feed")


@dataclass
class PolygonConfig:
    """Polygon.io feed configuration."""
    api_key: str = ""
    base_url: str = "wss://socket.polygon.io/stocks"
    # Subscription channels
    subscribe_trades: bool = True       # T.* — last sale
    subscribe_quotes: bool = True       # Q.* — NBBO quotes (bid/ask)
    subscribe_aggregates: bool = True   # AM.* — minute aggregates
    # Reconnection
    max_reconnect_attempts: int = 50
    reconnect_base_delay_s: float = 1.0
    reconnect_max_delay_s: float = 60.0
    # Health
    heartbeat_interval_s: float = 30.0
    stale_threshold_s: float = 10.0
    # Rate limits (free tier: 5/min, paid: unlimited WebSocket)
    max_subscriptions: int = 200


@dataclass
class PolygonTick:
    """Normalized tick from Polygon.io."""
    symbol: str
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: int = 0
    timestamp_ms: int = 0
    source: str = "polygon"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "volume": self.volume,
            "timestamp_ms": self.timestamp_ms,
            "source": self.source,
        }


class PolygonFeed:
    """Polygon.io WebSocket market data feed with auto-reconnection.

    Usage:
        feed = PolygonFeed(config)
        feed.subscribe(["AAPL", "MSFT", "SPY"])
        feed.on_tick = my_callback
        feed.start()  # non-blocking, runs in background thread
    """

    def __init__(self, config: Optional[PolygonConfig] = None):
        self.config = config or PolygonConfig()
        if not self.config.api_key:
            self.config.api_key = os.environ.get("POLYGON_API_KEY", "")
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._subscriptions: Set[str] = set()
        self._reconnect_count = 0
        self._last_message_time = 0.0
        self._tick_count = 0
        self._error_count = 0
        # Per-symbol latest tick cache
        self._latest: Dict[str, PolygonTick] = {}
        # Tick ring buffer for latency analysis
        self._latency_ring: deque = deque(maxlen=1000)
        # Callbacks
        self.on_tick: Optional[Callable[[PolygonTick], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None
        self.on_reconnect: Optional[Callable[[], None]] = None
        # Redis integration (optional)
        self._redis = None

    def subscribe(self, symbols: List[str]):
        """Add symbols to subscription set."""
        for sym in symbols:
            self._subscriptions.add(sym.upper())
        if self._connected and self._ws:
            self._send_subscribe(symbols)

    def unsubscribe(self, symbols: List[str]):
        """Remove symbols from subscription set."""
        for sym in symbols:
            self._subscriptions.discard(sym.upper())
        if self._connected and self._ws:
            self._send_unsubscribe(symbols)

    def start(self):
        """Start WebSocket connection in background thread."""
        if not self.config.api_key:
            log.warning("POLYGON_API_KEY not set — Polygon feed disabled")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="polygon-feed")
        self._thread.start()
        log.info("Polygon feed started (subscriptions=%d)", len(self._subscriptions))

    def stop(self):
        """Stop the feed gracefully."""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Polygon feed stopped (ticks=%d, errors=%d)", self._tick_count, self._error_count)

    def get_latest(self, symbol: str) -> Optional[PolygonTick]:
        """Get latest tick for a symbol (thread-safe read from cache)."""
        return self._latest.get(symbol.upper())

    def is_healthy(self) -> bool:
        """Check if feed is connected and receiving data."""
        if not self._connected:
            return False
        age = time.time() - self._last_message_time
        return age < self.config.stale_threshold_s

    def health_dict(self) -> dict:
        """Health status for monitoring."""
        return {
            "connected": self._connected,
            "running": self._running,
            "subscriptions": len(self._subscriptions),
            "tick_count": self._tick_count,
            "error_count": self._error_count,
            "reconnect_count": self._reconnect_count,
            "last_message_age_s": round(time.time() - self._last_message_time, 1) if self._last_message_time else -1,
            "healthy": self.is_healthy(),
            "cached_symbols": len(self._latest),
        }

    # ── Internal ──

    def _run_loop(self):
        """Main WebSocket event loop with reconnection."""
        while self._running:
            try:
                self._connect_and_listen()
            except Exception as e:
                self._error_count += 1
                log.error("Polygon WebSocket error: %s", e)
                self._connected = False
                if self.on_disconnect:
                    self.on_disconnect()

            if not self._running:
                break

            # Exponential backoff reconnection
            self._reconnect_count += 1
            if self._reconnect_count > self.config.max_reconnect_attempts:
                log.error("Polygon feed: max reconnect attempts (%d) exceeded", self.config.max_reconnect_attempts)
                break
            delay = min(
                self.config.reconnect_base_delay_s * (2 ** min(self._reconnect_count, 6)),
                self.config.reconnect_max_delay_s,
            )
            log.info("Polygon feed: reconnecting in %.1fs (attempt %d)", delay, self._reconnect_count)
            time.sleep(delay)

    def _connect_and_listen(self):
        """Connect to Polygon WebSocket and process messages."""
        try:
            import websocket  # websocket-client library
        except ImportError:
            log.error("websocket-client not installed. Run: pip install websocket-client")
            self._running = False
            return

        url = self.config.base_url
        log.info("Connecting to Polygon: %s", url)

        ws = websocket.WebSocket()
        ws.connect(url, timeout=30)
        self._ws = ws
        self._connected = True
        self._last_message_time = time.time()

        # Authenticate
        auth_msg = json.dumps({"action": "auth", "params": self.config.api_key})
        ws.send(auth_msg)

        # Wait for auth response
        auth_resp = ws.recv()
        auth_data = json.loads(auth_resp)
        if isinstance(auth_data, list):
            for msg in auth_data:
                if msg.get("status") == "auth_success":
                    log.info("Polygon: authenticated successfully")
                    break
                elif msg.get("status") == "auth_failed":
                    log.error("Polygon: authentication failed — check API key")
                    self._running = False
                    return

        # Subscribe
        if self._subscriptions:
            self._send_subscribe(list(self._subscriptions))

        if self.on_reconnect and self._reconnect_count > 0:
            self.on_reconnect()
        self._reconnect_count = 0  # Reset on successful connection

        # Message loop
        while self._running:
            try:
                raw = ws.recv()
                if not raw:
                    continue
                self._last_message_time = time.time()
                messages = json.loads(raw)
                if isinstance(messages, list):
                    for msg in messages:
                        self._handle_message(msg)
                elif isinstance(messages, dict):
                    self._handle_message(messages)
            except websocket.WebSocketTimeoutException:
                continue
            except websocket.WebSocketConnectionClosedException:
                log.warning("Polygon: connection closed by server")
                break
            except json.JSONDecodeError:
                self._error_count += 1
                continue

        ws.close()
        self._connected = False

    def _send_subscribe(self, symbols: List[str]):
        """Send subscription message for symbols."""
        if not self._ws:
            return
        channels = []
        for sym in symbols:
            if self.config.subscribe_trades:
                channels.append(f"T.{sym}")
            if self.config.subscribe_quotes:
                channels.append(f"Q.{sym}")
            if self.config.subscribe_aggregates:
                channels.append(f"AM.{sym}")

        # Polygon limits subscriptions per message; batch if needed
        batch_size = 100
        for i in range(0, len(channels), batch_size):
            batch = channels[i:i + batch_size]
            msg = json.dumps({"action": "subscribe", "params": ",".join(batch)})
            try:
                self._ws.send(msg)
                log.info("Polygon: subscribed to %d channels (batch %d)", len(batch), i // batch_size + 1)
            except Exception as e:
                log.error("Polygon: subscribe failed: %s", e)

    def _send_unsubscribe(self, symbols: List[str]):
        """Send unsubscribe message."""
        if not self._ws:
            return
        channels = []
        for sym in symbols:
            channels.extend([f"T.{sym}", f"Q.{sym}", f"AM.{sym}"])
        msg = json.dumps({"action": "unsubscribe", "params": ",".join(channels)})
        try:
            self._ws.send(msg)
        except Exception as e:
            log.error("Polygon: unsubscribe failed: %s", e)

    def _handle_message(self, msg: dict):
        """Route incoming Polygon message to appropriate handler."""
        ev = msg.get("ev", "")
        if ev == "T":  # Trade
            self._handle_trade(msg)
        elif ev == "Q":  # Quote (NBBO)
            self._handle_quote(msg)
        elif ev == "AM":  # Minute aggregate
            self._handle_aggregate(msg)
        elif ev == "status":
            log.debug("Polygon status: %s", msg.get("message", ""))

    def _handle_trade(self, msg: dict):
        """Process trade event: {ev: "T", sym: "AAPL", p: 150.25, s: 100, t: 1234567890000}"""
        sym = msg.get("sym", "")
        if not sym:
            return
        tick = self._latest.get(sym) or PolygonTick(symbol=sym)
        tick.last = msg.get("p", tick.last)
        tick.volume = msg.get("s", 0)
        tick.timestamp_ms = msg.get("t", 0)
        self._latest[sym] = tick
        self._tick_count += 1
        self._emit_tick(tick)

    def _handle_quote(self, msg: dict):
        """Process NBBO quote: {ev: "Q", sym: "AAPL", bp: 150.24, ap: 150.26, t: ...}"""
        sym = msg.get("sym", "")
        if not sym:
            return
        tick = self._latest.get(sym) or PolygonTick(symbol=sym)
        tick.bid = msg.get("bp", tick.bid)
        tick.ask = msg.get("ap", tick.ask)
        tick.timestamp_ms = msg.get("t", 0)
        self._latest[sym] = tick
        self._tick_count += 1
        self._emit_tick(tick)

    def _handle_aggregate(self, msg: dict):
        """Process minute aggregate: {ev: "AM", sym: "AAPL", c: 150.30, v: 5000, s: ...}"""
        sym = msg.get("sym", "")
        if not sym:
            return
        tick = self._latest.get(sym) or PolygonTick(symbol=sym)
        tick.last = msg.get("c", tick.last)  # close of minute bar
        tick.volume = msg.get("v", 0)
        tick.timestamp_ms = msg.get("s", 0)  # start of aggregate window
        self._latest[sym] = tick
        self._tick_count += 1
        self._emit_tick(tick)

    def _emit_tick(self, tick: PolygonTick):
        """Emit tick to callback and optional Redis queue."""
        if self.on_tick:
            try:
                self.on_tick(tick)
            except Exception as e:
                log.error("Polygon tick callback error: %s", e)

        # Publish to Redis for engine consumption
        if self._redis:
            try:
                self._redis.publish("polygon:ticks", json.dumps(tick.to_dict()))
            except Exception:
                pass

    def connect_redis(self, redis_url: str = "redis://localhost:6379"):
        """Connect to Redis for cross-process tick distribution."""
        try:
            import redis
            self._redis = redis.Redis.from_url(redis_url)
            self._redis.ping()
            log.info("Polygon feed: Redis connected (%s)", redis_url)
        except Exception as e:
            log.warning("Polygon feed: Redis connection failed: %s", e)
            self._redis = None
