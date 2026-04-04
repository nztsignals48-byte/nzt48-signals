"""Validated Financial Metrics — empyrical-reloaded wrapper.

Replaces manual Sharpe/Sortino/Calmar/MaxDD calculations across the codebase
with empyrical's validated, academically-correct implementations.

Consumers:
  - bridge.py: _strategy_pnl_history → compounding machine metrics
  - nightly_v6.py: daily trade analysis → tearsheet metrics
  - bayesian.py: deflated Sharpe ratio base input
  - monte_carlo/engine.py: simulation metrics
  - world_class_backtest.py: proof package metrics

License: empyrical-reloaded is Apache 2.0.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Union

import numpy as np

log = logging.getLogger("validated_metrics")

# Try to import empyrical; fall back to manual calculations if unavailable
try:
    import empyrical
    _HAS_EMPYRICAL = True
except ImportError:
    _HAS_EMPYRICAL = False
    log.warning("empyrical-reloaded not installed — using manual metric calculations")


def sharpe_ratio(
    returns: Union[List[float], np.ndarray],
    risk_free: float = 0.0,
    annualization: int = 252,
) -> float:
    """Annualized Sharpe ratio.

    Uses empyrical if available, falls back to manual calculation.
    """
    if not returns or (isinstance(returns, np.ndarray) and len(returns) == 0):
        return 0.0

    arr = np.asarray(returns, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 2:
        return 0.0

    if _HAS_EMPYRICAL:
        try:
            return float(empyrical.sharpe_ratio(arr, risk_free=risk_free, annualization=annualization))
        except Exception:
            pass

    # Manual fallback
    excess = arr - risk_free / annualization
    if excess.std() == 0:
        return 0.0
    return float(excess.mean() / excess.std() * np.sqrt(annualization))


def sortino_ratio(
    returns: Union[List[float], np.ndarray],
    required_return: float = 0.0,
    annualization: int = 252,
) -> float:
    """Annualized Sortino ratio (downside deviation only)."""
    if not returns or (isinstance(returns, np.ndarray) and len(returns) == 0):
        return 0.0

    arr = np.asarray(returns, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 2:
        return 0.0

    if _HAS_EMPYRICAL:
        try:
            return float(empyrical.sortino_ratio(arr, required_return=required_return, annualization=annualization))
        except Exception:
            pass

    # Manual fallback
    excess = arr - required_return / annualization
    downside = np.minimum(excess, 0)
    downside_std = np.sqrt(np.mean(downside ** 2))
    if downside_std == 0:
        return 0.0
    return float(excess.mean() / downside_std * np.sqrt(annualization))


def calmar_ratio(
    returns: Union[List[float], np.ndarray],
    annualization: int = 252,
) -> float:
    """Calmar ratio (annualized return / max drawdown)."""
    if not returns or (isinstance(returns, np.ndarray) and len(returns) == 0):
        return 0.0

    arr = np.asarray(returns, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 2:
        return 0.0

    if _HAS_EMPYRICAL:
        try:
            return float(empyrical.calmar_ratio(arr, annualization=annualization))
        except Exception:
            pass

    # Manual fallback
    ann_return = float(np.mean(arr) * annualization)
    dd = max_drawdown(arr)
    if dd == 0:
        return 0.0
    return ann_return / abs(dd)


def max_drawdown(
    returns: Union[List[float], np.ndarray],
) -> float:
    """Maximum drawdown (as a negative percentage)."""
    if not returns or (isinstance(returns, np.ndarray) and len(returns) == 0):
        return 0.0

    arr = np.asarray(returns, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 1:
        return 0.0

    if _HAS_EMPYRICAL:
        try:
            return float(empyrical.max_drawdown(arr))
        except Exception:
            pass

    # Manual fallback
    cum = np.cumprod(1 + arr)
    running_max = np.maximum.accumulate(cum)
    drawdowns = cum / running_max - 1
    return float(np.min(drawdowns))


def annual_return(
    returns: Union[List[float], np.ndarray],
    annualization: int = 252,
) -> float:
    """Annualized return (CAGR)."""
    if not returns or (isinstance(returns, np.ndarray) and len(returns) == 0):
        return 0.0

    arr = np.asarray(returns, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 1:
        return 0.0

    if _HAS_EMPYRICAL:
        try:
            return float(empyrical.annual_return(arr, annualization=annualization))
        except Exception:
            pass

    # Manual fallback
    cum = np.prod(1 + arr)
    n_years = len(arr) / annualization
    if n_years <= 0 or cum <= 0:
        return 0.0
    return float(cum ** (1 / n_years) - 1)


def annual_volatility(
    returns: Union[List[float], np.ndarray],
    annualization: int = 252,
) -> float:
    """Annualized volatility."""
    if not returns or (isinstance(returns, np.ndarray) and len(returns) == 0):
        return 0.0

    arr = np.asarray(returns, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 2:
        return 0.0

    if _HAS_EMPYRICAL:
        try:
            return float(empyrical.annual_volatility(arr, annualization=annualization))
        except Exception:
            pass

    return float(np.std(arr, ddof=1) * np.sqrt(annualization))


def tail_ratio(returns: Union[List[float], np.ndarray]) -> float:
    """Tail ratio: abs(95th percentile) / abs(5th percentile)."""
    arr = np.asarray(returns, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 20:
        return 1.0

    if _HAS_EMPYRICAL:
        try:
            return float(empyrical.tail_ratio(arr))
        except Exception:
            pass

    p95 = np.percentile(arr, 95)
    p5 = np.percentile(arr, 5)
    if p5 == 0:
        return 1.0
    return float(abs(p95) / abs(p5))


def compute_all_metrics(
    returns: Union[List[float], np.ndarray],
    benchmark_returns: Optional[Union[List[float], np.ndarray]] = None,
    annualization: int = 252,
) -> Dict[str, float]:
    """Compute all key metrics in a single call.

    Returns dict with all metrics suitable for reports and proof packages.
    """
    result = {
        "sharpe_ratio": sharpe_ratio(returns, annualization=annualization),
        "sortino_ratio": sortino_ratio(returns, annualization=annualization),
        "calmar_ratio": calmar_ratio(returns, annualization=annualization),
        "max_drawdown": max_drawdown(returns),
        "annual_return": annual_return(returns, annualization=annualization),
        "annual_volatility": annual_volatility(returns, annualization=annualization),
        "tail_ratio": tail_ratio(returns),
    }

    arr = np.asarray(returns, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) > 0:
        result["win_rate"] = float(np.sum(arr > 0) / len(arr))
        result["profit_factor"] = float(
            np.sum(arr[arr > 0]) / max(abs(np.sum(arr[arr < 0])), 1e-9)
        )
        result["total_trades"] = len(arr)
        result["cumulative_return"] = float(np.prod(1 + arr) - 1)

    # Alpha/beta vs benchmark if provided
    if benchmark_returns is not None and _HAS_EMPYRICAL:
        bench = np.asarray(benchmark_returns, dtype=float)
        min_len = min(len(arr), len(bench))
        if min_len >= 20:
            try:
                result["alpha"] = float(empyrical.alpha(arr[:min_len], bench[:min_len], annualization=annualization))
                result["beta"] = float(empyrical.beta(arr[:min_len], bench[:min_len]))
            except Exception:
                pass

    return result
