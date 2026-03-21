"""HMM with Student-t Emissions — Fat-tailed regime detection.

P2-4: Replaces Gaussian HMM emissions with Student-t (nu ~ 4-6) for
better tail risk capture in financial return regime classification.

Financial returns exhibit excess kurtosis (fat tails), meaning extreme
moves occur far more frequently than a Gaussian model predicts.  The
Student-t distribution with low degrees of freedom (nu=4-6) captures
this property, giving more realistic regime classifications during
market stress events.

Three regimes:
  0 = Low Volatility   (calm / trending)
  1 = Normal Volatility (typical trading)
  2 = High Volatility   (crisis / opportunity)

The Baum-Welch (EM) algorithm is distribution-agnostic; we simply swap
the Gaussian emission PDF for the Student-t PDF:

    f(x | mu, sigma, nu) =
        Gamma((nu+1)/2) / (Gamma(nu/2) * sqrt(nu*pi) * sigma)
        * (1 + ((x - mu) / sigma)^2 / nu)^(-(nu+1)/2)

Degrees of freedom (nu) are FIXED at init time — MLE estimation of nu
is numerically fragile and adds negligible value in practice.

QUARANTINE MODULE: read-only analytics, generates reports only.
No side effects on engine state, WAL, or risk parameters.

Dependencies: numpy, math (stdlib).  No scipy, hmmlearn, or sklearn.

Usage:
    from python_brain.ouroboros.hmm_student_t import StudentTHMM

    model = StudentTHMM(n_regimes=3, nu=4.0)
    model.fit(returns)              # numpy array of log returns
    regimes = model.predict(returns)
    current = model.current_regime(returns)
    probs = model.regime_probabilities(returns)

CLI:
    python -m python_brain.ouroboros.hmm_student_t --wal-dir /app/events --lookback 90
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
REPORT_DIR = DATA_DIR / "regime_reports"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [HMM-StudentT] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("hmm_student_t")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LOG_ZERO: float = -1e10          # Floor for log-space to avoid -inf
MIN_SIGMA: float = 1e-8         # Floor for scale parameter
MIN_TRANSITION: float = 1e-6    # Floor for transition probabilities

REGIME_LABELS = {0: "Low Vol", 1: "Normal Vol", 2: "High Vol"}


# ---------------------------------------------------------------------------
# Student-t log-PDF (pure numpy + math.lgamma)
# ---------------------------------------------------------------------------

def _student_t_logpdf(
    x: np.ndarray,
    mu: float,
    sigma: float,
    nu: float,
) -> np.ndarray:
    """Log-PDF of the (location-scale) Student-t distribution.

    Parameters
    ----------
    x : np.ndarray
        Observation values, shape (T,).
    mu : float
        Location parameter (regime mean).
    sigma : float
        Scale parameter (regime spread).  Must be > 0.
    nu : float
        Degrees of freedom.  Must be > 0.  Lower = fatter tails.

    Returns
    -------
    np.ndarray
        Log-probability densities, shape (T,).

    Mathematical form
    -----------------
    log f(x | mu, sigma, nu) =
        lgamma((nu+1)/2) - lgamma(nu/2)
        - 0.5 * log(nu * pi) - log(sigma)
        - ((nu+1)/2) * log(1 + ((x - mu) / sigma)^2 / nu)
    """
    sigma = max(sigma, MIN_SIGMA)
    z = (x - mu) / sigma
    z_sq = z * z

    # Normalisation constant (scalar, computed once)
    log_norm = (
        math.lgamma((nu + 1.0) / 2.0)
        - math.lgamma(nu / 2.0)
        - 0.5 * math.log(nu * math.pi)
        - math.log(sigma)
    )

    # Kernel (vectorised over observations)
    log_kernel = -((nu + 1.0) / 2.0) * np.log1p(z_sq / nu)

    return log_norm + log_kernel


# ---------------------------------------------------------------------------
# Log-space arithmetic helpers
# ---------------------------------------------------------------------------

def _logsumexp(a: np.ndarray, axis: Optional[int] = None) -> np.ndarray:
    """Numerically stable log-sum-exp.

    Implements the standard trick:
        log(sum(exp(a_i))) = max(a) + log(sum(exp(a_i - max(a))))

    Parameters
    ----------
    a : np.ndarray
        Input array of log-values.
    axis : int or None
        Axis along which to reduce.

    Returns
    -------
    np.ndarray or float
        Reduced log-sum-exp value.
    """
    a_max = np.max(a, axis=axis, keepdims=True)
    # Guard against all -inf
    a_max = np.where(np.isfinite(a_max), a_max, 0.0)
    result = a_max + np.log(np.sum(np.exp(a - a_max), axis=axis, keepdims=True))
    if axis is not None:
        return np.squeeze(result, axis=axis)
    return np.squeeze(result)


def _log_normalise(log_vec: np.ndarray) -> np.ndarray:
    """Normalise a log-probability vector so exp(result) sums to 1.

    Parameters
    ----------
    log_vec : np.ndarray
        Unnormalised log-probabilities, shape (K,).

    Returns
    -------
    np.ndarray
        Normalised log-probabilities, shape (K,).
    """
    return log_vec - _logsumexp(log_vec)


# ---------------------------------------------------------------------------
# K-Means initialisation (minimal, numpy-only)
# ---------------------------------------------------------------------------

def _kmeans_init(
    x: np.ndarray,
    k: int,
    max_iter: int = 50,
    rng: np.random.RandomState | None = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Simple K-Means to seed HMM emission parameters.

    Parameters
    ----------
    x : np.ndarray
        1-D data, shape (T,).
    k : int
        Number of clusters.
    max_iter : int
        Maximum iterations.
    rng : RandomState
        For reproducibility.

    Returns
    -------
    mu : np.ndarray, shape (k,)
        Cluster centres (sorted ascending by mean).
    sigma : np.ndarray, shape (k,)
        Cluster standard deviations.
    labels : np.ndarray, shape (T,)
        Cluster assignments.
    """
    if rng is None:
        rng = np.random.RandomState(42)

    n = len(x)

    # Initialise centres via quantiles (deterministic, stable)
    quantiles = np.linspace(0, 100, k + 2)[1:-1]  # e.g. [25, 50, 75] for k=3
    centres = np.percentile(x, quantiles)

    labels = np.zeros(n, dtype=np.int32)

    for _ in range(max_iter):
        # Assignment step
        dists = np.abs(x[:, None] - centres[None, :])  # (n, k)
        new_labels = np.argmin(dists, axis=1)

        # Check convergence
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels

        # Update step
        for j in range(k):
            mask = labels == j
            if np.any(mask):
                centres[j] = np.mean(x[mask])

    # Compute per-cluster statistics
    mu = np.zeros(k)
    sigma = np.zeros(k)
    for j in range(k):
        mask = labels == j
        if np.any(mask):
            mu[j] = np.mean(x[mask])
            sigma[j] = max(np.std(x[mask]), MIN_SIGMA)
        else:
            mu[j] = centres[j]
            sigma[j] = np.std(x) if np.std(x) > MIN_SIGMA else MIN_SIGMA

    # Sort clusters by mu ascending (Low Vol = lowest mean absolute return)
    order = np.argsort(np.abs(mu))
    mu = mu[order]
    sigma = sigma[order]
    # Re-map labels to match the sorted order
    inv_order = np.argsort(order)
    labels = inv_order[labels]

    return mu, sigma, labels


