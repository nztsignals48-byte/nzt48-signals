"""Graph Signal Processing & Spectral Alpha Decomposition — Book 174.

Implements graph signal processing for market data: the Graph Laplacian
captures inter-asset relationships, the Graph Fourier Transform (GFT)
decomposes market signals into frequency bands, and spectral analysis
separates market-wide movements from idiosyncratic alpha.

Key idea (from joshuaaalampour's 4-layer architecture):
  Graph Laplacian -> ETF Decomposition -> TCN -> Dynamic Allocator

Frequency interpretation:
  - Low-frequency:  Market-wide / systematic factor movements
  - Mid-frequency:  Sector / cluster rotations
  - High-frequency: Idiosyncratic / alpha opportunities

Components:
  - GraphLaplacian:            L = D - A (unnormalized) and normalised variant
  - GraphFourierTransform:     GFT (forward/inverse) via eigenvectors of L
  - SpectralAlphaDecomposer:   Decompose returns into frequency bands
  - GraphSignalFeatures:       Extract features for downstream ML

Bridge.py integration:
    try:
        from python_brain.features.graph_signal_processor import (
            GraphLaplacian, GraphFourierTransform,
            SpectralAlphaDecomposer, GraphSignalFeatures,
        )
        _gsp = GraphSignalFeatures()
    except ImportError:
        _gsp = None

Cross-references:
  - Book 29 (TCN Deep Learning): Innovation signals feed TCN
  - Books 82-85 (Regime Detection): Topology changes trigger reassessment
  - Book 169 (Lambda Vol Regime Field Theory): Spatial structure
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

log = logging.getLogger("graph_signal_processor")

__all__ = [
    "GraphLaplacian",
    "GraphFourierTransform",
    "SpectralAlphaDecomposer",
    "GraphSignalFeatures",
]

# ---------------------------------------------------------------------------
# Paths (production)
# ---------------------------------------------------------------------------
_DATA_DIR = "/app/data"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_LOW_FREQ_CUTOFF = 0.2    # fraction of spectrum that is "low frequency"
_MID_FREQ_CUTOFF = 0.6    # fraction where mid-frequency ends
_MIN_EIGENVALUE_GAP = 1e-10


# ---------------------------------------------------------------------------
# GraphLaplacian
# ---------------------------------------------------------------------------
class GraphLaplacian:
    """Compute the Graph Laplacian and its eigendecomposition.

    The unnormalised Laplacian L = D - A captures the structure of
    inter-asset relationships.  The eigenvalues encode graph
    connectivity and the eigenvectors form the Fourier basis.

    For FTSE 100 constituents:
      - A_ij = max(0, correlation(asset_i, asset_j))
      - D_ii = sum_j A_ij  (degree = total connection strength)
      - L = D - A
    """

    @staticmethod
    def compute(adjacency: np.ndarray) -> np.ndarray:
        """Compute unnormalised Graph Laplacian L = D - A.

        Args:
            adjacency: (N, N) symmetric non-negative adjacency matrix.
                       Self-loops (diagonal) are ignored (set to 0).

        Returns:
            (N, N) Graph Laplacian matrix.
        """
        A = adjacency.copy().astype(float)
        np.fill_diagonal(A, 0.0)  # no self-loops
        A = np.maximum(A, 0.0)    # non-negative

        # Degree matrix
        degrees = np.sum(A, axis=1)
        D = np.diag(degrees)

        L = D - A
        log.debug(
            "compute | N=%d mean_degree=%.2f",
            A.shape[0], float(np.mean(degrees)),
        )
        return L

    @staticmethod
    def normalized(adjacency: np.ndarray) -> np.ndarray:
        """Compute normalised Graph Laplacian L_norm = I - D^(-1/2) A D^(-1/2).

        The normalised Laplacian has eigenvalues in [0, 2] regardless
        of graph scale, making it more suitable for comparing graphs
        of different sizes.

        Args:
            adjacency: (N, N) symmetric non-negative adjacency matrix.

        Returns:
            (N, N) normalised Laplacian.
        """
        A = adjacency.copy().astype(float)
        np.fill_diagonal(A, 0.0)
        A = np.maximum(A, 0.0)
        N = A.shape[0]

        degrees = np.sum(A, axis=1)

        # D^(-1/2)
        d_inv_sqrt = np.zeros(N)
        for i in range(N):
            if degrees[i] > _MIN_EIGENVALUE_GAP:
                d_inv_sqrt[i] = 1.0 / math.sqrt(degrees[i])

        D_inv_sqrt = np.diag(d_inv_sqrt)

        # L_norm = I - D^(-1/2) A D^(-1/2)
        L_norm = np.eye(N) - D_inv_sqrt @ A @ D_inv_sqrt

        log.debug(
            "normalized | N=%d isolated_nodes=%d",
            N, int(np.sum(degrees < _MIN_EIGENVALUE_GAP)),
        )
        return L_norm

    @staticmethod
    def eigendecompose(
        L: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Eigendecompose the Laplacian (ascending eigenvalue order).

        Args:
            L: (N, N) Laplacian matrix (symmetric).

        Returns:
            (eigenvalues, eigenvectors) where eigenvalues are sorted
            ascending and eigenvectors[:, k] is the k-th eigenvector.
        """
        # Force symmetry (numerical stability)
        L_sym = 0.5 * (L + L.T)

        eigenvalues, eigenvectors = np.linalg.eigh(L_sym)

        # Sort ascending (eigh usually returns ascending but be safe)
        order = np.argsort(eigenvalues)
        eigenvalues = eigenvalues[order]
        eigenvectors = eigenvectors[:, order]

        # Clamp small negatives from numerical error
        eigenvalues = np.maximum(eigenvalues, 0.0)

        log.debug(
            "eigendecompose | N=%d lambda_min=%.6f lambda_max=%.6f algebraic_connectivity=%.6f",
            L.shape[0],
            float(eigenvalues[0]),
            float(eigenvalues[-1]),
            float(eigenvalues[1]) if len(eigenvalues) > 1 else 0.0,
        )
        return (eigenvalues, eigenvectors)


