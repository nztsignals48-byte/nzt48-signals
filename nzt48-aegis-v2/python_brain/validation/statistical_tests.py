"""Book 6: Statistical Validation Tests for Live Strategy Assessment.

Provides 7 key statistical tests that can validate strategies using live P&L
data from the Compounding Machine, without requiring a full backtesting engine.

Tests:
  T1: Walk-Forward Sharpe Ratio (annualized OOS)
  T2: Walk-Forward Profit Factor (OOS PF)
  T3: Maximum OOS Drawdown
  T4: CPCV p-value (fraction with negative OOS SR)
  T5: Deflated Sharpe Ratio (DSR) — delegates to strategy_gates.py
  T6: Walk-Forward Efficiency (WFE)
  T7: Probability of Backtest Overfitting (PBO)
  + Monte Carlo permutation p-value
  + Block bootstrap confidence intervals
  + Minimum Track Record Length (MinTRL)

Usage (nightly):
    from python_brain.validation.statistical_tests import run_strategy_validation
    report = run_strategy_validation()
"""

from __future__ import annotations

import json
import math
import os
import random
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

DATA_DIR = os.environ.get("AEGIS_DATA_DIR", "/app/data")
PNL_FILE = os.path.join(DATA_DIR, "strategy_pnl_history.json")
VALIDATION_FILE = os.path.join(DATA_DIR, "strategy_validation.json")
TRIALS_FILE = os.path.join(DATA_DIR, "backtest_results", "trials_register.ndjson")

EULER_MASCHERONI = 0.5772156649
RF_DAILY = 0.04 / 252  # UK gilt ~4% annualized


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """Standard normal CDF using math.erfc (no scipy needed)."""
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _norm_ppf(p: float) -> float:
    """Approximate standard normal PPF (inverse CDF). Beasley-Springer-Moro."""
    if p <= 0:
        return -8.0
    if p >= 1:
        return 8.0
    if p == 0.5:
        return 0.0

    # Rational approximation (Abramowitz & Stegun 26.2.23)
    if p < 0.5:
        t = math.sqrt(-2.0 * math.log(p))
    else:
        t = math.sqrt(-2.0 * math.log(1.0 - p))

    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    result = t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t * t * t)

    if p < 0.5:
        return -result
    return result


# ─── Core Statistical Tests ──────────────────────────────────────────────────

def sharpe_ratio(returns: List[float], rf_daily: float = RF_DAILY) -> float:
    """Annualized Sharpe Ratio from daily returns."""
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    mean_r = sum(returns) / n
    var_r = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    if var_r <= 0:
        return 0.0
    std_r = math.sqrt(var_r)
    return (mean_r - rf_daily) / std_r * math.sqrt(252)


def profit_factor(trade_pnls: List[float]) -> float:
    """Profit Factor = sum(wins) / abs(sum(losses))."""
    wins = sum(p for p in trade_pnls if p > 0)
    losses = abs(sum(p for p in trade_pnls if p < 0))
    if losses <= 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


def max_drawdown(equity_curve: List[float]) -> float:
    """Maximum drawdown as a fraction (0 to 1)."""
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def deflated_sharpe_ratio(
    observed_sr: float,
    num_trials: int,
    skewness: float,
    kurtosis: float,
    track_record_length: int,
) -> float:
    """DSR per Bailey & Lopez de Prado (2014) with Euler-Mascheroni correction.

    Returns probability that observed SR > 0 after correcting for:
    - Multiple testing (num_trials)
    - Non-normal returns (skewness, kurtosis)
    - Sample size (track_record_length)
    """
    if track_record_length < 2 or observed_sr <= 0:
        return 0.0

    T = track_record_length
    N = max(num_trials, 1)

    # Expected max SR under null
    if N > 1:
        e_max_sr = (
            (1 - EULER_MASCHERONI) * _norm_ppf(1 - 1.0 / N)
            + EULER_MASCHERONI * _norm_ppf(1 - 1.0 / (N * math.e))
        )
    else:
        e_max_sr = 0.0

    # SE of SR with non-normality correction
    excess_kurt = kurtosis - 3.0
    se_denom = max(T - 1, 1)
    se_num = 1 - skewness * observed_sr + excess_kurt / 4.0 * observed_sr ** 2
    se_sr = math.sqrt(max(se_num, 1e-10) / se_denom)

    if se_sr <= 0:
        return 0.0

    return _norm_cdf((observed_sr - e_max_sr) / se_sr)


