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


# ---------------------------------------------------------------------------
# A/B Testing & Statistical Comparison (Book 67 extension)
# ---------------------------------------------------------------------------

@dataclass
class ABComparison:
    """Result of an A/B strategy comparison."""
    control_name: str
    test_name: str
    control_result: ShadowResult
    test_result: ShadowResult
    p_value: float = 1.0
    effect_size: float = 0.0  # Cohen's d
    significant: bool = False


def welch_ttest(returns_a: List[float], returns_b: List[float]) -> Tuple[float, float]:
    """Welch's t-test for two samples with unequal variance.

    Returns: (t_statistic, p_value)
    Uses scipy-free implementation with Welch-Satterthwaite dof.
    """
    import numpy as np

    a = np.array(returns_a, dtype=float)
    b = np.array(returns_b, dtype=float)
    n_a, n_b = len(a), len(b)

    if n_a < 2 or n_b < 2:
        return 0.0, 1.0

    mean_a, mean_b = np.mean(a), np.mean(b)
    var_a, var_b = np.var(a, ddof=1), np.var(b, ddof=1)

    se_a = var_a / n_a
    se_b = var_b / n_b
    se_diff = math.sqrt(se_a + se_b)

    if se_diff < 1e-15:
        return 0.0, 1.0

    t_stat = (mean_a - mean_b) / se_diff

    # Welch-Satterthwaite degrees of freedom
    numerator = (se_a + se_b) ** 2
    denominator = (se_a ** 2) / (n_a - 1) + (se_b ** 2) / (n_b - 1)
    if denominator < 1e-15:
        return t_stat, 1.0
    dof = numerator / denominator

    # Approximate p-value using normal distribution for large dof,
    # else use a conservative t-distribution approximation
    # For stdlib-only: use the regularized incomplete beta function approach
    # Simplified: for dof > 30, normal approx is fine; else use conservative bound
    abs_t = abs(t_stat)
    if dof > 30:
        # Normal approximation (two-tailed)
        # P(|Z| > t) ~ 2 * erfc(t / sqrt(2)) / 2
        p_value = math.erfc(abs_t / math.sqrt(2))
    else:
        # Conservative approximation for smaller dof using normal
        # This slightly underestimates p-value (conservative = harder to promote)
        correction = 1.0 + 1.0 / (4.0 * max(dof, 1))
        p_value = math.erfc(abs_t / (math.sqrt(2) * correction))

    return float(t_stat), max(0.0, min(1.0, float(p_value)))


def effect_size_cohens_d(returns_a: List[float], returns_b: List[float]) -> float:
    """Cohen's d — standardized effect size between two return distributions.

    Interpretation: 0.2 = small, 0.5 = medium, 0.8 = large.
    Uses pooled standard deviation.
    """
    import numpy as np

    a = np.array(returns_a, dtype=float)
    b = np.array(returns_b, dtype=float)
    n_a, n_b = len(a), len(b)

    if n_a < 2 or n_b < 2:
        return 0.0

    mean_a, mean_b = np.mean(a), np.mean(b)
    var_a, var_b = np.var(a, ddof=1), np.var(b, ddof=1)

    # Pooled standard deviation
    pooled_var = ((n_a - 1) * var_a + (n_b - 1) * var_b) / (n_a + n_b - 2)
    pooled_std = math.sqrt(pooled_var)

    if pooled_std < 1e-15:
        return 0.0

    return float((mean_a - mean_b) / pooled_std)


def compare_strategies(
    tracker: ShadowTracker,
    strategy_a: str,
    strategy_b: str,
) -> ABComparison:
    """Run A/B comparison between two shadow strategies.

    strategy_a = control (incumbent), strategy_b = test (challenger).
    """
    result_a = tracker.evaluate(strategy_a)
    result_b = tracker.evaluate(strategy_b)

    # Extract per-trade P&L returns
    strat_a = tracker._strategies.get(strategy_a)
    strat_b = tracker._strategies.get(strategy_b)

    returns_a = [t.virtual_pnl for t in strat_a.closed_trades] if strat_a else []
    returns_b = [t.virtual_pnl for t in strat_b.closed_trades] if strat_b else []

    comp = ABComparison(
        control_name=strategy_a,
        test_name=strategy_b,
        control_result=result_a,
        test_result=result_b,
    )

    if len(returns_a) < 10 or len(returns_b) < 10:
        log.info("A/B: insufficient trades (a=%d, b=%d) — need 10+ each",
                 len(returns_a), len(returns_b))
        return comp

    t_stat, p_val = welch_ttest(returns_b, returns_a)  # test vs control
    d = effect_size_cohens_d(returns_b, returns_a)

    comp.p_value = p_val
    comp.effect_size = d
    comp.significant = p_val < 0.05 and abs(d) > 0.2

    log.info("A/B: %s vs %s — t=%.3f, p=%.4f, d=%.3f, sig=%s",
             strategy_a, strategy_b, t_stat, p_val, d, comp.significant)
    return comp


def promotion_decision(
    comparison: ABComparison,
    min_p: float = 0.05,
    min_effect: float = 0.2,
) -> str:
    """Decide whether to PROMOTE, HOLD, or DEMOTE the test strategy.

    Returns:
      "PROMOTE" — test is significantly better than control
      "HOLD"    — inconclusive, keep running both
      "DEMOTE"  — test is significantly worse than control
    """
    p = comparison.p_value
    d = comparison.effect_size

    if p > min_p:
        # Not statistically significant — keep running
        return "HOLD"

    if d > min_effect:
        # Test significantly outperforms control
        return "PROMOTE"
    elif d < -min_effect:
        # Test significantly underperforms control
        return "DEMOTE"
    else:
        # Significant p but tiny effect — not actionable
        return "HOLD"
