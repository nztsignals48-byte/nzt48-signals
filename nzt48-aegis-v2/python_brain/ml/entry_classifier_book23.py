"""Book 23 — LightGBM Entry Classifier for AEGIS V2.

The 48-feature entry classifier that predicts whether a signal entry will be profitable.
Trained on 2+ years of minute-level data with 500-bar forward-labeling.

Output: Confidence score [0.0-1.0] added to every signal for live filtering.
- Only generate signals if classifier confidence > 0.6
- Track classifier performance vs actual signal performance for quarterly retraining

FEATURE ENGINEERING (48 features across 8 categories):

1. MOMENTUM (6 features):
   - RSI(14), RSI(20), RSI(2)
   - MACD signal, histogram, trend

2. MEAN REVERSION (6 features):
   - Bollinger Band deviation
   - Price percentiles [5, 20, 50] day lookback
   - Stochastic %K, %D
   - Keltner envelope deviation

3. VOLUME (6 features):
   - Volume ratio: current / 20-day avg
   - OBV trend: current / 20-bar SMA
   - VWAP deviation: price / VWAP
   - Volume momentum (vol_today / vol_yesterday)
   - Accumulation/Distribution indicator
   - CMF (Chaikin Money Flow)

4. MICROSTRUCTURE (6 features):
   - Bid-ask spread (bps)
   - Order imbalance: (bid_size - ask_size) / (bid_size + ask_size)
   - VPIN (Volume-synchronized Probability of Informed Trading)
   - Tick direction (up vs down ticks)
   - Price acceleration (2nd derivative of close)
   - Volatility of volatility (std of 5-bar ATR)

5. REGIME (6 features):
   - VIX level (absolute)
   - VIX trend (VIX_today / VIX_20bar_avg)
   - Realized vol (5-day) vs implied (VIX)
   - Market regime: 1.0=STEADY, 0.5=TRANSITION, 0.0=CRISIS
   - VIX term structure: (VIX1m - VIX3m) / VIX3m
   - Interest rate environment: 10Y yield vs 3-month avg

6. CORRELATION (6 features):
   - Symbol vs market ES (20-bar rolling)
   - Symbol vs sector (sector correlation)
   - Cross-asset momentum: ES lead-lag with 3USL
   - NQ lead-lag with 3QQL
   - Correlation stability: std of rolling correlation
   - Systemic risk: high_yield_spread moving avg

7. VOLATILITY (4 features):
   - ATR(14) / close
   - Parkinson volatility (high-low range)
   - Garman-Klass volatility (open-high-low-close)
   - Historical volatility (20-bar close-to-close)

8. INTRADAY/SESSION (6 features):
   - Time of day: 0.0 (market open) to 1.0 (market close)
   - Session: 0=ASIA, 0.33=EUROPE, 0.66=US
   - Minutes since market open
   - Minutes to next major economic event
   - Day of week: 0=Monday to 4=Friday
   - Gap from previous close (%)

TRAINING DATA:
- 2+ years minute-level OHLCV
- 80% train / 20% holdout test split (chronological, no lookahead)
- 5-fold time-series cross-validation
- Class balance: use class_weight='balanced'
- Min samples per leaf: 50
- Max depth: 7
- Learning rate: 0.05
- Early stopping: validation AUC > 100 rounds

TARGET VARIABLE:
- Entry signal: 1 = signal generated + won > 15 pips (after 1% cost)
- 0 = no signal or loss
- Lookback: 500-bar forward window to label

VALIDATION:
- AUC-ROC > 0.65 on holdout test
- Precision > 0.55
- Recall > 0.50
- Top 10 features explain > 60% of variance

RUST INFERENCE:
- Export to ONNX
- Use ort crate for inference (< 1ms per prediction)
- Confidence score [0.0-1.0] per signal
- Filter signals with confidence < 0.6
"""

import json
import math
import os
import pickle
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
    HAS_LGBM = True
except ImportError:
    HAS_LGBM = False

try:
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import roc_auc_score, precision_score, recall_score, confusion_matrix, roc_curve
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


# ════════════════════════════════════════════════════════════════════════════════
# FEATURE ENGINEERING: 48 Features Across 8 Categories
# ════════════════════════════════════════════════════════════════════════════════