def min_track_record_length(
    observed_sr: float,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    alpha: float = 0.05,
    sr_benchmark: float = 0.0,
) -> float:
    """MinTRL: minimum observations needed to trust the observed SR.

    Returns number of annual observations (multiply by 252 for daily).
    """
    if observed_sr <= sr_benchmark:
        return float("inf")

    z_alpha = _norm_ppf(1 - alpha)
    excess_kurt = kurtosis - 3.0
    sr_diff = observed_sr - sr_benchmark

    factor = 1 - skewness * observed_sr + excess_kurt / 4.0 * observed_sr ** 2
    mintrl = 1 + factor * (z_alpha / sr_diff) ** 2
    return mintrl


def walk_forward_efficiency(is_returns: List[float], oos_returns: List[float]) -> float:
    """WFE = mean(OOS_return / IS_return) across walk-forward windows.

    For single-window: WFE = OOS_return / IS_return.
    """
    if not is_returns or not oos_returns:
        return 0.0

    ratios = []
    for is_r, oos_r in zip(is_returns, oos_returns):
        if abs(is_r) > 1e-10:
            ratios.append(oos_r / is_r)
    if not ratios:
        return 0.0
    return sum(ratios) / len(ratios)


def monte_carlo_pvalue(
    trade_returns: List[float],
    n_permutations: int = 5000,
    seed: int = 42,
) -> float:
    """Monte Carlo permutation test p-value.

    Null: entry timing has no predictive power.
    Shuffles trade returns, recomputes Sharpe, counts how often random >= observed.
    Uses block shuffle (block_size = sqrt(N)) to preserve autocorrelation.
    """
    if len(trade_returns) < 10:
        return 1.0

    rng = random.Random(seed)
    observed = sharpe_ratio(trade_returns)
    if observed <= 0:
        return 1.0

    n = len(trade_returns)
    block_size = max(2, int(math.sqrt(n)))
    count_exceed = 0

    for _ in range(n_permutations):
        # Block shuffle: break into blocks, shuffle blocks, flatten
        blocks = [trade_returns[i:i + block_size] for i in range(0, n, block_size)]
        rng.shuffle(blocks)
        shuffled = [r for block in blocks for r in block][:n]
        if sharpe_ratio(shuffled) >= observed:
            count_exceed += 1

    return count_exceed / n_permutations


def bootstrap_ci(
    values: List[float],
    stat_fn=None,
    n_bootstrap: int = 5000,
    alpha: float = 0.05,
    seed: int = 42,
) -> Tuple[float, float, float]:
    """Block bootstrap confidence interval.

    Returns (point_estimate, ci_lower, ci_upper).
    Default stat_fn is sharpe_ratio.
    """
    if stat_fn is None:
        stat_fn = sharpe_ratio

    if len(values) < 5:
        point = stat_fn(values) if values else 0.0
        return (point, point, point)

    rng = random.Random(seed)
    n = len(values)
    block_size = max(2, int(math.sqrt(n)))
    point = stat_fn(values)

    boot_stats = []
    for _ in range(n_bootstrap):
        # Stationary block bootstrap
        sample = []
        while len(sample) < n:
            start = rng.randint(0, n - 1)
            for j in range(block_size):
                if len(sample) >= n:
                    break
                sample.append(values[(start + j) % n])
        boot_stats.append(stat_fn(sample))

    boot_stats.sort()
    lo_idx = max(0, int(alpha / 2 * len(boot_stats)))
    hi_idx = min(len(boot_stats) - 1, int((1 - alpha / 2) * len(boot_stats)))
    return (point, boot_stats[lo_idx], boot_stats[hi_idx])


