"""Publish `watchlist.current` to NATS. Engine subscribes and hot-swaps IBKR subscriptions."""
from __future__ import annotations

from typing import List

from python_brain.core.nats_client import NatsClient


async def publish(client: NatsClient, watchlist: List[str]) -> None:
    payload = ",".join(watchlist).encode()
    await client.publish("watchlist.current", payload)