# ---------------------------------------------------------------------------
# StudentTHMM
# ---------------------------------------------------------------------------

@dataclass
class HMMParams:
    """Serialisable HMM parameter container."""

    n_regimes: int
    nu: float
    log_pi: List[float]          # Initial state log-probabilities
    log_A: List[List[float]]     # Transition matrix (log-space)
    mu: List[float]              # Emission location per regime
    sigma: List[float]           # Emission scale per regime
    log_likelihood: float = 0.0
    n_iter: int = 0
    fitted_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "HMMParams":
        return cls(**d)


class StudentTHMM:
    """Hidden Markov Model with Student-t emission distributions.

    Parameters
    ----------
    n_regimes : int
        Number of hidden states (regimes).  Default 3.
    nu : float
        Degrees of freedom for Student-t emissions.  Default 4.0.
        Lower = heavier tails.  Typical range for finance: 3-6.
        Fixed at init; not estimated during EM (numerically unstable).
    max_iter : int
        Maximum Baum-Welch (EM) iterations.  Default 100.
    tol : float
        Convergence tolerance on log-likelihood improvement.  Default 1e-6.
    random_state : int or None
        Random seed for reproducibility.

    Attributes
    ----------
    log_pi_ : np.ndarray, shape (K,)
        Log initial state distribution.
    log_A_ : np.ndarray, shape (K, K)
        Log transition matrix.  log_A_[i, j] = log P(s_{t+1}=j | s_t=i).
    mu_ : np.ndarray, shape (K,)
        Emission location (mean) per regime.
    sigma_ : np.ndarray, shape (K,)
        Emission scale (std) per regime.
    n_iter_ : int
        Number of EM iterations performed.
    log_likelihood_ : float
        Final log-likelihood after fit.
    is_fitted_ : bool
        Whether the model has been fit to data.
    """

    def __init__(
        self,
        n_regimes: int = 3,
        nu: float = 4.0,
        max_iter: int = 100,
        tol: float = 1e-6,
        random_state: Optional[int] = 42,
    ) -> None:
        if n_regimes < 2:
            raise ValueError(f"n_regimes must be >= 2, got {n_regimes}")
        if nu <= 2.0:
            raise ValueError(
                f"nu must be > 2 for finite variance, got {nu}. "
                f"Typical range: 3-6 for financial data."
            )

        self.n_regimes = n_regimes
        self.nu = float(nu)
        self.max_iter = max_iter
        self.tol = tol
        self.rng = np.random.RandomState(random_state) if random_state is not None else np.random.RandomState()

        # Parameters (set during fit)
        self.log_pi_: np.ndarray = np.full(n_regimes, -math.log(n_regimes))  # uniform
        self.log_A_: np.ndarray = np.zeros((n_regimes, n_regimes))
        self.mu_: np.ndarray = np.zeros(n_regimes)
        self.sigma_: np.ndarray = np.ones(n_regimes)

        self.n_iter_: int = 0
        self.log_likelihood_: float = -np.inf
        self.is_fitted_: bool = False

    # ----- Emission probabilities -----------------------------------------

    def _compute_log_emission(self, returns: np.ndarray) -> np.ndarray:
        """Compute log emission probabilities for all regimes.

        Parameters
        ----------
        returns : np.ndarray, shape (T,)
            Observed log-returns.

        Returns
        -------
        log_B : np.ndarray, shape (T, K)
            log_B[t, k] = log P(x_t | state=k).
        """
        T = len(returns)
        K = self.n_regimes
        log_B = np.empty((T, K))

        for k in range(K):
            log_B[:, k] = _student_t_logpdf(returns, self.mu_[k], self.sigma_[k], self.nu)

        # Clamp to avoid -inf propagation
        np.clip(log_B, LOG_ZERO, None, out=log_B)

        return log_B

    # ----- Forward algorithm (log-space) ----------------------------------

    def _forward(self, log_B: np.ndarray) -> Tuple[np.ndarray, float]:
        """Forward algorithm in log-space.

        Parameters
        ----------
        log_B : np.ndarray, shape (T, K)
            Log emission probabilities.

        Returns
        -------
        log_alpha : np.ndarray, shape (T, K)
            Forward log-probabilities.
            log_alpha[t, k] = log P(x_1...x_t, s_t=k).
        log_likelihood : float
            log P(x_1...x_T) under the model.
        """
        T, K = log_B.shape
        log_alpha = np.full((T, K), LOG_ZERO)

        # Initialisation: alpha_0(k) = pi(k) * B_k(x_0)
        log_alpha[0] = self.log_pi_ + log_B[0]

        # Recursion: alpha_t(j) = B_j(x_t) * sum_i(alpha_{t-1}(i) * A(i,j))
        for t in range(1, T):
            for j in range(K):
                # log sum_i(alpha_{t-1}(i) * A(i,j))
                log_alpha[t, j] = (
                    _logsumexp(log_alpha[t - 1] + self.log_A_[:, j])
                    + log_B[t, j]
                )

        log_likelihood = float(_logsumexp(log_alpha[T - 1]))
        return log_alpha, log_likelihood

    # ----- Backward algorithm (log-space) ---------------------------------

    def _backward(self, log_B: np.ndarray) -> np.ndarray:
        """Backward algorithm in log-space.

        Parameters
        ----------
        log_B : np.ndarray, shape (T, K)
            Log emission probabilities.

        Returns
        -------
        log_beta : np.ndarray, shape (T, K)
            Backward log-probabilities.
            log_beta[t, k] = log P(x_{t+1}...x_T | s_t=k).
        """
        T, K = log_B.shape
        log_beta = np.full((T, K), LOG_ZERO)

        # Initialisation: beta_T(k) = 1 => log beta_T(k) = 0
        log_beta[T - 1] = 0.0

        # Recursion (backwards)
        for t in range(T - 2, -1, -1):
            for i in range(K):
                # log sum_j(A(i,j) * B_j(x_{t+1}) * beta_{t+1}(j))
                log_beta[t, i] = _logsumexp(
                    self.log_A_[i, :] + log_B[t + 1] + log_beta[t + 1]
                )

        return log_beta

    # ----- Viterbi decoding (log-space) -----------------------------------

    def _viterbi(self, log_B: np.ndarray) -> np.ndarray:
        """Viterbi algorithm — most likely state sequence.

        Parameters
        ----------
        log_B : np.ndarray, shape (T, K)
            Log emission probabilities.

        Returns
        -------
        states : np.ndarray, shape (T,), dtype int
            Most likely regime at each time step.
        """
        T, K = log_B.shape
        delta = np.full((T, K), LOG_ZERO)    # max log-prob
        psi = np.zeros((T, K), dtype=np.int32)  # backpointers

        # Initialisation
        delta[0] = self.log_pi_ + log_B[0]

        # Recursion
        for t in range(1, T):
            for j in range(K):
                scores = delta[t - 1] + self.log_A_[:, j]
                psi[t, j] = int(np.argmax(scores))
                delta[t, j] = scores[psi[t, j]] + log_B[t, j]

        # Backtracking
        states = np.zeros(T, dtype=np.int32)
        states[T - 1] = int(np.argmax(delta[T - 1]))
        for t in range(T - 2, -1, -1):
            states[t] = psi[t + 1, states[t + 1]]

        return states

    # ----- Baum-Welch (EM) ------------------------------------------------

    def _initialise_params(self, returns: np.ndarray) -> None:
        """Initialise HMM parameters from data using K-Means.

        Sets log_pi_, log_A_, mu_, sigma_ to sensible starting values.

        Parameters
        ----------
        returns : np.ndarray, shape (T,)
            Observed log-returns.
        """
        K = self.n_regimes

        # K-Means for emission parameters
        mu, sigma, _ = _kmeans_init(returns, K, rng=self.rng)
        self.mu_ = mu
        self.sigma_ = sigma

        # Transition matrix: sticky (0.9 on diagonal, uniform off-diagonal)
        diag_prob = 0.9
        off_diag_prob = (1.0 - diag_prob) / max(K - 1, 1)
        A = np.full((K, K), off_diag_prob)
        np.fill_diagonal(A, diag_prob)
        self.log_A_ = np.log(np.clip(A, MIN_TRANSITION, None))

        # Initial distribution: uniform
        self.log_pi_ = np.full(K, -math.log(K))

        log.debug(
            "Init: mu=%s, sigma=%s",
            np.round(self.mu_, 6).tolist(),
            np.round(self.sigma_, 6).tolist(),
        )

    def _baum_welch_step(
        self, returns: np.ndarray, log_B: np.ndarray
    ) -> Tuple[float, np.ndarray]:
        """Single Baum-Welch (EM) iteration.

        E-step: compute gamma (state posteriors) and xi (transition posteriors)
                via the forward-backward algorithm.
        M-step: re-estimate pi, A, mu, sigma from sufficient statistics.

        Parameters
        ----------
        returns : np.ndarray, shape (T,)
        log_B : np.ndarray, shape (T, K)

        Returns
        -------
        log_likelihood : float
            Model log-likelihood after this iteration's E-step.
        log_B : np.ndarray, shape (T, K)
            Updated log emission matrix (recomputed after M-step).
        """
        T, K = log_B.shape

        # ===== E-STEP =====

        # Forward pass
        log_alpha, ll = self._forward(log_B)

        # Backward pass
        log_beta = self._backward(log_B)

        # Gamma: P(s_t = k | X)
        # log_gamma[t, k] = log_alpha[t, k] + log_beta[t, k] - log P(X)
        log_gamma = log_alpha + log_beta
        for t in range(T):
            log_gamma[t] = _log_normalise(log_gamma[t])
        gamma = np.exp(log_gamma)

        # Xi: P(s_t=i, s_{t+1}=j | X) for t = 0..T-2
        # log_xi[t, i, j] = log_alpha[t,i] + log_A[i,j] + log_B[t+1,j] + log_beta[t+1,j] - log P(X)
        log_xi = np.full((T - 1, K, K), LOG_ZERO)
        for t in range(T - 1):
            for i in range(K):
                for j in range(K):
                    log_xi[t, i, j] = (
                        log_alpha[t, i]
                        + self.log_A_[i, j]
                        + log_B[t + 1, j]
                        + log_beta[t + 1, j]
                    )
            # Normalise over (i, j) for this time step
            log_norm = _logsumexp(log_xi[t].ravel())
            log_xi[t] -= log_norm

        xi = np.exp(log_xi)

        # ===== M-STEP =====

        # Update initial distribution
        self.log_pi_ = _log_normalise(log_gamma[0])

        # Update transition matrix
        xi_sum = xi.sum(axis=0)           # shape (K, K)
        gamma_sum = gamma[:-1].sum(axis=0)  # shape (K,) — exclude last time step
        for i in range(K):
            denom = max(gamma_sum[i], 1e-300)
            for j in range(K):
                self.log_A_[i, j] = math.log(max(xi_sum[i, j] / denom, MIN_TRANSITION))
            # Re-normalise row in log-space
            self.log_A_[i] = _log_normalise(self.log_A_[i])

        # Update emission parameters (mu, sigma)
        for k in range(K):
            gamma_k = gamma[:, k]         # shape (T,)
            w_sum = max(gamma_k.sum(), 1e-300)

            # Weighted mean
            self.mu_[k] = np.dot(gamma_k, returns) / w_sum

            # Weighted standard deviation
            diff = returns - self.mu_[k]
            var_k = np.dot(gamma_k, diff * diff) / w_sum
            self.sigma_[k] = max(math.sqrt(var_k), MIN_SIGMA)

        # Recompute emissions with updated parameters
        log_B_new = self._compute_log_emission(returns)

        return ll, log_B_new

    def fit(self, returns: np.ndarray) -> "StudentTHMM":
        """Fit the model via Baum-Welch (EM) with Student-t emissions.

        Parameters
        ----------
        returns : np.ndarray, shape (T,)
            Array of log-returns (or simple returns).  Must have T >= 10.

        Returns
        -------
        self
            The fitted model instance (for chaining).

        Raises
        ------
        ValueError
            If returns has fewer than 10 observations.
        """
        returns = np.asarray(returns, dtype=np.float64).ravel()
        T = len(returns)
        if T < 10:
            raise ValueError(f"Need at least 10 observations, got {T}")

        log.info(
            "Fitting StudentTHMM: K=%d, nu=%.1f, T=%d, max_iter=%d",
            self.n_regimes, self.nu, T, self.max_iter,
        )

        # Initialise parameters
        self._initialise_params(returns)

        # Initial emission matrix
        log_B = self._compute_log_emission(returns)

        prev_ll = -np.inf
        for iteration in range(1, self.max_iter + 1):
            ll, log_B = self._baum_welch_step(returns, log_B)

            # Check convergence
            improvement = ll - prev_ll
            if iteration % 10 == 0 or iteration == 1:
                log.debug("  iter %3d  LL=%.4f  delta=%.2e", iteration, ll, improvement)

            if improvement < 0 and iteration > 1:
                # Likelihood decreased — numerical instability, stop
                log.warning(
                    "LL decreased at iter %d (%.4f -> %.4f); stopping early.",
                    iteration, prev_ll, ll,
                )
                break

            if 0 <= improvement < self.tol and iteration > 1:
                log.info("Converged at iter %d (delta=%.2e < tol=%.2e)", iteration, improvement, self.tol)
                break

            prev_ll = ll

        self.n_iter_ = iteration
        self.log_likelihood_ = prev_ll
        self.is_fitted_ = True

        # Ensure regimes are ordered by volatility (sigma ascending)
        self._sort_regimes_by_volatility()

        log.info(
            "Fit complete: %d iters, LL=%.4f, mu=%s, sigma=%s",
            self.n_iter_,
            self.log_likelihood_,
            np.round(self.mu_, 6).tolist(),
            np.round(self.sigma_, 6).tolist(),
        )

        return self

    def _sort_regimes_by_volatility(self) -> None:
        """Re-order regimes so that sigma_[0] <= sigma_[1] <= sigma_[2].

        This ensures regime 0 is always "Low Vol", regime K-1 is "High Vol",
        regardless of how K-Means initialised the clusters.
        """
        order = np.argsort(self.sigma_)
        if np.array_equal(order, np.arange(self.n_regimes)):
            return  # already sorted

        self.mu_ = self.mu_[order]
        self.sigma_ = self.sigma_[order]
        self.log_pi_ = self.log_pi_[order]
        self.log_A_ = self.log_A_[np.ix_(order, order)]

        log.debug("Regimes reordered by volatility: %s", order.tolist())

    # ----- Inference methods ----------------------------------------------

    def predict(self, returns: np.ndarray) -> np.ndarray:
        """Viterbi decoding: most likely regime sequence.

        Parameters
        ----------
        returns : np.ndarray, shape (T,)

        Returns
        -------
        regimes : np.ndarray, shape (T,), dtype int
            Most likely regime at each time step.
        """
        self._check_fitted()
        returns = np.asarray(returns, dtype=np.float64).ravel()
        log_B = self._compute_log_emission(returns)
        return self._viterbi(log_B)

    def current_regime(self, returns: np.ndarray) -> int:
        """Return the current (latest) regime.

        Parameters
        ----------
        returns : np.ndarray, shape (T,)

        Returns
        -------
        int
            Regime index of the last observation.
        """
        regimes = self.predict(returns)
        return int(regimes[-1])

    def regime_probabilities(self, returns: np.ndarray) -> np.ndarray:
        """Forward-algorithm state probabilities (filtered).

        Parameters
        ----------
        returns : np.ndarray, shape (T,)

        Returns
        -------
        probs : np.ndarray, shape (T, K)
            probs[t, k] = P(s_t = k | x_1 ... x_t).
        """
        self._check_fitted()
        returns = np.asarray(returns, dtype=np.float64).ravel()
        log_B = self._compute_log_emission(returns)
        log_alpha, _ = self._forward(log_B)

        # Normalise each row to get filtered probabilities
        T, K = log_alpha.shape
        log_probs = np.empty_like(log_alpha)
        for t in range(T):
            log_probs[t] = _log_normalise(log_alpha[t])

        return np.exp(log_probs)

    def log_likelihood(self, returns: np.ndarray) -> float:
        """Compute log-likelihood of data under the fitted model.

        Parameters
        ----------
        returns : np.ndarray, shape (T,)

        Returns
        -------
        float
            log P(returns | model parameters).
        """
        self._check_fitted()
        returns = np.asarray(returns, dtype=np.float64).ravel()
        log_B = self._compute_log_emission(returns)
        _, ll = self._forward(log_B)
        return ll

    def _check_fitted(self) -> None:
        if not self.is_fitted_:
            raise RuntimeError("Model has not been fitted.  Call .fit() first.")

    # ----- Serialisation --------------------------------------------------

    def get_params(self) -> HMMParams:
        """Export fitted parameters as a serialisable dataclass."""
        self._check_fitted()
        return HMMParams(
            n_regimes=self.n_regimes,
            nu=self.nu,
            log_pi=self.log_pi_.tolist(),
            log_A=self.log_A_.tolist(),
            mu=self.mu_.tolist(),
            sigma=self.sigma_.tolist(),
            log_likelihood=float(self.log_likelihood_),
            n_iter=self.n_iter_,
            fitted_at=datetime.now(timezone.utc).isoformat(),
        )

    def load_params(self, params: HMMParams) -> "StudentTHMM":
        """Restore parameters from a serialised HMMParams.

        Parameters
        ----------
        params : HMMParams

        Returns
        -------
        self
        """
        if params.n_regimes != self.n_regimes:
            raise ValueError(
                f"Param n_regimes={params.n_regimes} != model n_regimes={self.n_regimes}"
            )
        self.log_pi_ = np.array(params.log_pi, dtype=np.float64)
        self.log_A_ = np.array(params.log_A, dtype=np.float64)
        self.mu_ = np.array(params.mu, dtype=np.float64)
        self.sigma_ = np.array(params.sigma, dtype=np.float64)
        self.nu = params.nu
        self.log_likelihood_ = params.log_likelihood
        self.n_iter_ = params.n_iter
        self.is_fitted_ = True
        return self


