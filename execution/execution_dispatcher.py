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
        # TODO: Route to actual broker API calls
        pass

    def start(self) -> None:
        self._task = asyncio.ensure_future(self.run())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    @property
    def queue_depth(self) -> int:
        return len(self._queue)
