"""synthetic/data_generator.py — Book 34: Synthetic Data Augmentation.

Generators: Merton jump-diffusion, HMM regime-switching, GARCH(1,1)-FHB.
Validation suite: KS test, Wasserstein distance, moment matching,
autocorrelation, volatility clustering, tail risk.

Mix: 40% regime-switching, 30% GARCH-FHB, 20% Merton, 10% GAN (when trained).
"""

import json
import logging
import math
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)


# ── GBM Baseline ───────────────────────────────────────────────────────

def calibrate_gbm(prices: np.ndarray, dt: float = 1 / 252) -> Dict:
    """Calibrate Geometric Brownian Motion from price series."""
    log_returns = np.diff(np.log(prices))
    sigma = float(np.std(log_returns) / np.sqrt(dt))
    mu = float(np.mean(log_returns) / dt + 0.5 * sigma ** 2)
    return {"mu": mu, "sigma": sigma, "S0": float(prices[-1])}


def generate_gbm_paths(S0: float, mu: float, sigma: float,
                       T: float, dt: float, n_paths: int,
                       seed: int = 42) -> np.ndarray:
    """Generate GBM price paths. Returns (n_steps+1, n_paths)."""
    rng = np.random.default_rng(seed)
    n_steps = int(T / dt)
    Z = rng.standard_normal((n_steps, n_paths))
    log_inc = (mu - 0.5 * sigma ** 2) * dt + sigma * np.sqrt(dt) * Z
    log_prices = np.zeros((n_steps + 1, n_paths))
    log_prices[0, :] = np.log(S0)
    log_prices[1:, :] = np.log(S0) + np.cumsum(log_inc, axis=0)
    return np.exp(log_prices)


# ── Merton Jump-Diffusion ─────────────────────────────────────────────

def calibrate_merton(prices: np.ndarray, dt: float = 1 / 252,
                     jump_threshold_sigma: float = 3.0) -> Dict:
    """Calibrate Merton jump-diffusion from historical prices."""
    log_returns = np.diff(np.log(prices))
    sigma_est = np.std(log_returns)
    is_jump = np.abs(log_returns) > jump_threshold_sigma * sigma_est

    jump_returns = log_returns[is_jump]
    normal_returns = log_returns[~is_jump]

    n_jumps = int(np.sum(is_jump))
    T_total = len(log_returns) * dt
    lam = n_jumps / T_total if T_total > 0 else 5.0

    mu_J = float(np.mean(jump_returns)) if n_jumps > 0 else 0.0
    sigma_J = float(np.std(jump_returns)) if n_jumps > 1 else 0.01

    sigma = float(np.std(normal_returns) / np.sqrt(dt)) if len(normal_returns) > 1 else 0.20
    mu = float(np.mean(normal_returns) / dt + 0.5 * sigma ** 2) if len(normal_returns) > 1 else 0.08

    return {
        "mu": mu, "sigma": sigma,
        "lambda": lam, "mu_J": mu_J, "sigma_J": sigma_J,
        "S0": float(prices[-1]),
    }


def generate_merton_paths(S0: float, mu: float, sigma: float,
                          lam: float, mu_J: float, sigma_J: float,
                          T: float, dt: float, n_paths: int,
                          seed: int = 42) -> np.ndarray:
    """Generate Merton jump-diffusion price paths."""
    rng = np.random.default_rng(seed)
    n_steps = int(T / dt)
    k = np.exp(mu_J + 0.5 * sigma_J ** 2) - 1

    Z = rng.standard_normal((n_steps, n_paths))
    diffusion = (mu - lam * k - 0.5 * sigma ** 2) * dt + sigma * np.sqrt(dt) * Z

    # Jumps
    N_jumps = rng.poisson(lam * dt, (n_steps, n_paths))
    J = np.zeros((n_steps, n_paths))
    for i in range(n_steps):
        for j in range(n_paths):
            if N_jumps[i, j] > 0:
                J[i, j] = np.sum(rng.normal(mu_J, sigma_J, N_jumps[i, j]))

    log_inc = diffusion + J
    log_prices = np.zeros((n_steps + 1, n_paths))
    log_prices[0, :] = np.log(S0)
    log_prices[1:, :] = np.log(S0) + np.cumsum(log_inc, axis=0)
    return np.exp(log_prices)


# ── Regime-Switching (HMM) ─────────────────────────────────────────────

