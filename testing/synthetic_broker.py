"""
NZT-48 SyntheticBroker — Local Matching Engine (AEGIS K-01)
============================================================
LSE ETP matching engine for deterministic testing. Replaces IBKR gateway
in paper/backtest mode with a fully controllable order book.

FIFO queue priority with configurable adverse selection, partial fills,
and latency injection. Every fill is reproducible given the same seed.

Adverse selection model:
  - 35% of limit orders filled AT the limit price experience immediate
    adverse movement (Easley, Lopez de Prado & O'Hara 2012: "Flow Toxicity
    and Liquidity in a High-Frequency World")
  - Configurable via adverse_selection_rate parameter

Fill model:
  - Market orders: immediate fill at best bid/ask + slippage
  - Limit orders: FIFO queue, fill when price crosses limit
  - Stop orders: convert to market when trigger hit
  - Partial fills: enabled for orders > partial_fill_threshold shares

This is a SKELETON — full Q2 implementation will add:
  - Realistic order book depth simulation
  - Latency injection (network + exchange matching)
  - Cross-impact between correlated ETPs
  - Intraday auction mechanics (open/close)

References:
  - Easley, Lopez de Prado & O'Hara (2012) "Flow Toxicity"
  - Harris (2003) "Trading and Exchanges"
  - Cont, Stoikov & Talreja (2010) "A Stochastic Model for Order Book Dynamics"
"""
from __future__ import annotations

