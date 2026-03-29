"""Shadow Trading & A/B Testing Framework — Book 67.

Run candidate strategies in shadow mode alongside production.
Shadow strategies generate virtual signals and track virtual P&L
without affecting real capital.

This allows:
1. Testing new strategies with zero risk
2. A/B comparison of parameter variants
3. Collecting 100+ virtual trades before promotion decision
4. Measuring paper-to-live Sharpe degradation

Shadow signals are stored in WAL with event_type="ShadowSignal"
and never routed to the broker adapter.

Usage:
    from python_brain.validation.shadow_trading import (
        ShadowTracker, ShadowStrategy, ShadowResult,
    )

    tracker = ShadowTracker()
    tracker.register("candidate_v2", strategy_fn, params)
    tracker.on_tick(tick_data)  # generates virtual signals
    result = tracker.evaluate("candidate_v2")
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("shadow_trading")


@dataclass
class ShadowTrade:
    """A virtual trade from a shadow strategy."""
    ticker: str
    direction: str  # "long"
    entry_price: float
    entry_time_ns: int
    exit_price: float = 0.0
    exit_time_ns: int = 0
    confidence: int = 0
    strategy: str = ""
    virtual_pnl: float = 0.0
    is_open: bool = True

    def close(self, exit_price: float, exit_time_ns: int):
        self.exit_price = exit_price
        self.exit_time_ns = exit_time_ns
        self.virtual_pnl = exit_price - self.entry_price
        self.is_open = False


@dataclass
class ShadowResult:
    """Evaluation result for a shadow strategy."""
    name: str
    total_signals: int = 0
    total_trades: int = 0  # Signals that would have been approved by risk
    virtual_pnl: float = 0.0
    win_rate: float = 0.0
    sharpe: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_hold_time_mins: float = 0.0
    days_running: int = 0
    ready_for_promotion: bool = False
    promotion_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ShadowStrategy:
    """A registered shadow strategy."""
    name: str
    params: Dict[str, Any] = field(default_factory=dict)
    trades: List[ShadowTrade] = field(default_factory=list)
    signal_count: int = 0
    start_time_ns: int = 0
    is_active: bool = True

    @property
    def closed_trades(self) -> List[ShadowTrade]:
        return [t for t in self.trades if not t.is_open]

    @property
    def open_trades(self) -> List[ShadowTrade]:
        return [t for t in self.trades if t.is_open]


class ShadowTracker:
    """Manage shadow strategies running alongside production."""

    def __init__(self, min_trades_for_promotion: int = 100, min_days: int = 14):
        self._strategies: Dict[str, ShadowStrategy] = {}
        self.min_trades = min_trades_for_promotion
        self.min_days = min_days

    def register(
        self,
        name: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> ShadowStrategy:
        """Register a new shadow strategy."""
        strat = ShadowStrategy(
            name=name,
            params=params or {},
            start_time_ns=time.time_ns(),
        )
        self._strategies[name] = strat
        log.info("SHADOW: registered strategy '%s'", name)
        return strat

    def record_signal(
        self,
        strategy_name: str,
        ticker: str,
        direction: str,
        entry_price: float,
        confidence: int,
    ) -> Optional[ShadowTrade]:
        """Record a virtual signal from a shadow strategy."""
        strat = self._strategies.get(strategy_name)
        if strat is None or not strat.is_active:
            return None

        strat.signal_count += 1
        trade = ShadowTrade(
            ticker=ticker,
            direction=direction,
            entry_price=entry_price,
            entry_time_ns=time.time_ns(),
            confidence=confidence,
            strategy=strategy_name,
        )
        strat.trades.append(trade)
        return trade

    def close_trade(
        self,
        strategy_name: str,
        ticker: str,
        exit_price: float,
    ):
        """Close the most recent open trade for a ticker in a shadow strategy."""
        strat = self._strategies.get(strategy_name)
        if strat is None:
            return

        for trade in reversed(strat.trades):
            if trade.ticker == ticker and trade.is_open:
                trade.close(exit_price, time.time_ns())
                break

    def evaluate(self, strategy_name: str) -> ShadowResult:
        """Evaluate a shadow strategy's performance."""
        strat = self._strategies.get(strategy_name)
        if strat is None:
            return ShadowResult(name=strategy_name)

        closed = strat.closed_trades
        result = ShadowResult(
            name=strategy_name,
            total_signals=strat.signal_count,
            total_trades=len(closed),
        )

        if not closed:
            return result

        # Virtual P&L
        pnls = [t.virtual_pnl for t in closed]
        result.virtual_pnl = sum(pnls)
        result.win_rate = sum(1 for p in pnls if p > 0) / len(pnls)

        # Sharpe
        import numpy as np
        arr = np.array(pnls)
        if np.std(arr) > 0:
            result.sharpe = float(np.mean(arr) / np.std(arr) * np.sqrt(252))

        # Max drawdown
        cum = np.cumsum(arr)
        running_max = np.maximum.accumulate(cum)
        dd = running_max - cum
        result.max_drawdown_pct = float(np.max(dd)) if len(dd) > 0 else 0

        # Days running
        elapsed_ns = time.time_ns() - strat.start_time_ns
        result.days_running = max(1, int(elapsed_ns / (86400 * 1e9)))

        # Promotion check
        if (result.total_trades >= self.min_trades
                and result.days_running >= self.min_days
                and result.sharpe > 0.5
                and result.win_rate > 0.40
                and result.max_drawdown_pct < 15):
            result.ready_for_promotion = True
            result.promotion_reason = (
                f"trades={result.total_trades}, days={result.days_running}, "
                f"Sharpe={result.sharpe:.2f}, WR={result.win_rate:.1%}"
            )
        else:
            reasons = []
            if result.total_trades < self.min_trades:
                reasons.append(f"need {self.min_trades} trades (have {result.total_trades})")
            if result.days_running < self.min_days:
                reasons.append(f"need {self.min_days} days (have {result.days_running})")
            if result.sharpe <= 0.5:
                reasons.append(f"Sharpe {result.sharpe:.2f} < 0.5")
            if result.win_rate <= 0.40:
                reasons.append(f"WR {result.win_rate:.1%} < 40%")
            result.promotion_reason = "; ".join(reasons)

        return result

    def evaluate_all(self) -> Dict[str, ShadowResult]:
        """Evaluate all shadow strategies."""
        return {name: self.evaluate(name) for name in self._strategies}

    def save_state(self, output_dir: Path):
        """Persist shadow state for cross-session continuity."""
        output_dir.mkdir(parents=True, exist_ok=True)
        state = {}
        for name, strat in self._strategies.items():
            state[name] = {
                "params": strat.params,
                "signal_count": strat.signal_count,
                "start_time_ns": strat.start_time_ns,
                "trade_count": len(strat.trades),
                "closed_count": len(strat.closed_trades),
                "open_count": len(strat.open_trades),
                "evaluation": self.evaluate(name).to_dict(),
            }
        path = output_dir / "shadow_state.json"
        with open(path, "w") as f:
            json.dump(state, f, indent=2, default=str)
        log.info("Shadow state saved: %s (%d strategies)", path, len(state))
