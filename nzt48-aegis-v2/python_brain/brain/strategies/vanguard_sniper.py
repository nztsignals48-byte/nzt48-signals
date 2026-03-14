"""Vanguard Sniper — Momentum strategy for top 300 ultra-liquid tickers.

PURE FUNCTION. No side effects. No I/O. No state mutation. No threading (H07).
No .apply() or iterrows() (H60). Zero-division guards on ALL divisions (H61).
Correlation on log returns, not raw prices (H63).
Logging via callback to Rust PyO3 channel (H08).
"""

import numpy as np

from brain.config import (
    ADX_PERIOD,
    CONFIDENCE_FLOOR,
    EMA_FAST_PERIOD,
    LOG_LEVEL_DEBUG,
    MOMENTUM_LOOKBACK,
    VOL_ROLLING_WINDOW,
    VOL_TARGET_ANNUAL_PCT,
    VOLUME_BREAKOUT_MULT,
    TRADING_DAYS_PER_YEAR,
)


def _safe_div(numer, denom):
    """Zero-division guard (H61): replace zero denominators with 1e-9."""
    safe_denom = np.where(denom == 0, 1e-9, denom)
    return numer / safe_denom


def _ema(prices, period):
    """Exponential Moving Average using vectorized cumulative approach."""
    if len(prices) < period:
        return np.full_like(prices, np.nan, dtype=np.float64)
    alpha = 2.0 / (period + 1)
    result = np.empty_like(prices, dtype=np.float64)
    result[0] = prices[0]
    for i in range(1, len(prices)):
        result[i] = alpha * prices[i] + (1 - alpha) * result[i - 1]
    return result


def _adx(highs, lows, closes, period):
    """Average Directional Index (Wilder). Vectorized, no .apply() (H60)."""
    n = len(closes)
    if n < period + 1:
        return np.full(n, np.nan, dtype=np.float64)

    # True Range
    high_low = highs[1:] - lows[1:]
    high_close = np.abs(highs[1:] - closes[:-1])
    low_close = np.abs(lows[1:] - closes[:-1])
    tr = np.maximum(high_low, np.maximum(high_close, low_close))

    # Directional Movement
    up_move = highs[1:] - highs[:-1]
    down_move = lows[:-1] - lows[1:]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # Smoothed using Wilder's method (equivalent to EMA with alpha=1/period)
    atr = np.empty(len(tr), dtype=np.float64)
    smooth_plus = np.empty(len(tr), dtype=np.float64)
    smooth_minus = np.empty(len(tr), dtype=np.float64)
    atr[0] = tr[0]
    smooth_plus[0] = plus_dm[0]
    smooth_minus[0] = minus_dm[0]
    for i in range(1, len(tr)):
        atr[i] = atr[i - 1] - atr[i - 1] / period + tr[i]
        smooth_plus[i] = smooth_plus[i - 1] - smooth_plus[i - 1] / period + plus_dm[i]
        smooth_minus[i] = (
            smooth_minus[i - 1] - smooth_minus[i - 1] / period + minus_dm[i]
        )

    plus_di = 100.0 * _safe_div(smooth_plus, atr)
    minus_di = 100.0 * _safe_div(smooth_minus, atr)
    dx = 100.0 * _safe_div(np.abs(plus_di - minus_di), plus_di + minus_di)

    # Smooth DX to get ADX
    adx_arr = np.empty(len(dx), dtype=np.float64)
    adx_arr[0] = dx[0]
    for i in range(1, len(dx)):
        adx_arr[i] = adx_arr[i - 1] + (dx[i] - adx_arr[i - 1]) / period

    # Pad with NaN at front to match original array length
    result = np.full(n, np.nan, dtype=np.float64)
    result[1:] = adx_arr
    return result


