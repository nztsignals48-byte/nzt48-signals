"""
data_hub/sources/ibkr_source.py
================================
IBKR primary truth source via ib_insync.

Connects to IB Gateway (headless, managed by IBC on EC2).
Provides real-time quotes and historical bars for LSE leveraged ETPs.

Architecture:
  - ib_insync runs in a dedicated thread with its own asyncio event loop
  - All API calls dispatched as coroutines via asyncio.run_coroutine_threadsafe()
  - This avoids conflicts with the main engine's uvloop
  - Contract cache to avoid re-qualifying on every call
  - ISA universe: .L suffix → LSE exchange, GBP currency

References:
  - ib_insync docs: https://ib-insync.readthedocs.io/
  - IBKR API: https://interactivebrokers.github.io/tws-api/
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import pandas as pd

logger = logging.getLogger("nzt48.data_hub.ibkr")

# H-07: Reconnection loop constants
_RECONNECT_INTERVAL_SEC = 5        # Attempt reconnect every 5 seconds
_RECONNECT_MAX_DURATION_SEC = 600  # 10 minutes total reconnection window
_DOCKER_RESTART_AFTER_FAILS = 3   # Issue Docker restart after 3 consecutive failures
_IB_GATEWAY_CONTAINER = "ib-gateway"  # Docker container name

# Timeframe mapping: DataHub interval → IBKR barSizeSetting
_INTERVAL_MAP = {
    "1m":  "1 min",
    "5m":  "5 mins",
    "15m": "15 mins",
    "30m": "30 mins",
    "1h":  "1 hour",
    "1d":  "1 day",
}

# Period mapping: DataHub period → IBKR durationStr
_PERIOD_MAP = {
    "1d":  "1 D",
    "2d":  "2 D",
    "5d":  "5 D",
    "1mo": "1 M",
    "3mo": "3 M",
    "6mo": "6 M",
    "1y":  "1 Y",
}


class IBKRSource:
    """Primary truth source: IBKR via IB Gateway + ib_insync."""

    NAME = "ibkr"
    IS_TRUTH = True
    IS_AVAILABLE = False  # Set dynamically on successful connect
    REQUIRED_CONFIG_KEY = "data.ibkr_host"
    RECOMMENDED_SETUP = "IB Gateway + IBC running in Docker; ib_insync installed"

    def __init__(self):
        self._ib = None
        self._connected = False
        self._contracts: dict = {}
        self._lock = threading.Lock()
        self._ib_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ib_thread: Optional[threading.Thread] = None

        self._host = os.environ.get("IBKR_HOST", "127.0.0.1")
        self._port = int(os.environ.get("IBKR_PORT", "4002"))
        self._client_id = int(os.environ.get("IBKR_CLIENT_ID", "10"))

        # H-07: Reconnection loop state
        self._reconnect_thread: Optional[threading.Thread] = None
        self._reconnect_active = False     # True while reconnection loop is running
        self._reconnect_consecutive_fails = 0
        self._telegram_alert_fn: Optional[Callable] = None  # Set by engine for Telegram alerts
        self._market_data_subscriptions: list[str] = []  # Tickers to re-subscribe on reconnect
        self._degraded = False             # H-07: DEGRADED mode after 10min timeout

        # Try to import ib_insync and connect on init
        try:
            from ib_insync import IB
            self._ib = IB()
            self._try_connect()
        except ImportError:
            logger.info("[IBKR] ib_insync not installed — source unavailable")

    def _dispatch(self, coro, timeout: float = 15.0) -> Any:
        """Run an async coroutine in the IB thread's event loop. Thread-safe."""
        if self._ib_loop is None or not self._ib_loop.is_running():
            raise RuntimeError("IB event loop not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._ib_loop)
        return future.result(timeout=timeout)

    def _try_connect(self) -> bool:
        """Attempt connection to IB Gateway in a dedicated thread."""
        if self._ib is None:
            return False
        if self._connected and self._ib.isConnected():
            return True
        try:
            ready = threading.Event()
            connect_error: list = []

            def _ib_thread_main():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._ib_loop = loop

                async def _do_connect():
                    try:
                        await self._ib.connectAsync(
                            self._host, self._port,
                            clientId=self._client_id,
                            timeout=15,
                            readonly=True,
                        )
                        self._ib.reqMarketDataType(1)
                    except Exception as e:
                        connect_error.append(e)
                    finally:
                        ready.set()

                loop.run_until_complete(_do_connect())
                # Keep loop alive for subsequent async dispatches
                if not connect_error and self._ib.isConnected():
                    loop.run_forever()

            self._ib_thread = threading.Thread(
                target=_ib_thread_main, daemon=True, name="ibkr-datahub",
            )
            self._ib_thread.start()
            ready.wait(timeout=20)

            if connect_error:
                raise connect_error[0]

            self._connected = self._ib.isConnected()
            if self._connected:
                IBKRSource.IS_AVAILABLE = True
                logger.info("[IBKR] Connected to IB Gateway at %s:%d (client %d) — reqMarketDataType(1) set",
                            self._host, self._port, self._client_id)
            else:
                IBKRSource.IS_AVAILABLE = False
                logger.warning("[IBKR] Connect completed but isConnected=False at %s:%d",
                               self._host, self._port)
            return self._connected
        except Exception as e:
            self._connected = False
            IBKRSource.IS_AVAILABLE = False
            logger.warning("[IBKR] Connection failed to %s:%d — %s", self._host, self._port, e)
            return False

    # Known IBKR exchange/currency for ISA leveraged ETPs
    # Most trade in USD on LSEETF; a few in GBP on LSE
    _LSE_CONTRACT_MAP: dict[str, tuple[str, str]] = {
        "3LUS": ("LSEETF", "GBP"),
        "SP5L": ("LSE", "GBP"),
    }
    _LSE_DEFAULT = ("LSEETF", "USD")  # Most leveraged ETPs

    def _get_contract(self, ticker: str):
        """Resolve ticker to IBKR contract. Caches qualified contracts."""
        if ticker in self._contracts:
            return self._contracts[ticker]

        from ib_insync import Stock

        if ticker.endswith(".L"):
            symbol = ticker[:-2]
            exchange, currency = self._LSE_CONTRACT_MAP.get(symbol, self._LSE_DEFAULT)
            contract = Stock(symbol, exchange, currency)
        else:
            contract = Stock(ticker, "SMART", "USD")

        try:
            qualified = self._dispatch(
                self._ib.qualifyContractsAsync(contract), timeout=10,
            )
            if qualified:
                self._contracts[ticker] = qualified[0]
                logger.debug("[IBKR] Qualified %s → %s/%s conId=%d",
                             ticker, exchange if ticker.endswith(".L") else "SMART",
                             currency if ticker.endswith(".L") else "USD",
                             qualified[0].conId)
                return qualified[0]

            # Fallback: try LSE/GBP if LSEETF/USD failed
            if ticker.endswith(".L") and exchange == "LSEETF":
                fallback = Stock(symbol, "LSE", "GBP")
                qualified = self._dispatch(
                    self._ib.qualifyContractsAsync(fallback), timeout=10,
                )
                if qualified:
                    self._contracts[ticker] = qualified[0]
                    logger.info("[IBKR] Qualified %s via LSE/GBP fallback", ticker)
                    return qualified[0]

            logger.warning("[IBKR] Failed to qualify contract for %s", ticker)
            return None
        except Exception as e:
            logger.warning("[IBKR] Contract qualification error for %s: %s", ticker, e)
            return None

    def fetch_bars(
        self,
        ticker: str,
        period: str = "5d",
        interval: str = "1h",
    ) -> Optional[pd.DataFrame]:
        """Fetch OHLCV bars from IBKR."""
        with self._lock:
            if not self._ensure_connected():
                return None

            contract = self._get_contract(ticker)
            if contract is None:
                return None

            duration = _PERIOD_MAP.get(period, "5 D")
            bar_size = _INTERVAL_MAP.get(interval, "1 hour")

            try:
                bars = self._dispatch(
                    self._ib.reqHistoricalDataAsync(
                        contract,
                        endDateTime="",
                        durationStr=duration,
                        barSizeSetting=bar_size,
                        whatToShow="TRADES",
                        useRTH=True,
                        formatDate=2,
                    ),
                    timeout=30,
                )

                if not bars:
                    logger.debug("[IBKR] No bars returned for %s (%s / %s)", ticker, period, interval)
                    return None

                df = pd.DataFrame([{
                    "timestamp": b.date,
                    "open":      b.open,
                    "high":      b.high,
                    "low":       b.low,
                    "close":     b.close,
                    "volume":    b.volume,
                } for b in bars])

                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                df.set_index("timestamp", inplace=True)
                df.index.name = "timestamp"

                logger.debug("[IBKR] Fetched %d bars for %s (%s / %s)", len(df), ticker, period, interval)
                return df

            except Exception as e:
                logger.warning("[IBKR] fetch_bars failed for %s: %s", ticker, e)
                self._connected = False
                IBKRSource.IS_AVAILABLE = False
                return None

    def fetch_quote(self, ticker: str) -> Optional[dict]:
        """Fetch real-time Level 1 quote from IBKR."""
        with self._lock:
            if not self._ensure_connected():
                return None

            contract = self._get_contract(ticker)
            if contract is None:
                return None

            try:
                tickers = self._dispatch(
                    self._ib.reqTickersAsync(contract), timeout=10,
                )

                if not tickers:
                    return None

                t = tickers[0]
                bid = t.bid if t.bid and t.bid > 0 else 0.0
                ask = t.ask if t.ask and t.ask > 0 else 0.0
                last = t.last if t.last and t.last > 0 else t.close or 0.0

                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else last
                spread_bps = ((ask - bid) / mid * 10000) if mid > 0 and bid > 0 and ask > 0 else 0.0

                return {
                    "ticker":    ticker,
                    "bid":       bid,
                    "ask":       ask,
                    "last":      last,
                    "bid_size":  int(t.bidSize or 0),
                    "ask_size":  int(t.askSize or 0),
                    "spread_bps": round(spread_bps, 2),
                    "source":    "ibkr",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as e:
                logger.warning("[IBKR] fetch_quote failed for %s: %s", ticker, e)
                self._connected = False
                IBKRSource.IS_AVAILABLE = False
                return None

    # ---------------------------------------------------------------
    # H-07: BACKGROUND RECONNECTION LOOP
    # ---------------------------------------------------------------

    def set_telegram_alert_fn(self, fn: Callable) -> None:
        """H-07: Set async Telegram alert function for reconnection notifications.

        The function should accept a single string argument (the alert message).
        It will be called from a background thread via asyncio if possible.
        """
        self._telegram_alert_fn = fn
        logger.info("[IBKR] H-07: Telegram alert function attached")

    def add_market_data_subscription(self, ticker: str) -> None:
        """H-07: Track ticker for re-subscription after reconnect."""
        if ticker not in self._market_data_subscriptions:
            self._market_data_subscriptions.append(ticker)

    def _send_telegram_alert(self, message: str) -> None:
        """H-07: Fire-and-forget Telegram alert from background thread."""
        if self._telegram_alert_fn is None:
            logger.info("[IBKR] H-07: No Telegram function — alert logged only: %s", message)
            return
        try:
            import asyncio as _aio
            try:
                loop = _aio.get_running_loop()
                loop.create_task(self._telegram_alert_fn(message))
            except RuntimeError:
                # No running loop — create one or use a new thread
                _new_loop = _aio.new_event_loop()
                _new_loop.run_until_complete(self._telegram_alert_fn(message))
                _new_loop.close()
        except Exception as e:
            logger.warning("[IBKR] H-07: Telegram alert failed: %s", e)

    def _docker_restart_ib_gateway(self) -> bool:
        """H-07: Issue Docker restart command for ib-gateway container.

        Returns True if the restart command succeeded.
        """
        logger.warning("[IBKR] H-07: Issuing Docker restart for container '%s'",
                       _IB_GATEWAY_CONTAINER)
        try:
            result = subprocess.run(
                ["docker", "restart", _IB_GATEWAY_CONTAINER],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                logger.info("[IBKR] H-07: Docker restart succeeded for '%s'",
                           _IB_GATEWAY_CONTAINER)
                return True
            else:
                logger.error("[IBKR] H-07: Docker restart failed: %s", result.stderr.strip())
                return False
        except subprocess.TimeoutExpired:
            logger.error("[IBKR] H-07: Docker restart timed out after 60s")
            return False
        except FileNotFoundError:
            logger.error("[IBKR] H-07: Docker command not found — cannot restart container")
            return False
        except Exception as e:
            logger.error("[IBKR] H-07: Docker restart exception: %s", e)
            return False

    def _resubscribe_market_data(self) -> None:
        """H-07: Re-subscribe to market data for tracked tickers after reconnect.

        Clears and re-qualifies contracts since the IB thread/loop has changed.
        """
        if not self._market_data_subscriptions:
            logger.info("[IBKR] H-07: No market data subscriptions to restore")
            return

        logger.info("[IBKR] H-07: Re-subscribing to %d tickers",
                    len(self._market_data_subscriptions))
        # Clear contract cache since the loop/connection has changed
        self._contracts.clear()
        resubscribed = 0
        for ticker in self._market_data_subscriptions:
            try:
                contract = self._get_contract(ticker)
                if contract:
                    resubscribed += 1
            except Exception as e:
                logger.warning("[IBKR] H-07: Re-subscribe failed for %s: %s", ticker, e)
        logger.info("[IBKR] H-07: Re-subscribed %d/%d tickers",
                    resubscribed, len(self._market_data_subscriptions))

    def start_reconnection_loop(self) -> None:
        """H-07: Start background reconnection loop when IS_AVAILABLE becomes False.

        Protocol:
        1. Attempt ib.connectAsync() every 5s for up to 10 min
        2. Log each attempt
        3. If reconnect succeeds, re-subscribe to market data
        4. If 3 consecutive fails, issue Docker restart for ib-gateway container
        5. After 10 min total: send Telegram alert, remain on yfinance fallback, set DEGRADED
        """
        if self._reconnect_active:
            logger.debug("[IBKR] H-07: Reconnection loop already active — skipping")
            return
        if self._ib is None:
            logger.warning("[IBKR] H-07: Cannot start reconnection — ib_insync not available")
            return

        self._reconnect_active = True
        self._reconnect_consecutive_fails = 0

        def _reconnect_loop():
            start_time = time.monotonic()
            attempt = 0
            docker_restarted = False

            logger.warning("[IBKR] H-07: Reconnection loop STARTED — will attempt for up to %ds",
                          _RECONNECT_MAX_DURATION_SEC)

            while self._reconnect_active:
                elapsed = time.monotonic() - start_time

                # 10-minute hard timeout
                if elapsed >= _RECONNECT_MAX_DURATION_SEC:
                    self._degraded = True
                    self._reconnect_active = False
                    alert_msg = (
                        "H-07 IBKR RECONNECTION FAILED\n"
                        f"Attempted for {elapsed:.0f}s ({attempt} attempts)\n"
                        "Remaining on yfinance fallback\n"
                        "Status: DEGRADED — manual intervention required"
                    )
                    logger.critical("[IBKR] %s", alert_msg)
                    self._send_telegram_alert(alert_msg)
                    return

                attempt += 1

                # Tear down old thread/loop before reconnecting
                if self._ib_loop and self._ib_loop.is_running():
                    self._ib_loop.call_soon_threadsafe(self._ib_loop.stop)
                    self._ib_loop = None

                if self._ib_thread and self._ib_thread.is_alive():
                    self._ib_thread.join(timeout=5)
                    self._ib_thread = None

                # Disconnect cleanly if still lingering
                try:
                    if self._ib.isConnected():
                        self._ib.disconnect()
                except Exception:
                    pass

                logger.info(
                    "[IBKR] H-07: Reconnect attempt %d (elapsed=%.0fs, consecutive_fails=%d)",
                    attempt, elapsed, self._reconnect_consecutive_fails,
                )

                # Attempt connection
                success = self._try_connect()

                if success:
                    logger.info("[IBKR] H-07: Reconnect SUCCEEDED on attempt %d (%.0fs elapsed)",
                               attempt, elapsed)
                    self._reconnect_consecutive_fails = 0
                    self._degraded = False
                    self._reconnect_active = False

                    # Re-subscribe to market data
                    try:
                        self._resubscribe_market_data()
                    except Exception as e:
                        logger.warning("[IBKR] H-07: Market data re-subscribe failed: %s", e)

                    self._send_telegram_alert(
                        f"H-07 IBKR RECONNECTED after {attempt} attempts ({elapsed:.0f}s)"
                    )
                    return

                # Failed attempt
                self._reconnect_consecutive_fails += 1

                # Docker restart after 3 consecutive failures (only once)
                if self._reconnect_consecutive_fails >= _DOCKER_RESTART_AFTER_FAILS and not docker_restarted:
                    logger.warning(
                        "[IBKR] H-07: %d consecutive failures — issuing Docker restart",
                        self._reconnect_consecutive_fails,
                    )
                    docker_restarted = self._docker_restart_ib_gateway()
                    if docker_restarted:
                        # Give Docker container time to start up
                        time.sleep(15)
                        self._reconnect_consecutive_fails = 0

                time.sleep(_RECONNECT_INTERVAL_SEC)

        self._reconnect_thread = threading.Thread(
            target=_reconnect_loop, daemon=True, name="ibkr-reconnect",
        )
        self._reconnect_thread.start()

    def _ensure_connected(self) -> bool:
        """Reconnect if disconnected. H-07: Triggers background reconnection loop."""
        if self._ib is None:
            return False
        if self._connected and self._ib.isConnected():
            return True
        self._connected = False
        IBKRSource.IS_AVAILABLE = False

        # H-07: Start background reconnection loop (non-blocking)
        self.start_reconnection_loop()

        # Still return False — caller falls back to yfinance
        return False

    @property
    def is_degraded(self) -> bool:
        """H-07: True when IBKR has been unavailable for >10min."""
        return self._degraded

    def disconnect(self):
        """Clean disconnect from IB Gateway."""
        # H-07: Stop reconnection loop if active
        self._reconnect_active = False
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            self._reconnect_thread.join(timeout=10)

        if self._ib and self._connected:
            try:
                self._ib.disconnect()
            except Exception:
                pass
            self._connected = False
            IBKRSource.IS_AVAILABLE = False
            if self._ib_loop and self._ib_loop.is_running():
                self._ib_loop.call_soon_threadsafe(self._ib_loop.stop)
            logger.info("[IBKR] Disconnected")

    @classmethod
    def availability(cls) -> dict:
        return {
            "name": cls.NAME,
            "is_truth": cls.IS_TRUTH,
            "is_available": cls.IS_AVAILABLE,
            "required_config_key": cls.REQUIRED_CONFIG_KEY,
            "recommended_setup": cls.RECOMMENDED_SETUP,
        }
