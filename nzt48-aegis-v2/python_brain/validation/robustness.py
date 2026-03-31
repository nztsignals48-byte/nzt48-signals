"""validation/robustness.py — Book 31: Adversarial Robustness & Anti-Overfitting Framework.

Implements DSR gate, CPCV, walk-forward efficiency, noise injection,
adversarial perturbation, and Anti-BS checklist scoring.

Bailey & Lopez de Prado (2014): Deflated Sharpe Ratio
Lopez de Prado (2018): Combinatorial Purged Cross-Validation
"""

import math
import time
import json
import os
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Callable
from pathlib import Path
from itertools import combinations

import numpy as np

try:
    from scipy import stats as sp_stats
except ImportError:
    sp_stats = None

log = logging.getLogger(__name__)

EULER_MASCHERONI = 0.5772156649

# ── DSR Calculator ──────────────────────────────────────────────────────

def compute_sharpe(returns: np.ndarray, periods_per_year: float = 252.0) -> float:
    """Annualised Sharpe ratio."""
    if len(returns) < 2:
        return 0.0
    std = np.std(returns, ddof=1)
    if std < 1e-12:
        return 0.0
    return float(np.mean(returns) / std * np.sqrt(periods_per_year))


def expected_max_sharpe(n_trials: int, sharpe_variance: float = 0.3) -> float:
    """Expected maximum Sharpe under the null (no edge, N trials)."""
    if n_trials <= 1 or sp_stats is None:
        return 0.0
    std_sr = math.sqrt(max(sharpe_variance, 1e-10))
    gamma = EULER_MASCHERONI
    term1 = (1 - gamma) * sp_stats.norm.ppf(1 - 1.0 / n_trials)
    term2 = gamma * sp_stats.norm.ppf(1 - 1.0 / (n_trials * math.e))
    return float(std_sr * (term1 + term2))


def compute_dsr(returns: np.ndarray, n_trials: int = 1,
                sharpe_variance: float = 0.3,
                periods_per_year: float = 252.0) -> float:
    """Deflated Sharpe Ratio — probability that observed SR is genuine.

    Returns 0-1.  >0.95 = significant, <0.50 = kill.
    """
    T = len(returns)
    if T < 3 or sp_stats is None:
        return 0.0
    sr_star = compute_sharpe(returns, periods_per_year)
    sr_0 = expected_max_sharpe(n_trials, sharpe_variance)
    skew = float(sp_stats.skew(returns))
    kurt = float(sp_stats.kurtosis(returns, fisher=False))  # excess=False → raw
    numerator = (sr_star - sr_0) * math.sqrt(T - 1)
    denom_sq = max(1e-10, 1.0 - skew * sr_star + ((kurt - 1) / 4.0) * sr_star ** 2)
    return float(sp_stats.norm.cdf(numerator / math.sqrt(denom_sq)))


# ── CPCV ────────────────────────────────────────────────────────────────

def run_cpcv(returns: np.ndarray,
             n_groups: int = 10,
             n_test_groups: int = 2,
             purge_window: int = 200,
             embargo_pct: float = 0.01) -> Dict:
    """Combinatorial Purged Cross-Validation.

    Returns dict with mean_oos_sharpe, positive_path_fraction, pbo, n_paths.
    """
    T = len(returns)
    if T < 100:
        return {"mean_oos_sharpe": 0.0, "positive_path_fraction": 0.0,
                "n_paths": 0, "pbo": 1.0, "status": "insufficient_data"}

    group_size = T // n_groups
    embargo_size = max(1, int(embargo_pct * T))
    groups = [list(range(g * group_size, min((g + 1) * group_size, T)))
              for g in range(n_groups)]

    oos_sharpes = []
    is_sharpes = []

    for test_gs in combinations(range(n_groups), n_test_groups):
        test_idx = set()
        for g in test_gs:
            test_idx.update(groups[g])

        train_idx = []
        for g in range(n_groups):
            if g in test_gs:
                continue
            for idx in groups[g]:
                # Purge: skip indices close to any test index
                too_close = False
                for tg in test_gs:
                    for t_idx in groups[tg]:
                        if abs(idx - t_idx) <= purge_window:
                            too_close = True
                            break
                    if too_close:
                        break
                if too_close:
                    continue
                # Embargo: skip indices just after test groups
                in_embargo = False
                for tg in test_gs:
                    max_tg = max(groups[tg])
                    if max_tg < idx <= max_tg + embargo_size:
                        in_embargo = True
                        break
                if in_embargo:
                    continue
                train_idx.append(idx)

        if len(train_idx) < 30 or len(test_idx) < 10:
            continue

        is_sharpes.append(_quick_sr(returns[np.array(train_idx)]))
        oos_sharpes.append(_quick_sr(returns[np.array(sorted(test_idx))]))

    if not oos_sharpes:
        return {"mean_oos_sharpe": 0.0, "positive_path_fraction": 0.0,
                "n_paths": 0, "pbo": 1.0, "status": "no_valid_paths"}

    oos = np.array(oos_sharpes)
    is_arr = np.array(is_sharpes)

    # PBO approximation via IS-OOS rank correlation
    if len(is_arr) > 2:
        corr = np.corrcoef(is_arr, oos)[0, 1]
        if np.isnan(corr):
            corr = 0.0
    else:
        corr = 0.0

    return {
        "mean_oos_sharpe": float(np.mean(oos)),
        "std_oos_sharpe": float(np.std(oos)),
        "positive_path_fraction": float(np.mean(oos > 0)),
        "worst_path_sharpe": float(np.min(oos)),
        "n_paths": len(oos_sharpes),
        "pbo": float(max(0.0, 0.5 - 0.5 * corr)),
        "status": "ok",
    }