def cpcv_validation(
    daily_returns: List[float],
    n_groups: int = 6,
    k_test: int = 2,
    purge_bars: int = 20,
) -> Dict:
    """Combinatorial Purged Cross-Validation (simplified for live returns).

    Splits daily returns into n_groups, tests all C(n,k) combinations.
    Returns median OOS Sharpe, PBO, and fraction with negative OOS SR.
    """
    n = len(daily_returns)
    if n < n_groups * 10:
        return {"median_oos_sr": 0.0, "pbo": 1.0, "p_cpcv": 1.0, "n_splits": 0}

    group_size = n // n_groups
    groups = []
    for i in range(n_groups):
        start = i * group_size
        end = start + group_size if i < n_groups - 1 else n
        groups.append(daily_returns[start:end])

    # Generate all C(n,k) combinations
    from itertools import combinations
    test_combos = list(combinations(range(n_groups), k_test))

    oos_sharpes = []
    is_sharpes = []

    for test_indices in test_combos:
        train_indices = [i for i in range(n_groups) if i not in test_indices]
        # Purge: remove `purge_bars` from edges of training groups adjacent to test
        train_returns = []
        for ti in train_indices:
            g = groups[ti]
            # Simple purge: trim edges if adjacent to test group
            trim = purge_bars if (ti + 1 in test_indices or ti - 1 in test_indices) else 0
            if trim < len(g):
                train_returns.extend(g[trim:len(g) - trim] if trim > 0 else g)
            else:
                train_returns.extend(g)

        test_returns = []
        for ti in test_indices:
            test_returns.extend(groups[ti])

        oos_sr = sharpe_ratio(test_returns)
        is_sr = sharpe_ratio(train_returns)
        oos_sharpes.append(oos_sr)
        is_sharpes.append(is_sr)

    if not oos_sharpes:
        return {"median_oos_sr": 0.0, "pbo": 1.0, "p_cpcv": 1.0, "n_splits": 0}

    oos_sharpes.sort()
    median_idx = len(oos_sharpes) // 2
    median_oos_sr = oos_sharpes[median_idx]

    # p_CPCV = fraction with negative OOS SR
    neg_count = sum(1 for s in oos_sharpes if s < 0)
    p_cpcv = neg_count / len(oos_sharpes)

    # PBO = fraction where IS-optimal ranks below median OOS
    # Simplified: fraction where IS rank > OOS rank
    n_splits = len(oos_sharpes)
    rank_inversions = 0
    for i in range(n_splits):
        is_rank = sorted(range(n_splits), key=lambda j: is_sharpes[j], reverse=True).index(i)
        oos_rank = sorted(range(n_splits), key=lambda j: oos_sharpes[j], reverse=True).index(i)
        if is_rank < n_splits // 2 and oos_rank >= n_splits // 2:
            rank_inversions += 1
    pbo = rank_inversions / max(n_splits // 2, 1)

    return {
        "median_oos_sr": round(median_oos_sr, 4),
        "pbo": round(min(pbo, 1.0), 4),
        "p_cpcv": round(p_cpcv, 4),
        "n_splits": n_splits,
        "oos_sharpes": [round(s, 4) for s in oos_sharpes],
    }


# ─── Gate Thresholds ─────────────────────────────────────────────────────────

@dataclass
class GateThresholds:
    """Book 6 pass/fail criteria."""
    min_sharpe: float = 0.5
    min_profit_factor: float = 1.2
    max_drawdown: float = 0.15
    max_p_cpcv: float = 0.05
    min_dsr: float = 0.95
    min_wfe: float = 0.40
    max_pbo: float = 0.40
    min_trades: int = 200
    mc_alpha: float = 0.05  # Bonferroni-adjusted per strategy


@dataclass
class ValidationResult:
    strategy: str
    n_trades: int
    sharpe: float
    sharpe_ci: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    profit_factor_val: float = 0.0
    max_dd: float = 0.0
    dsr: float = 0.0
    mc_pvalue: float = 1.0
    cpcv: Dict = field(default_factory=dict)
    mintrl_years: float = 0.0
    passed: bool = False
    failed_gates: List[str] = field(default_factory=list)
    status: str = "INSUFFICIENT_DATA"


# ─── Main Runner ─────────────────────────────────────────────────────────────

def _load_strategy_returns() -> Dict[str, List[float]]:
    """Load per-strategy trade returns from Compounding Machine state."""
    if not os.path.exists(PNL_FILE):
        return {}
    try:
        with open(PNL_FILE) as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if isinstance(v, list) and len(v) > 0}
    except Exception:
        return {}


def _count_trials() -> int:
    """Count total strategy variants ever tested (from trials register)."""
    if not os.path.exists(TRIALS_FILE):
        return 1
    try:
        count = 0
        with open(TRIALS_FILE) as f:
            for line in f:
                if line.strip():
                    count += 1
        return max(count, 1)
    except Exception:
        return 1