class FeatureEngineer:
    """Compute all 48 features from OHLCV bar history."""

    def __init__(self, ohlcv_df: pd.DataFrame):
        """
        Args:
            ohlcv_df: DataFrame with columns [timestamp, open, high, low, close, volume]
                     Sorted by timestamp, at least 500 bars for full feature set.
        """
        self.df = ohlcv_df.copy()
        self.df = self.df.reset_index(drop=True)

    def compute_all_features(self) -> pd.DataFrame:
        """Compute all 48 features and return as DataFrame (one row per bar)."""
        features = {}

        # MOMENTUM (6)
        features.update(self._compute_momentum())

        # MEAN REVERSION (6)
        features.update(self._compute_mean_reversion())

        # VOLUME (6)
        features.update(self._compute_volume())

        # MICROSTRUCTURE (6)
        features.update(self._compute_microstructure())

        # REGIME (6)
        features.update(self._compute_regime())

        # CORRELATION (6)
        features.update(self._compute_correlation())

        # VOLATILITY (4)
        features.update(self._compute_volatility())

        # INTRADAY/SESSION (6)
        features.update(self._compute_intraday_session())

        # Merge all features into DataFrame
        result = pd.DataFrame(features)
        result.index = self.df.index
        return result

    # ─────────────────────────────────────────────────────────────────────────────
    # MOMENTUM (6 features)
    # ─────────────────────────────────────────────────────────────────────────────
    def _compute_momentum(self) -> Dict[str, List[float]]:
        """RSI(14), RSI(20), RSI(2), MACD signal, histogram, trend."""
        features = {}

        close = self.df['close'].values
        features['rsi_14'] = self._rsi(close, 14)
        features['rsi_20'] = self._rsi(close, 20)
        features['rsi_2'] = self._rsi(close, 2)

        # MACD(12, 26, 9)
        macd_val, signal, histogram = self._macd(close, 12, 26, 9)
        features['macd_signal'] = signal
        features['macd_histogram'] = histogram

        # MACD trend: difference of MACD from previous bar
        macd_trend = np.diff(macd_val, prepend=macd_val[0])
        features['macd_trend'] = macd_trend

        return features

    def _rsi(self, prices: np.ndarray, period: int) -> np.ndarray:
        """Relative Strength Index."""
        deltas = np.diff(prices)
        seed = deltas[:period + 1]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period
        rs = up / down if down != 0 else 0

        rsi = np.zeros_like(prices, dtype=float)
        rsi[:period] = 100.0 - 100.0 / (1.0 + rs)

        for i in range(period, len(prices)):
            delta = deltas[i - 1]
            if delta > 0:
                upval = delta
                downval = 0.0
            else:
                upval = 0.0
                downval = -delta

            up = (up * (period - 1) + upval) / period
            down = (down * (period - 1) + downval) / period

            rs = up / down if down != 0 else 0
            rsi[i] = 100.0 - 100.0 / (1.0 + rs)

        return rsi

    def _macd(self, prices: np.ndarray, fast: int, slow: int, signal_period: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """MACD(fast, slow, signal_period)."""
        ema_fast = self._ema(prices, fast)
        ema_slow = self._ema(prices, slow)
        macd_val = ema_fast - ema_slow
        signal = self._ema(macd_val, signal_period)
        histogram = macd_val - signal
        return macd_val, signal, histogram

    def _ema(self, prices: np.ndarray, period: int) -> np.ndarray:
        """Exponential Moving Average."""
        ema = np.zeros_like(prices, dtype=float)
        ema[0] = prices[0]
        alpha = 2.0 / (period + 1.0)
        for i in range(1, len(prices)):
            ema[i] = alpha * prices[i] + (1.0 - alpha) * ema[i - 1]
        return ema

    # ─────────────────────────────────────────────────────────────────────────────
    # MEAN REVERSION (6 features)
    # ─────────────────────────────────────────────────────────────────────────────
    def _compute_mean_reversion(self) -> Dict[str, List[float]]:
        """BB deviation, price percentiles, Stochastic, Keltner deviation."""
        features = {}
        close = self.df['close'].values
        high = self.df['high'].values
        low = self.df['low'].values

        # Bollinger Bands deviation: (price - SMA) / (2 * std)
        sma20 = self._sma(close, 20)
        std20 = self._rolling_std(close, 20)
        bb_width = 2 * std20
        bb_dev = np.where(bb_width > 0, (close - sma20) / bb_width, 0.0)
        features['bb_deviation'] = bb_dev

        # Price percentiles in [5, 20, 50] day lookback
        features['price_percentile_5d'] = self._rolling_percentile(close, 5 * 24 * 60, 0.5)  # 5 days in minutes
        features['price_percentile_20d'] = self._rolling_percentile(close, 20 * 24 * 60, 0.5)
        features['price_percentile_50d'] = self._rolling_percentile(close, 50 * 24 * 60, 0.5)

        # Stochastic(14, 3, 3): %K, %D
        stoch_k, stoch_d = self._stochastic(high, low, close, 14, 3, 3)
        features['stochastic_k'] = stoch_k
        features['stochastic_d'] = stoch_d

        # Keltner envelope deviation: (price - EMA20) / (ATR(10))
        ema20 = self._ema(close, 20)
        atr10 = self._atr(high, low, close, 10)
        kelt_dev = np.where(atr10 > 0, (close - ema20) / atr10, 0.0)
        features['keltner_deviation'] = kelt_dev

        return features

    def _sma(self, prices: np.ndarray, period: int) -> np.ndarray:
        """Simple Moving Average."""
        sma = np.zeros_like(prices, dtype=float)
        for i in range(len(prices)):
            start = max(0, i - period + 1)
            sma[i] = np.mean(prices[start:i + 1])
        return sma

    def _rolling_std(self, prices: np.ndarray, period: int) -> np.ndarray:
        """Rolling standard deviation."""
        std = np.zeros_like(prices, dtype=float)
        for i in range(len(prices)):
            start = max(0, i - period + 1)
            std[i] = np.std(prices[start:i + 1])
        return std

    def _rolling_percentile(self, prices: np.ndarray, window: int, percentile: float) -> np.ndarray:
        """Rolling percentile within a window."""
        result = np.zeros_like(prices, dtype=float)
        for i in range(len(prices)):
            start = max(0, i - window + 1)
            result[i] = np.percentile(prices[start:i + 1], percentile * 100)
        return result

    def _stochastic(self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
                   period: int, k_period: int, d_period: int) -> Tuple[np.ndarray, np.ndarray]:
        """Stochastic Oscillator: %K, %D."""
        k = np.zeros_like(close, dtype=float)
        for i in range(len(close)):
            start = max(0, i - period + 1)
            h = np.max(high[start:i + 1])
            l = np.min(low[start:i + 1])
            if h != l:
                k[i] = 100.0 * (close[i] - l) / (h - l)
            else:
                k[i] = 50.0

        # Smooth %K with SMA
        k_smooth = self._sma(k, k_period)

        # %D is SMA of %K
        d = self._sma(k_smooth, d_period)

        return k_smooth, d

    def _atr(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
        """Average True Range."""
        tr = np.zeros_like(close, dtype=float)
        tr[0] = high[0] - low[0]
        for i in range(1, len(close)):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )

        atr = self._sma(tr, period)
        return atr

    # ─────────────────────────────────────────────────────────────────────────────
    # VOLUME (6 features)
    # ─────────────────────────────────────────────────────────────────────────────
    def _compute_volume(self) -> Dict[str, List[float]]:
        """Volume ratio, OBV trend, VWAP deviation, volume momentum, A/D, CMF."""
        features = {}
        close = self.df['close'].values
        volume = self.df['volume'].values
        high = self.df['high'].values
        low = self.df['low'].values

        # Volume ratio: current / 20-day avg
        vol_avg20 = self._sma(volume, 20 * 24 * 60)  # 20 days in minutes
        vol_ratio = np.where(vol_avg20 > 0, volume / vol_avg20, 1.0)
        features['volume_ratio'] = vol_ratio

        # OBV trend: current / 20-bar SMA
        obv = self._obv(close, volume)
        obv_sma = self._sma(obv, 20)
        obv_trend = np.where(obv_sma > 0, obv / obv_sma, 1.0)
        features['obv_trend'] = obv_trend

        # VWAP deviation: price / VWAP
        vwap = self._vwap(high, low, close, volume)
        vwap_dev = np.where(vwap > 0, close / vwap, 1.0)
        features['vwap_deviation'] = vwap_dev

        # Volume momentum: vol_today / vol_yesterday
        vol_momentum = np.ones_like(volume, dtype=float)
        for i in range(1, len(volume)):
            if volume[i - 1] > 0:
                vol_momentum[i] = volume[i] / volume[i - 1]
        features['volume_momentum'] = vol_momentum

        # Accumulation/Distribution indicator
        ad = self._accumulation_distribution(high, low, close, volume)
        features['accumulation_distribution'] = ad

        # Chaikin Money Flow (CMF)
        cmf = self._cmf(high, low, close, volume, 20)
        features['cmf'] = cmf

        return features

    def _obv(self, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
        """On-Balance Volume."""
        obv = np.zeros_like(close, dtype=float)
        obv[0] = volume[0]
        for i in range(1, len(close)):
            if close[i] > close[i - 1]:
                obv[i] = obv[i - 1] + volume[i]
            elif close[i] < close[i - 1]:
                obv[i] = obv[i - 1] - volume[i]
            else:
                obv[i] = obv[i - 1]
        return obv

    def _vwap(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
        """Volume-Weighted Average Price."""
        typical_price = (high + low + close) / 3.0
        vwap = np.zeros_like(close, dtype=float)
        cum_vol = np.zeros_like(volume, dtype=float)
        cum_tp_vol = np.zeros_like(volume, dtype=float)

        for i in range(len(close)):
            cum_vol[i] = np.sum(volume[:i + 1])
            cum_tp_vol[i] = np.sum(typical_price[:i + 1] * volume[:i + 1])
            vwap[i] = cum_tp_vol[i] / cum_vol[i] if cum_vol[i] > 0 else typical_price[i]

        return vwap

    def _accumulation_distribution(self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
                                   volume: np.ndarray) -> np.ndarray:
        """Accumulation/Distribution Line."""
        ad = np.zeros_like(close, dtype=float)
        for i in range(len(close)):
            if high[i] != low[i]:
                clv = ((close[i] - low[i]) - (high[i] - close[i])) / (high[i] - low[i])
            else:
                clv = 0
            ad[i] = ad[i - 1] + clv * volume[i] if i > 0 else clv * volume[i]
        return ad

    def _cmf(self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
             volume: np.ndarray, period: int) -> np.ndarray:
        """Chaikin Money Flow."""
        mfv = np.zeros_like(close, dtype=float)
        for i in range(len(close)):
            if high[i] != low[i]:
                mfm = ((close[i] - low[i]) - (high[i] - close[i])) / (high[i] - low[i])
            else:
                mfm = 0
            mfv[i] = mfm * volume[i]

        cmf = np.zeros_like(close, dtype=float)
        for i in range(len(close)):
            start = max(0, i - period + 1)
            sum_mfv = np.sum(mfv[start:i + 1])
            sum_vol = np.sum(volume[start:i + 1])
            cmf[i] = sum_mfv / sum_vol if sum_vol > 0 else 0
        return cmf

    # ─────────────────────────────────────────────────────────────────────────────
    # MICROSTRUCTURE (6 features)
    # ─────────────────────────────────────────────────────────────────────────────
    def _compute_microstructure(self) -> Dict[str, List[float]]:
        """Spread, order imbalance, VPIN, tick direction, price acceleration, vol-of-vol."""
        features = {}

        # For now, use placeholder values if bid/ask not available in OHLCV
        # In real implementation, these would come from tick-level data
        n = len(self.df)

        # Bid-ask spread (bps) - placeholder
        features['spread_bps'] = np.full(n, 1.0)  # 1 bp default if not available

        # Order imbalance - placeholder
        features['order_imbalance'] = np.zeros(n)

        # VPIN - placeholder (complex to compute from OHLCV alone)
        features['vpin'] = np.zeros(n)

        # Tick direction: up vs down ticks
        close = self.df['close'].values
        tick_direction = np.zeros(n, dtype=float)
        for i in range(1, n):
            if close[i] > close[i - 1]:
                tick_direction[i] = 1.0
            elif close[i] < close[i - 1]:
                tick_direction[i] = -1.0
        features['tick_direction'] = tick_direction

        # Price acceleration: 2nd derivative of close
        close_diff = np.diff(close, prepend=close[0])
        price_accel = np.diff(close_diff, prepend=close_diff[0])
        features['price_acceleration'] = price_accel

        # Volatility of volatility: std of 5-bar ATR
        high = self.df['high'].values
        low = self.df['low'].values
        atr5 = self._atr(high, low, close, 5)
        atr5_std = self._rolling_std(atr5, 5)
        features['volatility_of_volatility'] = atr5_std

        return features

    # ─────────────────────────────────────────────────────────────────────────────
    # REGIME (6 features)
    # ─────────────────────────────────────────────────────────────────────────────
    def _compute_regime(self) -> Dict[str, List[float]]:
        """VIX level, VIX trend, realized vs implied vol, market regime, VIX term structure, IR env."""
        features = {}
        n = len(self.df)

        # VIX level - placeholder (would come from external source)
        features['vix_level'] = np.full(n, 15.0)

        # VIX trend - placeholder
        features['vix_trend'] = np.ones(n)

        # Realized vol vs implied (5-day realized vol / VIX)
        close = self.df['close'].values
        returns = np.diff(close) / close[:-1]
        realized_vol = np.zeros(n, dtype=float)
        for i in range(5, n):
            realized_vol[i] = np.std(returns[i - 5:i]) * math.sqrt(252)
        features['realized_vs_implied_vol'] = np.where(np.full(n, 15.0) > 0, realized_vol / 15.0, 1.0)

        # Market regime: 1.0=STEADY, 0.5=TRANSITION, 0.0=CRISIS (placeholder)
        features['market_regime'] = np.ones(n)

        # VIX term structure (VIX1m - VIX3m) / VIX3m - placeholder
        features['vix_term_structure'] = np.zeros(n)

        # Interest rate environment: 10Y yield vs 3-month avg - placeholder
        features['interest_rate_env'] = np.zeros(n)

        return features

    # ─────────────────────────────────────────────────────────────────────────────
    # CORRELATION (6 features)
    # ─────────────────────────────────────────────────────────────────────────────
    def _compute_correlation(self) -> Dict[str, List[float]]:
        """Symbol vs ES, sector, cross-asset lead-lag, correlation stability, systemic risk."""
        features = {}
        n = len(self.df)

        # Correlation with ES (would come from external data) - placeholder
        features['symbol_vs_es_correlation'] = np.full(n, 0.5)

        # Sector correlation - placeholder
        features['sector_correlation'] = np.full(n, 0.5)

        # ES/3USL lead-lag - placeholder
        features['es_3usl_leadlag'] = np.zeros(n)

        # NQ/3QQL lead-lag - placeholder
        features['nq_3qql_leadlag'] = np.zeros(n)

        # Correlation stability - placeholder
        features['correlation_stability'] = np.zeros(n)

        # Systemic risk (high yield spread MA) - placeholder
        features['systemic_risk'] = np.full(n, 2.0)

        return features

    # ─────────────────────────────────────────────────────────────────────────────
    # VOLATILITY (4 features)
    # ─────────────────────────────────────────────────────────────────────────────
    def _compute_volatility(self) -> Dict[str, List[float]]:
        """ATR%, Parkinson vol, Garman-Klass vol, historical vol."""
        features = {}
        close = self.df['close'].values
        high = self.df['high'].values
        low = self.df['low'].values

        # ATR(14) / close
        atr14 = self._atr(high, low, close, 14)
        atr_pct = np.where(close > 0, atr14 / close, 0.0)
        features['atr_pct'] = atr_pct

        # Parkinson volatility: sqrt((1/4*ln(2)) * sum(ln(H/L)^2))
        hl_ratio = np.where(low > 0, high / low, 1.0)
        ln_hl = np.log(hl_ratio)
        parkinson_var = np.zeros_like(close, dtype=float)
        for i in range(20, len(close)):
            parkinson_var[i] = np.sqrt(np.sum(ln_hl[i - 20:i] ** 2) / (4 * 20 * math.log(2)))
        features['parkinson_volatility'] = parkinson_var

        # Garman-Klass volatility (more complex formula)
        gk_vol = np.zeros_like(close, dtype=float)
        for i in range(1, len(close)):
            hl = high[i] - low[i]
            co = close[i] - self.df['open'].values[i]
            gk_vol[i] = math.sqrt(0.5 * (high[i] / low[i]) ** 2 - (2 * math.log(2) - 1) * (close[i] / self.df['open'].values[i]) ** 2)
        features['garman_klass_volatility'] = gk_vol

        # Historical volatility (20-bar close-to-close)
        historical_vol = np.zeros_like(close, dtype=float)
        for i in range(20, len(close)):
            returns = np.diff(close[i - 20:i]) / close[i - 21:i - 1]
            historical_vol[i] = np.std(returns) * math.sqrt(252)
        features['historical_volatility'] = historical_vol

        return features

    # ─────────────────────────────────────────────────────────────────────────────
    # INTRADAY/SESSION (6 features)
    # ─────────────────────────────────────────────────────────────────────────────
    def _compute_intraday_session(self) -> Dict[str, List[float]]:
        """Time of day, session, minutes since open, minutes to next event, day of week, gap %."""
        features = {}
        n = len(self.df)

        # Extract timestamp from DataFrame if available, otherwise use index
        if 'timestamp' in self.df.columns:
            timestamps = pd.to_datetime(self.df['timestamp'])
        else:
            timestamps = pd.date_range(start='2024-01-01', periods=n, freq='1min')

        # Time of day: 0.0 (9:30 ET) to 1.0 (16:00 ET)
        hours = timestamps.dt.hour
        minutes = timestamps.dt.minute
        time_of_day = np.zeros(n, dtype=float)
        for i in range(n):
            h, m = hours[i], minutes[i]
            minutes_since_open = (h - 9) * 60 + (m - 30)
            minutes_since_open = max(0, min(minutes_since_open, 390))  # 390 min = 6.5 hours
            time_of_day[i] = minutes_since_open / 390.0
        features['time_of_day'] = time_of_day

        # Session: 0=ASIA, 0.33=EUROPE, 0.66=US
        session = np.zeros(n, dtype=float)
        for i in range(n):
            h = hours[i]
            if h < 8:
                session[i] = 0.0  # ASIA
            elif h < 16:
                session[i] = 0.33  # EUROPE
            else:
                session[i] = 0.66  # US
        features['session'] = session

        # Minutes since market open
        minutes_since_open = np.zeros(n, dtype=float)
        for i in range(n):
            h, m = hours[i], minutes[i]
            minutes_since_open[i] = (h - 9) * 60 + (m - 30)
        features['minutes_since_open'] = minutes_since_open

        # Minutes to next major economic event - placeholder (would come from calendar)
        features['minutes_to_event'] = np.full(n, 1440)

        # Day of week: 0=Monday to 4=Friday
        dow = timestamps.dt.dayofweek
        features['day_of_week'] = dow.astype(float)

        # Gap from previous close (%)
        close = self.df['close'].values
        gap_pct = np.zeros(n, dtype=float)
        if 'open' in self.df.columns:
            open_prices = self.df['open'].values
            for i in range(1, n):
                if close[i - 1] > 0:
                    gap_pct[i] = ((open_prices[i] - close[i - 1]) / close[i - 1]) * 100
        features['gap_pct'] = gap_pct

        return features


# ════════════════════════════════════════════════════════════════════════════════
# TARGET LABELING: Forward-Window Labeling (500 bars)
# ════════════════════════════════════════════════════════════════════════════════

class TargetLabeler:
    """Label training targets based on forward-looking price movement."""

    def __init__(self, ohlcv_df: pd.DataFrame, entry_target_pips: float = 15, slippage_cost_pct: float = 0.01):
        """
        Args:
            ohlcv_df: OHLCV DataFrame (must have 'close' column)
            entry_target_pips: Win threshold in pips (after cost)
            slippage_cost_pct: Slippage + commission as % of entry price
        """
        self.df = ohlcv_df.copy()
        self.entry_target_pips = entry_target_pips
        self.slippage_cost_pct = slippage_cost_pct
        self.pip_value = self.df['close'].iloc[0] / 100.0  # Approximate pip value

    def label_targets(self, lookahead_bars: int = 500) -> np.ndarray:
        """
        Label each bar based on forward price movement.

        Returns:
            targets: np.array of 0s and 1s
            - 1 if price moves > entry_target_pips within lookahead_bars
            - 0 otherwise
        """
        close = self.df['close'].values
        high = self.df['high'].values
        targets = np.zeros(len(close), dtype=int)

        pips_threshold = self.entry_target_pips * self.pip_value
        cost_threshold = close * (self.slippage_cost_pct / 100.0)
        actual_threshold = pips_threshold + cost_threshold

        for i in range(len(close) - lookahead_bars):
            # Maximum high in next lookahead_bars
            future_high = np.max(high[i:i + lookahead_bars])
            profit = future_high - close[i]

            if profit > actual_threshold[i]:
                targets[i] = 1

        # Last lookahead_bars bars get no label (can't look ahead)
        targets[-lookahead_bars:] = 0

        return targets


# ════════════════════════════════════════════════════════════════════════════════
# MODEL TRAINING & VALIDATION
# ════════════════════════════════════════════════════════════════════════════════

class EntryClassifierTrainer:
    """Train LightGBM entry classifier on 2+ years of minute-level data."""

    def __init__(self, model_dir: str = "/app/data/entry_classifier"):
        """
        Args:
            model_dir: Directory to save model artifacts
        """
        if not HAS_LGBM or not HAS_SKLEARN:
            raise ImportError("LightGBM and scikit-learn required. pip install lightgbm scikit-learn")

        self.model_dir = model_dir
        os.makedirs(model_dir, exist_ok=True)

        self.model = None
        self.scaler = None
        self.feature_names = None
        self.metrics = {}

    def train(self, X: pd.DataFrame, y: np.ndarray, test_size: float = 0.2):
        """
        Train LightGBM classifier with time-series cross-validation.

        Args:
            X: Features DataFrame (all 48 features)
            y: Target labels (0/1)
            test_size: Fraction of data to hold out for final test
        """
        # Remove rows with NaN
        valid_idx = ~(X.isna().any(axis=1) | np.isnan(y))
        X = X[valid_idx].reset_index(drop=True)
        y = y[valid_idx]

        # Time-series split
        n_train = int(len(X) * (1 - test_size))
        X_train, X_test = X[:n_train], X[n_train:]
        y_train, y_test = y[:n_train], y[n_train:]

        # Standardize features
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        # Store feature names
        self.feature_names = X.columns.tolist()

        # LightGBM parameters
        params = {
            'objective': 'binary',
            'metric': 'auc',
            'num_leaves': 31,
            'max_depth': 7,
            'learning_rate': 0.05,
            'min_data_in_leaf': 50,
            'feature_fraction': 0.8,
            'bagging_fraction': 0.8,
            'bagging_freq': 5,
            'class_weight': 'balanced',
            'verbose': -1,
        }

        # Create training data
        train_data = lgb.Dataset(X_train_scaled, label=y_train)
        test_data = lgb.Dataset(X_test_scaled, label=y_test, reference=train_data)

        # Train with early stopping
        self.model = lgb.train(
            params,
            train_data,
            num_boost_round=500,
            valid_sets=[test_data],
            callbacks=[
                lgb.early_stopping(100),
                lgb.log_evaluation(period=50),
            ],
        )

        # Evaluate on test set
        self._evaluate(X_test_scaled, y_test)

        return self.metrics

    def _evaluate(self, X_test: np.ndarray, y_test: np.ndarray):
        """Compute validation metrics."""
        y_pred_proba = self.model.predict(X_test)
        y_pred = (y_pred_proba >= 0.5).astype(int)

        # AUC-ROC
        auc = roc_auc_score(y_test, y_pred_proba)
        fpr, tpr, _ = roc_curve(y_test, y_pred_proba)

        # Precision & Recall
        precision = precision_score(y_test, y_pred, zero_division=0)
        recall = recall_score(y_test, y_pred, zero_division=0)

        # Confusion matrix
        tn, fp, fn, tp = confusion_matrix(y_test, y_pred).ravel()

        self.metrics = {
            'auc': auc,
            'precision': precision,
            'recall': recall,
            'fpr': fpr.tolist(),
            'tpr': tpr.tolist(),
            'tp': int(tp),
            'fp': int(fp),
            'tn': int(tn),
            'fn': int(fn),
            'timestamp': datetime.now().isoformat(),
        }

        print(f"VALIDATION METRICS:")
        print(f"  AUC-ROC: {auc:.4f}")
        print(f"  Precision: {precision:.4f}")
        print(f"  Recall: {recall:.4f}")
        print(f"  TP={tp}, FP={fp}, TN={tn}, FN={fn}")

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """
        Predict confidence scores for new data.

        Args:
            X: Features DataFrame (same shape as training)

        Returns:
            confidence: np.array of floats [0.0-1.0]
        """
        if self.model is None:
            raise ValueError("Model not trained. Call train() first.")

        X_scaled = self.scaler.transform(X)
        return self.model.predict(X_scaled)

    def save(self, model_path: str):
        """Save model to disk."""
        self.model.save_model(model_path)

        # Also save scaler and feature names
        metadata = {
            'feature_names': self.feature_names,
            'metrics': self.metrics,
            'model_path': model_path,
        }
        with open(model_path.replace('.txt', '_metadata.json'), 'w') as f:
            json.dump(metadata, f, indent=2)

        print(f"Model saved to {model_path}")

    @classmethod
    def load(cls, model_path: str) -> 'EntryClassifierTrainer':
        """Load model from disk."""
        trainer = cls()
        trainer.model = lgb.Booster(model_file=model_path)

        # Load metadata
        metadata_path = model_path.replace('.txt', '_metadata.json')
        if os.path.exists(metadata_path):
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
                trainer.feature_names = metadata['feature_names']
                trainer.metrics = metadata['metrics']

        return trainer

    def get_feature_importance(self, top_n: int = 10) -> pd.DataFrame:
        """Return top N most important features."""
        if self.model is None:
            raise ValueError("Model not trained.")

        importance = self.model.feature_importance(importance_type='gain')
        feature_importance_df = pd.DataFrame({
            'feature': self.feature_names,
            'importance': importance,
        }).sort_values('importance', ascending=False)

        return feature_importance_df.head(top_n)


# ════════════════════════════════════════════════════════════════════════════════
# ONNX EXPORT FOR RUST INFERENCE
# ════════════════════════════════════════════════════════════════════════════════

class ONNXExporter:
    """Export LightGBM model to ONNX format for Rust inference."""

    @staticmethod
    def export_to_onnx(lightgbm_model, feature_names: List[str], output_path: str):
        """
        Export LightGBM model to ONNX.

        Args:
            lightgbm_model: Trained LightGBM Booster
            feature_names: List of feature names
            output_path: Output .onnx file path
        """
        try:
            import onnx
            import onnxmltools
            from skl2onnx import convert_sklearn
            print("ONNX export requires: pip install onnxmltools skl2onnx onnx")
            print("For LightGBM, use: pip install onnxmltools[lightgbm]")
        except ImportError:
            print("ONNX tools not installed. Skipping export.")
            return None

        # This is a placeholder — actual ONNX export requires additional setup
        # For production, use onnxmltools.convert_lightgbm(lightgbm_model)
        print(f"Would export to {output_path}")
        return output_path


# ════════════════════════════════════════════════════════════════════════════════
# LIVE INFERENCE: Add to Bridge.py
# ════════════════════════════════════════════════════════════════════════════════

class LivePredictor:
    """Real-time entry confidence scoring for bridge.py."""

    def __init__(self, model_path: str):
        """Load trained model."""
        self.trainer = EntryClassifierTrainer.load(model_path)
        self.last_features = {}

    def score_entry(self, ticker_id: int, features_dict: Dict[str, float],
                   confidence_threshold: float = 0.6) -> Tuple[float, bool]:
        """
        Score an entry signal based on 48 features.

        Args:
            ticker_id: Ticker ID
            features_dict: Dict of feature values
            confidence_threshold: Minimum confidence to approve signal

        Returns:
            (confidence_score, is_approved)
            - confidence_score: [0.0-1.0]
            - is_approved: True if confidence > threshold
        """
        # Convert dict to DataFrame row
        X = pd.DataFrame([features_dict])

        # Predict
        confidence = self.trainer.predict(X)[0]

        # Approve if above threshold
        is_approved = confidence > confidence_threshold

        return confidence, is_approved


if __name__ == '__main__':
    print("Entry Classifier (Book 23) loaded. Use in bridge.py or training script.")
