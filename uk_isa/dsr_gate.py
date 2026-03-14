"""
NZT-48 AEGIS Phase I -- DSR Graduation Gate + PSR Confirmation (I-03)
=====================================================================
Bailey & Lopez de Prado (2014) Deflated Sharpe Ratio with Probabilistic
Sortino Ratio as secondary confirmation.

Graduation criteria (BOTH must pass for full Kelly):
    1. DSR t-stat >= 3.0
    2. PSR      >  0.95

Below threshold: Bayesian penalty applies (0.25x to 1.0x Kelly scaling).

References:
    Bailey, D. H., & Lopez de Prado, M. (2014). "The Deflated Sharpe
        Ratio: Correcting for Selection Bias, Backtest Overfitting, and
        Non-Normality." Journal of Portfolio Management, 40(5), 94-107.

    Probabilistic Sortino Ratio follows analogous construction using
    downside deviation instead of total volatility.

Usage:
    from uk_isa.dsr_gate import DSRGate

    gate = DSRGate()
    result = gate.check_graduation("QQQ3.L", returns, n_trades=100)
    if result.graduated:
        kelly_scale = 1.0  # full Kelly
    else:
        kelly_scale = result.bayesian_penalty  # 0.25x to 1.0x
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy import stats as sp_stats

logger = logging.getLogger("nzt48.dsr_gate")

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
_DSR_TSTAT_THRESHOLD: float = 3.0       # Bailey & Lopez de Prado (2014)
_PSR_THRESHOLD: float = 0.95            # 95% confidence of positive Sortino
_MIN_OBSERVATIONS: int = 30             # minimum returns for statistical validity
_ANNUALISATION_FACTOR: float = 252.0    # trading days per year
_RISK_FREE_DAILY: float = 0.045 / 252   # 4.5% annual UK gilt rate


@dataclass(frozen=True)
class GraduationResult:
    """Result of DSR/PSR graduation check.

    Attributes
    ----------
    ticker : str
        Ticker symbol evaluated.
    graduated : bool
        True if BOTH DSR and PSR thresholds are met.
    dsr_tstat : float
        Deflated Sharpe Ratio t-statistic.
    psr_value : float
        Probabilistic Sortino Ratio (0-1 probability).
    sharpe_ratio : float
        Annualised Sharpe ratio of the return series.
    sortino_ratio : float
        Annualised Sortino ratio of the return series.
    bayesian_penalty : float
        Kelly scaling factor (1.0 if graduated, 0.25-1.0 otherwise).
    n_observations : int
        Number of return observations used.
    n_trades : int
        Number of trades in the evaluation period.
    reason : str
        Human-readable explanation of the result.
    """

    ticker: str
    graduated: bool
    dsr_tstat: float
    psr_value: float
    sharpe_ratio: float
    sortino_ratio: float
    bayesian_penalty: float
    n_observations: int
    n_trades: int
    reason: str


class DSRGate:
    """Bailey & Lopez de Prado (2014) Deflated Sharpe Ratio graduation gate.

    Evaluates whether a ticker's track record is statistically significant
    enough for full Kelly position sizing. Uses DSR t-stat as primary gate
    and PSR as secondary confirmation.

    Parameters
    ----------
    dsr_threshold : float
        Minimum DSR t-stat for graduation (default 3.0).
    psr_threshold : float
        Minimum PSR for graduation (default 0.95).
    risk_free_annual : float
        Annual risk-free rate for Sharpe/Sortino (default 0.045).
    """

    def __init__(
        self,
        dsr_threshold: float = _DSR_TSTAT_THRESHOLD,
        psr_threshold: float = _PSR_THRESHOLD,
        risk_free_annual: float = 0.045,
    ) -> None:
        self._dsr_threshold = dsr_threshold
        self._psr_threshold = psr_threshold
        self._rf_daily = risk_free_annual / _ANNUALISATION_FACTOR

    def check_graduation(
        self,
        ticker: str,
        returns: np.ndarray,
        n_trades: int,
        benchmark_sharpe: float = 0.0,
    ) -> GraduationResult:
        """Evaluate whether *ticker* graduates for full Kelly sizing.

        Parameters
        ----------
        ticker : str
            Ticker symbol (for logging and result tagging).
        returns : np.ndarray
            Array of daily percentage returns (e.g. [0.5, -0.3, 1.2, ...]).
            These should be NET returns (after costs).
        n_trades : int
            Number of completed trades in the evaluation period.
            Used for DSR deflation (more trials = higher bar).
        benchmark_sharpe : float
            Benchmark Sharpe ratio to deflate against (default 0.0).
            Set to the median Sharpe across all tickers for proper deflation.

        Returns
        -------
        GraduationResult
            Immutable result with graduation status and diagnostics.
        """
        returns = np.asarray(returns, dtype=np.float64)
        returns = returns[np.isfinite(returns)]
        n_obs = len(returns)

        # Insufficient data guard
        if n_obs < _MIN_OBSERVATIONS:
            logger.warning(
                "DSR_GATE: %s — insufficient data (%d obs, need %d)",
                ticker, n_obs, _MIN_OBSERVATIONS,
            )
            return GraduationResult(
                ticker=ticker,
                graduated=False,
                dsr_tstat=0.0,
                psr_value=0.0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                bayesian_penalty=0.25,
                n_observations=n_obs,
                n_trades=n_trades,
                reason=f"Insufficient data: {n_obs} observations (need >= {_MIN_OBSERVATIONS})",
            )

        # Compute Sharpe ratio (annualised)
        excess = returns - self._rf_daily
        mean_excess = np.mean(excess)
        std_excess = np.std(excess, ddof=1)

        if std_excess < 1e-10:
            sharpe_ann = 0.0
        else:
            sharpe_ann = (mean_excess / std_excess) * math.sqrt(_ANNUALISATION_FACTOR)

        # Compute Sortino ratio (annualised, using downside deviation)
        downside = excess[excess < 0]
        if len(downside) > 1:
            downside_std = np.sqrt(np.mean(downside ** 2))
        else:
            downside_std = std_excess  # fallback to total vol

        if downside_std < 1e-10:
            sortino_ann = 0.0
        else:
            sortino_ann = (mean_excess / downside_std) * math.sqrt(_ANNUALISATION_FACTOR)

        # DSR t-statistic (Bailey & Lopez de Prado 2014)
        dsr_tstat = self._compute_dsr_tstat(
            returns=excess,
            sharpe_ratio=mean_excess / std_excess if std_excess > 1e-10 else 0.0,
            n_obs=n_obs,
            n_trades=n_trades,
            benchmark_sharpe=benchmark_sharpe / math.sqrt(_ANNUALISATION_FACTOR) if benchmark_sharpe != 0 else 0.0,
        )

        # PSR: Probabilistic Sortino Ratio
        psr_value = self._compute_psr(
            returns=excess,
            sortino_ratio=mean_excess / downside_std if downside_std > 1e-10 else 0.0,
            n_obs=n_obs,
        )

        # Graduation check: BOTH must pass
        dsr_pass = dsr_tstat >= self._dsr_threshold
        psr_pass = psr_value > self._psr_threshold
        graduated = dsr_pass and psr_pass

        # Bayesian penalty for non-graduated tickers
        if graduated:
            bayesian_penalty = 1.0
            reason = (
                f"GRADUATED: DSR t-stat={dsr_tstat:.2f} >= {self._dsr_threshold}, "
                f"PSR={psr_value:.4f} > {self._psr_threshold}"
            )
        else:
            bayesian_penalty = self._compute_bayesian_penalty(dsr_tstat, psr_value)
            parts = []
            if not dsr_pass:
                parts.append(f"DSR t-stat={dsr_tstat:.2f} < {self._dsr_threshold}")
            if not psr_pass:
                parts.append(f"PSR={psr_value:.4f} <= {self._psr_threshold}")
            reason = f"NOT GRADUATED: {'; '.join(parts)}. Bayesian penalty={bayesian_penalty:.2f}x"

        logger.info(
            "DSR_GATE: %s — %s (SR=%.2f, SoR=%.2f, DSR_t=%.2f, PSR=%.4f, "
            "penalty=%.2f, n=%d, trades=%d)",
            ticker,
            "PASS" if graduated else "FAIL",
            sharpe_ann,
            sortino_ann,
            dsr_tstat,
            psr_value,
            bayesian_penalty,
            n_obs,
            n_trades,
        )

        return GraduationResult(
            ticker=ticker,
            graduated=graduated,
            dsr_tstat=dsr_tstat,
            psr_value=psr_value,
            sharpe_ratio=sharpe_ann,
            sortino_ratio=sortino_ann,
            bayesian_penalty=bayesian_penalty,
            n_observations=n_obs,
            n_trades=n_trades,
            reason=reason,
        )

    def _compute_dsr_tstat(
        self,
        returns: np.ndarray,
        sharpe_ratio: float,
        n_obs: int,
        n_trades: int,
        benchmark_sharpe: float = 0.0,
    ) -> float:
        """Compute the Deflated Sharpe Ratio t-statistic.

        Bailey & Lopez de Prado (2014) Equation 14:
            DSR = (SR_observed - SR_benchmark) / SE(SR)

        where SE(SR) accounts for skewness and kurtosis of returns,
        and SR_benchmark is deflated by the expected maximum Sharpe
        from n_trades independent trials.

        Parameters
        ----------
        returns : np.ndarray
            Excess returns array (already excess of risk-free).
        sharpe_ratio : float
            Observed Sharpe ratio (non-annualised, daily frequency).
        n_obs : int
            Number of observations.
        n_trades : int
            Number of independent trials (trades) for deflation.
        benchmark_sharpe : float
            Benchmark Sharpe to deflate against.

        Returns
        -------
        float
            DSR t-statistic. Higher = more statistically significant.
        """
        if n_obs < 3:
            return 0.0

        # Skewness and excess kurtosis of returns
        skew = float(sp_stats.skew(returns, bias=False))
        kurt = float(sp_stats.kurtosis(returns, bias=False))  # excess kurtosis

        # Standard error of the Sharpe ratio (Lo, 2002)
        # SE(SR) = sqrt((1 - gamma_3 * SR + (gamma_4 - 1)/4 * SR^2) / (n - 1))
        # where gamma_3 = skewness, gamma_4 = kurtosis + 3
        sr = sharpe_ratio
        se_sr_sq = (1.0 - skew * sr + ((kurt) / 4.0) * sr ** 2) / (n_obs - 1)

        if se_sr_sq <= 0:
            se_sr_sq = 1.0 / (n_obs - 1)  # fallback to basic SE

        se_sr = math.sqrt(se_sr_sq)

        # Expected maximum Sharpe from n_trades trials (Euler-Mascheroni deflation)
        # E[max(SR)] ~ SR_0 * sqrt(2 * ln(n_trades))
        # For benchmark_sharpe = 0, this simplifies to:
        #   E[max(SR)] ~ sqrt(2 * ln(n_trades)) / sqrt(n_obs)
        if n_trades > 1:
            euler_mascheroni = 0.5772156649
            expected_max_sr = (
                (1.0 - euler_mascheroni) * sp_stats.norm.ppf(1 - 1.0 / n_trades)
                + euler_mascheroni * sp_stats.norm.ppf(1 - 1.0 / (n_trades * math.e))
            )
            # Scale by SE to get in same units
            deflated_benchmark = max(benchmark_sharpe, expected_max_sr * se_sr)
        else:
            deflated_benchmark = benchmark_sharpe

        # DSR t-stat
        if se_sr < 1e-10:
            return 0.0

        dsr_tstat = (sr - deflated_benchmark) / se_sr
        return dsr_tstat

    def _compute_psr(
        self,
        returns: np.ndarray,
        sortino_ratio: float,
        n_obs: int,
        target_sortino: float = 0.0,
    ) -> float:
        """Compute Probabilistic Sortino Ratio.

        PSR = Phi((SoR_observed - SoR_target) / SE(SoR))

        where Phi is the standard normal CDF and SE(SoR) accounts
        for the sampling distribution of the Sortino ratio.

        Parameters
        ----------
        returns : np.ndarray
            Excess returns array.
        sortino_ratio : float
            Observed Sortino ratio (non-annualised).
        n_obs : int
            Number of observations.
        target_sortino : float
            Target Sortino to test against (default 0.0 = test for positive).

        Returns
        -------
        float
            Probability (0-1) that the true Sortino exceeds target_sortino.
        """
        if n_obs < 3:
            return 0.0

        # Standard error of Sortino ratio (analogous to Lo 2002 for Sharpe)
        # Using simplified SE based on downside observations
        downside = returns[returns < 0]
        n_down = len(downside)

        if n_down < 2:
            # Not enough downside observations — can't estimate SE
            # Use a permissive estimate
            return 0.5

        skew = float(sp_stats.skew(returns, bias=False))
        kurt = float(sp_stats.kurtosis(returns, bias=False))

        sor = sortino_ratio
        se_sor_sq = (1.0 - skew * sor + ((kurt) / 4.0) * sor ** 2) / (n_obs - 1)

        if se_sor_sq <= 0:
            se_sor_sq = 1.0 / (n_obs - 1)

        se_sor = math.sqrt(se_sor_sq)

        if se_sor < 1e-10:
            return 1.0 if sor > target_sortino else 0.0

        z_score = (sor - target_sortino) / se_sor
        psr = float(sp_stats.norm.cdf(z_score))

        return psr

    def _compute_bayesian_penalty(
        self,
        dsr_tstat: float,
        psr_value: float,
    ) -> float:
        """Compute Bayesian penalty for non-graduated tickers.

        Scales linearly from 0.25x (worst) to 1.0x (at threshold).
        Uses the worse of the two metrics to determine the penalty.

        Parameters
        ----------
        dsr_tstat : float
            Observed DSR t-statistic.
        psr_value : float
            Observed PSR probability.

        Returns
        -------
        float
            Kelly scaling factor in [0.25, 1.0].
        """
        # Normalise each metric to [0, 1] range relative to threshold
        dsr_ratio = max(0.0, min(1.0, dsr_tstat / self._dsr_threshold))
        psr_ratio = max(0.0, min(1.0, psr_value / self._psr_threshold))

        # Use the worse of the two (conservative approach)
        worst_ratio = min(dsr_ratio, psr_ratio)

        # Scale from 0.25 (worst) to 1.0 (at threshold)
        penalty = 0.25 + 0.75 * worst_ratio

        return round(penalty, 4)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_gate_instance: Optional[DSRGate] = None


def get_gate() -> DSRGate:
    """Return the module-level singleton DSRGate."""
    global _gate_instance
    if _gate_instance is None:
        _gate_instance = DSRGate()
    return _gate_instance