def validate_strategy(
    returns: List[float],
    strategy_name: str = "unknown",
    n_trials: int = 1,
    thresholds: Optional[GateThresholds] = None,
) -> ValidationResult:
    """Run all 7 statistical tests on a single strategy's returns."""
    if thresholds is None:
        thresholds = GateThresholds()

    result = ValidationResult(strategy=strategy_name, n_trades=len(returns))

    if len(returns) < 30:
        result.status = "INSUFFICIENT_DATA"
        return result

    # T1: Sharpe + Bootstrap CI
    sr_point, sr_lo, sr_hi = bootstrap_ci(returns, sharpe_ratio, n_bootstrap=2000)
    result.sharpe = round(sr_point, 4)
    result.sharpe_ci = (round(sr_lo, 4), round(sr_point, 4), round(sr_hi, 4))

    # T2: Profit Factor
    result.profit_factor_val = round(profit_factor(returns), 4)

    # T3: Max Drawdown (from cumulative returns)
    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1 + r))
    result.max_dd = round(max_drawdown(equity), 4)

    # Return distribution stats
    n = len(returns)
    mean_r = sum(returns) / n
    var_r = sum((r - mean_r) ** 2 for r in returns) / max(n - 1, 1)
    std_r = math.sqrt(var_r) if var_r > 0 else 1e-10
    skew = sum((r - mean_r) ** 3 for r in returns) / (n * std_r ** 3) if std_r > 0 else 0.0
    kurt = sum((r - mean_r) ** 4 for r in returns) / (n * std_r ** 4) if std_r > 0 else 3.0

    # T4: CPCV
    result.cpcv = cpcv_validation(returns)

    # T5: DSR
    result.dsr = round(deflated_sharpe_ratio(
        observed_sr=result.sharpe,
        num_trials=n_trials,
        skewness=skew,
        kurtosis=kurt,
        track_record_length=n,
    ), 4)

    # T6: WFE (split returns into halves: IS=first half, OOS=second half)
    mid = len(returns) // 2
    is_sr = sharpe_ratio(returns[:mid])
    oos_sr = sharpe_ratio(returns[mid:])
    wfe = oos_sr / is_sr if abs(is_sr) > 1e-10 else 0.0
    result.cpcv["wfe"] = round(wfe, 4)

    # T7: PBO (from CPCV)
    # Already computed in cpcv_validation

    # Monte Carlo p-value
    result.mc_pvalue = round(monte_carlo_pvalue(returns, n_permutations=2000), 4)

    # MinTRL
    result.mintrl_years = round(min_track_record_length(result.sharpe, skew, kurt), 2)

    # Gate checks
    failed = []
    if result.sharpe < thresholds.min_sharpe:
        failed.append(f"T1: SR={result.sharpe} < {thresholds.min_sharpe}")
    if sr_lo < thresholds.min_sharpe:
        failed.append(f"T1-CI: SR_lower={sr_lo} < {thresholds.min_sharpe}")
    if result.profit_factor_val < thresholds.min_profit_factor:
        failed.append(f"T2: PF={result.profit_factor_val} < {thresholds.min_profit_factor}")
    if result.max_dd > thresholds.max_drawdown:
        failed.append(f"T3: MaxDD={result.max_dd} > {thresholds.max_drawdown}")
    p_cpcv = result.cpcv.get("p_cpcv", 1.0)
    if p_cpcv > thresholds.max_p_cpcv:
        failed.append(f"T4: p_CPCV={p_cpcv} > {thresholds.max_p_cpcv}")
    if result.dsr < thresholds.min_dsr:
        failed.append(f"T5: DSR={result.dsr} < {thresholds.min_dsr}")
    if wfe < thresholds.min_wfe:
        failed.append(f"T6: WFE={wfe:.2f} < {thresholds.min_wfe}")
    pbo = result.cpcv.get("pbo", 1.0)
    if pbo > thresholds.max_pbo:
        failed.append(f"T7: PBO={pbo} > {thresholds.max_pbo}")
    if result.mc_pvalue > thresholds.mc_alpha:
        failed.append(f"MC: p={result.mc_pvalue} > {thresholds.mc_alpha}")

    result.failed_gates = failed
    result.passed = len(failed) == 0
    result.status = "PASSED" if result.passed else "FAILED"

    return result


def run_strategy_validation() -> Dict:
    """Nightly runner: validate all strategies with sufficient data."""
    strategy_returns = _load_strategy_returns()
    n_trials = _count_trials()
    n_strategies = max(len(strategy_returns), 1)
    # Bonferroni correction for multiple strategies
    thresholds = GateThresholds(mc_alpha=0.05 / n_strategies)

    results = {}
    for name, returns in strategy_returns.items():
        result = validate_strategy(returns, name, n_trials, thresholds)
        results[name] = asdict(result)

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_strategies": len(results),
        "n_trials": n_trials,
        "strategies": results,
        "summary": {
            "passed": sum(1 for r in results.values() if r["passed"]),
            "failed": sum(1 for r in results.values() if not r["passed"] and r["status"] != "INSUFFICIENT_DATA"),
            "insufficient_data": sum(1 for r in results.values() if r["status"] == "INSUFFICIENT_DATA"),
        },
    }

    # Persist
    try:
        os.makedirs(os.path.dirname(VALIDATION_FILE), exist_ok=True)
        with open(VALIDATION_FILE, "w") as f:
            json.dump(report, f, indent=2, default=str)
    except Exception:
        pass

    return report


if __name__ == "__main__":
    report = run_strategy_validation()
    print(f"Validation: {report['summary']}")
    for name, r in report.get("strategies", {}).items():
        status = r.get("status", "?")
        sr = r.get("sharpe", 0)
        n = r.get("n_trades", 0)
        print(f"  {name}: {status} (SR={sr}, N={n})")
