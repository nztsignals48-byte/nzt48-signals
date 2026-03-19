"""RSI(2) / IBS Combined Signal for mean-reversion entries.

Provides:
- RSI with configurable period (default 2 for Connors RSI-2 strategy)
- IBS (Internal Bar Strength) = (Close - Low) / (High - Low)
- Combined RSI(2)/IBS entry signal: both below configurable thresholds
- 200-day SMA trend filter (only buy when price > SMA-200)
- 5-day SMA exit signal (exit when price > SMA-5)

Reference: Connors & Alvarez, "Short Term Trading Strategies That Work" (2008).

PURE FUNCTION-style design. No I/O. No threading (H07).
Zero-division guards on ALL divisions (H61).
"""

from __future__ import annotations

from typing import List, Optional


def calculate_rsi(closes: List[float], period: int = 2) -> Optional[float]:
    """Calculate Relative Strength Index.

    Uses Wilder's smoothing method (exponential moving average of gains/losses).

    Args:
        closes: List of closing prices. Minimum period + 1 values required.
        period: RSI lookback period (default 2 for Connors RSI-2).

    Returns:
        RSI value in [0, 100], or None if insufficient data.
    """
    if len(closes) < period + 1 or period < 1:
        return None

    # Calculate price changes
    changes: List[float] = []
    for i in range(1, len(closes)):
        changes.append(closes[i] - closes[i - 1])

    # Initial average gain/loss over first 'period' changes
    gains = [max(c, 0.0) for c in changes[:period]]
    losses = [max(-c, 0.0) for c in changes[:period]]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # Wilder's smoothing for remaining changes
    for i in range(period, len(changes)):
        gain = max(changes[i], 0.0)
        loss = max(-changes[i], 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    # H61: zero-division guard
    if avg_loss <= 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def calculate_ibs(high: float, low: float, close: float) -> Optional[float]:
    """Calculate Internal Bar Strength.

    IBS = (Close - Low) / (High - Low)

    Low IBS (< 0.2) indicates a close near the low -- oversold intrabar.
    High IBS (> 0.8) indicates a close near the high -- overbought intrabar.

    Args:
        high: Bar high price.
        low: Bar low price.
        close: Bar close price.

    Returns:
        IBS in [0, 1], or None if high == low (doji bar, H61).
    """
    bar_range = high - low
    if bar_range <= 0.0:  # H61: zero-range bar (doji)
        return None
    ibs = (close - low) / bar_range
    # Clamp to [0, 1] for safety (close should be between low and high)
    return max(0.0, min(1.0, ibs))


def calculate_sma(values: List[float], period: int) -> Optional[float]:
    """Calculate Simple Moving Average.

    Args:
        values: List of values.
        period: SMA period.

    Returns:
        SMA value, or None if insufficient data.
    """
    if len(values) < period or period < 1:
        return None
    return sum(values[-period:]) / period


def combined_rsi_ibs_signal(
    closes: List[float],
    high: float,
    low: float,
    rsi_period: int = 2,
    rsi_threshold: float = 10.0,
    ibs_threshold: float = 0.2,
) -> bool:
    """Combined RSI(2)/IBS entry signal for mean-reversion.

    Fires when BOTH conditions are met:
    - RSI(period) < rsi_threshold (deeply oversold on momentum)
    - IBS < ibs_threshold (closed near session low)

    This combination catches extreme oversold conditions with high
    probability of short-term reversal.

    Args:
        closes: Price history for RSI calculation (minimum rsi_period + 1 values).
        high: Current bar high.
        low: Current bar low.
        rsi_period: RSI lookback (default 2).
        rsi_threshold: RSI must be below this (default 10.0).
        ibs_threshold: IBS must be below this (default 0.2).

    Returns:
        True if both RSI and IBS are below their thresholds.
    """
    rsi = calculate_rsi(closes, period=rsi_period)
    if rsi is None:
        return False

    close = closes[-1]
    ibs = calculate_ibs(high, low, close)
    if ibs is None:
        return False

    return rsi < rsi_threshold and ibs < ibs_threshold


def trend_filter_sma200(closes: List[float]) -> bool:
    """200-day SMA trend filter: only buy when price > SMA(200).

    Ensures mean-reversion trades align with the primary uptrend.
    Connors rule: RSI(2) entries only above the 200-day SMA.

    Args:
        closes: Price history (minimum 200 values).

    Returns:
        True if current price is above the 200-day SMA. False if insufficient data.
    """
    sma_200 = calculate_sma(closes, 200)
    if sma_200 is None:
        return False
    return closes[-1] > sma_200


def exit_signal_sma5(closes: List[float]) -> bool:
    """5-day SMA exit signal: exit when price rises above SMA(5).

    Connors rule: exit RSI(2) long when price crosses above the 5-day SMA.

    Args:
        closes: Price history (minimum 5 values).

    Returns:
        True if current close is above the 5-day SMA (exit signal).
        False if insufficient data.
    """
    sma_5 = calculate_sma(closes, 5)
    if sma_5 is None:
        return False
    return closes[-1] > sma_5


class RSIIBSStrategy:
    """Stateless RSI(2)/IBS strategy evaluator.

    Combines all signals into a single evaluation for entry/exit decisions.
    """

    def __init__(
        self,
        rsi_period: int = 2,
        rsi_threshold: float = 10.0,
        ibs_threshold: float = 0.2,
        sma_trend_period: int = 200,
        sma_exit_period: int = 5,
    ) -> None:
        """Initialize strategy parameters.

        Args:
            rsi_period: RSI lookback (default 2).
            rsi_threshold: RSI threshold for entry (default 10).
            ibs_threshold: IBS threshold for entry (default 0.2).
            sma_trend_period: SMA period for trend filter (default 200).
            sma_exit_period: SMA period for exit signal (default 5).
        """
        self.rsi_period = rsi_period
        self.rsi_threshold = rsi_threshold
        self.ibs_threshold = ibs_threshold
        self.sma_trend_period = sma_trend_period
        self.sma_exit_period = sma_exit_period

    def evaluate_entry(
        self,
        closes: List[float],
        high: float,
        low: float,
    ) -> dict:
        """Evaluate entry conditions.

        Args:
            closes: Historical closing prices (need >= 200 for full evaluation).
            high: Current bar high.
            low: Current bar low.

        Returns:
            dict with keys:
                entry_signal: bool -- True if RSI and IBS both triggered
                trend_aligned: bool -- True if price > SMA(200)
                rsi: Optional[float] -- Current RSI value
                ibs: Optional[float] -- Current IBS value
                sma_200: Optional[float] -- Current 200-day SMA
                qualified: bool -- True if entry_signal AND trend_aligned
        """
        rsi = calculate_rsi(closes, period=self.rsi_period)
        close = closes[-1] if closes else 0.0
        ibs = calculate_ibs(high, low, close)
        sma_200 = calculate_sma(closes, self.sma_trend_period)

        entry_signal = (
            rsi is not None
            and ibs is not None
            and rsi < self.rsi_threshold
            and ibs < self.ibs_threshold
        )

        trend_aligned = sma_200 is not None and close > sma_200

        return {
            "entry_signal": entry_signal,
            "trend_aligned": trend_aligned,
            "rsi": rsi,
            "ibs": ibs,
            "sma_200": sma_200,
            "qualified": entry_signal and trend_aligned,
        }

    def evaluate_exit(self, closes: List[float]) -> dict:
        """Evaluate exit conditions.

        Args:
            closes: Historical closing prices (need >= 5 for exit signal).

        Returns:
            dict with keys:
                exit_signal: bool -- True if price > SMA(5)
                sma_5: Optional[float] -- Current 5-day SMA
        """
        sma_5 = calculate_sma(closes, self.sma_exit_period)
        close = closes[-1] if closes else 0.0

        exit_signal = sma_5 is not None and close > sma_5

        return {
            "exit_signal": exit_signal,
            "sma_5": sma_5,
        }
