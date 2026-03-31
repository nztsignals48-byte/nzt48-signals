"""Book 133: Causal Inference for Persistent Alpha.

Implements simple Granger causality testing and alpha persistence checking
using stdlib-only OLS regression. These tools help the self-reflection loop
distinguish truly persistent alpha sources from those that are decaying or
spurious (curve-fitted).

Key idea: If a strategy's edge persists across multiple non-overlapping
time windows (train/validate/test), the alpha is more likely causal than
coincidental. Strategies with rapidly decaying Sharpe ratios across windows
are flagged for review.

Usage (nightly — alpha persistence check):
    from python_brain.analytics.causal_alpha import run_alpha_persistence_check
    report = run_alpha_persistence_check(trade_history)
    # report: {"S1_Microstructure": {"persistent": True, "sharpes": [1.2, 0.9, 0.8], "decay_rate": 0.17}, ...}

Usage (research — Granger test):
    from python_brain.analytics.causal_alpha import granger_test_simple
    p_value = granger_test_simple(x_series, y_series, lag=5)
    if p_value < 0.05:
        print("x Granger-causes y at 5% significance")
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("causal_alpha")

# ---------------------------------------------------------------------------
# OLS regression (pure stdlib)
# ---------------------------------------------------------------------------

def _ols_residual_ss(y: List[float], X: List[List[float]]) -> float:
    """Compute residual sum of squares from OLS: y = X @ beta + epsilon.

    Uses the normal equation: beta = (X'X)^{-1} X'y.
    For small systems (lag <= 10, N < 1000), this is efficient enough.

    Args:
        y: Response vector, length N.
        X: Design matrix, N x K (list of K-length row vectors).

    Returns:
        Residual sum of squares (RSS).
    """
    N = len(y)
    if N == 0 or not X:
        return 0.0
    K = len(X[0])
    if N <= K:
        return 0.0  # Underdetermined — cannot estimate

    # X'X (K x K)
    XtX = [[0.0] * K for _ in range(K)]
    for i in range(K):
        for j in range(K):
            s = 0.0
            for n in range(N):
                s += X[n][i] * X[n][j]
            XtX[i][j] = s

    # X'y (K x 1)
    Xty = [0.0] * K
    for i in range(K):
        s = 0.0
        for n in range(N):
            s += X[n][i] * y[n]
        Xty[i] = s

    # Solve via Gaussian elimination (XtX @ beta = Xty)
    beta = _solve_linear_system(XtX, Xty)
    if beta is None:
        return sum(yi ** 2 for yi in y)  # Singular — all residual

    # Compute residuals
    rss = 0.0
    for n in range(N):
        pred = sum(X[n][k] * beta[k] for k in range(K))
        rss += (y[n] - pred) ** 2

    return rss


def _solve_linear_system(A: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """Solve A @ x = b via Gaussian elimination with partial pivoting.

    Args:
        A: N x N matrix (will be modified in-place).
        b: N-length vector (will be modified in-place).

    Returns:
        Solution vector x, or None if singular.
    """
    N = len(b)
    # Augment
    M = [A[i][:] + [b[i]] for i in range(N)]

    for col in range(N):
        # Partial pivoting
        max_row = col
        max_val = abs(M[col][col])
        for row in range(col + 1, N):
            if abs(M[row][col]) > max_val:
                max_val = abs(M[row][col])
                max_row = row
        if max_val < 1e-12:
            return None  # Singular
        M[col], M[max_row] = M[max_row], M[col]

        # Eliminate below
        pivot = M[col][col]
        for row in range(col + 1, N):
            factor = M[row][col] / pivot
            for j in range(col, N + 1):
                M[row][j] -= factor * M[col][j]

    # Back-substitution
    x = [0.0] * N
    for row in range(N - 1, -1, -1):
        if abs(M[row][row]) < 1e-12:
            return None
        s = M[row][N]
        for j in range(row + 1, N):
            s -= M[row][j] * x[j]
        x[row] = s / M[row][row]

    return x


# ---------------------------------------------------------------------------
# F-distribution approximation (for p-value without scipy)
# ---------------------------------------------------------------------------

def _f_cdf_approx(f_stat: float, df1: int, df2: int) -> float:
    """Approximate F-distribution CDF using the normal approximation.

    For df2 > 40, this is reasonably accurate. For smaller df2, it's
    a rough estimate — sufficient for our screening purposes (we care
    about p < 0.05, not exact values).

    Uses the Abramowitz & Stegun transformation:
        z = ((f * df1/df2)^(1/3) - (1 - 2/(9*df2))) / sqrt(2/(9*df2))
    Then p = Phi(z) where Phi is standard normal CDF.

    Returns:
        Approximate CDF value (0 to 1).
    """
    if f_stat <= 0 or df1 <= 0 or df2 <= 0:
        return 0.0

    # Abramowitz & Stegun approximation
    a = 2.0 / (9.0 * df1)
    b = 2.0 / (9.0 * df2)
    x = f_stat ** (1.0 / 3.0)

    num = x * (1.0 - b) - (1.0 - a)
    denom = (x * x * b + a) ** 0.5

    if denom < 1e-12:
        return 0.5

    z = num / denom

    # Standard normal CDF via error function approximation
    return _normal_cdf(z)


def _normal_cdf(z: float) -> float:
    """Approximate standard normal CDF using Abramowitz & Stegun 26.2.17.

    |error| < 7.5e-8 for all z.
    """
    if z < -8.0:
        return 0.0
    if z > 8.0:
        return 1.0

    sign = 1 if z >= 0 else -1
    z = abs(z)

    p = 0.2316419
    b1 = 0.319381530
    b2 = -0.356563782
    b3 = 1.781477937
    b4 = -1.821255978
    b5 = 1.330274429

    t = 1.0 / (1.0 + p * z)
    phi = (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * z * z)
    poly = t * (b1 + t * (b2 + t * (b3 + t * (b4 + t * b5))))
    area = 1.0 - phi * poly

    if sign < 0:
        area = 1.0 - area

    return area


# ---------------------------------------------------------------------------
# Granger causality test
# ---------------------------------------------------------------------------

def granger_test_simple(
    x: List[float],
    y: List[float],
    lag: int = 5,
) -> float:
    """Simple Granger causality test: does x help predict y?

    Compares two OLS models:
        Restricted:   y_t = a_0 + sum(a_i * y_{t-i})
        Unrestricted: y_t = a_0 + sum(a_i * y_{t-i}) + sum(b_i * x_{t-i})

    If the unrestricted model has significantly lower RSS, x "Granger-causes" y.

    Args:
        x: Time series that might cause y. Length >= lag + 10.
        y: Time series to predict. Same length as x.
        lag: Number of lags to test (default 5).

    Returns:
        p-value from the F-test. Low p (<0.05) = x Granger-causes y.
        Returns 1.0 if insufficient data or computation fails.
    """
    if len(x) != len(y):
        log.warning("granger_test: x and y must be same length")
        return 1.0

    N = len(y)
    min_obs = lag + 10  # Need enough observations after creating lagged features
    if N < min_obs:
        log.debug("granger_test: insufficient data (%d < %d)", N, min_obs)
        return 1.0

    # Build lagged datasets (starting from index=lag so all lags are available)
    T = N - lag  # Number of usable observations
    y_target = [y[t] for t in range(lag, N)]

    # Restricted model: y_t ~ 1 + y_{t-1} + ... + y_{t-lag}
    X_restricted = []
    for t in range(lag, N):
        row = [1.0]  # intercept
        for l in range(1, lag + 1):
            row.append(y[t - l])
        X_restricted.append(row)

    # Unrestricted model: y_t ~ 1 + y_{t-1}...y_{t-lag} + x_{t-1}...x_{t-lag}
    X_unrestricted = []
    for t in range(lag, N):
        row = [1.0]  # intercept
        for l in range(1, lag + 1):
            row.append(y[t - l])
        for l in range(1, lag + 1):
            row.append(x[t - l])
        X_unrestricted.append(row)

    rss_r = _ols_residual_ss(y_target, X_restricted)
    rss_u = _ols_residual_ss(y_target, X_unrestricted)

    if rss_u <= 0 or rss_r <= 0:
        return 1.0

    # F-statistic: ((RSS_r - RSS_u) / q) / (RSS_u / (T - k))
    # where q = additional parameters (lag x-lags), k = total params unrestricted
    q = lag  # Number of additional x-lag coefficients
    k = 1 + 2 * lag  # intercept + lag y-lags + lag x-lags
    df2 = T - k

    if df2 <= 0:
        return 1.0

    if rss_u < 1e-15:
        # Perfect fit (degenerate case)
        return 0.0

    f_stat = ((rss_r - rss_u) / q) / (rss_u / df2)

    if f_stat <= 0:
        return 1.0

    # p-value = 1 - F_CDF(f_stat, q, df2)
    p_value = 1.0 - _f_cdf_approx(f_stat, q, df2)

    log.debug(
        "GRANGER: F=%.3f df1=%d df2=%d p=%.4f RSS_r=%.4f RSS_u=%.4f",
        f_stat, q, df2, p_value, rss_r, rss_u,
    )

    return max(0.0, min(1.0, p_value))


# ---------------------------------------------------------------------------
# Alpha persistence check
# ---------------------------------------------------------------------------

def _compute_sharpe(returns: List[float]) -> float:
    """Compute Sharpe ratio (mean / stdev) for a return series.

    Returns 0.0 if insufficient data or zero variance.
    """
    n = len(returns)
    if n < 3:
        return 0.0

    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    stdev = variance ** 0.5

    if stdev < 1e-9:
        return 0.0

    return mean / stdev


def check_alpha_persistence(
    strategy_name: str,
    returns: List[float],
    window: int = 60,
) -> Dict[str, Any]:
    """Check if a strategy's alpha persists across 3 non-overlapping windows.

    Splits the return series into 3 equal windows (train/validate/test) and
    checks if Sharpe > 0 in all three. Also computes a decay rate — the
    rate at which Sharpe declines from the earliest to latest window.

    Args:
        strategy_name: Strategy identifier (for logging).
        returns: List of per-trade returns (not cumulative). Should have >= 3*window obs,
                 but will work with >= 9 observations (3 per window min).
        window: Target observations per window (default 60).

    Returns:
        {
            "strategy": str,
            "persistent": bool,       # True if Sharpe > 0 in all 3 windows
            "sharpes": [s1, s2, s3],   # Sharpe per window (train, validate, test)
            "decay_rate": float,       # 0 = no decay, 1 = complete decay
            "n_total": int,            # Total observations used
            "verdict": str,            # "PERSISTENT" | "DECAYING" | "DEAD" | "INSUFFICIENT"
        }
    """
    n = len(returns)

    if n < 9:
        return {
            "strategy": strategy_name,
            "persistent": False,
            "sharpes": [0.0, 0.0, 0.0],
            "decay_rate": 0.0,
            "n_total": n,
            "verdict": "INSUFFICIENT",
        }

    # Split into 3 equal windows
    third = n // 3
    w1 = returns[:third]
    w2 = returns[third:2 * third]
    w3 = returns[2 * third:]

    s1 = _compute_sharpe(w1)
    s2 = _compute_sharpe(w2)
    s3 = _compute_sharpe(w3)
    sharpes = [round(s1, 3), round(s2, 3), round(s3, 3)]

    persistent = s1 > 0 and s2 > 0 and s3 > 0

    # Decay rate: how much Sharpe drops from window 1 to window 3
    # 0 = no decay (or improvement), 1 = complete decay (Sharpe went to 0)
    if s1 > 0.01:
        decay_rate = max(0.0, (s1 - s3) / s1)
    elif s1 <= 0 and s3 <= 0:
        decay_rate = 1.0  # Both negative — alpha dead
    else:
        decay_rate = 0.0  # s1 near zero — can't compute meaningful decay

    decay_rate = round(min(1.0, decay_rate), 3)

    # Verdict
    if n < 20:
        verdict = "INSUFFICIENT"
    elif persistent and decay_rate < 0.3:
        verdict = "PERSISTENT"
    elif persistent and decay_rate >= 0.3:
        verdict = "DECAYING"
    elif s3 <= 0:
        verdict = "DEAD"
    else:
        verdict = "DECAYING"

    log.info(
        "ALPHA_PERSISTENCE: %s sharpes=[%.3f, %.3f, %.3f] decay=%.3f verdict=%s (n=%d)",
        strategy_name, s1, s2, s3, decay_rate, verdict, n,
    )

    return {
        "strategy": strategy_name,
        "persistent": persistent,
        "sharpes": sharpes,
        "decay_rate": decay_rate,
        "n_total": n,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Nightly: Run persistence check across all strategies
# ---------------------------------------------------------------------------

def run_alpha_persistence_check(
    trade_history: List[Dict[str, Any]],
    min_trades: int = 9,
) -> Dict[str, Dict[str, Any]]:
    """Run alpha persistence check for every strategy in trade history.

    Groups trades by strategy, extracts per-trade returns, and runs
    check_alpha_persistence for each.

    Args:
        trade_history: List of closed trade dicts. Each must have:
            - "strategy" (str)
            - "pnl" (float)
            - "entry_price" (float, > 0)
            Optional:
            - "qty" (int, default 1)
        min_trades: Minimum trades per strategy to include (default 9).

    Returns:
        Dict mapping strategy name to persistence result.
        Strategies with fewer than min_trades trades are included with
        verdict="INSUFFICIENT".
    """
    # Group returns by strategy
    strategy_returns: Dict[str, List[float]] = {}
    for trade in trade_history:
        strat = trade.get("strategy", "")
        if not strat:
            continue

        pnl = trade.get("pnl", 0.0)
        entry_price = trade.get("entry_price", 0.0)
        qty = trade.get("qty", 1)
        if qty < 1:
            qty = 1

        # Convert P&L to return: pnl / (entry_price * qty)
        if entry_price > 0:
            ret = pnl / (entry_price * qty)
        else:
            ret = 0.0

        if strat not in strategy_returns:
            strategy_returns[strat] = []
        strategy_returns[strat].append(ret)

    # Run persistence check for each strategy
    results: Dict[str, Dict[str, Any]] = {}
    decaying_count = 0
    dead_count = 0

    for strat, returns in sorted(strategy_returns.items()):
        result = check_alpha_persistence(strat, returns)
        results[strat] = result

        if result["verdict"] == "DECAYING" and result["decay_rate"] > 0.5:
            decaying_count += 1
            log.warning(
                "ALPHA_DECAY_FLAG: %s decay_rate=%.3f — flagged for self-reflection",
                strat, result["decay_rate"],
            )
        elif result["verdict"] == "DEAD":
            dead_count += 1
            log.warning("ALPHA_DEAD_FLAG: %s — no edge in recent window", strat)

    log.info(
        "ALPHA_PERSISTENCE_SUMMARY: %d strategies checked, %d decaying (>0.5), %d dead",
        len(results), decaying_count, dead_count,
    )

    return results