# ---------------------------------------------------------------------------
# GraphFourierTransform
# ---------------------------------------------------------------------------
class GraphFourierTransform:
    """Graph Fourier Transform using Laplacian eigenvectors.

    The GFT is the projection of a graph signal onto the eigenvectors
    of the Graph Laplacian:

        s_hat = U^T @ s    (forward GFT)
        s     = U @ s_hat  (inverse GFT)

    Where U is the matrix of eigenvectors (columns) of L.

    Low eigenvalues correspond to smooth (low-frequency) components.
    High eigenvalues correspond to rapidly varying (high-frequency)
    components.
    """

    def __init__(self, laplacian_eigenvectors: np.ndarray) -> None:
        """Initialise with pre-computed eigenvectors.

        Args:
            laplacian_eigenvectors: (N, N) matrix where column k is
                the k-th eigenvector of the Laplacian.
        """
        self.U = laplacian_eigenvectors.copy()
        self.U_T = self.U.T.copy()
        self._n = self.U.shape[0]
        log.info("GraphFourierTransform init | N=%d", self._n)

    def forward(self, signal: np.ndarray) -> np.ndarray:
        """Compute the Graph Fourier Transform (analysis).

        s_hat = U^T @ s

        Args:
            signal: (N,) graph signal (e.g., returns across N assets).

        Returns:
            (N,) spectral coefficients.
        """
        if len(signal) != self._n:
            log.warning(
                "forward: signal length %d != graph size %d",
                len(signal), self._n,
            )
            return np.zeros(self._n)

        spectrum = self.U_T @ signal
        return spectrum

    def inverse(self, spectrum: np.ndarray) -> np.ndarray:
        """Compute the inverse Graph Fourier Transform (synthesis).

        s = U @ s_hat

        Args:
            spectrum: (N,) spectral coefficients.

        Returns:
            (N,) reconstructed graph signal.
        """
        if len(spectrum) != self._n:
            log.warning(
                "inverse: spectrum length %d != graph size %d",
                len(spectrum), self._n,
            )
            return np.zeros(self._n)

        signal = self.U @ spectrum
        return signal

    def bandpass(
        self,
        signal: np.ndarray,
        low_idx: int,
        high_idx: int,
    ) -> np.ndarray:
        """Apply a spectral bandpass filter.

        Keeps only spectral components between low_idx and high_idx
        (inclusive), zeroing all others.

        Args:
            signal:   (N,) graph signal
            low_idx:  lower spectral index (0 = DC / constant)
            high_idx: upper spectral index (N-1 = highest frequency)

        Returns:
            (N,) bandpass-filtered signal.
        """
        spectrum = self.forward(signal)
        filtered = np.zeros_like(spectrum)
        lo = max(0, low_idx)
        hi = min(len(spectrum), high_idx + 1)
        filtered[lo:hi] = spectrum[lo:hi]
        return self.inverse(filtered)


