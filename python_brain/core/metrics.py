"""Prometheus metrics registry (stdlib-only, no external deps).

Every counter/gauge exposed here must have a producer in the hot or warm path.
Phase 11 dead-code sweep enforces that.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, Tuple


@dataclass
class Metric:
    name: str
    kind: str   # counter | gauge
    help: str
    values: Dict[Tuple[Tuple[str, str], ...], float] = field(default_factory=dict)


class Registry:
    def __init__(self) -> None:
        self._metrics: Dict[str, Metric] = {}
        self._lock = threading.Lock()
        self._declare()

    def _declare(self) -> None:
        # Engine
        self.counter("ticks_received_total", "Ticks received from the broker")
        self.counter("bars_completed_total", "Completed bars (any TF)")
        self.counter("wal_events_total", "Events written to WAL")
        self.counter("risk_deltas_total", "Risk-check delta emissions")
        self.counter("exit_triggered_total", "Exit-method triggers")
        self.counter("fills_total", "Position opens")
        self.counter("closes_total", "Position closes")
        self.counter("kill_triggered_total", "Kill events (any authority)")
        self.gauge("ibkr_session_up", "1 if IBKR session connected")
        self.gauge("equity_total_gbp", "Engine equity in GBP")
        self.gauge("equity_hwm_gbp", "Engine HWM in GBP")
        self.gauge("drawdown_pct", "Drawdown from HWM")
        self.gauge("positions_open", "Number of open positions")
        # Brain
        self.counter("signals_generated_total", "Strategy signals generated")
        self.counter("signals_ranked_top_n", "Signals ranked in top-N")
        self.counter("signals_rejected_by_floor", "Signals below confidence floor")
        self.counter("preference_logger_calls_total", "Preference logger invocations")
        self.counter("llm_calls_total", "LLM calls by provider+agent")
        self.counter("llm_tokens_total", "LLM tokens billed")
        self.gauge("llm_cost_usd_total", "LLM spend today (USD)")
        self.gauge("conviction_delta_vs_default", "Mean LLM conviction delta vs strategy default")
        # Scanner
        self.gauge("scanner_universe_size", "Scanner universe size")
        self.gauge("watchlist_churn_rate", "Fraction of new tickers in last watchlist")
        self.gauge("held_position_preserved_total", "Held positions preserved across rotations")
        # Ouroboros
        self.gauge("kelly_fraction", "Current Kelly fraction")
        self.gauge("chandelier_atr_mult", "Current Chandelier ATR mult")
        self.gauge("confidence_floor", "Current confidence floor")
        # Data health
        self.gauge("intel_files_fed", "Intel files FED")
        self.gauge("intel_files_stale", "Intel files STALE")
        self.gauge("intel_files_missing", "Intel files MISSING")
        self.counter("zero_trade_day_total", "Zero-trade-day incidents")

    def counter(self, name: str, help_: str) -> None:
        self._metrics[name] = Metric(name, "counter", help_)

    def gauge(self, name: str, help_: str) -> None:
        self._metrics[name] = Metric(name, "gauge", help_)

    def inc(self, name: str, by: float = 1.0, labels: Iterable[Tuple[str, str]] = ()) -> None:
        m = self._metrics.get(name)
        if not m:
            return
        k = tuple(sorted(labels))
        with self._lock:
            m.values[k] = m.values.get(k, 0.0) + by

    def set(self, name: str, v: float, labels: Iterable[Tuple[str, str]] = ()) -> None:
        m = self._metrics.get(name)
        if not m:
            return
        k = tuple(sorted(labels))
        with self._lock:
            m.values[k] = v

    def render_prometheus(self) -> str:
        out: list[str] = []
        for name, m in sorted(self._metrics.items()):
            out.append(f"# HELP {name} {m.help}")
            out.append(f"# TYPE {name} {m.kind}")
            if not m.values:
                out.append(f"{name} 0")
            else:
                for labels, v in m.values.items():
                    if labels:
                        lbl = ",".join(f'{k}="{v}"' for k, v in labels)
                        out.append(f"{name}{{{lbl}}} {v}")
                    else:
                        out.append(f"{name} {v}")
        return "\n".join(out) + "\n"


REGISTRY = Registry()
