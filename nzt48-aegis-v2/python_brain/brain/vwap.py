"""VWAP (Volume-Weighted Average Price) Calculator for intraday strategy signals.

Provides:
- Intraday VWAP: Sum(TypicalPrice * Volume) / Sum(Volume)
- Rolling standard deviation bands at configurable sigma levels (1, 2, 3)
- VWAP slope (rate of change over last N bars)
- Sigma band proximity detection
- Volume profile classification (declining vs accelerating)
- Automatic session reset

PURE FUNCTION-style design. No I/O. No threading (H07).
Zero-division guards on ALL divisions (H61).
"""

from __future__ import annotations

import math
from typing import Dict, List, NamedTuple, Optional


class VWAPBar(NamedTuple):
    """Single OHLCV bar for VWAP calculation."""

    high: float
    low: float
    close: float
    volume: float


class VWAPResult(NamedTuple):
    """Complete VWAP calculation output."""

    vwap: float
    upper_1: float  # +1 sigma
    lower_1: float  # -1 sigma
    upper_2: float  # +2 sigma
    lower_2: float  # -2 sigma
    upper_3: float  # +3 sigma
    lower_3: float  # -3 sigma
    slope: float  # VWAP rate of change over slope_window bars
    sigma_position: float  # Current price in sigma units from VWAP
    volume_profile: str  # "declining", "accelerating", or "neutral"