import enum
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("nzt48.synthetic_broker")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OrderSide(str, enum.Enum):
    """Order direction."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, enum.Enum):
    """Supported order types."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(str, enum.Enum):
    """Order lifecycle states."""
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class FillType(str, enum.Enum):
    """How the fill was generated."""
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    ADVERSE = "ADVERSE"          # filled but adverse selection triggered


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Order:
    """Order submitted to the synthetic matching engine."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ticker: str = ""
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    quantity: int = 0
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0
    avg_fill_price: float = 0.0
    tag: str = ""                # free-form label (strategy ID, signal ID, etc.)


@dataclass
class Fill:
    """Execution report for a filled (or partially filled) order."""
    order_id: str = ""
    ticker: str = ""
    side: OrderSide = OrderSide.BUY
    quantity: int = 0
    price: float = 0.0
    fill_type: FillType = FillType.FULL
    adverse_selected: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    latency_ms: float = 0.0     # simulated exchange latency


@dataclass
class FillResult:
    """Result of submit_order — contains fill(s) or rejection reason."""
    success: bool = False
    order: Optional[Order] = None
    fills: list[Fill] = field(default_factory=list)
    rejection_reason: str = ""


@dataclass
class SyntheticPosition:
    """Net position for a single ticker within the synthetic broker."""
    ticker: str = ""
    quantity: int = 0            # positive = long, negative = short
    avg_entry_price: float = 0.0
    realised_pnl: float = 0.0
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# SyntheticBroker
# ---------------------------------------------------------------------------

class SyntheticBroker:
    """LSE ETP matching engine for testing. FIFO queue priority.

    Provides a fully deterministic, in-process broker that can replace
    IBKRGateway during backtests and paper validation runs.

    Parameters
    ----------
    adverse_selection_rate : float
        Probability that a limit fill experiences immediate adverse price
        movement (default 0.35 per Easley et al. 2012).
    latency_mean_ms : float
        Mean simulated matching latency in milliseconds (default 5.0).
    partial_fill_threshold : int
        Orders above this share count may receive partial fills (default 500).
    seed : int | None
        Random seed for reproducible fill simulation.
    """

    def __init__(
        self,
        adverse_selection_rate: float = 0.35,
        latency_mean_ms: float = 5.0,
        partial_fill_threshold: int = 500,
        seed: int | None = None,
    ):
        self._adverse_selection = adverse_selection_rate
        self._latency_mean_ms = latency_mean_ms
        self._partial_fill_threshold = partial_fill_threshold
        self._seed = seed

        # Internal state
        self._order_book: dict[str, list[Order]] = {}   # ticker -> FIFO queue
        self._orders: dict[str, Order] = {}              # order_id -> Order
        self._fills: list[Fill] = []                     # all fills in order
        self._positions: dict[str, SyntheticPosition] = {}  # ticker -> position
        self._prices: dict[str, float] = {}              # ticker -> last price

        logger.info(
            "SyntheticBroker initialised: adverse_sel=%.2f, latency=%.1fms, "
            "partial_threshold=%d, seed=%s",
            adverse_selection_rate, latency_mean_ms,
            partial_fill_threshold, seed,
        )

    # -------------------------------------------------------------------
    # Price feed (test harness injects prices)
    # -------------------------------------------------------------------

    def set_price(self, ticker: str, price: float) -> None:
        """Update the last-traded price for a ticker.

        The test harness calls this to advance the simulated market.
        After each price update, pending orders are checked for fills.

        Parameters
        ----------
        ticker : str
            LSE ticker (e.g. "QQQ3.L").
        price : float
            New last-traded price.
        """
        self._prices[ticker] = price
        # TODO (Q2): trigger matching engine sweep for pending orders

    # -------------------------------------------------------------------
    # Order management
    # -------------------------------------------------------------------

    async def submit_order(self, order: Order) -> FillResult:
        """Submit an order to the matching engine.

        For MARKET orders, fills immediately at current price + slippage.
        For LIMIT orders, enters the FIFO queue and fills when price crosses.
        For STOP orders, arms and converts to MARKET when trigger price hit.

        Parameters
        ----------
        order : Order
            The order to submit.

        Returns
        -------
        FillResult
            Contains fill(s) if immediately executed, or queued order state.
        """
        # TODO (Q2): implement matching logic
        #   1. Validate order fields (ticker, quantity > 0, etc.)
        #   2. For MARKET: fill at self._prices[ticker] + slippage
        #   3. For LIMIT: add to FIFO queue, check immediate fill
        #   4. For STOP: arm trigger, convert to MARKET when hit
        #   5. Apply adverse selection probability
        #   6. Apply partial fill logic for large orders
        #   7. Update self._positions
        #   8. Record Fill objects
        raise NotImplementedError("K-01 skeleton — Q2 implementation pending")

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending or open order.

        Parameters
        ----------
        order_id : str
            The UUID of the order to cancel.

        Returns
        -------
        bool
            True if the order was successfully cancelled, False if already
            filled or not found.
        """
        # TODO (Q2): remove from FIFO queue, set status to CANCELLED
        raise NotImplementedError("K-01 skeleton — Q2 implementation pending")

    async def cancel_all(self, ticker: str | None = None) -> int:
        """Cancel all pending orders, optionally filtered by ticker.

        Parameters
        ----------
        ticker : str | None
            If provided, only cancel orders for this ticker.

        Returns
        -------
        int
            Number of orders cancelled.
        """
        # TODO (Q2): iterate order book, cancel matching orders
        raise NotImplementedError("K-01 skeleton — Q2 implementation pending")

    # -------------------------------------------------------------------
    # Position & fill queries
    # -------------------------------------------------------------------

    def get_position(self, ticker: str) -> SyntheticPosition:
        """Get current net position for a ticker.

        Parameters
        ----------
        ticker : str
            LSE ticker.

        Returns
        -------
        SyntheticPosition
            Current position state. Returns zero-position if no trades.
        """
        return self._positions.get(ticker, SyntheticPosition(ticker=ticker))

    def get_all_positions(self) -> dict[str, SyntheticPosition]:
        """Get all non-zero positions.

        Returns
        -------
        dict[str, SyntheticPosition]
            Ticker -> position mapping for all tickers with open positions.
        """
        return {
            ticker: pos
            for ticker, pos in self._positions.items()
            if pos.quantity != 0
        }

    def get_fills(self, ticker: str | None = None) -> list[Fill]:
        """Get fill history, optionally filtered by ticker.

        Parameters
        ----------
        ticker : str | None
            If provided, only return fills for this ticker.

        Returns
        -------
        list[Fill]
            Fill records in chronological order.
        """
        if ticker is None:
            return list(self._fills)
        return [f for f in self._fills if f.ticker == ticker]

    def get_order(self, order_id: str) -> Order | None:
        """Look up an order by ID.

        Parameters
        ----------
        order_id : str
            The UUID of the order.

        Returns
        -------
        Order | None
            The order if found, else None.
        """
        return self._orders.get(order_id)

    def get_open_orders(self, ticker: str | None = None) -> list[Order]:
        """Get all orders with status PENDING or OPEN.

        Parameters
        ----------
        ticker : str | None
            If provided, only return open orders for this ticker.

        Returns
        -------
        list[Order]
            Open orders in submission order.
        """
        open_statuses = {OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED}
        result = [
            o for o in self._orders.values()
            if o.status in open_statuses
        ]
        if ticker is not None:
            result = [o for o in result if o.ticker == ticker]
        return sorted(result, key=lambda o: o.submitted_at)

    # -------------------------------------------------------------------
    # Portfolio summary
    # -------------------------------------------------------------------

    def get_portfolio_value(self) -> float:
        """Calculate total portfolio value (sum of position mark-to-market).

        Returns
        -------
        float
            Total unrealised + realised P&L across all positions.
        """
        total = 0.0
        for ticker, pos in self._positions.items():
            mark_price = self._prices.get(ticker, pos.avg_entry_price)
            unrealised = (mark_price - pos.avg_entry_price) * pos.quantity
            total += pos.realised_pnl + unrealised
        return total

    # -------------------------------------------------------------------
    # Reset
    # -------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all state — orders, fills, positions, prices.

        Use between test runs for clean state.
        """
        self._order_book.clear()
        self._orders.clear()
        self._fills.clear()
        self._positions.clear()
        self._prices.clear()
        logger.info("SyntheticBroker state reset")