# ── Walk-Forward ────────────────────────────────────────────────────────

def walk_forward_expanding(returns: np.ndarray,
                           min_train: int = 200,
                           test_size: int = 50,
                           step_size: int = 20) -> Dict:
    """Expanding-window walk-forward analysis.

    Returns dict with WFER, stability, is/oos sharpe lists.
    """
    T = len(returns)
    if T < min_train + test_size:
        return {"wfer": 0.0, "stability": 0.0, "n_windows": 0,
                "status": "insufficient_data"}

    is_sharpes = []
    oos_sharpes = []
    t = min_train
    while t + test_size <= T:
        train_r = returns[:t]
        test_r = returns[t:t + test_size]
        is_sharpes.append(_quick_sr(train_r))
        oos_sharpes.append(_quick_sr(test_r))
        t += step_size

    if not oos_sharpes:
        return {"wfer": 0.0, "stability": 0.0, "n_windows": 0,
                "status": "no_windows"}

    oos = np.array(oos_sharpes)
    is_arr = np.array(is_sharpes)
    mean_is = np.mean(is_arr)
    mean_oos = np.mean(oos)
    wfer = float(mean_oos / mean_is) if abs(mean_is) > 1e-10 else 0.0
    std_oos = np.std(oos)
    stability = float(1.0 - std_oos / abs(mean_oos)) if abs(mean_oos) > 1e-10 else 0.0

    return {
        "wfer": wfer,
        "stability": stability,
        "mean_is_sharpe": float(mean_is),
        "mean_oos_sharpe": float(mean_oos),
        "n_windows": len(oos_sharpes),
        "status": "ok",
    }


def walk_forward_rolling(returns: np.ndarray,
                         train_size: int = 500,
                         test_size: int = 50,
                         step_size: int = 20) -> Dict:
    """Rolling-window walk-forward analysis."""
    T = len(returns)
    if T < train_size + test_size:
        return {"wfer": 0.0, "stability": 0.0, "n_windows": 0,
                "status": "insufficient_data"}

    is_sharpes = []
    oos_sharpes = []
    t = train_size
    while t + test_size <= T:
        train_r = returns[t - train_size:t]
        test_r = returns[t:t + test_size]
        is_sharpes.append(_quick_sr(train_r))
        oos_sharpes.append(_quick_sr(test_r))
        t += step_size

    if not oos_sharpes:
        return {"wfer": 0.0, "stability": 0.0, "n_windows": 0,
                "status": "no_windows"}

    oos = np.array(oos_sharpes)
    is_arr = np.array(is_sharpes)
    mean_is = np.mean(is_arr)
    mean_oos = np.mean(oos)
    wfer = float(mean_oos / mean_is) if abs(mean_is) > 1e-10 else 0.0
    std_oos = np.std(oos)
    stability = float(1.0 - std_oos / abs(mean_oos)) if abs(mean_oos) > 1e-10 else 0.0

    return {
        "wfer": wfer,
        "stability": stability,
        "mean_is_sharpe": float(mean_is),
        "mean_oos_sharpe": float(mean_oos),
        "n_windows": len(oos_sharpes),
        "status": "ok",
    }


# ── Noise Injection ────────────────────────────────────────────────────

