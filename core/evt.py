"""
NZT-48 Extreme Value Theory Module -- Peak-Over-Threshold Tail Risk Monitor
============================================================================
Resolves contradiction C-24 from the V8.0 audit: the system lacked a
stateful, per-ticker EVT veto with caching and fast numerical paths.

Theory:
    Balkema & de Haan (1974), Pickands (1975):
        For a sufficiently high threshold u, the distribution of exceedances
        (X - u | X > u) converges to a Generalized Pareto Distribution (GPD).
        This is the Peaks-Over-Threshold (POT) method.

    McNeil & Frey (2000):
        Applied EVT to financial risk management, demonstrating that GPD-based
        VaR/ES estimates outperform Gaussian and historical simulation in the
        tails. Their two-step approach (GARCH filter + GPD tail) is the
        institutional standard.

Relationship to existing modules:
    - core/quant_math/evt.py: functional, stateless GPD fitting (gpd_tail_risk).
      Kept as the low-level primitive.
    - core/tail_loss_monitor.py: CVaR-based tail monitoring (Bali et al. 2011).
      Simpler empirical approach. Remains for CVaR alerts and clustering.
    - THIS MODULE (core/evt.py): stateful TailRiskMonitor class with per-ticker
      GPD caching (1-hour TTL), Numba-accelerated hot loops, and a clean
      veto_signal() interface for signal execution gates.

Usage in virtual_trader.py execute_signal():
    monitor = TailRiskMonitor()
    veto, reason = monitor.veto_signal(ticker, historical_returns)
    if veto:
        logger.warning("EVT_VETO %s: %s", ticker, reason)
        return  # reject trade
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from numba import njit
from scipy.stats import genpareto

from core.clock import now_utc

logger = logging.getLogger("nzt48.evt")

# ─── Constants ────────────────────────────────────────────────────────────────
_CACHE_TTL_SECONDS: float = 3600.0  # 1 hour
_MIN_EXCEEDANCES: int = 10          # Minimum POT exceedances for reliable GPD fit
_MIN_OBSERVATIONS: int = 60         # Minimum return observations to attempt EVT
_SHAPE_XI_UPPER_BOUND: float = 0.5  # xi > 0.5 → extremely heavy tail, unreliable


# ─── Numba-accelerated helpers ────────────────────────────────────────────────

@njit(nogil=True, cache=True)
def _select_threshold_and_exceedances(
    losses: np.ndarray,
    percentile_rank: float,
) -> tuple[np.ndarray, float]:
    """Compute POT threshold and extract exceedances above it.

    Hot loop: called once per fit, iterates over the full loss array to
    partition exceedances. Numba eliminates Python-level overhead.

    Parameters
    ----------
    losses : np.ndarray
        Absolute values of negative returns (all positive).
    percentile_rank : float
        Percentile (0-100) for threshold selection. E.g. 95.0 means the
        threshold is the 95th percentile of the loss distribution.

    Returns
    -------
    exceedances : np.ndarray
        Losses minus threshold, for values exceeding the threshold.
    threshold : float
        The computed threshold value.
    """
    n = losses.shape[0]
    # Sort for percentile computation
    sorted_losses = np.sort(losses)
    # Compute percentile index (linear interpolation)
    idx_float = (percentile_rank / 100.0) * (n - 1)
    idx_low = int(idx_float)
    idx_high = min(idx_low + 1, n - 1)
    frac = idx_float - idx_low
    threshold = sorted_losses[idx_low] * (1.0 - frac) + sorted_losses[idx_high] * frac

    # Count exceedances first to pre-allocate
    count = 0
    for i in range(n):
        if losses[i] > threshold:
            count += 1

    exceedances = np.empty(count, dtype=np.float64)
    j = 0
    for i in range(n):
        if losses[i] > threshold:
            exceedances[j] = losses[i] - threshold
            j += 1

    return exceedances, threshold


@njit(nogil=True, cache=True)
def _gpd_survival(x: float, xi: float, sigma: float) -> float:
    """GPD survival function P(X > x) for a single point.

    For xi != 0:  S(x) = (1 + xi * x / sigma) ^ (-1/xi)
    For xi == 0:  S(x) = exp(-x / sigma)

    Parameters
    ----------
    x : float
        Exceedance value (must be > 0).
    xi : float
        Shape parameter.
    sigma : float
        Scale parameter (must be > 0).

    Returns
    -------
    float
        Survival probability.
    """
    if sigma <= 0.0 or x < 0.0:
        return 0.0

    if abs(xi) < 1e-10:
        # Exponential limit (xi → 0)
        return np.exp(-x / sigma)

    z = 1.0 + xi * x / sigma
    if z <= 0.0:
        # Beyond the support of the distribution
        if xi < 0.0:
            return 0.0  # Finite upper endpoint exceeded
        return 0.0
    return z ** (-1.0 / xi)


@njit(nogil=True, cache=True)
def _compute_loss_stats(returns: np.ndarray) -> tuple[float, float, int]:
    """Extract loss statistics from a return series.

    Returns
    -------
    mean_loss : float
        Mean of absolute losses.
    std_loss : float
        Standard deviation of absolute losses (ddof=1).
    n_losses : int
        Number of negative returns.
    """
    n = returns.shape[0]
    # Count and sum losses
    n_losses = 0
    sum_loss = 0.0
    for i in range(n):
        if returns[i] < 0.0:
            n_losses += 1
            sum_loss += -returns[i]  # Absolute value

    if n_losses == 0:
        return 0.0, 0.0, 0

    mean_loss = sum_loss / n_losses

    # Variance (ddof=1)
    sum_sq = 0.0
    for i in range(n):
        if returns[i] < 0.0:
            diff = (-returns[i]) - mean_loss
            sum_sq += diff * diff

    if n_losses > 1:
        std_loss = np.sqrt(sum_sq / (n_losses - 1))
    else:
        std_loss = 0.0

    return mean_loss, std_loss, n_losses


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class GPDFit:
    """Result of fitting a Generalized Pareto Distribution to tail exceedances.

    Attributes
    ----------
    xi : float
        Shape parameter. xi > 0 → heavy tail (Frechet domain),
        xi = 0 → exponential tail, xi < 0 → finite upper endpoint.
    sigma : float
        Scale parameter (always positive).
    threshold : float
        POT threshold value.
    n_exceedances : int
        Number of observations exceeding the threshold.
    n_total : int
        Total number of observations used for fitting.
    fit_time : str
        ISO timestamp of when this fit was computed.
    """
    xi: float = 0.0
    sigma: float = 0.0
    threshold: float = 0.0
    n_exceedances: int = 0
    n_total: int = 0
    fit_time: str = ""


@dataclass
class _CacheEntry:
    """Internal cache entry for per-ticker GPD fits."""
    gpd_fit: GPDFit
    timestamp: float  # time.monotonic() for TTL checks
    losses: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.float64))


# ─── Main class ───────────────────────────────────────────────────────────────

class TailRiskMonitor:
    """Stateful EVT tail risk monitor with per-ticker GPD caching.

    Provides the primary signal veto interface for the NZT-48 execution
    pipeline. Fits GPD to historical loss tails using the Peaks-Over-Threshold
    method (Balkema & de Haan 1974, Pickands 1975) and vetoes trades where
    the GPD predicts unacceptable probability of extreme gap losses.

    The monitor maintains a cache of GPD fits per ticker with a configurable
    TTL (default 1 hour), avoiding redundant re-fitting on every signal
    evaluation within the same trading session.

    Parameters
    ----------
    cache_ttl : float
        Time-to-live for cached GPD fits, in seconds. Default 3600 (1 hour).

    Examples
    --------
    >>> monitor = TailRiskMonitor()
    >>> returns = np.array([-0.02, 0.01, -0.05, 0.03, ...])  # 200+ returns
    >>> veto, reason = monitor.veto_signal("QQQ3.L", returns)
    >>> if veto:
    ...     print(f"Trade vetoed: {reason}")
    """

    def __init__(self, cache_ttl: float = _CACHE_TTL_SECONDS) -> None:
        self._cache: dict[str, _CacheEntry] = {}
        self._cache_ttl: float = cache_ttl

    # ── Public API ────────────────────────────────────────────────────────

    def fit_gpd(
        self,
        returns: np.ndarray,
        threshold_percentile: float = 95.0,
    ) -> dict:
        """Fit a Generalized Pareto Distribution to loss tail exceedances.

        Implements the Balkema-de Haan-Pickands POT theorem:
        for a sufficiently high threshold u, exceedances (X - u | X > u)
        converge to GPD(xi, sigma).

        Uses scipy.stats.genpareto MLE fitting with location fixed at 0
        (exceedances are already shifted by the threshold).

        Parameters
        ----------
        returns : np.ndarray
            Array of log returns or simple returns. Losses are identified
            as negative values.
        threshold_percentile : float
            Percentile (0-100) of the loss distribution to use as the POT
            threshold. Default 95 (i.e. only the worst 5% of losses are
            used as exceedances). McNeil & Frey (2000) recommend 90-95%.

        Returns
        -------
        dict
            Keys: 'xi' (shape), 'sigma' (scale), 'threshold', 'n_exceedances',
            'n_total', 'fit_time'. Returns empty/default dict if fitting fails
            or insufficient data.

        Raises
        ------
        No exceptions raised — fitting failures are logged and return defaults.
        """
        result = GPDFit()

        # Extract losses as positive magnitudes
        losses = np.abs(returns[returns < 0.0]).astype(np.float64)
        result.n_total = len(returns)

        if len(losses) < _MIN_OBSERVATIONS:
            logger.debug(
                "fit_gpd: insufficient losses (%d < %d)",
                len(losses), _MIN_OBSERVATIONS,
            )
            return self._gpd_fit_to_dict(result)

        # Numba-accelerated threshold selection and exceedance extraction
        exceedances, threshold = _select_threshold_and_exceedances(
            losses, threshold_percentile,
        )
        result.threshold = float(threshold)
        result.n_exceedances = len(exceedances)

        if len(exceedances) < _MIN_EXCEEDANCES:
            logger.debug(
                "fit_gpd: insufficient exceedances (%d < %d)",
                len(exceedances), _MIN_EXCEEDANCES,
            )
            return self._gpd_fit_to_dict(result)

        # MLE fit via scipy (location fixed at 0 — exceedances are pre-shifted)
        try:
            shape, _loc, scale = genpareto.fit(exceedances, floc=0)
            result.xi = float(shape)
            result.sigma = float(scale)
            result.fit_time = now_utc().isoformat()

            # Diagnostic: warn on extreme shape parameters
            if shape > _SHAPE_XI_UPPER_BOUND:
                logger.warning(
                    "fit_gpd: xi=%.4f > %.1f — extremely heavy tail, "
                    "GPD fit may be unreliable (n_exc=%d)",
                    shape, _SHAPE_XI_UPPER_BOUND, len(exceedances),
                )

        except Exception as exc:
            logger.error("fit_gpd: MLE fitting failed: %s", exc)

        return self._gpd_fit_to_dict(result)

    def prob_extreme_loss(
        self,
        fitted_gpd: dict,
        loss_threshold: float,
    ) -> float:
        """Compute P(X > loss_threshold | X > u) using the fitted GPD.

        This is the conditional exceedance probability: given that a loss
        already exceeds the POT threshold u, what is the probability that
        it exceeds the more extreme `loss_threshold`?

        For unconditional probability, multiply by the empirical probability
        of exceeding u: P(X > loss_threshold) = P(X > u) * P(X > loss_threshold | X > u).

        Parameters
        ----------
        fitted_gpd : dict
            Output of fit_gpd(). Must contain 'xi', 'sigma', 'threshold'.
        loss_threshold : float
            The extreme loss magnitude to compute the probability for.
            Must be expressed as a positive value (absolute loss).

        Returns
        -------
        float
            Conditional survival probability in [0, 1]. Returns 0.0 if
            the fitted GPD is invalid or loss_threshold <= threshold.
        """
        xi = fitted_gpd.get("xi", 0.0)
        sigma = fitted_gpd.get("sigma", 0.0)
        threshold = fitted_gpd.get("threshold", 0.0)

        if sigma <= 0.0 or fitted_gpd.get("n_exceedances", 0) < _MIN_EXCEEDANCES:
            return 0.0

        # Exceedance above the POT threshold
        excess = loss_threshold - threshold
        if excess <= 0.0:
            # loss_threshold is below the POT threshold — always exceeded
            return 1.0

        # Use the Numba-accelerated survival function
        return float(_gpd_survival(excess, xi, sigma))

    def veto_signal(
        self,
        ticker: str,
        returns: np.ndarray,
        sigma_threshold: float = 5.0,
    ) -> tuple[bool, str]:
        """Decide whether to veto a trade signal based on EVT tail risk.

        Vetoes if the GPD predicts > 1% probability of a loss exceeding
        `sigma_threshold` standard deviations of the loss distribution.

        This is the primary interface for execute_signal() in virtual_trader.py.

        The decision pipeline:
        1. Check cache for a valid (non-expired) GPD fit for this ticker.
        2. If cache miss/expired, refit GPD.
        3. Compute the gap size as sigma_threshold * std(losses).
        4. Compute P(loss > gap) using GPD survival function.
        5. Veto if P > 1%.

        Parameters
        ----------
        ticker : str
            Instrument ticker (e.g. "QQQ3.L").
        returns : np.ndarray
            Historical return series for this ticker.
        sigma_threshold : float
            Number of loss standard deviations defining "extreme gap".
            Default 5.0 (a 5-sigma event).

        Returns
        -------
        tuple[bool, str]
            (should_veto, reason). If should_veto is True, the trade
            should be rejected.
        """
        if len(returns) < _MIN_OBSERVATIONS:
            return False, f"Insufficient data ({len(returns)} < {_MIN_OBSERVATIONS})"

        # Compute loss statistics via Numba
        mean_loss, std_loss, n_losses = _compute_loss_stats(
            returns.astype(np.float64),
        )

        if n_losses < 20 or std_loss <= 0.0:
            return False, f"Insufficient loss data (n_losses={n_losses}, std={std_loss:.6f})"

        # Get or refit GPD (cache-aware)
        fitted = self._get_or_fit(ticker, returns)

        if fitted.get("n_exceedances", 0) < _MIN_EXCEEDANCES:
            return False, (
                f"GPD not fitted — insufficient exceedances "
                f"({fitted.get('n_exceedances', 0)} < {_MIN_EXCEEDANCES})"
            )

        # Compute the gap size in absolute loss terms
        gap_size = sigma_threshold * std_loss

        # Unconditional probability of extreme loss:
        # P(loss > gap) = P(loss > threshold) * P(loss > gap | loss > threshold)
        threshold = fitted.get("threshold", 0.0)
        n_total = fitted.get("n_total", 1)

        # Empirical probability of exceeding the POT threshold
        losses = np.abs(returns[returns < 0.0])
        p_exceed_threshold = float(np.sum(losses > threshold)) / max(len(losses), 1)

        # Conditional GPD probability
        p_gap_given_exceed = self.prob_extreme_loss(fitted, gap_size)

        # Unconditional tail probability
        tail_prob = p_exceed_threshold * p_gap_given_exceed

        xi = fitted.get("xi", 0.0)
        sigma = fitted.get("sigma", 0.0)
        veto_threshold = 0.01  # 1% probability

        # Shape parameter sanity check — conservative veto on unreliable fit
        if xi > _SHAPE_XI_UPPER_BOUND:
            reason = (
                f"EVT_VETO {ticker}: xi={xi:.4f} > {_SHAPE_XI_UPPER_BOUND} "
                f"(extremely heavy tail, unreliable fit) — conservative veto"
            )
            logger.warning(reason)
            return True, reason

        if tail_prob > veto_threshold:
            reason = (
                f"EVT_VETO {ticker}: P(loss > {sigma_threshold:.1f}sigma) = "
                f"{tail_prob:.6f} > {veto_threshold:.2f} "
                f"[xi={xi:.4f}, sigma={sigma:.4f}, gap={gap_size:.4f}]"
            )
            logger.warning(reason)
            return True, reason

        reason = (
            f"EVT_OK {ticker}: P(loss > {sigma_threshold:.1f}sigma) = "
            f"{tail_prob:.6f} <= {veto_threshold:.2f} "
            f"[xi={xi:.4f}, sigma={sigma:.4f}, n_exc={fitted.get('n_exceedances', 0)}]"
        )
        logger.debug(reason)
        return False, reason

    def update_cache(self, ticker: str, returns: np.ndarray) -> None:
        """Force-update the internal GPD fit cache for a ticker.

        Call this when new trade outcomes arrive to refresh the tail
        model. The fit is stored with a fresh TTL (1 hour by default).

        Parameters
        ----------
        ticker : str
            Instrument ticker.
        returns : np.ndarray
            Updated return series for the ticker.
        """
        fitted = self.fit_gpd(returns)
        losses = np.abs(returns[returns < 0.0]).astype(np.float64)
        self._cache[ticker] = _CacheEntry(
            gpd_fit=self._dict_to_gpd_fit(fitted),
            timestamp=time.monotonic(),
            losses=losses,
        )
        logger.debug(
            "update_cache: %s refreshed (n_exc=%d, xi=%.4f)",
            ticker, fitted.get("n_exceedances", 0), fitted.get("xi", 0.0),
        )

    def invalidate(self, ticker: Optional[str] = None) -> None:
        """Invalidate cached GPD fit(s).

        Parameters
        ----------
        ticker : str, optional
            If provided, invalidate only this ticker. If None, clear all.
        """
        if ticker is None:
            self._cache.clear()
            logger.debug("invalidate: all cache entries cleared")
        else:
            self._cache.pop(ticker, None)
            logger.debug("invalidate: %s removed from cache", ticker)

    def get_diagnostics(self, ticker: str) -> Optional[dict]:
        """Return cached GPD diagnostics for a ticker, or None if not cached.

        Parameters
        ----------
        ticker : str
            Instrument ticker.

        Returns
        -------
        dict or None
            The cached GPD fit as a dict, or None if no valid cache entry.
        """
        entry = self._cache.get(ticker)
        if entry is None:
            return None
        age = time.monotonic() - entry.timestamp
        result = self._gpd_fit_to_dict(entry.gpd_fit)
        result["cache_age_seconds"] = round(age, 1)
        result["cache_expired"] = age > self._cache_ttl
        return result

    # ── Private helpers ───────────────────────────────────────────────────

    def _get_or_fit(self, ticker: str, returns: np.ndarray) -> dict:
        """Return cached GPD fit if valid, otherwise refit and cache.

        Parameters
        ----------
        ticker : str
            Instrument ticker.
        returns : np.ndarray
            Return series for fitting.

        Returns
        -------
        dict
            GPD fit dictionary.
        """
        entry = self._cache.get(ticker)
        if entry is not None:
            age = time.monotonic() - entry.timestamp
            if age < self._cache_ttl:
                return self._gpd_fit_to_dict(entry.gpd_fit)
            else:
                logger.debug(
                    "_get_or_fit: %s cache expired (age=%.0fs > TTL=%.0fs)",
                    ticker, age, self._cache_ttl,
                )

        # Cache miss or expired — refit
        fitted = self.fit_gpd(returns)
        losses = np.abs(returns[returns < 0.0]).astype(np.float64)
        self._cache[ticker] = _CacheEntry(
            gpd_fit=self._dict_to_gpd_fit(fitted),
            timestamp=time.monotonic(),
            losses=losses,
        )
        return fitted

    @staticmethod
    def _gpd_fit_to_dict(fit: GPDFit) -> dict:
        """Convert GPDFit dataclass to a plain dict."""
        return {
            "xi": fit.xi,
            "sigma": fit.sigma,
            "threshold": fit.threshold,
            "n_exceedances": fit.n_exceedances,
            "n_total": fit.n_total,
            "fit_time": fit.fit_time,
        }

    @staticmethod
    def _dict_to_gpd_fit(d: dict) -> GPDFit:
        """Convert a dict back to GPDFit dataclass."""
        return GPDFit(
            xi=d.get("xi", 0.0),
            sigma=d.get("sigma", 0.0),
            threshold=d.get("threshold", 0.0),
            n_exceedances=d.get("n_exceedances", 0),
            n_total=d.get("n_total", 0),
            fit_time=d.get("fit_time", ""),
        )
