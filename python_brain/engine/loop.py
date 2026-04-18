"""Engine hot loop. Ties everything together.

Per tick:
  1. bar_builder.on_tick
  2. indicators.update
  3. exit_engine checks for every open position (invariant: every tick)
  4. strategies evaluate -> StrategyView
  5. conviction_engine.rank_signals (LLM delta if available)
  6. risk_arbiter on each top-N ranked signal -> final_confidence
  7. portfolio_constructor budgets top-N
  8. open position (sim fill) + write SignalReceived WAL + publish signals.*

Per position close:
  - write TradeClosed WAL + publish fills.closed
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from python_brain.conviction_engine import ConvictionEngine, StrategyView
from python_brain.core.ab_harness import AgentABHarness
from python_brain.core.cost_governor import CostGovernor
from python_brain.core.data_health import DataHealthMonitor
from python_brain.core.nats_client import NatsClient
from python_brain.core.preference_logger import PreferenceLogger
from python_brain.engine.bar_builder import BarBuilder
from python_brain.engine.exit_engine import evaluate as exit_evaluate
from python_brain.engine.indicators import IndicatorStore
from python_brain.engine.portfolio_state import PortfolioState, Position
from python_brain.engine.quant_core import QuantCore
from python_brain.engine.risk_arbiter import RiskArbiter
from python_brain.engine.tick_feed import SimTickFeed, Tick
from python_brain.engine.wal import WAL
from python_brain.portfolio_constructor import PortfolioConstructor
from python_brain.strategies.base import StrategyContext, StrategyView as BaseView


def _git_short_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(Path(__file__).resolve().parent.parent.parent), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip() or "no-git"
    except Exception:
        return "no-git"


STRATEGY_CLASSES = [
    ("python_brain.strategies.sentiment",          "SentimentLongShort"),
    ("python_brain.strategies.filing_change",      "FilingChangeDetect"),
    ("python_brain.strategies.index_recon",        "IndexRecon"),
    ("python_brain.strategies.earnings_pattern",   "EarningsPattern"),
    ("python_brain.strategies.overnight_return",   "OvernightReturn"),
    ("python_brain.strategies.ibs_mean_reversion", "IbsMeanReversion"),
    # price-breakout strategy (no intel dependency — runs on any ticker)
    ("python_brain.strategies.momentum_burst",     "MomentumBurst"),
]


@dataclass
class EngineRunSummary:
    ticks: int
    signals_generated: int
    signals_ranked: int
    positions_opened: int
    positions_closed: int
    pnl_gbp: float
    starved_strategies: List[str]
    per_strategy_trades: Dict[str, int] = field(default_factory=dict)


class Engine:
    def __init__(self, confidence_floor: float = 0.55, kelly_fraction: float = 0.15) -> None:
        self.bus = NatsClient.from_env()
        self.bars = BarBuilder()
        self.ind = IndicatorStore()
        self.quant = QuantCore()
        self.portfolio = PortfolioState()
        self.risk = RiskArbiter(confidence_floor=confidence_floor)
        self.conviction = ConvictionEngine(max_per_batch=5, min_composite_score=0.1)
        self.constructor = PortfolioConstructor(max_concurrent=6, min_position_gbp=2000.0)
        self.wal = WAL()
        self.prefs = PreferenceLogger()
        self.cost = CostGovernor.from_defaults()
        self.strategies = self._load_strategies()
        self.strategy_version = _git_short_sha()
        self.kelly_fraction = kelly_fraction
        self.intel = self._load_intel()

    def _load_strategies(self):
        instances = []
        for mod_name, cls_name in STRATEGY_CLASSES:
            mod = importlib.import_module(mod_name)
            instances.append(getattr(mod, cls_name)())
        return instances

    def _load_intel(self) -> Dict[str, dict]:
        intel_dir = Path(os.environ.get("AEGIS_V5_DATA", "/Users/rr/aegis-v5/data")) / "intel"
        out: Dict[str, dict] = {}
        if not intel_dir.exists():
            return out
        for p in intel_dir.glob("*.json"):
            try:
                out[p.name] = json.loads(p.read_text())
            except Exception:
                out[p.name] = {}
        return out

    async def run(self, feed: Optional[SimTickFeed] = None) -> EngineRunSummary:
        await self.bus.connect()
        feed = feed or SimTickFeed()
        session_close_ts_ns = 1_700_000_000_000_000_000 + 499 * 250_000_000
        summary = EngineRunSummary(ticks=0, signals_generated=0, signals_ranked=0,
                                    positions_opened=0, positions_closed=0, pnl_gbp=0.0,
                                    starved_strategies=DataHealthMonitor().starved_strategies())

        tick_buffer: Dict[str, Tick] = {}
        for tick in feed:
            summary.ticks += 1
            tick_buffer[tick.ticker] = tick
            self.bars.on_tick(tick)
            bars_1m = self.bars.recent("1m", tick.ticker, n=200)
            ind = self.ind.update(tick.ticker, bars_1m, tick.timestamp_ns, session_close_ts_ns)
            q = self.quant.on_tick(tick.ticker, tick.last, tick.timestamp_ns)

            # 3. Exits (every tick, every position for this ticker)
            for pos in [p for p in self.portfolio.positions if p.ticker == tick.ticker]:
                decision = exit_evaluate(self._exit_method_for(pos.strategy), pos, tick.last, tick.timestamp_ns, ind.atr, session_close_ts_ns)
                if decision.flatten:
                    await self._close_position(pos, tick, decision.reason, summary)

            # 4. Strategies (every 20 ticks to avoid spam in sim).
            if summary.ticks % 20 != 0:
                continue
            ctx = StrategyContext(
                ticker=tick.ticker,
                timestamp_ns=tick.timestamp_ns,
                bars={"1m": [b.__dict__ for b in bars_1m]},
                indicators={
                    "rsi": ind.rsi, "atr": ind.atr, "ibs": ind.ibs,
                    "momentum_5d": ind.momentum_5d, "close_proximity_min": ind.close_proximity_min,
                    "session_high": ind.session_high, "session_low": ind.session_low,
                    "vwap_distance_bps": ind.vwap_distance_bps, "ema_fast": ind.ema_fast, "ema_slow": ind.ema_slow,
                },
                quant={"garch_vol": q.garch_vol_annualized, "evt_cvar_95": q.evt_cvar_95,
                       "kalman_z": q.kalman_z, "hy_corr_spy": q.hy_correlation_to_spy},
                regime_probs=q.regime_probs,
                intel=self.intel,
                portfolio={"equity_gbp": self.portfolio.equity_gbp, "drawdown_pct": self.portfolio.drawdown_pct},
            )

            views: List[StrategyView] = []
            for s in self.strategies:
                if s.name in summary.starved_strategies:
                    continue
                bv: Optional[BaseView] = s.evaluate(ctx)
                if bv is None:
                    continue
                summary.signals_generated += 1
                views.append(StrategyView(
                    signal_id=str(uuid.uuid4()),
                    strategy=bv.strategy,
                    ticker=bv.ticker,
                    default_conviction=bv.default_conviction,
                    edge_estimate_bps=bv.edge_estimate_bps,
                    risk_bps=bv.risk_bps,
                    features=bv.features,
                ))

            if not views:
                continue

            # 5. Conviction ranking. No LLM deltas at this phase (Phase 6 wires them).
            ranked = self.conviction.rank_signals(views)
            summary.signals_ranked += len(ranked)

            # 6. Risk arbiter.
            approved = []
            for r in ranked:
                features = dict(r.features)
                features["ticker"] = r.ticker
                features["edge_bps"] = r.edge_estimate_bps
                features["est_cost_bps"] = 3.0
                features["spread_bps"] = max(1.0, (tick.ask - tick.bid) / tick.last * 1e4) if tick.last else 5.0
                features["avg_volume"] = tick.avg_volume
                features["shortable"] = tick.shortable
                features["halted"] = tick.halted
                features["rt_hist_vol"] = max(tick.rt_hist_vol, q.garch_vol_annualized)
                features["correlation_spy"] = q.hy_correlation_to_spy
                features["kalman_z"] = q.kalman_z
                features["regime_crisis"] = q.regime_probs[2]
                eval_r = self.risk.evaluate(r.final_conviction, features, self.portfolio)
                if eval_r.halt:
                    break
                if eval_r.final_confidence >= self.risk.confidence_floor:
                    approved.append((r, eval_r, features))

            # 7. Portfolio constructor.
            if approved:
                ranked_for_portfolio = [r for (r, _, _) in approved]
                allocs = self.constructor.allocate(ranked_for_portfolio, self.portfolio, kelly_fraction=self.kelly_fraction)
                for (r, eval_r, features), alloc in zip(approved, allocs):
                    await self._open_position(r, eval_r, features, alloc, tick, summary)

        return summary

    def _exit_method_for(self, strategy_name: str) -> str:
        for s in self.strategies:
            if s.name == strategy_name:
                return s.exit_method
        return "ChandelierStop"

    async def _open_position(self, ranked, eval_r, features, alloc, tick: Tick, summary: EngineRunSummary) -> None:
        size_shares = max(1, int(alloc.size_gbp / max(tick.last, 0.01)))
        pos = Position(
            signal_id=ranked.signal_id, ticker=ranked.ticker, strategy=ranked.strategy,
            account=alloc.account, entry_price=tick.ask, entry_ts_ns=tick.timestamp_ns,
            size_shares=size_shares, peak_price=tick.last,
        )
        self.portfolio.on_open(pos, alloc.size_gbp)
        summary.positions_opened += 1
        summary.per_strategy_trades[ranked.strategy] = summary.per_strategy_trades.get(ranked.strategy, 0) + 1

        signal_payload = {
            "schema_version": 1,
            "signal_id": ranked.signal_id,
            "strategy_name": ranked.strategy,
            "strategy_version": self.strategy_version,
            "ticker": ranked.ticker,
            "exchange": tick.exchange,
            "account": alloc.account,
            "timestamp_ns": tick.timestamp_ns,
            "feature_vector": features,
            "conviction_score": ranked.final_conviction,
            "portfolio_rank": ranked.rank,
            "account_route_chosen": alloc.account,
            "expected_fill_price": tick.ask,
            "risk_deltas": eval_r.deltas,
            "risk_final_confidence": eval_r.final_confidence,
        }
        self.wal.append("SignalReceived", signal_payload)
        await self.bus.publish("signals.core", signal_payload)
        self.prefs.log_signal(ranked.signal_id, ranked.strategy, ranked.default_conviction, ranked.final_conviction, features)

    async def _close_position(self, pos: Position, tick: Tick, reason: str, summary: EngineRunSummary) -> None:
        exit_price = tick.bid
        pnl_gbp = (exit_price - pos.entry_price) * pos.size_shares
        size_gbp = pos.entry_price * pos.size_shares
        realized_bps = (exit_price - pos.entry_price) / pos.entry_price * 1e4 if pos.entry_price else 0.0

        close_payload = {
            "schema_version": 1,
            "signal_id": pos.signal_id,
            "entry_timestamp_ns": pos.entry_ts_ns,
            "exit_timestamp_ns": tick.timestamp_ns,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "size_shares": pos.size_shares,
            "spread_cost_bps": (tick.ask - tick.bid) / max(tick.last, 0.01) * 1e4,
            "commission_abs": 0.0,           # ISA
            "stamp_duty_abs": 0.0,
            "financing_cost_abs": 0.0,
            "slippage_bps_vs_arrival": 0.0,
            "realized_pnl_abs": pnl_gbp,
            "realized_pnl_bps": realized_bps,
            "mae_bps": pos.mae_bps,
            "mfe_bps": pos.mfe_bps,
            "regime_at_entry": [1.0, 0.0, 0.0, 0.0],
            "regime_at_exit":  [1.0, 0.0, 0.0, 0.0],
            "exit_reason": reason,
        }
        self.wal.append("TradeClosed", close_payload)
        await self.bus.publish("fills.closed", close_payload)
        self.prefs.log_close(pos.signal_id, realized_bps, reason)

        self.portfolio.on_close(pos, pnl_gbp, size_gbp)
        summary.positions_closed += 1
        summary.pnl_gbp += pnl_gbp


async def run_one_session() -> EngineRunSummary:
    eng = Engine()
    return await eng.run()


if __name__ == "__main__":
    s = asyncio.run(run_one_session())
    print(s)
