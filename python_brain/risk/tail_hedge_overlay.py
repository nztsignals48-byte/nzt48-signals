"""
Tail Hedging Overlay

Dynamically hedges portfolio tail risk using inverse ETFs, VIX futures proxies,
or put-spread synthetics.

Reference:
- Taleb & Martin (2012) "Why risk parity is a losing strategy"
- Barclays Research (2024) "Tail Hedging in Active Portfolios"
- LongTail Alpha / Capstone hedging playbook

Strategy:
1. Monitor real-time portfolio drawdown + VaR + CVaR
2. Compute "tail load" = max drawdown over last N minutes
3. When tail load exceeds threshold, add inverse ETF position sized to offset
4. When regime normalizes, unwind hedge
5. Hedge sizing proportional to portfolio beta + CVaR breach

V5 hedging instruments:
- SH (ProShares Short S&P 500) — primary US hedge
- PSQ (ProShares Short QQQ) — NASDAQ hedge
- SDS / QID — 2x leveraged short (when crisis mode)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class HedgeRecommendation:
    hedge_symbol: str
    hedge_size_usd: float
    hedge_ratio: float                      # fraction of portfolio to hedge
    rationale: str
    urgency: str                            # "low" / "medium" / "high"


HEDGE_INSTRUMENTS = {
    "SH": {"name": "ProShares Short S&P 500", "leverage": -1, "target": "SPY"},
    "PSQ": {"name": "ProShares Short QQQ", "leverage": -1, "target": "QQQ"},
    "SDS": {"name": "ProShares UltraShort S&P 500", "leverage": -2, "target": "SPY"},
    "QID": {"name": "ProShares UltraShort QQQ", "leverage": -2, "target": "QQQ"},
}


def compute_tail_load(
    equity_curve_recent: np.ndarray,
    lookback_minutes: int = 60,
) -> dict:
    """
    Compute current tail load metrics.

    Returns: {drawdown_pct, downside_vol, worst_drop_pct}
    """
    if len(equity_curve_recent) < 10:
        return {"drawdown_pct": 0, "downside_vol": 0, "worst_drop_pct": 0}

    running_max = np.maximum.accumulate(equity_curve_recent)
    drawdown = (running_max - equity_curve_recent) / np.maximum(running_max, 1e-9)
    current_drawdown = float(drawdown[-1])

    # Downside vol (std of negative returns)
    returns = np.diff(equity_curve_recent) / np.maximum(equity_curve_recent[:-1], 1e-9)
    neg_returns = returns[returns < 0]
    downside_vol = float(neg_returns.std()) if len(neg_returns) > 0 else 0.0

    worst_drop = float(drawdown.max())

    return {
        "drawdown_pct": current_drawdown,
        "downside_vol": downside_vol,
        "worst_drop_pct": worst_drop,
    }


def classify_tail_regime(
    tail_load: dict,
    vix_level: float | None = None,
) -> str:
    """
    Classify current tail regime.

    Returns: "benign" / "elevated" / "stressed" / "crisis"
    """
    dd = tail_load.get("drawdown_pct", 0)
    vol = tail_load.get("downside_vol", 0)

    if vix_level and vix_level > 35:
        return "crisis"
    if dd > 0.08 or (vix_level and vix_level > 25):
        return "stressed"
    if dd > 0.04 or vol > 0.03:
        return "elevated"
    return "benign"


def recommend_hedge(
    portfolio_value: float,
    portfolio_beta: float,
    tail_regime: str,
    equity_exposure_usd: float,
    current_hedge_usd: float = 0.0,
    max_hedge_ratio: float = 0.3,
) -> HedgeRecommendation | None:
    """
    Recommend hedge size based on tail regime.

    Returns None if no hedge change needed.
    """
    if tail_regime == "benign":
        # Unwind any hedges
        if current_hedge_usd > 100:
            return HedgeRecommendation(
                hedge_symbol="SH",
                hedge_size_usd=-current_hedge_usd,  # sell existing
                hedge_ratio=0.0,
                rationale="Benign regime: unwind hedges",
                urgency="low",
            )
        return None

    # Target hedge ratio based on regime
    regime_targets = {
        "elevated": 0.05,       # 5% of portfolio
        "stressed": 0.15,       # 15%
        "crisis": max_hedge_ratio,
    }
    target_ratio = regime_targets.get(tail_regime, 0.0)

    # Beta-adjusted target
    beta_adjusted_target = target_ratio * portfolio_beta
    beta_adjusted_target = min(beta_adjusted_target, max_hedge_ratio)

    target_hedge_usd = portfolio_value * beta_adjusted_target
    hedge_delta = target_hedge_usd - current_hedge_usd

    # Only trade if delta is meaningful
    if abs(hedge_delta) < portfolio_value * 0.01:
        return None

    # Pick instrument based on regime severity
    if tail_regime == "crisis":
        symbol = "SDS"  # 2x short
    elif tail_regime == "stressed":
        symbol = "SH"
    else:
        symbol = "SH"

    return HedgeRecommendation(
        hedge_symbol=symbol,
        hedge_size_usd=hedge_delta,
        hedge_ratio=beta_adjusted_target,
        rationale=f"{tail_regime} regime, beta={portfolio_beta:.2f}: hedge {beta_adjusted_target:.1%}",
        urgency={"elevated": "low", "stressed": "medium", "crisis": "high"}[tail_regime],
    )


class TailHedgeManager:
    """Manages tail hedge state + recommendations over time."""

    def __init__(
        self,
        portfolio_beta: float = 1.0,
        max_hedge_ratio: float = 0.3,
        rebalance_threshold_pct: float = 0.02,
    ):
        self.portfolio_beta = portfolio_beta
        self.max_hedge_ratio = max_hedge_ratio
        self.rebalance_threshold = rebalance_threshold_pct
        self.current_hedges: dict[str, float] = {}  # symbol -> usd value
        self.equity_curve: list[float] = []
        self.last_regime: str = "benign"

    def update_equity(self, value: float) -> None:
        self.equity_curve.append(value)
        if len(self.equity_curve) > 500:
            self.equity_curve.pop(0)

    def update_hedge_position(self, symbol: str, usd_value: float) -> None:
        if abs(usd_value) < 1:
            self.current_hedges.pop(symbol, None)
        else:
            self.current_hedges[symbol] = usd_value

    def evaluate(
        self,
        portfolio_value: float,
        equity_exposure_usd: float,
        vix_level: float | None = None,
    ) -> HedgeRecommendation | None:
        """Evaluate whether hedge should be added/adjusted/removed."""
        if len(self.equity_curve) < 10:
            return None

        tail_load = compute_tail_load(np.array(self.equity_curve))
        regime = classify_tail_regime(tail_load, vix_level)

        total_hedge = sum(self.current_hedges.values())
        rec = recommend_hedge(
            portfolio_value=portfolio_value,
            portfolio_beta=self.portfolio_beta,
            tail_regime=regime,
            equity_exposure_usd=equity_exposure_usd,
            current_hedge_usd=total_hedge,
            max_hedge_ratio=self.max_hedge_ratio,
        )
        self.last_regime = regime
        return rec


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        rng = np.random.default_rng(42)

        # Simulate equity curve with a crash
        normal = np.cumsum(rng.normal(0.001, 0.01, 100))
        crash = normal[-1] - np.abs(rng.normal(0.01, 0.005, 50)).cumsum()
        recovery = crash[-1] + np.cumsum(rng.normal(0.0005, 0.008, 50))
        equity_curve = 10000 * np.exp(np.concatenate([normal, crash, recovery]))

        tail_load = compute_tail_load(equity_curve)
        print(f"Tail load: {tail_load}")

        # Classify at different points
        mid_crash = equity_curve[:150]
        crash_load = compute_tail_load(mid_crash)
        regime = classify_tail_regime(crash_load, vix_level=22)
        print(f"Mid-crash regime: {regime}")

        # Recommend hedge
        rec = recommend_hedge(
            portfolio_value=10000,
            portfolio_beta=1.1,
            tail_regime=regime,
            equity_exposure_usd=9000,
            current_hedge_usd=0,
        )
        if rec:
            print(f"Hedge rec: {rec.hedge_symbol} ${rec.hedge_size_usd:.0f} ratio={rec.hedge_ratio:.2%}")
            print(f"Urgency: {rec.urgency} - {rec.rationale}")

        # Test manager
        mgr = TailHedgeManager(portfolio_beta=1.1)
        for v in equity_curve:
            mgr.update_equity(float(v))

        rec = mgr.evaluate(
            portfolio_value=10000,
            equity_exposure_usd=9000,
            vix_level=30,
        )
        if rec:
            print(f"\nManager rec: {rec.hedge_symbol} ${rec.hedge_size_usd:.0f}")
        print("OK")
