"""
K-10: Single-Writer Actor Model for Broker API.
Only ONE coroutine talks to broker. Priority queue dispatching.
P0=EMERGENCY_FLATTEN, P1=TOXICITY_CANCEL, P2=HAWKES_EXIT, P3=TACHYON_ENTRY.
"""
import asyncio
import heapq
import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Coroutine, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class OrderPriority(IntEnum):
    EMERGENCY_FLATTEN = 0
    TOXICITY_CANCEL = 1
    HAWKES_EXIT = 2
    NORMAL_EXIT = 3
    TACHYON_ENTRY = 4
    NORMAL_ENTRY = 5


@dataclass(order=True)
class DispatchItem:
    priority: int
    timestamp: float = field(compare=False)
    ticker: str = field(compare=False)
    action: str = field(compare=False)  # 'buy', 'sell', 'cancel', 'flatten'
    params: dict = field(default_factory=dict, compare=False)
    callback: Optional[Any] = field(default=None, compare=False)


class ExecutionDispatcher:
    """Single-writer actor for all broker API interactions.

    Ensures only ONE coroutine communicates with broker at any time.
    Orders dispatched by priority — emergency flatten always first.
    Ticker-level locking prevents conflicting commands on same instrument.
    """

    def __init__(self, broker_api=None):
        self._queue: list = []  # heapq
        self._broker = broker_api
        self._locked_tickers: set = set()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_order: Any = None  # Last order placed (for introspection)

    async def submit(self, priority: OrderPriority, ticker: str, action: str,
                     params: Optional[dict] = None) -> bool:
        if ticker in self._locked_tickers and priority > OrderPriority.EMERGENCY_FLATTEN:
            logger.warning("DISPATCH: %s locked — rejecting %s %s", ticker, action, priority.name)
            return False
        item = DispatchItem(
            priority=priority.value,
            timestamp=asyncio.get_event_loop().time(),
            ticker=ticker,
            action=action,
            params=params or {},
        )
        heapq.heappush(self._queue, item)
        logger.info("DISPATCH: queued %s %s %s (depth=%d)", priority.name, action, ticker, len(self._queue))
        return True

    async def run(self) -> None:
        self._running = True
        logger.info("ExecutionDispatcher: started")
        while self._running:
            if not self._queue:
                await asyncio.sleep(0.01)
                continue
            item = heapq.heappop(self._queue)
            self._locked_tickers.add(item.ticker)
            try:
                await self._execute(item)
            except Exception as e:
                logger.error("DISPATCH ERROR: %s %s %s — %s", item.action, item.ticker, OrderPriority(item.priority).name, e)
            finally:
                self._locked_tickers.discard(item.ticker)

    async def _execute(self, item: DispatchItem) -> None:
        logger.info("DISPATCH: executing %s %s (priority=%s)", item.action, item.ticker, OrderPriority(item.priority).name)
        if self._broker is None:
            logger.warning("DISPATCH: no broker connected — dry run")
            return

        try:
            action = item.action.upper()

            # ===================================================================
            # ENTRY ORDERS
            # ===================================================================
            if action in ('BUY', 'SELL'):
                await self._execute_entry_order(item)

            # ===================================================================
            # EXIT ORDERS
            # ===================================================================
            elif action in ('CLOSE', 'FLATTEN', 'PARTIAL_EXIT'):
                await self._execute_exit_order(item)

            # ===================================================================
            # CANCEL/REPLACE
            # ===================================================================
            elif action == 'CANCEL':
                await self._execute_cancel_order(item)

            elif action == 'REPLACE':
                await self._execute_replace_order(item)

            # ===================================================================
            # EMERGENCY FLATTEN (highest priority)
            # ===================================================================
            elif action == 'EMERGENCY_FLATTEN':
                await self._execute_emergency_flatten(item)

            else:
                logger.warning("DISPATCH: unknown action '%s' for %s", action, item.ticker)

        except Exception as e:
            logger.error("DISPATCH ERROR in _execute: %s %s — %s", item.action, item.ticker, e, exc_info=True)
            raise

    async def _execute_entry_order(self, item: DispatchItem) -> None:
        """Route entry signal to broker (maker limit or market)."""
        params = item.params
        ticker = item.ticker
        side = item.action.upper()  # 'BUY' or 'SELL'
        quantity = int(params.get('quantity', 0))
        price = float(params.get('price', 0.0))
        limit_price = params.get('limit_price')

        if quantity <= 0:
            logger.warning("DISPATCH: invalid quantity %d for %s", quantity, ticker)
            return

        try:
            if limit_price and limit_price > 0:
                # Maker-only limit order (GTC)
                order = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._broker.place_maker_limit(
                        ticker=ticker,
                        side=side,
                        quantity=quantity,
                        price=limit_price,
                        tif='GTC'
                    )
                )
            else:
                # Market order (emergency only)
                order = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._broker.place_market_order(
                        ticker=ticker,
                        side=side,
                        quantity=quantity
                    )
                )

            if order:
                logger.info(
                    "DISPATCH: entry order placed: %s %s %d @%s | order_id=%s",
                    side, ticker, quantity, limit_price or "MKT", getattr(order, 'id', 'N/A')
                )
                self._last_order = order
            else:
                logger.error("DISPATCH: entry order rejected for %s %s", ticker, side)

        except Exception as e:
            logger.error("DISPATCH: entry order failed for %s — %s", ticker, e, exc_info=True)
            raise

    async def _execute_exit_order(self, item: DispatchItem) -> None:
        """Route exit signal to broker (market close)."""
        params = item.params
        ticker = item.ticker
        quantity = int(params.get('quantity', 0))

        if quantity <= 0:
            logger.warning("DISPATCH: invalid exit quantity %d for %s", quantity, ticker)
            return

        try:
            # Determine side (opposite of original entry)
            entry_side = params.get('entry_side', 'BUY')
            exit_side = 'SELL' if entry_side == 'BUY' else 'BUY'

            # Market close order
            order = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._broker.place_market_order(
                    ticker=ticker,
                    side=exit_side,
                    quantity=quantity
                )
            )

            if order:
                logger.info(
                    "DISPATCH: exit order placed: %s %s %d | order_id=%s",
                    exit_side, ticker, quantity, getattr(order, 'id', 'N/A')
                )
                self._last_order = order
            else:
                logger.error("DISPATCH: exit order rejected for %s", ticker)

        except Exception as e:
            logger.error("DISPATCH: exit order failed for %s — %s", ticker, e, exc_info=True)
            raise

    async def _execute_cancel_order(self, item: DispatchItem) -> None:
        """Cancel an open order by order ID."""
        params = item.params
        order_id = params.get('order_id')

        if not order_id:
            logger.warning("DISPATCH: no order_id provided for CANCEL")
            return

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._broker.cancel_order(order_id)
            )

            if result:
                logger.info("DISPATCH: order %s cancelled", order_id)
            else:
                logger.warning("DISPATCH: cancel failed for order %s", order_id)

        except Exception as e:
            logger.error("DISPATCH: cancel failed for order %s — %s", order_id, e, exc_info=True)
            raise

    async def _execute_replace_order(self, item: DispatchItem) -> None:
        """Replace/modify an open order (new price/quantity)."""
        params = item.params
        order_id = params.get('order_id')
        new_price = params.get('new_price')
        new_quantity = params.get('new_quantity')

        if not order_id or (not new_price and not new_quantity):
            logger.warning("DISPATCH: incomplete REPLACE params for %s", item.ticker)
            return

        try:
            order = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._broker.modify_order(
                    order_id=order_id,
                    new_price=new_price,
                    new_quantity=new_quantity
                )
            )

            if order:
                logger.info("DISPATCH: order %s replaced | price=%s qty=%s", order_id, new_price, new_quantity)
            else:
                logger.warning("DISPATCH: replace failed for order %s", order_id)

        except Exception as e:
            logger.error("DISPATCH: replace failed for order %s — %s", order_id, e, exc_info=True)
            raise

    async def _execute_emergency_flatten(self, item: DispatchItem) -> None:
        """Emergency flatten: close ALL positions for this ticker at market."""
        params = item.params
        ticker = item.ticker

        try:
            # Get current position from broker
            position = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._broker.get_position(ticker)
            )

            if position is None or position.get('quantity', 0) == 0:
                logger.info("DISPATCH: no position to flatten for %s", ticker)
                return

            quantity = int(abs(position['quantity']))
            side = 'SELL' if position['quantity'] > 0 else 'BUY'

            # Market close order
            order = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._broker.place_market_order(
                    ticker=ticker,
                    side=side,
                    quantity=quantity
                )
            )

            if order:
                logger.critical(
                    "DISPATCH: EMERGENCY FLATTEN executed: %s %s %d | order_id=%s",
                    side, ticker, quantity, getattr(order, 'id', 'N/A')
                )
                self._last_order = order
            else:
                logger.error("DISPATCH: EMERGENCY FLATTEN failed for %s", ticker)

        except Exception as e:
            logger.error("DISPATCH: EMERGENCY FLATTEN failed for %s — %s", ticker, e, exc_info=True)
            raise

    def start(self) -> None:
        self._task = asyncio.ensure_future(self.run())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    @property
    def queue_depth(self) -> int:
        return len(self._queue)
