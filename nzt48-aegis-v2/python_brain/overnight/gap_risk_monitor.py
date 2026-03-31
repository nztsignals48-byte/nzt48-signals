"""Overnight Gap Risk Monitoring for Leveraged ETPs — Book 148.

Quantitative gap risk assessment for leveraged products held overnight.
Extends the basic overnight risk module (Book 40/148/186) with
probabilistic gap analysis using empirical + normal mixture distributions.

Key features:
  - Empirical gap distribution fitting from historical data
  - VIX-conditional gap probability estimation
  - Per-position expected loss calculation accounting for leverage
  - Overnight VaR computation
  - Nightly gap risk report for pipeline integration

Overnight gaps in 3x leveraged ETPs can wipe 15-20% in a single
gap-down. This module quantifies that risk and recommends flattening
when gap probability exceeds thresholds.

State persisted to /app/data/gap_risk/.

Usage:
    from python_brain.overnight.gap_risk_monitor import (
        GapRiskMonitor, GapRiskConfig, GapDistribution,
    )
    config = GapRiskConfig()
    monitor = GapRiskMonitor(config)
    risk = monitor.assess_overnight_risk(positions, vix=22.5)
    should_flat = monitor.should_flatten_before_close(position, gap_prob=0.15)
    overnight_var = monitor.compute_overnight_var(positions, alpha=0.05)
    report = monitor.run_nightly_gap_report()
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("gap_risk_monitor")

__all__ = [
    "GapRiskConfig",
    "GapDistribution",
    "GapRiskMonitor",
]

# ── Constants ──────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/gap_risk")
HISTORICAL_GAPS_PATH = Path("/app/data/gap_risk/historical_gaps.npy")
NIGHTLY_REPORT_PATH = Path("/app/data/gap_risk/nightly_report.json")

# Default leverage multipliers for common LSE leveraged ETPs
DEFAULT_LEVERAGE_MAP: Dict[str, float] = {
    "3USL.L": 3.0, "QQQ3.L": 3.0, "NVD3.L": 3.0, "TSL3.L": 3.0,
    "AMD3.L": 3.0, "MSF3.L": 3.0, "GOO3.L": 3.0, "AAP3.L": 3.0,
    "3LUS.L": 3.0, "MET3.L": 3.0, "GPT3.L": 3.0, "TSM3.L": 3.0,
    "MS23.L": 3.0, "COI3.L": 3.0, "3LAP.L": 3.0, "3LMS.L": 3.0,
    "QQQ5.L": 5.0, "5SPY.L": 5.0,
    "VIXL.L": 1.0,
    # Inverse products
    "QQQS.L": -3.0, "3USS.L": -3.0, "NV3S.L": -3.0,
    "TS3S.L": -3.0, "3STS.L": -3.0,
}


# ── Config ─────────────────────────────────────────────────────────────

@dataclass
class GapRiskConfig:
    """Configuration for overnight gap risk monitoring.

    Attributes:
        max_overnight_exposure_pct: Max total overnight exposure as % of equity.
        gap_alert_threshold_pct: Gap size (%) that triggers an alert.
        leverage_multiplier_map: Ticker -> leverage multiplier mapping.
        vix_scaling_factor: How much VIX amplifies gap estimates.
        flatten_probability_threshold: Gap probability above which to flatten.
        max_acceptable_loss_pct: Max acceptable overnight loss (% equity).
    """
    max_overnight_exposure_pct: float = 0.25
    gap_alert_threshold_pct: float = 2.0
    leverage_multiplier_map: Dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_LEVERAGE_MAP)
    )
    vix_scaling_factor: float = 0.15
    flatten_probability_threshold: float = 0.12
    max_acceptable_loss_pct: float = 3.0


# ── Gap Distribution ──────────────────────────────────────────────────

class GapDistribution:
    """Empirical + normal mixture distribution for overnight gaps.

    Fits a mixture of empirical (histogram) and normal components
    to historical gap data. The empirical component captures fat tails
    while the normal provides smooth interpolation.
    """

    def __init__(self, mixture_weight: float = 0.6, seed: int = 42):
        """Initialize gap distribution.

        Args:
            mixture_weight: Weight on empirical component (1 - weight for normal).
            seed: Random seed.
        """
        self._mix_w = mixture_weight  # Weight on empirical
        self._rng = np.random.default_rng(seed)

        # Fitted parameters
        self._empirical_gaps: Optional[np.ndarray] = None
        self._mu: float = 0.0
        self._sigma: float = 1.0
        self._fitted: bool = False
        self._n_samples: int = 0

    def fit(self, historical_gaps: np.ndarray) -> None:
        """Fit the mixture distribution to historical gap data.

        Args:
            historical_gaps: Array of historical overnight gap percentages.
                             Positive = gap up, negative = gap down.
        """
        gaps = np.asarray(historical_gaps, dtype=np.float64).ravel()
        if len(gaps) < 5:
            log.warning("Insufficient gap data for fitting: %d samples", len(gaps))
            self._mu = 0.0
            self._sigma = 2.0  # Conservative default
            self._empirical_gaps = gaps if len(gaps) > 0 else np.array([0.0])
            self._fitted = True
            self._n_samples = len(gaps)
            return

        self._empirical_gaps = gaps.copy()
        self._mu = float(np.mean(gaps))
        self._sigma = float(np.std(gaps))
        if self._sigma < 1e-6:
            self._sigma = 1.0  # Prevent degenerate distribution

        self._fitted = True
        self._n_samples = len(gaps)

        log.info("GapDistribution fitted: n=%d, mu=%.4f%%, sigma=%.4f%%, "
                 "min=%.2f%%, max=%.2f%%",
                 len(gaps), self._mu, self._sigma,
                 float(np.min(gaps)), float(np.max(gaps)))

    def probability_of_gap_exceeding(self, threshold_pct: float) -> float:
        """Estimate probability that |gap| exceeds threshold.

        Args:
            threshold_pct: Gap magnitude threshold in percent.

        Returns:
            Probability P(|gap| > threshold).
        """
        if not self._fitted:
            log.warning("GapDistribution not fitted — returning conservative estimate")
            return 0.10

        # Empirical estimate
        if self._empirical_gaps is not None and len(self._empirical_gaps) > 0:
            emp_prob = float(
                np.mean(np.abs(self._empirical_gaps) > threshold_pct)
            )
        else:
            emp_prob = 0.0

        # Normal estimate: P(|X| > t) = 2 * (1 - Phi(t/sigma - mu/sigma))
        if self._sigma > 1e-8:
            z_up = (threshold_pct - self._mu) / self._sigma
            z_down = (-threshold_pct - self._mu) / self._sigma
            # Using complementary error function for standard normal CDF
            norm_prob = 0.5 * math.erfc(z_up / math.sqrt(2.0)) + \
                        0.5 * (1.0 - math.erfc(-z_down / math.sqrt(2.0)))
            norm_prob = max(norm_prob, 0.0)
        else:
            norm_prob = 0.0

        # Mixture
        prob = self._mix_w * emp_prob + (1.0 - self._mix_w) * norm_prob
        return float(np.clip(prob, 0.0, 1.0))

    def expected_loss(self, position_value: float, leverage: float) -> float:
        """Compute expected overnight loss from gap risk.

        Expected loss = E[|gap| * leverage] * position_value
        Only considers downside (gap moves against position).

        Args:
            position_value: Notional position value in GBP.
            leverage: Effective leverage multiplier.

        Returns:
            Expected loss in GBP (positive number).
        """
        if not self._fitted or self._empirical_gaps is None:
            # Conservative: assume 1% expected gap
            return abs(position_value * leverage * 0.01)

        # Expected absolute gap percentage
        gaps = self._empirical_gaps
        # Downside gaps (negative for long positions)
        downside = gaps[gaps < 0]
        if len(downside) == 0:
            expected_gap_pct = abs(self._mu) + self._sigma
        else:
            expected_gap_pct = float(np.mean(np.abs(downside)))

        # Leverage amplifies the gap
        effective_gap = expected_gap_pct * abs(leverage) / 100.0
        return abs(position_value * effective_gap)

    def sample(self, n: int = 1000) -> np.ndarray:
        """Sample from the fitted distribution.

        Args:
            n: Number of samples.

        Returns:
            Array of simulated gap percentages.
        """
        if not self._fitted:
            return self._rng.normal(0.0, 2.0, n)

        n_emp = int(n * self._mix_w)
        n_norm = n - n_emp

        samples = np.empty(n)
        if self._empirical_gaps is not None and len(self._empirical_gaps) > 0 and n_emp > 0:
            idx = self._rng.choice(len(self._empirical_gaps), size=n_emp, replace=True)
            samples[:n_emp] = self._empirical_gaps[idx]
        if n_norm > 0:
            samples[n_emp:] = self._rng.normal(self._mu, self._sigma, n_norm)

        self._rng.shuffle(samples)
        return samples

    @property
    def stats(self) -> Dict[str, Any]:
        """Distribution statistics."""
        result: Dict[str, Any] = {
            "fitted": self._fitted,
            "n_samples": self._n_samples,
            "mu": round(self._mu, 4),
            "sigma": round(self._sigma, 4),
        }
        if self._empirical_gaps is not None and len(self._empirical_gaps) > 0:
            result["empirical"] = {
                "min": round(float(np.min(self._empirical_gaps)), 2),
                "max": round(float(np.max(self._empirical_gaps)), 2),
                "p5": round(float(np.percentile(self._empirical_gaps, 5)), 2),
                "p95": round(float(np.percentile(self._empirical_gaps, 95)), 2),
            }
        return result


# ── Gap Risk Monitor ──────────────────────────────────────────────────

class GapRiskMonitor:
    """Overnight gap risk monitor for leveraged ETP portfolios.

    Assesses per-position and portfolio-level overnight gap risk,
    recommends flattening positions when risk exceeds thresholds,
    and computes overnight VaR.
    """

    def __init__(self, config: Optional[GapRiskConfig] = None):
        """Initialize gap risk monitor.

        Args:
            config: GapRiskConfig instance. Uses defaults if None.
        """
        self._config = config or GapRiskConfig()
        self._gap_dist = GapDistribution()
        self._last_report: Optional[Dict] = None

        # Try to load historical gap data
        self._load_historical_gaps()

        log.info("GapRiskMonitor initialized: max_exposure=%.0f%%, "
                 "alert_threshold=%.1f%%, flatten_prob=%.2f",
                 self._config.max_overnight_exposure_pct * 100,
                 self._config.gap_alert_threshold_pct,
                 self._config.flatten_probability_threshold)

    def _load_historical_gaps(self) -> None:
        """Load historical gap data and fit distribution."""
        try:
            if HISTORICAL_GAPS_PATH.exists():
                gaps = np.load(str(HISTORICAL_GAPS_PATH))
                self._gap_dist.fit(gaps)
                log.info("Loaded %d historical gaps", len(gaps))
            else:
                # Generate synthetic gaps for initial calibration
                rng = np.random.default_rng(42)
                # Typical leveraged ETP gap distribution: mean -0.1%, std 1.5%
                synthetic = np.concatenate([
                    rng.normal(-0.1, 1.2, 200),   # Normal regime
                    rng.normal(-0.3, 2.5, 50),     # Elevated vol
                    rng.normal(-1.0, 4.0, 10),     # Crisis gaps
                ])
                self._gap_dist.fit(synthetic)
                log.info("Using synthetic gap distribution (no historical data)")
        except Exception as e:
            log.warning("Failed to load gap data: %s", e)

    def _get_leverage(self, ticker: str) -> float:
        """Get leverage multiplier for a ticker."""
        return self._config.leverage_multiplier_map.get(ticker, 3.0)

    def assess_overnight_risk(self, positions: List[Dict],
                              vix: float) -> Dict[str, Any]:
        """Assess overnight gap risk for all positions.

        Args:
            positions: List of dicts with keys:
                       ticker, notional (GBP), direction ('long'/'short').
            vix: Current VIX level.

        Returns:
            Dict with per-position risk, total risk, and recommendations.
        """
        # VIX-adjusted gap threshold
        vix_scale = 1.0 + self._config.vix_scaling_factor * max(vix - 15.0, 0.0)
        adjusted_threshold = self._config.gap_alert_threshold_pct / vix_scale

        per_position: List[Dict] = []
        total_expected_loss = 0.0
        total_notional = 0.0

        for pos in positions:
            ticker = pos.get("ticker", "UNKNOWN")
            notional = abs(float(pos.get("notional", 0.0)))
            direction = pos.get("direction", "long")
            leverage = self._get_leverage(ticker)

            # Effective leverage considering direction
            if direction == "short" and leverage > 0:
                leverage = -leverage

            # Gap probability exceeding threshold
            gap_prob = self._gap_dist.probability_of_gap_exceeding(adjusted_threshold)

            # VIX amplification of gap probability
            gap_prob_vix = min(gap_prob * vix_scale, 0.95)

            # Expected loss
            exp_loss = self._gap_dist.expected_loss(notional, leverage)
            exp_loss_vix = exp_loss * vix_scale

            # Should flatten?
            should_flat = self.should_flatten_before_close(
                {"ticker": ticker, "notional": notional, "leverage": leverage},
                gap_prob_vix,
            )

            per_position.append({
                "ticker": ticker,
                "notional": round(notional, 2),
                "leverage": leverage,
                "gap_probability": round(gap_prob_vix, 4),
                "expected_loss_gbp": round(exp_loss_vix, 2),
                "recommend_flatten": should_flat,
            })

            total_expected_loss += exp_loss_vix
            total_notional += notional

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vix": vix,
            "vix_scale_factor": round(vix_scale, 2),
            "adjusted_gap_threshold_pct": round(adjusted_threshold, 2),
            "n_positions": len(positions),
            "total_notional": round(total_notional, 2),
            "total_expected_loss": round(total_expected_loss, 2),
            "positions": per_position,
        }

    def should_flatten_before_close(self, position: Dict,
                                    gap_prob: float) -> bool:
        """Determine if a position should be flattened before market close.

        Args:
            position: Dict with ticker, notional, leverage.
            gap_prob: Probability of gap exceeding threshold.

        Returns:
            True if the position should be closed.
        """
        ticker = position.get("ticker", "")
        leverage = abs(float(position.get("leverage", 3.0)))
        notional = abs(float(position.get("notional", 0.0)))

        # Rule 1: 5x products — always flatten
        if leverage >= 5.0:
            log.info("FLATTEN: %s — 5x product cannot be held overnight", ticker)
            return True

        # Rule 2: Gap probability exceeds threshold
        if gap_prob > self._config.flatten_probability_threshold:
            log.info("FLATTEN: %s — gap_prob=%.2f > threshold=%.2f",
                     ticker, gap_prob, self._config.flatten_probability_threshold)
            return True

        # Rule 3: Expected loss exceeds max acceptable
        exp_loss = self._gap_dist.expected_loss(notional, leverage)
        if notional > 0:
            loss_pct = exp_loss / notional
            if loss_pct > self._config.max_acceptable_loss_pct / 100.0:
                log.info("FLATTEN: %s — expected_loss=%.2f%% > max=%.2f%%",
                         ticker, loss_pct * 100,
                         self._config.max_acceptable_loss_pct)
                return True

        return False

    def compute_overnight_var(self, positions: List[Dict],
                              alpha: float = 0.05,
                              n_simulations: int = 5000) -> float:
        """Compute portfolio overnight Value-at-Risk via Monte Carlo.

        Simulates gap scenarios and computes the alpha-quantile loss.

        Args:
            positions: List of position dicts (ticker, notional, direction).
            alpha: VaR confidence level (0.05 = 95% VaR).
            n_simulations: Number of Monte Carlo scenarios.

        Returns:
            Overnight VaR in GBP (positive number = potential loss).
        """
        if not positions:
            return 0.0

        # Simulate gap scenarios
        gaps = self._gap_dist.sample(n_simulations)

        portfolio_pnl = np.zeros(n_simulations)

        for pos in positions:
            ticker = pos.get("ticker", "UNKNOWN")
            notional = float(pos.get("notional", 0.0))
            direction = pos.get("direction", "long")
            leverage = self._get_leverage(ticker)

            # Direction sign
            sign = 1.0 if direction == "long" else -1.0

            # P&L = notional * leverage * gap_pct / 100 * direction
            pos_pnl = notional * abs(leverage) * sign * gaps / 100.0

            # Add correlation noise (imperfect correlation between positions)
            noise = np.random.default_rng(hash(ticker) % (2**31)).normal(
                0, 0.3 * np.std(gaps) if np.std(gaps) > 0 else 0.5,
                n_simulations
            )
            pos_pnl += notional * abs(leverage) * noise / 100.0

            portfolio_pnl += pos_pnl

        # VaR = negative quantile (loss)
        var = float(-np.percentile(portfolio_pnl, alpha * 100))
        return max(var, 0.0)

    def run_nightly_gap_report(self) -> Dict[str, Any]:
        """Generate nightly gap risk report.

        Reads current positions and VIX, computes all risk metrics,
        and persists the report. Called from the nightly pipeline.

        Returns:
            Complete gap risk report dict.
        """
        # Load current positions
        positions = self._load_current_positions()
        vix = self._load_current_vix()

        # Risk assessment
        risk = self.assess_overnight_risk(positions, vix)

        # VaR at multiple levels
        var_95 = self.compute_overnight_var(positions, alpha=0.05)
        var_99 = self.compute_overnight_var(positions, alpha=0.01)

        # Count positions needing attention
        n_flatten = sum(1 for p in risk.get("positions", [])
                        if p.get("recommend_flatten"))

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vix": vix,
            "n_positions": len(positions),
            "n_recommend_flatten": n_flatten,
            "total_expected_loss": risk.get("total_expected_loss", 0.0),
            "var_95": round(var_95, 2),
            "var_99": round(var_99, 2),
            "gap_distribution": self._gap_dist.stats,
            "risk_assessment": risk,
        }

        # Persist
        self._save_report(report)
        self._last_report = report

        log.info("Nightly gap report: %d positions, %d flatten, "
                 "VaR95=%.0f GBP, VaR99=%.0f GBP",
                 len(positions), n_flatten, var_95, var_99)

        return report

    def _load_current_positions(self) -> List[Dict]:
        """Load current positions from engine state."""
        positions_path = Path("/app/data/positions.json")
        try:
            if positions_path.exists():
                with open(str(positions_path), "r") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    # Convert dict format to list format
                    return [
                        {"ticker": k, "notional": abs(v), "direction": "long" if v > 0 else "short"}
                        for k, v in data.items()
                        if isinstance(v, (int, float)) and v != 0
                    ]
        except Exception as e:
            log.warning("Failed to load positions: %s", e)
        return []

    def _load_current_vix(self) -> float:
        """Load current VIX from engine state."""
        vix_path = Path("/app/data/vix_current.json")
        try:
            if vix_path.exists():
                with open(str(vix_path), "r") as f:
                    data = json.load(f)
                return float(data.get("vix", data.get("value", 20.0)))
        except Exception as e:
            log.warning("Failed to load VIX: %s — using default 20.0", e)
        return 20.0

    def _save_report(self, report: Dict) -> None:
        """Save nightly report to disk."""
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            with open(str(NIGHTLY_REPORT_PATH), "w") as f:
                json.dump(report, f, indent=2, default=str)
        except Exception as e:
            log.error("Failed to save gap risk report: %s", e)
