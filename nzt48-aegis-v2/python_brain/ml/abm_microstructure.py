"""Emergent Market Microstructure — Agent-Based Model — Book 154.

Simulates a Limit Order Book (LOB) with heterogeneous agent populations:
market makers, momentum traders, mean-reversion traders, and noise traders.
Emergent phenomena (flash crashes, liquidity vacuums, momentum cascades)
arise naturally from agent interactions without being explicitly coded.

The ABM produces meta-signals for AEGIS:
  - Price distribution forecasts (directional bias from simulation)
  - Flash crash probability (when market makers withdraw)
  - Liquidity depth (spread and order book imbalance)

Components:
  - OrderType: Enum for limit/market/cancel orders
  - Order: Single order in the LOB
  - LimitOrderBook: Matching engine with bid/ask books
  - MarketMakerAgent, MomentumAgent, MeanReversionAgent, NoiseAgent
  - ABMSimulator: Orchestrates agents and LOB

Bridge.py integration:
    try:
        from python_brain.ml.abm_microstructure import (
            ABMSimulator, LimitOrderBook, OrderType,
        )
    except ImportError:
        pass

    # Run forward simulation from current price:
    sim = ABMSimulator(initial_price=150.0)
    result = sim.run(n_steps=500)
    flash_crashes = result["flash_crashes"]
    price_series = result["prices"]
"""

from __future__ import annotations

import logging
import math
import os
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("abm_microstructure")

__all__ = [
    "OrderType",
    "Order",
    "LimitOrderBook",
    "MarketMakerAgent",
    "MomentumAgent",
    "MeanReversionAgent",
    "NoiseAgent",
    "ABMSimulator",
]

DATA_DIR = "/app/data/abm_microstructure"


# ---------------------------------------------------------------------------
# Enums & Dataclasses
# ---------------------------------------------------------------------------

class OrderType(Enum):
    """Types of orders in the simulated LOB."""
    LIMIT_BUY = "limit_buy"
    LIMIT_SELL = "limit_sell"
    MARKET_BUY = "market_buy"
    MARKET_SELL = "market_sell"
    CANCEL = "cancel"


@dataclass
class Order:
    """A single order in the limit order book.

    Attributes:
        order_id: Unique order identifier.
        agent_id: ID of the agent that placed the order.
        order_type: Type of order.
        price: Limit price (0.0 for market orders).
        quantity: Number of units.
        timestamp: Simulation step when order was placed.
    """
    order_id: int
    agent_id: int
    order_type: OrderType
    price: float
    quantity: int
    timestamp: int


# ---------------------------------------------------------------------------
# Limit Order Book
# ---------------------------------------------------------------------------

