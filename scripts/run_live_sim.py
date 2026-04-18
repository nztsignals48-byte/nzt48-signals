"""Run the V5 engine in simulation mode with a live Prometheus exporter.

Starts the metrics HTTP server on :9100, then runs engine sessions in a loop,
publishing summary state as gauges after every batch so Grafana shows movement.

This script does not modify the engine; it only observes the returned summary
and writes to the shared REGISTRY.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

# Ensure repo root on sys.path.
ROOT = Path(__file__).resolve().parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python_brain.core import metrics_http
from python_brain.core.metrics import REGISTRY
from python_brain.engine.loop import Engine
from python_brain.engine.tick_feed import SimTickFeed

PORT = int(os.environ.get("AEGIS_V5_METRICS_PORT", "9100"))


async def run_forever() -> None:
    metrics_http.start(port=PORT)
    print(f"[live-sim] Prometheus exporter listening on :{PORT}/metrics")
    start_ts = time.time()

    # Cumulative counters across sessions (mirrored into REGISTRY).
    ticks_total = 0
    signals_total = 0
    opened_total = 0
    closed_total = 0
    pnl_total = 0.0
    per_strategy_trades: dict[str, int] = {}
    per_strategy_pnl: dict[str, float] = {}

    session = 0
    while True:
        session += 1
        eng = Engine()
        feed = SimTickFeed(steps=400)
        summary = await eng.run(feed)

        ticks_total += summary.ticks
        signals_total += summary.signals_generated
        opened_total += summary.positions_opened
        closed_total += summary.positions_closed
        pnl_total += summary.pnl_gbp

        for strat, n in summary.per_strategy_trades.items():
            per_strategy_trades[strat] = per_strategy_trades.get(strat, 0) + n

        # Publish counters (absolute values via set -- REGISTRY stores by key).
        REGISTRY.values_set_raw = None  # marker for humans reading code
        _set_counter(REGISTRY, "ticks_received_total", ticks_total)
        _set_counter(REGISTRY, "signals_generated_total", signals_total)
        _set_counter(REGISTRY, "fills_total", opened_total)
        _set_counter(REGISTRY, "closes_total", closed_total)
        _set_counter(REGISTRY, "positions_opened_total", opened_total)
        _set_counter(REGISTRY, "positions_closed_total", closed_total)
        _set_counter(REGISTRY, "wal_events_total", opened_total + closed_total)

        REGISTRY.set("positions_open", float(opened_total - closed_total))
        REGISTRY.set("equity_total_gbp", 10_000.0 + pnl_total)
        REGISTRY.set("equity_hwm_gbp", 10_000.0 + max(pnl_total, 0.0))
        REGISTRY.set("realised_pnl_gbp", pnl_total)
        REGISTRY.set("unrealised_pnl_gbp", 0.0)
        REGISTRY.set("win_rate", 0.5)  # placeholder until close-by-close tally added
        REGISTRY.set("engine_uptime_seconds", time.time() - start_ts)
        REGISTRY.set("ibkr_session_up", 1.0)

        for strat, n in per_strategy_trades.items():
            REGISTRY.set("strategy_trade_count", float(n), labels=[("strategy", strat)])

        print(
            f"[live-sim] session={session} ticks={ticks_total} signals={signals_total} "
            f"opened={opened_total} closed={closed_total} pnl=£{pnl_total:.2f}"
        )
        # Short idle so Prometheus can scrape between sessions.
        await asyncio.sleep(3)


def _set_counter(reg, name: str, total: float) -> None:
    """Force a counter to an absolute total (bypasses inc semantics)."""
    m = reg._metrics.get(name)
    if m is None:
        # dynamically register if missing
        reg.counter(name, f"{name} (live-sim auto-registered)")
        m = reg._metrics.get(name)
    with reg._lock:
        m.values[()] = float(total)


if __name__ == "__main__":
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        print("\n[live-sim] shutting down.")