def calibrate_regime_model(returns: np.ndarray, n_regimes: int = 3,
                           n_iter: int = 200, seed: int = 42) -> Dict:
    """Calibrate Gaussian HMM regime-switching model.

    Requires hmmlearn. Falls back to manual 3-regime estimation if unavailable.
    """
    try:
        from hmmlearn import hmm as hmmlearn_hmm
        model = hmmlearn_hmm.GaussianHMM(
            n_components=n_regimes, covariance_type="full",
            n_iter=n_iter, random_state=seed)
        X = returns.reshape(-1, 1)
        model.fit(X)

        # Sort by volatility ascending
        vols = np.sqrt(model.covars_.flatten())
        order = np.argsort(vols)
        means = model.means_.flatten()[order].tolist()
        sigmas = vols[order].tolist()
        transmat = model.transmat_[order][:, order].tolist()
        startprob = model.startprob_[order].tolist()
    except ImportError:
        # Manual 3-regime estimation from percentile splits
        log.info("hmmlearn not available — using percentile-based regime estimation")
        abs_r = np.abs(returns)
        p33 = np.percentile(abs_r, 33)
        p66 = np.percentile(abs_r, 66)

        low_mask = abs_r <= p33
        mid_mask = (abs_r > p33) & (abs_r <= p66)
        high_mask = abs_r > p66

        means = [float(np.mean(returns[low_mask])),
                 float(np.mean(returns[mid_mask])),
                 float(np.mean(returns[high_mask]))]
        sigmas = [float(np.std(returns[low_mask])),
                  float(np.std(returns[mid_mask])),
                  float(np.std(returns[high_mask]))]
        transmat = [[0.98, 0.015, 0.005],
                    [0.02, 0.95, 0.03],
                    [0.01, 0.04, 0.95]]
        startprob = [0.6, 0.3, 0.1]

    return {
        "n_regimes": n_regimes,
        "means": means, "sigmas": sigmas,
        "transmat": transmat, "startprob": startprob,
    }


def generate_regime_paths(S0: float, params: Dict,
                          T: float, dt: float, n_paths: int,
                          seed: int = 42) -> Tuple[np.ndarray, np.ndarray]:
    """Generate regime-switching price paths.

    Returns (prices: (n_steps+1, n_paths), regimes: (n_steps, n_paths)).
    """
    rng = np.random.default_rng(seed)
    n_steps = int(T / dt)
    n_regimes = params["n_regimes"]
    means = np.array(params["means"])
    sigmas = np.array(params["sigmas"])
    transmat = np.array(params["transmat"])
    startprob = np.array(params["startprob"])

    regimes = np.zeros((n_steps, n_paths), dtype=int)
    log_returns = np.zeros((n_steps, n_paths))

    regimes[0, :] = rng.choice(n_regimes, size=n_paths, p=startprob)

    for t in range(n_steps):
        if t > 0:
            for j in range(n_paths):
                regimes[t, j] = rng.choice(n_regimes, p=transmat[regimes[t - 1, j]])

        for r in range(n_regimes):
            mask = regimes[t, :] == r
            n_in = int(np.sum(mask))
            if n_in > 0:
                log_returns[t, mask] = means[r] * dt + sigmas[r] * np.sqrt(dt) * rng.standard_normal(n_in)

    log_prices = np.zeros((n_steps + 1, n_paths))
    log_prices[0, :] = np.log(S0)
    log_prices[1:, :] = np.log(S0) + np.cumsum(log_returns, axis=0)
    return np.exp(log_prices), regimes


# ── GARCH(1,1) with Filtered Historical Bootstrap ─────────────────────

def calibrate_garch(returns: np.ndarray) -> Dict:
    """Calibrate GARCH(1,1) model.

    Requires arch package. Falls back to moment estimator if unavailable.
    """
    try:
        from arch import arch_model
        model = arch_model(returns * 100, vol="Garch", p=1, q=1, dist="t")
        result = model.fit(disp="off")
        params = result.params

        return {
            "mu": float(params["mu"]) / 100,
            "omega": float(params["omega"]) / 10000,
            "alpha": float(params["alpha[1]"]),
            "beta": float(params["beta[1]"]),
            "nu": float(params.get("nu", 5.0)),
            "conditional_vol": float(result.conditional_volatility.iloc[-1]) / 100,
        }
    except ImportError:
        # Moment-based estimation
        log.info("arch package not available — using moment estimation")
        var = np.var(returns)
        return {
            "mu": float(np.mean(returns)),
            "omega": var * 0.02,
            "alpha": 0.08,
            "beta": 0.90,
            "nu": 5.0,
            "conditional_vol": float(np.std(returns)),
        }


