"""
NZT-48 Trading System — Layer 1: Price Action Indicator Engine
Section 6: All 22 core indicators.

Accepts a pandas DataFrame with columns: Open, High, Low, Close, Volume.
Uses pandas_ta for standard TA calculations; manual implementations for
VWAP, RVOL, cumulative delta, speed of tape, opening range, and microstructure.

Every method handles missing data gracefully — returns 0/default on failure.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

sys.path.insert(0, str(Path(__file__).parent.parent))
from models import IndicatorSnapshot

logger = logging.getLogger(__name__)

# US market open in ET
_MARKET_OPEN = time(9, 30)


class IndicatorEngine:
    """Computes all 22 core indicators defined in the NZT-48 spec (Section 6).

    Usage:
        engine = IndicatorEngine()
        snapshot = engine.compute_all(df, ticker="AAPL", interval="1m")
    """

    # ------------------------------------------------------------------ #
    #  Master orchestrator                                                 #
    # ------------------------------------------------------------------ #

    def compute_all(
        self,
        df: pd.DataFrame,
        ticker: str,
        interval: str = "1m",
        df_weekly: Optional[pd.DataFrame] = None,
        df_underlying: Optional[pd.DataFrame] = None,
    ) -> IndicatorSnapshot:
        """Compute all 22 indicators and return a populated IndicatorSnapshot.

        AEGIS 0-03: When ``df_underlying`` is provided, momentum/trend
        indicators (RSI, EMA stack, MACD, ADX, Bollinger Bands, Keltner
        Channels, Stochastic RSI, EMA alignment) are computed on the
        underlying instrument's price data.  This avoids vol-drag-induced
        RSI compression, false EMA downtrends, and miscalibrated oscillator
        thresholds that occur when computing on leveraged ETP prices.

        VWAP, spread, and all volume-based indicators (OBV, RVOL, MFI,
        cumulative delta, speed of tape, volume spike) remain on ETP data
        because they reflect actual execution quality and market-maker activity.

        ATR is computed on ETP data (used for stop distances on actual
        execution prices, not underlying).

        Args:
            df: OHLCV DataFrame for the ETP (Open, High, Low, Close, Volume).
                Index should be DatetimeIndex in ET for time-of-day features.
            ticker: Symbol string (e.g. "QQQ3.L").
            interval: Bar interval -- "1m", "5m", "1d", etc.
            df_weekly: Optional weekly-bar DataFrame for the 10-week EMA.
            df_underlying: Optional OHLCV DataFrame for the underlying
                instrument (e.g. ^IXIC for QQQ3.L, NVDA for NVD3.L).
                When provided, momentum/trend indicators are computed on
                this data instead of the ETP.

        Returns:
            Fully populated IndicatorSnapshot dataclass.
        """
        if df is None or df.empty:
            logger.warning("compute_all called with empty DataFrame for %s", ticker)
            return IndicatorSnapshot(timestamp=datetime.now(timezone.utc), ticker=ticker)

        df = df.copy()

        # AEGIS 0-03: Select data source for momentum/trend indicators.
        # Use underlying data when available; fall back to ETP data.
        if df_underlying is not None and not df_underlying.empty:
            df_trend = df_underlying.copy()
            logger.debug(
                "AEGIS 0-03: computing trend indicators on underlying for %s (%d bars)",
                ticker, len(df_trend),
            )
        else:
            df_trend = df
            if df_underlying is not None:
                logger.debug(
                    "AEGIS 0-03: underlying df empty for %s, falling back to ETP data",
                    ticker,
                )

        snap = IndicatorSnapshot(
            timestamp=datetime.now(timezone.utc),
            ticker=ticker,
            price=float(df["Close"].iloc[-1]),
        )

        # --- 1. VWAP + bands (on ETP — reflects actual execution quality) ---
        try:
            vwap, v1u, v1l, v2u, v2l = self.calc_vwap(df)
            snap.vwap = vwap
            snap.vwap_upper_1s = v1u
            snap.vwap_lower_1s = v1l
            snap.vwap_upper_2s = v2u
            snap.vwap_lower_2s = v2l
        except Exception:
            logger.debug("VWAP calculation failed for %s", ticker, exc_info=True)

        # --- 2. EMAs (9, 20, 50) — on ETP (price-level dependent) ---
        try:
            e9, e20, e50 = self.calc_emas(df)
            snap.ema9 = e9
            snap.ema20 = e20
            snap.ema50 = e50
        except Exception:
            logger.debug("EMA calculation failed for %s", ticker, exc_info=True)

        # --- 3. 10-week EMA ---
        try:
            if df_weekly is not None and not df_weekly.empty:
                snap.ema10w = self.calc_ema_10week(df_weekly)
        except Exception:
            logger.debug("10-week EMA failed for %s", ticker, exc_info=True)

        # --- 4. RSI(14) — on UNDERLYING (AEGIS 0-03) ---
        try:
            snap.rsi14 = self.calc_rsi(df_trend, period=14)
        except Exception:
            logger.debug("RSI failed for %s", ticker, exc_info=True)

        # --- 5. MACD — on UNDERLYING (AEGIS 0-03) ---
        try:
            ml, ms, mh = self.calc_macd(df_trend)
            snap.macd_line = ml
            snap.macd_signal = ms
            snap.macd_histogram = mh
        except Exception:
            logger.debug("MACD failed for %s", ticker, exc_info=True)

        # --- 6. ATR (on ETP — used for stop distances on actual prices) ---
        try:
            atr_val, atr_pct = self.calc_atr(df, period=14)
            snap.atr14 = atr_val
            snap.atr_pct = atr_pct
        except Exception:
            logger.debug("ATR failed for %s", ticker, exc_info=True)

        # --- 7. RVOL (time-adjusted) ---
        try:
            snap.rvol = self.calc_rvol(df, ticker)
        except Exception:
            logger.debug("RVOL failed for %s", ticker, exc_info=True)

        # --- 8. Volume spike ---
        try:
            snap.volume_spike = self.calc_volume_spike(df)
        except Exception:
            logger.debug("Volume spike failed for %s", ticker, exc_info=True)

        # --- 9. Dollar volume ---
        try:
            snap.dollar_volume = self.calc_dollar_volume(df)
        except Exception:
            logger.debug("Dollar volume failed for %s", ticker, exc_info=True)

        # --- 10. Opening Range ---
        try:
            or5h, or5l, or15h, or15l = self.calc_opening_range(df)
            snap.or_high_5m = or5h
            snap.or_low_5m = or5l
            snap.or_high_15m = or15h
            snap.or_low_15m = or15l
        except Exception:
            logger.debug("Opening range failed for %s", ticker, exc_info=True)

        # --- 11. Bid-ask spread proxy ---
        try:
            snap.bid_ask_spread = self.calc_bid_ask_spread(df)
        except Exception:
            logger.debug("Bid-ask spread failed for %s", ticker, exc_info=True)

        # --- 12. Microstructure score ---
        try:
            snap.microstructure_score = self.calc_microstructure_score(df)
        except Exception:
            logger.debug("Microstructure score failed for %s", ticker, exc_info=True)

        # --- 13. Bollinger Bands — on ETP (price-level dependent) ---
        try:
            bbu, bbm, bbl = self.calc_bollinger_bands(df, period=20, std=2)
            snap.bb_upper = bbu
            snap.bb_middle = bbm
            snap.bb_lower = bbl
        except Exception:
            logger.debug("Bollinger Bands failed for %s", ticker, exc_info=True)

        # --- 14. ADX — on UNDERLYING (AEGIS 0-03) ---
        try:
            snap.adx14 = self.calc_adx(df_trend, period=14)
        except Exception:
            logger.debug("ADX failed for %s", ticker, exc_info=True)

        # --- 15. Stochastic RSI — on UNDERLYING (AEGIS 0-03) ---
        try:
            snap.stochastic_rsi = self.calc_stochastic_rsi(df_trend, period=14)
        except Exception:
            logger.debug("Stoch RSI failed for %s", ticker, exc_info=True)

        # --- 16. Keltner Channels — on ETP (price-level dependent) ---
        try:
            kcu, kcl = self.calc_keltner_channels(df, period=20, atr_mult=1.5)
            snap.keltner_upper = kcu
            snap.keltner_lower = kcl
        except Exception:
            logger.debug("Keltner failed for %s", ticker, exc_info=True)

        # --- 17. OBV ---
        try:
            snap.obv = self.calc_obv(df)
        except Exception:
            logger.debug("OBV failed for %s", ticker, exc_info=True)

        # --- 18. MFI ---
        try:
            snap.mfi14 = self.calc_mfi(df, period=14)
        except Exception:
            logger.debug("MFI failed for %s", ticker, exc_info=True)

        # --- 19. Cumulative Delta ---
        try:
            snap.cumulative_delta = self.calc_cumulative_delta(df)
        except Exception:
            logger.debug("Cumulative delta failed for %s", ticker, exc_info=True)

        # --- 21. CVD Divergence Detection ---
        try:
            cvd_div = self.detect_cvd_divergence(df)
            snap.cvd_bearish_div = cvd_div["bearish_divergence"]
            snap.cvd_bullish_div = cvd_div["bullish_divergence"]
            snap.absorption_detected = cvd_div["absorption"]
        except Exception:
            logger.debug("CVD divergence failed for %s", ticker, exc_info=True)

        # --- 22. Initial Balance (Auction Market Theory) ---
        try:
            ib_h, ib_l, ib_r, ib_ext, _ = self.compute_initial_balance(df)
            snap.ib_high = ib_h
            snap.ib_low = ib_l
            snap.ib_range = ib_r
            snap.ib_extension_pct = ib_ext
        except Exception:
            logger.debug("Initial Balance failed for %s", ticker, exc_info=True)

        # --- 20. Speed of Tape ---
        try:
            snap.speed_of_tape = self.calc_speed_of_tape(df)
        except Exception:
            logger.debug("Speed of tape failed for %s", ticker, exc_info=True)

        # --- EMA alignment score — on UNDERLYING (AEGIS 0-03) ---
        try:
            snap.ema_alignment = self.calc_ema_alignment(df_trend)
        except Exception:
            logger.debug("EMA alignment failed for %s", ticker, exc_info=True)

        # --- Squeeze detection (pattern) ---
        try:
            if snap.bb_upper and snap.keltner_upper:
                squeeze = self.detect_squeeze(
                    snap.bb_upper, snap.bb_lower,
                    snap.keltner_upper, snap.keltner_lower,
                )
                if squeeze:
                    snap.patterns_detected.append("SQUEEZE")
        except Exception:
            logger.debug("Squeeze detection failed for %s", ticker, exc_info=True)

        # --- Capital Gains Overhang (Disposition Effect) ---
        try:
            snap.capital_gains_overhang = self.compute_capital_gains_overhang(df, lookback=60)
        except Exception:
            logger.debug("Capital gains overhang failed for %s", ticker, exc_info=True)

        # --- T-05: ROC(30) — 30-bar Rate of Change — on UNDERLYING (AEGIS 0-03) ---
        # 30-minute price change on 1-min bars (not 5-min which is pure noise on leveraged ETPs)
        try:
            if len(df_trend) >= 31:
                close_now = float(df_trend["Close"].iloc[-1])
                close_30ago = float(df_trend["Close"].iloc[-31])
                if close_30ago > 0:
                    snap.roc_30 = ((close_now - close_30ago) / close_30ago) * 100
        except Exception:
            logger.debug("ROC(30) failed for %s", ticker, exc_info=True)

        # --- T-06: ADX Delta (trend acceleration) — on UNDERLYING (AEGIS 0-03) ---
        # ADX rising > 2 pts/bar = emerging trend; uses same ADX series as calc_adx
        try:
            if len(df_trend) >= 30:
                adx_df = ta.adx(df_trend["High"], df_trend["Low"], df_trend["Close"], length=14)
                if adx_df is not None and not adx_df.empty:
                    adx_col = [c for c in adx_df.columns if c.startswith("ADX_")]
                    if adx_col and len(adx_df[adx_col[0]]) >= 2:
                        adx_cur = float(adx_df[adx_col[0]].iloc[-1])
                        adx_prev = float(adx_df[adx_col[0]].iloc[-2])
                        if not (np.isnan(adx_cur) or np.isnan(adx_prev)):
                            snap.adx_delta = adx_cur - adx_prev
        except Exception:
            logger.debug("ADX delta failed for %s", ticker, exc_info=True)

        # --- T-07: RVOL Trajectory (volume acceleration) ---
        # current_rvol / mean(last 3 bars' volume relative to 20-bar avg)
        try:
            if len(df) >= 24 and "Volume" in df.columns and snap.rvol is not None:
                vol_20_mean = df["Volume"].rolling(20).mean()
                if vol_20_mean is not None and len(vol_20_mean) >= 4:
                    avg_20 = float(vol_20_mean.iloc[-1])
                    if avg_20 > 0:
                        # RVOL for last 3 bars
                        recent_rvols = []
                        for offset in [-4, -3, -2]:
                            bar_vol = float(df["Volume"].iloc[offset])
                            bar_avg = float(vol_20_mean.iloc[offset])
                            if bar_avg > 0:
                                recent_rvols.append(bar_vol / bar_avg)
                        if len(recent_rvols) == 3:
                            mean_recent = sum(recent_rvols) / 3
                            if mean_recent > 0:
                                snap.rvol_trajectory = snap.rvol / mean_recent
        except Exception:
            logger.debug("RVOL trajectory failed for %s", ticker, exc_info=True)

        # --- Q1: MACD Divergence Detection ---
        try:
            from core.indicator_enhancements import IndicatorEnhancements
            enhancer = IndicatorEnhancements()
            macd_div = enhancer.detect_macd_divergence(df_trend, lookback=20)
            snap.macd_bearish_div = macd_div["bearish_divergence"]
            snap.macd_bullish_div = macd_div["bullish_divergence"]
            snap.macd_div_strength = macd_div["divergence_strength"]
        except Exception:
            logger.debug("MACD divergence failed for %s", ticker, exc_info=True)

        # --- Q1: Vol_MA50 (50-bar volume MA) ---
        try:
            if len(df) >= 50:
                snap.vol_ma50 = float(df["Volume"].iloc[-50:].mean())
        except Exception:
            logger.debug("Vol_MA50 failed for %s", ticker, exc_info=True)

        # --- Q1: Volume Acceleration (vol_ma20 > vol_ma50) ---
        try:
            if len(df) >= 50:
                vol_ma20 = float(df["Volume"].iloc[-20:].mean())
                vol_ma50 = snap.vol_ma50
                if vol_ma20 > 0 and vol_ma50 > 0:
                    snap.vol_acceleration = vol_ma20 > vol_ma50
        except Exception:
            logger.debug("Volume acceleration failed for %s", ticker, exc_info=True)

        # --- Q1: Price Action Filter (close > open) ---
        try:
            last_bar = df.iloc[-1]
            close = float(last_bar["Close"])
            open_price = float(last_bar["Open"])
            snap.price_action_bullish = close > open_price
        except Exception:
            logger.debug("Price action filter failed for %s", ticker, exc_info=True)

        # --- Q1: Dynamic Bollinger Bands (regime-adaptive) ---
        try:
            from core.indicator_enhancements import IndicatorEnhancements
            enhancer = IndicatorEnhancements()
            # Determine regime from VIX (if available from cross-asset macro)
            regime = "neutral"  # Default
            # TODO: wire in VIX from cross_asset_macro if available
            bb_dyn = enhancer.calc_dynamic_bollinger_bands(df, period=20, regime=regime)
            snap.bb_dynamic_upper, snap.bb_dynamic_middle, snap.bb_dynamic_lower = bb_dyn
        except Exception:
            logger.debug("Dynamic Bollinger Bands failed for %s", ticker, exc_info=True)

        return snap

    # ------------------------------------------------------------------ #
    #  1. VWAP + bands (manual calculation)                                #
    # ------------------------------------------------------------------ #

    def calc_vwap(
        self, df: pd.DataFrame
    ) -> tuple[float, float, float, float, float]:
        """Calculate VWAP with 1-sigma and 2-sigma bands.

        Institutional fair value anchor.  Above VWAP = buyers control.

        Returns:
            (vwap, upper_1s, lower_1s, upper_2s, lower_2s)
        """
        typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
        tp_vol = typical_price * df["Volume"]

        cum_tp_vol = tp_vol.cumsum()
        cum_vol = df["Volume"].cumsum()

        # Avoid division by zero
        cum_vol_safe = cum_vol.replace(0, np.nan)
        vwap_series = cum_tp_vol / cum_vol_safe

        # Standard deviation bands: cumulative variance of (TP - VWAP)
        deviation = typical_price - vwap_series
        cum_dev_sq = (deviation ** 2 * df["Volume"]).cumsum()
        variance = cum_dev_sq / cum_vol_safe
        std_dev = np.sqrt(variance)

        # T-11: Cap band width to prevent late-session explosion.
        # Cumulative std grows unbounded; cap at 2.5× rolling price std.
        _rolling_price_std = df["Close"].rolling(window=20, min_periods=1).std()
        _max_band_width = _rolling_price_std * 2.5
        std_dev = np.minimum(std_dev, _max_band_width.fillna(std_dev))

        vwap_val = float(vwap_series.iloc[-1]) if not vwap_series.empty else 0.0
        std_val = float(std_dev.iloc[-1]) if not std_dev.empty else 0.0

        if np.isnan(vwap_val):
            vwap_val = 0.0
        if np.isnan(std_val):
            std_val = 0.0

        return (
            vwap_val,
            vwap_val + std_val,       # +1σ
            vwap_val - std_val,       # -1σ
            vwap_val + 2 * std_val,   # +2σ
            vwap_val - 2 * std_val,   # -2σ
        )

    # ------------------------------------------------------------------ #
    #  2. EMAs (9, 20, 50)                                                 #
    # ------------------------------------------------------------------ #

    def calc_emas(self, df: pd.DataFrame) -> tuple[float, float, float]:
        """EMA(9), EMA(20), EMA(50) on the close series.

        Spec: EMA9 on 1-min, EMA20 on 1-min, EMA50 on 5-min.
        When called on a single DataFrame these are all computed
        on the provided bars; the caller is responsible for passing
        the correct timeframe data.

        Returns:
            (ema9, ema20, ema50)
        """
        close = df["Close"]

        ema9_s = ta.ema(close, length=9)
        ema20_s = ta.ema(close, length=20)
        ema50_s = ta.ema(close, length=50)

        ema9 = float(ema9_s.iloc[-1]) if ema9_s is not None and not ema9_s.empty else 0.0
        ema20 = float(ema20_s.iloc[-1]) if ema20_s is not None and not ema20_s.empty else 0.0
        ema50 = float(ema50_s.iloc[-1]) if ema50_s is not None and not ema50_s.empty else 0.0

        return (
            ema9 if not np.isnan(ema9) else 0.0,
            ema20 if not np.isnan(ema20) else 0.0,
            ema50 if not np.isnan(ema50) else 0.0,
        )

    # ------------------------------------------------------------------ #
    #  3. 10-week EMA (swing/compound)                                     #
    # ------------------------------------------------------------------ #

    def calc_ema_10week(self, df_weekly: pd.DataFrame) -> float:
        """10-week EMA for swing and compound strategies.

        Args:
            df_weekly: Weekly-bar OHLCV DataFrame.

        Returns:
            Current 10-week EMA value.
        """
        if df_weekly is None or df_weekly.empty:
            return 0.0

        ema_s = ta.ema(df_weekly["Close"], length=10)
        if ema_s is None or ema_s.empty:
            return 0.0

        val = float(ema_s.iloc[-1])
        return val if not np.isnan(val) else 0.0

    # ------------------------------------------------------------------ #
    #  4. RSI(14)                                                          #
    # ------------------------------------------------------------------ #

    def calc_rsi(self, df: pd.DataFrame, period: int = 14) -> float:
        """RSI(14) — used for FILTERING, not signal generation.

        Args:
            df: OHLCV DataFrame (1-min bars preferred).
            period: RSI look-back period (default 14).

        Returns:
            Current RSI value (0-100).
        """
        rsi_s = ta.rsi(df["Close"], length=period)
        if rsi_s is None or rsi_s.empty:
            return 50.0

        val = float(rsi_s.iloc[-1])
        return val if not np.isnan(val) else 50.0

    # ------------------------------------------------------------------ #
    #  5. MACD(12,26,9)                                                    #
    # ------------------------------------------------------------------ #

    def calc_macd(self, df: pd.DataFrame) -> tuple[float, float, float]:
        """MACD(12,26,9).  Histogram expanding = momentum accelerating.

        Spec: 5-min timeframe.  Caller passes correct bars.

        Returns:
            (macd_line, signal_line, histogram)
        """
        macd_df = ta.macd(df["Close"], fast=12, slow=26, signal=9)
        if macd_df is None or macd_df.empty:
            return (0.0, 0.0, 0.0)

        cols = macd_df.columns.tolist()
        # pandas_ta returns columns like MACD_12_26_9, MACDs_12_26_9, MACDh_12_26_9
        macd_line = float(macd_df[cols[0]].iloc[-1])
        signal_line = float(macd_df[cols[1]].iloc[-1])
        histogram = float(macd_df[cols[2]].iloc[-1])

        return (
            macd_line if not np.isnan(macd_line) else 0.0,
            signal_line if not np.isnan(signal_line) else 0.0,
            histogram if not np.isnan(histogram) else 0.0,
        )

    # ------------------------------------------------------------------ #
    #  6. ATR(14) + ATR%                                                   #
    # ------------------------------------------------------------------ #

    def calc_atr(
        self, df: pd.DataFrame, period: int = 14
    ) -> tuple[float, float]:
        """ATR(14) and ATR as percentage of price.

        Determines stop distances, position size, and whether to trade at all.
        Spec: 5-min timeframe.

        Returns:
            (atr_value, atr_percent)
        """
        atr_s = ta.atr(df["High"], df["Low"], df["Close"], length=period)
        if atr_s is None or atr_s.empty:
            return (0.0, 0.0)

        atr_val = float(atr_s.iloc[-1])
        if np.isnan(atr_val):
            return (0.0, 0.0)

        price = float(df["Close"].iloc[-1])
        atr_pct = (atr_val / price * 100.0) if price != 0 else 0.0

        return (atr_val, atr_pct)

    # ------------------------------------------------------------------ #
    #  7. RVOL (time-adjusted relative volume)                             #
    # ------------------------------------------------------------------ #

    def calc_rvol(self, df: pd.DataFrame, ticker: str) -> float:
        """Time-adjusted Relative Volume — THE NZT indicator.

        High RVOL = institutions are active.  Compares current volume to
        the 20-day average volume **at the same time of day**.

        This requires a DatetimeIndex so we can group bars by time-of-day.
        If the index is not datetime or there is insufficient history,
        falls back to a simple RVOL (current bar vol / 20-bar avg vol).

        Args:
            df: OHLCV DataFrame, ideally with 20+ days of 1-min bars.
            ticker: Symbol (used only for logging).

        Returns:
            RVOL multiplier (e.g. 2.5 means 2.5x normal volume).
        """
        if df.empty or "Volume" not in df.columns:
            return 0.0

        # If we have a DatetimeIndex, use time-of-day adjustment
        if isinstance(df.index, pd.DatetimeIndex) and len(df) > 100:
            try:
                current_bar = df.iloc[-1]
                current_tod = current_bar.name.time()
                current_vol = float(current_bar["Volume"])

                # Extract time-of-day for each bar
                times = df.index.time
                # Find bars at the same time-of-day (excluding today's bar)
                today = df.index[-1].date()
                mask = pd.Series(
                    [(t == current_tod and d != today)
                     for t, d in zip(times, df.index.date)],
                    index=df.index,
                )
                historical_same_time = df.loc[mask, "Volume"]

                # Use last 20 matching bars (20-day lookback at this TOD)
                if len(historical_same_time) >= 5:
                    avg_vol = float(historical_same_time.tail(20).mean())
                    if avg_vol > 0:
                        return round(current_vol / avg_vol, 2)
            except Exception:
                logger.debug("Time-adjusted RVOL failed for %s, using fallback", ticker)

        # Fallback: simple relative volume vs 20-bar average
        current_vol = float(df["Volume"].iloc[-1])
        avg_vol = float(df["Volume"].iloc[-21:-1].mean()) if len(df) > 21 else float(df["Volume"].mean())
        if avg_vol > 0:
            return round(current_vol / avg_vol, 2)
        return 0.0

    # ------------------------------------------------------------------ #
    #  8. Volume spike                                                     #
    # ------------------------------------------------------------------ #

    def calc_volume_spike(self, df: pd.DataFrame) -> bool:
        """Detect volume spike: current bar > 2.0x the 20-bar average.

        Strong confirmation signal on 1-min bars.

        Returns:
            True if current volume >= 2.0x average.
        """
        if df.empty or len(df) < 2:
            return False

        current_vol = float(df["Volume"].iloc[-1])
        lookback = min(20, len(df) - 1)
        avg_vol = float(df["Volume"].iloc[-lookback - 1 : -1].mean())

        if avg_vol <= 0:
            return False
        return current_vol >= 2.0 * avg_vol

    # ------------------------------------------------------------------ #
    #  9. Dollar volume                                                    #
    # ------------------------------------------------------------------ #

    def calc_dollar_volume(self, df: pd.DataFrame) -> float:
        """Dollar volume = Price x Volume.  Sum for the day.

        Min $500M/day for Bot B qualification.

        Returns:
            Total dollar volume across all bars in the DataFrame.
        """
        if df.empty:
            return 0.0

        # Use typical price * volume for each bar, then sum
        dollar_vol = (df["Close"] * df["Volume"]).sum()
        return float(dollar_vol)

    # ------------------------------------------------------------------ #
    #  10. Opening Range (5-min and 15-min)                                #
    # ------------------------------------------------------------------ #

    def calc_opening_range(
        self, df: pd.DataFrame
    ) -> tuple[float, float, float, float]:
        """Compute the Opening Range high/low for 5-min and 15-min periods.

        Uses the first 5 and 15 minutes after 09:30 ET.  A tight Opening
        Range = coiled spring (breakout imminent).

        Requires a DatetimeIndex in US/Eastern.  Falls back to first N bars
        if the index is not timezone-aware.

        Returns:
            (or_high_5m, or_low_5m, or_high_15m, or_low_15m)
        """
        if df.empty:
            return (0.0, 0.0, 0.0, 0.0)

        or_5_high = 0.0
        or_5_low = 0.0
        or_15_high = 0.0
        or_15_low = 0.0

        if isinstance(df.index, pd.DatetimeIndex):
            try:
                # Try to use actual time-of-day
                idx = df.index
                # Convert to US/Eastern if not already
                if idx.tz is None:
                    # Assume already in ET
                    pass

                today = idx[-1].date()
                today_bars = df[idx.date == today]

                if not today_bars.empty:
                    market_open_dt = pd.Timestamp(
                        year=today.year, month=today.month, day=today.day,
                        hour=9, minute=30,
                    )
                    if idx.tz is not None:
                        market_open_dt = market_open_dt.tz_localize(idx.tz)

                    # 5-minute opening range: 09:30:00 to 09:34:59
                    or5_end = market_open_dt + pd.Timedelta(minutes=5)
                    or5_bars = today_bars[
                        (today_bars.index >= market_open_dt)
                        & (today_bars.index < or5_end)
                    ]
                    if not or5_bars.empty:
                        or_5_high = float(or5_bars["High"].max())
                        or_5_low = float(or5_bars["Low"].min())

                    # 15-minute opening range: 09:30:00 to 09:44:59
                    or15_end = market_open_dt + pd.Timedelta(minutes=15)
                    or15_bars = today_bars[
                        (today_bars.index >= market_open_dt)
                        & (today_bars.index < or15_end)
                    ]
                    if not or15_bars.empty:
                        or_15_high = float(or15_bars["High"].max())
                        or_15_low = float(or15_bars["Low"].min())

                    return (or_5_high, or_5_low, or_15_high, or_15_low)
            except Exception:
                logger.debug("Time-based OR failed, falling back to bar count")

        # Fallback: use first 5 and 15 bars as proxy
        if len(df) >= 5:
            or5 = df.iloc[:5]
            or_5_high = float(or5["High"].max())
            or_5_low = float(or5["Low"].min())

        if len(df) >= 15:
            or15 = df.iloc[:15]
            or_15_high = float(or15["High"].max())
            or_15_low = float(or15["Low"].min())

        return (or_5_high, or_5_low, or_15_high, or_15_low)

    # ------------------------------------------------------------------ #
    #  11. Bid-ask spread proxy                                            #
    # ------------------------------------------------------------------ #

    def calc_bid_ask_spread(self, df: pd.DataFrame) -> float:
        """Estimate bid-ask spread from OHLC data.

        Proxy: uses Corwin-Schultz high-low spread estimator simplified.
        A simpler proxy: (High - Low) / ((High + Low) / 2) for the last bar,
        scaled down because the full bar range overstates the spread.

        > 0.15% = skip the ticker (execution cost too high).

        Returns:
            Estimated spread as a percentage (e.g. 0.05 = 0.05%).
        """
        if df.empty:
            return 0.0

        # Use Corwin-Schultz simplified: average of recent bars
        lookback = min(20, len(df))
        recent = df.iloc[-lookback:]

        # High-low spread estimator
        highs = recent["High"].values
        lows = recent["Low"].values
        mid = (highs + lows) / 2.0
        mid_safe = np.where(mid == 0, np.nan, mid)

        # Raw range as % of mid
        raw_spreads = (highs - lows) / mid_safe * 100.0

        # The bar range overstates spread by roughly 3-5x for liquid stocks
        # on 1-min bars. Scale factor adjusts for this.
        scale_factor = 0.25
        avg_spread = float(np.nanmean(raw_spreads)) * scale_factor

        return round(avg_spread, 4) if not np.isnan(avg_spread) else 0.0

    # ------------------------------------------------------------------ #
    #  12. Microstructure score                                            #
    # ------------------------------------------------------------------ #

    def calc_microstructure_score(self, df: pd.DataFrame) -> float:
        """Compute a microstructure health score from 0-10.

        Combines:
        - Uptick/downtick ratio (close > previous close)
        - Trade velocity (volume acceleration)
        - Buying pressure proxy (close position within bar)

        Higher = healthier, more institutional-quality price action.

        Returns:
            Score 0.0 to 10.0.
        """
        if df.empty or len(df) < 10:
            return 5.0  # neutral default

        lookback = min(50, len(df))
        recent = df.iloc[-lookback:]

        score = 0.0

        # --- Component 1: Uptick ratio (0-3 points) ---
        close_diff = recent["Close"].diff()
        upticks = (close_diff > 0).sum()
        total_ticks = close_diff.count()
        uptick_ratio = upticks / total_ticks if total_ticks > 0 else 0.5
        # Score: 0.5 is neutral (1.5), strong directionality scores higher
        score += abs(uptick_ratio - 0.5) * 2.0 * 3.0  # max ~3.0

        # --- Component 2: Volume acceleration (0-3 points) ---
        vol = recent["Volume"].values
        if len(vol) >= 10:
            recent_avg = float(np.mean(vol[-5:]))
            older_avg = float(np.mean(vol[-10:-5]))
            if older_avg > 0:
                accel = recent_avg / older_avg
                # Accelerating volume (> 1.0) is positive
                vol_score = min(3.0, max(0.0, (accel - 0.5) * 2.0))
                score += vol_score
            else:
                score += 1.5

        # --- Component 3: Close position within bar — buying pressure (0-2 points) ---
        bar_ranges = recent["High"] - recent["Low"]
        bar_ranges_safe = bar_ranges.replace(0, np.nan)
        close_position = (recent["Close"] - recent["Low"]) / bar_ranges_safe
        avg_close_pos = float(close_position.mean())
        if not np.isnan(avg_close_pos):
            # 0.5 = neutral → 1 pt; closer to 1.0 = strong buying → 2 pts
            score += min(2.0, avg_close_pos * 2.0)
        else:
            score += 1.0

        # --- Component 4: Consistency — low wick-to-body ratio (0-2 points) ---
        body = abs(recent["Close"] - recent["Open"])
        total_range = recent["High"] - recent["Low"]
        total_range_safe = total_range.replace(0, np.nan)
        body_ratio = body / total_range_safe
        avg_body = float(body_ratio.mean())
        if not np.isnan(avg_body):
            # Higher body ratio = cleaner price action
            score += min(2.0, avg_body * 2.5)
        else:
            score += 1.0

        return round(min(10.0, max(0.0, score)), 1)

    # ------------------------------------------------------------------ #
    #  13. Bollinger Bands                                                 #
    # ------------------------------------------------------------------ #

    def calc_bollinger_bands(
        self, df: pd.DataFrame, period: int = 20, std: int = 2
    ) -> tuple[float, float, float]:
        """Bollinger Bands. Squeeze = breakout imminent. Mean reversion zones.

        Spec: 5-min timeframe.

        Returns:
            (upper_band, middle_band, lower_band)
        """
        bb = ta.bbands(df["Close"], length=period, std=std)
        if bb is None or bb.empty:
            return (0.0, 0.0, 0.0)

        cols = bb.columns.tolist()
        # pandas_ta bbands columns: BBL, BBM, BBU, BBB, BBP
        # Typical order: BBL_{p}_{s}, BBM_{p}_{s}, BBU_{p}_{s}, ...
        bbl_col = [c for c in cols if c.startswith("BBL")]
        bbm_col = [c for c in cols if c.startswith("BBM")]
        bbu_col = [c for c in cols if c.startswith("BBU")]

        upper = float(bb[bbu_col[0]].iloc[-1]) if bbu_col else 0.0
        middle = float(bb[bbm_col[0]].iloc[-1]) if bbm_col else 0.0
        lower = float(bb[bbl_col[0]].iloc[-1]) if bbl_col else 0.0

        return (
            upper if not np.isnan(upper) else 0.0,
            middle if not np.isnan(middle) else 0.0,
            lower if not np.isnan(lower) else 0.0,
        )

    # ------------------------------------------------------------------ #
    #  14. ADX(14)                                                         #
    # ------------------------------------------------------------------ #

    def calc_adx(self, df: pd.DataFrame, period: int = 14) -> float:
        """ADX(14) — trend strength indicator.

        > 25 = trending, < 15 = no trend (chop).
        Spec: 5-min timeframe.

        Returns:
            ADX value (0-100).
        """
        adx_df = ta.adx(df["High"], df["Low"], df["Close"], length=period)
        if adx_df is None or adx_df.empty:
            return 0.0

        # Column name: ADX_{period}
        adx_col = [c for c in adx_df.columns if c.startswith("ADX_")]
        if not adx_col:
            return 0.0

        val = float(adx_df[adx_col[0]].iloc[-1])
        return val if not np.isnan(val) else 0.0

    # ------------------------------------------------------------------ #
    #  15. Stochastic RSI                                                  #
    # ------------------------------------------------------------------ #

    def calc_stochastic_rsi(self, df: pd.DataFrame, period: int = 14) -> float:
        """Stochastic RSI — oversold/overbought in momentum context.

        Spec: 5-min timeframe.

        Returns:
            StochRSI %K value (0-100).
        """
        stoch_rsi = ta.stochrsi(df["Close"], length=period)
        if stoch_rsi is None or stoch_rsi.empty:
            return 50.0

        # pandas_ta returns STOCHRSIk_{period}_{rsi_period}_{k}_{d}
        k_col = [c for c in stoch_rsi.columns if "STOCHRSIk" in c]
        if not k_col:
            # Fallback to first column
            val = float(stoch_rsi.iloc[-1, 0])
        else:
            val = float(stoch_rsi[k_col[0]].iloc[-1])

        return val if not np.isnan(val) else 50.0

    # ------------------------------------------------------------------ #
    #  16. Keltner Channels                                                #
    # ------------------------------------------------------------------ #

    def calc_keltner_channels(
        self, df: pd.DataFrame, period: int = 20, atr_mult: float = 1.5
    ) -> tuple[float, float]:
        """Keltner Channels — BB inside Keltner = squeeze (pre-breakout).

        Spec: 5-min timeframe.

        Returns:
            (upper_channel, lower_channel)
        """
        kc = ta.kc(
            df["High"], df["Low"], df["Close"],
            length=period, scalar=atr_mult,
        )
        if kc is None or kc.empty:
            return (0.0, 0.0)

        cols = kc.columns.tolist()
        kcl_col = [c for c in cols if c.startswith("KCL")]
        kcu_col = [c for c in cols if c.startswith("KCU")]

        upper = float(kc[kcu_col[0]].iloc[-1]) if kcu_col else 0.0
        lower = float(kc[kcl_col[0]].iloc[-1]) if kcl_col else 0.0

        return (
            upper if not np.isnan(upper) else 0.0,
            lower if not np.isnan(lower) else 0.0,
        )

    # ------------------------------------------------------------------ #
    #  17. OBV (On-Balance Volume)                                         #
    # ------------------------------------------------------------------ #

    def calc_obv(self, df: pd.DataFrame) -> float:
        """On-Balance Volume — divergence from price = warning.

        Spec: 5-min timeframe.

        Returns:
            Current OBV value.
        """
        obv_s = ta.obv(df["Close"], df["Volume"])
        if obv_s is None or obv_s.empty:
            return 0.0

        val = float(obv_s.iloc[-1])
        return val if not np.isnan(val) else 0.0

    # ------------------------------------------------------------------ #
    #  18. MFI (Money Flow Index)                                          #
    # ------------------------------------------------------------------ #

    def calc_mfi(self, df: pd.DataFrame, period: int = 14) -> float:
        """Money Flow Index — volume-weighted RSI.

        Spec: 5-min timeframe.

        Returns:
            MFI value (0-100).
        """
        mfi_s = ta.mfi(df["High"], df["Low"], df["Close"], df["Volume"], length=period)
        if mfi_s is None or mfi_s.empty:
            return 50.0

        val = float(mfi_s.iloc[-1])
        return val if not np.isnan(val) else 50.0

    # ------------------------------------------------------------------ #
    #  19. Cumulative Delta (OHLC proxy)                                   #
    # ------------------------------------------------------------------ #

    def calc_cumulative_delta(self, df: pd.DataFrame) -> float:
        """Cumulative delta — net buying vs selling pressure.

        Proxy from OHLC: delta per bar = (Close - Open) / (High - Low) * Volume.
        When close > open, buyers dominated; when close < open, sellers dominated.
        The (Close - Open)/(High - Low) ratio estimates the fraction of volume
        attributable to the dominant side.

        Spec: 1-min bars.

        Returns:
            Cumulative delta for the session (positive = net buying).
        """
        if df.empty:
            return 0.0

        bar_range = df["High"] - df["Low"]
        bar_range_safe = bar_range.replace(0, np.nan).fillna(
            0.0001  # tiny epsilon for flat bars
        )

        delta_per_bar = ((df["Close"] - df["Open"]) / bar_range_safe) * df["Volume"]
        cum_delta = float(delta_per_bar.sum())

        return cum_delta if not np.isnan(cum_delta) else 0.0

    # ------------------------------------------------------------------ #
    #  20. Speed of Tape                                                   #
    # ------------------------------------------------------------------ #

    def calc_speed_of_tape(self, df: pd.DataFrame) -> float:
        """Speed of tape — trades-per-second proxy.

        Calculated as volume / number_of_bars in the recent period.
        Accelerating speed = institutional activity.

        Spec: 1-min bars.

        Returns:
            Average volume per bar over the last 10 bars (proxy for
            trades per unit time).  Higher = faster tape.
        """
        if df.empty:
            return 0.0

        lookback = min(10, len(df))
        recent_vol = df["Volume"].iloc[-lookback:]
        avg_vol_per_bar = float(recent_vol.mean())

        return round(avg_vol_per_bar, 2) if not np.isnan(avg_vol_per_bar) else 0.0

    # ------------------------------------------------------------------ #
    #  EMA Alignment Scorer                                                #
    # ------------------------------------------------------------------ #

    def calc_ema_alignment(self, df: pd.DataFrame) -> int:
        """Score EMA alignment from 0 to 8.

        Scoring:
        - Price > EMA9 > EMA20 > EMA50 = 8 (perfect bull alignment)
        - Price < EMA9 < EMA20 < EMA50 = 8 (perfect bear alignment)
        - Mixed = proportional score based on how many conditions hold.

        Each of the 4 pairwise comparisons (bullish direction) earns 2 points.
        The maximum of bull and bear scores is returned.

        Returns:
            Integer score 0-8.
        """
        close = df["Close"]
        price = float(close.iloc[-1])

        ema9_s = ta.ema(close, length=9)
        ema20_s = ta.ema(close, length=20)
        ema50_s = ta.ema(close, length=50)

        if ema9_s is None or ema20_s is None or ema50_s is None:
            return 0

        ema9 = float(ema9_s.iloc[-1]) if not ema9_s.empty else 0.0
        ema20 = float(ema20_s.iloc[-1]) if not ema20_s.empty else 0.0
        ema50 = float(ema50_s.iloc[-1]) if not ema50_s.empty else 0.0

        if any(np.isnan(v) for v in (ema9, ema20, ema50)):
            return 0

        # Bull alignment checks
        bull_score = 0
        if price > ema9:
            bull_score += 2
        if ema9 > ema20:
            bull_score += 2
        if ema20 > ema50:
            bull_score += 2
        if price > ema50:
            bull_score += 2

        # Bear alignment checks
        bear_score = 0
        if price < ema9:
            bear_score += 2
        if ema9 < ema20:
            bear_score += 2
        if ema20 < ema50:
            bear_score += 2
        if price < ema50:
            bear_score += 2

        return max(bull_score, bear_score)

    # ------------------------------------------------------------------ #
    #  Squeeze Detector                                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def detect_squeeze(
        bb_upper: float,
        bb_lower: float,
        kc_upper: float,
        kc_lower: float,
    ) -> bool:
        """Detect a volatility squeeze: Bollinger Bands inside Keltner Channels.

        When BB contracts inside KC, a breakout is imminent.  This is the
        classic TTM Squeeze indicator condition.

        Args:
            bb_upper: Bollinger upper band.
            bb_lower: Bollinger lower band.
            kc_upper: Keltner upper channel.
            kc_lower: Keltner lower channel.

        Returns:
            True if squeeze is active (BB inside KC).
        """
        if not all(v > 0 for v in (bb_upper, bb_lower, kc_upper, kc_lower)):
            return False

        return bb_upper < kc_upper and bb_lower > kc_lower

    # ------------------------------------------------------------------ #
    #  CVD Divergence Detection                                            #
    # ------------------------------------------------------------------ #

    def detect_cvd_divergence(
        self, df: pd.DataFrame, lookback: int = 20
    ) -> dict:
        """Detect divergence between price and Cumulative Volume Delta (CVD).

        Uses 5-bar pivot detection to find swing highs/lows in both price
        and CVD, then checks for bearish and bullish divergences.

        Bearish divergence: price makes a higher high but CVD makes a lower high.
            Indicates buying pressure is weakening despite price advancing.
        Bullish divergence: price makes a lower low but CVD makes a higher low.
            Indicates selling pressure is weakening despite price declining.

        Args:
            df: OHLCV DataFrame (needs at least ``lookback`` bars).
            lookback: Number of bars to scan for swing points (default 20).

        Returns:
            Dict with keys: bearish_divergence (bool), bullish_divergence (bool),
            absorption (bool).
        """
        result = {
            "bearish_divergence": False,
            "bullish_divergence": False,
            "absorption": False,
        }

        if df.empty or len(df) < lookback + 5:
            return result

        recent = df.iloc[-(lookback + 5):]

        # --- Build CVD series ---
        bar_range = recent["High"] - recent["Low"]
        bar_range_safe = bar_range.replace(0, np.nan).fillna(0.0001)
        delta_per_bar = ((recent["Close"] - recent["Open"]) / bar_range_safe) * recent["Volume"]
        cvd_series = delta_per_bar.cumsum()

        highs = recent["High"].values
        lows = recent["Low"].values
        cvd_vals = cvd_series.values

        # --- 5-bar pivot detection ---
        # A pivot high at index i: High[i] > High[i-1], High[i] > High[i-2],
        #                           High[i] > High[i+1], High[i] > High[i+2]
        pivot_highs_price = []
        pivot_lows_price = []
        pivot_highs_cvd = []
        pivot_lows_cvd = []

        for i in range(2, len(highs) - 2):
            # Price pivot high
            if (highs[i] > highs[i - 1] and highs[i] > highs[i - 2]
                    and highs[i] > highs[i + 1] and highs[i] > highs[i + 2]):
                pivot_highs_price.append((i, highs[i]))
                pivot_highs_cvd.append((i, cvd_vals[i]))

            # Price pivot low
            if (lows[i] < lows[i - 1] and lows[i] < lows[i - 2]
                    and lows[i] < lows[i + 1] and lows[i] < lows[i + 2]):
                pivot_lows_price.append((i, lows[i]))
                pivot_lows_cvd.append((i, cvd_vals[i]))

        # --- Bearish divergence: price higher high + CVD lower high ---
        if len(pivot_highs_price) >= 2:
            prev_ph = pivot_highs_price[-2]
            curr_ph = pivot_highs_price[-1]
            prev_cvd_h = pivot_highs_cvd[-2]
            curr_cvd_h = pivot_highs_cvd[-1]

            if curr_ph[1] > prev_ph[1] and curr_cvd_h[1] < prev_cvd_h[1]:
                result["bearish_divergence"] = True

        # --- Bullish divergence: price lower low + CVD higher low ---
        if len(pivot_lows_price) >= 2:
            prev_pl = pivot_lows_price[-2]
            curr_pl = pivot_lows_price[-1]
            prev_cvd_l = pivot_lows_cvd[-2]
            curr_cvd_l = pivot_lows_cvd[-1]

            if curr_pl[1] < prev_pl[1] and curr_cvd_l[1] > prev_cvd_l[1]:
                result["bullish_divergence"] = True

        # --- Absorption detection ---
        result["absorption"] = self.detect_absorption(df)

        return result

    # ------------------------------------------------------------------ #
    #  Absorption Detection                                                #
    # ------------------------------------------------------------------ #

    def detect_absorption(self, df: pd.DataFrame) -> bool:
        """Detect absorption: high volume (>2x avg) but small price range (<0.3 * ATR).

        Absorption signals that large players are absorbing selling/buying pressure
        without allowing price to move. This typically precedes a reversal or
        breakout once the absorbing side is done accumulating.

        Args:
            df: OHLCV DataFrame.

        Returns:
            True if the current bar shows absorption characteristics.
        """
        if df.empty or len(df) < 15:
            return False

        current_bar = df.iloc[-1]
        current_vol = float(current_bar["Volume"])
        current_range = float(current_bar["High"] - current_bar["Low"])

        # Average volume over the last 20 bars (or available)
        lookback = min(20, len(df) - 1)
        avg_vol = float(df["Volume"].iloc[-lookback - 1:-1].mean())

        if avg_vol <= 0:
            return False

        # ATR(14) for range comparison
        atr_s = ta.atr(df["High"], df["Low"], df["Close"], length=14)
        if atr_s is None or atr_s.empty:
            return False

        atr_val = float(atr_s.iloc[-1])
        if np.isnan(atr_val) or atr_val <= 0:
            return False

        # Absorption: volume > 2x average AND price range < 0.3 * ATR
        high_volume = current_vol > 2.0 * avg_vol
        small_range = current_range < 0.3 * atr_val

        return high_volume and small_range

    # ------------------------------------------------------------------ #
    #  Initial Balance (Auction Market Theory)                             #
    # ------------------------------------------------------------------ #

    def compute_initial_balance(
        self, df: pd.DataFrame
    ) -> tuple[float, float, float, float, bool]:
        """Compute the Initial Balance (IB) for Auction Market Theory.

        The Initial Balance is the first 60 minutes of trading (09:30-10:30 ET).
        It establishes the day's value area framework.

        IB extension measures how far price has moved beyond the IB range,
        expressed as a percentage of that range.  Extension > 100% indicates
        a trend day with strong directional conviction.

        Requires a DatetimeIndex in US/Eastern.  Falls back to first 60 bars
        if the index is not timezone-aware.

        Args:
            df: OHLCV DataFrame (1-min bars preferred).

        Returns:
            (ib_high, ib_low, ib_range, ib_extension_pct, is_trend_day)
        """
        if df.empty:
            return (0.0, 0.0, 0.0, 0.0, False)

        ib_high = 0.0
        ib_low = 0.0

        if isinstance(df.index, pd.DatetimeIndex):
            try:
                idx = df.index
                today = idx[-1].date()
                today_bars = df[idx.date == today]

                if not today_bars.empty:
                    market_open_dt = pd.Timestamp(
                        year=today.year, month=today.month, day=today.day,
                        hour=9, minute=30,
                    )
                    if idx.tz is not None:
                        market_open_dt = market_open_dt.tz_localize(idx.tz)

                    # 60-minute IB: 09:30:00 to 10:29:59
                    ib_end = market_open_dt + pd.Timedelta(minutes=60)
                    ib_bars = today_bars[
                        (today_bars.index >= market_open_dt)
                        & (today_bars.index < ib_end)
                    ]
                    if not ib_bars.empty:
                        ib_high = float(ib_bars["High"].max())
                        ib_low = float(ib_bars["Low"].min())
            except Exception:
                logger.debug("Time-based IB failed, falling back to bar count")
                ib_high = 0.0
                ib_low = 0.0

        # Fallback: use first 60 bars as proxy for 60 minutes of 1-min data
        if ib_high == 0.0 and ib_low == 0.0 and len(df) >= 60:
            ib_slice = df.iloc[:60]
            ib_high = float(ib_slice["High"].max())
            ib_low = float(ib_slice["Low"].min())

        ib_range = ib_high - ib_low if (ib_high > 0 and ib_low > 0) else 0.0

        # Extension: how far current price has moved beyond IB
        current_price = float(df["Close"].iloc[-1])
        if ib_range > 0:
            if current_price > ib_high:
                extension = (current_price - ib_high) / ib_range * 100.0
            elif current_price < ib_low:
                extension = (ib_low - current_price) / ib_range * 100.0
            else:
                extension = 0.0
        else:
            extension = 0.0

        is_trend_day = extension > 100.0

        return (ib_high, ib_low, ib_range, round(extension, 1), is_trend_day)

    # ------------------------------------------------------------------ #
    #  Cross-Sectional Momentum (Industry-Neutralized)                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def compute_cross_sectional_momentum(
        ticker_returns: dict[str, float],
        sector_map: dict[str, str],
    ) -> dict[str, float]:
        """Rank tickers by 3-month momentum within their sector.

        Industry-neutralized momentum strips out sector-level effects so
        the ranking captures only stock-specific momentum.  This avoids
        the common pitfall where a "momentum" signal is just a bet on a
        hot sector.

        Args:
            ticker_returns: Mapping of ticker -> 3-month return (e.g.
                {"AAPL": 0.12, "MSFT": 0.08, ...}).
            sector_map: Mapping of ticker -> sector string (e.g.
                {"AAPL": "Technology", "JPM": "Financials", ...}).

        Returns:
            dict mapping each ticker to its within-sector rank score
            on a 0-1 scale (1 = strongest momentum in sector).
            Tickers with no sector mapping are ranked in an "Unknown"
            group.  Singletons in a sector receive a rank of 0.5.
        """
        if not ticker_returns:
            return {}

        # --- Group tickers by sector ---
        sectors: dict[str, list[str]] = {}
        for ticker in ticker_returns:
            sector = sector_map.get(ticker, "Unknown")
            sectors.setdefault(sector, []).append(ticker)

        # --- Rank within each sector ---
        result: dict[str, float] = {}

        for sector, tickers in sectors.items():
            # Sort tickers by return ascending (lowest first)
            sorted_tickers = sorted(tickers, key=lambda t: ticker_returns.get(t, 0.0))
            n = len(sorted_tickers)

            if n == 1:
                # Singleton sector — assign neutral rank
                result[sorted_tickers[0]] = 0.5
                continue

            for rank_idx, ticker in enumerate(sorted_tickers):
                # Normalize rank to 0-1 scale: 0 = weakest, 1 = strongest
                result[ticker] = rank_idx / (n - 1)

        return result

    # ------------------------------------------------------------------ #
    #  Capital Gains Overhang (Disposition Effect Proxy)                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def compute_capital_gains_overhang(
        df: pd.DataFrame,
        lookback: int = 60,
    ) -> float:
        """Compute the capital gains overhang — a disposition effect proxy.

        Uses the volume-weighted average price (VWAP) over the lookback
        period as an approximate reference price (average cost basis for
        recent market participants).  The overhang is the percentage
        difference between the current price and this reference price.

        Interpretation:
            - Negative overhang: many recent buyers are underwater.
              Behavioural finance research shows this creates a bullish
              bias because investors are reluctant to sell at a loss.
            - High positive overhang: investors are sitting on profits
              and eager to sell, creating bearish friction.

        Args:
            df: OHLCV DataFrame with at least ``lookback`` rows.
                Must have Close and Volume columns.
            lookback: Number of bars to use for the reference price
                calculation. Default 60 (approximately 3 months of
                daily bars, or ~1 hour of 1-min bars).

        Returns:
            Capital gains overhang as a decimal fraction
            (e.g. 0.05 = 5% above reference, -0.03 = 3% below).
            Returns 0.0 on insufficient data or errors.
        """
        if df is None or df.empty or len(df) < 2:
            return 0.0

        try:
            window = min(lookback, len(df))
            recent = df.iloc[-window:]

            vol = recent["Volume"].values
            close = recent["Close"].values

            total_vol = vol.sum()
            if total_vol <= 0:
                return 0.0

            # Volume-weighted average price as reference (cost basis proxy)
            reference_price = float(np.dot(close, vol) / total_vol)

            if reference_price <= 0:
                return 0.0

            current_price = float(df["Close"].iloc[-1])
            overhang = (current_price - reference_price) / reference_price

            return round(overhang, 6) if not np.isnan(overhang) else 0.0
        except Exception:
            return 0.0