# ---------------------------------------------------------------------------
# SpectralAlphaDecomposer
# ---------------------------------------------------------------------------
class SpectralAlphaDecomposer:
    """Decompose returns into spectral frequency bands.

    Three bands:
      - Low-frequency:  Market-wide factor (systematic risk)
      - Mid-frequency:  Sector / cluster rotations
      - High-frequency: Idiosyncratic alpha
    """

    def decompose(
        self,
        returns: np.ndarray,
        adjacency: np.ndarray,
    ) -> Dict[str, Any]:
        """Decompose returns into spectral bands.

        Args:
            returns:   (N,) cross-sectional returns for N assets.
            adjacency: (N, N) adjacency matrix.

        Returns:
            Dict with keys:
              low_freq:       (N,) market component
              mid_freq:       (N,) sector component
              high_freq:      (N,) idiosyncratic component
              spectrum:       (N,) full spectrum
              eigenvalues:    (N,) Laplacian eigenvalues
              reconstruction_error: float
        """
        N = len(returns)
        if N < 3 or adjacency.shape != (N, N):
            log.warning("decompose: invalid inputs (N=%d)", N)
            return {
                "low_freq": returns.copy(),
                "mid_freq": np.zeros_like(returns),
                "high_freq": np.zeros_like(returns),
                "spectrum": np.zeros_like(returns),
                "eigenvalues": np.zeros_like(returns),
                "reconstruction_error": 0.0,
            }

        # Build Laplacian and GFT
        L = GraphLaplacian.normalized(adjacency)
        eigenvalues, eigenvectors = GraphLaplacian.eigendecompose(L)
        gft = GraphFourierTransform(eigenvectors)

        # Compute spectrum
        spectrum = gft.forward(returns)

        # Band boundaries
        low_cutoff = max(1, int(N * _LOW_FREQ_CUTOFF))
        mid_cutoff = max(low_cutoff + 1, int(N * _MID_FREQ_CUTOFF))

        # Decompose
        low_freq = gft.bandpass(returns, 0, low_cutoff - 1)
        mid_freq = gft.bandpass(returns, low_cutoff, mid_cutoff - 1)
        high_freq = gft.bandpass(returns, mid_cutoff, N - 1)

        # Reconstruction error check
        reconstructed = low_freq + mid_freq + high_freq
        error = float(np.max(np.abs(reconstructed - returns)))

        log.debug(
            "decompose | N=%d low=%d mid=%d high=%d recon_err=%.6f",
            N, low_cutoff, mid_cutoff - low_cutoff, N - mid_cutoff, error,
        )

        return {
            "low_freq": low_freq,
            "mid_freq": mid_freq,
            "high_freq": high_freq,
            "spectrum": spectrum,
            "eigenvalues": eigenvalues,
            "reconstruction_error": error,
        }

    def innovation_signal(
        self,
        current_spectrum: np.ndarray,
        baseline_spectrum: np.ndarray,
    ) -> np.ndarray:
        """Compute spectral innovation (deviation from baseline).

        Innovation = current_spectrum - baseline_spectrum

        Large innovations in high-frequency bands suggest new
        idiosyncratic alpha; large innovations in low-frequency
        suggest regime shift.

        Args:
            current_spectrum:  (N,) current GFT coefficients.
            baseline_spectrum: (N,) historical average GFT coefficients.

        Returns:
            (N,) innovation signal in spectral domain.
        """
        if len(current_spectrum) != len(baseline_spectrum):
            log.warning(
                "innovation_signal: length mismatch (%d vs %d)",
                len(current_spectrum), len(baseline_spectrum),
            )
            return current_spectrum.copy()

        innovation = current_spectrum - baseline_spectrum

        log.debug(
            "innovation_signal | max_innov=%.4f mean_abs=%.4f",
            float(np.max(np.abs(innovation))),
            float(np.mean(np.abs(innovation))),
        )
        return innovation


