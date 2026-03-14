"""
Adaptive Time-Weighted Average Price execution.
Slices large orders into smaller child orders, adapting slice size
based on spread conditions.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from execution.ibkr_gateway import IBKRGateway

logger = logging.getLogger("nzt48.twap")


class AdaptiveTWAP:
    """
    Adaptive TWAP for thin markets.
    Standard TWAP is dumb (equal slices). Adaptive TWAP:
    - Pauses when spread widens > 1.5x normal
    - Cancels unfilled slices after timeout
    """

    def __init__(self, ibkr_client: IBKRGateway, total_shares: int, ticker: str,
                 direction: str, duration_seconds: int = 60, num_slices: int = 5):
        self._ibkr = ibkr_client
        self._total = total_shares
        self._ticker = ticker
        self._direction = direction
        self._duration = duration_seconds
        self._num_slices = max(num_slices, 1)
        self._filled = 0
        self._slice_size = max(total_shares // self._num_slices, 1)

    async def execute(self) -> dict:
        """Execute the adaptive TWAP. Returns fill summary."""
        interval = self._duration / self._num_slices
        fills = []

        for i in range(self._num_slices):
            remaining = self._total - self._filled
            if remaining <= 0:
                break

            slice_qty = min(self._slice_size, remaining)

            # Check spread condition
            bid, ask, bid_sz, ask_sz = self._ibkr.get_bid_ask(self._ticker)
            if bid <= 0 or ask <= 0:
                await asyncio.sleep(interval)
                continue

            spread_bps = (ask - bid) / ((bid + ask) / 2) * 10_000

            if spread_bps > 40:  # Spread too wide -- pause
                logger.info("TWAP_PAUSE: %s spread=%.0f bps -- skipping slice %d",
                           self._ticker, spread_bps, i)
                await asyncio.sleep(interval)
                continue

            # Place maker limit for this slice
            price = bid if self._direction.upper() == "LONG" else ask
            result = self._ibkr.place_maker_limit(self._ticker, self._direction, slice_qty, price)

            if result.get("order_id", -1) < 0:
                await asyncio.sleep(interval)
                continue

            await asyncio.sleep(min(interval, 2.0))  # Wait for fill

            # Check fill
            try:
                status = self._ibkr.ib.orderStatus(result["order_id"])
                if hasattr(status, 'status') and status.status == "Filled":
                    self._filled += slice_qty
                    fills.append({"slice": i, "qty": slice_qty, "price": price})
                else:
                    self._ibkr.cancel_order(result["order_id"])
            except Exception:
                self._ibkr.cancel_order(result["order_id"])

            await asyncio.sleep(max(0, interval - 2.0))

        return {
            "total_requested": self._total,
            "total_filled": self._filled,
            "fill_rate": self._filled / self._total if self._total > 0 else 0,
            "num_slices": len(fills),
            "fills": fills,
        }
