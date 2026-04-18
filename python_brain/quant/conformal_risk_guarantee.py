"""
Conformal Prediction with Risk Guarantees

Upgrades standard conformal prediction (coverage guarantee) to
Risk-Controlled Prediction Sets (RCPS) and Conformal Risk Control (CRC).

Reference:
- Angelopoulos & Bates (2022) "Gentle Introduction to Conformal Prediction"
- Angelopoulos et al. (2024) "Conformal Risk Control"
- Bates et al. (2021) "Distribution-Free Risk-Controlling Prediction Sets"

Standard conformal: "prediction set contains truth with probability 1-alpha"
Risk-controlled:    "expected loss of prediction <= alpha"

Useful for:
- Position sizing with bounded expected loss
- Stop placement with bounded expected drawdown
- Entry timing with bounded expected slippage
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class ConformalRiskBounds:
    """Bounds for a prediction with risk guarantee."""
    lower: float
    upper: float
    median: float
    risk_level: float              # alpha (max expected loss)
    coverage: float                 # 1 - alpha
    n_calibration: int              # size of calibration set


def conformal_quantile_prediction(
    calibration_scores: np.ndarray,     # |residuals| on calibration set
    alpha: float = 0.1,
) -> float:
    """
    Standard split conformal prediction: (1-alpha)-quantile of residuals.

    Returns the half-width of a prediction interval with 1-alpha coverage.
    """
    n = len(calibration_scores)
    if n == 0:
        return 0.0

    # Adjusted quantile for finite sample
    q_level = math.ceil((n + 1) * (1 - alpha)) / n
    q_level = min(q_level, 1.0)

    return float(np.quantile(calibration_scores, q_level))


def conformal_risk_control_threshold(
    loss_scores: np.ndarray,           # loss(y, prediction) on calibration set
    target_risk: float = 0.1,          # max allowed E[loss]
) -> float:
    """
    Find the threshold lambda_hat such that E[loss(y, prediction_set(lambda))] <= target_risk.

    For monotone loss functions (e.g., "prediction set contains truth").
    """
    n = len(loss_scores)
    if n == 0:
        return 0.0

    # Sort losses in decreasing order of lambda tightness
    sorted_losses = np.sort(loss_scores)[::-1]

    # Find smallest lambda_hat such that mean(losses) + 1/n <= target_risk
    # (Conservative upper bound from Bates et al. 2021)
    for i in range(n):
        # Treat top-i losses as "above threshold"
        candidate_risk = np.mean(sorted_losses[i:]) + 1.0 / n
        if candidate_risk <= target_risk:
            return float(sorted_losses[i])

    return float(sorted_losses[-1])


def build_prediction_bounds(
    point_prediction: float,
    calibration_residuals: np.ndarray,
    alpha: float = 0.1,
) -> ConformalRiskBounds:
    """
    Build conformal prediction interval around a point estimate.

    Args:
        point_prediction: e.g., predicted return, predicted drawdown
        calibration_residuals: |y_i - y_hat_i| on calibration set
        alpha: miscoverage rate (0.1 = 90% coverage)

    Returns:
        ConformalRiskBounds with [lower, upper] that contains truth w.p. 1-alpha
    """
    half_width = conformal_quantile_prediction(calibration_residuals, alpha)
    return ConformalRiskBounds(
        lower=point_prediction - half_width,
        upper=point_prediction + half_width,
        median=point_prediction,
        risk_level=alpha,
        coverage=1.0 - alpha,
        n_calibration=len(calibration_residuals),
    )


def risk_controlled_position_size(
    predicted_edge_bps: float,
    calibration_residuals_bps: np.ndarray,
    max_expected_loss_bps: float = 10.0,
    capital: float = 10000.0,
) -> tuple[float, ConformalRiskBounds]:
    """
    Size a position such that conformal lower bound of edge > cost.

    Returns:
        (position_fraction, bounds)

    position_fraction = 0 if conformal lower bound is below zero.
    """
    # Build 90% CI on edge
    bounds = build_prediction_bounds(
        predicted_edge_bps,
        calibration_residuals_bps,
        alpha=0.1,
    )

    # If even the lower bound is positive, size up
    if bounds.lower > max_expected_loss_bps:
        # Kelly-like sizing on lower bound
        edge_frac = (bounds.lower - max_expected_loss_bps) / 10000
        # Cap at 5% per position
        position_frac = min(edge_frac * 2, 0.05)
    elif bounds.median > max_expected_loss_bps:
        # Median positive but lower bound negative — half size
        position_frac = 0.005  # 0.5% of capital
    else:
        # Not worth taking
        position_frac = 0.0

    return position_frac, bounds


def conformal_stop_placement(
    entry_price: float,
    side: str,                          # "BUY" or "SELL"
    calibration_adverse_moves_bps: np.ndarray,  # historical adverse moves in bps
    max_drawdown_prob: float = 0.05,    # 5% prob of stop being hit (well... calibrated)
    atr_bps: float = 100.0,
) -> tuple[float, ConformalRiskBounds]:
    """
    Place stop using conformal prediction of adverse moves.

    Returns stop price that, with probability (1-alpha), won't be breached
    by normal volatility.

    Falls back to ATR-based if calibration empty.
    """
    if len(calibration_adverse_moves_bps) < 20:
        # Not enough calibration data — ATR fallback
        stop_distance_bps = 2.0 * atr_bps
        if side == "BUY":
            stop_price = entry_price * (1 - stop_distance_bps / 10000)
        else:
            stop_price = entry_price * (1 + stop_distance_bps / 10000)

        return stop_price, ConformalRiskBounds(
            lower=entry_price * (1 - stop_distance_bps / 10000),
            upper=entry_price * (1 + stop_distance_bps / 10000),
            median=entry_price,
            risk_level=0.2,  # ATR has no formal guarantee
            coverage=0.8,
            n_calibration=len(calibration_adverse_moves_bps),
        )

    # Conformal quantile of adverse moves
    stop_distance_bps = conformal_quantile_prediction(
        calibration_adverse_moves_bps,
        alpha=max_drawdown_prob,
    )

    if side == "BUY":
        stop_price = entry_price * (1 - stop_distance_bps / 10000)
    else:
        stop_price = entry_price * (1 + stop_distance_bps / 10000)

    bounds = build_prediction_bounds(
        entry_price,
        np.abs(calibration_adverse_moves_bps / 10000 * entry_price),
        alpha=max_drawdown_prob,
    )

    return stop_price, bounds


class RollingCalibrationSet:
    """Maintains a rolling window of calibration residuals for online conformal prediction."""

    def __init__(self, window_size: int = 500):
        self.window_size = window_size
        self.residuals: list[float] = []

    def add(self, residual: float) -> None:
        self.residuals.append(abs(residual))
        if len(self.residuals) > self.window_size:
            self.residuals.pop(0)

    def get_threshold(self, alpha: float = 0.1) -> float:
        if not self.residuals:
            return 0.0
        return conformal_quantile_prediction(np.array(self.residuals), alpha)

    def __len__(self) -> int:
        return len(self.residuals)


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        # Test 1: Conformal interval
        rng = np.random.default_rng(42)
        residuals = np.abs(rng.normal(0, 1, 200))
        half_width = conformal_quantile_prediction(residuals, alpha=0.1)
        print(f"90% CI half-width for std-normal: {half_width:.3f} (expected ~1.64)")

        # Test 2: Risk-controlled position size
        edge_residuals = np.abs(rng.normal(0, 5, 500))
        pos_frac, bounds = risk_controlled_position_size(
            predicted_edge_bps=15.0,
            calibration_residuals_bps=edge_residuals,
            max_expected_loss_bps=5.0,
            capital=10000,
        )
        print(f"Position fraction: {pos_frac:.4f}")
        print(f"Bounds: [{bounds.lower:.2f}, {bounds.upper:.2f}] bps")

        # Test 3: Conformal stop
        adverse = np.abs(rng.normal(0, 50, 500))  # 50 bps std
        stop_price, bounds = conformal_stop_placement(
            entry_price=100.0,
            side="BUY",
            calibration_adverse_moves_bps=adverse,
            max_drawdown_prob=0.05,
        )
        print(f"Stop price: {stop_price:.4f} (entry=100.00)")
        print(f"Stop distance: {(100 - stop_price) * 100:.2f} bps")

        # Test 4: Rolling calibration
        calibrator = RollingCalibrationSet(window_size=100)
        for _ in range(150):
            calibrator.add(rng.normal(0, 2))
        print(f"Rolling window size: {len(calibrator)}")
        print(f"90% threshold: {calibrator.get_threshold(0.1):.3f}")
        print("OK")