class LimitOrderBook:
    """Simulated limit order book with price-time priority matching.

    Maintains sorted bid and ask books. Market orders execute immediately
    against the best available limit orders. Limit orders rest in the book
    until matched or cancelled.
    """

    def __init__(self) -> None:
        self.bids: List[Order] = []   # sorted descending by price
        self.asks: List[Order] = []   # sorted ascending by price
        self.trades: List[Dict[str, Any]] = []
        self._order_map: Dict[int, Order] = {}
        self._next_order_id: int = 0

    def _new_order_id(self) -> int:
        """Generate a unique order ID."""
        oid = self._next_order_id
        self._next_order_id += 1
        return oid

    def add_order(self, order: Order) -> List[Dict[str, Any]]:
        """Add an order and attempt matching.

        Args:
            order: The order to add.

        Returns:
            List of trade dicts if any matching occurred.
        """
        if order.order_id == -1:
            order.order_id = self._new_order_id()

        fills: List[Dict[str, Any]] = []

        if order.order_type == OrderType.MARKET_BUY:
            fills = self._match_market_buy(order)
        elif order.order_type == OrderType.MARKET_SELL:
            fills = self._match_market_sell(order)
        elif order.order_type == OrderType.LIMIT_BUY:
            fills = self._match_limit_buy(order)
        elif order.order_type == OrderType.LIMIT_SELL:
            fills = self._match_limit_sell(order)
        elif order.order_type == OrderType.CANCEL:
            self.cancel_order(order.order_id)

        self.trades.extend(fills)
        return fills

    def cancel_order(self, order_id: int) -> bool:
        """Remove an order from the book by ID.

        Returns:
            True if the order was found and removed.
        """
        for book in (self.bids, self.asks):
            for i, o in enumerate(book):
                if o.order_id == order_id:
                    book.pop(i)
                    self._order_map.pop(order_id, None)
                    return True
        return False

    def _match_market_buy(self, order: Order) -> List[Dict[str, Any]]:
        """Match a market buy against the ask book."""
        fills: List[Dict[str, Any]] = []
        remaining = order.quantity

        while remaining > 0 and self.asks:
            best_ask = self.asks[0]
            fill_qty = min(remaining, best_ask.quantity)
            fills.append({
                "price": best_ask.price,
                "quantity": fill_qty,
                "buyer_id": order.agent_id,
                "seller_id": best_ask.agent_id,
                "timestamp": order.timestamp,
            })
            remaining -= fill_qty
            best_ask.quantity -= fill_qty
            if best_ask.quantity <= 0:
                self.asks.pop(0)
                self._order_map.pop(best_ask.order_id, None)

        return fills

    def _match_market_sell(self, order: Order) -> List[Dict[str, Any]]:
        """Match a market sell against the bid book."""
        fills: List[Dict[str, Any]] = []
        remaining = order.quantity

        while remaining > 0 and self.bids:
            best_bid = self.bids[0]
            fill_qty = min(remaining, best_bid.quantity)
            fills.append({
                "price": best_bid.price,
                "quantity": fill_qty,
                "buyer_id": best_bid.agent_id,
                "seller_id": order.agent_id,
                "timestamp": order.timestamp,
            })
            remaining -= fill_qty
            best_bid.quantity -= fill_qty
            if best_bid.quantity <= 0:
                self.bids.pop(0)
                self._order_map.pop(best_bid.order_id, None)

        return fills

    def _match_limit_buy(self, order: Order) -> List[Dict[str, Any]]:
        """Match a limit buy: cross with asks at or below limit price, then rest."""
        fills: List[Dict[str, Any]] = []
        remaining = order.quantity

        while remaining > 0 and self.asks and self.asks[0].price <= order.price:
            best_ask = self.asks[0]
            fill_qty = min(remaining, best_ask.quantity)
            fills.append({
                "price": best_ask.price,
                "quantity": fill_qty,
                "buyer_id": order.agent_id,
                "seller_id": best_ask.agent_id,
                "timestamp": order.timestamp,
            })
            remaining -= fill_qty
            best_ask.quantity -= fill_qty
            if best_ask.quantity <= 0:
                self.asks.pop(0)
                self._order_map.pop(best_ask.order_id, None)

        if remaining > 0:
            rest_order = Order(
                order_id=order.order_id,
                agent_id=order.agent_id,
                order_type=OrderType.LIMIT_BUY,
                price=order.price,
                quantity=remaining,
                timestamp=order.timestamp,
            )
            self._insert_bid(rest_order)

        return fills

    def _match_limit_sell(self, order: Order) -> List[Dict[str, Any]]:
        """Match a limit sell: cross with bids at or above limit price, then rest."""
        fills: List[Dict[str, Any]] = []
        remaining = order.quantity

        while remaining > 0 and self.bids and self.bids[0].price >= order.price:
            best_bid = self.bids[0]
            fill_qty = min(remaining, best_bid.quantity)
            fills.append({
                "price": best_bid.price,
                "quantity": fill_qty,
                "buyer_id": best_bid.agent_id,
                "seller_id": order.agent_id,
                "timestamp": order.timestamp,
            })
            remaining -= fill_qty
            best_bid.quantity -= fill_qty
            if best_bid.quantity <= 0:
                self.bids.pop(0)
                self._order_map.pop(best_bid.order_id, None)

        if remaining > 0:
            rest_order = Order(
                order_id=order.order_id,
                agent_id=order.agent_id,
                order_type=OrderType.LIMIT_SELL,
                price=order.price,
                quantity=remaining,
                timestamp=order.timestamp,
            )
            self._insert_ask(rest_order)

        return fills

    def _insert_bid(self, order: Order) -> None:
        """Insert a limit buy into the bid book (descending price order)."""
        inserted = False
        for i, existing in enumerate(self.bids):
            if order.price > existing.price:
                self.bids.insert(i, order)
                inserted = True
                break
        if not inserted:
            self.bids.append(order)
        self._order_map[order.order_id] = order

    def _insert_ask(self, order: Order) -> None:
        """Insert a limit sell into the ask book (ascending price order)."""
        inserted = False
        for i, existing in enumerate(self.asks):
            if order.price < existing.price:
                self.asks.insert(i, order)
                inserted = True
                break
        if not inserted:
            self.asks.append(order)
        self._order_map[order.order_id] = order

    # ------------------------------------------------------------------
    # Book Queries
    # ------------------------------------------------------------------

    def best_bid(self) -> Optional[float]:
        """Return the highest bid price, or None if empty."""
        return self.bids[0].price if self.bids else None

    def best_ask(self) -> Optional[float]:
        """Return the lowest ask price, or None if empty."""
        return self.asks[0].price if self.asks else None

    def spread(self) -> Optional[float]:
        """Return bid-ask spread, or None if either side is empty."""
        bb = self.best_bid()
        ba = self.best_ask()
        if bb is not None and ba is not None:
            return ba - bb
        return None

    def midprice(self) -> Optional[float]:
        """Return midpoint between best bid and best ask."""
        bb = self.best_bid()
        ba = self.best_ask()
        if bb is not None and ba is not None:
            return (bb + ba) / 2.0
        return None

    def book_imbalance(self) -> float:
        """Compute order book imbalance: (bid_vol - ask_vol) / (bid_vol + ask_vol).

        Returns:
            Float in [-1, 1]. Positive = more buying pressure.
        """
        bid_vol = sum(o.quantity for o in self.bids[:5])
        ask_vol = sum(o.quantity for o in self.asks[:5])
        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        return (bid_vol - ask_vol) / total


