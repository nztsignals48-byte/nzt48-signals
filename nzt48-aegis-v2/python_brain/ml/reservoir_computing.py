"""Echo State Networks for Nonlinear Feature Extraction — Book 129.

Implements reservoir computing (Echo State Networks) for extracting
nonlinear temporal features from market data. The reservoir acts as
a high-dimensional nonlinear expansion of the input, capturing
complex temporal dependencies without backpropagation.

Key properties:
  - Echo state property: reservoir forgets initial conditions
  - Only the output layer is trained (ridge regression)
  - Spectral radius < 1 ensures stability
  - Sparse connectivity reduces computation

Components:
  - ReservoirConfig: Hyperparameter configuration
  - EchoStateNetwork: Full ESN with training and prediction
  - ReservoirFeatureExtractor: Extract reservoir states as features

Bridge.py integration:
    try:
        from python_brain.ml.reservoir_computing import (
            EchoStateNetwork, ReservoirConfig, ReservoirFeatureExtractor,
        )
        _esn_config = ReservoirConfig(n_reservoir=300, spectral_radius=0.95)
        _esn = EchoStateNetwork(n_input=10, n_output=1, config=_esn_config)
    except ImportError:
        _esn = None

    # Feature extraction for downstream ML:
    if _esn:
        extractor = ReservoirFeatureExtractor(n_input=10, config=_esn_config)
        features = extractor.extract(price_features_matrix)
        regime_score = extractor.regime_change_score(features)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("reservoir_computing")

__all__ = [
    "ReservoirConfig",
    "EchoStateNetwork",
    "ReservoirFeatureExtractor",
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ReservoirConfig:
    """Echo State Network hyperparameters.

    Attributes:
        n_reservoir: Number of reservoir neurons. Larger = more capacity
                     but slower. 300-500 is typical for financial data.
        spectral_radius: Spectral radius of reservoir weight matrix.
                         Controls memory length. Must be < 1.0 for
                         echo state property. 0.9-0.99 for long memory.
        input_scaling: Scaling factor for input-to-reservoir weights.
                       Controls nonlinearity strength. 0.05-0.5 typical.
        leak_rate: Leaky integrator rate (alpha). 0.0 = no leak,
                   1.0 = no memory. 0.1-0.5 typical for financial data.
        sparsity: Fraction of zero entries in reservoir matrix.
                  0.9 = 90% sparse. Higher = faster, less capacity.
        seed: Random seed for reproducibility.
    """
    n_reservoir: int = 500
    spectral_radius: float = 0.95
    input_scaling: float = 0.1
    leak_rate: float = 0.3
    sparsity: float = 0.9
    seed: Optional[int] = None

    def __post_init__(self) -> None:
        if self.spectral_radius >= 1.0:
            log.warning("Spectral radius %.3f >= 1.0 may violate echo state property",
                        self.spectral_radius)
        if not 0.0 <= self.leak_rate <= 1.0:
            raise ValueError(f"leak_rate must be in [0, 1], got {self.leak_rate}")
        if not 0.0 <= self.sparsity < 1.0:
            raise ValueError(f"sparsity must be in [0, 1), got {self.sparsity}")


# ---------------------------------------------------------------------------
# Echo State Network
# ---------------------------------------------------------------------------

class EchoStateNetwork:
    """Echo State Network for time series prediction.

    Architecture:
      Input (n_input) --> Reservoir (n_reservoir) --> Output (n_output)

    The reservoir is a large recurrent network with fixed random weights.
    Only the output weights (W_out) are trained via ridge regression.

    State update (leaky integrator):
      s(t) = (1 - alpha) * s(t-1) + alpha * tanh(W_in * x(t) + W * s(t-1))

    Output:
      y(t) = W_out * [1; x(t); s(t)]  (with bias and input concatenated)

    Attributes:
        n_input: Input dimension.
        n_output: Output dimension.
        config: Reservoir configuration.
    """

    def __init__(
        self,
        n_input: int,
        n_output: int,
        config: Optional[ReservoirConfig] = None,
    ) -> None:
        """Initialise Echo State Network.

        Args:
            n_input: Number of input features per time step.
            n_output: Number of output targets.
            config: Reservoir configuration. Uses defaults if None.
        """
        self.n_input = n_input
        self.n_output = n_output
        self.config = config or ReservoirConfig()

        self._n_res = self.config.n_reservoir

        # Set random seed
        self._rng = np.random.RandomState(self.config.seed)

        # Initialise weight matrices
        self._W_in: np.ndarray = np.array([])  # Input weights (n_res, n_input)
        self._W: np.ndarray = np.array([])      # Reservoir weights (n_res, n_res)
        self._W_out: Optional[np.ndarray] = None  # Output weights (n_output, 1+n_input+n_res)

        # Internal state
        self._state: np.ndarray = np.zeros(self._n_res)

        self._init_reservoir()

        log.info("ESN initialised: n_input=%d, n_reservoir=%d, n_output=%d, "
                 "spectral_radius=%.3f, leak_rate=%.2f",
                 n_input, self._n_res, n_output,
                 self.config.spectral_radius, self.config.leak_rate)

    def _init_reservoir(self) -> None:
        """Initialise reservoir weight matrices.

        1. W_in: Dense random matrix scaled by input_scaling
        2. W: Sparse random matrix scaled to target spectral_radius

        The spectral radius is the largest absolute eigenvalue of W.
        Scaling to a target spectral radius controls the memory
        and stability of the reservoir.
        """
        n_res = self._n_res

        # Input weights: uniform in [-input_scaling, input_scaling]
        self._W_in = self._rng.uniform(
            -self.config.input_scaling,
            self.config.input_scaling,
            (n_res, self.n_input),
        )

        # Reservoir weights: sparse random matrix
        W = self._rng.randn(n_res, n_res)

        # Apply sparsity mask
        mask = self._rng.random((n_res, n_res)) > self.config.sparsity
        W *= mask

        # Scale to target spectral radius
        # Compute spectral radius (largest absolute eigenvalue)
        if n_res <= 1000:
            eigenvalues = np.linalg.eigvals(W)
            current_radius = float(np.max(np.abs(eigenvalues)))
        else:
            # For large matrices, use power iteration approximation
            current_radius = self._power_iteration_spectral_radius(W, n_iter=100)

        if current_radius > 1e-10:
            W *= self.config.spectral_radius / current_radius

        self._W = W

        # Reset state
        self._state = np.zeros(n_res)

        log.debug("Reservoir initialised: density=%.2f, actual_spectral_radius=%.4f",
                  1.0 - self.config.sparsity,
                  self.config.spectral_radius)

    def _power_iteration_spectral_radius(
        self, W: np.ndarray, n_iter: int = 100
    ) -> float:
        """Approximate spectral radius via power iteration.

        Args:
            W: Square matrix.
            n_iter: Number of iterations.

        Returns:
            Approximate spectral radius.
        """
        n = W.shape[0]
        v = self._rng.randn(n)
        v /= np.linalg.norm(v)

        eigenvalue = 0.0
        for _ in range(n_iter):
            w = W @ v
            norm_w = np.linalg.norm(w)
            if norm_w < 1e-15:
                return 0.0
            eigenvalue = norm_w
            v = w / norm_w

        return eigenvalue

    def _update_state(self, x_t: np.ndarray) -> np.ndarray:
        """Update reservoir state with a single input.

        Leaky integrator equation:
          s(t) = (1 - alpha) * s(t-1) + alpha * tanh(W_in * x(t) + W * s(t-1))

        Args:
            x_t: Input vector of shape (n_input,).

        Returns:
            Updated state vector of shape (n_reservoir,).
        """
        alpha = self.config.leak_rate

        # Pre-activation: W_in * x + W * s
        pre_activation = self._W_in @ x_t + self._W @ self._state

        # Guard against NaN/Inf from numerical overflow
        pre_activation = np.nan_to_num(pre_activation, nan=0.0, posinf=10.0, neginf=-10.0)

        # Leaky integrator update
        self._state = (1.0 - alpha) * self._state + alpha * np.tanh(pre_activation)

        # Safety: clamp state to prevent runaway
        self._state = np.nan_to_num(self._state, nan=0.0, posinf=1.0, neginf=-1.0)

        return self._state.copy()

    def forward(self, X_seq: np.ndarray) -> np.ndarray:
        """Drive the reservoir with an input sequence, collecting states.

        Resets internal state before processing. For online use,
        call _update_state directly.

        Args:
            X_seq: Input sequence, shape (T, n_input) where T is
                   the number of time steps.

        Returns:
            Reservoir states, shape (T, n_reservoir).
        """
        T = X_seq.shape[0]
        states = np.zeros((T, self._n_res))

        # Reset state
        self._state = np.zeros(self._n_res)

        for t in range(T):
            states[t, :] = self._update_state(X_seq[t, :])

        return states

    def fit(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        ridge_alpha: float = 1e-4,
        washout: int = 50,
    ) -> Dict[str, float]:
        """Train the output layer via ridge regression.

        Drives the reservoir with X_train, collects states, then
        solves the regularised least-squares problem:

          W_out = Y * S_ext^T * (S_ext * S_ext^T + alpha * I)^{-1}

        where S_ext = [1; x(t); s(t)] is the extended state.

        Args:
            X_train: Training input, shape (T, n_input).
            y_train: Training targets, shape (T, n_output) or (T,).
            ridge_alpha: Ridge regression regularisation. Higher = more
                         regularisation, prevents overfitting.
            washout: Number of initial steps to discard (reservoir warmup).

        Returns:
            Dict with training metrics: mse, r2, n_train, washout.
        """
        if y_train.ndim == 1:
            y_train = y_train.reshape(-1, 1)

        T = X_train.shape[0]
        if T != y_train.shape[0]:
            raise ValueError(
                f"X_train and y_train length mismatch: {T} vs {y_train.shape[0]}"
            )

        if washout >= T:
            washout = max(T // 10, 1)
            log.warning("Washout reduced to %d (T=%d)", washout, T)

        # Collect reservoir states
        states = self.forward(X_train)

        # Extended state matrix: [bias, input, reservoir_state]
        ones = np.ones((T, 1))
        S_ext = np.hstack([ones, X_train, states])

        # Discard washout period
        S_ext = S_ext[washout:, :]
        Y = y_train[washout:, :]

        # Ridge regression: W_out = (S^T S + alpha I)^{-1} S^T Y
        n_ext = S_ext.shape[1]
        reg_matrix = S_ext.T @ S_ext + ridge_alpha * np.eye(n_ext)

        try:
            self._W_out = np.linalg.solve(reg_matrix, S_ext.T @ Y).T
        except np.linalg.LinAlgError:
            log.warning("Ridge regression singular — using pseudoinverse")
            self._W_out = (Y.T @ S_ext @ np.linalg.pinv(reg_matrix))

        # Training metrics
        Y_pred = S_ext @ self._W_out.T
        residuals = Y - Y_pred
        mse = float(np.mean(residuals ** 2))
        ss_res = float(np.sum(residuals ** 2))
        ss_tot = float(np.sum((Y - np.mean(Y, axis=0)) ** 2))
        r2 = 1.0 - ss_res / max(ss_tot, 1e-10)

        n_train = T - washout

        log.info("ESN trained: MSE=%.6f, R2=%.4f, n_train=%d, washout=%d",
                 mse, r2, n_train, washout)

        return {
            "mse": round(mse, 8),
            "r2": round(r2, 6),
            "n_train": n_train,
            "washout": washout,
            "n_reservoir": self._n_res,
            "ridge_alpha": ridge_alpha,
        }

    def predict(self, X_test: np.ndarray) -> np.ndarray:
        """Predict targets for a test input sequence.

        Drives the reservoir with X_test (continuing from the last
        training state) and applies the trained output weights.

        Args:
            X_test: Test input, shape (T, n_input).

        Returns:
            Predictions, shape (T, n_output).

        Raises:
            RuntimeError: If model has not been trained (fit not called).
        """
        if self._W_out is None:
            raise RuntimeError("ESN not trained — call fit() first")

        T = X_test.shape[0]
        states = self.forward(X_test)

        # Extended state
        ones = np.ones((T, 1))
        S_ext = np.hstack([ones, X_test, states])

        # Output: Y = S_ext @ W_out^T
        Y_pred = S_ext @ self._W_out.T

        return Y_pred

    def reset_state(self) -> None:
        """Reset the reservoir state to zeros."""
        self._state = np.zeros(self._n_res)


# ---------------------------------------------------------------------------
# Reservoir Feature Extractor
# ---------------------------------------------------------------------------

class ReservoirFeatureExtractor:
    """Extract nonlinear temporal features using a reservoir.

    Instead of training the ESN for prediction, we use the reservoir
    states directly as a high-dimensional nonlinear feature space
    for downstream ML models (XGBoost, etc.).

    The reservoir provides:
    1. Nonlinear expansion of inputs
    2. Temporal memory (fading memory of past inputs)
    3. Mixing of input channels

    Also provides regime change detection by monitoring the dynamics
    of the reservoir state space.

    Attributes:
        n_input: Input feature dimension.
        config: Reservoir configuration.
    """

    def __init__(
        self,
        n_input: int = 10,
        config: Optional[ReservoirConfig] = None,
    ) -> None:
        """Initialise feature extractor.

        Args:
            n_input: Number of input features.
            config: Reservoir configuration.
        """
        self.n_input = n_input
        self.config = config or ReservoirConfig(n_reservoir=300)
        self._esn = EchoStateNetwork(n_input, n_output=1, config=self.config)

    def extract(
        self,
        time_series: np.ndarray,
        washout: int = 20,
    ) -> np.ndarray:
        """Extract reservoir state features from input time series.

        Drives the reservoir and returns the full state trajectory,
        optionally with statistical summaries.

        Args:
            time_series: Input data, shape (T, n_input) or (T,) for
                         univariate. If 1-D, expanded to (T, 1).
            washout: Number of initial steps to discard.

        Returns:
            Feature matrix, shape (T - washout, n_reservoir).
        """
        if time_series.ndim == 1:
            time_series = time_series.reshape(-1, 1)

        if time_series.shape[1] != self.n_input:
            # Pad or truncate input features
            T = time_series.shape[0]
            padded = np.zeros((T, self.n_input))
            n_copy = min(time_series.shape[1], self.n_input)
            padded[:, :n_copy] = time_series[:, :n_copy]
            time_series = padded

        states = self._esn.forward(time_series)

        # Discard washout
        if washout >= states.shape[0]:
            washout = max(states.shape[0] // 10, 0)

        features = states[washout:, :]

        log.debug("Extracted %d reservoir features from %d time steps (washout=%d)",
                  features.shape[1], time_series.shape[0], washout)

        return features

    def extract_with_stats(
        self,
        time_series: np.ndarray,
        washout: int = 20,
        window: int = 10,
    ) -> np.ndarray:
        """Extract reservoir features augmented with rolling statistics.

        Adds rolling mean and std of reservoir states over a short
        window, capturing rate of change in the reservoir dynamics.

        Args:
            time_series: Input data, shape (T, n_input).
            washout: Warmup steps to discard.
            window: Rolling window for statistics.

        Returns:
            Augmented feature matrix, shape (T - washout - window, 3 * n_res).
            Columns: [states, rolling_mean, rolling_std].
        """
        raw_features = self.extract(time_series, washout=washout)
        T, n_res = raw_features.shape

        if T < window + 1:
            return raw_features

        # Rolling mean and std
        rolling_mean = np.zeros((T - window, n_res))
        rolling_std = np.zeros((T - window, n_res))

        for t in range(window, T):
            w = raw_features[t - window:t, :]
            rolling_mean[t - window, :] = np.mean(w, axis=0)
            rolling_std[t - window, :] = np.std(w, axis=0, ddof=1)

        # Align: trim raw_features to match rolling stats
        raw_trimmed = raw_features[window:, :]

        augmented = np.hstack([raw_trimmed, rolling_mean, rolling_std])
        return augmented

    def regime_change_score(
        self,
        states_history: np.ndarray,
        window: int = 20,
    ) -> float:
        """Detect regime changes via sudden shifts in reservoir dynamics.

        Compares the distribution of reservoir states in the recent
        window vs. the preceding window using the Frobenius norm
        of the covariance difference.

        A high score indicates the reservoir dynamics have changed
        significantly, suggesting a regime transition.

        Args:
            states_history: Reservoir states, shape (T, n_reservoir).
                            From extract() or forward().
            window: Window size for comparison.

        Returns:
            Regime change score (0 = stable, higher = more change).
            Returns 0.0 if insufficient data.
        """
        T = states_history.shape[0]
        if T < 2 * window:
            return 0.0

        # Recent window vs. preceding window
        recent = states_history[-window:, :]
        preceding = states_history[-2 * window:-window, :]

        # Compute covariance matrices
        cov_recent = np.cov(recent, rowvar=False)
        cov_preceding = np.cov(preceding, rowvar=False)

        # Handle 1-D case
        if cov_recent.ndim == 0:
            diff = abs(float(cov_recent) - float(cov_preceding))
            scale = max(abs(float(cov_preceding)), 1e-10)
            return diff / scale

        # Frobenius norm of covariance difference
        cov_diff = cov_recent - cov_preceding
        frobenius = float(np.sqrt(np.sum(cov_diff ** 2)))

        # Normalise by the scale of the preceding covariance
        scale = float(np.sqrt(np.sum(cov_preceding ** 2)))
        if scale < 1e-10:
            return 0.0

        score = frobenius / scale

        # Also check mean shift
        mean_recent = np.mean(recent, axis=0)
        mean_preceding = np.mean(preceding, axis=0)
        mean_shift = float(np.linalg.norm(mean_recent - mean_preceding))
        mean_scale = max(float(np.linalg.norm(mean_preceding)), 1e-10)
        mean_score = mean_shift / mean_scale

        # Composite score
        composite = 0.6 * score + 0.4 * mean_score

        if composite > 1.0:
            log.info("Regime change detected: score=%.3f (cov=%.3f, mean=%.3f)",
                     composite, score, mean_score)

        return composite