def generate_garch_paths(S0: float, params: Dict,
                         n_steps: int, n_paths: int,
                         seed: int = 42) -> np.ndarray:
    """Generate GARCH(1,1) price paths with t-distributed innovations."""
    rng = np.random.default_rng(seed)

    mu = params["mu"]
    omega = params["omega"]
    alpha = params["alpha"]
    beta = params["beta"]
    nu = params.get("nu", 5.0)
    sigma2 = np.full(n_paths, params["conditional_vol"] ** 2)

    log_prices = np.zeros((n_steps + 1, n_paths))
    log_prices[0, :] = np.log(S0)

    for t in range(n_steps):
        z = rng.standard_t(nu, size=n_paths)
        z = z / np.sqrt(nu / (nu - 2)) if nu > 2 else z
        r = mu + np.sqrt(sigma2) * z
        log_prices[t + 1, :] = log_prices[t, :] + r
        sigma2 = omega + alpha * r ** 2 + beta * sigma2

    return np.exp(log_prices)


def garch_filtered_bootstrap(returns: np.ndarray, S0: float,
                             n_steps: int, n_paths: int,
                             seed: int = 42) -> np.ndarray:
    """GARCH filtered historical bootstrap — preserves empirical tail distribution."""
    try:
        from arch import arch_model
        model = arch_model(returns * 100, vol="Garch", p=1, q=1, dist="t")
        result = model.fit(disp="off")

        resid = (result.resid / result.conditional_volatility).dropna().values
        params = result.params
        omega = params["omega"] / 10000
        alpha_val = params["alpha[1]"]
        beta_val = params["beta[1]"]
        mu_val = params["mu"] / 100
        sigma2_init = (result.conditional_volatility.iloc[-1] / 100) ** 2
    except ImportError:
        # Fallback: use standardised returns directly
        resid = (returns - np.mean(returns)) / (np.std(returns) + 1e-8)
        omega = np.var(returns) * 0.02
        alpha_val = 0.08
        beta_val = 0.90
        mu_val = np.mean(returns)
        sigma2_init = np.var(returns)

    rng = np.random.default_rng(seed)
    log_prices = np.zeros((n_steps + 1, n_paths))
    log_prices[0, :] = np.log(S0)
    sigma2 = np.full(n_paths, sigma2_init)

    for t in range(n_steps):
        idx = rng.integers(0, len(resid), size=n_paths)
        z = resid[idx]
        r = mu_val + np.sqrt(sigma2) * z
        log_prices[t + 1, :] = log_prices[t, :] + r
        sigma2 = omega + alpha_val * r ** 2 + beta_val * sigma2

    return np.exp(log_prices)


# ── Validation Suite ───────────────────────────────────────────────────

def validate_distribution(real: np.ndarray, synthetic: np.ndarray,
                          alpha: float = 0.05) -> Dict:
    """Compare real and synthetic return distributions."""
    try:
        from scipy.stats import ks_2samp, wasserstein_distance
    except ImportError:
        return {"status": "scipy_not_available"}

    ks_stat, ks_pval = ks_2samp(real, synthetic)
    w_dist = wasserstein_distance(real, synthetic)

    def _moments(r):
        return {
            "mean": float(np.mean(r)),
            "std": float(np.std(r)),
            "skew": float(np.mean(((r - np.mean(r)) / (np.std(r) + 1e-8)) ** 3)),
            "kurtosis": float(np.mean(((r - np.mean(r)) / (np.std(r) + 1e-8)) ** 4)),
        }

    real_m = _moments(real)
    synth_m = _moments(synthetic)

    return {
        "ks_statistic": float(ks_stat),
        "ks_p_value": float(ks_pval),
        "ks_pass": ks_pval > alpha,
        "wasserstein_distance": float(w_dist),
        "real_moments": real_m,
        "synthetic_moments": synth_m,
        "moment_errors": {
            k: abs(real_m[k] - synth_m[k]) / (abs(real_m[k]) + 1e-10)
            for k in real_m
        },
    }