# ---------------------------------------------------------------------------
# Agent Types
# ---------------------------------------------------------------------------

class MarketMakerAgent:
    """Quotes both sides of the LOB, manages inventory risk.

    Widens spread and eventually withdraws when inventory becomes
    extreme — this withdrawal mechanism drives flash crashes.

    Args:
        agent_id: Unique identifier.
        fair_value: Initial fair value estimate.
        base_spread_bps: Base spread in basis points.
        max_inventory: Maximum absolute inventory before withdrawal.
        risk_aversion: Inventory skew coefficient.
    """

    def __init__(
        self,
        agent_id: int,
        fair_value: float,
        base_spread_bps: float = 5.0,
        max_inventory: int = 1000,
        risk_aversion: float = 0.001,
    ) -> None:
        self.agent_id = agent_id
        self.fair_value = fair_value
        self.base_spread_bps = base_spread_bps
        self.max_inventory = max_inventory
        self.risk_aversion = risk_aversion
        self.inventory: int = 0
        self.active: bool = True
        self.withdrawal_threshold: float = 0.8
        self._rng = np.random.default_rng(agent_id)

    def generate_orders(
        self,
        current_price: float,
        volatility: float,
        step: int,
    ) -> List[Order]:
        """Generate bid and ask limit orders.

        Args:
            current_price: Last traded price.
            volatility: Recent realised volatility.
            step: Current simulation step.

        Returns:
            List of 0-2 limit orders (bid and/or ask).
        """
        # Check withdrawal condition
        inventory_pct = abs(self.inventory) / self.max_inventory if self.max_inventory > 0 else 0
        if inventory_pct > self.withdrawal_threshold:
            self.active = False
            return []
        self.active = True

        # Update fair value estimate (adaptive)
        self.fair_value = 0.95 * self.fair_value + 0.05 * current_price

        # Inventory-adjusted fair value
        skew = -self.risk_aversion * self.inventory * current_price
        adjusted_fair = self.fair_value + skew

        # Volatility-adjusted spread
        vol_multiplier = max(1.0, volatility * 100)
        half_spread = adjusted_fair * (self.base_spread_bps / 10000.0) * vol_multiplier

        bid_price = round(adjusted_fair - half_spread, 4)
        ask_price = round(adjusted_fair + half_spread, 4)
        qty = max(1, int(self._rng.integers(10, 50)))

        orders: List[Order] = []
        if bid_price > 0:
            orders.append(Order(
                order_id=-1, agent_id=self.agent_id,
                order_type=OrderType.LIMIT_BUY,
                price=bid_price, quantity=qty, timestamp=step,
            ))
        if ask_price > bid_price:
            orders.append(Order(
                order_id=-1, agent_id=self.agent_id,
                order_type=OrderType.LIMIT_SELL,
                price=ask_price, quantity=qty, timestamp=step,
            ))
        return orders

    def update_inventory(self, fills: List[Dict[str, Any]]) -> None:
        """Update inventory based on trade fills."""
        for fill in fills:
            if fill.get("buyer_id") == self.agent_id:
                self.inventory += fill["quantity"]
            if fill.get("seller_id") == self.agent_id:
                self.inventory -= fill["quantity"]


