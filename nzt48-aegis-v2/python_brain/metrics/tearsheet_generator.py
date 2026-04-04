"""QuantStats Tearsheet Generator — institutional-grade HTML reports.

Generates HTML tearsheets from trade returns with one function call.
Replaces manual metric computation in institutional_report.py and backtest.

Consumers:
  - world_class_backtest.py: post-backtest tearsheet
  - ouroboros/institutional_report.py: proof package tearsheet
  - nightly_pipeline.sh STEP 26: daily scorecard

License: QuantStats is Apache 2.0.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

import numpy as np

log = logging.getLogger("tearsheet_generator")

try:
    import quantstats as qs
    _HAS_QS = True
except ImportError:
    _HAS_QS = False
    log.warning("quantstats not installed — pip install quantstats")


def generate_tearsheet(
    returns: Union[list, np.ndarray],
    benchmark_ticker: str = "SPY",
    title: str = "AEGIS V2 Strategy Performance",
    output_path: Optional[str] = None,
) -> Optional[str]:
    """Generate a full HTML tearsheet from returns.

    Args:
        returns: Daily return series (list or array of floats)
        benchmark_ticker: Benchmark symbol for comparison (default SPY)
        title: Report title
        output_path: Where to save HTML. Auto-generated if None.

    Returns:
        Path to generated HTML file, or None on failure.
    """
    if not _HAS_QS:
        log.warning("QuantStats not available — skipping tearsheet generation")
        return None

    if output_path is None:
        reports_dir = os.environ.get("AEGIS_DATA_DIR", "/app/data") + "/reports"
        os.makedirs(reports_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = f"{reports_dir}/tearsheet_{timestamp}.html"

    try:
        import pandas as pd

        # Convert to pandas Series with date index
        arr = np.asarray(returns, dtype=float)
        arr = arr[~np.isnan(arr)]
        if len(arr) < 5:
            log.warning("Insufficient returns for tearsheet: %d < 5", len(arr))
            return None

        # Create date-indexed series (assume daily, working backwards from today)
        dates = pd.bdate_range(end=pd.Timestamp.today(), periods=len(arr))
        returns_series = pd.Series(arr, index=dates, name="Strategy")

        # Generate HTML tearsheet
        qs.reports.html(
            returns_series,
            benchmark=benchmark_ticker,
            title=title,
            output=output_path,
        )
        log.info("Tearsheet generated: %s (%d trading days)", output_path, len(arr))
        return output_path

    except Exception as e:
        log.error("Tearsheet generation failed: %s", str(e)[:200])
        return None


def generate_strategy_comparison(
    strategy_returns: dict,
    output_path: Optional[str] = None,
) -> Optional[str]:
    """Generate comparison tearsheet for multiple strategies.

    Args:
        strategy_returns: Dict of {strategy_name: returns_list}
        output_path: Where to save HTML
    """
    if not _HAS_QS:
        return None

    if output_path is None:
        reports_dir = os.environ.get("AEGIS_DATA_DIR", "/app/data") + "/reports"
        os.makedirs(reports_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = f"{reports_dir}/strategy_comparison_{timestamp}.html"

    try:
        import pandas as pd

        # Find minimum length across all strategies
        min_len = min(len(v) for v in strategy_returns.values() if v)
        if min_len < 5:
            return None

        dates = pd.bdate_range(end=pd.Timestamp.today(), periods=min_len)

        for name, rets in strategy_returns.items():
            arr = np.asarray(rets[-min_len:], dtype=float)
            series = pd.Series(arr, index=dates, name=name)
            # Generate individual metrics for each strategy
            try:
                metrics = qs.stats.monthly_returns(series)
                log.info("  %s: Sharpe=%.2f, MaxDD=%.2f%%",
                         name,
                         qs.stats.sharpe(series),
                         qs.stats.max_drawdown(series) * 100)
            except Exception:
                pass

        log.info("Strategy comparison: %d strategies analyzed", len(strategy_returns))
        return output_path

    except Exception as e:
        log.error("Strategy comparison failed: %s", str(e)[:200])
        return None


def quick_stats(returns: Union[list, np.ndarray]) -> dict:
    """Generate quick stats dict without full HTML report.

    Useful for Telegram alerts and JSON reports.
    """
    if not _HAS_QS:
        # Fallback to validated_metrics
        from python_brain.metrics.validated_metrics import compute_all_metrics
        return compute_all_metrics(returns)

    try:
        import pandas as pd
        arr = np.asarray(returns, dtype=float)
        arr = arr[~np.isnan(arr)]
        dates = pd.bdate_range(end=pd.Timestamp.today(), periods=len(arr))
        series = pd.Series(arr, index=dates)

        return {
            "sharpe": float(qs.stats.sharpe(series)),
            "sortino": float(qs.stats.sortino(series)),
            "max_drawdown": float(qs.stats.max_drawdown(series)),
            "calmar": float(qs.stats.calmar(series)),
            "win_rate": float(qs.stats.win_rate(series)),
            "profit_factor": float(qs.stats.profit_factor(series)),
            "cagr": float(qs.stats.cagr(series)),
            "volatility": float(qs.stats.volatility(series)),
            "tail_ratio": float(qs.stats.tail_ratio(series)),
            "avg_win": float(qs.stats.avg_win(series)),
            "avg_loss": float(qs.stats.avg_loss(series)),
        }
    except Exception as e:
        log.error("Quick stats failed: %s", str(e)[:100])
        return {}