def validate_temporal(real: np.ndarray, synthetic: np.ndarray,
                      max_lag: int = 50) -> Dict:
    """Validate autocorrelation structure."""
    def _acf(series, max_lag):
        n = len(series)
        mean = np.mean(series)
        var = np.var(series)
        if var < 1e-12:
            return np.zeros(max_lag + 1)
        acf = np.zeros(max_lag + 1)
        for lag in range(min(max_lag + 1, n)):
            acf[lag] = np.mean((series[:n - lag] - mean) * (series[lag:] - mean)) / var
        return acf

    results = {}
    for name, transform in [("returns", lambda x: x),
                            ("abs_returns", np.abs),
                            ("squared_returns", lambda x: x ** 2)]:
        real_acf = _acf(transform(real), max_lag)
        synth_acf = _acf(transform(synthetic), max_lag)
        results[name] = {
            "acf_mae": float(np.mean(np.abs(real_acf[1:] - synth_acf[1:]))),
        }

    # Vol clustering check
    vol_real = abs(_acf(np.abs(real), 10)[min(10, len(real) - 1)])
    vol_synth = abs(_acf(np.abs(synthetic), 10)[min(10, len(synthetic) - 1)])
    results["vol_clustering"] = {
        "real": float(vol_real),
        "synthetic": float(vol_synth),
        "both_present": vol_real > 0.03 and vol_synth > 0.03,
    }

    return results


def validate_tails(real: np.ndarray, synthetic: np.ndarray) -> Dict:
    """Validate tail behaviour: kurtosis, extreme events, drawdowns."""
    def _tail_metrics(r):
        std = np.std(r) + 1e-8
        kurt = float(np.mean(((r - np.mean(r)) / std) ** 4))
        pct_3sigma = float(np.mean(np.abs(r) > 3 * std) * 100)
        cum = np.cumsum(r)
        running_max = np.maximum.accumulate(cum)
        max_dd = float(np.min(cum - running_max))
        return {"kurtosis": kurt, "pct_beyond_3sigma": pct_3sigma,
                "max_drawdown": max_dd}

    real_t = _tail_metrics(real)
    synth_t = _tail_metrics(synthetic)

    return {
        "real": real_t,
        "synthetic": synth_t,
        "kurtosis_ratio": synth_t["kurtosis"] / max(real_t["kurtosis"], 0.01),
        "tail_pass": (
            synth_t["kurtosis"] > 3.0
            and abs(synth_t["kurtosis"] - real_t["kurtosis"]) / max(real_t["kurtosis"], 0.01) < 0.5
        ),
    }


def full_validation(real: np.ndarray, synthetic: np.ndarray,
                    generator_name: str) -> Dict:
    """Run all validation tests and produce summary."""
    dist = validate_distribution(real, synthetic)
    temporal = validate_temporal(real, synthetic)
    tails = validate_tails(real, synthetic)

    checks = {
        "distribution_ks": dist.get("ks_pass", False),
        "mean_error_<10%": dist.get("moment_errors", {}).get("mean", 1) < 0.10,
        "std_error_<10%": dist.get("moment_errors", {}).get("std", 1) < 0.10,
        "kurtosis_realistic": tails.get("tail_pass", False),
        "vol_clustering_present": temporal.get("vol_clustering", {}).get("both_present", False),
        "acf_abs_mae_<0.05": temporal.get("abs_returns", {}).get("acf_mae", 1) < 0.05,
    }

    n_pass = sum(checks.values())
    return {
        "generator": generator_name,
        "checks": checks,
        "score": f"{n_pass}/{len(checks)}",
        "verdict": "PASS" if n_pass >= len(checks) - 1 else "FAIL",
    }


# ── Stress Test Scenarios ──────────────────────────────────────────────

STRESS_SCENARIOS = {
    "2008_financial_crisis": {"mu": -0.60, "sigma": 0.80, "duration_days": 120,
                              "lam": 20, "mu_J": -0.05, "sigma_J": 0.03},
    "2020_covid_crash": {"mu": -1.50, "sigma": 1.20, "duration_days": 25,
                         "lam": 40, "mu_J": -0.08, "sigma_J": 0.05},
    "flash_crash": {"mu": -5.00, "sigma": 3.00, "duration_days": 1,
                    "lam": 100, "mu_J": -0.10, "sigma_J": 0.08},
    "slow_grind_bear": {"mu": -0.15, "sigma": 0.25, "duration_days": 400,
                        "lam": 2, "mu_J": -0.02, "sigma_J": 0.01},
    "vol_regime_shift": {"mu": 0.00, "sigma": 0.50, "duration_days": 60,
                         "lam": 10, "mu_J": 0.00, "sigma_J": 0.04},
}


def stress_test_paths(scenario_name: str,
                      n_paths: int = 1000,
                      seed: int = 42) -> np.ndarray:
    """Generate stress test paths for a named scenario."""
    params = STRESS_SCENARIOS.get(scenario_name)
    if params is None:
        raise ValueError(f"Unknown scenario: {scenario_name}")

    return generate_merton_paths(
        S0=100.0, mu=params["mu"], sigma=params["sigma"],
        lam=params["lam"], mu_J=params["mu_J"], sigma_J=params["sigma_J"],
        T=params["duration_days"] / 252, dt=1 / 252,
        n_paths=n_paths, seed=seed)