# ---------------------------------------------------------------------------
# WAL integration
# ---------------------------------------------------------------------------

def _parse_wal_prices(wal_dir: str | Path, lookback_days: int = 90) -> np.ndarray:
    """Extract log-returns from WAL PositionClosed events.

    Reads all .ndjson WAL files and extracts entry_price fields from
    PositionClosed events within the lookback window.  Computes
    log-returns as log(price_t / price_{t-1}).

    If insufficient PositionClosed events, falls back to RoutedOrder
    fill prices (which gives the sequence of prices the engine traded at).

    Parameters
    ----------
    wal_dir : str or Path
        Directory containing WAL .ndjson files.
    lookback_days : int
        Only consider events from the last N days.

    Returns
    -------
    np.ndarray
        Log-returns, shape (N-1,) where N is the number of price observations.

    Raises
    ------
    ValueError
        If fewer than 10 price observations found.
    """
    wal_path = Path(wal_dir)
    cutoff_ns = int(
        (datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp()
        * 1_000_000_000
    )

    prices: List[Tuple[int, float]] = []  # (timestamp_ns, price)

    # Scan all WAL files, including archive/
    wal_files = sorted(wal_path.glob("*.ndjson"))
    archive_dir = wal_path / "archive"
    if archive_dir.is_dir():
        wal_files.extend(sorted(archive_dir.glob("*.ndjson")))

    for wal_file in wal_files:
        try:
            text = wal_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = event.get("event_time_ns", 0)
            if ts < cutoff_ns:
                continue

            payload = event.get("payload", {})

            # Prefer PositionClosed (has entry_price)
            if "PositionClosed" in payload:
                d = payload["PositionClosed"]
                price = d.get("entry_price", 0.0)
                if price > 0:
                    prices.append((ts, price))
                # Also capture exit-implied price if final_pnl and qty available
                exit_price = d.get("exit_price", 0.0)
                if exit_price > 0:
                    prices.append((ts + 1, exit_price))

            # Fallback: RoutedOrder fill prices
            elif "RoutedOrder" in payload:
                d = payload["RoutedOrder"]
                price = d.get("limit_price", 0.0) or d.get("fill_price", 0.0)
                if price > 0:
                    prices.append((ts, price))

    if len(prices) < 10:
        raise ValueError(
            f"Insufficient price data from WAL: found {len(prices)} observations, "
            f"need at least 10.  lookback_days={lookback_days}, wal_dir={wal_dir}"
        )

    # Sort by timestamp, extract prices
    prices.sort(key=lambda x: x[0])
    price_arr = np.array([p for _, p in prices], dtype=np.float64)

    # Log-returns
    returns = np.diff(np.log(price_arr))

    # Remove any NaN or inf
    mask = np.isfinite(returns)
    returns = returns[mask]

    if len(returns) < 10:
        raise ValueError(
            f"Insufficient valid returns after cleaning: {len(returns)}, need >= 10"
        )

    log.info("Parsed %d returns from %d price observations in WAL", len(returns), len(prices))
    return returns


def calibrate_from_wal(
    wal_dir: str | Path = WAL_DIR,
    lookback_days: int = 90,
    n_regimes: int = 3,
    nu: float = 4.0,
    save_params: bool = True,
) -> StudentTHMM:
    """Load price data from WAL, fit HMM, optionally save params.

    Parameters
    ----------
    wal_dir : str or Path
        WAL directory containing .ndjson files.
    lookback_days : int
        Lookback window in days.
    n_regimes : int
        Number of regimes.
    nu : float
        Degrees of freedom for Student-t.
    save_params : bool
        If True, save fitted params to JSON sidecar in REPORT_DIR.

    Returns
    -------
    StudentTHMM
        Calibrated model instance.
    """
    returns = _parse_wal_prices(wal_dir, lookback_days)
    model = StudentTHMM(n_regimes=n_regimes, nu=nu)
    model.fit(returns)

    if save_params:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        params = model.get_params()
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = REPORT_DIR / f"hmm_params_{ts}.json"
        out_path.write_text(json.dumps(params.to_dict(), indent=2))
        log.info("Saved HMM params to %s", out_path)

    return model


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_regime_report(
    model: StudentTHMM,
    returns: np.ndarray,
    output_dir: str | Path = REPORT_DIR,
) -> str:
    """Generate a regime analysis report (JSON + text).

    Parameters
    ----------
    model : StudentTHMM
        Fitted model.
    returns : np.ndarray, shape (T,)
        Returns used for analysis.
    output_dir : str or Path
        Directory for output files.

    Returns
    -------
    str
        Path to the text report file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    returns = np.asarray(returns, dtype=np.float64).ravel()
    regimes = model.predict(returns)
    probs = model.regime_probabilities(returns)
    K = model.n_regimes
    T = len(returns)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # ------ Regime statistics ------
    regime_stats: Dict[int, Dict[str, Any]] = {}
    for k in range(K):
        mask = regimes == k
        count = int(mask.sum())
        if count > 0:
            r_k = returns[mask]
            regime_stats[k] = {
                "label": REGIME_LABELS.get(k, f"Regime {k}"),
                "count": count,
                "fraction": round(count / T, 4),
                "mean_return": round(float(np.mean(r_k)), 8),
                "std_return": round(float(np.std(r_k)), 8),
                "min_return": round(float(np.min(r_k)), 8),
                "max_return": round(float(np.max(r_k)), 8),
                "kurtosis": round(float(_kurtosis(r_k)), 4),
                "emission_mu": round(float(model.mu_[k]), 8),
                "emission_sigma": round(float(model.sigma_[k]), 8),
            }
        else:
            regime_stats[k] = {
                "label": REGIME_LABELS.get(k, f"Regime {k}"),
                "count": 0,
                "fraction": 0.0,
            }

    # ------ Transition matrix (exponentiated for readability) ------
    A = np.exp(model.log_A_)
    transition_matrix = [[round(float(A[i, j]), 6) for j in range(K)] for i in range(K)]

    # ------ Current regime ------
    current = int(regimes[-1])
    current_probs = probs[-1].tolist()

    # ------ Regime transitions count ------
    n_transitions = int(np.sum(regimes[1:] != regimes[:-1]))

    # ------ JSON report ------
    report_data = {
        "model": {
            "n_regimes": K,
            "nu": model.nu,
            "n_observations": T,
            "log_likelihood": round(model.log_likelihood_, 4),
            "n_iterations": model.n_iter_,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "current_regime": {
            "index": current,
            "label": REGIME_LABELS.get(current, f"Regime {current}"),
            "probabilities": {
                REGIME_LABELS.get(k, f"Regime {k}"): round(current_probs[k], 6)
                for k in range(K)
            },
        },
        "regime_stats": regime_stats,
        "transition_matrix": transition_matrix,
        "n_regime_transitions": n_transitions,
    }

    json_path = output_dir / f"hmm_regime_report_{ts}.json"
    json_path.write_text(json.dumps(report_data, indent=2))

    # ------ Text report ------
    lines = [
        "=" * 72,
        "  HMM Student-t Regime Report",
        f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "=" * 72,
        "",
        f"  Model:  K={K}, nu={model.nu}, T={T}",
        f"  LL={model.log_likelihood_:.4f}, converged in {model.n_iter_} iterations",
        "",
        "-" * 72,
        f"  Current Regime: {current} ({REGIME_LABELS.get(current, '?')})",
        "  Regime Probabilities:",
    ]
    for k in range(K):
        lines.append(f"    {REGIME_LABELS.get(k, f'Regime {k}'):>12s}: {current_probs[k]:.4f}")

    lines.extend(["", "-" * 72, "  Regime Statistics:", ""])

    for k in range(K):
        s = regime_stats[k]
        if s["count"] == 0:
            lines.append(f"  Regime {k} ({s['label']}): no observations")
            continue
        lines.append(
            f"  Regime {k} ({s['label']}):  "
            f"n={s['count']} ({s['fraction']:.1%})  "
            f"mean={s['mean_return']:.6f}  std={s['std_return']:.6f}  "
            f"kurt={s.get('kurtosis', 'N/A')}"
        )
        lines.append(
            f"    emission: mu={s['emission_mu']:.6f}, sigma={s['emission_sigma']:.6f}"
        )

    lines.extend(["", "-" * 72, "  Transition Matrix:", ""])
    header = "        " + "".join(f"  R{j:>2d}    " for j in range(K))
    lines.append(header)
    for i in range(K):
        row = f"  R{i:>2d}  " + "".join(f"  {transition_matrix[i][j]:.4f}" for j in range(K))
        lines.append(row)

    lines.extend([
        "",
        f"  Total regime transitions: {n_transitions} / {T - 1} time steps",
        f"  Transition rate: {n_transitions / max(T - 1, 1):.2%}",
        "",
        "=" * 72,
    ])

    txt_path = output_dir / f"hmm_regime_report_{ts}.txt"
    txt_path.write_text("\n".join(lines))

    log.info("Regime report written to %s", txt_path)
    log.info("Regime JSON  written to %s", json_path)

    return str(txt_path)


def _kurtosis(x: np.ndarray) -> float:
    """Excess kurtosis (Fisher definition, bias-corrected not needed here)."""
    n = len(x)
    if n < 4:
        return 0.0
    m = np.mean(x)
    s = np.std(x)
    if s < 1e-15:
        return 0.0
    return float(np.mean(((x - m) / s) ** 4) - 3.0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    """CLI entry point for HMM Student-t regime analysis."""
    parser = argparse.ArgumentParser(
        description="HMM with Student-t Emissions — regime detection from WAL data",
    )
    parser.add_argument(
        "--wal-dir",
        type=str,
        default=str(WAL_DIR),
        help=f"WAL directory (default: {WAL_DIR})",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=90,
        help="Lookback window in days (default: 90)",
    )
    parser.add_argument(
        "--n-regimes",
        type=int,
        default=3,
        help="Number of hidden regimes (default: 3)",
    )
    parser.add_argument(
        "--nu",
        type=float,
        default=4.0,
        help="Student-t degrees of freedom (default: 4.0)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(REPORT_DIR),
        help=f"Output directory for reports (default: {REPORT_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse WAL and print stats without fitting",
    )

    args = parser.parse_args()

    wal_dir = Path(args.wal_dir)
    if not wal_dir.is_dir():
        log.error("WAL directory not found: %s", wal_dir)
        return 1

    try:
        returns = _parse_wal_prices(wal_dir, args.lookback)
    except ValueError as e:
        log.error("Failed to parse WAL: %s", e)
        return 1

    log.info(
        "Loaded %d returns from WAL (lookback=%d days)",
        len(returns), args.lookback,
    )
    log.info(
        "Return stats: mean=%.6f, std=%.6f, min=%.6f, max=%.6f, kurtosis=%.2f",
        np.mean(returns), np.std(returns), np.min(returns), np.max(returns),
        _kurtosis(returns),
    )

    if args.dry_run:
        log.info("Dry run — skipping model fit.")
        return 0

    # Fit model
    model = StudentTHMM(n_regimes=args.n_regimes, nu=args.nu)
    model.fit(returns)

    # Save parameters
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    params = model.get_params()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    params_path = Path(args.output_dir) / f"hmm_params_{ts}.json"
    params_path.parent.mkdir(parents=True, exist_ok=True)
    params_path.write_text(json.dumps(params.to_dict(), indent=2))
    log.info("Saved params to %s", params_path)

    # Generate report
    report_path = generate_regime_report(model, returns, args.output_dir)
    log.info("Report: %s", report_path)

    # Print summary to stdout
    current = model.current_regime(returns)
    probs = model.regime_probabilities(returns)[-1]
    print()
    print(f"Current regime: {current} ({REGIME_LABELS.get(current, '?')})")
    for k in range(model.n_regimes):
        print(f"  P(regime={k}): {probs[k]:.4f}  [{REGIME_LABELS.get(k, '')}]")
    print(f"Model LL: {model.log_likelihood_:.4f}")
    print(f"Emission params:")
    for k in range(model.n_regimes):
        print(f"  Regime {k}: mu={model.mu_[k]:.6f}, sigma={model.sigma_[k]:.6f}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