# ---------------------------------------------------------------------------
# GraphSignalFeatures
# ---------------------------------------------------------------------------
class GraphSignalFeatures:
    """Extract graph-signal features for downstream ML models.

    Features include:
      - Graph smoothness (how well the signal follows the graph)
      - Spectral energy in each band
      - Band power ratios
      - Algebraic connectivity (Fiedler value)
      - Spectral entropy
    """

    def __init__(self) -> None:
        self._decomposer = SpectralAlphaDecomposer()
        log.info("GraphSignalFeatures init")

    def extract(
        self,
        returns_matrix: np.ndarray,
        adjacency: np.ndarray,
    ) -> Dict[str, Any]:
        """Extract graph signal features from cross-sectional returns.

        Args:
            returns_matrix: (N,) cross-sectional returns or
                            (T, N) where T is the time dimension.
            adjacency:      (N, N) adjacency matrix.

        Returns:
            Feature dictionary.
        """
        # If 2-D, use the latest cross-section
        if returns_matrix.ndim == 2:
            returns = returns_matrix[-1, :]
        else:
            returns = returns_matrix

        N = len(returns)
        if N < 3 or adjacency.shape != (N, N):
            log.warning("extract: invalid inputs (N=%d)", N)
            return self._empty_features()

        # Decomposition
        decomp = self._decomposer.decompose(returns, adjacency)

        # Graph smoothness: s^T L s / ||s||^2
        # Low value = signal is smooth on the graph (follows structure)
        L = GraphLaplacian.normalized(adjacency)
        norm_sq = float(np.dot(returns, returns))
        if norm_sq > 1e-12:
            smoothness = float(returns @ L @ returns) / norm_sq
        else:
            smoothness = 0.0

        # Band energies
        low_energy = float(np.sum(decomp["low_freq"] ** 2))
        mid_energy = float(np.sum(decomp["mid_freq"] ** 2))
        high_energy = float(np.sum(decomp["high_freq"] ** 2))
        total_energy = low_energy + mid_energy + high_energy + 1e-12

        # Band power ratios
        low_power_ratio = low_energy / total_energy
        mid_power_ratio = mid_energy / total_energy
        high_power_ratio = high_energy / total_energy

        # Algebraic connectivity (second smallest eigenvalue)
        eigenvalues = decomp["eigenvalues"]
        algebraic_connectivity = float(eigenvalues[1]) if len(eigenvalues) > 1 else 0.0

        # Spectral entropy
        spectrum_sq = decomp["spectrum"] ** 2
        spec_total = float(np.sum(spectrum_sq))
        if spec_total > 1e-12:
            probs = spectrum_sq / spec_total
            probs = probs[probs > 1e-12]
            spectral_entropy = float(-np.sum(probs * np.log(probs)))
            # Normalise by max entropy (uniform)
            max_entropy = math.log(N) if N > 1 else 1.0
            spectral_entropy /= max_entropy
        else:
            spectral_entropy = 0.0

        # Spectral gap (lambda_2 / lambda_max) — measures expander quality
        if len(eigenvalues) > 1 and eigenvalues[-1] > _MIN_EIGENVALUE_GAP:
            spectral_gap = float(eigenvalues[1] / eigenvalues[-1])
        else:
            spectral_gap = 0.0

        features = {
            "graph_smoothness": smoothness,
            "low_freq_energy": low_energy,
            "mid_freq_energy": mid_energy,
            "high_freq_energy": high_energy,
            "low_power_ratio": low_power_ratio,
            "mid_power_ratio": mid_power_ratio,
            "high_power_ratio": high_power_ratio,
            "algebraic_connectivity": algebraic_connectivity,
            "spectral_entropy": spectral_entropy,
            "spectral_gap": spectral_gap,
            "reconstruction_error": decomp["reconstruction_error"],
            "n_assets": N,
        }

        log.debug(
            "extract | smoothness=%.4f low=%.3f mid=%.3f high=%.3f conn=%.4f",
            smoothness, low_power_ratio, mid_power_ratio,
            high_power_ratio, algebraic_connectivity,
        )
        return features

    @staticmethod
    def _empty_features() -> Dict[str, Any]:
        """Return zeroed-out feature dictionary."""
        return {
            "graph_smoothness": 0.0,
            "low_freq_energy": 0.0,
            "mid_freq_energy": 0.0,
            "high_freq_energy": 0.0,
            "low_power_ratio": 0.0,
            "mid_power_ratio": 0.0,
            "high_power_ratio": 0.0,
            "algebraic_connectivity": 0.0,
            "spectral_entropy": 0.0,
            "spectral_gap": 0.0,
            "reconstruction_error": 0.0,
            "n_assets": 0,
        }