# ── Main Pipeline Orchestrator ─────────────────────────────────────────

class SyntheticDataPipeline:
    """Orchestrates synthetic data generation and validation.

    Default mix: 40% regime-switching, 30% GARCH-FHB, 20% Merton, 10% reserved (GAN).
    """

    def __init__(self, output_dir: str = "/app/data/synthetic",
                 seed: int = 42):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.seed = seed

    def generate(self, real_prices: np.ndarray,
                 n_paths: int = 50000,
                 path_length: int = 252) -> Dict:
        """Generate mixed synthetic dataset from real price history.

        Returns summary dict with validation results.
        """
        real_returns = np.diff(np.log(real_prices))
        S0 = float(real_prices[-1])

        # Calibrate all generators
        garch_params = calibrate_garch(real_returns)
        regime_params = calibrate_regime_model(real_returns, seed=self.seed)
        merton_params = calibrate_merton(real_prices)

        all_synthetic_returns = []

        # 40% regime-switching
        n_regime = int(n_paths * 0.40)
        regime_prices, _ = generate_regime_paths(
            S0=S0, params=regime_params, T=1.0, dt=1 / 252,
            n_paths=n_regime, seed=self.seed)
        for i in range(n_regime):
            all_synthetic_returns.append(np.diff(np.log(regime_prices[:, i])))

        # 30% GARCH-FHB
        n_garch = int(n_paths * 0.30)
        garch_prices = garch_filtered_bootstrap(
            real_returns, S0=S0, n_steps=path_length,
            n_paths=n_garch, seed=self.seed + 1)
        for i in range(n_garch):
            all_synthetic_returns.append(np.diff(np.log(garch_prices[:, i])))

        # 20% Merton
        n_merton = int(n_paths * 0.20)
        merton_prices = generate_merton_paths(
            S0=S0, mu=merton_params["mu"], sigma=merton_params["sigma"],
            lam=merton_params["lambda"], mu_J=merton_params["mu_J"],
            sigma_J=merton_params["sigma_J"],
            T=1.0, dt=1 / 252, n_paths=n_merton, seed=self.seed + 2)
        for i in range(n_merton):
            all_synthetic_returns.append(np.diff(np.log(merton_prices[:, i])))

        # 10% GBM (placeholder for future GAN)
        n_gbm = n_paths - n_regime - n_garch - n_merton
        gbm_params = calibrate_gbm(real_prices)
        gbm_prices = generate_gbm_paths(
            S0=S0, mu=gbm_params["mu"], sigma=gbm_params["sigma"],
            T=1.0, dt=1 / 252, n_paths=max(n_gbm, 1), seed=self.seed + 3)
        for i in range(n_gbm):
            all_synthetic_returns.append(np.diff(np.log(gbm_prices[:, i])))

        # Validate each generator
        synth_concat = np.concatenate(all_synthetic_returns)
        sample_idx = np.random.default_rng(self.seed).choice(
            len(synth_concat), size=min(len(synth_concat), len(real_returns) * 10),
            replace=False)
        synth_sample = synth_concat[sample_idx]

        validation = full_validation(real_returns, synth_sample, "mixed_pipeline")

        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "n_paths": n_paths,
            "mix": {"regime": n_regime, "garch_fhb": n_garch,
                    "merton": n_merton, "gbm": n_gbm},
            "calibration": {
                "garch": garch_params,
                "regime": {k: v for k, v in regime_params.items() if k != "transmat"},
                "merton": {k: v for k, v in merton_params.items() if k != "S0"},
            },
            "validation": validation,
        }

        # Save
        try:
            with open(str(self.output_dir / "synthetic_summary.json"), "w") as f:
                json.dump(summary, f, indent=2, default=str)
        except Exception as e:
            log.warning("Failed to save synthetic summary: %s", e)

        return summary


# ── Nightly Integration ─────────────────────────────────────────────────

def run_nightly_synthetic(prices_path: str = "/app/data/prices/combined.npy") -> Dict:
    """Nightly synthetic data generation step.

    Generates calibrated synthetic data and validates against real.
    """
    prices_p = Path(prices_path)
    if not prices_p.exists():
        return {"status": "skipped", "reason": "No price data file"}

    try:
        prices = np.load(str(prices_p))
    except Exception as e:
        return {"status": "error", "reason": f"Failed to load prices: {e}"}

    pipeline = SyntheticDataPipeline()
    # Use small batch for nightly (full 50K is for pre-training)
    return pipeline.generate(prices, n_paths=1000, path_length=252)