def _moreira_muir_scale(log_returns, vol_target_annual, window, days_per_year):
    """Moreira-Muir (2017): scale position inversely by realized volatility.

    Higher realized vol → smaller position. Returns scaling factor in (0, 2].
    Uses log returns (H63), not raw prices.
    """
    if len(log_returns) < window:
        return 1.0
    recent = log_returns[-window:]
    realized_vol = np.std(recent, ddof=1) * np.sqrt(days_per_year)
    safe_vol = max(realized_vol, 1e-9)  # H61: zero-division guard
    scale = vol_target_annual / (100.0 * safe_vol)
    return float(np.clip(scale, 0.01, 2.0))


def evaluate(ticks, log_fn=None):
    """Evaluate Vanguard Sniper on a batch of ticks for ONE ticker.

    Args:
        ticks: list of dicts with keys: last, bid, ask, volume, timestamp_ns.
            Must be chronologically ordered. Rolling window max 500 bars.
        log_fn: optional callback(level, message) for logging to Rust (H08).

    Returns:
        dict with keys {confidence, kelly_fraction, features} or None.
        None means no signal (filtered, insufficient data, below floor).
    """
    if not ticks:
        return None

    n = len(ticks)
    if n < 2:
        if log_fn:
            log_fn(LOG_LEVEL_DEBUG, f"vanguard: insufficient ticks ({n})")
        return None

    # Extract arrays — vectorized (H60: no iterrows)
    closes = np.array([t["last"] for t in ticks], dtype=np.float64)
    highs = np.array(
        [max(t["last"], t.get("high", t["last"])) for t in ticks], dtype=np.float64
    )
    lows = np.array(
        [min(t["last"], t.get("low", t["last"])) for t in ticks], dtype=np.float64
    )
    volumes = np.array([t["volume"] for t in ticks], dtype=np.float64)

    # Log returns (H63: correlation on log returns, not raw prices)
    safe_closes = np.where(closes == 0, 1e-9, closes)  # H61
    log_returns = np.diff(np.log(safe_closes))

    if len(log_returns) < MOMENTUM_LOOKBACK:
        return None

    # ADX momentum detection
    adx_values = _adx(highs, lows, closes, ADX_PERIOD)
    current_adx = adx_values[-1]
    if np.isnan(current_adx):
        return None

    # EMA trend confirmation
    ema_values = _ema(closes, EMA_FAST_PERIOD)
    current_ema = ema_values[-1]
    price_above_ema = closes[-1] > current_ema

    # Volume breakout detection
    if n >= MOMENTUM_LOOKBACK:
        vol_mean = np.mean(volumes[-MOMENTUM_LOOKBACK:])
        safe_vol_mean = max(vol_mean, 1e-9)  # H61
        rvol = volumes[-1] / safe_vol_mean
    else:
        rvol = 1.0

    volume_breakout = rvol >= VOLUME_BREAKOUT_MULT

    # Moreira-Muir volatility scaling
    mm_scale = _moreira_muir_scale(
        log_returns, VOL_TARGET_ANNUAL_PCT, VOL_ROLLING_WINDOW, TRADING_DAYS_PER_YEAR
    )

    # Momentum score: weighted combination
    momentum_score = 0.0
    if current_adx > ADX_PERIOD:  # ADX > period threshold = trending
        momentum_score += 40.0
    if price_above_ema:
        momentum_score += 30.0
    if volume_breakout:
        momentum_score += 30.0

    # Scale confidence by momentum score
    confidence = momentum_score * mm_scale

    # Confidence floor (from config, H109)
    if confidence < CONFIDENCE_FLOOR:
        if log_fn:
            log_fn(LOG_LEVEL_DEBUG, f"vanguard: confidence {confidence:.1f} < floor")
        return None

    # Kelly fraction: simple preliminary sizing (full 13-factor in Phase 6C)
    kelly = min(confidence / 1000.0, 0.20)  # Preliminary, capped at H57

    features = {
        "adx": float(current_adx),
        "ema_20": float(current_ema),
        "rvol": float(rvol),
        "mm_scale": float(mm_scale),
        "momentum_score": float(momentum_score),
    }

    return {
        "confidence": float(np.clip(confidence, 0.0, 100.0)),
        "kelly_fraction": float(np.clip(kelly, 0.0, 0.20)),
        "features": features,
    }