class MomentumAgent:
    """Chases price trends with market orders.

    Computes short-term momentum and fires market orders when
    momentum exceeds a threshold. Creates trends and cascades.

    Args:
        agent_id: Unique identifier.
        lookback: Number of bars to measure trend.
        threshold: Minimum momentum to trigger a trade.
    """

    def __init__(
        self,
        agent_id: int,
        lookback: int = 10,
        threshold: float = 0.002,
    ) -> None:
        self.agent_id = agent_id
        self.lookback = lookback
        self.threshold = threshold
        self._rng = np.random.default_rng(agent_id + 1000)

    def generate_orders(
        self,
        prices: List[float],
        step: int,
    ) -> List[Order]:
        """Generate market order if momentum exceeds threshold.

        Args:
            prices: Recent price history.
            step: Current simulation step.

        Returns:
            List of 0-1 market orders.
        """
        if len(prices) < self.lookback + 1:
            return []

        window = prices[-self.lookback:]
        momentum = (window[-1] - window[0]) / window[0] if window[0] > 0 else 0.0

        if abs(momentum) < self.threshold:
            return []

        qty = max(1, int(self._rng.integers(5, 30)))
        if momentum > 0:
            return [Order(
                order_id=-1, agent_id=self.agent_id,
                order_type=OrderType.MARKET_BUY,
                price=0.0, quantity=qty, timestamp=step,
            )]
        else:
            return [Order(
                order_id=-1, agent_id=self.agent_id,
                order_type=OrderType.MARKET_SELL,
                price=0.0, quantity=qty, timestamp=step,
            )]


class MeanReversionAgent:
    """Bets on price returning to a moving average.

    Places limit orders at mean-reverting levels. Stabilises prices
    and provides counter-trend liquidity.

    Args:
        agent_id: Unique identifier.
        lookback: Window for computing the mean.
        z_threshold: Z-score threshold to trigger a trade.
    """

    def __init__(
        self,
        agent_id: int,
        lookback: int = 30,
        z_threshold: float = 1.5,
    ) -> None:
        self.agent_id = agent_id
        self.lookback = lookback
        self.z_threshold = z_threshold
        self._rng = np.random.default_rng(agent_id + 2000)

    def generate_orders(
        self,
        prices: List[float],
        step: int,
    ) -> List[Order]:
        """Generate limit orders when price deviates from mean.

        Args:
            prices: Recent price history.
            step: Current simulation step.

        Returns:
            List of 0-1 limit orders.
        """
        if len(prices) < self.lookback:
            return []

        window = np.array(prices[-self.lookback:])
        mean_price = window.mean()
        std_price = window.std()
        if std_price < 1e-8:
            return []

        z_score = (prices[-1] - mean_price) / std_price
        qty = max(1, int(self._rng.integers(10, 40)))

        if z_score > self.z_threshold:
            # Price too high — place limit sell at mean
            return [Order(
                order_id=-1, agent_id=self.agent_id,
                order_type=OrderType.LIMIT_SELL,
                price=round(mean_price + std_price * 0.5, 4),
                quantity=qty, timestamp=step,
            )]
        elif z_score < -self.z_threshold:
            # Price too low — place limit buy at mean
            return [Order(
                order_id=-1, agent_id=self.agent_id,
                order_type=OrderType.LIMIT_BUY,
                price=round(mean_price - std_price * 0.5, 4),
                quantity=qty, timestamp=step,
            )]
        return []