def noise_injection_test(returns: np.ndarray,
                         epsilon: float = 0.05,
                         n_realizations: int = 100) -> Dict:
    """Test strategy robustness to random feature noise.

    If 5% noise causes >20% Sharpe degradation, strategy is memorising noise.
    """
    clean_sr = compute_sharpe(returns)
    if abs(clean_sr) < 1e-10:
        return {"mean_degradation": 1.0, "pass": False,
                "message": "Clean Sharpe ~0, cannot test"}

    rng = np.random.default_rng(42)
    degradations = []
    for _ in range(n_realizations):
        noise = 1.0 + epsilon * rng.standard_normal(len(returns))
        noisy_ret = returns * noise
        noisy_sr = compute_sharpe(noisy_ret)
        deg = 1.0 - noisy_sr / clean_sr if clean_sr != 0 else 1.0
        degradations.append(deg)

    degradations = np.array(degradations)
    mean_deg = float(np.mean(degradations))
    p95_deg = float(np.percentile(degradations, 95))

    return {
        "epsilon": epsilon,
        "clean_sharpe": clean_sr,
        "mean_degradation": mean_deg,
        "p95_degradation": p95_deg,
        "n_realizations": n_realizations,
        "pass": mean_deg < 0.20,
    }


# ── Adversarial Robustness Score ────────────────────────────────────────

def adversarial_robustness_score(returns: np.ndarray,
                                 spread_bps: float = 10.0) -> Dict:
    """Compute Adversarial Robustness Score (ARS) = weighted margin across attacks.

    ARS >= 0.60 required.  Attacks: sign-flip, spread, timing, volume.
    """
    clean_sr = compute_sharpe(returns)
    if abs(clean_sr) < 1e-10:
        return {"ars": 0.0, "pass": False, "message": "Clean Sharpe ~0"}

    rng = np.random.default_rng(42)

    # Attack 1: Sign-flip margin — how much noise to flip signal direction
    flip_margins = []
    for eps in [0.005, 0.01, 0.02, 0.05, 0.10]:
        noise = 1.0 + eps * rng.standard_normal(len(returns))
        noisy_sr = compute_sharpe(returns * noise)
        if noisy_sr * clean_sr < 0:  # Sign flipped
            flip_margins.append(eps)
            break
    sign_flip_margin = min(flip_margins) if flip_margins else 0.10
    sign_flip_score = min(1.0, sign_flip_margin / 0.05)  # Normalise: 5% = 1.0

    # Attack 2: Spread poisoning — widen effective cost
    spread_costs = spread_bps / 10000.0
    spread_degradations = []
    for mult in [1.2, 1.5, 2.0, 3.0]:
        cost_adj = returns - spread_costs * (mult - 1.0)
        deg = 1.0 - compute_sharpe(cost_adj) / clean_sr
        spread_degradations.append(deg)
    # Score: how much spread widening before 50% Sharpe loss
    spread_score = 1.0
    for i, deg in enumerate(spread_degradations):
        if deg > 0.50:
            spread_score = [1.2, 1.5, 2.0, 3.0][i] / 3.0
            break

    # Attack 3: Timestamp perturbation — shift returns by 1 bar
    shifted_sr = compute_sharpe(np.roll(returns, 1))
    timing_deg = abs(1.0 - shifted_sr / clean_sr) if clean_sr != 0 else 1.0
    timing_score = max(0.0, 1.0 - timing_deg)

    # Attack 4: Volume perturbation (proxy: scale returns by noise)
    vol_noise = 1.0 + 0.3 * rng.standard_normal(len(returns))
    vol_sr = compute_sharpe(returns * vol_noise)
    vol_deg = abs(1.0 - vol_sr / clean_sr) if clean_sr != 0 else 1.0
    vol_score = max(0.0, 1.0 - vol_deg)

    ars = 0.4 * sign_flip_score + 0.3 * spread_score + 0.2 * timing_score + 0.1 * vol_score

    return {
        "ars": round(ars, 4),
        "sign_flip_margin": sign_flip_margin,
        "sign_flip_score": round(sign_flip_score, 4),
        "spread_score": round(spread_score, 4),
        "timing_score": round(timing_score, 4),
        "volume_score": round(vol_score, 4),
        "pass": ars >= 0.60,
    }


# ── Anti-BS Checklist ──────────────────────────────────────────────────

