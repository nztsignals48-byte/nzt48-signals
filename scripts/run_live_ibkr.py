"""Run the V5 engine against the **live** IBKR tick stream from NATS.

Architecture:
  IB Gateway :4002
     │
     ▼
  aegis-engine (Rust) ── publishes → NATS ticks.live.*
     │
     ▼
  this runner ── IbkrTickFeed ── feeds Engine hot loop
     │
     ▼
  WAL + NATS signals + Prometheus exporter

Requires the Rust bridge to be running concurrently (``cargo run`` or
``target/debug/aegis-engine``).
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python_brain.core import metrics_http
from python_brain.core.metrics import REGISTRY
from python_brain.core.nats_client_live import LiveNatsClient as _Live

# Patch on both the module AND loop's already-bound name once it imports.
import python_brain.core.nats_client as _nc_module
_nc_module.NatsClient = _Live

from python_brain.engine.ibkr_tick_feed import IbkrTickFeed
from python_brain.engine import loop as _loop_mod
# loop.py did `from ... import NatsClient` earlier; rebind in its namespace.
_loop_mod.NatsClient = _Live
Engine = _loop_mod.Engine
print(f"[live-ibkr] bus class patched to {_loop_mod.NatsClient.__name__}")

PORT = int(os.environ.get("AEGIS_V5_METRICS_PORT", "9101"))


async def run() -> None:
    metrics_http.start(port=PORT)
    print(f"[live-ibkr] Prometheus exporter :{PORT}/metrics")

    start_ts = time.time()
    feed = IbkrTickFeed()
    await _drive_engine(feed, start_ts)


async def _drive_engine(feed: IbkrTickFeed, start_ts: float):
    """Drive Engine with live IBKR ticks. One Engine session per 400 ticks
    so the summary counters and dashboards refresh continuously.
    """
    eng = Engine()
    # Override hardcoded 6 — raise to 50 so engine keeps publishing signals.
    from python_brain.portfolio_constructor import PortfolioConstructor
    eng.constructor = PortfolioConstructor(max_concurrent=50, min_position_gbp=2000.0)
    # Higher Kelly so conviction actually sizes meaningfully on paper.
    eng.kelly_fraction = 0.10

    # Add MomentumBurst as a 7th strategy (no-intel, for rotator discoveries).
    try:
        from python_brain.strategies.momentum_burst import MomentumBurst
        if not any(s.name == "momentum_burst" for s in eng.strategies):
            eng.strategies.append(MomentumBurst())
        print(f"[live-ibkr] strategies loaded: {[s.name for s in eng.strategies]}")
    except Exception as e:
        print(f"[live-ibkr] failed to load MomentumBurst: {e}")

    print(f"[live-ibkr] constructor max_concurrent=50, kelly=0.10")
    # Engine's hot loop uses `for tick in feed:`. We need a sync-iter shim
    # that pulls from the async feed.
    buffer: asyncio.Queue = asyncio.Queue(maxsize=2000)

    async def producer():
        async for tick in feed:
            await buffer.put(tick)

    prod_task = asyncio.create_task(producer())

    ticks_total = 0
    signals_total = 0
    opened_total = 0
    closed_total = 0
    pnl_total = 0.0
    last_report = time.time()

    # Instead of calling Engine.run() (which expects a SimTickFeed iterable),
    # we replicate its hot-loop against the live buffer.
    from python_brain.engine.loop import EngineRunSummary, STRATEGY_CLASSES  # noqa
    from python_brain.engine.bar_builder import BarBuilder
    from python_brain.engine.indicators import IndicatorStore
    from python_brain.engine.quant_core import QuantCore
    from python_brain.core.data_health import DataHealthMonitor
    from python_brain.engine.tick_feed import Tick

    await eng.bus.connect()
    session_close_ts_ns = 0  # disable EOD for continuous live run
    summary = EngineRunSummary(
        ticks=0, signals_generated=0, signals_ranked=0,
        positions_opened=0, positions_closed=0, pnl_gbp=0.0,
        starved_strategies=DataHealthMonitor().starved_strategies()
    )

    while True:
        tick: Tick = await buffer.get()
        summary.ticks += 1
        eng.bars.on_tick(tick)
        bars_1m = eng.bars.recent("1m", tick.ticker, n=200)
        ind = eng.ind.update(tick.ticker, bars_1m, tick.timestamp_ns, session_close_ts_ns)
        q = eng.quant.on_tick(tick.ticker, tick.last, tick.timestamp_ns)

        # Exits on every tick (V5 invariant) — Chandelier v4 (percentile-MFE + AVWAP).
        from python_brain.engine.chandelier_v2 import ChandelierV2State
        from python_brain.engine.chandelier_v4 import (
            evaluate_v4, V4InputFrame, V4Config,
        )
        if not hasattr(eng, "_chandelier_v2_states"):
            eng._chandelier_v2_states = {}
        if not hasattr(eng, "_v4_cfg"):
            eng._v4_cfg = V4Config()
        if not hasattr(eng, "_avwap_cache"):
            eng._avwap_cache = {}  # ticker -> (num, den) rolling since session start

        # Update rolling AVWAP (session-wide proxy since we don't have entry-anchored for every pos)
        num, den = eng._avwap_cache.get(tick.ticker, (0.0, 0.0))
        tick_vol = float(tick.volume or 0)
        num += tick.last * tick_vol
        den += tick_vol
        eng._avwap_cache[tick.ticker] = (num, den)
        avwap_now = (num / den) if den > 0 else 0.0

        # KER approximation from recent bars
        bars_list = bars_1m[-11:]
        ker10 = None
        if len(bars_list) >= 11:
            try:
                closes = [b.close for b in bars_list]
                num_k = abs(closes[-1] - closes[0])
                den_k = sum(abs(closes[i] - closes[i-1]) for i in range(1, len(closes)))
                ker10 = num_k / den_k if den_k > 0 else 0.0
            except Exception:
                ker10 = None

        for pos in [p for p in eng.portfolio.positions if p.ticker == tick.ticker]:
            st = eng._chandelier_v2_states.setdefault(
                pos.signal_id, ChandelierV2State(
                    entry_price=pos.entry_price, entry_ts_ns=pos.entry_ts_ns,
                    peak_price=pos.entry_price, trough_price=pos.entry_price,
                ),
            )
            is_lev = any(pos.ticker.startswith(p) for p in ("3L", "3S", "2L", "2S"))
            frame = V4InputFrame(
                entry_price=pos.entry_price,
                current_price=tick.last,
                current_ts_ns=tick.timestamp_ns,
                entry_ts_ns=pos.entry_ts_ns,
                atr=ind.atr,
                bars_since_entry=max(0, int((tick.timestamp_ns - pos.entry_ts_ns) / 60e9)),
                ker10=ker10,
                rsi=ind.rsi,
                avwap_entry=avwap_now,
                bar_volume=float(tick.volume or 0),
                avg_volume_20=float(tick.avg_volume or 0),
                bar_close_in_lower_third=(
                    (tick.high - tick.last) / (tick.high - tick.low) > 0.66
                    if (tick.high and tick.low and tick.high > tick.low)
                    else False
                ),
                bar_is_red=(tick.last < tick.open) if tick.open else False,
                rv_now=q.garch_vol_annualized,
                rv_20d_ema=0.20,  # TODO persist rolling EMA
                regime_probs=q.regime_probs,
                pctl80_giveback_pct=None,  # wired by ouroboros_v2 later
                is_leveraged_etp=is_lev,
                nights_held=0,
            )
            decision = evaluate_v4(st, frame, eng._v4_cfg)
            if decision.flatten:
                await eng._close_position(pos, tick, decision.reason, summary)
                eng._chandelier_v2_states.pop(pos.signal_id, None)

        # Strategy eval every 20 ticks.
        if summary.ticks % 20 == 0:
            from python_brain.strategies.base import StrategyContext, StrategyView as BaseView
            from python_brain.conviction_engine import StrategyView
            import uuid

            ctx = StrategyContext(
                ticker=tick.ticker, timestamp_ns=tick.timestamp_ns,
                bars={"1m": [b.__dict__ for b in bars_1m]},
                indicators={
                    "rsi": ind.rsi, "atr": ind.atr, "ibs": ind.ibs,
                    "momentum_5d": ind.momentum_5d, "close_proximity_min": ind.close_proximity_min,
                    "session_high": ind.session_high, "session_low": ind.session_low,
                    "vwap_distance_bps": ind.vwap_distance_bps,
                    "ema_fast": ind.ema_fast, "ema_slow": ind.ema_slow,
                },
                quant={"garch_vol": q.garch_vol_annualized, "evt_cvar_95": q.evt_cvar_95,
                       "kalman_z": q.kalman_z, "hy_corr_spy": q.hy_correlation_to_spy},
                regime_probs=q.regime_probs,
                intel=eng.intel,
                portfolio={"equity_gbp": eng.portfolio.equity_gbp,
                           "drawdown_pct": eng.portfolio.drawdown_pct},
            )
            views = []
            for s in eng.strategies:
                if s.name in summary.starved_strategies:
                    continue
                bv = s.evaluate(ctx)
                if bv is None:
                    continue
                summary.signals_generated += 1
                views.append(StrategyView(
                    signal_id=str(uuid.uuid4()),
                    strategy=bv.strategy, ticker=bv.ticker,
                    default_conviction=bv.default_conviction,
                    edge_estimate_bps=bv.edge_estimate_bps,
                    risk_bps=bv.risk_bps, features=bv.features,
                ))
            if views:
                ranked = eng.conviction.rank_signals(views)
                summary.signals_ranked += len(ranked)
                approved = []
                for r in ranked:
                    features = dict(r.features)
                    features["ticker"] = r.ticker
                    features["edge_bps"] = r.edge_estimate_bps
                    features["est_cost_bps"] = 3.0
                    features["spread_bps"] = max(
                        1.0, (tick.ask - tick.bid) / tick.last * 1e4
                    ) if tick.last else 5.0
                    features["avg_volume"] = tick.avg_volume
                    features["shortable"] = tick.shortable
                    features["halted"] = tick.halted
                    features["rt_hist_vol"] = max(tick.rt_hist_vol, q.garch_vol_annualized)
                    features["correlation_spy"] = q.hy_correlation_to_spy
                    features["kalman_z"] = q.kalman_z
                    features["regime_crisis"] = q.regime_probs[2]
                    eval_r = eng.risk.evaluate(r.final_conviction, features, eng.portfolio)
                    if eval_r.halt:
                        break
                    if eval_r.final_confidence >= eng.risk.confidence_floor:
                        approved.append((r, eval_r, features))
                        # Always-publish: bypass engine's max_concurrent by publishing
                        # here directly so sig2order has its own gate.
                        candidate = {
                            "schema_version": 1,
                            "signal_id": r.signal_id,
                            "strategy_name": r.strategy,
                            "strategy_version": eng.strategy_version,
                            "ticker": r.ticker,
                            "exchange": tick.exchange,
                            "account": "DUM983136",
                            "timestamp_ns": tick.timestamp_ns,
                            "feature_vector": features,
                            "conviction_score": eval_r.final_confidence,
                            "portfolio_rank": r.rank,
                            "account_route_chosen": "DUM983136",
                            "expected_fill_price": tick.ask,
                            "risk_deltas": eval_r.deltas,
                            "risk_final_confidence": eval_r.final_confidence,
                        }
                        try:
                            await eng.bus.publish("signals.core", candidate)
                        except Exception as _e:
                            pass
                if approved:
                    ranked_for_portfolio = [r for (r, _, _) in approved]
                    allocs = eng.constructor.allocate(
                        ranked_for_portfolio, eng.portfolio,
                        kelly_fraction=eng.kelly_fraction,
                    )
                    for (r, eval_r, features), alloc in zip(approved, allocs):
                        await eng._open_position(r, eval_r, features, alloc, tick, summary)

        # Metrics + periodic report.
        if time.time() - last_report > 5:
            ticks_total = summary.ticks
            signals_total = summary.signals_generated
            opened_total = summary.positions_opened
            closed_total = summary.positions_closed
            pnl_total = summary.pnl_gbp
            _push_metrics(ticks_total, signals_total, opened_total,
                          closed_total, pnl_total, start_ts, summary)
            print(
                f"[live-ibkr] ticks={ticks_total:,} "
                f"signals={signals_total} opened={opened_total} closed={closed_total} "
                f"pnl=£{pnl_total:.2f} open_positions={opened_total - closed_total}"
            )
            last_report = time.time()


def _push_metrics(ticks, signals, opened, closed, pnl, start_ts, summary):
    _set_counter(REGISTRY, "ticks_received_total", ticks)
    _set_counter(REGISTRY, "signals_generated_total", signals)
    _set_counter(REGISTRY, "positions_opened_total", opened)
    _set_counter(REGISTRY, "positions_closed_total", closed)
    _set_counter(REGISTRY, "wal_events_total", opened + closed)
    _set_counter(REGISTRY, "ibkr_live_ticks_total", ticks)
    REGISTRY.set("positions_open", float(opened - closed))
    REGISTRY.set("equity_total_gbp", 20_000.0 + pnl)
    REGISTRY.set("equity_hwm_gbp", 20_000.0 + max(pnl, 0.0))
    REGISTRY.set("realised_pnl_gbp", pnl)
    REGISTRY.set("unrealised_pnl_gbp", 0.0)
    REGISTRY.set("engine_uptime_seconds", time.time() - start_ts)
    REGISTRY.set("ibkr_session_up", 1.0)
    for strat, n in summary.per_strategy_trades.items():
        REGISTRY.set("strategy_trade_count", float(n), labels=[("strategy", strat)])


def _set_counter(reg, name: str, total: float) -> None:
    m = reg._metrics.get(name)
    if m is None:
        reg.counter(name, f"{name} (live-ibkr)")
        m = reg._metrics.get(name)
    with reg._lock:
        m.values[()] = float(total)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[live-ibkr] shutting down.")
