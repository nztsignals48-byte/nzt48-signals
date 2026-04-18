"""
SPDE Limit Order Book Simulator

Stochastic PDE-based model for simulating realistic limit order book dynamics.

Reference:
- Cont et al. (2010) "A Stochastic Model for Order Book Dynamics"
- Lehalle & Mounjid (2017) "Limit order strategic placement with adverse selection"
- Avellaneda & Stoikov (2008) "HF Market Making"

Used for:
- Backtesting execution strategies with realistic fill probabilities
- Queue position modeling (where am I in the queue? fill prob?)
- Adversarial testing of order placement decisions

Model:
- Limit orders arrive as Poisson processes at each price level
- Market orders arrive as Poisson with size distribution
- Cancellations decay linearly with distance from mid
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class LOBSimulatorConfig:
    tick_size: float = 0.01
    mid_start: float = 100.0
    book_depth_levels: int = 10

    # Arrival rates (Poisson lambda per second)
    limit_order_rate: float = 5.0         # new limit orders per second per side
    market_order_rate: float = 2.0        # market orders per second
    cancel_rate: float = 3.0               # cancellations per second per level

    # Size distributions
    limit_order_size_mean: float = 100
    market_order_size_mean: float = 200

    # Price dynamics
    drift_bps_per_s: float = 0.0           # long-term drift
    volatility_bps_per_sqrt_s: float = 2.0 # tick-level vol

    seed: int | None = None


@dataclass
class SimOrder:
    order_id: int
    price: float
    size: int
    side: str                              # "BUY" / "SELL"
    timestamp_s: float
    is_mine: bool = False                  # for tracking our orders
    queue_position: int = 0                # shares ahead of us at this price


@dataclass
class SimFillEvent:
    order_id: int
    fill_price: float
    fill_size: int
    timestamp_s: float
    is_mine: bool


class LOBSimulator:
    def __init__(self, config: LOBSimulatorConfig):
        self.cfg = config
        self.rng = np.random.default_rng(config.seed)
        self.mid = config.mid_start
        self.time_s = 0.0
        self.next_order_id = 1

        # Book state: price -> list of orders (FIFO queue)
        self.bids: dict[float, list[SimOrder]] = {}
        self.asks: dict[float, list[SimOrder]] = {}

        self.fills: list[SimFillEvent] = []
        self._initialize_book()

    def _initialize_book(self):
        """Seed the book with initial liquidity."""
        for i in range(1, self.cfg.book_depth_levels + 1):
            bid_price = round(self.mid - i * self.cfg.tick_size, 4)
            ask_price = round(self.mid + i * self.cfg.tick_size, 4)

            # Seed 3 orders per level
            for _ in range(3):
                bid_size = max(1, int(self.rng.exponential(self.cfg.limit_order_size_mean)))
                ask_size = max(1, int(self.rng.exponential(self.cfg.limit_order_size_mean)))

                self.bids.setdefault(bid_price, []).append(
                    SimOrder(self.next_order_id, bid_price, bid_size, "BUY", 0.0)
                )
                self.next_order_id += 1
                self.asks.setdefault(ask_price, []).append(
                    SimOrder(self.next_order_id, ask_price, ask_size, "SELL", 0.0)
                )
                self.next_order_id += 1

    @property
    def best_bid(self) -> float:
        return max(self.bids.keys()) if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return min(self.asks.keys()) if self.asks else 0.0

    def step(self, dt: float = 0.1) -> list[SimFillEvent]:
        """Advance simulation by dt seconds, return any fills."""
        new_fills = []
        self.time_s += dt

        # Price drift
        drift = self.cfg.drift_bps_per_s / 10000 * dt * self.mid
        vol_shock = self.rng.normal(0, self.cfg.volatility_bps_per_sqrt_s / 10000) * np.sqrt(dt) * self.mid
        self.mid += drift + vol_shock

        # Limit order arrivals
        n_limit_buys = self.rng.poisson(self.cfg.limit_order_rate * dt)
        n_limit_sells = self.rng.poisson(self.cfg.limit_order_rate * dt)

        for _ in range(n_limit_buys):
            price = self._sample_limit_price("BUY")
            size = max(1, int(self.rng.exponential(self.cfg.limit_order_size_mean)))
            order = SimOrder(self.next_order_id, price, size, "BUY", self.time_s)
            self.bids.setdefault(price, []).append(order)
            self.next_order_id += 1

        for _ in range(n_limit_sells):
            price = self._sample_limit_price("SELL")
            size = max(1, int(self.rng.exponential(self.cfg.limit_order_size_mean)))
            order = SimOrder(self.next_order_id, price, size, "SELL", self.time_s)
            self.asks.setdefault(price, []).append(order)
            self.next_order_id += 1

        # Market order arrivals
        n_market_buys = self.rng.poisson(self.cfg.market_order_rate * dt)
        n_market_sells = self.rng.poisson(self.cfg.market_order_rate * dt)

        for _ in range(n_market_buys):
            size = max(1, int(self.rng.exponential(self.cfg.market_order_size_mean)))
            fills = self._execute_market_order("BUY", size)
            new_fills.extend(fills)

        for _ in range(n_market_sells):
            size = max(1, int(self.rng.exponential(self.cfg.market_order_size_mean)))
            fills = self._execute_market_order("SELL", size)
            new_fills.extend(fills)

        # Cancellations (decay far-from-mid orders faster)
        self._process_cancellations(dt)

        self.fills.extend(new_fills)
        return new_fills

    def _sample_limit_price(self, side: str) -> float:
        """Sample a limit price (more likely closer to mid)."""
        # Geometric distribution for tick distance from mid
        tick_distance = max(1, self.rng.geometric(0.4))
        tick_distance = min(tick_distance, self.cfg.book_depth_levels)

        if side == "BUY":
            return round(self.mid - tick_distance * self.cfg.tick_size, 4)
        else:
            return round(self.mid + tick_distance * self.cfg.tick_size, 4)

    def _execute_market_order(self, side: str, size: int) -> list[SimFillEvent]:
        """Execute a market order, walk the book."""
        fills = []
        remaining = size

        if side == "BUY":
            # Walk asks from best upward
            ask_prices = sorted(self.asks.keys())
            for price in ask_prices:
                if remaining <= 0:
                    break
                queue = self.asks[price]
                while queue and remaining > 0:
                    order = queue[0]
                    fill_size = min(order.size, remaining)
                    fills.append(SimFillEvent(
                        order_id=order.order_id,
                        fill_price=price,
                        fill_size=fill_size,
                        timestamp_s=self.time_s,
                        is_mine=order.is_mine,
                    ))
                    order.size -= fill_size
                    remaining -= fill_size
                    if order.size == 0:
                        queue.pop(0)
                if not queue:
                    del self.asks[price]
        else:
            # Walk bids from best downward
            bid_prices = sorted(self.bids.keys(), reverse=True)
            for price in bid_prices:
                if remaining <= 0:
                    break
                queue = self.bids[price]
                while queue and remaining > 0:
                    order = queue[0]
                    fill_size = min(order.size, remaining)
                    fills.append(SimFillEvent(
                        order_id=order.order_id,
                        fill_price=price,
                        fill_size=fill_size,
                        timestamp_s=self.time_s,
                        is_mine=order.is_mine,
                    ))
                    order.size -= fill_size
                    remaining -= fill_size
                    if order.size == 0:
                        queue.pop(0)
                if not queue:
                    del self.bids[price]

        return fills

    def _process_cancellations(self, dt: float) -> None:
        """Remove orders stochastically, more likely far from mid."""
        for side_book in (self.bids, self.asks):
            for price in list(side_book.keys()):
                distance_ticks = abs(price - self.mid) / self.cfg.tick_size
                cancel_prob = self.cfg.cancel_rate * dt * (1 + distance_ticks * 0.2) / 100

                queue = side_book[price]
                survivors = [o for o in queue if self.rng.random() > cancel_prob]
                if not survivors:
                    del side_book[price]
                else:
                    side_book[price] = survivors

    def place_limit_order(self, price: float, size: int, side: str) -> SimOrder:
        """Place our own limit order, returning the SimOrder with queue position."""
        side_book = self.bids if side == "BUY" else self.asks
        queue = side_book.setdefault(price, [])
        queue_position = sum(o.size for o in queue)

        order = SimOrder(
            order_id=self.next_order_id,
            price=price,
            size=size,
            side=side,
            timestamp_s=self.time_s,
            is_mine=True,
            queue_position=queue_position,
        )
        queue.append(order)
        self.next_order_id += 1
        return order

    def get_queue_position(self, order_id: int) -> int:
        """How many shares are ahead of us at our price?"""
        for book in (self.bids, self.asks):
            for queue in book.values():
                for i, o in enumerate(queue):
                    if o.order_id == order_id:
                        return sum(oo.size for oo in queue[:i])
        return -1

    def estimate_fill_probability(self, price: float, size: int, side: str, horizon_s: float) -> float:
        """Estimate probability our order at (price, size) fills within horizon_s."""
        # Queue position
        side_book = self.bids if side == "BUY" else self.asks
        queue = side_book.get(price, [])
        queue_ahead = sum(o.size for o in queue)

        # Expected market order volume against us during horizon
        expected_volume = self.cfg.market_order_rate * self.cfg.market_order_size_mean * horizon_s

        # Distance adjustment: farther from mid = less likely to trade
        distance_ticks = abs(price - self.mid) / self.cfg.tick_size
        distance_penalty = max(0, 1 - 0.1 * distance_ticks)

        effective_volume = expected_volume * distance_penalty
        if effective_volume == 0:
            return 0.0

        # Simple probability: if expected volume > queue ahead + us
        fill_prob = min(1.0, effective_volume / max(1, queue_ahead + size))
        return fill_prob


def run_simulation(
    duration_s: float = 60,
    step_s: float = 0.1,
    config: LOBSimulatorConfig | None = None,
) -> tuple[LOBSimulator, dict]:
    """Run sim for duration_s, return final sim + stats."""
    cfg = config or LOBSimulatorConfig()
    sim = LOBSimulator(cfg)

    steps = int(duration_s / step_s)
    mid_history = []
    for _ in range(steps):
        sim.step(step_s)
        mid_history.append(sim.mid)

    stats = {
        "total_fills": len(sim.fills),
        "mid_start": cfg.mid_start,
        "mid_end": sim.mid,
        "mid_min": min(mid_history),
        "mid_max": max(mid_history),
        "mid_vol_bps": float(np.std(np.diff(mid_history)) / cfg.mid_start * 10000) if len(mid_history) > 1 else 0,
        "final_spread_bps": (sim.best_ask - sim.best_bid) / sim.mid * 10000 if sim.mid > 0 else 0,
    }
    return sim, stats


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        cfg = LOBSimulatorConfig(seed=42)
        sim, stats = run_simulation(duration_s=30, config=cfg)

        print(f"Duration: 30s, steps: {30/0.1:.0f}")
        print(f"Total fills: {stats['total_fills']}")
        print(f"Mid: {stats['mid_start']:.4f} -> {stats['mid_end']:.4f}")
        print(f"Mid range: [{stats['mid_min']:.4f}, {stats['mid_max']:.4f}]")
        print(f"Mid vol: {stats['mid_vol_bps']:.2f} bps/step")
        print(f"Final spread: {stats['final_spread_bps']:.2f} bps")
        print(f"Book: bid={sim.best_bid:.4f}, ask={sim.best_ask:.4f}")

        # Place a test order
        order = sim.place_limit_order(sim.best_bid, 100, "BUY")
        print(f"Our order: {order.order_id} @ {order.price:.4f}, queue pos: {order.queue_position}")
        fill_prob = sim.estimate_fill_probability(order.price, order.size, order.side, 10)
        print(f"10s fill probability: {fill_prob:.2%}")
        print("OK")
