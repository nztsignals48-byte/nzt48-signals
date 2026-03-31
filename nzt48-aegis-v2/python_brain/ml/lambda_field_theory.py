"""Lambda Vol Regime Field Theory — Book 169.

PDE-based continuous regime detection using reaction-diffusion equations.
Markets are modelled as continuous density fields where regime state evolves
through space (instruments) and time via a reaction-diffusion PDE:

    d_rho/dt = D * Laplacian(rho) + R(rho; params)

Where:
  - rho(x, t) is the regime density field across instruments
  - D is the diffusion coefficient (vol contagion speed)
  - R is the nonlinear reaction term (bistable: low-vol vs high-vol attractors)

This detects regime transitions BEFORE they complete by measuring
density flow between the two stable states.

Components:
  - VolatilityField:         Continuous vol field with PDE solver
  - RegimeFieldDetector:     Classify field state into regimes
  - FieldTheorySignalGenerator: Top-level signal generation

Bridge.py integration:
    try:
        from python_brain.ml.lambda_field_theory import (
            VolatilityField, RegimeFieldDetector,
            FieldTheorySignalGenerator,
        )
        _ft_gen = FieldTheorySignalGenerator(n_instruments=51)
    except ImportError:
        _ft_gen = None

Cross-references:
  - Book 124 (Volatility Regime Clustering)
  - Book 116 (Stochastic Calculus)
  - Book 113 (Hidden Markov Models)
  - Book 163 (Diffusion Models)
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("lambda_field_theory")

__all__ = [
    "VolatilityField",
    "RegimeFieldDetector",
    "FieldTheorySignalGenerator",
]

# ---------------------------------------------------------------------------
# Paths (production)
# ---------------------------------------------------------------------------
_DATA_DIR = "/app/data"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_GRID_SIZE = 100
_DEFAULT_DIFFUSION_COEFF = 0.05
_DEFAULT_DX = 1.0
_MAX_FIELD_HISTORY = 200
_CFL_SAFETY = 0.4  # Courant-Friedrichs-Lewy safety factor

# Bistable reaction parameters
_BISTABLE_A = 0.0    # low-vol attractor
_BISTABLE_B = 1.0    # high-vol attractor
_BISTABLE_MID = 0.5  # unstable equilibrium
_REACTION_RATE = 1.0


# ---------------------------------------------------------------------------
# VolatilityField
# ---------------------------------------------------------------------------
class VolatilityField:
    """Continuous volatility field across instruments, evolved via PDE.

    The field rho(x, t) represents the normalised volatility state at
    each point in instrument-space.  Values near 0 = low vol, near 1 =
    high vol.

    The PDE is solved with finite differences (explicit Euler):
      rho(t+dt) = rho(t) + dt * [D * Laplacian(rho) + R(rho)]

    Where R(rho) is a bistable reaction: R = rate * rho * (1 - rho) * (rho - mid)
    This creates two stable fixed points (low-vol and high-vol) with an
    unstable transition at rho = mid.
    """

    def __init__(
        self,
        n_instruments: int,
        grid_size: int = _DEFAULT_GRID_SIZE,
        diffusion_coeff: float = _DEFAULT_DIFFUSION_COEFF,
        dx: float = _DEFAULT_DX,
    ) -> None:
        self.n_instruments = n_instruments
        self.grid_size = grid_size
        self.D = diffusion_coeff
        self.dx = dx

        # Field: (n_instruments, grid_size) — each instrument has a 1-D spatial grid
        self.field = np.full((n_instruments, grid_size), 0.1, dtype=float)
        self._field_history: Deque[np.ndarray] = deque(maxlen=_MAX_FIELD_HISTORY)

        # CFL stability constraint: dt <= CFL_SAFETY * dx^2 / (2 * D)
        self._max_dt = _CFL_SAFETY * (dx ** 2) / (2.0 * max(self.D, 1e-12))

        log.info(
            "VolatilityField init | n=%d grid=%d D=%.4f max_dt=%.3f",
            n_instruments, grid_size, diffusion_coeff, self._max_dt,
        )

    # ---- public -----------------------------------------------------------

    def update(self, returns: np.ndarray, dt: float = 1.0) -> None:
        """Evolve the volatility field forward by one time step.

        Args:
            returns: (n_instruments,) array of latest returns used
                     to inject volatility into the field.
            dt:      nominal time step (will be sub-stepped for CFL).
        """
        if len(returns) != self.n_instruments:
            log.warning(
                "update: returns length %d != n_instruments %d",
                len(returns), self.n_instruments,
            )
            return

        # Inject observed volatility as a source term
        abs_ret = np.abs(returns)
        # Normalise to [0, 1] range
        max_ret = float(np.max(abs_ret)) if float(np.max(abs_ret)) > 0 else 1.0
        injection = abs_ret / max_ret

        # Sub-step if dt exceeds CFL limit
        n_substeps = max(1, int(math.ceil(dt / self._max_dt)))
        sub_dt = dt / n_substeps

        for _ in range(n_substeps):
            # Diffusion step (spatial coupling across instruments)
            diffused = self._diffusion_step(self.field, self.D, sub_dt, self.dx)

            # Reaction step (bistable nonlinearity)
            reacted = self._reaction_step(
                diffused,
                {
                    "rate": _REACTION_RATE,
                    "a": _BISTABLE_A,
                    "b": _BISTABLE_B,
                    "mid": _BISTABLE_MID,
                },
            )

            # Source injection — nudge field toward observed vol
            for i in range(self.n_instruments):
                centre = self.grid_size // 2
                width = max(1, self.grid_size // 10)
                lo = max(0, centre - width)
                hi = min(self.grid_size, centre + width)
                reacted[i, lo:hi] += 0.1 * sub_dt * injection[i]

            self.field = np.clip(reacted, 0.0, 1.0)

        self._field_history.append(self.field.copy())
        log.debug("update | mean_field=%.4f max_field=%.4f", float(np.mean(self.field)), float(np.max(self.field)))

    def get_instrument_state(self, idx: int) -> np.ndarray:
        """Return the 1-D field for a single instrument."""
        if 0 <= idx < self.n_instruments:
            return self.field[idx].copy()
        return np.zeros(self.grid_size)

    def get_mean_field(self) -> np.ndarray:
        """Return the mean field across all instruments (1-D grid)."""
        return np.mean(self.field, axis=0)

    @property
    def field_history(self) -> List[np.ndarray]:
        """Read-only access to field history."""
        return list(self._field_history)

    # ---- private ----------------------------------------------------------

    @staticmethod
    def _diffusion_step(
        field: np.ndarray, D: float, dt: float, dx: float,
    ) -> np.ndarray:
        """Apply diffusion via discrete Laplacian (finite differences).

        Uses second-order central differences with Neumann (zero-flux)
        boundary conditions.

        Args:
            field: (n_instruments, grid_size)
            D:     diffusion coefficient
            dt:    time step
            dx:    spatial step

        Returns:
            Updated field after diffusion.
        """
        laplacian = np.zeros_like(field)
        # Interior points: L = (f[i-1] - 2*f[i] + f[i+1]) / dx^2
        laplacian[:, 1:-1] = (
            field[:, :-2] - 2.0 * field[:, 1:-1] + field[:, 2:]
        ) / (dx ** 2)
        # Neumann BC: zero flux at boundaries
        laplacian[:, 0] = (field[:, 1] - field[:, 0]) / (dx ** 2)
        laplacian[:, -1] = (field[:, -2] - field[:, -1]) / (dx ** 2)

        # Also diffuse across instruments (cross-instrument contagion)
        if field.shape[0] > 2:
            cross_lap = np.zeros_like(field)
            cross_lap[1:-1, :] = (
                field[:-2, :] - 2.0 * field[1:-1, :] + field[2:, :]
            ) / (dx ** 2)
            cross_lap[0, :] = (field[1, :] - field[0, :]) / (dx ** 2)
            cross_lap[-1, :] = (field[-2, :] - field[-1, :]) / (dx ** 2)
            laplacian += 0.5 * cross_lap  # cross-instrument coupling is weaker

        return field + D * dt * laplacian

    @staticmethod
    def _reaction_step(
        field: np.ndarray, params: Dict[str, float],
    ) -> np.ndarray:
        """Apply bistable reaction term.

        R(rho) = rate * rho * (1 - rho) * (rho - mid)

        This creates two stable attractors at rho=a (low vol) and rho=b
        (high vol) with an unstable saddle at rho=mid.

        Args:
            field:  current field values
            params: dict with 'rate', 'a', 'b', 'mid'

        Returns:
            Updated field after reaction.
        """
        rate = params.get("rate", 1.0)
        mid = params.get("mid", 0.5)
        # Bistable: rho * (1 - rho) * (rho - mid)
        reaction = rate * field * (1.0 - field) * (field - mid)
        return field + reaction


# ---------------------------------------------------------------------------
# RegimeFieldDetector
# ---------------------------------------------------------------------------
class RegimeFieldDetector:
    """Classify the volatility field into regimes.

    Regimes:
      - DISPERSION:    Normal markets — field mostly near low-vol attractor
      - VOL_CONTAGION: Crisis spreading — high-vol region growing
      - FLAT:          Suppressed vol — entire field compressed near zero
      - TRANSITION:    Between regimes — bimodal field distribution
    """

    # Thresholds for regime classification
    _HIGH_VOL_FRAC_CONTAGION = 0.4
    _LOW_VOL_FRAC_FLAT = 0.85
    _FLAT_MAX = 0.15
    _TRANSITION_BIMODALITY = 0.3

    def classify(self, field: np.ndarray) -> str:
        """Classify the current field state.

        Args:
            field: (n_instruments, grid_size) or (grid_size,) volatility field.

        Returns:
            One of: DISPERSION, VOL_CONTAGION, FLAT, TRANSITION.
        """
        if field.ndim == 1:
            flat = field.ravel()
        else:
            flat = field.ravel()

        mean_val = float(np.mean(flat))
        high_frac = float(np.mean(flat > 0.6))
        low_frac = float(np.mean(flat < self._FLAT_MAX))

        # Check for flat / suppressed vol
        if low_frac > self._LOW_VOL_FRAC_FLAT and mean_val < self._FLAT_MAX:
            regime = "FLAT"
        # Check for contagion
        elif high_frac > self._HIGH_VOL_FRAC_CONTAGION:
            regime = "VOL_CONTAGION"
        # Check for transition (bimodal distribution)
        elif self._is_bimodal(flat):
            regime = "TRANSITION"
        else:
            regime = "DISPERSION"

        log.debug(
            "classify | mean=%.3f high_frac=%.3f low_frac=%.3f => %s",
            mean_val, high_frac, low_frac, regime,
        )
        return regime

    def contagion_speed(self, field_history: List[np.ndarray]) -> float:
        """Estimate the speed at which vol contagion is spreading.

        Measured as the rate of growth of the high-vol region across
        consecutive field snapshots.

        Args:
            field_history: list of field arrays over time.

        Returns:
            Speed estimate (fraction of instruments per time step).
            Positive = spreading, negative = receding.
        """
        if len(field_history) < 2:
            return 0.0

        # Measure high-vol fraction over time
        high_fracs = []
        for f in field_history[-min(20, len(field_history)):]:
            flat = f.ravel()
            high_fracs.append(float(np.mean(flat > 0.6)))

        if len(high_fracs) < 2:
            return 0.0

        # Linear regression slope (manual)
        n = len(high_fracs)
        x = np.arange(n, dtype=float)
        y = np.array(high_fracs)
        x_mean = float(np.mean(x))
        y_mean = float(np.mean(y))
        num = float(np.sum((x - x_mean) * (y - y_mean)))
        den = float(np.sum((x - x_mean) ** 2))
        if abs(den) < 1e-12:
            return 0.0

        speed = num / den
        log.debug("contagion_speed | speed=%.5f", speed)
        return speed

    def epicenter(self, field: np.ndarray) -> int:
        """Identify which instrument is the volatility epicenter.

        The epicenter is the instrument with the highest mean field value
        (i.e. the source of vol contagion).

        Args:
            field: (n_instruments, grid_size) volatility field.

        Returns:
            Index of the epicenter instrument.
        """
        if field.ndim == 1:
            return 0
        means = np.mean(field, axis=1)
        idx = int(np.argmax(means))
        log.debug("epicenter | instrument=%d mean_vol=%.4f", idx, float(means[idx]))
        return idx

    # ---- private ----------------------------------------------------------

    @staticmethod
    def _is_bimodal(values: np.ndarray) -> bool:
        """Simple bimodality test using histogram peaks.

        A bimodal distribution has two distinct peaks separated by a
        valley.  We use Hartigan's dip-style heuristic.
        """
        n_bins = 20
        hist, _ = np.histogram(values, bins=n_bins, range=(0.0, 1.0))
        if len(hist) < 4:
            return False

        # Smooth histogram
        kernel = np.array([0.25, 0.5, 0.25])
        smoothed = np.convolve(hist.astype(float), kernel, mode="same")

        # Count local maxima
        peaks = 0
        for i in range(1, len(smoothed) - 1):
            if smoothed[i] > smoothed[i - 1] and smoothed[i] > smoothed[i + 1]:
                if smoothed[i] > 0.05 * float(np.max(smoothed)):
                    peaks += 1

        return peaks >= 2


# ---------------------------------------------------------------------------
# FieldTheorySignalGenerator
# ---------------------------------------------------------------------------
class FieldTheorySignalGenerator:
    """Top-level signal generator combining field theory components.

    Output dict schema:
      - regime:           current regime classification
      - contagion_speed:  rate of vol spread
      - epicenter:        instrument index of vol source
      - field_mean:       mean field level (0-1)
      - field_dispersion: field std dev
      - signal:           directional signal [-1, 1]
      - confidence:       signal confidence [0, 100]
      - sizing_factor:    Kelly multiplier [0, 1]
    """

    def __init__(
        self,
        n_instruments: int = 51,
        grid_size: int = _DEFAULT_GRID_SIZE,
        diffusion_coeff: float = _DEFAULT_DIFFUSION_COEFF,
    ) -> None:
        self.vol_field = VolatilityField(
            n_instruments=n_instruments,
            grid_size=grid_size,
            diffusion_coeff=diffusion_coeff,
        )
        self.detector = RegimeFieldDetector()
        log.info("FieldTheorySignalGenerator init | n=%d", n_instruments)

    def generate(
        self,
        returns_matrix: np.ndarray,
        current_field: Optional[np.ndarray] = None,
    ) -> Dict[str, Any]:
        """Generate regime + contagion signals from the field.

        Args:
            returns_matrix: (n_instruments,) latest returns per instrument.
            current_field:  optional override for the field state.

        Returns:
            Signal dictionary with regime, contagion, and sizing info.
        """
        # Update the field with new returns
        self.vol_field.update(returns_matrix)

        field = current_field if current_field is not None else self.vol_field.field

        # Classify regime
        regime = self.detector.classify(field)

        # Contagion dynamics
        speed = self.detector.contagion_speed(self.vol_field.field_history)
        epi = self.detector.epicenter(field)

        # Field statistics
        field_mean = float(np.mean(field))
        field_std = float(np.std(field))

        # Signal generation based on regime
        signal, confidence, sizing = self._regime_to_signal(
            regime, speed, field_mean, field_std,
        )

        result = {
            "regime": regime,
            "contagion_speed": speed,
            "epicenter": epi,
            "field_mean": field_mean,
            "field_dispersion": field_std,
            "signal": signal,
            "confidence": confidence,
            "sizing_factor": sizing,
        }

        log.info(
            "generate | regime=%s speed=%.4f signal=%.3f conf=%.0f",
            regime, speed, signal, confidence,
        )
        return result

    # ---- private ----------------------------------------------------------

    @staticmethod
    def _regime_to_signal(
        regime: str,
        contagion_speed: float,
        field_mean: float,
        field_std: float,
    ) -> Tuple[float, float, float]:
        """Map regime state to a trading signal.

        Returns:
            (signal, confidence, sizing_factor)
        """
        if regime == "FLAT":
            # Low-vol: mild bullish bias (trend continuation)
            signal = 0.3
            confidence = 60.0
            sizing = 0.8
        elif regime == "DISPERSION":
            # Normal: neutral with slight conviction
            signal = 0.1
            confidence = 55.0
            sizing = 0.7
        elif regime == "VOL_CONTAGION":
            # Crisis: bearish signal, reduced sizing
            severity = min(1.0, abs(contagion_speed) * 10.0)
            signal = -0.5 - 0.5 * severity
            confidence = 65.0 + 15.0 * severity
            sizing = max(0.2, 0.6 - 0.4 * severity)
        elif regime == "TRANSITION":
            # Transition: uncertainty — reduce all sizing
            signal = 0.0
            confidence = 40.0
            sizing = 0.4
        else:
            signal = 0.0
            confidence = 30.0
            sizing = 0.5

        # Adjust confidence based on field clarity
        if field_std < 0.1:
            # Clear regime — more confident
            confidence = min(85.0, confidence + 10.0)
        elif field_std > 0.3:
            # Noisy field — less confident
            confidence = max(30.0, confidence - 10.0)

        return (
            float(np.clip(signal, -1.0, 1.0)),
            float(np.clip(confidence, 0.0, 100.0)),
            float(np.clip(sizing, 0.0, 1.0)),
        )
