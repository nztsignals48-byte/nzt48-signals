"""AEGIS V5 brain — orchestrator.

Hot path: Rust engine (ticks, bars, indicators, risk, exits, orders).
Warm path (this server): strategies -> conviction engine -> portfolio constructor
-> publish ranked signals to NATS `signals.*`.

Phase 0: scaffold. Phase 2A expects this server to connect to NATS, subscribe to
`ticks.*` and `bars.*`, and emit `signals.*`.
"""
from __future__ import annotations

import asyncio
import sys

from python_brain.core.nats_client import NatsClient
from python_brain.core.data_health import DataHealthMonitor
from python_brain.core.cost_governor import CostGovernor
from python_brain.core.preference_logger import PreferenceLogger


async def main() -> int:
    nats = NatsClient.from_env()
    await nats.connect()
    # Phase 1 gate: data-health must be green before we arm strategies.
    monitor = DataHealthMonitor()
    monitor.log_summary()
    if not monitor.is_startup_ok():
        print("data health not ok — blocking startup per Phase 1 gate", file=sys.stderr)
        # Phase 1: make this a hard exit. For Phase 0 scaffold, we only warn.
    _ = CostGovernor.from_defaults()
    _ = PreferenceLogger()
    print("aegis-v5 brain started (Phase 0 scaffold)")
    await asyncio.sleep(0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
