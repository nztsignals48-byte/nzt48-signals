"""
NZT-48 Trading System — Herding Behaviour Detection Engine
Sprint 2, Feature #15 from research doc.

Tracks herding intensity across the 18-stock universe using multiple
proxies: short interest direction consensus, price direction consensus,
and RVOL consensus. Returns a 0-100 herding intensity score and
contrarian trading signals when extreme herding is detected.

Extreme bullish herding (>80) suggests overcrowding and mean reversion risk.
Extreme bearish herding (<20 inverted, i.e. >80 bearish consensus) suggests
panic exhaustion and potential reversal opportunity.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("nzt48.herding_detector")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum observations stored per ticker
_MAX_OBSERVATIONS = 100

# Herding intensity thresholds
_EXTREME_BULLISH_THRESHOLD = 80
_EXTREME_BEARISH_THRESHOLD = 20
_NEUTRAL_UPPER = 60
_NEUTRAL_LOWER = 40

# 18-stock universe (semiconductors + AI infrastructure + broad market)
UNIVERSE = [
    "NVDA", "AMD", "MU", "SNDK", "AVGO", "MRVL", "ARM", "TSM",
    "ASML", "SMCI", "VRT", "TSLA",
    "QQQ", "SPY", "SMH", "SOXX",
    "INTC", "QCOM",
]

# Sector groupings for sector-level consensus analysis
SECTOR_MAP: dict[str, str] = {
    "NVDA": "semiconductors", "AMD": "semiconductors", "AVGO": "semiconductors",
    "MRVL": "semiconductors", "ARM": "semiconductors", "TSM": "semiconductors",
    "ASML": "semiconductor_equip", "INTC": "semiconductors", "QCOM": "semiconductors",
    "MU": "memory", "SNDK": "memory",
    "SMCI": "ai_infrastructure", "VRT": "ai_infrastructure",
    "TSLA": "ev_auto",
    "QQQ": "broad_market", "SPY": "broad_market",
    "SMH": "semiconductors", "SOXX": "semiconductors",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class _ShortInterestEntry:
    """Single short interest observation."""
    short_interest_pct: float
    date: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class _PriceEntry:
    """Single price observation for direction tracking."""
    price: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class _RvolEntry:
    """Single RVOL observation."""
    rvol: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# HerdingDetector
# ---------------------------------------------------------------------------

class HerdingDetector:
    """Tracks herding intensity across the 18-stock universe.

    Uses three proxies to measure herding:
    1. Short interest change direction consensus (all moving same way = high herding)
    2. Price direction consensus across sector (all semis moving together = herding)
    3. RVOL consensus (all spiking together = herding)

    The composite herding score is 0-100 where:
    - > 80 = extreme bullish herding (overcrowding, contrarian sell signal)
    - < 20 = extreme bearish herding (panic, contrarian buy signal)
    - 40-60 = neutral (no strong herding)
    """

    def __init__(self) -> None:
        # Short interest data per ticker (biweekly updates)
        self._short_interest: dict[str, deque[_ShortInterestEntry]] = {}

        # Price data per ticker (for direction consensus)
        self._prices: dict[str, deque[_PriceEntry]] = {}

        # RVOL data per ticker
        self._rvol: dict[str, deque[_RvolEntry]] = {}

        # Herding intensity history (for trend detection)
        self._herding_history: deque[tuple[float, datetime]] = deque(maxlen=_MAX_OBSERVATIONS)

        logger.info("HerdingDetector initialized for %d-stock universe", len(UNIVERSE))

    # ------------------------------------------------------------------
    # Public API: Data Ingestion
    # ------------------------------------------------------------------

    def update_short_interest(
        self, ticker: str, short_interest_pct: float, date: str,
    ) -> None:
        """Store biweekly short interest data for a ticker.

        Args:
            ticker: Stock ticker symbol.
            short_interest_pct: Short interest as percentage of float (e.g. 5.2 for 5.2%).
            date: Date string for the observation (e.g. "2025-03-15").
        """
        try:
            if ticker not in self._short_interest:
                self._short_interest[ticker] = deque(maxlen=_MAX_OBSERVATIONS)

            entry = _ShortInterestEntry(
                short_interest_pct=short_interest_pct,
                date=date,
            )
            self._short_interest[ticker].append(entry)
            logger.debug(
                "HERDING: short interest update %s=%.2f%% on %s",
                ticker, short_interest_pct, date,
            )
        except Exception:
            logger.exception("Failed to update short interest for %s", ticker)

    def update_price(self, ticker: str, price: float) -> None:
        """Store a price observation for direction consensus tracking.

        Args:
            ticker: Stock ticker symbol.
            price: Current price.
        """
        try:
            if ticker not in self._prices:
                self._prices[ticker] = deque(maxlen=_MAX_OBSERVATIONS)

            self._prices[ticker].append(_PriceEntry(price=price))
        except Exception:
            logger.exception("Failed to update price for %s", ticker)

    def update_rvol(self, ticker: str, rvol: float) -> None:
        """Store an RVOL observation for volume consensus tracking.

        Args:
            ticker: Stock ticker symbol.
            rvol: Relative volume (e.g. 2.5 = 2.5x average volume).
        """
        try:
            if ticker not in self._rvol:
                self._rvol[ticker] = deque(maxlen=_MAX_OBSERVATIONS)

            self._rvol[ticker].append(_RvolEntry(rvol=rvol))
        except Exception:
            logger.exception("Failed to update RVOL for %s", ticker)

    # ------------------------------------------------------------------
    # Public API: Herding Analysis
    # ------------------------------------------------------------------

    def compute_herding_intensity(self, ticker: str) -> float:
        """Compute composite herding intensity score for a ticker.

        Combines three proxies:
        - Short interest change direction consensus (weight: 30%)
        - Price direction consensus across sector (weight: 40%)
        - RVOL consensus (weight: 30%)

        Args:
            ticker: Stock ticker symbol.

        Returns:
            Herding intensity score 0-100. Higher = more bullish consensus,
            lower = more bearish consensus, 50 = balanced.
        """
        try:
            si_score = self._compute_short_interest_consensus(ticker)
            price_score = self._compute_price_direction_consensus(ticker)
            rvol_score = self._compute_rvol_consensus(ticker)

            # Weighted composite
            composite = (
                si_score * 0.30
                + price_score * 0.40
                + rvol_score * 0.30
            )

            # Clamp to 0-100
            composite = max(0.0, min(100.0, composite))

            # Store in history for trend detection
            self._herding_history.append((composite, datetime.now(timezone.utc)))

            logger.debug(
                "HERDING [%s]: SI=%.1f, price=%.1f, RVOL=%.1f -> composite=%.1f",
                ticker, si_score, price_score, rvol_score, composite,
            )

            return round(composite, 2)

        except Exception:
            logger.exception("Failed to compute herding intensity for %s", ticker)
            return 50.0  # Neutral default on error

    def detect_herding_extreme(self, intensity: float) -> str:
        """Classify herding intensity into regime labels.

        Args:
            intensity: Herding intensity score (0-100).

        Returns:
            One of: "EXTREME_BULLISH_HERD", "EXTREME_BEARISH_HERD",
            "MODERATE", "NEUTRAL".
        """
        try:
            if intensity > _EXTREME_BULLISH_THRESHOLD:
                return "EXTREME_BULLISH_HERD"
            if intensity < _EXTREME_BEARISH_THRESHOLD:
                return "EXTREME_BEARISH_HERD"
            if _NEUTRAL_LOWER <= intensity <= _NEUTRAL_UPPER:
                return "NEUTRAL"
            return "MODERATE"
        except Exception:
            logger.exception("Failed to detect herding extreme")
            return "NEUTRAL"

    def get_contrarian_signal(self, intensity: float) -> float:
        """Generate a contrarian signal from herding intensity.

        Extreme bullish herding suggests overcrowding and mean reversion risk,
        so the contrarian signal is negative (reduce/short).
        Extreme bearish herding suggests panic exhaustion,
        so the contrarian signal is positive (buy the dip).

        Args:
            intensity: Herding intensity score (0-100).

        Returns:
            Signal strength from -1.0 (strong sell) to +1.0 (strong buy).
            - Extreme bullish herding (>80) -> -0.5
            - Extreme bearish herding (<20) -> +0.5
            - Moderate herding: linearly interpolated between extremes
            - Neutral (40-60) -> 0.0
        """
        try:
            if intensity > _EXTREME_BULLISH_THRESHOLD:
                # Scale from -0.5 at 80 to -1.0 at 100
                excess = (intensity - _EXTREME_BULLISH_THRESHOLD) / (100.0 - _EXTREME_BULLISH_THRESHOLD)
                return round(-0.5 - excess * 0.5, 3)

            if intensity < _EXTREME_BEARISH_THRESHOLD:
                # Scale from +0.5 at 20 to +1.0 at 0
                excess = (_EXTREME_BEARISH_THRESHOLD - intensity) / _EXTREME_BEARISH_THRESHOLD
                return round(0.5 + excess * 0.5, 3)

            if _NEUTRAL_LOWER <= intensity <= _NEUTRAL_UPPER:
                return 0.0

            # Moderate zones: linear interpolation
            if intensity > _NEUTRAL_UPPER:
                # 60-80: scale from 0.0 to -0.5
                t = (intensity - _NEUTRAL_UPPER) / (_EXTREME_BULLISH_THRESHOLD - _NEUTRAL_UPPER)
                return round(-0.5 * t, 3)
            else:
                # 20-40: scale from +0.5 to 0.0
                t = (intensity - _EXTREME_BEARISH_THRESHOLD) / (_NEUTRAL_LOWER - _EXTREME_BEARISH_THRESHOLD)
                return round(0.5 * (1.0 - t), 3)

        except Exception:
            logger.exception("Failed to compute contrarian signal")
            return 0.0

    def get_herding_trend(self, window: int = 10) -> Optional[str]:
        """Detect the trend direction of herding intensity.

        Args:
            window: Number of recent observations to analyze.

        Returns:
            "INCREASING", "DECREASING", "STABLE", or None if insufficient data.
        """
        try:
            if len(self._herding_history) < max(3, window):
                return None

            recent = [h[0] for h in list(self._herding_history)[-window:]]

            # Simple linear regression slope
            n = len(recent)
            x_mean = (n - 1) / 2.0
            y_mean = sum(recent) / n
            numerator = sum((i - x_mean) * (recent[i] - y_mean) for i in range(n))
            denominator = sum((i - x_mean) ** 2 for i in range(n))

            if denominator == 0:
                return "STABLE"

            slope = numerator / denominator

            if slope > 1.0:
                return "INCREASING"
            elif slope < -1.0:
                return "DECREASING"
            else:
                return "STABLE"

        except Exception:
            logger.exception("Failed to compute herding trend")
            return None

    def get_status(self) -> dict:
        """Return current herding detector state for dashboard display.

        Returns:
            Dict with tracked tickers, observation counts, latest intensity,
            and trend information.
        """
        try:
            tickers_with_si = list(self._short_interest.keys())
            tickers_with_prices = list(self._prices.keys())
            tickers_with_rvol = list(self._rvol.keys())

            latest_intensity = None
            latest_label = None
            if self._herding_history:
                latest_intensity = self._herding_history[-1][0]
                latest_label = self.detect_herding_extreme(latest_intensity)

            return {
                "engine": "HerdingDetector",
                "universe_size": len(UNIVERSE),
                "tickers_with_short_interest": len(tickers_with_si),
                "tickers_with_prices": len(tickers_with_prices),
                "tickers_with_rvol": len(tickers_with_rvol),
                "herding_history_length": len(self._herding_history),
                "latest_herding_intensity": latest_intensity,
                "latest_herding_label": latest_label,
                "herding_trend": self.get_herding_trend(),
            }
        except Exception:
            logger.exception("Failed to get herding detector status")
            return {"engine": "HerdingDetector", "error": "status_failed"}

    # ------------------------------------------------------------------
    # Private: Consensus Computation
    # ------------------------------------------------------------------

    def _compute_short_interest_consensus(self, ticker: str) -> float:
        """Compute short interest direction consensus score.

        Measures whether short interest across the universe is moving
        in the same direction (all increasing or all decreasing).

        Returns 0-100:
        - 100 = all tickers' SI decreasing (bullish consensus — shorts covering)
        - 0 = all tickers' SI increasing (bearish consensus — shorts piling in)
        - 50 = mixed directions
        """
        try:
            increasing_count = 0
            decreasing_count = 0
            total_count = 0

            for t, si_deque in self._short_interest.items():
                if len(si_deque) < 2:
                    continue
                entries = list(si_deque)
                latest = entries[-1].short_interest_pct
                previous = entries[-2].short_interest_pct

                total_count += 1
                if latest > previous:
                    increasing_count += 1
                elif latest < previous:
                    decreasing_count += 1

            if total_count == 0:
                return 50.0  # No data = neutral

            # Decreasing SI = bullish (shorts covering), score toward 100
            # Increasing SI = bearish (shorts piling in), score toward 0
            bullish_ratio = decreasing_count / total_count
            return bullish_ratio * 100.0

        except Exception:
            logger.exception("Failed to compute SI consensus")
            return 50.0

    def _compute_price_direction_consensus(self, ticker: str) -> float:
        """Compute price direction consensus within the ticker's sector.

        Measures whether all tickers in the same sector are moving in
        the same direction (high consensus = herding).

        Returns 0-100:
        - 100 = all sector peers rising (bullish herd)
        - 0 = all sector peers falling (bearish herd)
        - 50 = mixed
        """
        try:
            sector = SECTOR_MAP.get(ticker, "other")
            sector_tickers = [t for t, s in SECTOR_MAP.items() if s == sector]

            rising_count = 0
            falling_count = 0
            total_count = 0

            for t in sector_tickers:
                if t not in self._prices or len(self._prices[t]) < 2:
                    continue

                entries = list(self._prices[t])
                latest = entries[-1].price
                previous = entries[-2].price

                total_count += 1
                if latest > previous:
                    rising_count += 1
                elif latest < previous:
                    falling_count += 1

            if total_count == 0:
                return 50.0

            bullish_ratio = rising_count / total_count
            return bullish_ratio * 100.0

        except Exception:
            logger.exception("Failed to compute price direction consensus")
            return 50.0

    def _compute_rvol_consensus(self, ticker: str) -> float:
        """Compute RVOL consensus (are all names spiking together?).

        High RVOL across the board combined with price direction gives
        the herding intensity.

        Returns 0-100:
        - High score when most tickers have elevated RVOL AND are moving
          in the same direction (bullish herding if rising)
        - Low score when most tickers have elevated RVOL AND are falling
          (bearish herding)
        - 50 when RVOL is not elevated or mixed
        """
        try:
            elevated_count = 0
            rising_with_vol = 0
            falling_with_vol = 0
            total_with_data = 0

            for t in UNIVERSE:
                if t not in self._rvol or len(self._rvol[t]) < 1:
                    continue

                latest_rvol = list(self._rvol[t])[-1].rvol
                total_with_data += 1

                if latest_rvol > 1.5:
                    elevated_count += 1

                    # Check price direction for this ticker
                    if t in self._prices and len(self._prices[t]) >= 2:
                        prices = list(self._prices[t])
                        if prices[-1].price > prices[-2].price:
                            rising_with_vol += 1
                        elif prices[-1].price < prices[-2].price:
                            falling_with_vol += 1

            if total_with_data == 0 or elevated_count == 0:
                return 50.0

            # Ratio of elevated RVOL tickers
            rvol_breadth = elevated_count / total_with_data

            # Direction consensus among elevated RVOL tickers
            total_directional = rising_with_vol + falling_with_vol
            if total_directional == 0:
                return 50.0

            bullish_vol_ratio = rising_with_vol / total_directional

            # Composite: breadth * direction
            # High breadth + bullish direction = high score (bullish herd)
            # High breadth + bearish direction = low score (bearish herd)
            consensus_score = 50.0 + (bullish_vol_ratio - 0.5) * 100.0 * rvol_breadth

            return max(0.0, min(100.0, consensus_score))

        except Exception:
            logger.exception("Failed to compute RVOL consensus")
            return 50.0


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def create_herding_detector() -> HerdingDetector:
    """Factory function for creating a HerdingDetector instance."""
    return HerdingDetector()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    detector = create_herding_detector()

    # Simulate short interest data
    for ticker in ["NVDA", "AMD", "AVGO", "MRVL", "TSM"]:
        detector.update_short_interest(ticker, 5.0, "2025-03-01")
        detector.update_short_interest(ticker, 4.0, "2025-03-15")  # All decreasing = bullish

    # Simulate price data
    for ticker in ["NVDA", "AMD", "AVGO", "MRVL", "TSM", "SMH"]:
        detector.update_price(ticker, 100.0)
        detector.update_price(ticker, 105.0)  # All rising = bullish herd

    # Simulate RVOL data
    for ticker in UNIVERSE[:10]:
        detector.update_rvol(ticker, 3.0)  # Elevated volume everywhere

    intensity = detector.compute_herding_intensity("NVDA")
    label = detector.detect_herding_extreme(intensity)
    signal = detector.get_contrarian_signal(intensity)

    print(f"\n--- Herding Analysis ---")
    print(f"  Intensity: {intensity}")
    print(f"  Label: {label}")
    print(f"  Contrarian signal: {signal}")
    print(f"  Trend: {detector.get_herding_trend()}")

    status = detector.get_status()
    print(f"\n--- Status ---")
    for k, v in status.items():
        print(f"  {k}: {v}")
