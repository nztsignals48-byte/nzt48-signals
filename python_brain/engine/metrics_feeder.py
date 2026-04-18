"""metrics_feeder — bridges NATS state to the shared Prometheus REGISTRY.

Subscribes:
    portfolio.equity  -> v5_equity_gbp, v5_hwm_gbp, v5_drawdown_pct,
                         v5_realised_pnl_gbp, v5_trades_win, v5_trades_loss
    portfolio.fill    -> v5_fills_total, v5_fills_by_strategy{strategy=...}
    news.raw          -> v5_news_raw_total
    news.alpha        -> v5_news_alpha_total
    orders.filled     -> v5_fills_by_ticker{ticker=...}

Runs its own HTTP exporter on port 9103 so Prometheus can scrape directly
(the live-runner's port 9101 is for engine metrics).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python_brain.core import metrics_http
from python_brain.core.metrics import REGISTRY

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

PORT = int(os.environ.get("AEGIS_V5_METRICS_PORT", "9103"))


def _set_counter(reg, name: str, total: float) -> None:
    m = reg._metrics.get(name)
    if m is None:
        reg.counter(name, f"{name} (metrics_feeder)")
        m = reg._metrics.get(name)
    with reg._lock:
        m.values[()] = float(total)


async def main() -> None:
    import nats  # type: ignore
    metrics_http.start(port=PORT)
    log.info("metrics feeder exporter on :%d/metrics", PORT)

    nc = await nats.connect("nats://127.0.0.1:4222", name="aegis-v5-metrics-feeder")
    log.info("metrics feeder connected to NATS")

    # REGISTRY.set silently drops undeclared names. Register v5_* gauges first.
    for gauge_name, help_txt in [
        ("v5_equity_gbp", "Paper-account equity in GBP (IBKR netLiq × 0.79)"),
        ("v5_hwm_gbp", "Equity high-water mark GBP"),
        ("v5_drawdown_pct", "Current drawdown from HWM percent"),
        ("v5_realised_pnl_gbp", "Realised P&L today in GBP"),
        ("v5_trades_win", "Winning trades today"),
        ("v5_trades_loss", "Losing trades today"),
        ("v5_llm_cost_usd", "Anthropic LLM spend today USD"),
        ("v5_unrealised_pnl_usd", "Unrealised P&L across open positions USD"),
        ("v5_market_value_usd", "Market value of open positions USD"),
        ("v5_position_unrealised_usd", "Per-ticker unrealised P&L"),
        ("v5_position_qty", "Per-ticker open quantity"),
        ("v5_fills_by_strategy", "Order submits by strategy"),
        ("v5_fills_by_ticker", "Fills by ticker"),
    ]:
        REGISTRY.gauge(gauge_name, help_txt)
    for counter_name, help_txt in [
        ("v5_fills_total", "Portfolio fills received"),
        ("v5_news_raw_total", "Raw news events received"),
        ("v5_news_alpha_total", "LLM-scored news events"),
    ]:
        REGISTRY.counter(counter_name, help_txt)

    START_EQUITY_GBP = 100_000.0
    REGISTRY.set("v5_equity_gbp", START_EQUITY_GBP)
    REGISTRY.set("v5_hwm_gbp", START_EQUITY_GBP)
    REGISTRY.set("v5_drawdown_pct", 0.0)
    REGISTRY.set("v5_realised_pnl_gbp", 0.0)
    REGISTRY.set("v5_trades_win", 0.0)
    REGISTRY.set("v5_trades_loss", 0.0)
    REGISTRY.set("v5_llm_cost_usd", 0.0)
    REGISTRY.set("v5_unrealised_pnl_usd", 0.0)

    fills_total = 0
    news_raw_total = 0
    news_alpha_total = 0
    fills_by_strategy: dict = defaultdict(int)
    fills_by_ticker: dict = defaultdict(int)

    async def on_equity(msg):
        try:
            e = json.loads(msg.data)
        except Exception:
            return
        REGISTRY.set("v5_equity_gbp", float(e.get("equity_gbp", 0)))
        REGISTRY.set("v5_hwm_gbp", float(e.get("hwm_gbp", 0)))
        REGISTRY.set("v5_drawdown_pct", float(e.get("drawdown_pct", 0)))
        REGISTRY.set("v5_realised_pnl_gbp", float(e.get("realised_pnl_gbp", 0)))
        REGISTRY.set("v5_trades_win", float(e.get("trades_win", 0)))
        REGISTRY.set("v5_trades_loss", float(e.get("trades_loss", 0)))

    async def on_fill(msg):
        nonlocal fills_total
        try:
            f = json.loads(msg.data)
        except Exception:
            return
        fills_total += 1
        _set_counter(REGISTRY, "v5_fills_total", fills_total)
        t = f.get("ticker")
        if t:
            fills_by_ticker[t] += 1
            REGISTRY.set("v5_fills_by_ticker", float(fills_by_ticker[t]),
                         labels=[("ticker", t)])

    async def on_order_submit(msg):
        # Enrich fills_by_strategy by watching orders.submit.
        try:
            o = json.loads(msg.data)
        except Exception:
            return
        strat = o.get("strategy") or "unknown"
        fills_by_strategy[strat] += 1
        REGISTRY.set("v5_fills_by_strategy", float(fills_by_strategy[strat]),
                     labels=[("strategy", strat)])

    async def on_news_raw(msg):
        nonlocal news_raw_total
        news_raw_total += 1
        _set_counter(REGISTRY, "v5_news_raw_total", news_raw_total)

    async def on_news_alpha(msg):
        nonlocal news_alpha_total
        news_alpha_total += 1
        _set_counter(REGISTRY, "v5_news_alpha_total", news_alpha_total)
        # Cost from llm_cost_today.json
        try:
            cost_path = Path("/Users/rr/aegis-v5/data/llm_cost_today.json")
            if cost_path.exists():
                d = json.loads(cost_path.read_text())
                REGISTRY.set("v5_llm_cost_usd", float(d.get("usd", 0)))
        except Exception:
            pass

    await nc.subscribe("portfolio.equity", cb=on_equity)
    await nc.subscribe("portfolio.fill", cb=on_fill)
    await nc.subscribe("orders.submit", cb=on_order_submit)
    await nc.subscribe("news.raw", cb=on_news_raw)
    await nc.subscribe("news.alpha", cb=on_news_alpha)

    # --- IBKR heartbeat: pull real broker equity + unrealised P&L every 10s,
    # publish to both Prometheus gauges AND NATS account.* subjects so the
    # Data Flow dashboard sees live account data. This piggy-backs on the
    # already-working client_id=118 connection rather than fighting the
    # Gateway apiStart wedge with a new client.
    async def ibkr_heartbeat():
        try:
            from ib_insync import IB  # type: ignore
        except ImportError:
            log.warning("ib_insync not importable; heartbeat disabled")
            return
        ib = IB()
        try:
            await ib.connectAsync("127.0.0.1", 4002, clientId=118, readonly=True)
            log.info("IBKR heartbeat connected (client_id=118)")
        except Exception as e:
            log.warning("IBKR heartbeat connect failed: %s; gauges will stay seed", e)
            return

        ibkr_account = os.environ.get("IBKR_ACCOUNT", "DUM983136")

        while True:
            try:
                port_items = ib.portfolio()
                total_unreal = sum((p.unrealizedPNL or 0) for p in port_items)
                total_real = sum((p.realizedPNL or 0) for p in port_items)
                total_mv = sum((p.marketValue or 0) for p in port_items)
                # Account values
                acct = ib.accountValues()
                tags = {}
                for v in acct:
                    if v.currency in ("USD", "BASE", ""):
                        tags[v.tag] = v.value
                net_liq_usd = 0.0
                try:
                    net_liq_usd = float(tags.get("NetLiquidation", "0") or 0)
                except Exception:
                    pass
                # Convert to GBP (rough)
                usd_to_gbp = 0.79
                equity_gbp = net_liq_usd * usd_to_gbp if net_liq_usd > 0 else START_EQUITY_GBP
                REGISTRY.set("v5_equity_gbp", equity_gbp)
                REGISTRY.set("v5_unrealised_pnl_usd", total_unreal)
                REGISTRY.set("v5_market_value_usd", total_mv)
                REGISTRY.set("v5_realised_pnl_gbp",
                             (total_real * usd_to_gbp) if total_real else 0.0)
                # Per-ticker unrealised
                for p in port_items:
                    REGISTRY.set("v5_position_unrealised_usd",
                                 float(p.unrealizedPNL or 0),
                                 labels=[("ticker", p.contract.symbol)])
                    REGISTRY.set("v5_position_qty", float(p.position),
                                 labels=[("ticker", p.contract.symbol)])

                # account_streamer (client_id=139) is the authoritative publisher
                # of account.* NATS subjects. We only write to Prometheus gauges
                # here; removed duplicate publishes to avoid double-counting on
                # the Data Flow dashboard.
            except Exception as e:
                log.warning("heartbeat iter failed: %s", e)
            await asyncio.sleep(10)

    asyncio.create_task(ibkr_heartbeat())

    log.info("metrics feeder listening on all portfolio/news/fill subjects")
    while True:
        await asyncio.sleep(60)
        log.info("state: fills=%d news_raw=%d news_alpha=%d strategies=%d tickers=%d",
                 fills_total, news_raw_total, news_alpha_total,
                 len(fills_by_strategy), len(fills_by_ticker))


if __name__ == "__main__":
    asyncio.run(main())
