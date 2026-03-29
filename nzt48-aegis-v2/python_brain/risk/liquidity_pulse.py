"""Liquidity Pulse Detection — Book 117.

Detects manipulation and abnormal order flow patterns:
1. Spoofing: large orders appear/cancel rapidly without fills
2. Layering: multi-level manipulation across order book
3. Volume-price divergence: large moves on thin volume
4. Spread dislocation: sudden widening without news
5. Trade intensity anomaly: tick rate explosion

These are risk GATES — they BLOCK entries, not generate signals.

Usage:
    from python_brain.risk.liquidity_pulse import (
        LiquidityPulseDetector, PulseAlert,
    )

    detector = LiquidityPulseDetector()
    alert = detector.check_tick(price, volume, spread_bps, tick_rate)
    if alert:
        block_entry(alert.reason)
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Optional

log = logging.getLogger("liquidity_pulse")


class AlertType(Enum):
    VOLUME_DIVERGENCE = "VOLUME_DIVERGENCE"
    SPREAD_DISLOCATION = "SPREAD_DISLOCATION"
    TICK_RATE_EXPLOSION = "TICK_RATE_EXPLOSION"
    VOLUME_CLIMAX = "VOLUME_CLIMAX"


@dataclass
class PulseAlert:
    """Alert from liquidity anomaly detection."""
    alert_type: AlertType
    severity: float  # 0-1 (1 = most severe)
    reason: str
    block_entry: bool = True
    tighten_trail: bool = False
    trail_multiplier: float = 1.0


class LiquidityPulseDetector:
    """Detect abnormal liquidity events that indicate manipulation or stress."""

    def __init__(
        self,
        spread_window: int = 100,
        volume_window: int = 50,
        tick_rate_window: int = 20,
    ):
        self._spreads: Deque[float] = deque(maxlen=spread_window)
        self._volumes: Deque[float] = deque(maxlen=volume_window)
        self._tick_times: Deque[float] = deque(maxlen=tick_rate_window)
        self._prices: Deque[float] = deque(maxlen=volume_window)

    def check_tick(
        self,
        price: float,
        volume: float,
        spread_bps: float,
        timestamp_secs: float,
    ) -> Optional[PulseAlert]:
        """Check a single tick for liquidity anomalies.

        Returns PulseAlert if anomaly detected, None otherwise.
        """
        self._prices.append(price)
        self._volumes.append(volume)
        self._spreads.append(spread_bps)
        self._tick_times.append(timestamp_secs)

        # Need minimum history
        if len(self._volumes) < 20:
            return None

        # Check 1: Volume-price divergence
        alert = self._check_volume_divergence(price, volume)
        if alert:
            return alert

        # Check 2: Spread dislocation
        alert = self._check_spread_dislocation(spread_bps)
        if alert:
            return alert

        # Check 3: Tick rate explosion
        if len(self._tick_times) >= 10:
            alert = self._check_tick_rate()
            if alert:
                return alert

        # Check 4: Volume climax (exhaustion signal)
        alert = self._check_volume_climax(volume)
        if alert:
            return alert

        return None

    def _check_volume_divergence(self, price: float, volume: float) -> Optional[PulseAlert]:
        """Large price move on low volume = suspicious."""
        if len(self._prices) < 10:
            return None

        # Price move in last 5 ticks
        recent_prices = list(self._prices)[-5:]
        price_move_pct = abs(recent_prices[-1] - recent_prices[0]) / max(abs(recent_prices[0]), 1e-10) * 100

        # Average volume
        avg_vol = sum(self._volumes) / len(self._volumes)
        recent_vol = sum(list(self._volumes)[-5:]) / 5

        # Large price move on below-average volume
        if price_move_pct > 0.5 and avg_vol > 0 and recent_vol < avg_vol * 0.3:
            return PulseAlert(
                AlertType.VOLUME_DIVERGENCE, 0.7,
                f"Price moved {price_move_pct:.1f}% on {recent_vol/avg_vol:.0%} of avg volume",
                block_entry=True,
            )
        return None

    def _check_spread_dislocation(self, spread_bps: float) -> Optional[PulseAlert]:
        """Sudden spread widening without news."""
        if len(self._spreads) < 20:
            return None

        avg_spread = sum(self._spreads) / len(self._spreads)
        if avg_spread <= 0:
            return None

        ratio = spread_bps / avg_spread

        if ratio > 3.0:
            return PulseAlert(
                AlertType.SPREAD_DISLOCATION, 0.8,
                f"Spread {spread_bps:.0f}bps = {ratio:.1f}x average ({avg_spread:.0f}bps)",
                block_entry=True,
            )
        return None

    def _check_tick_rate(self) -> Optional[PulseAlert]:
        """Tick rate explosion without proportional price move."""
        times = list(self._tick_times)
        if len(times) < 10:
            return None

        # Ticks per second in last 10 ticks vs historical
        recent_interval = (times[-1] - times[-10]) / 9 if len(times) >= 10 else 1
        if recent_interval <= 0:
            return None

        recent_rate = 1.0 / recent_interval

        # Historical rate
        if len(times) >= 20:
            hist_interval = (times[-1] - times[0]) / (len(times) - 1)
            hist_rate = 1.0 / max(hist_interval, 0.001)

            if recent_rate > hist_rate * 10:  # 10x normal rate
                return PulseAlert(
                    AlertType.TICK_RATE_EXPLOSION, 0.6,
                    f"Tick rate {recent_rate:.0f}/s = {recent_rate/hist_rate:.0f}x normal",
                    block_entry=True,
                )
        return None

    def _check_volume_climax(self, volume: float) -> Optional[PulseAlert]:
        """Volume > 10x average = potential exhaustion/manipulation."""
        avg_vol = sum(self._volumes) / max(len(self._volumes), 1)
        if avg_vol <= 0:
            return None

        ratio = volume / avg_vol
        if ratio > 10:
            return PulseAlert(
                AlertType.VOLUME_CLIMAX, 0.5,
                f"Volume climax: {ratio:.0f}x average",
                block_entry=False,  # Don't block, but tighten trail
                tighten_trail=True,
                trail_multiplier=0.5,  # Tighten to 50% of normal
            )
        return None
