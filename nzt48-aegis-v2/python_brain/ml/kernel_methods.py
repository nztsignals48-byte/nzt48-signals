"""Gaussian Processes + SVR for Nonlinear Regression — Book 114.

Numpy-only implementation of kernel methods for market prediction:
  - RBF and Matern-3/2 kernels
  - Gaussian Process regression with Cholesky-based inference
  - Nystrom approximation for scalable GP (m << n inducing points)
  - Simplified dual-formulation SVR

GP provides uncertainty estimates (prediction intervals) which are
critical for position sizing: wide uncertainty → smaller position.

State: /app/data/models/gp_*.npz

Bridge.py integration:
    try:
        from python_brain.ml.kernel_methods import (
            GaussianProcess, RBFKernel, MaternKernel,
            NystromApproximation, SVR,
        )
    except ImportError:
        pass
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger(__name__)

__all__ = [
    "RBFKernel",
    "MaternKernel",
    "GaussianProcess",
    "NystromApproximation",
    "SVR",
]

# ── Paths ──────────────────────────────────────────────────────────────
MODEL_DIR = Path("/app/data/models")

# ── Constants ──────────────────────────────────────────────────────────
EPSILON = 1e-8
JITTER = 1e-6          # Numerical stability for Cholesky
MAX_MATRIX_SIZE = 5000  # Cap for full GP (memory safety)


# ── Kernels ────────────────────────────────────────────────────────────

class RBFKernel:
    """Radial Basis Function (Squared Exponential) kernel.

    K(x, y) = sigma_f^2 * exp(-||x - y||^2 / (2 * length_scale^2))

    The RBF kernel assumes smooth functions. Good for price predictions
    where nearby feature values should produce similar outputs.

    Args:
        length_scale: controls how quickly correlation decays with distance
        sigma_f: signal variance (output scale)
    """

    def __init__(self, length_scale: float = 1.0, sigma_f: float = 1.0) -> None:
        self.length_scale = length_scale
        self.sigma_f = sigma_f

    def __call__(self, X1: np.ndarray, X2: np.ndarray) -> np.ndarray:
        """Compute kernel matrix K(X1, X2).

        Args:
            X1: first input matrix (n1, d)
            X2: second input matrix (n2, d)

        Returns:
            Kernel matrix (n1, n2)
        """
        # Efficient squared distance computation
        # ||x - y||^2 = ||x||^2 + ||y||^2 - 2 x^T y
        sq1 = np.sum(X1 ** 2, axis=1, keepdims=True)  # (n1, 1)
        sq2 = np.sum(X2 ** 2, axis=1, keepdims=True)  # (n2, 1)
        sq_dist = sq1 + sq2.T - 2.0 * (X1 @ X2.T)
        sq_dist = np.maximum(sq_dist, 0.0)  # Numerical safety

        return self.sigma_f ** 2 * np.exp(-sq_dist / (2.0 * self.length_scale ** 2))

    def diagonal(self, X: np.ndarray) -> np.ndarray:
        """Compute diagonal of K(X, X) efficiently.

        Args:
            X: input matrix (n, d)

        Returns:
            Diagonal values (n,)
        """
        return np.full(X.shape[0], self.sigma_f ** 2)

    def __repr__(self) -> str:
        return f"RBFKernel(length_scale={self.length_scale:.4f}, sigma_f={self.sigma_f:.4f})"


class MaternKernel:
    """Matern-3/2 kernel.

    K(x, y) = sigma_f^2 * (1 + sqrt(3)*r/l) * exp(-sqrt(3)*r/l)
    where r = ||x - y||, l = length_scale

    Less smooth than RBF — allows for more rugged function surfaces.
    Better for financial data which has regime changes and jumps.

    Args:
        length_scale: controls correlation decay
        sigma_f: signal variance
    """

    def __init__(self, length_scale: float = 1.0, sigma_f: float = 1.0) -> None:
        self.length_scale = length_scale
        self.sigma_f = sigma_f

    def __call__(self, X1: np.ndarray, X2: np.ndarray) -> np.ndarray:
        """Compute Matern-3/2 kernel matrix.

        Args:
            X1: first input matrix (n1, d)
            X2: second input matrix (n2, d)

        Returns:
            Kernel matrix (n1, n2)
        """
        sq1 = np.sum(X1 ** 2, axis=1, keepdims=True)
        sq2 = np.sum(X2 ** 2, axis=1, keepdims=True)
        sq_dist = sq1 + sq2.T - 2.0 * (X1 @ X2.T)
        sq_dist = np.maximum(sq_dist, 0.0)
        r = np.sqrt(sq_dist)

        sqrt3_r_l = math.sqrt(3.0) * r / max(self.length_scale, EPSILON)

        return self.sigma_f ** 2 * (1.0 + sqrt3_r_l) * np.exp(-sqrt3_r_l)

    def diagonal(self, X: np.ndarray) -> np.ndarray:
        """Compute diagonal of K(X, X)."""
        return np.full(X.shape[0], self.sigma_f ** 2)

    def __repr__(self) -> str:
        return f"MaternKernel(length_scale={self.length_scale:.4f}, sigma_f={self.sigma_f:.4f})"


# ── Gaussian Process Regression ────────────────────────────────────────

class GaussianProcess:
    """Gaussian Process regression with exact inference.

    Provides probabilistic predictions: both mean (point estimate) and
    variance (uncertainty). Uncertainty is critical for trading:
    - Low uncertainty → confident prediction → larger position
    - High uncertainty → unsure → smaller position or no trade

    Uses Cholesky decomposition for numerically stable inference.

    Args:
        kernel: kernel function (RBFKernel or MaternKernel)
        noise: observation noise variance (sigma_n^2)
    """

    def __init__(
        self,
        kernel: Union[RBFKernel, MaternKernel, None] = None,
        noise: float = 1e-3,
    ) -> None:
        self.kernel = kernel or RBFKernel()
        self.noise = noise

        # Fitted state
        self.X_train: Optional[np.ndarray] = None
        self.y_train: Optional[np.ndarray] = None
        self.L: Optional[np.ndarray] = None       # Cholesky factor of K + noise*I
        self.alpha: Optional[np.ndarray] = None    # L^T \ (L \ y)
        self._fitted = False

    def fit(self, X_train: np.ndarray, y_train: np.ndarray) -> None:
        """Fit the GP to training data.

        Computes and stores the Cholesky decomposition of the kernel
        matrix plus noise for efficient prediction.

        Args:
            X_train: training inputs (n, d)
            y_train: training targets (n,)
        """
        n = X_train.shape[0]
        if n > MAX_MATRIX_SIZE:
            log.warning("GP.fit: n=%d exceeds MAX_MATRIX_SIZE=%d. "
                        "Consider NystromApproximation.", n, MAX_MATRIX_SIZE)
            # Subsample for safety
            idx = np.random.choice(n, MAX_MATRIX_SIZE, replace=False)
            X_train = X_train[idx]
            y_train = y_train[idx]
            n = MAX_MATRIX_SIZE

        self.X_train = X_train.copy()
        self.y_train = y_train.copy()

        # Kernel matrix
        K = self.kernel(X_train, X_train)

        # Add noise + jitter for numerical stability
        K_noisy = K + (self.noise + JITTER) * np.eye(n)

        # Cholesky decomposition: K_noisy = L @ L^T
        self.L = self._safe_cholesky(K_noisy)

        # alpha = K_noisy^{-1} @ y = L^T \ (L \ y)
        self.alpha = self._cholesky_solve(self.L, y_train)

        self._fitted = True
        log.info("GP fitted: n=%d, kernel=%s, noise=%.6f", n, self.kernel, self.noise)

    def predict(
        self,
        X_test: np.ndarray,
        return_std: bool = True,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Predict mean and variance at test points.

        Args:
            X_test: test inputs (n_test, d)
            return_std: if True, return std dev instead of variance

        Returns:
            Tuple of (mean, variance_or_std), each shape (n_test,)
        """
        if not self._fitted:
            n_test = X_test.shape[0]
            log.warning("GP.predict called before fit — returning zeros")
            return np.zeros(n_test), np.ones(n_test)

        # K(X_test, X_train)
        K_star = self.kernel(X_test, self.X_train)  # (n_test, n_train)

        # Predictive mean: mu = K_star @ alpha
        mu = K_star @ self.alpha

        # Predictive variance: K(X_test, X_test) - v^T @ v
        # where v = L \ K_star^T
        v = np.linalg.solve(self.L, K_star.T)  # (n_train, n_test)

        # K(X_test, X_test) diagonal
        k_diag = self.kernel.diagonal(X_test)

        # Variance = k_diag - sum(v^2, axis=0)
        var = k_diag - np.sum(v ** 2, axis=0)
        var = np.maximum(var, 0.0)  # Numerical safety

        if return_std:
            return mu, np.sqrt(var)
        return mu, var

    def _cholesky_solve(self, L: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Solve L @ L^T @ x = b using Cholesky factors.

        Two triangular solves: L @ z = b, then L^T @ x = z.

        Args:
            L: lower triangular Cholesky factor (n, n)
            b: right-hand side (n,) or (n, k)

        Returns:
            Solution x with same shape as b
        """
        # Forward substitution: L @ z = b
        z = np.linalg.solve(L, b)
        # Back substitution: L^T @ x = z
        x = np.linalg.solve(L.T, z)
        return x

    def _safe_cholesky(self, K: np.ndarray) -> np.ndarray:
        """Cholesky decomposition with progressive jitter on failure.

        Args:
            K: positive semi-definite matrix

        Returns:
            Lower triangular Cholesky factor L
        """
        jitter = JITTER
        n = K.shape[0]
        for attempt in range(6):
            try:
                L = np.linalg.cholesky(K + jitter * np.eye(n))
                return L
            except np.linalg.LinAlgError:
                jitter *= 10
                log.debug("Cholesky failed, increasing jitter to %.2e", jitter)

        # Last resort: eigendecomposition repair
        log.warning("Cholesky failed after 6 attempts, using eigendecomposition repair")
        eigvals, eigvecs = np.linalg.eigh(K)
        eigvals = np.maximum(eigvals, JITTER)
        K_repaired = eigvecs @ np.diag(eigvals) @ eigvecs.T
        return np.linalg.cholesky(K_repaired + JITTER * np.eye(n))

    def log_marginal_likelihood(self) -> float:
        """Compute log marginal likelihood of the fitted model.

        log p(y|X) = -0.5 * (y^T alpha + sum(log(diag(L))) + n*log(2*pi))

        Used for hyperparameter optimization (kernel parameters).

        Returns:
            Log marginal likelihood (higher = better fit)
        """
        if not self._fitted:
            return float("-inf")

        n = len(self.y_train)

        # Data fit term: -0.5 * y^T @ alpha
        data_fit = -0.5 * float(self.y_train @ self.alpha)

        # Complexity penalty: -sum(log(diag(L)))
        complexity = -float(np.sum(np.log(np.diag(self.L))))

        # Constant: -0.5 * n * log(2*pi)
        constant = -0.5 * n * math.log(2.0 * math.pi)

        lml = data_fit + complexity + constant
        return lml

    def optimize_hyperparameters(
        self,
        X: np.ndarray,
        y: np.ndarray,
        n_restarts: int = 5,
    ) -> Dict[str, float]:
        """Simple grid search for kernel hyperparameters.

        Optimizes length_scale and sigma_f by maximizing log marginal likelihood.

        Args:
            X: training inputs
            y: training targets
            n_restarts: number of random restarts

        Returns:
            Dict with best hyperparameters and LML
        """
        best_lml = float("-inf")
        best_params: Dict[str, float] = {}

        # Grid of length_scales and sigma_f values
        length_scales = [0.1, 0.3, 0.5, 1.0, 2.0, 5.0, 10.0]
        sigma_fs = [0.1, 0.5, 1.0, 2.0]
        noises = [1e-4, 1e-3, 1e-2, 0.1]

        for ls in length_scales:
            for sf in sigma_fs:
                for ns in noises:
                    try:
                        if isinstance(self.kernel, RBFKernel):
                            test_kernel = RBFKernel(length_scale=ls, sigma_f=sf)
                        else:
                            test_kernel = MaternKernel(length_scale=ls, sigma_f=sf)

                        gp = GaussianProcess(kernel=test_kernel, noise=ns)
                        gp.fit(X, y)
                        lml = gp.log_marginal_likelihood()

                        if lml > best_lml:
                            best_lml = lml
                            best_params = {
                                "length_scale": ls,
                                "sigma_f": sf,
                                "noise": ns,
                                "lml": lml,
                            }
                    except Exception:
                        continue

        # Apply best parameters
        if best_params:
            if isinstance(self.kernel, RBFKernel):
                self.kernel = RBFKernel(
                    length_scale=best_params["length_scale"],
                    sigma_f=best_params["sigma_f"],
                )
            else:
                self.kernel = MaternKernel(
                    length_scale=best_params["length_scale"],
                    sigma_f=best_params["sigma_f"],
                )
            self.noise = best_params["noise"]
            self.fit(X, y)

            log.info("GP hyperparameters optimized: %s, LML=%.4f",
                     self.kernel, best_lml)

        return best_params

    def save(self, path: Optional[Path] = None) -> str:
        """Save fitted GP to disk."""
        save_path = path or (MODEL_DIR / "gp_latest.npz")
        save_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "X_train": self.X_train,
            "y_train": self.y_train,
            "alpha": self.alpha,
            "L": self.L,
        }
        np.savez(str(save_path), **data)

        # Save metadata separately
        meta_path = save_path.with_suffix(".json")
        meta = {
            "kernel_type": type(self.kernel).__name__,
            "length_scale": getattr(self.kernel, "length_scale", 1.0),
            "sigma_f": getattr(self.kernel, "sigma_f", 1.0),
            "noise": self.noise,
            "n_train": len(self.X_train) if self.X_train is not None else 0,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        log.info("GP saved to %s", save_path)
        return str(save_path)

    def load(self, path: Optional[Path] = None) -> bool:
        """Load fitted GP from disk."""
        load_path = path or (MODEL_DIR / "gp_latest.npz")
        if not load_path.exists():
            return False

        try:
            data = np.load(str(load_path))
            self.X_train = data["X_train"]
            self.y_train = data["y_train"]
            self.alpha = data["alpha"]
            self.L = data["L"]
            self._fitted = True

            # Load metadata
            meta_path = load_path.with_suffix(".json")
            if meta_path.exists():
                with open(meta_path) as f:
                    meta = json.load(f)
                ls = meta.get("length_scale", 1.0)
                sf = meta.get("sigma_f", 1.0)
                if meta.get("kernel_type") == "MaternKernel":
                    self.kernel = MaternKernel(length_scale=ls, sigma_f=sf)
                else:
                    self.kernel = RBFKernel(length_scale=ls, sigma_f=sf)
                self.noise = meta.get("noise", 1e-3)

            log.info("GP loaded from %s (n_train=%d)", load_path, len(self.X_train))
            return True
        except Exception as e:
            log.error("GP load failed: %s", e)
            return False


# ── Nystrom Approximation ─────────────────────────────────────────────

class NystromApproximation:
    """Nystrom low-rank approximation for scalable kernel methods.

    Approximates the kernel matrix using m << n inducing (landmark) points:
        K ≈ K_nm @ K_mm^{-1} @ K_mn

    This reduces the O(n^3) cost of GP inference to O(n * m^2), enabling
    GP on datasets of 10K+ points.

    Args:
        kernel: kernel function
        n_components: number of inducing points (m)
    """

    def __init__(
        self,
        kernel: Union[RBFKernel, MaternKernel, None] = None,
        n_components: int = 100,
    ) -> None:
        self.kernel = kernel or RBFKernel()
        self.n_components = n_components

        # Fitted state
        self.components: Optional[np.ndarray] = None  # Landmark points (m, d)
        self.K_mm_inv_sqrt: Optional[np.ndarray] = None  # K_mm^{-1/2}
        self._fitted = False

    def fit(self, X: np.ndarray) -> "NystromApproximation":
        """Select inducing points and compute approximation basis.

        Uses k-means-like selection: subsample + slight perturbation
        for diversity.

        Args:
            X: training data (n, d)

        Returns:
            self (for chaining)
        """
        n, d = X.shape
        m = min(self.n_components, n)

        # Select inducing points via random subsample
        indices = np.random.choice(n, m, replace=False)
        self.components = X[indices].copy()

        # Compute K_mm (kernel between inducing points)
        K_mm = self.kernel(self.components, self.components)
        K_mm += JITTER * np.eye(m)  # Stability

        # Eigendecomposition for K_mm^{-1/2}
        eigvals, eigvecs = np.linalg.eigh(K_mm)
        eigvals = np.maximum(eigvals, JITTER)

        # K_mm^{-1/2} = V @ diag(1/sqrt(lambda)) @ V^T
        self.K_mm_inv_sqrt = eigvecs @ np.diag(1.0 / np.sqrt(eigvals)) @ eigvecs.T

        self._fitted = True
        log.info("Nystrom fitted: m=%d components from n=%d samples", m, n)
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        """Transform data into Nystrom feature space.

        Maps n-dimensional kernel space to m-dimensional feature space:
            Phi(X) = K_nm @ K_mm^{-1/2}

        Args:
            X: input data (n, d)

        Returns:
            Nystrom features (n, m)
        """
        if not self._fitted:
            log.error("NystromApproximation.transform called before fit")
            return X

        # K(X, components)
        K_nm = self.kernel(X, self.components)  # (n, m)

        # Transform: Phi = K_nm @ K_mm^{-1/2}
        features = K_nm @ self.K_mm_inv_sqrt

        return features

    def get_approximate_kernel(self, X1: np.ndarray, X2: np.ndarray) -> np.ndarray:
        """Compute approximate kernel matrix using Nystrom features.

        K_approx = Phi(X1) @ Phi(X2)^T

        Args:
            X1: first input (n1, d)
            X2: second input (n2, d)

        Returns:
            Approximate kernel matrix (n1, n2)
        """
        if not self._fitted:
            return self.kernel(X1, X2)

        phi1 = self.transform(X1)
        phi2 = self.transform(X2)
        return phi1 @ phi2.T


# ── Support Vector Regression ─────────────────────────────────────────

class SVR:
    """Support Vector Regression (simplified dual formulation).

    Epsilon-insensitive loss with kernel trick:
      min 0.5 * ||w||^2 + C * sum(xi_i)
      s.t. |y_i - f(x_i)| <= epsilon + xi_i

    Uses a simplified coordinate descent on the dual problem.
    For large datasets, use NystromApproximation to reduce dimensionality
    first, then apply SVR on the Nystrom features.

    Args:
        kernel: kernel function
        C: regularization parameter (higher = less regularization)
        epsilon: tube width (predictions within epsilon of target incur no loss)
    """

    def __init__(
        self,
        kernel: Union[RBFKernel, MaternKernel, None] = None,
        C: float = 1.0,
        epsilon: float = 0.1,
    ) -> None:
        self.kernel = kernel or RBFKernel()
        self.C = C
        self.epsilon = epsilon

        # Fitted state
        self.X_train: Optional[np.ndarray] = None
        self.alpha_diff: Optional[np.ndarray] = None  # alpha_i - alpha_i*
        self.bias: float = 0.0
        self.support_indices: Optional[np.ndarray] = None
        self._fitted = False

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        C: Optional[float] = None,
        epsilon: Optional[float] = None,
        max_iter: int = 1000,
        tol: float = 1e-4,
    ) -> None:
        """Fit SVR using simplified SMO-style coordinate descent.

        Args:
            X: training inputs (n, d)
            y: training targets (n,)
            C: override regularization parameter
            epsilon: override tube width
            max_iter: maximum iterations
            tol: convergence tolerance
        """
        C_eff = C if C is not None else self.C
        eps = epsilon if epsilon is not None else self.epsilon

        n = X.shape[0]
        if n > MAX_MATRIX_SIZE:
            log.warning("SVR.fit: n=%d exceeds MAX_MATRIX_SIZE, subsampling", n)
            idx = np.random.choice(n, MAX_MATRIX_SIZE, replace=False)
            X = X[idx]
            y = y[idx]
            n = MAX_MATRIX_SIZE

        self.X_train = X.copy()

        # Compute full kernel matrix
        K = self.kernel(X, X)

        # Dual variables: alpha_i (for y_i - f(x_i) >= eps + xi)
        #                 alpha_i* (for f(x_i) - y_i >= eps + xi)
        alpha = np.zeros(n)       # alpha_i
        alpha_star = np.zeros(n)  # alpha_i*

        # Simplified coordinate descent
        for iteration in range(max_iter):
            max_change = 0.0

            for i in range(n):
                # Current prediction at point i
                f_i = float(np.sum((alpha - alpha_star) * K[i, :])) + self.bias

                # Residual
                residual = f_i - y[i]

                # Update alpha_i (for residual > epsilon)
                if residual > eps:
                    delta = min(alpha_star[i], (residual - eps) / (K[i, i] + EPSILON))
                    alpha_star[i] -= delta
                    alpha[i] -= delta  # Not standard but helps convergence
                    max_change = max(max_change, abs(delta))
                elif residual < -eps:
                    # Update alpha_star (for residual < -epsilon)
                    delta = min(C_eff - alpha[i], (-residual - eps) / (K[i, i] + EPSILON))
                    delta = max(delta, 0.0)
                    alpha[i] += delta
                    max_change = max(max_change, abs(delta))

                # Clip to [0, C]
                alpha[i] = np.clip(alpha[i], 0.0, C_eff)
                alpha_star[i] = np.clip(alpha_star[i], 0.0, C_eff)

            # Update bias using support vectors
            sv_mask = ((alpha > tol) & (alpha < C_eff - tol)) | \
                      ((alpha_star > tol) & (alpha_star < C_eff - tol))

            if sv_mask.any():
                sv_idx = np.where(sv_mask)[0]
                bias_values = []
                for idx in sv_idx[:min(10, len(sv_idx))]:
                    f_idx = float(np.sum((alpha - alpha_star) * K[idx, :]))
                    if alpha[idx] > tol and alpha[idx] < C_eff - tol:
                        bias_values.append(y[idx] - eps - f_idx)
                    elif alpha_star[idx] > tol and alpha_star[idx] < C_eff - tol:
                        bias_values.append(y[idx] + eps - f_idx)
                if bias_values:
                    self.bias = float(np.mean(bias_values))

            if max_change < tol:
                log.debug("SVR converged at iteration %d", iteration + 1)
                break

        self.alpha_diff = alpha - alpha_star

        # Identify support vectors
        sv_threshold = tol
        self.support_indices = np.where(
            (np.abs(self.alpha_diff) > sv_threshold)
        )[0]

        self._fitted = True
        log.info("SVR fitted: n=%d, n_support=%d, C=%.2f, eps=%.4f",
                 n, len(self.support_indices), C_eff, eps)

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict target values for new inputs.

        Args:
            X: test inputs (n_test, d)

        Returns:
            Predictions (n_test,)
        """
        if not self._fitted:
            log.warning("SVR.predict called before fit — returning zeros")
            return np.zeros(X.shape[0])

        # K(X_test, X_train)
        K_test = self.kernel(X, self.X_train)

        # Prediction: f(x) = sum_i (alpha_i - alpha_i*) * K(x, x_i) + bias
        predictions = K_test @ self.alpha_diff + self.bias

        return predictions

    def get_stats(self) -> Dict[str, Any]:
        """Return model statistics."""
        return {
            "fitted": self._fitted,
            "n_train": len(self.X_train) if self.X_train is not None else 0,
            "n_support_vectors": len(self.support_indices) if self.support_indices is not None else 0,
            "C": self.C,
            "epsilon": self.epsilon,
            "bias": round(self.bias, 6),
            "kernel": repr(self.kernel),
        }