class NoiseAgent:
    """Random trader providing volume and liquidity.

    Submits random market/limit orders with no directional intelligence.
    Represents uninformed flow in the market.

    Args:
        agent_id: Unique identifier.
        market_order_prob: Probability of placing a market vs limit order.
    """

    def __init__(
        self,
        agent_id: int,
        market_order_prob: float = 0.3,
    ) -> None:
        self.agent_id = agent_id
        self.market_order_prob = market_order_prob
        self._rng = np.random.default_rng(agent_id + 3000)

    def generate_orders(
        self,
        current_price: float,
        step: int,
    ) -> List[Order]:
        """Generate a random order with probability ~50% per step.

        Args:
            current_price: Last traded price.
            step: Current simulation step.

        Returns:
            List of 0-1 orders.
        """
        if self._rng.random() < 0.5:
            return []

        qty = max(1, int(self._rng.integers(1, 20)))
        is_buy = self._rng.random() < 0.5
        is_market = self._rng.random() < self.market_order_prob

        if is_market:
            otype = OrderType.MARKET_BUY if is_buy else OrderType.MARKET_SELL
            return [Order(
                order_id=-1, agent_id=self.agent_id,
                order_type=otype, price=0.0,
                quantity=qty, timestamp=step,
            )]
        else:
            # Limit order near current price
            offset = self._rng.uniform(0.001, 0.01) * current_price
            if is_buy:
                price = round(current_price - offset, 4)
                otype = OrderType.LIMIT_BUY
            else:
                price = round(current_price + offset, 4)
                otype = OrderType.LIMIT_SELL
            return [Order(
                order_id=-1, agent_id=self.agent_id,
                order_type=otype, price=max(0.01, price),
                quantity=qty, timestamp=step,
            )]


# ---------------------------------------------------------------------------
# ABM Simulator
# ---------------------------------------------------------------------------

