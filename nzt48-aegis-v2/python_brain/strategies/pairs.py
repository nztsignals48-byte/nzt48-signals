"""Cointegration & Pairs Trading — Books 125, 126.

Trades mean reversion of the spread between cointegrated ETP pairs.
The 17 long/inverse ETP pairs on LSE are natural candidates because
they track the same underlying with opposite leverage.

Entry: Spread z-score crosses ±2.0σ → enter convergence trade
Exit:  Spread reverts within 0.5σ of mean, or time-stop at 5× half-life

Key formulas:
  Spread: Z(t) = Long(t) - β × Inverse(t)
  Z-score: z = (spread - μ) / σ
  Half-life: τ = -log(2) / log(φ) where φ is AR(1) coefficient

Requirements: numpy, scipy (for ADF test)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("pairs_trading")


# ---------------------------------------------------------------------------
# ETP Pair Definitions (17 long/inverse pairs from Book 3)
# ---------------------------------------------------------------------------
ETP_PAIRS: Dict[str, Tuple[str, str]] = {
    "SP500": ("3USL.L", "3USS.L"),
    "NASDAQ": ("QQQ3.L", "QQQS.L"),
    "FTSE": ("3UKL.L", "3UKS.L"),
    "NVIDIA": ("NVD3.L", "NV3S.L"),
    "TESLA": ("TSL3.L", "TS3S.L"),
    "OIL": ("3LOI.L", "3SOI.L"),
    "GOLD": ("3LGD.L", "3SGD.L"),
    "SILVER": ("3LSV.L", "3SSV.L"),
}


@dataclass
class PairState:
    """Current state of a cointegrated pair."""
    pair_name: str
    long_ticker: str
    inverse_ticker: str
    hedge_ratio: float = 1.0  # β from OLS regression
    spread_mean: float = 0.0
    spread_std: float = 0.0
    current_spread: float = 0.0
    z_score: float = 0.0
    half_life_bars: float = 20.0
    adf_pvalue: float = 1.0  # ADF test p-value (< 0.01 = cointegrated)
    is_cointegrated: bool = False

    @property
    def is_tradeable(self) -> bool:
        return self.is_cointegrated and self.half_life_bars > 2 and self.half_life_bars < 100


@dataclass
class PairsSignal:
    """Trading signal from pairs strategy."""
    pair_name: str
    direction: str  # "long_spread" (buy long, sell inverse) or "short_spread"
    long_ticker: str
    inverse_ticker: str
    z_score: float
    confidence: int
    hedge_ratio: float
    half_life_bars: float
    time_stop_bars: int  # 5 × half_life
    strategy: str = "Pairs"


class PairsTracker:
    """Track cointegration state and generate signals for ETP pairs."""

    def __init__(self, lookback: int = 120, z_entry: float = 2.0, z_exit: float = 0.5):
        self.lookback = lookback
        self.z_entry = z_entry
        self.z_exit = z_exit
        self._states: Dict[str, PairState] = {}

    def update_pair(
        self,
        pair_name: str,
        long_prices: np.ndarray,
        inverse_prices: np.ndarray,
    ) -> Optional[PairsSignal]:
        """Update pair state and check for entry/exit signals.

        Args:
            pair_name: Key in ETP_PAIRS
            long_prices: Price history for long ETP (newest last)
            inverse_prices: Price history for inverse ETP (newest last)

        Returns: PairsSignal if entry triggered, None otherwise
        """
        if pair_name not in ETP_PAIRS:
            return None

        long_t, inv_t = ETP_PAIRS[pair_name]
        n = min(len(long_prices), len(inverse_prices))
        if n < self.lookback:
            return None

        lp = long_prices[-self.lookback:]
        ip = inverse_prices[-self.lookback:]

        # Step 1: OLS hedge ratio (β)
        beta = self._ols_hedge_ratio(lp, ip)

        # Step 2: Compute spread
        spread = lp - beta * ip

        # Step 3: ADF test on spread
        adf_p = self._adf_pvalue(spread)

        # Step 4: Spread statistics
        spread_mean = float(np.mean(spread))
        spread_std = float(np.std(spread, ddof=1))
        if spread_std < 1e-10:
            return None

        current_spread = float(spread[-1])
        z = (current_spread - spread_mean) / spread_std

        # Step 5: Half-life
        half_life = self._compute_half_life(spread)

        # Update state
        state = PairState(
            pair_name=pair_name,
            long_ticker=long_t,
            inverse_ticker=inv_t,
            hedge_ratio=round(beta, 4),
            spread_mean=round(spread_mean, 4),
            spread_std=round(spread_std, 4),
            current_spread=round(current_spread, 4),
            z_score=round(z, 3),
            half_life_bars=round(half_life, 1),
            adf_pvalue=round(adf_p, 4),
            is_cointegrated=adf_p < 0.05,
        )
        self._states[pair_name] = state

        # Step 6: Generate signal if cointegrated and z-score extreme
        if not state.is_tradeable:
            return None

        if abs(z) >= self.z_entry:
            # Long spread when z < -2 (spread too low, expect reversion up)
            # Short spread when z > +2 (spread too high, expect reversion down)
            direction = "long_spread" if z < -self.z_entry else "short_spread"

            # Confidence based on z-score magnitude and cointegration strength
            base_conf = 55
            z_bonus = min(20, int((abs(z) - self.z_entry) * 10))
            adf_bonus = 10 if adf_p < 0.01 else 0
            hl_bonus = 5 if 5 < half_life < 30 else 0
            confidence = min(90, base_conf + z_bonus + adf_bonus + hl_bonus)

            return PairsSignal(
                pair_name=pair_name,
                direction=direction,
                long_ticker=long_t,
                inverse_ticker=inv_t,
                z_score=round(z, 3),
                confidence=confidence,
                hedge_ratio=round(beta, 4),
                half_life_bars=round(half_life, 1),
                time_stop_bars=int(half_life * 5),
            )

        return None

    def _ols_hedge_ratio(self, y: np.ndarray, x: np.ndarray) -> float:
        """OLS regression: y = β × x + ε. Returns β."""
        x_with_const = np.column_stack([np.ones(len(x)), x])
        try:
            beta = np.linalg.lstsq(x_with_const, y, rcond=None)[0]
            return float(beta[1])
        except np.linalg.LinAlgError:
            return 1.0

    def _adf_pvalue(self, spread: np.ndarray) -> float:
        """Approximate ADF test p-value for stationarity.

        Simplified implementation using Dickey-Fuller regression.
        H0: unit root (non-stationary). Reject if p < 0.05.
        """
        n = len(spread)
        if n < 20:
            return 1.0

        # ΔZ_t = α + γ * Z_{t-1} + ε_t
        dz = np.diff(spread)
        z_lag = spread[:-1]

        X = np.column_stack([np.ones(len(z_lag)), z_lag])
        try:
            beta = np.linalg.lstsq(X, dz, rcond=None)[0]
            residuals = dz - X @ beta
            se = np.sqrt(np.sum(residuals ** 2) / (len(dz) - 2))
            se_gamma = se / np.sqrt(np.sum((z_lag - np.mean(z_lag)) ** 2))

            if se_gamma <= 0:
                return 1.0

            t_stat = beta[1] / se_gamma

            # Approximate p-value from MacKinnon critical values
            # For n=100: 1%=-3.51, 5%=-2.89, 10%=-2.58
            if t_stat < -3.51:
                return 0.005
            elif t_stat < -2.89:
                return 0.03
            elif t_stat < -2.58:
                return 0.08
            else:
                return 0.50

        except (np.linalg.LinAlgError, ValueError):
            return 1.0

    def _compute_half_life(self, spread: np.ndarray) -> float:
        """Compute OU mean-reversion half-life.

        Half-life = -log(2) / log(φ) where φ is AR(1) coefficient.
        """
        n = len(spread)
        if n < 10:
            return 100.0

        # AR(1): Z_t = φ * Z_{t-1} + ε
        z_lag = spread[:-1].reshape(-1, 1)
        z_cur = spread[1:]

        try:
            phi = float(np.linalg.lstsq(z_lag, z_cur, rcond=None)[0][0])
        except np.linalg.LinAlgError:
            return 100.0

        if phi >= 1.0 or phi <= 0:
            return 100.0  # Non-mean-reverting

        half_life = -math.log(2) / math.log(phi)
        return max(1.0, min(200.0, half_life))

    def get_all_states(self) -> Dict[str, dict]:
        """Get current state of all tracked pairs."""
        return {name: {
            "z_score": s.z_score,
            "is_cointegrated": s.is_cointegrated,
            "half_life": s.half_life_bars,
            "adf_p": s.adf_pvalue,
            "hedge_ratio": s.hedge_ratio,
        } for name, s in self._states.items()}


def detect_pair_signal(
    symbol: str,
    prices: List[float],
    hurst: float,
) -> Optional[Dict]:
    """Detect pairs trading signal for a symbol.

    Called by bridge.py as a signal generator. Checks if the symbol
    belongs to a known ETP pair, computes the z-score of recent prices
    vs the pair's mean, and signals if mean-reverting conditions hold.

    Args:
        symbol: Ticker symbol (e.g. "3USL.L")
        prices: Recent close prices (list)
        hurst: Current Hurst exponent for the symbol

    Returns:
        dict with confidence (int) and z_score (float), or None.
    """
    # Find which pair this symbol belongs to
    pair_name = None
    for name, (long_t, inv_t) in ETP_PAIRS.items():
        if symbol in (long_t, inv_t):
            pair_name = name
            break

    if pair_name is None:
        return None

    if len(prices) < 10:
        return None

    arr = np.array(prices, dtype=float)
    mean_val = float(np.mean(arr))
    std_val = float(np.std(arr, ddof=1))

    if std_val < 1e-10:
        return None

    z_score = float((arr[-1] - mean_val) / std_val)

    # Signal if |z| > 2.0 and hurst < 0.4 (mean-reverting regime)
    if abs(z_score) > 2.0 and hurst < 0.4:
        # Confidence scales with z-score magnitude and mean-reversion strength
        base_conf = 55
        z_bonus = min(20, int((abs(z_score) - 2.0) * 10))
        hurst_bonus = int((0.4 - hurst) * 30)  # Lower hurst = stronger MR
        confidence = min(90, base_conf + z_bonus + hurst_bonus)

        return {
            "confidence": confidence,
            "z_score": round(z_score, 3),
            "pair_name": pair_name,
            "direction": "long" if z_score < -2.0 else "short",
            "strategy": "Pairs",
        }

    return None
