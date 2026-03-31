"""ouroboros/exit_optimizer.py — Book 39: Exit Optimization & Adaptive Trade Management.

Nightly exit parameter optimisation using MFE/MAE analysis.
Generates per-strategy Chandelier calibration recommendations.

Depends on forensics/mfe_mae.py (already exists) for trade metrics.
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

# ── Per-Strategy Defaults ───────────────────────────────────────────────

# Strategy type classification
STRATEGY_TYPES = {
    "VanguardSniper": "momentum",
    "EarlyRunner": "momentum",
    "IBS_MeanReversion": "mean_reversion",
    "OverboughtFade": "mean_reversion",
    "DipRecovery": "mean_reversion",
    "OBV_Divergence": "divergence",
    "S_VanguardSniper": "momentum",
    "S_ApexScout": "momentum",
    "S_AutonomousOrchestrator": "mixed",
}

# Target exit efficiency by strategy type
TARGET_EXIT_EFFICIENCY = {
    "momentum": (0.35, 0.55),     # Momentum: wide trail, capture tail
    "mean_reversion": (0.60, 0.80),  # MR: tight target, capture fast
    "divergence": (0.50, 0.70),   # Divergence: moderate
    "mixed": (0.40, 0.60),
}

# Expected hold times in minutes by strategy
EXPECTED_HOLD_MINUTES = {
    "VanguardSniper": 60,
    "EarlyRunner": 90,
    "IBS_MeanReversion": 30,
    "OverboughtFade": 20,
    "DipRecovery": 45,
    "OBV_Divergence": 60,
}

# Default Chandelier params per strategy (initial values before MFE/MAE optimisation)
DEFAULT_CHANDELIER = {
    "VanguardSniper": {"initial_atr": 2.0, "rung_pct": [0.0, 0.010, 0.020, 0.035, 0.050],
                       "trail_atr": [0, 0, 1.2, 0.9, 0.6], "time_stop": 90},
    "EarlyRunner": {"initial_atr": 2.5, "rung_pct": [0.0, 0.012, 0.025, 0.040, 0.060],
                    "trail_atr": [0, 0, 1.3, 1.0, 0.7], "time_stop": 120},
    "IBS_MeanReversion": {"initial_atr": 1.2, "rung_pct": [0.0, 0.005, 0.010, 0.018, 0.030],
                          "trail_atr": [0, 0, 0.7, 0.5, 0.3], "time_stop": 30},
    "OverboughtFade": {"initial_atr": 1.0, "rung_pct": [0.0, 0.004, 0.008, 0.015, 0.025],
                       "trail_atr": [0, 0, 0.6, 0.4, 0.3], "time_stop": 20},
    "DipRecovery": {"initial_atr": 1.3, "rung_pct": [0.0, 0.006, 0.012, 0.020, 0.035],
                    "trail_atr": [0, 0, 0.8, 0.6, 0.4], "time_stop": 45},
    "OBV_Divergence": {"initial_atr": 1.5, "rung_pct": [0.0, 0.008, 0.015, 0.025, 0.040],
                       "trail_atr": [0, 0, 0.9, 0.7, 0.4], "time_stop": 60},
}


# ── Dataclasses ─────────────────────────────────────────────────────────

@dataclass
class ExitQualityScore:
    """Post-trade exit quality score (5 dimensions)."""
    strategy: str
    ticker: str
    timing: float = 0.0       # weight 0.30
    trail_quality: float = 0.0  # weight 0.25
    time_management: float = 0.0  # weight 0.20
    stop_quality: float = 0.0    # weight 0.15
    cost_efficiency: float = 0.0  # weight 0.10
    overall: float = 0.0
    grade: str = "D"

    def compute(self):
        self.overall = (
            self.timing * 0.30
            + self.trail_quality * 0.25
            + self.time_management * 0.20
            + self.stop_quality * 0.15
            + self.cost_efficiency * 0.10
        )
        if self.overall >= 8.0:
            self.grade = "A"
        elif self.overall >= 6.0:
            self.grade = "B"
        elif self.overall >= 4.0:
            self.grade = "C"
        else:
            self.grade = "D"

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ExitOptimisationResult:
    """Recommended exit parameter changes for a strategy."""
    strategy: str
    n_trades: int
    current_params: Dict = field(default_factory=dict)
    recommended_params: Dict = field(default_factory=dict)
    changes: Dict = field(default_factory=dict)  # param → {old, new, pct_change}
    exit_efficiency_median: float = 0.0
    exit_efficiency_target: Tuple[float, float] = (0.0, 0.0)
    diagnosis: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class RegimeExitScaling:
    """Regime-adaptive exit parameter scaling."""
    regime: str
    trail_scale: float = 1.0
    time_scale: float = 1.0
    breakeven_add_pct: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


# ── Regime Exit Scaling ─────────────────────────────────────────────────

REGIME_EXIT_SCALES = {
    "STEADY":  RegimeExitScaling("STEADY", trail_scale=1.0, time_scale=1.0, breakeven_add_pct=0.0),
    "WOI":     RegimeExitScaling("WOI", trail_scale=0.85, time_scale=0.80, breakeven_add_pct=0.002),
    "CRISIS":  RegimeExitScaling("CRISIS", trail_scale=0.70, time_scale=0.60, breakeven_add_pct=0.005),
    "EXTREME": RegimeExitScaling("EXTREME", trail_scale=0.50, time_scale=0.33, breakeven_add_pct=0.010),
}


def get_regime_scaling(regime: str) -> RegimeExitScaling:
    """Get exit scaling for a VIX regime."""
    return REGIME_EXIT_SCALES.get(regime, REGIME_EXIT_SCALES["STEADY"])


# ── Signal Quality Decay ───────────────────────────────────────────────

def signal_quality_decay(t_minutes: float, half_life_minutes: float,
                         initial_quality: float = 1.0) -> float:
    """Exponential decay of signal predictive power."""
    if half_life_minutes <= 0:
        return 0.0
    decay_constant = math.log(2) / half_life_minutes
    return initial_quality * math.exp(-decay_constant * t_minutes)


# Signal half-lives by category
SIGNAL_HALF_LIVES = {
    "microstructure": 2.5,   # 2.5 minutes
    "mean_reversion": 30.0,  # 30 minutes
    "momentum_short": 120.0,  # 2 hours
    "momentum_medium": 480.0,  # 8 hours
    "fundamental": 1440.0,   # 24 hours
}


# ── Exit Quality Scorer ────────────────────────────────────────────────

def score_exit_quality(trade: Dict) -> ExitQualityScore:
    """Score a completed trade's exit quality (0-10 on 5 dimensions).

    trade dict must have: strategy, ticker, mfe_pct, mae_pct,
    actual_pnl_pct, exit_efficiency, hold_time_minutes, exit_rung,
    initial_R, exit_R_multiple, round_trip_cost_pct.
    """
    strat = trade.get("strategy", "unknown")
    ticker = trade.get("ticker", "")
    score = ExitQualityScore(strategy=strat, ticker=ticker)

    # 1. TIMING (weight 0.30): exit_efficiency * 10
    eff = trade.get("exit_efficiency", 0)
    if eff is None or not np.isfinite(eff):
        eff = 0
    score.timing = min(10.0, max(0.0, eff * 10))

    # 2. TRAIL_QUALITY (weight 0.25): (1 - gave_back_fraction) * 10
    mfe = trade.get("mfe_pct", 0) or 0
    actual = trade.get("actual_pnl_pct", 0) or 0
    if mfe > 0:
        gave_back = max(0.0, 1.0 - actual / mfe)
        score.trail_quality = max(0.0, (1.0 - gave_back) * 10)
    else:
        score.trail_quality = 5.0  # Neutral if no MFE

    # 3. TIME_MANAGEMENT (weight 0.20): based on hold time vs expected
    hold_mins = trade.get("hold_time_minutes", 0) or 0
    expected = EXPECTED_HOLD_MINUTES.get(strat, 60)
    ratio = hold_mins / expected if expected > 0 else 1.0
    if 0.5 <= ratio <= 1.5:
        score.time_management = 8.0
    elif 0.25 <= ratio <= 2.0:
        score.time_management = 6.0
    else:
        score.time_management = 4.0

    # 4. STOP_QUALITY (weight 0.15): based on MAE in R-multiples
    mae_r = abs(trade.get("mae_pct", 0) or 0) / max(abs(trade.get("initial_R", 1) or 1), 0.001)
    if mae_r < 0.5:
        score.stop_quality = 9.0
    elif mae_r < 1.0:
        score.stop_quality = 7.0
    elif mae_r < 1.5:
        score.stop_quality = 5.0
    else:
        score.stop_quality = 3.0

    # 5. COST_EFFICIENCY (weight 0.10): did we beat costs?
    cost = trade.get("round_trip_cost_pct", 0.003) or 0.003
    if actual > cost * 3:
        score.cost_efficiency = 9.0
    elif actual > cost:
        score.cost_efficiency = 7.0
    elif actual > 0:
        score.cost_efficiency = 5.0
    else:
        score.cost_efficiency = 3.0

    score.compute()
    return score


# ── MFE/MAE-Based Parameter Optimisation ─────────────────────────────

def optimise_exit_params(strategy_name: str,
                         trades: List[Dict],
                         min_trades: int = 30,
                         max_change_pct: float = 0.20) -> Optional[ExitOptimisationResult]:
    """Optimise exit parameters for a strategy using MFE/MAE data.

    Trades must have: mfe_pct, mae_pct, actual_pnl_pct, exit_efficiency,
    hold_time_minutes, initial_R, exit_R_multiple.

    Returns None if insufficient trades.
    """
    if len(trades) < min_trades:
        return None

    strat_type = STRATEGY_TYPES.get(strategy_name, "mixed")
    target_eff = TARGET_EXIT_EFFICIENCY.get(strat_type, (0.40, 0.60))
    current = DEFAULT_CHANDELIER.get(strategy_name, DEFAULT_CHANDELIER.get("VanguardSniper", {})).copy()

    result = ExitOptimisationResult(
        strategy=strategy_name,
        n_trades=len(trades),
        current_params=current.copy(),
        exit_efficiency_target=target_eff,
    )

    # Extract arrays
    mfe_pcts = np.array([t.get("mfe_pct", 0) or 0 for t in trades], dtype=float)
    mae_pcts = np.array([abs(t.get("mae_pct", 0) or 0) for t in trades], dtype=float)
    eff_arr = np.array([t.get("exit_efficiency", 0) or 0 for t in trades], dtype=float)
    hold_times = np.array([t.get("hold_time_minutes", 0) or 0 for t in trades], dtype=float)
    pnl_pcts = np.array([t.get("actual_pnl_pct", 0) or 0 for t in trades], dtype=float)

    # Filter invalid
    valid = np.isfinite(mfe_pcts) & np.isfinite(mae_pcts) & np.isfinite(eff_arr)
    mfe_pcts = mfe_pcts[valid]
    mae_pcts = mae_pcts[valid]
    eff_arr = eff_arr[valid]
    hold_times = hold_times[valid]
    pnl_pcts = pnl_pcts[valid]

    if len(mfe_pcts) < min_trades:
        return None

    result.exit_efficiency_median = float(np.median(eff_arr))

    # Separate winners and losers
    winners = pnl_pcts > 0
    winner_mae = mae_pcts[winners] if winners.any() else mae_pcts

    recommended = current.copy()
    changes = {}

    # Optimisation 1: Initial stop from P90 winner MAE
    if len(winner_mae) >= 10:
        p90_mae = float(np.percentile(winner_mae, 90))
        # Convert to ATR multiplier (approximate: MAE_pct / 0.5% ≈ ATR mult)
        new_initial_atr = max(1.0, min(3.0, p90_mae / 0.005 + 0.2))
        old_val = current.get("initial_atr", 1.5)
        clamped = _clamp_change(old_val, new_initial_atr, max_change_pct)
        if abs(clamped - old_val) > 0.01:
            recommended["initial_atr"] = round(clamped, 2)
            changes["initial_atr"] = {"old": old_val, "new": round(clamped, 2),
                                      "pct_change": round((clamped - old_val) / old_val * 100, 1)}

    # Optimisation 2: Trail multiplier based on exit efficiency
    median_eff = result.exit_efficiency_median
    if median_eff < target_eff[0]:
        # Trail too loose — tighten
        adjustment = -0.1
        diagnosis = f"efficiency {median_eff:.2f} < target {target_eff[0]:.2f} — tighten trail"
    elif median_eff > target_eff[1]:
        # Trail too tight — widen
        adjustment = 0.1
        diagnosis = f"efficiency {median_eff:.2f} > target {target_eff[1]:.2f} — widen trail"
    else:
        adjustment = 0.0
        diagnosis = f"efficiency {median_eff:.2f} within target range"

    result.diagnosis = diagnosis

    if adjustment != 0 and "trail_atr" in current:
        old_trails = current["trail_atr"]
        new_trails = []
        for i, t in enumerate(old_trails):
            if i < 2:  # Rungs 0,1 don't have trails
                new_trails.append(t)
            else:
                new_t = max(0.2, t + adjustment)
                new_trails.append(round(new_t, 2))
        recommended["trail_atr"] = new_trails
        changes["trail_atr"] = {"old": old_trails, "new": new_trails,
                                "adjustment": adjustment}

    # Optimisation 3: Time-stop from P80 of MFE time
    mfe_times = hold_times[mfe_pcts > 0]  # Time to MFE for profitable entries
    if len(mfe_times) >= 10:
        p80_mfe_time = float(np.percentile(mfe_times, 80))
        new_time_stop = max(15, min(120, int(p80_mfe_time * 1.5)))
        old_ts = current.get("time_stop", 60)
        clamped_ts = int(_clamp_change(old_ts, new_time_stop, max_change_pct))
        if abs(clamped_ts - old_ts) >= 5:
            recommended["time_stop"] = clamped_ts
            changes["time_stop"] = {"old": old_ts, "new": clamped_ts}

    # Optimisation 4: Rung thresholds from MFE percentiles
    if len(mfe_pcts[mfe_pcts > 0]) >= 20:
        positive_mfe = mfe_pcts[mfe_pcts > 0]
        new_rungs = [
            0.0,
            round(float(np.percentile(positive_mfe, 25)), 4),
            round(float(np.percentile(positive_mfe, 50)), 4),
            round(float(np.percentile(positive_mfe, 75)), 4),
            round(float(np.percentile(positive_mfe, 90)), 4),
        ]
        old_rungs = current.get("rung_pct", [0, 0.008, 0.015, 0.025, 0.040])
        # Clamp each rung change
        clamped_rungs = [0.0]
        changed = False
        for i in range(1, 5):
            c = _clamp_change(old_rungs[i] if i < len(old_rungs) else 0.01,
                              new_rungs[i], max_change_pct)
            clamped_rungs.append(round(c, 4))
            if abs(c - (old_rungs[i] if i < len(old_rungs) else 0.01)) > 0.0005:
                changed = True
        if changed:
            recommended["rung_pct"] = clamped_rungs
            changes["rung_pct"] = {"old": old_rungs, "new": clamped_rungs}

    result.recommended_params = recommended
    result.changes = changes
    return result


# ── R-Multiple Tracking ────────────────────────────────────────────────

def compute_r_multiple(entry_price: float, exit_price: float,
                       initial_stop: float) -> Dict:
    """Compute R-multiple metrics for a trade."""
    initial_R = abs(entry_price - initial_stop)
    if initial_R < 1e-8:
        return {"initial_R": 0.0, "exit_R": 0.0, "exit_efficiency": 0.0}

    exit_R = (exit_price - entry_price) / initial_R

    return {
        "initial_R": round(initial_R, 4),
        "exit_R": round(exit_R, 2),
    }


# ── Nightly Integration ─────────────────────────────────────────────────

def run_nightly_exit_optimization(mfe_mae_path: str = "/app/data/mfe_mae_analysis.json",
                                  output_dir: str = "/app/data/claude/reviews") -> Dict:
    """Nightly exit optimisation step.

    Reads MFE/MAE data, optimises per-strategy exit params,
    scores exit quality, writes recommendations.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load MFE/MAE data
    mfe_path = Path(mfe_mae_path)
    if not mfe_path.exists():
        return {"status": "skipped", "reason": "No MFE/MAE data file"}

    try:
        with open(str(mfe_path)) as f:
            all_trades = json.load(f)
    except Exception as e:
        return {"status": "error", "reason": f"Failed to load MFE/MAE: {e}"}

    if not isinstance(all_trades, list):
        return {"status": "error", "reason": "MFE/MAE data not a list"}

    # Group trades by strategy
    by_strategy: Dict[str, List[Dict]] = {}
    for t in all_trades:
        strat = t.get("strategy", "unknown")
        by_strategy.setdefault(strat, []).append(t)

    # Per-strategy optimisation
    optimisations = {}
    quality_scores = []
    grade_counts = {"A": 0, "B": 0, "C": 0, "D": 0}

    for strat_name, trades in by_strategy.items():
        # Optimise parameters
        opt = optimise_exit_params(strat_name, trades)
        if opt is not None:
            optimisations[strat_name] = opt.to_dict()

        # Score exit quality
        for t in trades[-50:]:  # Last 50 trades only for efficiency
            qs = score_exit_quality(t)
            quality_scores.append(qs.to_dict())
            grade_counts[qs.grade] = grade_counts.get(qs.grade, 0) + 1

    # Summary
    all_eff = [t.get("exit_efficiency", 0) for t in all_trades
               if t.get("exit_efficiency") is not None and np.isfinite(t.get("exit_efficiency", float("nan")))]
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_trades": len(all_trades),
        "strategies_optimised": list(optimisations.keys()),
        "mean_exit_efficiency": round(float(np.mean(all_eff)), 3) if all_eff else 0,
        "median_exit_efficiency": round(float(np.median(all_eff)), 3) if all_eff else 0,
        "grade_distribution": grade_counts,
        "optimisations": optimisations,
    }

    # Save
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with open(str(out_dir / f"exit_optimization_{today}.json"), "w") as f:
            json.dump(summary, f, indent=2, default=str)
    except Exception as e:
        log.warning("Failed to save exit optimisation: %s", e)

    return summary


# ── Helpers ─────────────────────────────────────────────────────────────

def _clamp_change(old_val: float, new_val: float, max_pct: float) -> float:
    """Clamp a parameter change to max_pct of the old value (20% guardrail)."""
    if old_val == 0:
        return new_val
    max_delta = abs(old_val) * max_pct
    delta = new_val - old_val
    clamped_delta = max(-max_delta, min(max_delta, delta))
    return old_val + clamped_delta
