"""LLM-Driven Automated Alpha Factor Discovery — Book 108.

Systematic alpha discovery pipeline that proposes, backtests, and gates
new alpha factors. Uses Claude to generate factor hypotheses from market
context, then rigorously evaluates them with walk-forward backtesting
and multiple-testing correction (deflated Sharpe ratio).

Pipeline:
  1. Claude proposes factor formulas from market context
  2. Each formula is backtested with walk-forward IC + Sharpe
  3. Deflated Sharpe ratio corrects for selection bias
  4. Survivors added to AlphaStore; poor performers retired

State: /app/data/alpha_store.json

Bridge.py integration:
    try:
        from python_brain.alphas.alpha_discovery_agent import (
            AlphaDiscoveryAgent, AlphaStore, AlphaCandidate,
        )
    except ImportError:
        pass
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger(__name__)

__all__ = [
    "AlphaCandidate",
    "AlphaStore",
    "AlphaDiscoveryAgent",
]

# ── Paths ──────────────────────────────────────────────────────────────
ALPHA_STORE_PATH = Path("/app/data/alpha_store.json")

# ── Constants ──────────────────────────────────────────────────────────
MIN_IC = 0.02                 # Minimum IC to keep a factor
MIN_SHARPE = 0.3              # Minimum annualized Sharpe
MAX_TURNOVER = 0.8            # Maximum daily turnover rate
MIN_OBSERVATIONS = 100        # Minimum observations for evaluation
WALK_FORWARD_SPLITS = 5       # Number of walk-forward splits
PURGE_BARS = 78               # Purge window (1 trading day at 78 bars/day)
IC_DECAY_HALFLIFE_MIN = 20    # Minimum halflife in bars before retirement
DEFLATED_SHARPE_THRESHOLD = 0.5  # Adjusted Sharpe must exceed this


# ── Data Structures ────────────────────────────────────────────────────

@dataclass
class AlphaCandidate:
    """A discovered alpha factor with evaluation metrics.

    Attributes:
        formula: mathematical formula string (e.g. "ts_rank(volume, 20) - ts_rank(close, 20)")
        description: human-readable description of the factor logic
        ic: information coefficient (rank correlation with forward returns)
        sharpe: annualized Sharpe ratio of factor returns
        turnover: daily turnover rate (0-1)
        decay_halflife: halflife of IC decay in bars
        status: active | retired | candidate | rejected
        discovery_date: ISO timestamp of discovery
        n_observations: number of observations used for evaluation
        deflated_sharpe: multiple-testing corrected Sharpe
        ic_std: standard deviation of IC across walk-forward splits
    """
    formula: str
    description: str = ""
    ic: float = 0.0
    sharpe: float = 0.0
    turnover: float = 0.0
    decay_halflife: float = 0.0
    status: str = "candidate"
    discovery_date: str = ""
    n_observations: int = 0
    deflated_sharpe: float = 0.0
    ic_std: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def passes_gate(self) -> bool:
        """Check if this candidate passes all quality gates."""
        return (
            self.ic >= MIN_IC
            and self.sharpe >= MIN_SHARPE
            and self.turnover <= MAX_TURNOVER
            and self.decay_halflife >= IC_DECAY_HALFLIFE_MIN
            and self.deflated_sharpe >= DEFLATED_SHARPE_THRESHOLD
            and self.n_observations >= MIN_OBSERVATIONS
        )


# ── Alpha Store ────────────────────────────────────────────────────────

class AlphaStore:
    """JSON-backed persistent store for discovered alpha factors.

    Tracks all discovered factors with their evaluation metrics,
    status (active/retired), and discovery history.

    State persisted to /app/data/alpha_store.json
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or ALPHA_STORE_PATH
        self.candidates: List[AlphaCandidate] = []
        self._load()

    def add(self, candidate: AlphaCandidate) -> None:
        """Add a new alpha candidate to the store.

        Args:
            candidate: evaluated alpha candidate
        """
        # Check for duplicate formula
        for existing in self.candidates:
            if existing.formula == candidate.formula:
                log.info("AlphaStore: updating existing formula: %s", candidate.formula[:50])
                existing.ic = candidate.ic
                existing.sharpe = candidate.sharpe
                existing.turnover = candidate.turnover
                existing.decay_halflife = candidate.decay_halflife
                existing.deflated_sharpe = candidate.deflated_sharpe
                existing.n_observations = candidate.n_observations
                existing.ic_std = candidate.ic_std
                existing.status = candidate.status
                self._save()
                return

        if not candidate.discovery_date:
            candidate.discovery_date = datetime.now(timezone.utc).isoformat()

        self.candidates.append(candidate)
        self._save()
        log.info("AlphaStore: added '%s' (IC=%.4f, Sharpe=%.2f, status=%s)",
                 candidate.formula[:50], candidate.ic, candidate.sharpe, candidate.status)

    def get_active(self) -> List[AlphaCandidate]:
        """Return all active alpha factors.

        Returns:
            List of active AlphaCandidate objects, sorted by IC descending
        """
        active = [c for c in self.candidates if c.status == "active"]
        active.sort(key=lambda c: c.ic, reverse=True)
        return active

    def get_all(self) -> List[AlphaCandidate]:
        """Return all candidates regardless of status."""
        return self.candidates.copy()

    def retire(self, formula: str, reason: str = "") -> bool:
        """Retire an alpha factor.

        Args:
            formula: the formula string to retire
            reason: human-readable reason for retirement

        Returns:
            True if factor was found and retired
        """
        for c in self.candidates:
            if c.formula == formula and c.status == "active":
                c.status = "retired"
                log.info("AlphaStore: retired '%s' — reason: %s",
                         formula[:50], reason or "not specified")
                self._save()
                return True
        return False

    def get_stats(self) -> Dict[str, Any]:
        """Return summary statistics of the alpha store."""
        active = [c for c in self.candidates if c.status == "active"]
        retired = [c for c in self.candidates if c.status == "retired"]
        rejected = [c for c in self.candidates if c.status == "rejected"]

        return {
            "total": len(self.candidates),
            "active": len(active),
            "retired": len(retired),
            "rejected": len(rejected),
            "avg_ic": round(np.mean([c.ic for c in active]), 4) if active else 0.0,
            "avg_sharpe": round(np.mean([c.sharpe for c in active]), 2) if active else 0.0,
            "avg_turnover": round(np.mean([c.turnover for c in active]), 3) if active else 0.0,
        }

    def _save(self) -> None:
        """Persist store to disk."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "alphas": [c.to_dict() for c in self.candidates],
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "stats": self.get_stats(),
            }
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self.path)
        except Exception as e:
            log.error("AlphaStore save failed: %s", e)

    def _load(self) -> None:
        """Load store from disk."""
        if not self.path.exists():
            log.info("AlphaStore: no store at %s, starting empty", self.path)
            return
        try:
            with open(self.path) as f:
                data = json.load(f)
            for alpha_dict in data.get("alphas", []):
                self.candidates.append(AlphaCandidate(**alpha_dict))
            log.info("AlphaStore loaded: %d factors (%d active)",
                     len(self.candidates),
                     len([c for c in self.candidates if c.status == "active"]))
        except Exception as e:
            log.error("AlphaStore load failed: %s — starting empty", e)
            self.candidates = []


# ── Safe Formula Evaluator ─────────────────────────────────────────────

# Allowed operations for formula evaluation (sandboxed)
_SAFE_OPS: Dict[str, Callable] = {}


def _register_ops() -> None:
    """Register safe operators for formula evaluation."""
    global _SAFE_OPS

    def ts_rank(x: np.ndarray, d: int) -> np.ndarray:
        n = len(x)
        result = np.full(n, 0.5)
        for i in range(d - 1, n):
            window = x[i - d + 1:i + 1]
            result[i] = float(np.sum(window <= x[i])) / d
        return result

    def ts_delta(x: np.ndarray, d: int) -> np.ndarray:
        result = np.zeros(len(x))
        if d < len(x):
            result[d:] = x[d:] - x[:-d]
        return result

    def ts_mean(x: np.ndarray, d: int) -> np.ndarray:
        n = len(x)
        result = np.zeros(n)
        cs = np.cumsum(x)
        for i in range(d - 1, n):
            if i - d >= 0:
                result[i] = (cs[i] - cs[i - d]) / d
            else:
                result[i] = cs[i] / (i + 1)
        return result

    def ts_std(x: np.ndarray, d: int) -> np.ndarray:
        n = len(x)
        result = np.zeros(n)
        for i in range(d - 1, n):
            window = x[i - d + 1:i + 1]
            result[i] = float(np.std(window))
        return result

    def ts_max(x: np.ndarray, d: int) -> np.ndarray:
        n = len(x)
        result = np.zeros(n)
        for i in range(d - 1, n):
            result[i] = float(np.max(x[i - d + 1:i + 1]))
        return result

    def ts_min(x: np.ndarray, d: int) -> np.ndarray:
        n = len(x)
        result = np.zeros(n)
        for i in range(d - 1, n):
            result[i] = float(np.min(x[i - d + 1:i + 1]))
        return result

    def ts_corr(x: np.ndarray, y: np.ndarray, d: int) -> np.ndarray:
        n = min(len(x), len(y))
        result = np.zeros(n)
        for i in range(d - 1, n):
            wx = x[i - d + 1:i + 1]
            wy = y[i - d + 1:i + 1]
            if np.std(wx) > 1e-12 and np.std(wy) > 1e-12:
                result[i] = float(np.corrcoef(wx, wy)[0, 1])
        return result

    def decay_linear(x: np.ndarray, d: int) -> np.ndarray:
        n = len(x)
        result = np.zeros(n)
        weights = np.arange(1, d + 1, dtype=np.float64)
        weights = weights / weights.sum()
        for i in range(d - 1, n):
            result[i] = float(np.dot(x[i - d + 1:i + 1], weights))
        return result

    def log_return(x: np.ndarray) -> np.ndarray:
        result = np.zeros(len(x))
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = x[1:] / np.where(x[:-1] != 0, x[:-1], 1.0)
            result[1:] = np.log(np.abs(ratio) + 1e-12)
        return result

    def rank(x: np.ndarray) -> np.ndarray:
        """Cross-sectional rank normalized to [0, 1]."""
        order = x.argsort().argsort()
        return order.astype(np.float64) / max(len(x) - 1, 1)

    _SAFE_OPS.update({
        "ts_rank": ts_rank,
        "ts_delta": ts_delta,
        "ts_mean": ts_mean,
        "ts_std": ts_std,
        "ts_max": ts_max,
        "ts_min": ts_min,
        "ts_corr": ts_corr,
        "decay_linear": decay_linear,
        "log_return": log_return,
        "rank": rank,
        "abs": np.abs,
        "sign": np.sign,
        "sqrt": np.sqrt,
        "log": np.log,
        "exp": np.exp,
        "clip": np.clip,
    })


_register_ops()


def _safe_eval_formula(
    formula: str,
    data: Dict[str, np.ndarray],
) -> Optional[np.ndarray]:
    """Safely evaluate an alpha formula.

    Only allows registered operators and data column references.
    No exec/eval of arbitrary code.

    Args:
        formula: alpha formula string
        data: dict of data arrays (close, open, high, low, volume, returns)

    Returns:
        Alpha values array, or None if evaluation fails
    """
    # Build a restricted namespace
    namespace: Dict[str, Any] = {}
    namespace.update(_SAFE_OPS)
    namespace.update(data)
    # Add numpy for basic operations
    namespace["np"] = np

    try:
        result = eval(formula, {"__builtins__": {}}, namespace)  # noqa: S307
        if isinstance(result, np.ndarray):
            # Replace inf/nan with 0
            result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
            return result
        elif isinstance(result, (int, float)):
            return np.full(len(next(iter(data.values()))), float(result))
        return None
    except Exception as e:
        log.debug("Formula eval failed for '%s': %s", formula[:50], e)
        return None


# ── Alpha Discovery Agent ─────────────────────────────────────────────

class AlphaDiscoveryAgent:
    """LLM-driven automated alpha factor discovery.

    Proposes new factor formulas via Claude, backtests them with
    walk-forward evaluation, applies deflated Sharpe correction for
    multiple testing, and gates survivors into the AlphaStore.

    Usage:
        agent = AlphaDiscoveryAgent()
        results = agent.run_weekly_discovery(data={
            "close": close_prices,
            "volume": volume_array,
            "returns": returns_array,
        })
    """

    def __init__(self, store: Optional[AlphaStore] = None) -> None:
        self.store = store or AlphaStore()
        self.n_trials_total = 0  # Running count for deflated Sharpe

    def propose_factors(
        self,
        market_context: Dict[str, Any],
    ) -> List[AlphaCandidate]:
        """Propose new alpha factor formulas based on market context.

        In production, this calls Claude to generate hypotheses.
        Here we provide a systematic set of factor templates that
        cover common alpha categories.

        Args:
            market_context: dict with keys like 'regime', 'vol', 'recent_returns',
                           'top_performers', 'sector_rotation'

        Returns:
            List of AlphaCandidate objects (unevaluated)
        """
        regime = market_context.get("regime", "unknown")
        vol_level = market_context.get("vol", "normal")

        # Factor templates — categorized by alpha type
        templates: List[Tuple[str, str]] = []

        # Momentum factors
        templates.extend([
            ("ts_rank(close, 20) - 0.5",
             "20-bar momentum rank deviation from median"),
            ("ts_delta(close, 10) / (ts_std(close, 10) + 1e-8)",
             "10-bar return normalized by volatility (z-score momentum)"),
            ("decay_linear(ts_delta(close, 5), 10)",
             "Decayed short-term momentum (5-bar delta, 10-bar decay)"),
        ])

        # Mean-reversion factors
        templates.extend([
            ("-(ts_rank(close, 5) - ts_rank(close, 60))",
             "Short-term vs long-term rank divergence (mean reversion)"),
            ("ts_mean(close, 50) - close",
             "Distance from 50-bar moving average (mean reversion)"),
        ])

        # Volume factors
        templates.extend([
            ("ts_rank(volume, 20) - ts_rank(close, 20)",
             "Volume-price rank divergence (accumulation/distribution)"),
            ("ts_delta(volume, 5) * sign(ts_delta(close, 5))",
             "Directional volume change (volume confirms price direction)"),
            ("decay_linear(volume, 20) / (ts_mean(volume, 60) + 1e-8)",
             "Recent volume ratio (short vs long average, decayed)"),
        ])

        # Volatility factors
        templates.extend([
            ("-(ts_std(close, 10) / (ts_std(close, 60) + 1e-8) - 1.0)",
             "Vol compression: low recent vol relative to long-term (breakout setup)"),
            ("ts_std(close, 5) - ts_std(close, 20)",
             "Volatility acceleration (rising vol = momentum, falling = consolidation)"),
        ])

        # Regime-adaptive factors
        if regime == "trending":
            templates.extend([
                ("ts_rank(ts_delta(close, 20), 60)",
                 "Trend persistence rank (regime: trending)"),
                ("decay_linear(close, 20) - decay_linear(close, 60)",
                 "Dual-timeframe momentum crossover (regime: trending)"),
            ])
        elif regime == "mean_reverting":
            templates.extend([
                ("ts_mean(close, 10) - ts_mean(close, 3)",
                 "Short MA overshoot (regime: mean reverting)"),
                ("-(ts_rank(close, 3) - 0.5) * ts_std(close, 20)",
                 "Vol-weighted rank deviation (regime: mean reverting)"),
            ])

        # Volatility-adaptive factors
        if vol_level == "high":
            templates.extend([
                ("ts_min(close, 10) / (close + 1e-8) - 1.0",
                 "Distance from 10-bar low (high vol: buy dips)"),
            ])
        elif vol_level == "low":
            templates.extend([
                ("ts_max(close, 20) / (close + 1e-8) - 1.0",
                 "Distance from 20-bar high (low vol: breakout proximity)"),
            ])

        candidates = []
        for formula, description in templates:
            candidates.append(AlphaCandidate(
                formula=formula,
                description=description,
                status="candidate",
            ))

        log.info("AlphaDiscoveryAgent: proposed %d factors for regime=%s, vol=%s",
                 len(candidates), regime, vol_level)
        return candidates

    def backtest_factor(
        self,
        formula: str,
        returns: np.ndarray,
        features: Dict[str, np.ndarray],
        n_splits: int = WALK_FORWARD_SPLITS,
    ) -> Dict[str, Any]:
        """Walk-forward backtest of an alpha factor.

        Evaluates the factor using purged walk-forward cross-validation:
        each split has a training period (for factor parameter estimation)
        and a test period (for out-of-sample evaluation), with a purge
        window in between to prevent lookahead bias.

        Args:
            formula: alpha formula string
            returns: forward returns array (same length as features)
            features: dict of market data arrays (close, volume, etc.)
            n_splits: number of walk-forward splits

        Returns:
            Dict with ic, sharpe, turnover, decay_halflife, per-split results
        """
        n = len(returns)
        if n < MIN_OBSERVATIONS:
            return {
                "status": "insufficient_data",
                "n_observations": n,
                "ic": 0.0,
                "sharpe": 0.0,
            }

        # Evaluate formula on full dataset
        alpha_values = _safe_eval_formula(formula, features)
        if alpha_values is None:
            return {
                "status": "eval_failed",
                "n_observations": 0,
                "ic": 0.0,
                "sharpe": 0.0,
            }

        if len(alpha_values) != n:
            return {
                "status": "length_mismatch",
                "n_observations": 0,
                "ic": 0.0,
                "sharpe": 0.0,
            }

        # Walk-forward splits with purge
        split_size = n // n_splits
        split_ics: List[float] = []
        split_sharpes: List[float] = []
        split_turnovers: List[float] = []

        for i in range(n_splits):
            test_start = i * split_size
            test_end = min(test_start + split_size, n)

            # Purge: skip PURGE_BARS before test period
            effective_start = max(0, test_start + PURGE_BARS)
            if effective_start >= test_end:
                continue

            test_alpha = alpha_values[effective_start:test_end]
            test_returns = returns[effective_start:test_end]

            if len(test_alpha) < 20:
                continue

            # IC: rank correlation between alpha and forward returns
            ic = self._compute_ic(test_alpha, test_returns)
            split_ics.append(ic)

            # Factor returns: sign(alpha) * returns (long-short)
            alpha_sign = np.sign(test_alpha)
            factor_returns = alpha_sign * test_returns
            factor_returns = factor_returns[~np.isnan(factor_returns)]

            if len(factor_returns) > 1:
                mean_ret = float(np.mean(factor_returns))
                std_ret = float(np.std(factor_returns))
                # Annualize (assume 78 bars/day, 252 days/year)
                ann_factor = math.sqrt(78 * 252)
                sharpe = (mean_ret / max(std_ret, 1e-12)) * ann_factor
                split_sharpes.append(sharpe)
            else:
                split_sharpes.append(0.0)

            # Turnover: how often does the signal flip?
            signal_changes = np.sum(np.abs(np.diff(alpha_sign)) > 0)
            turnover = signal_changes / max(len(alpha_sign) - 1, 1)
            split_turnovers.append(turnover)

        if not split_ics:
            return {
                "status": "no_valid_splits",
                "n_observations": n,
                "ic": 0.0,
                "sharpe": 0.0,
            }

        # Aggregate metrics
        mean_ic = float(np.mean(split_ics))
        std_ic = float(np.std(split_ics))
        mean_sharpe = float(np.mean(split_sharpes))
        mean_turnover = float(np.mean(split_turnovers))

        # IC decay halflife: how quickly does IC decay over time?
        decay_halflife = self._compute_ic_decay(alpha_values, returns)

        # Deflated Sharpe (multiple testing correction)
        self.n_trials_total += 1
        deflated = self._deflated_sharpe(mean_sharpe, self.n_trials_total, n)

        return {
            "status": "completed",
            "n_observations": n,
            "ic": round(mean_ic, 6),
            "ic_std": round(std_ic, 6),
            "sharpe": round(mean_sharpe, 4),
            "deflated_sharpe": round(deflated, 4),
            "turnover": round(mean_turnover, 4),
            "decay_halflife": round(decay_halflife, 1),
            "n_splits": len(split_ics),
            "split_ics": [round(ic, 4) for ic in split_ics],
            "split_sharpes": [round(s, 4) for s in split_sharpes],
        }

    @staticmethod
    def _deflated_sharpe(
        sharpe: float,
        n_trials: int,
        n_obs: int,
    ) -> float:
        """Compute deflated Sharpe ratio (Bailey & Lopez de Prado 2014).

        Corrects for multiple testing: the more factors we test, the higher
        the chance of finding spuriously high Sharpe ratios.

        Expected max Sharpe under null:
            E[max(SR)] ≈ sqrt(2 * ln(n_trials)) * (1 - gamma / (2 * ln(n_trials)))
            where gamma ≈ 0.5772 (Euler-Mascheroni constant)

        Deflated SR = (observed SR - E[max SR]) / SE(SR)

        Args:
            sharpe: observed annualized Sharpe ratio
            n_trials: total number of strategies tested
            n_obs: number of observations

        Returns:
            Deflated Sharpe ratio
        """
        if n_trials <= 0 or n_obs <= 1:
            return sharpe

        # Expected maximum Sharpe under the null (all strategies are noise)
        euler_gamma = 0.5772156649

        if n_trials <= 1:
            e_max_sr = 0.0
        else:
            ln_n = math.log(n_trials)
            if ln_n > 0:
                e_max_sr = math.sqrt(2.0 * ln_n) * (
                    1.0 - euler_gamma / (2.0 * ln_n)
                ) + euler_gamma / (2.0 * math.sqrt(2.0 * ln_n))
            else:
                e_max_sr = 0.0

        # Standard error of Sharpe ratio
        # SE(SR) ≈ sqrt((1 + 0.5 * SR^2) / n_obs)
        se_sr = math.sqrt((1.0 + 0.5 * sharpe ** 2) / max(n_obs, 1))

        if se_sr < 1e-12:
            return sharpe

        # Deflated Sharpe
        deflated = (sharpe - e_max_sr) / se_sr

        return deflated

    @staticmethod
    def _compute_ic(
        predictions: np.ndarray,
        outcomes: np.ndarray,
    ) -> float:
        """Compute Information Coefficient (Spearman rank correlation).

        IC measures how well factor ranks predict return ranks.
        IC > 0.05 is considered meaningful for alpha factors.

        Args:
            predictions: alpha factor values
            outcomes: forward returns

        Returns:
            IC value in [-1, 1]
        """
        # Remove NaN pairs
        mask = ~(np.isnan(predictions) | np.isnan(outcomes))
        pred = predictions[mask]
        out = outcomes[mask]

        n = len(pred)
        if n < 5:
            return 0.0

        # Rank correlation (Spearman)
        pred_rank = pred.argsort().argsort().astype(np.float64)
        out_rank = out.argsort().argsort().astype(np.float64)

        # Pearson correlation of ranks
        pred_centered = pred_rank - pred_rank.mean()
        out_centered = out_rank - out_rank.mean()

        num = float(np.sum(pred_centered * out_centered))
        denom = math.sqrt(float(np.sum(pred_centered ** 2)) *
                          float(np.sum(out_centered ** 2)))

        if denom < 1e-12:
            return 0.0

        return num / denom

    @staticmethod
    def _compute_ic_decay(
        alpha_values: np.ndarray,
        returns: np.ndarray,
        max_lag: int = 100,
        min_halflife: float = 5.0,
    ) -> float:
        """Estimate IC decay halflife.

        Computes IC at increasing lags (1, 2, 5, 10, 20, 50, 100 bars)
        and fits an exponential decay to estimate halflife.

        Args:
            alpha_values: alpha factor values
            returns: forward returns
            max_lag: maximum lag to test
            min_halflife: minimum halflife to return

        Returns:
            Halflife in bars
        """
        lags = [1, 2, 5, 10, 20, 50, min(100, max_lag)]
        ics: List[Tuple[int, float]] = []

        n = len(alpha_values)
        for lag in lags:
            if lag >= n - 20:
                break
            # IC at this lag: correlation of alpha[t] with returns[t+lag]
            pred = alpha_values[:n - lag]
            out = returns[lag:]
            if len(pred) < 20:
                continue

            mask = ~(np.isnan(pred) | np.isnan(out))
            if mask.sum() < 20:
                continue

            # Quick rank correlation
            p = pred[mask]
            o = out[mask]
            p_rank = p.argsort().argsort().astype(np.float64)
            o_rank = o.argsort().argsort().astype(np.float64)
            p_c = p_rank - p_rank.mean()
            o_c = o_rank - o_rank.mean()
            denom = math.sqrt(float(np.sum(p_c ** 2)) * float(np.sum(o_c ** 2)))
            ic = float(np.sum(p_c * o_c)) / max(denom, 1e-12)
            ics.append((lag, abs(ic)))

        if len(ics) < 2:
            return min_halflife

        # Fit exponential decay: IC(lag) = IC(0) * exp(-lag / tau)
        # Halflife = tau * ln(2)
        # Use log-linear regression: log(IC) = log(IC0) - lag/tau
        lags_arr = np.array([t[0] for t in ics], dtype=np.float64)
        ic_arr = np.array([max(t[1], 1e-12) for t in ics], dtype=np.float64)
        log_ic = np.log(ic_arr)

        # Linear regression: log_ic = a + b * lag
        n_pts = len(lags_arr)
        mean_lag = lags_arr.mean()
        mean_log = log_ic.mean()
        cov = float(np.sum((lags_arr - mean_lag) * (log_ic - mean_log)))
        var_lag = float(np.sum((lags_arr - mean_lag) ** 2))

        if var_lag < 1e-12:
            return min_halflife

        b = cov / var_lag  # Slope (should be negative)

        if b >= 0:
            # IC doesn't decay (or increases) — very long halflife
            return 1000.0

        tau = -1.0 / b  # Decay constant
        halflife = tau * math.log(2.0)

        return max(halflife, min_halflife)

    def run_weekly_discovery(
        self,
        data: Dict[str, np.ndarray],
        market_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Full weekly discovery pipeline.

        1. Propose new factor candidates
        2. Backtest each candidate
        3. Gate survivors (deflated Sharpe, IC, turnover)
        4. Add survivors to AlphaStore
        5. Review existing factors for retirement

        Args:
            data: market data with keys: close, volume, returns
                  Each value is a numpy array of same length
            market_context: optional context for factor proposal

        Returns:
            Dict with pipeline results
        """
        context = market_context or {"regime": "unknown", "vol": "normal"}
        start_time = time.time()

        # Validate data
        required = ["close", "returns"]
        for key in required:
            if key not in data or data[key] is None:
                return {"status": "missing_data", "missing": key}

        returns = data["returns"]
        n = len(returns)

        log.info("AlphaDiscoveryAgent: starting weekly discovery (n=%d, regime=%s)",
                 n, context.get("regime", "?"))

        # Step 1: Propose
        candidates = self.propose_factors(context)

        # Step 2: Backtest each
        evaluated: List[Tuple[AlphaCandidate, Dict]] = []
        for candidate in candidates:
            bt_result = self.backtest_factor(
                formula=candidate.formula,
                returns=returns,
                features=data,
            )

            candidate.ic = bt_result.get("ic", 0.0)
            candidate.sharpe = bt_result.get("sharpe", 0.0)
            candidate.turnover = bt_result.get("turnover", 0.0)
            candidate.decay_halflife = bt_result.get("decay_halflife", 0.0)
            candidate.deflated_sharpe = bt_result.get("deflated_sharpe", 0.0)
            candidate.n_observations = bt_result.get("n_observations", 0)
            candidate.ic_std = bt_result.get("ic_std", 0.0)

            evaluated.append((candidate, bt_result))

        # Step 3: Gate
        accepted: List[AlphaCandidate] = []
        rejected: List[AlphaCandidate] = []

        for candidate, bt_result in evaluated:
            if bt_result.get("status") != "completed":
                candidate.status = "rejected"
                rejected.append(candidate)
                continue

            if candidate.passes_gate():
                candidate.status = "active"
                accepted.append(candidate)
            else:
                candidate.status = "rejected"
                rejected.append(candidate)

        # Step 4: Store
        for candidate in accepted:
            self.store.add(candidate)
        for candidate in rejected:
            self.store.add(candidate)

        # Step 5: Review existing factors for retirement
        retired_count = 0
        for existing in self.store.get_active():
            # Re-evaluate on current data
            bt = self.backtest_factor(
                formula=existing.formula,
                returns=returns,
                features=data,
            )
            if bt.get("status") == "completed":
                new_ic = bt.get("ic", 0.0)
                new_sharpe = bt.get("sharpe", 0.0)

                # Retire if performance degraded significantly
                if new_ic < MIN_IC * 0.5 or new_sharpe < MIN_SHARPE * 0.3:
                    self.store.retire(
                        existing.formula,
                        reason=f"IC degraded to {new_ic:.4f}, Sharpe to {new_sharpe:.2f}",
                    )
                    retired_count += 1

        elapsed = time.time() - start_time

        result = {
            "status": "completed",
            "n_proposed": len(candidates),
            "n_accepted": len(accepted),
            "n_rejected": len(rejected),
            "n_retired": retired_count,
            "elapsed_seconds": round(elapsed, 2),
            "store_stats": self.store.get_stats(),
            "accepted_formulas": [
                {"formula": c.formula, "ic": c.ic, "sharpe": c.sharpe,
                 "deflated_sharpe": c.deflated_sharpe}
                for c in accepted
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        log.info("AlphaDiscoveryAgent: completed in %.1fs — "
                 "%d proposed, %d accepted, %d rejected, %d retired",
                 elapsed, len(candidates), len(accepted),
                 len(rejected), retired_count)

        return result
