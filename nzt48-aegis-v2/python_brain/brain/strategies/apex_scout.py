"""Apex Scout — RVOL anomaly scanner for 700 tickers on 60s snapshots.

PURE FUNCTION. No side effects. No I/O. No state mutation. No threading (H07).
No .apply() or iterrows() (H60). Zero-division guards on ALL divisions (H61).
Correlation on log returns, not raw prices (H63).
Logging via callback to Rust PyO3 channel (H08).
"""

import numpy as np

from brain.config import (
    CONFIDENCE_FLOOR,
    LOG_LEVEL_DEBUG,
    RVOL_LOOKBACK,
    RVOL_THRESHOLD,
    VOL_ROLLING_WINDOW,
    VOL_TARGET_ANNUAL_PCT,
    TRADING_DAYS_PER_YEAR,
)


def _safe_div(numer, denom):
    """Zero-division guard (H61): replace zero denominators with 1e-9."""
    safe_denom = np.where(denom == 0, 1e-9, denom)
    return numer / safe_denom


def _moreira_muir_scale(log_returns, vol_target_annual, window, days_per_year):
    """Moreira-Muir (2017): inversely scale by realized volatility.

    Higher realized vol → smaller position. Returns scaling factor in (0, 2].
    Uses log returns (H63).
    """
    if len(log_returns) < window:
        return 1.0
    recent = log_returns[-window:]
    realized_vol = np.std(recent, ddof=1) * np.sqrt(days_per_year)
    safe_vol = max(realized_vol, 1e-9)  # H61
    scale = vol_target_annual / (100.0 * safe_vol)
    return float(np.clip(scale, 0.01, 2.0))


def evaluate(snapshots, log_fn=None):
    """Evaluate Apex Scout on a list of 60-second OHLCV snapshots for ONE ticker.

    Args:
        snapshots: list of dicts with keys: open, high, low, close, volume,
            timestamp_ns. Must be chronologically ordered. Max 500 bars.
        log_fn: optional callback(level, message) for logging to Rust (H08).

    Returns:
        dict with keys {confidence, kelly_fraction, features} or None.
        None means no signal (filtered, insufficient data, below floor).
    """
    if not snapshots:
        return None

    n = len(snapshots)
    if n < 2:
        if log_fn:
            log_fn(LOG_LEVEL_DEBUG, f"apex: insufficient snapshots ({n})")
        return None

    # Extract arrays — vectorized (H60: no iterrows)
    closes = np.array([s["close"] for s in snapshots], dtype=np.float64)
    volumes = np.array([s["volume"] for s in snapshots], dtype=np.float64)

    # Log returns (H63)
    safe_closes = np.where(closes == 0, 1e-9, closes)  # H61
    log_returns = np.diff(np.log(safe_closes))

    # Relative Volume (RVOL) anomaly detection
    lookback = min(RVOL_LOOKBACK, n)
    vol_mean = np.mean(volumes[-lookback:])
    safe_vol_mean = max(vol_mean, 1e-9)  # H61
    current_rvol = volumes[-1] / safe_vol_mean

    # Price momentum: last bar return
    if n >= 2:
        bar_return = (closes[-1] - closes[-2]) / max(closes[-2], 1e-9)  # H61
    else:
        bar_return = 0.0

    # Moreira-Muir scaling
    mm_scale = _moreira_muir_scale(
        log_returns, VOL_TARGET_ANNUAL_PCT, VOL_ROLLING_WINDOW, TRADING_DAYS_PER_YEAR
    )

    # RVOL anomaly scoring
    rvol_score = 0.0
    if current_rvol >= RVOL_THRESHOLD:
        # Scale score by how much RVOL exceeds threshold
        rvol_excess = (current_rvol - RVOL_THRESHOLD) / RVOL_THRESHOLD
        rvol_score = min(rvol_excess * 50.0, 50.0)

    # Positive momentum confirmation
    momentum_score = 0.0
    if bar_return > 0:
        momentum_score = min(bar_return * 1000.0, 50.0)

    # Combined confidence
    raw_confidence = (rvol_score + momentum_score) * mm_scale
    confidence = float(np.clip(raw_confidence, 0.0, 100.0))

    # Confidence floor
    if confidence < CONFIDENCE_FLOOR:
        if log_fn:
            log_fn(LOG_LEVEL_DEBUG, f"apex: confidence {confidence:.1f} < floor")
        return None

    # Preliminary Kelly (full 13-factor in Phase 6C)
    kelly = min(confidence / 1000.0, 0.20)

    features = {
        "rvol": float(current_rvol),
        "bar_return": float(bar_return),
        "mm_scale": float(mm_scale),
        "rvol_score": float(rvol_score),
        "momentum_score": float(momentum_score),
    }

    return {
        "confidence": confidence,
        "kelly_fraction": float(np.clip(kelly, 0.0, 0.20)),
        "features": features,
    }
