"""
NZT-48 Trading System -- Cointegration-Based Pairs Trading Engine
Sprint 2 Enhancement #22: Institutional-grade pairs trading via cointegration.

Replaces naive correlation-based pair detection with statistically rigorous
cointegration testing, Kalman-filter adaptive hedge ratios, and
Ornstein-Uhlenbeck mean-reversion timing.

Pipeline per pair:
    1. Engle-Granger cointegration test  (are they truly linked?)
    2. Kalman filter hedge ratio          (what is the live ratio?)
    3. Spread construction                (series_a - HR * series_b - intercept)
    4. OU half-life estimation            (how fast does it revert?)
    5. Z-score signal generation          (trade it now?)

Consumers:
    - signal_aggregator.py   -- pairs signals feed into the master signal
    - portfolio_risk.py      -- spread exposure tracked as synthetic position
    - dashboard              -- live pair status table

Dependencies: statsmodels, pykalman, numpy, pandas.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from pykalman import KalmanFilter
from statsmodels.tsa.stattools import adfuller, coint

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.feeds.cointegration")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class KalmanState:
    """Tracks the live Kalman filter state for a single pair.

    The hedge ratio and intercept are the state vector of the filter.
    hedge_ratio_std captures estimation uncertainty from the filtered
    state covariance.
    """
    hedge_ratio: float = 1.0
    intercept: float = 0.0
    hedge_ratio_std: float = 0.0
    last_updated: str = ""


@dataclass
class PairAnalysis:
    """Complete statistical profile of a cointegrated pair.

    Produced by CointegrationEngine.analyze_pair() and consumed by
    signal_aggregator and the dashboard.
    """
    ticker_a: str = ""
    ticker_b: str = ""
    is_cointegrated: bool = False
    p_value: float = 1.0
    hedge_ratio: float = 1.0
    intercept: float = 0.0
    half_life_days: float = float("inf")
    current_zscore: float = 0.0
    is_spread_stationary: bool = False
    signal: str = "NONE"           # "LONG_A_SHORT_B", "SHORT_A_LONG_B", "NONE"
    signal_strength: float = 0.0   # 0-1


# ---------------------------------------------------------------------------
# Defined pairs (matching S9 semiconductor universe)
# ---------------------------------------------------------------------------

DEFINED_PAIRS: list[tuple[str, str]] = [
    ("NVDA", "AMD"),     # GPU duopoly
    ("AVGO", "MRVL"),    # Networking semis
    ("TSM", "ASML"),     # Foundry vs equipment
    ("LRCX", "KLAC"),    # Semi equipment
]


# ---------------------------------------------------------------------------
# Cointegration Engine
# ---------------------------------------------------------------------------

class CointegrationEngine:
    """Institutional-grade pairs trading engine using cointegration,
    Kalman filter hedge ratios, and Ornstein-Uhlenbeck half-life
    estimation.

    All public methods are designed to be safe: they catch exceptions
    internally and return conservative defaults so the caller never
    has to guard against unexpected blowups.
    """

    # ------------------------------------------------------------------ #
    # 1. Cointegration Testing
    # ------------------------------------------------------------------ #

    @staticmethod
    def test_cointegration(
        series_a: np.ndarray,
        series_b: np.ndarray,
    ) -> dict:
        """Run Engle-Granger cointegration test on two price series.

        Parameters
        ----------
        series_a, series_b : np.ndarray
            Equal-length price arrays (e.g. daily closes).

        Returns
        -------
        dict with keys:
            is_cointegrated : bool   -- True if p_value < 0.05
            p_value         : float  -- Engle-Granger p-value
            test_statistic  : float  -- Engle-Granger test statistic
            critical_values : dict   -- {"1%": .., "5%": .., "10%": ..}
            method          : str    -- "engle_granger"
        """
        default = {
            "is_cointegrated": False,
            "p_value": 1.0,
            "test_statistic": 0.0,
            "critical_values": {"1%": 0.0, "5%": 0.0, "10%": 0.0},
            "method": "engle_granger",
        }
        try:
            series_a = np.asarray(series_a, dtype=float)
            series_b = np.asarray(series_b, dtype=float)

            if len(series_a) < 20 or len(series_b) < 20:
                logger.warning(
                    "Cointegration test requires >= 20 observations, got %d / %d",
                    len(series_a), len(series_b),
                )
                return default

            if len(series_a) != len(series_b):
                logger.warning(
                    "Series length mismatch: %d vs %d", len(series_a), len(series_b),
                )
                return default

            # Engle-Granger two-step cointegration test
            test_stat, p_value, crit_values = coint(series_a, series_b)

            critical_values_dict = {
                "1%": float(crit_values[0]),
                "5%": float(crit_values[1]),
                "10%": float(crit_values[2]),
            }

            is_coint = bool(p_value < 0.05)

            logger.info(
                "Cointegration test: stat=%.4f p=%.4f cointegrated=%s",
                test_stat, p_value, is_coint,
            )

            return {
                "is_cointegrated": is_coint,
                "p_value": float(p_value),
                "test_statistic": float(test_stat),
                "critical_values": critical_values_dict,
                "method": "engle_granger",
            }

        except Exception:
            logger.exception("Cointegration test failed")
            return default

    # ------------------------------------------------------------------ #
    # 2. Kalman Filter Hedge Ratio
    # ------------------------------------------------------------------ #

    @staticmethod
    def compute_kalman_hedge_ratio(
        series_a: np.ndarray,
        series_b: np.ndarray,
    ) -> KalmanState:
        """Compute a dynamically-updating hedge ratio via Kalman filter.

        The state vector is [hedge_ratio, intercept].
        Observation model: series_a[t] = hedge_ratio * series_b[t] + intercept + noise.

        Parameters
        ----------
        series_a, series_b : np.ndarray
            Equal-length price arrays.

        Returns
        -------
        KalmanState with the final filtered hedge ratio, intercept, and
        estimation uncertainty (hedge_ratio_std).
        """
        default = KalmanState(
            last_updated=datetime.now(timezone.utc).isoformat(),
        )
        try:
            series_a = np.asarray(series_a, dtype=float)
            series_b = np.asarray(series_b, dtype=float)

            n = len(series_a)
            if n < 10 or len(series_b) < 10:
                logger.warning("Kalman filter requires >= 10 observations")
                return default
            if len(series_a) != len(series_b):
                logger.warning("Series length mismatch for Kalman filter")
                return default

            # Build observation matrices: each row is [series_b[t], 1]
            obs_mat = np.column_stack([series_b, np.ones(n)])
            # Shape required by pykalman: (n_timesteps, 1, n_state)
            obs_mat = obs_mat.reshape(n, 1, 2)

            # Kalman filter setup
            # State: [hedge_ratio, intercept]
            # Transition: identity (random walk on parameters)
            kf = KalmanFilter(
                n_dim_obs=1,
                n_dim_state=2,
                transition_matrices=np.eye(2),
                observation_matrices=obs_mat,
                initial_state_mean=np.array([1.0, 0.0]),
                initial_state_covariance=np.eye(2),
                transition_covariance=1e-4 * np.eye(2),
                observation_covariance=np.array([[1.0]]),
            )

            # Run the filter forward
            state_means, state_covs = kf.filter(series_a.reshape(-1, 1))

            # Extract final state
            final_mean = state_means[-1]
            final_cov = state_covs[-1]

            hedge_ratio = float(final_mean[0])
            intercept = float(final_mean[1])
            hedge_ratio_std = float(np.sqrt(max(final_cov[0, 0], 0.0)))

            logger.info(
                "Kalman hedge ratio: %.4f (std=%.4f) intercept: %.4f",
                hedge_ratio, hedge_ratio_std, intercept,
            )

            return KalmanState(
                hedge_ratio=hedge_ratio,
                intercept=intercept,
                hedge_ratio_std=hedge_ratio_std,
                last_updated=datetime.now(timezone.utc).isoformat(),
            )

        except Exception:
            logger.exception("Kalman filter computation failed")
            return default

    @staticmethod
    def update_hedge_ratio(
        state: KalmanState,
        new_a: float,
        new_b: float,
    ) -> KalmanState:
        """Incremental Kalman update with a single new observation.

        Applies one step of the Kalman filter equations to update the
        hedge ratio and intercept given a new (series_a, series_b) tick.

        Parameters
        ----------
        state : KalmanState
            Current filter state (from compute_kalman_hedge_ratio or
            a previous update_hedge_ratio call).
        new_a, new_b : float
            New price observation for series A and B.

        Returns
        -------
        Updated KalmanState.
        """
        try:
            # Reconstruct minimal Kalman state for one-step update
            x = np.array([state.hedge_ratio, state.intercept])
            P = np.eye(2) * max(state.hedge_ratio_std ** 2, 1e-6)

            # Transition: identity (random walk)
            Q = 1e-4 * np.eye(2)
            R = np.array([[1.0]])

            # Predict step
            x_pred = x  # F = I
            P_pred = P + Q

            # Observation matrix for this tick: [new_b, 1]
            H = np.array([[new_b, 1.0]])

            # Innovation
            y = np.array([new_a]) - H @ x_pred
            S = H @ P_pred @ H.T + R

            # Kalman gain
            K = P_pred @ H.T @ np.linalg.inv(S)

            # Update step
            x_new = x_pred + (K @ y).flatten()
            P_new = (np.eye(2) - K @ H) @ P_pred

            return KalmanState(
                hedge_ratio=float(x_new[0]),
                intercept=float(x_new[1]),
                hedge_ratio_std=float(np.sqrt(max(P_new[0, 0], 0.0))),
                last_updated=datetime.now(timezone.utc).isoformat(),
            )

        except Exception:
            logger.exception("Kalman update failed, returning previous state")
            return KalmanState(
                hedge_ratio=state.hedge_ratio,
                intercept=state.intercept,
                hedge_ratio_std=state.hedge_ratio_std,
                last_updated=state.last_updated,
            )

    # ------------------------------------------------------------------ #
    # 3. Ornstein-Uhlenbeck Half-Life
    # ------------------------------------------------------------------ #

    @staticmethod
    def compute_ou_half_life(spread: np.ndarray) -> float:
        """Estimate the mean-reversion half-life of a spread via OU model.

        Fits: dS(t) = theta * S(t-1) + noise
        Half-life = -ln(2) / theta

        Parameters
        ----------
        spread : np.ndarray
            The spread time series (e.g. series_a - HR * series_b).

        Returns
        -------
        float : Half-life in bars (trading days).  Returns inf if
                the spread is not mean-reverting (theta >= 0).
        """
        try:
            spread = np.asarray(spread, dtype=float)

            if len(spread) < 10:
                logger.warning("OU half-life requires >= 10 observations")
                return float("inf")

            # dS = spread[1:] - spread[:-1]
            spread_lag = spread[:-1]
            spread_diff = spread[1:] - spread[:-1]

            # OLS: spread_diff = theta * spread_lag + noise
            # theta = cov(dS, S_lag) / var(S_lag)
            var_lag = np.var(spread_lag)
            if var_lag < 1e-15:
                logger.warning("Spread variance near zero, cannot compute OU half-life")
                return float("inf")

            theta = np.cov(spread_diff, spread_lag)[0, 1] / var_lag

            if theta >= 0:
                logger.info("Spread is not mean-reverting (theta=%.6f)", theta)
                return float("inf")

            half_life = -np.log(2) / theta
            logger.info("OU half-life: %.2f bars (theta=%.6f)", half_life, theta)
            return float(half_life)

        except Exception:
            logger.exception("OU half-life computation failed")
            return float("inf")

    # ------------------------------------------------------------------ #
    # 4. Spread Analysis
    # ------------------------------------------------------------------ #

    @staticmethod
    def compute_spread(
        series_a: np.ndarray,
        series_b: np.ndarray,
        hedge_ratio: float,
        intercept: float = 0.0,
    ) -> np.ndarray:
        """Compute the spread between two series given a hedge ratio.

        spread = series_a - hedge_ratio * series_b - intercept

        Parameters
        ----------
        series_a, series_b : np.ndarray
            Price arrays of equal length.
        hedge_ratio : float
            The cointegrating hedge ratio (from Kalman filter).
        intercept : float
            Intercept term (default 0.0).

        Returns
        -------
        np.ndarray : The spread series.
        """
        try:
            series_a = np.asarray(series_a, dtype=float)
            series_b = np.asarray(series_b, dtype=float)
            return series_a - hedge_ratio * series_b - intercept
        except Exception:
            logger.exception("Spread computation failed")
            return np.array([])

    @staticmethod
    def compute_zscore(spread: np.ndarray, lookback: int = 20) -> float:
        """Z-score of the latest spread value vs a rolling window.

        Parameters
        ----------
        spread : np.ndarray
            The spread time series.
        lookback : int
            Number of trailing bars for mean/std calculation (default 20).

        Returns
        -------
        float : Z-score of the most recent spread value.
                Returns 0.0 on any error.
        """
        try:
            spread = np.asarray(spread, dtype=float)

            if len(spread) < lookback:
                logger.warning(
                    "Spread length %d < lookback %d for Z-score",
                    len(spread), lookback,
                )
                return 0.0

            window = spread[-lookback:]
            mean = np.mean(window)
            std = np.std(window, ddof=1)

            if std < 1e-15:
                return 0.0

            zscore = (spread[-1] - mean) / std
            return float(zscore)

        except Exception:
            logger.exception("Z-score computation failed")
            return 0.0

    @staticmethod
    def is_spread_stationary(spread: np.ndarray) -> bool:
        """Test whether a spread is stationary via ADF test.

        Parameters
        ----------
        spread : np.ndarray
            The spread time series.

        Returns
        -------
        bool : True if ADF p-value < 0.05 (stationary).
        """
        try:
            spread = np.asarray(spread, dtype=float)

            if len(spread) < 20:
                logger.warning("ADF test requires >= 20 observations")
                return False

            result = adfuller(spread, autolag="AIC")
            p_value = result[1]

            logger.info("ADF stationarity test: p=%.4f stationary=%s", p_value, p_value < 0.05)
            return bool(p_value < 0.05)

        except Exception:
            logger.exception("ADF stationarity test failed")
            return False

    # ------------------------------------------------------------------ #
    # 5. Signal Generation
    # ------------------------------------------------------------------ #

    @staticmethod
    def generate_pair_signal(analysis: PairAnalysis) -> str:
        """Generate a trading signal from a completed PairAnalysis.

        Rules (applied in order):
            1. If NOT cointegrated            -> NONE
            2. If spread NOT stationary        -> NONE
            3. If half_life > 30 days          -> NONE (too slow)
            4. If Z > +2.0                     -> SHORT_A_LONG_B (expect convergence)
            5. If Z < -2.0                     -> LONG_A_SHORT_B
            6. If |Z| < 0.5                    -> NONE (too close to mean)
            7. Otherwise                       -> NONE

        Parameters
        ----------
        analysis : PairAnalysis
            Completed pair analysis from analyze_pair().

        Returns
        -------
        str : One of "LONG_A_SHORT_B", "SHORT_A_LONG_B", "NONE".
        """
        try:
            if not analysis.is_cointegrated:
                return "NONE"
            if not analysis.is_spread_stationary:
                return "NONE"
            if analysis.half_life_days > 30.0:
                return "NONE"

            z = analysis.current_zscore

            if z > 2.0:
                return "SHORT_A_LONG_B"
            if z < -2.0:
                return "LONG_A_SHORT_B"

            # |Z| between 0.5 and 2.0, or < 0.5 -- no signal
            return "NONE"

        except Exception:
            logger.exception("Signal generation failed")
            return "NONE"

    # ------------------------------------------------------------------ #
    # 6. Pair Analysis Pipeline
    # ------------------------------------------------------------------ #

    def analyze_pair(
        self,
        ticker_a: str,
        prices_a: np.ndarray,
        ticker_b: str,
        prices_b: np.ndarray,
    ) -> PairAnalysis:
        """Full cointegration analysis pipeline for a single pair.

        Steps:
            1. Cointegration test (Engle-Granger)
            2. Kalman filter hedge ratio
            3. Spread construction
            4. OU half-life estimation
            5. Spread stationarity check (ADF)
            6. Z-score computation
            7. Signal generation

        Parameters
        ----------
        ticker_a, ticker_b : str
            Ticker symbols.
        prices_a, prices_b : np.ndarray
            Equal-length daily close price arrays.

        Returns
        -------
        PairAnalysis : Fully populated analysis result.
        """
        result = PairAnalysis(ticker_a=ticker_a, ticker_b=ticker_b)

        try:
            prices_a = np.asarray(prices_a, dtype=float)
            prices_b = np.asarray(prices_b, dtype=float)

            # Step 1: Cointegration test
            coint_result = self.test_cointegration(prices_a, prices_b)
            result.is_cointegrated = coint_result["is_cointegrated"]
            result.p_value = coint_result["p_value"]

            # Step 2: Kalman filter hedge ratio
            kalman_state = self.compute_kalman_hedge_ratio(prices_a, prices_b)
            result.hedge_ratio = kalman_state.hedge_ratio
            result.intercept = kalman_state.intercept

            # Step 3: Spread construction
            spread = self.compute_spread(
                prices_a, prices_b, result.hedge_ratio, result.intercept,
            )

            if len(spread) == 0:
                logger.warning("Empty spread for %s/%s", ticker_a, ticker_b)
                return result

            # Step 4: OU half-life
            result.half_life_days = self.compute_ou_half_life(spread)

            # Step 5: Spread stationarity
            result.is_spread_stationary = self.is_spread_stationary(spread)

            # Step 6: Z-score
            result.current_zscore = self.compute_zscore(spread)

            # Step 7: Signal generation
            result.signal = self.generate_pair_signal(result)

            # Signal strength: normalised |Z| capped at 1.0 for Z in [2, 4]
            if result.signal != "NONE":
                abs_z = abs(result.current_zscore)
                # Strength ramps linearly from 0 at Z=2 to 1 at Z=4
                result.signal_strength = float(min(max((abs_z - 2.0) / 2.0, 0.0), 1.0))

            logger.info(
                "Pair analysis %s/%s: coint=%s p=%.4f HR=%.4f HL=%.1f Z=%.2f signal=%s (%.2f)",
                ticker_a, ticker_b,
                result.is_cointegrated, result.p_value,
                result.hedge_ratio, result.half_life_days,
                result.current_zscore, result.signal, result.signal_strength,
            )

        except Exception:
            logger.exception("Pair analysis failed for %s/%s", ticker_a, ticker_b)

        return result

    # ------------------------------------------------------------------ #
    # 7. Scan All Defined Pairs
    # ------------------------------------------------------------------ #

    def scan_all_pairs(
        self,
        price_data: dict[str, np.ndarray],
    ) -> list[PairAnalysis]:
        """Run analyze_pair for every pair in DEFINED_PAIRS.

        Parameters
        ----------
        price_data : dict[str, np.ndarray]
            Mapping of ticker -> daily close price array.

        Returns
        -------
        list[PairAnalysis] : Only pairs with a valid signal (signal != "NONE"),
                             sorted by signal_strength descending.
        """
        results: list[PairAnalysis] = []

        for ticker_a, ticker_b in DEFINED_PAIRS:
            if ticker_a not in price_data:
                logger.warning("No price data for %s, skipping pair %s/%s", ticker_a, ticker_a, ticker_b)
                continue
            if ticker_b not in price_data:
                logger.warning("No price data for %s, skipping pair %s/%s", ticker_b, ticker_a, ticker_b)
                continue

            analysis = self.analyze_pair(
                ticker_a, price_data[ticker_a],
                ticker_b, price_data[ticker_b],
            )

            if analysis.signal != "NONE":
                results.append(analysis)

        # Sort by signal strength descending
        results.sort(key=lambda a: a.signal_strength, reverse=True)

        logger.info(
            "Pair scan complete: %d/%d pairs with active signals",
            len(results), len(DEFINED_PAIRS),
        )

        return results
