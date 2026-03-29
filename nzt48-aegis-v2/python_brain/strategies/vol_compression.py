"""Volatility Compression Breakout — Book 22.

Detects periods of low volatility (squeeze) that precede explosive moves.
Three indicators combined into a squeeze score:
1. Bollinger Band Width (BBW) percentile
2. Keltner Channel squeeze (BB inside KC)
3. ATR percentile

Entry: squeeze_score > 0.7 AND breakout confirmed (close beyond Keltner)
Exit: trailing stop or exhaustion (volume climax)

Usage:
    from python_brain.strategies.vol_compression import (
        detect_squeeze, SqueezeSignal,
    )

    signal = detect_squeeze(closes, highs, lows, volumes)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

log = logging.getLogger("vol_compression")


@dataclass
class SqueezeSignal:
    """Signal from volatility compression detection."""
    ticker: str = ""
    squeeze_score: float = 0.0  # 0-1, higher = tighter squeeze
    breakout_direction: str = ""  # "up" or "down" or ""
    confidence: int = 0
    bbw_percentile: float = 0.0
    atr_percentile: float = 0.0
    keltner_squeeze: bool = False
    strategy: str = "VolCompression"


def detect_squeeze(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    volumes: np.ndarray,
    ticker: str = "",
    bb_period: int = 20,
    kc_period: int = 20,
    kc_mult: float = 1.5,
    lookback: int = 100,
) -> Optional[SqueezeSignal]:
    """Detect volatility compression and potential breakout.

    Returns SqueezeSignal if squeeze detected, None otherwise.
    """
    n = len(closes)
    if n < max(bb_period, kc_period, lookback) + 5:
        return None

    # Bollinger Band Width
    sma = np.convolve(closes, np.ones(bb_period) / bb_period, mode="valid")
    if len(sma) < 2:
        return None
    std = np.array([np.std(closes[i:i + bb_period], ddof=1) for i in range(n - bb_period + 1)])
    bbw = 2 * std / np.maximum(sma, 1e-10)
    current_bbw = bbw[-1]

    # BBW percentile over lookback
    bbw_history = bbw[-lookback:] if len(bbw) >= lookback else bbw
    bbw_pct = np.sum(bbw_history < current_bbw) / len(bbw_history)

    # ATR
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
        ),
    )
    atr = np.convolve(tr, np.ones(kc_period) / kc_period, mode="valid")
    if len(atr) < 2:
        return None
    current_atr = atr[-1]

    # ATR percentile
    atr_history = atr[-lookback:] if len(atr) >= lookback else atr
    atr_pct = np.sum(atr_history < current_atr) / len(atr_history)

    # Keltner Channel squeeze: BB inside KC
    kc_upper = sma[-1] + kc_mult * current_atr
    kc_lower = sma[-1] - kc_mult * current_atr
    bb_upper = sma[-1] + 2 * std[-1]
    bb_lower = sma[-1] - 2 * std[-1]
    kc_squeeze = bb_upper < kc_upper and bb_lower > kc_lower

    # Squeeze score: weighted combination
    squeeze_score = (
        (1 - bbw_pct) * 0.35  # Low BBW = high squeeze
        + (1 - atr_pct) * 0.35  # Low ATR = high squeeze
        + (1.0 if kc_squeeze else 0.0) * 0.30  # KC squeeze bonus
    )

    if squeeze_score < 0.5:
        return None  # Not enough compression

    # Breakout detection: close beyond Keltner Channel
    current_close = closes[-1]
    prev_close = closes[-2]
    breakout_dir = ""
    if current_close > kc_upper and prev_close <= kc_upper:
        breakout_dir = "up"
    elif current_close < kc_lower and prev_close >= kc_lower:
        breakout_dir = "down"

    # Confidence
    base_conf = 55
    squeeze_bonus = int(squeeze_score * 20)  # Max +20
    breakout_bonus = 15 if breakout_dir else 0
    vol_confirm = 5 if len(volumes) > 1 and volumes[-1] > 1.5 * np.mean(volumes[-20:]) else 0
    confidence = min(90, base_conf + squeeze_bonus + breakout_bonus + vol_confirm)

    if confidence < 55:
        return None

    return SqueezeSignal(
        ticker=ticker,
        squeeze_score=round(squeeze_score, 3),
        breakout_direction=breakout_dir,
        confidence=confidence,
        bbw_percentile=round(bbw_pct, 3),
        atr_percentile=round(atr_pct, 3),
        keltner_squeeze=kc_squeeze,
    )