@dataclass
class ChecklistResult:
    """Anti-Bullshit Checklist result (Book 31 Section 27-28)."""
    scores: Dict[str, int]  # question → score (-2 to +2)
    total: int = 0
    verdict: str = ""
    details: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.total = sum(self.scores.values())
        if self.total >= 16:
            self.verdict = "EXCEPTIONAL"
        elif self.total >= 12:
            self.verdict = "GOOD"
        elif self.total >= 8:
            self.verdict = "MARGINAL"
        elif self.total >= 4:
            self.verdict = "POOR"
        elif self.total >= 0:
            self.verdict = "FAILING"
        else:
            self.verdict = "HARMFUL"

    def to_dict(self) -> Dict:
        return asdict(self)


def anti_bs_checklist(strategy_name: str,
                      n_trades: int,
                      has_oos: bool = False,
                      n_variants_tested: int = 0,
                      realistic_costs: bool = False,
                      point_in_time_data: bool = False,
                      same_code_as_live: bool = False,
                      noise_tested: bool = False,
                      noise_pass: bool = False,
                      multi_regime: bool = False,
                      wfer: float = 0.0,
                      economic_rationale: bool = False,
                      min_trl: int = 200) -> ChecklistResult:
    """Score a strategy on the 10-question Anti-BS Checklist.

    Per question: definitive yes = +2, yes with caveats = +1,
    unknown = 0, definitive no = -2.  Range: -20 to +20.
    """
    scores = {}
    details = {}

    # Q1: Out-of-sample data?
    if has_oos:
        scores["Q1_oos"] = 2
        details["Q1"] = "Has OOS walk-forward/CPCV"
    else:
        scores["Q1_oos"] = 0
        details["Q1"] = "No OOS validation"

    # Q2: How many variants tested?
    if n_variants_tested > 0 and n_variants_tested <= 10:
        scores["Q2_variants"] = 2
        details["Q2"] = f"{n_variants_tested} variants tracked"
    elif n_variants_tested > 10:
        scores["Q2_variants"] = 1
        details["Q2"] = f"{n_variants_tested} variants — needs DSR adjustment"
    else:
        scores["Q2_variants"] = 0
        details["Q2"] = "Variant count unknown"

    # Q3: Realistic costs?
    scores["Q3_costs"] = 2 if realistic_costs else -1
    details["Q3"] = "Cost-adjusted" if realistic_costs else "Costs uncertain"

    # Q4: Point-in-time data?
    scores["Q4_pit"] = 2 if point_in_time_data else 0
    details["Q4"] = "PIT verified" if point_in_time_data else "PIT unverified"

    # Q5: Same code as live?
    if same_code_as_live:
        scores["Q5_code"] = 2
        details["Q5"] = "Same code path"
    else:
        scores["Q5_code"] = -2
        details["Q5"] = "Reimplemented — potential bugs"

    # Q6: Sufficient N?
    if n_trades >= min_trl:
        scores["Q6_n"] = 2
        details["Q6"] = f"N={n_trades} >= MinTRL={min_trl}"
    elif n_trades >= min_trl // 2:
        scores["Q6_n"] = 0
        details["Q6"] = f"N={n_trades} approaching MinTRL={min_trl}"
    else:
        scores["Q6_n"] = -2
        details["Q6"] = f"N={n_trades} << MinTRL={min_trl}"

    # Q7: Noise injection survived?
    if noise_tested and noise_pass:
        scores["Q7_noise"] = 2
        details["Q7"] = "Noise test passed"
    elif noise_tested and not noise_pass:
        scores["Q7_noise"] = -2
        details["Q7"] = "Noise test FAILED"
    else:
        scores["Q7_noise"] = 0
        details["Q7"] = "Not tested"

    # Q8: Multi-regime tested?
    scores["Q8_regime"] = 2 if multi_regime else 0
    details["Q8"] = "Multi-regime" if multi_regime else "Not tested"

    # Q9: Walk-Forward Efficiency?
    if wfer >= 0.50:
        scores["Q9_wfer"] = 2
        details["Q9"] = f"WFER={wfer:.2f} >= 0.50"
    elif wfer >= 0.30:
        scores["Q9_wfer"] = 1
        details["Q9"] = f"WFER={wfer:.2f} marginal"
    elif wfer > 0:
        scores["Q9_wfer"] = -1
        details["Q9"] = f"WFER={wfer:.2f} poor"
    else:
        scores["Q9_wfer"] = 0
        details["Q9"] = "WFER not computed"

    # Q10: Economic rationale?
    scores["Q10_econ"] = 2 if economic_rationale else -1
    details["Q10"] = "Has rationale" if economic_rationale else "No clear mechanism"

    return ChecklistResult(scores=scores, details=details)