class VWAPCalculator:
    """Intraday VWAP calculator with bands, slope, and volume profile.

    Designed to be called on every tick. Maintains cumulative state for
    the current session and resets at session open.

    Usage:
        calc = VWAPCalculator()
        calc.reset()  # Call at session open
        for bar in bars:
            result = calc.update(bar)
    """

    def __init__(
        self,
        sigma_levels: tuple[float, ...] = (1.0, 2.0, 3.0),
        slope_window: int = 10,
        volume_short_window: int = 5,
        volume_long_window: int = 20,
    ) -> None:
        """Initialize VWAP calculator.

        Args:
            sigma_levels: Standard deviation multipliers for bands.
                          Must contain exactly 3 values. Default (1.0, 2.0, 3.0).
            slope_window: Number of bars for VWAP slope calculation.
            volume_short_window: Recent bars for volume profile (default 5).
            volume_long_window: Average bars for volume profile (default 20).
        """
        self.sigma_levels = sigma_levels
        self.slope_window = slope_window
        self.volume_short_window = volume_short_window
        self.volume_long_window = volume_long_window

        # Cumulative state — reset each session
        self._cum_tp_vol: float = 0.0  # Sum(TP * Volume)
        self._cum_vol: float = 0.0  # Sum(Volume)
        self._cum_tp2_vol: float = 0.0  # Sum(TP^2 * Volume) for variance
        self._bar_count: int = 0
        self._vwap_history: List[float] = []  # For slope calculation
        self._volume_history: List[float] = []  # For volume profile

    def reset(self) -> None:
        """Reset all state for a new trading session. Call at session open."""
        self._cum_tp_vol = 0.0
        self._cum_vol = 0.0
        self._cum_tp2_vol = 0.0
        self._bar_count = 0
        self._vwap_history.clear()
        self._volume_history.clear()

    def update(self, bar: VWAPBar) -> Optional[VWAPResult]:
        """Process a new bar and return updated VWAP with bands.

        Args:
            bar: VWAPBar with high, low, close, volume.

        Returns:
            VWAPResult or None if insufficient data (zero cumulative volume).
        """
        typical_price = (bar.high + bar.low + bar.close) / 3.0

        # Accumulate
        self._cum_tp_vol += typical_price * bar.volume
        self._cum_vol += bar.volume
        self._cum_tp2_vol += (typical_price ** 2) * bar.volume
        self._bar_count += 1
        self._volume_history.append(bar.volume)

        # H61: zero-division guard
        if self._cum_vol <= 0.0:
            return None

        # VWAP
        vwap = self._cum_tp_vol / self._cum_vol

        # Standard deviation of typical prices around VWAP (volume-weighted)
        # Var = Sum(TP^2 * V) / Sum(V) - VWAP^2
        variance = (self._cum_tp2_vol / self._cum_vol) - (vwap ** 2)
        # Guard against floating-point negative variance
        std_dev = math.sqrt(max(variance, 0.0))

        # Bands
        s1, s2, s3 = self.sigma_levels
        upper_1 = vwap + s1 * std_dev
        lower_1 = vwap - s1 * std_dev
        upper_2 = vwap + s2 * std_dev
        lower_2 = vwap - s2 * std_dev
        upper_3 = vwap + s3 * std_dev
        lower_3 = vwap - s3 * std_dev

        # Track VWAP history for slope
        self._vwap_history.append(vwap)

        # Slope: rate of change of VWAP over last slope_window bars
        slope = self._calculate_slope()

        # Sigma position: how many sigmas current price is from VWAP
        sigma_position = 0.0
        if std_dev > 1e-12:  # H61
            sigma_position = (bar.close - vwap) / std_dev

        # Volume profile
        volume_profile = self._classify_volume_profile()

        return VWAPResult(
            vwap=vwap,
            upper_1=upper_1,
            lower_1=lower_1,
            upper_2=upper_2,
            lower_2=lower_2,
            upper_3=upper_3,
            lower_3=lower_3,
            slope=slope,
            sigma_position=sigma_position,
            volume_profile=volume_profile,
        )

    def _calculate_slope(self) -> float:
        """Calculate VWAP slope (rate of change) over last slope_window bars.

        Returns VWAP change per bar. Positive = rising, negative = falling.
        """
        history = self._vwap_history
        n = len(history)
        if n < 2:
            return 0.0

        window = min(self.slope_window, n)
        recent = history[-window:]

        # Simple linear regression slope over the window
        w = len(recent)
        x_mean = (w - 1.0) / 2.0
        y_mean = sum(recent) / w

        cov = 0.0
        var_x = 0.0
        for i, y in enumerate(recent):
            dx = i - x_mean
            cov += dx * (y - y_mean)
            var_x += dx * dx

        if var_x <= 0.0:  # H61
            return 0.0

        return cov / var_x

    def _classify_volume_profile(self) -> str:
        """Classify volume as declining, accelerating, or neutral.

        Compares average volume of the last volume_short_window bars
        to the average of the last volume_long_window bars.

        - accelerating: recent avg > 1.2x long avg
        - declining: recent avg < 0.8x long avg
        - neutral: otherwise
        """
        history = self._volume_history
        n = len(history)

        if n < self.volume_short_window:
            return "neutral"

        short_avg = sum(history[-self.volume_short_window :]) / self.volume_short_window

        long_window = min(self.volume_long_window, n)
        long_avg = sum(history[-long_window:]) / long_window

        if long_avg <= 0.0:  # H61
            return "neutral"

        ratio = short_avg / long_avg

        if ratio > 1.2:
            return "accelerating"
        elif ratio < 0.8:
            return "declining"
        else:
            return "neutral"


def is_at_sigma_band(
    sigma_position: float,
    target_sigma: float,
    tolerance: float = 0.15,
) -> bool:
    """Check if current price is at or beyond a given sigma band.

    Args:
        sigma_position: Current price distance from VWAP in sigma units.
        target_sigma: The sigma level to check (e.g. 2.0 for 2-sigma band).
        tolerance: How close to the band counts as "at" (default 0.15 sigma).

    Returns:
        True if |sigma_position| >= target_sigma - tolerance.
    """
    return abs(sigma_position) >= (target_sigma - tolerance)


def is_beyond_sigma_band(sigma_position: float, target_sigma: float) -> bool:
    """Check if current price is strictly beyond a given sigma band.

    Args:
        sigma_position: Current price distance from VWAP in sigma units.
        target_sigma: The sigma level to check.

    Returns:
        True if |sigma_position| > target_sigma.
    """
    return abs(sigma_position) > target_sigma