class ABMSimulator:
    """Agent-Based Model simulator orchestrating LOB + agents.

    Creates agent populations, runs the simulation loop, and collects
    emergent statistics (price series, spread dynamics, flash crashes).

    Args:
        initial_price: Starting price for the simulation.
        n_mm: Number of market maker agents.
        n_momentum: Number of momentum agents.
        n_mr: Number of mean-reversion agents.
        n_noise: Number of noise agents.
        seed: Random seed.
    """

    def __init__(
        self,
        initial_price: float = 100.0,
        n_mm: int = 5,
        n_momentum: int = 20,
        n_mr: int = 20,
        n_noise: int = 50,
        seed: Optional[int] = None,
    ) -> None:
        self.initial_price = initial_price
        self.n_mm = n_mm
        self.n_momentum = n_momentum
        self.n_mr = n_mr
        self.n_noise = n_noise
        self.rng = np.random.default_rng(seed)

        self.lob = LimitOrderBook()
        self.market_makers: List[MarketMakerAgent] = []
        self.momentum_agents: List[MomentumAgent] = []
        self.mr_agents: List[MeanReversionAgent] = []
        self.noise_agents: List[NoiseAgent] = []

        self._create_agents()
        log.info(
            "ABMSimulator initialised: price=%.2f, mm=%d, mom=%d, mr=%d, noise=%d",
            initial_price, n_mm, n_momentum, n_mr, n_noise,
        )

    def _create_agents(self) -> None:
        """Instantiate all agent populations."""
        aid = 0
        for _ in range(self.n_mm):
            spread_bps = float(self.rng.uniform(3, 10))
            self.market_makers.append(MarketMakerAgent(
                agent_id=aid, fair_value=self.initial_price,
                base_spread_bps=spread_bps,
            ))
            aid += 1

        for _ in range(self.n_momentum):
            lb = int(self.rng.integers(5, 30))
            thresh = float(self.rng.uniform(0.001, 0.005))
            self.momentum_agents.append(MomentumAgent(
                agent_id=aid, lookback=lb, threshold=thresh,
            ))
            aid += 1

        for _ in range(self.n_mr):
            lb = int(self.rng.integers(15, 60))
            z = float(self.rng.uniform(1.0, 2.5))
            self.mr_agents.append(MeanReversionAgent(
                agent_id=aid, lookback=lb, z_threshold=z,
            ))
            aid += 1

        for _ in range(self.n_noise):
            mp = float(self.rng.uniform(0.2, 0.5))
            self.noise_agents.append(NoiseAgent(
                agent_id=aid, market_order_prob=mp,
            ))
            aid += 1

    def run(self, n_steps: int = 1000) -> Dict[str, Any]:
        """Run the ABM simulation for n_steps.

        Each step:
          1. Market makers quote
          2. Momentum agents react to recent trend
          3. Mean-reversion agents react to deviation
          4. Noise agents submit random orders
          5. All orders processed through LOB
          6. Statistics collected

        Args:
            n_steps: Number of simulation ticks.

        Returns:
            Dict with:
              - prices: list of midprices
              - spreads: list of bid-ask spreads
              - volumes: list of per-step volume
              - flash_crashes: list of detected flash crash dicts
              - mm_active_pct: list of market maker activity percentage
              - book_imbalance: list of order book imbalance
        """
        prices: List[float] = [self.initial_price]
        spreads: List[float] = []
        volumes: List[int] = []
        mm_active_pct: List[float] = []
        book_imbalance: List[float] = []

        log.info("ABM simulation starting: %d steps", n_steps)
        t0 = time.time()

        for step in range(n_steps):
            step_fills: List[Dict[str, Any]] = []
            current_price = prices[-1]

            # Compute recent volatility
            if len(prices) >= 20:
                recent = np.array(prices[-20:])
                rets = np.diff(recent) / recent[:-1]
                vol = float(rets.std()) if len(rets) > 1 else 0.01
            else:
                vol = 0.01

            # 1. Market makers quote
            for mm in self.market_makers:
                orders = mm.generate_orders(current_price, vol, step)
                for o in orders:
                    fills = self.lob.add_order(o)
                    step_fills.extend(fills)

            # 2. Momentum agents
            for ma in self.momentum_agents:
                orders = ma.generate_orders(prices, step)
                for o in orders:
                    fills = self.lob.add_order(o)
                    step_fills.extend(fills)

            # 3. Mean-reversion agents
            for mr in self.mr_agents:
                orders = mr.generate_orders(prices, step)
                for o in orders:
                    fills = self.lob.add_order(o)
                    step_fills.extend(fills)

            # 4. Noise agents
            for na in self.noise_agents:
                orders = na.generate_orders(current_price, step)
                for o in orders:
                    fills = self.lob.add_order(o)
                    step_fills.extend(fills)

            # Update market maker inventories
            for mm in self.market_makers:
                mm.update_inventory(step_fills)

            # Collect step statistics
            mid = self.lob.midprice()
            if mid is not None:
                prices.append(mid)
            elif step_fills:
                # Use last fill price
                prices.append(step_fills[-1]["price"])
            else:
                prices.append(current_price)

            sp = self.lob.spread()
            spreads.append(sp if sp is not None else 0.0)
            volumes.append(sum(f["quantity"] for f in step_fills))
            mm_active_pct.append(
                sum(1 for mm in self.market_makers if mm.active) / max(1, len(self.market_makers))
            )
            book_imbalance.append(self.lob.book_imbalance())

            # Periodic clean: remove stale orders (older than 100 steps)
            if step % 50 == 0:
                self._clean_stale_orders(step, max_age=100)

        elapsed = time.time() - t0

        # Detect flash crashes
        flash_crashes = self.detect_flash_crash(prices)

        log.info(
            "ABM simulation complete in %.2fs: %d steps, %d flash crashes, "
            "final_price=%.4f, mean_spread=%.4f",
            elapsed, n_steps, len(flash_crashes),
            prices[-1], np.mean(spreads) if spreads else 0.0,
        )

        return {
            "prices": prices,
            "spreads": spreads,
            "volumes": volumes,
            "flash_crashes": flash_crashes,
            "mm_active_pct": mm_active_pct,
            "book_imbalance": book_imbalance,
            "elapsed_seconds": elapsed,
            "final_price": prices[-1],
            "total_trades": len(self.lob.trades),
        }

    def detect_flash_crash(self, prices: List[float]) -> List[Dict[str, Any]]:
        """Detect flash crashes: sudden drop > 3 std followed by recovery.

        A flash crash is defined as:
          1. Price drops > 3x rolling std within a short window
          2. Price partially recovers within 2x the drop window

        Args:
            prices: Price series from simulation.

        Returns:
            List of flash crash dicts with start_idx, trough_idx, recovery_idx,
            drop_pct, recovery_pct.
        """
        if len(prices) < 50:
            return []

        arr = np.array(prices)
        crashes: List[Dict[str, Any]] = []
        window = 20

        for i in range(window, len(arr) - window):
            local_mean = arr[i - window:i].mean()
            local_std = arr[i - window:i].std()
            if local_std < 1e-8:
                continue

            # Check for sudden drop
            drop = (arr[i] - local_mean) / local_mean
            z_drop = (arr[i] - local_mean) / local_std

            if z_drop < -3.0 and drop < -0.02:
                # Look for recovery within 2x window
                recovery_window = min(window * 2, len(arr) - i)
                future = arr[i:i + recovery_window]
                if len(future) < 2:
                    continue

                trough_offset = int(np.argmin(future))
                trough_price = future[trough_offset]
                post_trough = future[trough_offset:]
                if len(post_trough) < 2:
                    continue

                peak_after = float(np.max(post_trough))
                drop_pct = (trough_price - local_mean) / local_mean
                recovery_pct = (peak_after - trough_price) / abs(trough_price) if trough_price != 0 else 0

                # Flash crash requires partial recovery (> 50% of drop)
                if recovery_pct > abs(drop_pct) * 0.5:
                    crashes.append({
                        "start_idx": i,
                        "trough_idx": i + trough_offset,
                        "drop_pct": float(drop_pct),
                        "recovery_pct": float(recovery_pct),
                        "z_score": float(z_drop),
                        "pre_crash_mean": float(local_mean),
                        "trough_price": float(trough_price),
                    })
                    log.debug(
                        "Flash crash at step %d: drop=%.2f%% z=%.1f recovery=%.2f%%",
                        i, drop_pct * 100, z_drop, recovery_pct * 100,
                    )

        return crashes

    def _clean_stale_orders(self, current_step: int, max_age: int = 100) -> None:
        """Remove orders older than max_age steps from the LOB."""
        for book in (self.lob.bids, self.lob.asks):
            book[:] = [o for o in book if current_step - o.timestamp <= max_age]

    def save_results(
        self,
        results: Dict[str, Any],
        filepath: Optional[str] = None,
    ) -> None:
        """Persist simulation results to JSON."""
        filepath = filepath or os.path.join(DATA_DIR, "simulation_results.json")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        # Convert numpy types
        serialisable = {}
        for k, v in results.items():
            if isinstance(v, list) and v and isinstance(v[0], (np.floating, np.integer)):
                serialisable[k] = [float(x) for x in v]
            else:
                serialisable[k] = v

        try:
            with open(filepath, "w") as f:
                json.dump(serialisable, f, indent=2, default=str)
            log.info("ABM results saved to %s", filepath)
        except OSError as e:
            log.error("Failed to save ABM results: %s", e)