# ── Divergence Monitor ──────────────────────────────────────────────────

@dataclass
class DivergenceState:
    """Live-backtest divergence tracking (CUSUM + control chart)."""
    strategy: str
    divergences: List[float] = field(default_factory=list)
    cusum_high: float = 0.0
    cusum_low: float = 0.0
    consecutive_2sigma: int = 0
    state: str = "NORMAL"  # NORMAL, MONITORING, REDUCED, HALTED

    POSITION_MULT = {"NORMAL": 1.0, "MONITORING": 0.75, "REDUCED": 0.50, "HALTED": 0.0}

    def update(self, live_pnl: float, sim_pnl: float) -> Dict:
        """Update with new observation. Returns alert dict if state changes."""
        d = live_pnl - sim_pnl
        self.divergences.append(d)

        if len(self.divergences) < 5:
            return {"state": self.state, "alert": None}

        arr = np.array(self.divergences)
        mu = np.mean(arr)
        sigma = max(np.std(arr), 1e-8)

        # Control chart
        z = abs(d - mu) / sigma
        if z > 2.0:
            self.consecutive_2sigma += 1
        else:
            self.consecutive_2sigma = 0

        # CUSUM
        k = 0.5 * sigma
        h = 4.0 * sigma
        self.cusum_high = max(0, self.cusum_high + (d - mu - k))
        self.cusum_low = min(0, self.cusum_low + (d - mu + k))

        # State transitions
        old_state = self.state
        if z > 3.0 or self.cusum_high > h or self.cusum_low < -h:
            self.state = "HALTED"
        elif self.consecutive_2sigma >= 5:
            self.state = "HALTED"
        elif self.consecutive_2sigma >= 3:
            self.state = "REDUCED"
        elif self.consecutive_2sigma >= 1:
            self.state = "MONITORING"
        elif self.consecutive_2sigma == 0 and self.state != "HALTED":
            self.state = "NORMAL"

        alert = None
        if self.state != old_state:
            alert = {
                "strategy": self.strategy,
                "old_state": old_state,
                "new_state": self.state,
                "z_score": round(z, 2),
                "consecutive_2sigma": self.consecutive_2sigma,
                "position_multiplier": self.POSITION_MULT[self.state],
            }

        return {"state": self.state, "alert": alert,
                "position_multiplier": self.POSITION_MULT[self.state]}

    def to_dict(self) -> Dict:
        return {
            "strategy": self.strategy,
            "state": self.state,
            "n_observations": len(self.divergences),
            "consecutive_2sigma": self.consecutive_2sigma,
            "position_multiplier": self.POSITION_MULT[self.state],
        }


# ── Master Validation Framework ─────────────────────────────────────────

@dataclass
class ValidationResult:
    """Combined validation result for a strategy."""
    strategy: str
    n_trades: int
    timestamp: float = field(default_factory=time.time)

    # Gate results
    dsr: float = 0.0
    dsr_pass: bool = False
    cpcv: Dict = field(default_factory=dict)
    cpcv_pass: bool = False
    wfer: float = 0.0
    wfer_pass: bool = False
    noise: Dict = field(default_factory=dict)
    noise_pass: bool = False
    ars: Dict = field(default_factory=dict)
    ars_pass: bool = False
    checklist: Dict = field(default_factory=dict)
    checklist_score: int = 0

    # Overall
    gates_passed: int = 0
    gates_total: int = 5
    verdict: str = "INSUFFICIENT_DATA"

    def to_dict(self) -> Dict:
        return asdict(self)


