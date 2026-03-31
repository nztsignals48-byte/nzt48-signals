"""Granger Causality Testing for AEGIS V2 — Book 48.

Tests whether candidate variables Granger-cause targets.
Stationarity checked via ADF. Bonferroni correction applied.

Three core hypotheses tested per instrument:
  1. Net order flow → log returns (1-min, Kyle 1985)
  2. VIX change → overnight gap (1-day, VIX-ETP mechanism)
  3. Volume ratio → realized volatility (5-min, Clark 1973)

Nightly usage (step 5.44):
    from python_brain.causal.granger_causality import (
        GrangerResult, run_granger_test, run_nightly_granger,
    )
    results = run_nightly_granger()

Design:
  - Stationarity: ADF pre-test, auto-difference if needed
  - Lag selection: BIC over 1..max_lag
  - Correction: Bonferroni across all tests
  - Output: JSON report to /app/data/claude/reviews/granger_{date}.json
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("granger_causality")


@dataclass
class GrangerResult:
    """Result of a single Granger causality test."""
    cause_var: str
    effect_var: str
    instrument: str
    optimal_lag: int
    f_statistic: float
    p_value: float
    p_value_adjusted: float  # Bonferroni-corrected
    is_significant: bool
    direction: str           # "positive" or "negative"
    r2_improvement: float    # R² gain from adding cause variable
    adf_cause_pvalue: float
    adf_effect_pvalue: float
    n_observations: int


def test_stationarity(series: np.ndarray, sig: float = 0.05) -> Tuple[bool, float, int]:
    """Test stationarity via ADF.

    Returns: (is_stationary, adf_pvalue, differencing_needed)
    """
    from statsmodels.tsa.stattools import adfuller

    clean = series[~np.isnan(series)]
    if len(clean) < 20:
        return False, 1.0, 0

    result = adfuller(clean, autolag="BIC")
    if result[1] < sig:
        return True, float(result[1]), 0

    diff = np.diff(clean)
    if len(diff) < 20:
        return False, float(result[1]), 1

    result_d = adfuller(diff, autolag="BIC")
    if result_d[1] < sig:
        return False, float(result[1]), 1

    return False, float(result[1]), 2


def _apply_differencing(series: np.ndarray, n_diff: int) -> np.ndarray:
    """Apply n rounds of differencing."""
    s = series.copy()
    for _ in range(n_diff):
        s = np.diff(s)
    return s


def select_optimal_lag(
    cause: np.ndarray,
    effect: np.ndarray,
    max_lag: int = 20,
) -> int:
    """BIC-selected optimal lag for Granger test."""
    from statsmodels.tsa.stattools import grangercausalitytests
    import pandas as pd

    data = pd.DataFrame({"effect": effect, "cause": cause}).dropna()
    n = len(data)
    if n < 30:
        return 1

    best_bic = np.inf
    best_lag = 1

    for lag in range(1, min(max_lag + 1, n // 5)):
        try:
            result = grangercausalitytests(data, maxlag=[lag], verbose=False)
            ols_result = result[lag][1][0]  # Unrestricted OLS
            ssr = ols_result.ssr
            k = 2 * lag + 1
            obs = n - lag
            bic = obs * np.log(ssr / obs) + k * np.log(obs)
            if bic < best_bic:
                best_bic = bic
                best_lag = lag
        except Exception:
            continue

    return best_lag


def run_granger_test(
    cause: np.ndarray,
    effect: np.ndarray,
    cause_name: str,
    effect_name: str,
    instrument: str,
    max_lag: int = 20,
    significance: float = 0.05,
    n_total_tests: int = 1,
) -> Optional[GrangerResult]:
    """Full Granger test with stationarity checks and Bonferroni correction.

    Args:
        cause: Candidate causal series
        effect: Target series
        cause_name: Label for cause variable
        effect_name: Label for effect variable
        instrument: Instrument ticker
        max_lag: Maximum lag to test
        significance: Significance level
        n_total_tests: Total number of tests for Bonferroni correction

    Returns: GrangerResult or None if test cannot be performed
    """
    from statsmodels.tsa.stattools import grangercausalitytests
    import pandas as pd

    # Stationarity pre-tests
    _, cause_adf_p, c_diff = test_stationarity(cause)
    _, effect_adf_p, e_diff = test_stationarity(effect)

    c_adj = _apply_differencing(cause, c_diff)
    e_adj = _apply_differencing(effect, e_diff)

    # Align lengths after differencing
    min_len = min(len(c_adj), len(e_adj))
    c_adj = c_adj[-min_len:]
    e_adj = e_adj[-min_len:]

    # Remove NaN/Inf
    mask = np.isfinite(c_adj) & np.isfinite(e_adj)
    c_adj = c_adj[mask]
    e_adj = e_adj[mask]

    if len(c_adj) < 50:
        log.debug("Granger: insufficient observations (%d) for %s→%s on %s",
                  len(c_adj), cause_name, effect_name, instrument)
        return None

    data = pd.DataFrame({"effect": e_adj, "cause": c_adj})
    optimal_lag = select_optimal_lag(c_adj, e_adj, max_lag)

    try:
        result = grangercausalitytests(data, maxlag=[optimal_lag], verbose=False)

        # Extract F-test results
        f_stat = result[optimal_lag][0]["params_ftest"][0]
        p_val = result[optimal_lag][0]["params_ftest"][1]
        p_adj = min(p_val * n_total_tests, 1.0)  # Bonferroni

        # R² improvement
        ols_unrestricted = result[optimal_lag][1][0]
        ols_restricted = result[optimal_lag][1][1]
        ssr_u = ols_unrestricted.ssr
        ssr_r = ols_restricted.ssr
        r2_imp = 1.0 - (ssr_u / ssr_r) if ssr_r > 0 else 0.0

        # Direction of causal effect
        params = ols_unrestricted.params
        cause_coeffs = params[optimal_lag + 1: 2 * optimal_lag + 1]
        direction = "positive" if np.sum(cause_coeffs) > 0 else "negative"

        return GrangerResult(
            cause_var=cause_name,
            effect_var=effect_name,
            instrument=instrument,
            optimal_lag=optimal_lag,
            f_statistic=float(f_stat),
            p_value=float(p_val),
            p_value_adjusted=float(p_adj),
            is_significant=p_adj < significance,
            direction=direction,
            r2_improvement=float(r2_imp),
            adf_cause_pvalue=float(cause_adf_p),
            adf_effect_pvalue=float(effect_adf_p),
            n_observations=len(c_adj),
        )
    except Exception as e:
        log.debug("Granger test failed for %s→%s on %s: %s",
                  cause_name, effect_name, instrument, e)
        return None


# ---------------------------------------------------------------------------
# Standard test battery — the 3 hypotheses from Book 48
# ---------------------------------------------------------------------------

CAUSAL_PAIRS: List[Tuple[str, str, str]] = [
    ("net_order_flow", "log_returns", "1min"),
    ("vix_change", "overnight_gap", "1d"),
    ("volume_ratio", "realized_vol_5min", "5min"),
]


def run_full_battery(
    instruments: List[str],
    data_loader,
    max_lag: int = 20,
    sig: float = 0.05,
) -> List[GrangerResult]:
    """Run Granger tests across all instruments and variable pairs.

    Args:
        instruments: List of ticker symbols
        data_loader: Object with .load(instrument, variable, freq) -> np.ndarray
        max_lag: Maximum lag to test
        sig: Significance threshold

    Returns: List of significant GrangerResults, sorted by p-value
    """
    n_tests = len(instruments) * len(CAUSAL_PAIRS)
    results: List[GrangerResult] = []

    for inst in instruments:
        for cause_name, effect_name, freq in CAUSAL_PAIRS:
            try:
                c = data_loader.load(inst, cause_name, freq)
                e = data_loader.load(inst, effect_name, freq)
            except Exception:
                continue

            if c is None or e is None or len(c) < 50 or len(e) < 50:
                continue

            r = run_granger_test(
                c, e, cause_name, effect_name, inst,
                max_lag, sig, n_tests,
            )
            if r is not None:
                results.append(r)

    results.sort(key=lambda r: r.p_value_adjusted)
    return results


# ---------------------------------------------------------------------------
# Nightly entry point
# ---------------------------------------------------------------------------

def run_nightly_granger() -> Dict:
    """Nightly Granger causality scan.

    Loads WAL-derived return/volume series, runs all three hypothesis tests
    per instrument, writes report to data/claude/reviews/.

    Returns: Summary dict for nightly_v6 recommendations.
    """
    import os

    data_dir = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
    reports_dir = data_dir / "claude" / "reviews"
    reports_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Load cached WAL series if available
    wal_series_path = data_dir / "wal_derived_series.json"
    if not wal_series_path.exists():
        log.info("Granger: no WAL-derived series file found — skipping")
        return {"status": "skipped", "reason": "no_wal_series"}

    try:
        with open(wal_series_path) as f:
            wal_data = json.load(f)
    except Exception as e:
        log.warning("Granger: failed to load WAL series: %s", e)
        return {"status": "error", "reason": str(e)}

    instruments = list(wal_data.keys())
    if not instruments:
        return {"status": "skipped", "reason": "no_instruments"}

    class WalDataLoader:
        """Adapter from WAL JSON to numpy arrays."""
        def __init__(self, data: Dict):
            self._data = data

        def load(self, instrument: str, variable: str, freq: str) -> Optional[np.ndarray]:
            inst_data = self._data.get(instrument, {})
            series = inst_data.get(f"{variable}_{freq}", inst_data.get(variable))
            if series is None:
                return None
            arr = np.array(series, dtype=float)
            return arr[np.isfinite(arr)]

    loader = WalDataLoader(wal_data)
    all_results = run_full_battery(instruments, loader)

    significant = [r for r in all_results if r.is_significant]

    # Write detailed report
    report = {
        "date": today,
        "total_tests": len(instruments) * len(CAUSAL_PAIRS),
        "total_results": len(all_results),
        "significant_results": len(significant),
        "bonferroni_threshold": 0.05 / max(len(instruments) * len(CAUSAL_PAIRS), 1),
        "results": [asdict(r) for r in all_results],
    }

    report_path = reports_dir / f"granger_{today}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    log.info("Granger report: %d tests, %d significant → %s",
             len(all_results), len(significant), report_path)

    # Summary for nightly recommendations
    summary = {
        "status": "complete",
        "total_tests": report["total_tests"],
        "significant": len(significant),
        "top_results": [
            {
                "cause": r.cause_var, "effect": r.effect_var,
                "instrument": r.instrument, "p_adj": round(r.p_value_adjusted, 6),
                "direction": r.direction, "lag": r.optimal_lag,
            }
            for r in significant[:5]
        ],
        "report_path": str(report_path),
    }

    return summary
