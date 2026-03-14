"""
NZT-48 V8.0 -- S15 Gate Diagnostics
======================================
Retroactive analysis of why S15 (2% Daily Target) did or did not fire.
Replays the gate logic on historical data to produce an auditable funnel.

Used by PDF 3 (Daily Review) to show:
  Universe size -> ATR gate pass -> RVOL gate pass -> RR gate pass
  -> Confidence gate pass -> Final signal count (0 or 1)

This is NOT used for live trading -- it's a diagnostic tool only.
Imports S15 constants from strategies/daily_target.py for consistency.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Path bootstrap so we can import from project root
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# S15 constants -- imported directly to guarantee parity with live strategy.
# Fall back to hard-coded values if the import fails (e.g. missing deps).
# ---------------------------------------------------------------------------
try:
    from strategies.daily_target import (
        _DAILY_TARGET_PCT,
        _MIN_RR_RATIO,
        _MIN_ATR_PCT,
        _MIN_RVOL,
        _STOP_ATR_MULT,
        _MAX_SIGNALS_PER_DAY,
        _MIN_CONFIDENCE,
        _W_ATR,
        _W_RVOL,
        _W_MOMENTUM,
        _W_EMA_ALIGN,
        _W_BB_POSITION,
        _W_TREND,
    )
except Exception:
    _DAILY_TARGET_PCT = 2.0
    _MIN_RR_RATIO = 1.5
    _MIN_ATR_PCT = 1.0
    _MIN_RVOL = 0.8
    _STOP_ATR_MULT = 1.0
    _MAX_SIGNALS_PER_DAY = 1
    _MIN_CONFIDENCE = 55.0
    _W_ATR = 0.30
    _W_RVOL = 0.15
    _W_MOMENTUM = 0.20
    _W_EMA_ALIGN = 0.15
    _W_BB_POSITION = 0.10
    _W_TREND = 0.10

logger = logging.getLogger("nzt48.gate_diagnostics")


@dataclass
class GateResult:
    """Per-ticker result after replaying all S15 entry gates."""

    ticker: str

    # Raw computed metrics
    atr_pct: float
    rvol: float
    rr_ratio: float
    confidence: float
    direction: str

    # Gate pass/fail flags
    passed_atr: bool
    passed_rvol: bool
    passed_rr: bool
    passed_confidence: bool

    # Convenience roll-up
    all_passed: bool

    # Human-readable rejection reason (empty string when all_passed=True)
    rejection_reason: str


@dataclass
class GateDiagnostics:
    """Full funnel summary for one analysis date."""

    universe_size: int
    failed_atr: list
    failed_rvol: list
    failed_rr: list
    failed_confidence: list
    passed_all: list

    best_candidate: Optional[str]
    best_confidence: float

    reason_no_signal: str
    gate_results: list
    analysis_date: str


class GateDiagnosticsEngine:
    """
    Replays the S15 entry gate logic against historical data.

    Usage
    -----
    engine = GateDiagnosticsEngine()
    result = engine.analyse(["QQQ3.L", "3LUS.L", ...])
    print(result.reason_no_signal)
    """

    _ATR_PERIOD = 14
    _RVOL_PERIOD = 20
    _RSI_PERIOD = 14
    _MACD_FAST = 12
    _MACD_SLOW = 26
    _MACD_SIGNAL = 9
    _EMA_SHORT = 9
    _EMA_MED = 20
    _EMA_LONG = 50
    _ADX_PERIOD = 14
    _BB_PERIOD = 20
    _BB_STD = 2.0
    _FETCH_DAYS = "60d"

    def __init__(self) -> None:
        self._cache: dict = {}

    def analyse(self, tickers, date_str=None):
        """
        Main entry point. Fetches data for all tickers, runs every S15
        gate, and returns a fully populated GateDiagnostics instance.
        """
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        logger.info(
            "GateDiagnosticsEngine.analyse: %d tickers for %s",
            len(tickers),
            date_str,
        )

        gate_results = []

        for ticker in tickers:
            try:
                stats = self._fetch_ticker_stats(ticker)
            except Exception as exc:
                logger.warning("Could not fetch stats for %s: %s", ticker, exc)
                continue

            if stats is None:
                continue

            atr_pct = stats["atr_pct"]
            rvol = stats["rvol"]
            confidence = stats["confidence"]
            rr_ratio = stats["rr_ratio"]
            direction = stats["direction"]

            passed_atr = atr_pct >= _MIN_ATR_PCT
            passed_rvol = rvol >= _MIN_RVOL
            passed_rr = rr_ratio >= _MIN_RR_RATIO
            passed_confidence = confidence >= _MIN_CONFIDENCE
            all_passed = passed_atr and passed_rvol and passed_rr and passed_confidence

            rejection_reason = self._build_rejection_reason(
                passed_atr, passed_rvol, passed_rr, passed_confidence,
                atr_pct, rvol, rr_ratio, confidence,
            )

            gate_results.append(
                GateResult(
                    ticker=ticker,
                    atr_pct=round(atr_pct, 3),
                    rvol=round(rvol, 3),
                    rr_ratio=round(rr_ratio, 3),
                    confidence=round(confidence, 2),
                    direction=direction,
                    passed_atr=passed_atr,
                    passed_rvol=passed_rvol,
                    passed_rr=passed_rr,
                    passed_confidence=passed_confidence,
                    all_passed=all_passed,
                    rejection_reason=rejection_reason,
                )
            )

        failed_atr = []
        failed_rvol = []
        failed_rr = []
        failed_confidence = []
        passed_all = []

        for gr in gate_results:
            if not gr.passed_atr:
                failed_atr.append(gr.ticker)
            elif not gr.passed_rvol:
                failed_rvol.append(gr.ticker)
            elif not gr.passed_rr:
                failed_rr.append(gr.ticker)
            elif not gr.passed_confidence:
                failed_confidence.append(gr.ticker)
            else:
                passed_all.append(gr.ticker)

        best_candidate = None
        best_confidence = 0.0
        if gate_results:
            best_gr = max(gate_results, key=lambda g: g.confidence)
            best_candidate = best_gr.ticker
            best_confidence = best_gr.confidence

        universe_size = len(gate_results)
        reason_no_signal = self._build_reason_no_signal(
            universe_size=universe_size,
            failed_atr=failed_atr,
            failed_rvol=failed_rvol,
            failed_rr=failed_rr,
            failed_confidence=failed_confidence,
            passed_all=passed_all,
            best_candidate=best_candidate,
            best_confidence=best_confidence,
        )

        return GateDiagnostics(
            universe_size=universe_size,
            failed_atr=failed_atr,
            failed_rvol=failed_rvol,
            failed_rr=failed_rr,
            failed_confidence=failed_confidence,
            passed_all=passed_all,
            best_candidate=best_candidate,
            best_confidence=round(best_confidence, 2),
            reason_no_signal=reason_no_signal,
            gate_results=gate_results,
            analysis_date=date_str,
        )

    def _fetch_ticker_stats(self, ticker):
        """
        Download OHLCV data for ticker and compute all metrics needed
        by the S15 gate logic.

        Returns dict with keys: atr_pct, rvol, rr_ratio, confidence, direction
        or None if insufficient data is available.
        """
        try:
            if ticker in self._cache:
                df = self._cache[ticker]
            else:
                raw = yf.download(
                    ticker,
                    period=self._FETCH_DAYS,
                    interval="1d",
                    auto_adjust=True,
                    progress=False,
                )
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.get_level_values(0)
                raw.columns = [c.capitalize() for c in raw.columns]
                raw.dropna(subset=["Close", "High", "Low", "Volume"], inplace=True)
                self._cache[ticker] = raw
                df = raw

            min_bars = self._MACD_SLOW + self._MACD_SIGNAL + 5
            if len(df) < min_bars:
                logger.debug("%s: only %d bars (need %d)", ticker, len(df), min_bars)
                return None

            atr_pct = self._compute_atr_pct(df)
            rvol = self._compute_rvol(df)
            rr_ratio = self._compute_rr_ratio(atr_pct)

            rsi = self._compute_rsi(df["Close"], self._RSI_PERIOD)
            macd_hist = self._compute_macd_hist(
                df["Close"], self._MACD_FAST, self._MACD_SLOW, self._MACD_SIGNAL
            )
            bb_pct_b = self._compute_bb_pct_b(df["Close"], self._BB_PERIOD, self._BB_STD)
            adx = self._compute_adx(df, self._ADX_PERIOD)

            confidence = self._compute_confidence(
                atr_pct=atr_pct,
                rvol=rvol,
                rsi=rsi,
                macd_hist=macd_hist,
                bb_pct_b=bb_pct_b,
                adx=adx,
            )

            direction = self._compute_direction(df, rsi, macd_hist)

            return {
                "atr_pct": atr_pct,
                "rvol": rvol,
                "rr_ratio": rr_ratio,
                "confidence": confidence,
                "direction": direction,
            }

        except Exception as exc:
            logger.error("_fetch_ticker_stats(%s) failed: %s", ticker, exc, exc_info=True)
            return None

    def _compute_atr_pct(self, df):
        """
        Average True Range as a percentage of the most recent close.
        True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
        """
        high = df["High"]
        low = df["Low"]
        close = df["Close"]
        prev_close = close.shift(1)

        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        atr = tr.rolling(window=self._ATR_PERIOD).mean()
        last_close = float(close.iloc[-1])
        last_atr = float(atr.iloc[-1])

        if last_close <= 0 or (last_atr != last_atr):  # nan check
            return 0.0

        return (last_atr / last_close) * 100.0

    def _compute_rvol(self, df):
        """
        Relative volume: today's volume vs 20-day average.
        1.0 = average; 1.5 = 50% above average.
        """
        volume = df["Volume"]
        if len(volume) < self._RVOL_PERIOD + 1:
            return 0.0

        avg_vol = float(volume.iloc[-(self._RVOL_PERIOD + 1):-1].mean())
        today_vol = float(volume.iloc[-1])

        if avg_vol <= 0 or (avg_vol != avg_vol) or (today_vol != today_vol):
            return 0.0

        return today_vol / avg_vol

    def _compute_rr_ratio(self, atr_pct, target_pct=None, stop_mult=None):
        """
        Risk:Reward = target_pct / (atr_pct * stop_mult).
        Uses S15 constants by default.
        """
        if target_pct is None:
            target_pct = _DAILY_TARGET_PCT
        if stop_mult is None:
            stop_mult = _STOP_ATR_MULT
        stop_pct = atr_pct * stop_mult
        if stop_pct <= 0.0:
            return 0.0
        return target_pct / stop_pct

    def _compute_confidence(self, atr_pct, rvol, rsi, macd_hist, bb_pct_b, adx):
        """
        Mirror of S15's composite confidence score (0-100 scale).

        Weights:
          _W_ATR=0.30, _W_RVOL=0.15, _W_MOMENTUM=0.20,
          _W_EMA_ALIGN=0.15, _W_BB_POSITION=0.10, _W_TREND=0.10
        """
        # 1. ATR score: ATR==2% -> 100
        atr_score = min(100.0, (atr_pct / 2.0) * 100.0) * _W_ATR

        # 2. RVOL score: RVOL==2.0 -> 100
        rvol_score = min(100.0, rvol * 50.0) * _W_RVOL

        # 3. Momentum score (RSI + MACD)
        if 40.0 <= rsi <= 70.0:
            rsi_component = 50.0 + (rsi - 40.0) / 30.0 * 50.0
        elif rsi > 70.0:
            rsi_component = max(0.0, 100.0 - (rsi - 70.0) * 3.0)
        else:
            rsi_component = max(0.0, rsi / 40.0 * 50.0)

        macd_component = 75.0 if macd_hist > 0 else 25.0
        momentum_raw = (rsi_component + macd_component) / 2.0
        momentum_score = min(100.0, momentum_raw) * _W_MOMENTUM

        # 4. EMA alignment proxy via RSI distance from 50
        ema_strength = min(abs(rsi - 50.0) / 25.0, 1.0)
        ema_score = ema_strength * 100.0 * _W_EMA_ALIGN

        # 5. BB position: lower half = more room to rally
        if 0.0 <= bb_pct_b <= 1.0:
            bb_room = 1.0 - bb_pct_b
        else:
            bb_room = 0.0
        bb_score = max(0.0, min(bb_room, 1.0)) * 100.0 * _W_BB_POSITION

        # 6. Trend strength via ADX
        adx_norm = min(adx / 50.0, 1.0) if adx > 0 else 0.0
        trend_score = adx_norm * 100.0 * _W_TREND

        confidence = atr_score + rvol_score + momentum_score + ema_score + bb_score + trend_score
        return max(0.0, min(100.0, confidence))

    def _compute_rsi(self, close, period=14):
        """Wilder RSI for the most recent bar."""
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)

        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

        last_gain = float(avg_gain.iloc[-1])
        last_loss = float(avg_loss.iloc[-1])

        if last_loss == 0:
            return 100.0
        rs = last_gain / last_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _compute_macd_hist(self, close, fast=12, slow=26, signal=9):
        """MACD histogram value for the most recent bar."""
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        val = float(histogram.iloc[-1])
        import math
        return val if not math.isnan(val) else 0.0

    def _compute_bb_pct_b(self, close, period=20, num_std=2.0):
        """
        Bollinger Band %B for the most recent bar.
        %B = (price - lower) / (upper - lower)
        """
        rolling_mean = close.rolling(window=period).mean()
        rolling_std = close.rolling(window=period).std(ddof=1)
        upper = rolling_mean + num_std * rolling_std
        lower = rolling_mean - num_std * rolling_std

        last_price = float(close.iloc[-1])
        last_upper = float(upper.iloc[-1])
        last_lower = float(lower.iloc[-1])

        band_width = last_upper - last_lower
        import math
        if band_width <= 0 or math.isnan(band_width):
            return 0.5

        pct_b = (last_price - last_lower) / band_width
        return max(-0.5, min(1.5, pct_b))

    def _compute_adx(self, df, period=14):
        """
        Wilder ADX for the most recent bar.
        ADX > 25 indicates a trending market.
        """
        try:
            import math
            high = df["High"]
            low = df["Low"]
            close = df["Close"]

            up_move = high.diff()
            down_move = (-low.diff())

            plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
            minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

            prev_close = close.shift(1)
            tr = pd.concat(
                [
                    high - low,
                    (high - prev_close).abs(),
                    (low - prev_close).abs(),
                ],
                axis=1,
            ).max(axis=1)

            atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
            smooth_plus = plus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
            smooth_minus = minus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

            safe_atr = atr.replace(0, float("nan"))
            plus_di = (smooth_plus / safe_atr) * 100.0
            minus_di = (smooth_minus / safe_atr) * 100.0

            di_sum = (plus_di + minus_di).replace(0, float("nan"))
            dx = ((plus_di - minus_di).abs() / di_sum) * 100.0
            adx_series = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

            val = float(adx_series.iloc[-1])
            return val if not math.isnan(val) else 0.0
        except Exception:
            return 0.0

    def _compute_direction(self, df, rsi, macd_hist):
        """
        Simplified direction: LONG / SHORT / NEUTRAL.
        Mirrors S15 vote-counting logic.
        """
        bullish = 0
        bearish = 0

        if rsi > 55:
            bullish += 1
        elif rsi < 45:
            bearish += 1

        if macd_hist > 0:
            bullish += 1
        elif macd_hist < 0:
            bearish += 1

        close = df["Close"]
        if len(close) > 20:
            ema9 = float(close.ewm(span=9, adjust=False).mean().iloc[-1])
            ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
            last_close = float(close.iloc[-1])
            if last_close > ema9 and ema9 > ema20:
                bullish += 1
            elif last_close < ema9 and ema9 < ema20:
                bearish += 1

        if bullish >= 2:
            return "LONG"
        if bearish >= 2:
            return "SHORT"
        return "NEUTRAL"

    @staticmethod
    def _build_rejection_reason(
        passed_atr,
        passed_rvol,
        passed_rr,
        passed_confidence,
        atr_pct,
        rvol,
        rr_ratio,
        confidence,
    ):
        """Return the first failing gate as a concise rejection string."""
        if not passed_atr:
            return f"ATR {atr_pct:.2f}% < {_MIN_ATR_PCT}% minimum"
        if not passed_rvol:
            return f"RVOL {rvol:.2f}x < {_MIN_RVOL}x minimum"
        if not passed_rr:
            return f"R:R {rr_ratio:.2f} < {_MIN_RR_RATIO} minimum"
        if not passed_confidence:
            return f"Confidence {confidence:.1f} < {_MIN_CONFIDENCE} minimum"
        return ""

    @staticmethod
    def _build_reason_no_signal(
        universe_size,
        failed_atr,
        failed_rvol,
        failed_rr,
        failed_confidence,
        passed_all,
        best_candidate,
        best_confidence,
    ):
        """
        Single plain-English sentence explaining the day's outcome.
        Priority: universe empty > ATR only > RVOL > RR > confidence > fired.
        """
        if universe_size == 0:
            return "No data available for analysis date"

        if passed_all:
            return f"SIGNAL FIRED: {best_candidate} with confidence {best_confidence:.1f}"

        n_passed_atr = universe_size - len(failed_atr)

        if n_passed_atr == 0:
            return (
                f"Low volatility day -- ATR below {_MIN_ATR_PCT}% minimum "
                f"for {len(failed_atr)} tickers"
            )

        if failed_rvol and not failed_rr and not failed_confidence:
            return (
                f"Volume confirmation missing -- RVOL below {_MIN_RVOL}x "
                f"for {len(failed_rvol)} tickers passing ATR gate"
            )

        n_passed_rvol = n_passed_atr - len(failed_rvol)
        if n_passed_rvol > 0 and failed_rr and not failed_confidence:
            return (
                f"Risk:Reward insufficient -- R:R below {_MIN_RR_RATIO} "
                f"for {len(failed_rr)} tickers "
                f"(stop too wide vs {_DAILY_TARGET_PCT}% target)"
            )

        if failed_confidence:
            return (
                f"Confidence below threshold -- best score {best_confidence:.1f} "
                f"vs {_MIN_CONFIDENCE} minimum"
            )

        # Mixed failures -- report dominant gate
        if len(failed_rvol) >= len(failed_rr) and len(failed_rvol) >= len(failed_confidence):
            return (
                f"Volume confirmation missing -- RVOL below {_MIN_RVOL}x "
                f"for {len(failed_rvol)} tickers passing ATR gate"
            )
        if len(failed_rr) >= len(failed_confidence):
            return (
                f"Risk:Reward insufficient -- R:R below {_MIN_RR_RATIO} "
                f"for {len(failed_rr)} tickers "
                f"(stop too wide vs {_DAILY_TARGET_PCT}% target)"
            )
        return (
            f"Confidence below threshold -- best score {best_confidence:.1f} "
            f"vs {_MIN_CONFIDENCE} minimum"
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_engine_instance = None


def get_engine():
    """Return the module-level singleton GateDiagnosticsEngine."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = GateDiagnosticsEngine()
    return _engine_instance