class RobustnessValidator:
    """Master orchestrator — runs all validation gates for a strategy.

    Gates execute: Noise → ARS → Walk-Forward → CPCV → DSR.
    Any gate failure stops progression for deployment but all gates run for reporting.
    """

    # Thresholds
    DSR_THRESHOLD = 0.95
    WFER_THRESHOLD = 0.50
    CPCV_POSITIVE_FRACTION = 0.80
    PBO_MAX = 0.20
    ARS_THRESHOLD = 0.60
    NOISE_MAX_DEGRADATION = 0.20
    MIN_N = 200

    def __init__(self, output_dir: str = "/app/data/validation"):
        self.output_dir = Path(output_dir)
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def validate(self, strategy_name: str,
                 returns: np.ndarray,
                 n_trials: int = 1,
                 spread_bps: float = 10.0,
                 **checklist_kwargs) -> ValidationResult:
        """Run full validation suite on a strategy's returns."""
        n = len(returns)
        result = ValidationResult(strategy=strategy_name, n_trades=n)

        if n < 30:
            result.verdict = "INSUFFICIENT_DATA"
            return result

        # Gate 1: Noise injection
        noise = noise_injection_test(returns)
        result.noise = noise
        result.noise_pass = noise["pass"]
        if result.noise_pass:
            result.gates_passed += 1

        # Gate 2: Adversarial robustness
        ars = adversarial_robustness_score(returns, spread_bps=spread_bps)
        result.ars = ars
        result.ars_pass = ars["pass"]
        if result.ars_pass:
            result.gates_passed += 1

        # Gate 3: Walk-forward efficiency
        if n >= self.MIN_N:
            wf = walk_forward_expanding(returns)
            result.wfer = wf.get("wfer", 0.0)
            result.wfer_pass = result.wfer >= self.WFER_THRESHOLD
        else:
            result.wfer = 0.0
            result.wfer_pass = False
        if result.wfer_pass:
            result.gates_passed += 1

        # Gate 4: CPCV
        if n >= 500:
            cpcv = run_cpcv(returns)
            result.cpcv = cpcv
            result.cpcv_pass = (
                cpcv.get("positive_path_fraction", 0) >= self.CPCV_POSITIVE_FRACTION
                and cpcv.get("pbo", 1.0) <= self.PBO_MAX
            )
        else:
            result.cpcv = {"status": "insufficient_data", "n_required": 500}
            result.cpcv_pass = False
        if result.cpcv_pass:
            result.gates_passed += 1

        # Gate 5: DSR
        dsr = compute_dsr(returns, n_trials=n_trials)
        result.dsr = dsr
        result.dsr_pass = dsr >= self.DSR_THRESHOLD
        if result.dsr_pass:
            result.gates_passed += 1

        # Anti-BS Checklist
        cl = anti_bs_checklist(
            strategy_name=strategy_name,
            n_trades=n,
            has_oos=result.wfer_pass,
            noise_tested=True,
            noise_pass=result.noise_pass,
            wfer=result.wfer,
            **checklist_kwargs,
        )
        result.checklist = cl.to_dict()
        result.checklist_score = cl.total

        # Overall verdict
        if n < self.MIN_N:
            result.verdict = "COLLECTING_DATA"
        elif result.gates_passed == result.gates_total:
            result.verdict = "VALIDATED"
        elif result.gates_passed >= 3:
            result.verdict = "MARGINAL"
        else:
            result.verdict = "FAILED"

        return result

    def run_nightly(self, strategy_returns: Dict[str, np.ndarray],
                    n_trials: int = 1) -> Dict:
        """Run validation for all strategies. Returns summary dict."""
        results = {}
        for name, returns in strategy_returns.items():
            try:
                vr = self.validate(name, returns, n_trials=n_trials,
                                   realistic_costs=True, same_code_as_live=True,
                                   economic_rationale=True)
                results[name] = vr.to_dict()
            except Exception as e:
                log.warning("Validation failed for %s: %s", name, e)
                results[name] = {"strategy": name, "verdict": "ERROR",
                                 "error": str(e)[:200]}

        # Save to disk
        out_path = self.output_dir / "validation_audit.json"
        try:
            with open(str(out_path), "w") as f:
                json.dump(results, f, indent=2, default=str)
        except Exception as e:
            log.warning("Failed to save validation audit: %s", e)

        return results


# ── Helpers ─────────────────────────────────────────────────────────────

def _quick_sr(r: np.ndarray) -> float:
    """Quick Sharpe (not annualised, for relative comparisons)."""
    if len(r) < 2:
        return 0.0
    std = np.std(r, ddof=1)
    if std < 1e-12:
        return 0.0
    return float(np.mean(r) / std)


def min_track_record_length(observed_sharpe: float,
                            skew: float = 0.0,
                            kurt: float = 3.0,
                            sr_benchmark: float = 0.0,
                            alpha: float = 0.05) -> int:
    """Minimum Track Record Length for significance at given alpha."""
    if sp_stats is None or observed_sharpe <= sr_benchmark:
        return 99999
    z = sp_stats.norm.ppf(1 - alpha)
    sr_diff = observed_sharpe - sr_benchmark
    numer = 1.0 - skew * observed_sharpe + ((kurt - 1) / 4.0) * observed_sharpe ** 2
    return int(math.ceil(1 + numer * (z / sr_diff) ** 2))
