"""Book 8: Live Metrics Collector.

Tracks 10 critical live metrics for real-time observability:
  1. Signals per hour (by strategy)
  2. Fill rate (signals emitted / signals vetoed)
  3. Cost drag (total costs / gross P&L)
  4. Average holding period (bars)
  5. Regime time distribution (% in each regime)
  6. Win rate (rolling 50 trades)
  7. Sharpe (rolling 50 trades)
  8. Strategy distribution (signals by strategy)
  9. Veto reasons (aggregated)
  10. Time-to-fill (signal → fill latency)

Persists to /app/data/live_metrics.json on shutdown.
Wired into bridge.py signal emission and exit handlers.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

DATA_DIR = os.environ.get("AEGIS_DATA_DIR", "/app/data")
METRICS_FILE = os.path.join(DATA_DIR, "live_metrics.json")


@dataclass
class SignalEvent:
    strategy: str
    confidence: int
    kelly: float
    ticker: str
    timestamp: float


@dataclass
class ExitEvent:
    strategy: str
    pnl: float
    cost: float
    holding_bars: int
    timestamp: float


class LiveMetricsCollector:
    """Collects and aggregates live trading metrics."""

    def __init__(self, max_history: int = 500):
        self._max = max_history
        self._signals: deque = deque(maxlen=max_history)
        self._exits: deque = deque(maxlen=max_history)
        self._vetoes: deque = deque(maxlen=max_history)
        self._regime_ticks: Dict[str, int] = defaultdict(int)
        self._total_ticks: int = 0
        self._strategy_signals: Dict[str, int] = defaultdict(int)
        self._veto_reasons: Dict[str, int] = defaultdict(int)
        self._start_time: float = time.time()
        self._loaded = False

    def record_signal(self, strategy: str, confidence: int, kelly: float, ticker: str = ""):
        """Record a signal emission."""
        self._signals.append(SignalEvent(
            strategy=strategy, confidence=confidence, kelly=kelly,
            ticker=ticker, timestamp=time.time(),
        ))
        self._strategy_signals[strategy] += 1

    def record_exit(self, strategy: str, pnl: float, cost: float = 0.0, holding_bars: int = 0):
        """Record a trade exit."""
        self._exits.append(ExitEvent(
            strategy=strategy, pnl=pnl, cost=cost,
            holding_bars=holding_bars, timestamp=time.time(),
        ))

    def record_veto(self, reason: str):
        """Record a signal veto."""
        self._vetoes.append({"reason": reason, "timestamp": time.time()})
        self._veto_reasons[reason] += 1

    def record_regime_tick(self, regime: str):
        """Record a tick in the given regime."""
        self._regime_ticks[regime] += 1
        self._total_ticks += 1

    def summary(self) -> Dict:
        """Compute summary metrics."""
        now = time.time()
        uptime_hours = max((now - self._start_time) / 3600, 0.001)

        # 1. Signals per hour
        recent_signals = [s for s in self._signals if now - s.timestamp < 3600]
        signals_per_hour = len(recent_signals)

        # 2. Fill rate
        total_signals = len(self._signals)
        total_vetoes = len(self._vetoes)
        fill_rate = total_signals / max(total_signals + total_vetoes, 1)

        # 3. Cost drag
        gross_pnl = sum(abs(e.pnl) for e in self._exits)
        total_cost = sum(e.cost for e in self._exits)
        cost_drag_pct = (total_cost / gross_pnl * 100) if gross_pnl > 0 else 0.0

        # 4. Average holding period
        holding_bars = [e.holding_bars for e in self._exits if e.holding_bars > 0]
        avg_holding = sum(holding_bars) / len(holding_bars) if holding_bars else 0.0

        # 5. Regime distribution
        regime_dist = {}
        if self._total_ticks > 0:
            for regime, count in self._regime_ticks.items():
                regime_dist[regime] = round(count / self._total_ticks * 100, 1)

        # 6. Win rate (rolling 50)
        recent_exits = list(self._exits)[-50:]
        wins = sum(1 for e in recent_exits if e.pnl > 0)
        win_rate = wins / len(recent_exits) if recent_exits else 0.0

        # 7. Sharpe (rolling 50)
        if len(recent_exits) >= 5:
            returns = [e.pnl for e in recent_exits]
            mean_r = sum(returns) / len(returns)
            var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
            std_r = var_r ** 0.5 if var_r > 0 else 1e-10
            sharpe = (mean_r / std_r) * (252 ** 0.5) if std_r > 0 else 0.0
        else:
            sharpe = 0.0

        # 8. Strategy distribution
        strat_dist = dict(self._strategy_signals)

        # 9. Top veto reasons
        top_vetoes = sorted(self._veto_reasons.items(), key=lambda x: -x[1])[:10]

        # 10. Average confidence of emitted signals
        avg_confidence = 0.0
        if self._signals:
            avg_confidence = sum(s.confidence for s in self._signals) / len(self._signals)

        return {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "uptime_hours": round(uptime_hours, 2),
            "signals_per_hour": signals_per_hour,
            "signals_total": total_signals,
            "fill_rate_pct": round(fill_rate * 100, 1),
            "cost_drag_pct": round(cost_drag_pct, 1),
            "avg_holding_bars": round(avg_holding, 1),
            "regime_distribution": regime_dist,
            "win_rate_50": round(win_rate * 100, 1),
            "sharpe_50": round(sharpe, 2),
            "strategy_distribution": strat_dist,
            "top_veto_reasons": dict(top_vetoes),
            "total_exits": len(self._exits),
            "net_pnl": round(sum(e.pnl for e in self._exits), 2),
            "total_costs": round(total_cost, 2),
            "avg_confidence": round(avg_confidence, 1),
        }

    def save(self):
        """Persist metrics to disk."""
        try:
            os.makedirs(os.path.dirname(METRICS_FILE), exist_ok=True)
            with open(METRICS_FILE, "w") as f:
                json.dump(self.summary(), f, indent=2)
        except Exception:
            pass

    def load(self):
        """Load is a no-op — metrics are accumulated fresh each session."""
        self._loaded = True


# Singleton
_collector: Optional[LiveMetricsCollector] = None


def get_metrics_collector() -> LiveMetricsCollector:
    global _collector
    if _collector is None:
        _collector = LiveMetricsCollector()
    return _collector


if __name__ == "__main__":
    mc = LiveMetricsCollector()
    mc.record_signal("VanguardSniper", 72, 0.03, "QQQ3.L")
    mc.record_signal("ApexScout", 65, 0.02, "3USL.L")
    mc.record_exit("VanguardSniper", pnl=15.50, cost=5.90, holding_bars=12)
    mc.record_exit("ApexScout", pnl=-8.20, cost=5.90, holding_bars=8)
    mc.record_veto("CHECK_5_confidence_floor")
    mc.record_regime_tick("NORMAL")
    print(json.dumps(mc.summary(), indent=2))
