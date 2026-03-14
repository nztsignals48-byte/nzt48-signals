"""
IBKR TWS Gateway for NZT-48.

Provides:
  1. Real-time Level 1 quotes (~50-100ms vs yfinance 60-90s)
  2. Maker-only limit orders (earn spread instead of paying it)
  3. Broker-side GTC stops (survive EC2 death)
  4. Fill latency data (toxic fill detection)

Uses ib_insync library (pip install ib_insync).
Harris (2003): "Trading & Exchanges"

Architecture: ib_insync runs in a dedicated thread with its own asyncio event
loop. All API calls are dispatched as coroutines via run_coroutine_threadsafe()
to avoid conflicts with the main engine's uvloop.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("nzt48.ibkr")

# H-06: Broker Failure Protocol constants
_BROKER_ACK_TIMEOUT_SOFT = 30.0   # seconds — trigger retry with exponential backoff
_BROKER_ACK_TIMEOUT_HARD = 60.0   # seconds — enter DEGRADED mode
_BACKOFF_DELAYS = [1, 2, 4, 8, 16]  # exponential backoff sequence (seconds)
_MAX_RETRY_ATTEMPTS = len(_BACKOFF_DELAYS)


class IBKRGateway:
    """IBKR TWS/IB Gateway wrapper.

    Runs ib_insync in a dedicated thread to avoid uvloop conflicts.
    Gracefully degrades if ib_insync is not installed or connection fails.
    All public methods return sensible defaults on failure.
    """

    def __init__(self, host: str = None, port: int = None, client_id: int = 2):
        self._host = host or os.environ.get("IBKR_HOST", "127.0.0.1")
        self._port = port or int(os.environ.get("IBKR_PORT", "4002"))
        self._client_id = client_id
        self._contracts: dict = {}
        self._last_prices: dict[str, float] = {}
        self._connected = False
        self.ib = None
        self._ib_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ib_thread: Optional[threading.Thread] = None

        # H-06: Broker Failure Protocol state
        self._degraded = False                   # True = DEGRADED mode (no new entries, monitor only)
        self._last_ack_time: float = 0.0         # epoch of last successful broker ack
        self._connectivity_failures: list = []   # log of (timestamp, error, portfolio_snapshot)
        self._consecutive_failures: int = 0      # consecutive failed order attempts

        try:
            from ib_insync import IB
            self.ib = IB()
        except ImportError:
            logger.warning("ib_insync not installed -- IBKR gateway disabled")

    # ---------------------------------------------------------------
    # H-06: BROKER FAILURE PROTOCOL
    # ---------------------------------------------------------------

    @property
    def is_degraded(self) -> bool:
        """True when broker is in DEGRADED mode (no new entries allowed)."""
        return self._degraded

    def _log_connectivity_failure(self, error: str, portfolio_state: Optional[dict] = None) -> None:
        """H-06: Log connectivity failure with timestamp and portfolio state."""
        ts = datetime.now(timezone.utc).isoformat()
        entry = {
            "timestamp": ts,
            "error": str(error),
            "portfolio_state": portfolio_state or {},
            "was_degraded": self._degraded,
            "consecutive_failures": self._consecutive_failures,
        }
        self._connectivity_failures.append(entry)
        # Keep only last 100 failures to prevent memory leak
        if len(self._connectivity_failures) > 100:
            self._connectivity_failures = self._connectivity_failures[-100:]
        logger.error("H-06 CONNECTIVITY_FAILURE: %s | error=%s | degraded=%s | consecutive=%d",
                     ts, error, self._degraded, self._consecutive_failures)

    def _enter_degraded_mode(self, reason: str) -> None:
        """H-06: Enter DEGRADED mode — no new entries, monitor only.
        Open positions rely on broker-side bracket orders (GTC stops)."""
        if not self._degraded:
            self._degraded = True
            logger.critical(
                "H-06 DEGRADED_MODE_ENTERED: %s | "
                "No new entries allowed. Open positions rely on broker-side bracket orders.",
                reason,
            )

    def _exit_degraded_mode(self) -> None:
        """H-06: Exit DEGRADED mode after connection recovery."""
        if self._degraded:
            self._degraded = False
            self._consecutive_failures = 0
            logger.info("H-06 DEGRADED_MODE_EXITED: Broker connection recovered, resuming normal operations")

    def send_order_with_retry(
        self,
        order_fn,
        *args,
        portfolio_state: Optional[dict] = None,
        **kwargs,
    ) -> dict:
        """H-06: Send order with exponential backoff retry on no-ack.

        If no ack within 30s: retry with backoff (1s, 2s, 4s, 8s, 16s).
        If no ack within 60s total: enter DEGRADED mode.
        Returns the order result dict or a DEGRADED rejection.
        """
        if self._degraded:
            logger.warning("H-06: Order rejected — system in DEGRADED mode (no new entries)")
            return {
                "order_id": -1,
                "status": "DEGRADED_REJECTED",
                "reason": "Broker in DEGRADED mode — no new entries allowed",
            }

        start_time = time.monotonic()
        last_error = None

        for attempt, delay in enumerate(_BACKOFF_DELAYS):
            elapsed = time.monotonic() - start_time

            # Hard timeout — enter DEGRADED
            if elapsed >= _BROKER_ACK_TIMEOUT_HARD:
                self._enter_degraded_mode(
                    f"No broker ack after {elapsed:.1f}s ({attempt} retries). Last error: {last_error}"
                )
                self._log_connectivity_failure(
                    f"Hard timeout after {elapsed:.1f}s", portfolio_state
                )
                return {
                    "order_id": -1,
                    "status": "DEGRADED_TIMEOUT",
                    "reason": f"No ack after {elapsed:.1f}s — entered DEGRADED mode",
                }

            try:
                result = order_fn(*args, **kwargs)
                # Check for successful ack
                status = result.get("status", "")
                if status not in ("ERROR", "REJECTED", ""):
                    self._last_ack_time = time.monotonic()
                    self._consecutive_failures = 0
                    return result
                else:
                    last_error = f"Order returned status={status}"
                    logger.warning(
                        "H-06: Order attempt %d/%d failed (status=%s), retrying in %ds",
                        attempt + 1, _MAX_RETRY_ATTEMPTS, status, delay,
                    )
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "H-06: Order attempt %d/%d exception: %s, retrying in %ds",
                    attempt + 1, _MAX_RETRY_ATTEMPTS, e, delay,
                )

            self._consecutive_failures += 1
            time.sleep(delay)

        # All retries exhausted
        total_elapsed = time.monotonic() - start_time
        if total_elapsed >= _BROKER_ACK_TIMEOUT_HARD:
            self._enter_degraded_mode(
                f"All {_MAX_RETRY_ATTEMPTS} retries exhausted in {total_elapsed:.1f}s"
            )
        self._log_connectivity_failure(
            f"All retries exhausted. Last error: {last_error}", portfolio_state
        )
        return {
            "order_id": -1,
            "status": "RETRY_EXHAUSTED",
            "reason": f"All {_MAX_RETRY_ATTEMPTS} retries failed. Last: {last_error}",
        }

    def cancel_all_resting_orders(self) -> int:
        """H-06 CRO-10: Cancel all resting orders before resuming after recovery.

        Resting limit orders may have executed while blind — cancel them all
        and let the system re-evaluate positions from scratch.
        Returns the number of orders cancelled.
        """
        if not self.ib or not self._connected:
            logger.warning("H-06 CRO-10: Cannot cancel resting orders — not connected")
            return 0

        cancelled = 0
        try:
            open_trades = self.ib.openTrades()
            for trade in open_trades:
                try:
                    self.ib.cancelOrder(trade.order)
                    cancelled += 1
                    logger.info(
                        "H-06 CRO-10: Cancelled resting order %d (%s %s %d)",
                        trade.order.orderId, trade.order.action,
                        trade.contract.symbol, trade.order.totalQuantity,
                    )
                except Exception as e:
                    logger.error("H-06 CRO-10: Failed to cancel order %d: %s",
                                 trade.order.orderId, e)
        except Exception as e:
            logger.error("H-06 CRO-10: Failed to enumerate open trades: %s", e)

        logger.info("H-06 CRO-10: Cancelled %d resting orders on recovery", cancelled)
        return cancelled

    def on_connection_recovered(self) -> None:
        """H-06: Called when broker connection is re-established.

        Protocol: FIRST cancel all resting orders (CRO-10), THEN exit DEGRADED mode.
        Resting limit orders may have filled while we were blind.
        """
        logger.info("H-06: Connection recovery initiated — executing CRO-10 protocol")

        # Step 1: Cancel all resting orders BEFORE resuming
        cancelled = self.cancel_all_resting_orders()
        logger.info("H-06: CRO-10 complete — %d resting orders cancelled", cancelled)

        # Step 2: Exit DEGRADED mode
        self._exit_degraded_mode()
        self._last_ack_time = time.monotonic()
        logger.info("H-06: Connection recovery complete — normal operations resumed")

    def get_connectivity_log(self) -> list:
        """H-06: Return connectivity failure log for diagnostics."""
        return list(self._connectivity_failures)

    def _dispatch(self, coro, timeout: float = 15.0) -> Any:
        """Run an async coroutine in the IB thread's event loop. Thread-safe."""
        if self._ib_loop is None or not self._ib_loop.is_running():
            raise RuntimeError("IB event loop not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._ib_loop)
        return future.result(timeout=timeout)

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        if self.ib is None:
            return False
        try:
            ready = threading.Event()
            connect_error: list = []

            def _ib_thread_main():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._ib_loop = loop

                async def _do_connect():
                    try:
                        await self.ib.connectAsync(
                            self._host, self._port,
                            clientId=self._client_id, timeout=15,
                        )
                        self.ib.reqMarketDataType(1)
                    except Exception as e:
                        connect_error.append(e)
                    finally:
                        ready.set()

                loop.run_until_complete(_do_connect())
                # Keep loop alive for subsequent async dispatches
                if not connect_error and self.ib.isConnected():
                    loop.run_forever()

            self._ib_thread = threading.Thread(
                target=_ib_thread_main, daemon=True, name="ibkr-gateway",
            )
            self._ib_thread.start()
            ready.wait(timeout=20)

            if connect_error:
                raise connect_error[0]

            self._connected = self.ib.isConnected()
            if self._connected:
                logger.info("IBKR connected: %s:%d (client %d) — reqMarketDataType(1) set",
                            self._host, self._port, self._client_id)
            else:
                logger.warning("IBKR connect completed but isConnected=False")
            return self._connected
        except Exception as e:
            logger.error("IBKR connection failed: %s", e)
            self._connected = False
            return False

    def disconnect(self) -> None:
        if self.ib and self._connected:
            try:
                self.ib.disconnect()
            except Exception:
                pass
            self._connected = False
            if self._ib_loop and self._ib_loop.is_running():
                self._ib_loop.call_soon_threadsafe(self._ib_loop.stop)
            logger.info("IBKR disconnected")

    # Known IBKR exchange/currency for ISA leveraged ETPs
    _LSE_CONTRACT_MAP: dict[str, tuple[str, str]] = {
        "3LUS": ("LSEETF", "GBP"),
        "SP5L": ("LSE", "GBP"),
    }
    _LSE_DEFAULT = ("LSEETF", "USD")

    def _get_contract(self, ticker: str):
        if not self.ib or not self._connected:
            return None
        if ticker not in self._contracts:
            from ib_insync import Stock
            symbol = ticker.replace(".L", "")
            exchange, currency = self._LSE_CONTRACT_MAP.get(symbol, self._LSE_DEFAULT)
            contract = Stock(symbol, exchange, currency)
            try:
                qualified = self._dispatch(
                    self.ib.qualifyContractsAsync(contract), timeout=10,
                )
                if qualified:
                    self._contracts[ticker] = qualified[0]
                elif exchange == "LSEETF":
                    # Fallback: try LSE/GBP
                    fallback = Stock(symbol, "LSE", "GBP")
                    qualified = self._dispatch(
                        self.ib.qualifyContractsAsync(fallback), timeout=10,
                    )
                    if qualified:
                        self._contracts[ticker] = qualified[0]
                    else:
                        logger.warning("Failed to qualify contract %s", ticker)
                        return None
                else:
                    logger.warning("Failed to qualify contract %s", ticker)
                    return None
            except Exception as e:
                logger.error("Failed to qualify contract %s: %s", ticker, e)
                return None
        return self._contracts[ticker]

    def get_last_price(self, ticker: str) -> float:
        """Real-time last price via IBKR snapshot."""
        contract = self._get_contract(ticker)
        if not contract:
            return 0.0
        try:
            async def _fetch():
                tickers = await self.ib.reqTickersAsync(contract)
                if tickers:
                    t = tickers[0]
                    return t.last if t.last and t.last > 0 else (t.close or 0.0)
                return 0.0

            price = self._dispatch(_fetch(), timeout=5)
            self._last_prices[ticker] = price
            return price
        except Exception as e:
            logger.error("get_last_price failed for %s: %s", ticker, e)
            return self._last_prices.get(ticker, 0.0)

    def get_bid_ask(self, ticker: str) -> tuple[float, float, int, int]:
        """Returns (bid, ask, bid_size, ask_size) for micro-price calculation."""
        contract = self._get_contract(ticker)
        if not contract:
            return (0.0, 0.0, 0, 0)
        try:
            async def _fetch():
                tickers = await self.ib.reqTickersAsync(contract)
                if tickers:
                    t = tickers[0]
                    return (
                        t.bid or 0.0, t.ask or 0.0,
                        int(t.bidSize or 0), int(t.askSize or 0),
                    )
                return (0.0, 0.0, 0, 0)

            return self._dispatch(_fetch(), timeout=5)
        except Exception as e:
            logger.error("get_bid_ask failed for %s: %s", ticker, e)
            return (0.0, 0.0, 0, 0)

    # ---------------------------------------------------------------
    # ORDER ROUTING
    # ---------------------------------------------------------------

    def place_maker_limit(self, ticker: str, direction: str, qty: int, price: float) -> dict:
        """Place a maker-only limit order at the bid (buy) or ask (sell)."""
        contract = self._get_contract(ticker)
        if not contract:
            return {"order_id": -1, "status": "REJECTED", "ticker": ticker, "type": "MAKER_LIMIT"}
        try:
            from ib_insync import LimitOrder
            action = "BUY" if direction.upper() == "LONG" else "SELL"
            order = LimitOrder(action, qty, price)
            order.tif = "GTC"
            order.outsideRth = False

            trade = self._dispatch(
                self.ib.placeOrderAsync(contract, order) if hasattr(self.ib, 'placeOrderAsync')
                else asyncio.coroutine(lambda: self.ib.placeOrder(contract, order))(),
                timeout=10,
            )
            logger.info("MAKER_LIMIT: %s %s %d @ %.4f", action, ticker, qty, price)
            return {
                "order_id": trade.order.orderId,
                "status": trade.orderStatus.status,
                "ticker": ticker,
                "type": "MAKER_LIMIT",
            }
        except Exception as e:
            logger.error("place_maker_limit failed: %s", e)
            return {"order_id": -1, "status": "ERROR", "ticker": ticker, "type": "MAKER_LIMIT"}

    def place_gtc_stop(self, ticker: str, direction: str, qty: int, stop_price: float) -> dict:
        """Place broker-side GTC stop that survives EC2 death."""
        contract = self._get_contract(ticker)
        if not contract:
            return {"order_id": -1, "stop_price": stop_price, "type": "GTC_CATASTROPHIC_STOP"}
        try:
            from ib_insync import StopOrder
            action = "SELL" if direction.upper() == "LONG" else "BUY"
            order = StopOrder(action, qty, stop_price)
            order.tif = "GTC"

            trade = self._dispatch(
                self.ib.placeOrderAsync(contract, order) if hasattr(self.ib, 'placeOrderAsync')
                else asyncio.coroutine(lambda: self.ib.placeOrder(contract, order))(),
                timeout=10,
            )
            logger.info("GTC_STOP: %s %s %d @ %.4f (Dead Man's Switch)", action, ticker, qty, stop_price)
            return {
                "order_id": trade.order.orderId,
                "stop_price": stop_price,
                "type": "GTC_CATASTROPHIC_STOP",
            }
        except Exception as e:
            logger.error("place_gtc_stop failed: %s", e)
            return {"order_id": -1, "stop_price": stop_price, "type": "GTC_CATASTROPHIC_STOP"}

    def update_gtc_stop(self, order_id: int, new_stop_price: float) -> None:
        """Update the GTC stop as the Sniper trail tightens."""
        if not self.ib or not self._connected:
            return
        try:
            for trade in self.ib.openTrades():
                if trade.order.orderId == order_id:
                    trade.order.auxPrice = new_stop_price
                    self.ib.placeOrder(trade.contract, trade.order)
                    logger.info("GTC_STOP_UPDATE: order %d -> %.4f", order_id, new_stop_price)
                    return
        except Exception as e:
            logger.error("update_gtc_stop failed: %s", e)

    def get_fill_latency_ms(self, order_id: int) -> float:
        """Returns fill latency in milliseconds for toxicity detection."""
        if not self.ib or not self._connected:
            return -1.0
        try:
            for fill in self.ib.fills():
                if fill.execution.orderId == order_id:
                    return fill.execution.time.timestamp() * 1000
        except Exception:
            pass
        return -1.0

    def cancel_order(self, order_id: int) -> None:
        """Cancel resting order."""
        if not self.ib or not self._connected:
            return
        try:
            for trade in self.ib.openTrades():
                if trade.order.orderId == order_id:
                    self.ib.cancelOrder(trade.order)
                    logger.info("ORDER_CANCELLED: %d", order_id)
                    return
        except Exception as e:
            logger.error("cancel_order failed: %s", e)

    def place_market_order(self, ticker: str, direction: str, qty: int) -> dict:
        """Emergency market order (flash crash hedge only)."""
        contract = self._get_contract(ticker)
        if not contract:
            return {"order_id": -1, "status": "REJECTED"}
        try:
            from ib_insync import MarketOrder
            action = "BUY" if direction.upper() in ("LONG", "BUY") else "SELL"
            order = MarketOrder(action, qty)

            trade = self._dispatch(
                self.ib.placeOrderAsync(contract, order) if hasattr(self.ib, 'placeOrderAsync')
                else asyncio.coroutine(lambda: self.ib.placeOrder(contract, order))(),
                timeout=10,
            )
            logger.info("MARKET_ORDER: %s %s %d", action, ticker, qty)
            return {"order_id": trade.order.orderId, "status": trade.orderStatus.status}
        except Exception as e:
            logger.error("place_market_order failed: %s", e)
            return {"order_id": -1, "status": "ERROR"}
