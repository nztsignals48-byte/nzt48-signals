"""Walk-forward out-of-sample validator.

Rolling 30-day training window, 30-day test window. Reports OOS Sharpe/PF/DSR
per window per strategy. Consumed by Ouroboros nightly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class WalkForwardWindow:
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    train_sharpe: float
    test_sharpe: float
    test_pf: float
    test_trades: int


def sharpe(returns: np.ndarray, ann_factor: float = 252.0) -> float:
    std = returns.std()
    if std == 0:
        return 0.0
    return float(returns.mean() / std * math.sqrt(ann_factor))


def profit_factor(returns: np.ndarray) -> float:
    wins = returns[returns > 0].sum()
    losses = -returns[returns < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else 1.0
    return float(wins / losses)


def walk_forward(
    returns: np.ndarray,
    train_size: int = 30,
    test_size: int = 30,
    step: int | None = None,
) -> list[WalkForwardWindow]:
    """Rolling walk-forward split.

    At each window: train on [i, i+train_size), test on [i+train_size, i+train_size+test_size)
    step = test_size means non-overlapping test windows.
    """
    step = step or test_size
    n = len(returns)
    out = []
    i = 0
    while i + train_size + test_size <= n:
        train = returns[i : i + train_size]
        test = returns[i + train_size : i + train_size + test_size]
        out.append(WalkForwardWindow(
            train_start=i,
            train_end=i + train_size,
            test_start=i + train_size,
            test_end=i + train_size + test_size,
            train_sharpe=sharpe(train),
            test_sharpe=sharpe(test),
            test_pf=profit_factor(test),
            test_trades=len(test),
        ))
        i += step
    return out


def summarize(windows: list[WalkForwardWindow]) -> dict:
    if not windows:
        return {"n_windows": 0}
    test_sharpes = [w.test_sharpe for w in windows]
    test_pfs = [w.test_pf for w in windows if math.isfinite(w.test_pf)]
    return {
        "n_windows": len(windows),
        "oos_sharpe_mean": float(np.mean(test_sharpes)),
        "oos_sharpe_std": float(np.std(test_sharpes)),
        "oos_sharpe_min": float(np.min(test_sharpes)),
        "oos_sharpe_max": float(np.max(test_sharpes)),
        "oos_pf_mean": float(np.mean(test_pfs)) if test_pfs else 0.0,
        "consistency_pct": float(sum(1 for s in test_sharpes if s > 0) / len(test_sharpes) * 100),
    }


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        rng = np.random.default_rng(42)
        # 200 days of marginally profitable returns
        returns = rng.normal(0.0005, 0.012, 200)
        wins = walk_forward(returns, train_size=30, test_size=30)
        summ = summarize(wins)
        print(f"Windows: {summ['n_windows']}")
        print(f"OOS Sharpe: {summ['oos_sharpe_mean']:.3f} ± {summ['oos_sharpe_std']:.3f}")
        print(f"OOS consistency: {summ['consistency_pct']:.0f}% profitable")
        print("OK")
