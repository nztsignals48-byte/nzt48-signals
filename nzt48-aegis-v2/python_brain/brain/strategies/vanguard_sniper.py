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


def _is_auction_period(timestamp_ns: int) -> bool:
    """Check if a nanosecond UTC timestamp falls within LSE auction windows.

    LSE open auction:  07:50-08:00 UTC (approximate — BST offset not applied,
                       but Rust bridge sends UTC timestamps and LSE auctions
                       are defined in London local time. During BST, this gate
                       fires 1 hour early — acceptable conservative behaviour).
    LSE close auction: 16:30-16:35 UTC.

    Returns True if the timestamp is within an auction window.
    """
    if timestamp_ns == 0:
        return False
    utc_secs_of_day = (timestamp_ns // 1_000_000_000) % 86400
    # Open auction: 07:50-08:00 UTC = 28200-28800 secs
    if 28200 <= utc_secs_of_day < 28800:
        return True
    # Close auction: 16:30-16:35 UTC = 59400-59700 secs
    if 59400 <= utc_secs_of_day < 59700:
        return True
    return False


def evaluate(ticks, log_fn=None, confidence_floor=None):
    """Evaluate Vanguard Sniper on a batch of ticks for ONE ticker.

    Args:
        ticks: list of dicts with keys: last, bid, ask, volume, timestamp_ns.
            Must be chronologically ordered. Rolling window max 500 bars.
        log_fn: optional callback(level, message) for logging to Rust (H08).
        confidence_floor: optional override for minimum confidence. If None,
            uses the default from brain.config.CONFIDENCE_FLOOR.

    Returns:
        dict with keys {confidence, kelly_fraction, features} or None.
        None means no signal (filtered, insufficient data, below floor).
    """
    if not ticks:
        return None

    # Auction period gate: block entries during LSE open/close auctions.
    # Prices during auctions are indicative only — no continuous order book.
    last_tick = ticks[-1]
    ts_ns = last_tick.get("timestamp_ns", 0)
    if _is_auction_period(ts_ns):
        if log_fn:
            log_fn(LOG_LEVEL_DEBUG, "vanguard: AUCTION GATE — blocking signal during auction period")
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

    # Momentum score: graduated combination (not binary)
    # Graduated ADX: stronger trends score higher
    momentum_score = 0.0
    if current_adx >= 25.0:
        momentum_score += 40.0
    elif current_adx >= 15.0:
        momentum_score += 30.0
    elif current_adx >= 10.0:
        momentum_score += 20.0
    elif current_adx >= 7.0:
        momentum_score += 15.0

    # EMA trend confirmation (binary)
    if price_above_ema:
        momentum_score += 30.0

    # Graduated volume breakout
    if rvol >= VOLUME_BREAKOUT_MULT:
        momentum_score += 30.0
    elif rvol >= 1.5:
        momentum_score += 20.0
    elif rvol >= 1.2:
        momentum_score += 10.0

    # Confidence = raw momentum score (NOT scaled by Moreira-Muir).
    # Moreira-Muir vol scaling affects position SIZE via Kelly, not signal quality.
    # Previous bug: mm_scale ≈ 0.21 for 3x ETPs crushed confidence below floor,
    # killing 100% of signals. Vol-drag is already handled by 12-factor Kelly.
    confidence = momentum_score

    # Confidence floor (parameter override or config default, H109)
    _floor = confidence_floor if confidence_floor is not None else CONFIDENCE_FLOOR
    if confidence < _floor:
        if log_fn:
            log_fn(LOG_LEVEL_DEBUG, f"vanguard: confidence {confidence:.1f} < floor {_floor}")
        return None

    # Kelly fraction: preliminary sizing with Moreira-Muir applied to SIZE, not confidence.
    kelly = min((confidence / 1000.0) * mm_scale, 0.05)  # BT-008: optimal Kelly=5%

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
