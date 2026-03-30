"""
Book 21-22: Entry Feature Engineering
48 features across 8 categories for ML entry timing models
Pure Python stdlib, no numpy/sklearn dependencies
"""

from dataclasses import dataclass
from typing import Dict, List, Any
from datetime import datetime
import math


@dataclass
class Tick:
    """Minimal tick representation"""
    timestamp: datetime
    price: float
    volume: int
    bid: float = 0.0
    ask: float = 0.0


class FeatureExtractor:
    """
    Extracts 48 features from market data for entry timing ML models.

    Feature Groups:
    - Price Action (12): returns, volatility, technicals
    - Volume/Liquidity (8): RVOL, spreads, flow
    - Momentum (8): MACD, stochastic, oscillators
    - Volatility (6): Hurst, Parkinson, regime
    - Temporal (6): time of day, session, calendar
    - Signal Context (8): confidence, Kelly, strategy state
    """

    def __init__(self):
        self.session_open_hour = 9  # 9:30 AM ET
        self.session_close_hour = 16  # 4:00 PM ET

    def extract(self, ticks: List[Any], indicators: Dict[str, Any],
                signal: Dict[str, Any], msg: Dict[str, Any]) -> Dict[str, float]:
        """
        Extract all 48 features from market state at signal generation time.

        Args:
            ticks: List of recent tick data (at least 100 for full features)
            indicators: Dict with precomputed indicators (RSI, ADX, etc.)
            signal: Signal metadata (confidence, kelly, strategy)
            msg: State message (drawdown, trades, regime)

        Returns:
            Dict mapping feature names to float values
        """
        features = {}

        if not ticks or len(ticks) < 2:
            return self._zero_features()

        # Extract feature groups
        features.update(self._price_action_features(ticks, indicators))
        features.update(self._volume_liquidity_features(ticks, indicators))
        features.update(self._momentum_features(ticks, indicators))
        features.update(self._volatility_features(ticks, indicators))
        features.update(self._temporal_features(ticks))
        features.update(self._signal_context_features(signal, msg))

        return features

    def _price_action_features(self, ticks: List[Any], indicators: Dict[str, Any]) -> Dict[str, float]:
        """Features 1-12: Price action and technicals"""
        features = {}
        prices = [self._get_price(t) for t in ticks]

        # 1-3: Returns (1-tick, 5-tick, 20-tick)
        features['return_1t'] = self._safe_return(prices, 1)
        features['return_5t'] = self._safe_return(prices, 5)
        features['return_20t'] = self._safe_return(prices, 20)

        # 4: Volatility (20-tick rolling std)
        features['vol_20t'] = self._rolling_std(prices, 20)

        # 5: ATR (14-tick)
        features['atr_pct'] = self._atr_percent(ticks, 14)

        # 6: Bollinger %B
        features['bb_pctb'] = self._bollinger_pct_b(prices, 20, 2.0)

        # 7: RSI-14
        features['rsi_14'] = indicators.get('rsi', {}).get('value', 50.0)

        # 8: Price vs VWAP
        features['price_vs_vwap'] = self._price_vs_vwap(ticks)

        # 9-10: Price vs moving averages
        features['price_vs_sma20'] = self._price_vs_sma(prices, 20)
        features['price_vs_sma50'] = self._price_vs_sma(prices, 50)

        # 11-12: Candle patterns (for last complete candle)
        candle = self._get_candle_features(ticks)
        features['candle_body_ratio'] = candle['body_ratio']
        features['upper_wick_ratio'] = candle['upper_wick_ratio']

        return features

    def _volume_liquidity_features(self, ticks: List[Any], indicators: Dict[str, Any]) -> Dict[str, float]:
        """Features 13-20: Volume and liquidity metrics"""
        features = {}

        # 13: RVOL (relative volume)
        features['rvol'] = indicators.get('volume_analytics', {}).get('rvol', 1.0)

        # 14: Volume trend
        volumes = [self._get_volume(t) for t in ticks]
        features['volume_trend'] = self._volume_trend(volumes)

        # 15: Bid-ask spread %
        features['spread_pct'] = self._bid_ask_spread_pct(ticks)

        # 16: VPIN
        features['vpin'] = indicators.get('volume_analytics', {}).get('vpin', 0.5)

        # 17: Volume at price concentration
        features['vol_concentration'] = self._volume_concentration(ticks)

        # 18: Buy volume ratio
        features['buy_vol_ratio'] = self._buy_volume_ratio(ticks)

        # 19: Volume acceleration
        features['vol_acceleration'] = self._volume_acceleration(volumes)

        # 20: Spread volatility
        features['spread_vol'] = self._spread_volatility(ticks)

        return features

    def _momentum_features(self, ticks: List[Any], indicators: Dict[str, Any]) -> Dict[str, float]:
        """Features 21-28: Momentum indicators"""
        features = {}
        prices = [self._get_price(t) for t in ticks]

        # 21: MACD histogram
        macd = indicators.get('macd', {})
        features['macd_hist'] = macd.get('histogram', 0.0)

        # 22-23: Stochastic oscillator
        stoch = self._stochastic(ticks, 14, 3)
        features['stoch_k'] = stoch['k']
        features['stoch_d'] = stoch['d']

        # 24: Williams %R
        features['williams_r'] = self._williams_r(ticks, 14)

        # 25: CCI (Commodity Channel Index)
        features['cci_20'] = self._cci(ticks, 20)

        # 26: ADX
        features['adx_14'] = indicators.get('adx', {}).get('adx', 25.0)

        # 27: +DI / -DI ratio
        adx_data = indicators.get('adx', {})
        plus_di = adx_data.get('plus_di', 25.0)
        minus_di = adx_data.get('minus_di', 25.0)
        features['di_ratio'] = plus_di / (minus_di + 1e-10)

        # 28: Rate of change (12-tick)
        features['roc_12'] = self._rate_of_change(prices, 12)

        return features

    def _volatility_features(self, ticks: List[Any], indicators: Dict[str, Any]) -> Dict[str, float]:
        """Features 29-34: Volatility regime metrics"""
        features = {}

        # 29: Hurst exponent
        features['hurst'] = indicators.get('hurst', {}).get('H', 0.5)

        # 30: Parkinson volatility
        features['parkinson_vol'] = self._parkinson_vol(ticks, 20)

        # 31: Garman-Klass volatility
        features['garman_klass_vol'] = self._garman_klass_vol(ticks, 20)

        # 32: Vol-of-vol
        features['vol_of_vol'] = self._vol_of_vol(ticks, 20)

        # 33: ATR expansion
        atr_5 = self._atr(ticks, 5)
        atr_20 = self._atr(ticks, 20)
        features['atr_expansion'] = atr_5 / (atr_20 + 1e-10)

        # 34: Squeeze (BB width / KC width)
        features['squeeze'] = self._squeeze_indicator(ticks, 20)

        return features

    def _temporal_features(self, ticks: List[Any]) -> Dict[str, float]:
        """Features 35-40: Time-based features"""
        features = {}

        if not ticks:
            return {
                'hour': 12.0, 'minutes_since_open': 120.0, 'day_of_week': 2.0,
                'minutes_to_close': 120.0, 'us_overlap': 1.0, 'days_since_event': 999.0
            }

        ts = self._get_timestamp(ticks[-1])

        # 35: Hour of day
        features['hour'] = float(ts.hour)

        # 36: Minutes since session open (9:30 AM ET)
        open_time = ts.replace(hour=self.session_open_hour, minute=30, second=0)
        minutes_since = (ts - open_time).total_seconds() / 60.0
        features['minutes_since_open'] = max(0.0, minutes_since)

        # 37: Day of week (0=Monday, 4=Friday)
        features['day_of_week'] = float(ts.weekday())

        # 38: Time to session close (4:00 PM ET)
        close_time = ts.replace(hour=self.session_close_hour, minute=0, second=0)
        minutes_to = (close_time - ts).total_seconds() / 60.0
        features['minutes_to_close'] = max(0.0, minutes_to)

        # 39: US market overlap (9:30-16:00 ET)
        is_overlap = (9 <= ts.hour < 16) or (ts.hour == 9 and ts.minute >= 30)
        features['us_overlap'] = 1.0 if is_overlap else 0.0

        # 40: Days since last major event (FOMC/CPI)
        # Stub: return 999 (no recent event)
        features['days_since_event'] = 999.0

        return features

    def _signal_context_features(self, signal: Dict[str, Any], msg: Dict[str, Any]) -> Dict[str, float]:
        """Features 41-48: Signal and strategy state"""
        features = {}

        # 41: Signal confidence
        features['confidence'] = float(signal.get('confidence', 0.0))

        # 42: Kelly fraction
        features['kelly'] = float(signal.get('kelly', 0.0))

        # 43: Strategy encoding (hash mod 10)
        strategy = signal.get('strategy', 'unknown')
        features['strategy_enc'] = float(hash(strategy) % 10)

        # 44: Regime encoding (0=trend, 1=mean_rev, 2=random)
        regime_map = {'trend': 0.0, 'mean_reversion': 1.0, 'random_walk': 2.0}
        regime = msg.get('regime', {}).get('state', 'random_walk')
        features['regime_enc'] = regime_map.get(regime, 2.0)

        # 45: Drawdown at entry (%)
        features['drawdown_pct'] = float(msg.get('drawdown', 0.0))

        # 46: Trades today count
        features['trades_today'] = float(msg.get('trades_today', 0))

        # 47: Win rate last 20 trades
        features['win_rate_20'] = float(msg.get('win_rate_20', 0.5))

        # 48: Consecutive wins/losses (positive=wins, negative=losses)
        features['consecutive_wl'] = float(msg.get('consecutive_wl', 0))

        return features

    # ========== Helper Methods ==========

    def _zero_features(self) -> Dict[str, float]:
        """Return all 48 features initialized to safe defaults"""
        return {
            'return_1t': 0.0, 'return_5t': 0.0, 'return_20t': 0.0,
            'vol_20t': 0.0, 'atr_pct': 0.0, 'bb_pctb': 0.5,
            'rsi_14': 50.0, 'price_vs_vwap': 0.0, 'price_vs_sma20': 0.0,
            'price_vs_sma50': 0.0, 'candle_body_ratio': 0.5, 'upper_wick_ratio': 0.25,
            'rvol': 1.0, 'volume_trend': 1.0, 'spread_pct': 0.0,
            'vpin': 0.5, 'vol_concentration': 0.0, 'buy_vol_ratio': 0.5,
            'vol_acceleration': 0.0, 'spread_vol': 0.0,
            'macd_hist': 0.0, 'stoch_k': 50.0, 'stoch_d': 50.0,
            'williams_r': -50.0, 'cci_20': 0.0, 'adx_14': 25.0,
            'di_ratio': 1.0, 'roc_12': 0.0,
            'hurst': 0.5, 'parkinson_vol': 0.0, 'garman_klass_vol': 0.0,
            'vol_of_vol': 0.0, 'atr_expansion': 1.0, 'squeeze': 1.0,
            'hour': 12.0, 'minutes_since_open': 120.0, 'day_of_week': 2.0,
            'minutes_to_close': 120.0, 'us_overlap': 1.0, 'days_since_event': 999.0,
            'confidence': 0.0, 'kelly': 0.0, 'strategy_enc': 0.0,
            'regime_enc': 2.0, 'drawdown_pct': 0.0, 'trades_today': 0.0,
            'win_rate_20': 0.5, 'consecutive_wl': 0.0
        }

    def _get_price(self, tick: Any) -> float:
        """Extract price from tick (handle dict or object)"""
        if isinstance(tick, dict):
            return float(tick.get('price', 0.0))
        return float(getattr(tick, 'price', 0.0))

    def _get_volume(self, tick: Any) -> int:
        """Extract volume from tick"""
        if isinstance(tick, dict):
            return int(tick.get('volume', 0))
        return int(getattr(tick, 'volume', 0))

    def _get_timestamp(self, tick: Any) -> datetime:
        """Extract timestamp from tick"""
        if isinstance(tick, dict):
            return tick.get('timestamp', datetime.now())
        return getattr(tick, 'timestamp', datetime.now())

    def _safe_return(self, prices: List[float], period: int) -> float:
        """Calculate return over period with bounds check"""
        if len(prices) < period + 1:
            return 0.0
        old_price = prices[-period - 1]
        new_price = prices[-1]
        if old_price == 0:
            return 0.0
        return (new_price - old_price) / old_price

    def _rolling_std(self, prices: List[float], window: int) -> float:
        """Calculate rolling standard deviation"""
        if len(prices) < window:
            return 0.0
        recent = prices[-window:]
        mean = sum(recent) / len(recent)
        variance = sum((x - mean) ** 2 for x in recent) / len(recent)
        return math.sqrt(variance)

    def _atr(self, ticks: List[Any], period: int) -> float:
        """Calculate Average True Range"""
        if len(ticks) < period + 1:
            return 0.0

        true_ranges = []
        for i in range(len(ticks) - period, len(ticks)):
            high = self._get_high(ticks[i])
            low = self._get_low(ticks[i])
            prev_close = self._get_close(ticks[i - 1]) if i > 0 else low

            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

    def _atr_percent(self, ticks: List[Any], period: int) -> float:
        """ATR as percentage of price"""
        atr = self._atr(ticks, period)
        price = self._get_price(ticks[-1])
        return (atr / price * 100.0) if price > 0 else 0.0

    def _bollinger_pct_b(self, prices: List[float], period: int, std_dev: float) -> float:
        """Bollinger %B indicator"""
        if len(prices) < period:
            return 0.5

        recent = prices[-period:]
        sma = sum(recent) / len(recent)
        std = self._rolling_std(prices, period)

        upper = sma + (std_dev * std)
        lower = sma - (std_dev * std)

        if upper == lower:
            return 0.5

        return (prices[-1] - lower) / (upper - lower)

    def _price_vs_vwap(self, ticks: List[Any]) -> float:
        """Price distance from VWAP (%)"""
        if len(ticks) < 2:
            return 0.0

        total_pv = sum(self._get_price(t) * self._get_volume(t) for t in ticks)
        total_v = sum(self._get_volume(t) for t in ticks)

        if total_v == 0:
            return 0.0

        vwap = total_pv / total_v
        current = self._get_price(ticks[-1])

        return ((current - vwap) / vwap * 100.0) if vwap > 0 else 0.0

    def _price_vs_sma(self, prices: List[float], period: int) -> float:
        """Price distance from SMA (%)"""
        if len(prices) < period:
            return 0.0

        sma = sum(prices[-period:]) / period
        current = prices[-1]

        return ((current - sma) / sma * 100.0) if sma > 0 else 0.0

    def _get_candle_features(self, ticks: List[Any]) -> Dict[str, float]:
        """Extract candle pattern features"""
        if len(ticks) < 2:
            return {'body_ratio': 0.5, 'upper_wick_ratio': 0.25}

        tick = ticks[-1]
        open_p = self._get_open(tick)
        close_p = self._get_close(tick)
        high = self._get_high(tick)
        low = self._get_low(tick)

        body = abs(close_p - open_p)
        total_range = high - low + 1e-10
        upper_wick = high - max(open_p, close_p)

        return {
            'body_ratio': body / total_range,
            'upper_wick_ratio': upper_wick / total_range
        }

    def _volume_trend(self, volumes: List[int]) -> float:
        """SMA(vol,5) / SMA(vol,20)"""
        if len(volumes) < 20:
            return 1.0

        sma5 = sum(volumes[-5:]) / 5.0
        sma20 = sum(volumes[-20:]) / 20.0

        return (sma5 / sma20) if sma20 > 0 else 1.0

    def _bid_ask_spread_pct(self, ticks: List[Any]) -> float:
        """Bid-ask spread as % of mid"""
        if not ticks:
            return 0.0

        tick = ticks[-1]
        bid = self._get_bid(tick)
        ask = self._get_ask(tick)

        if bid == 0 or ask == 0:
            return 0.0

        mid = (bid + ask) / 2.0
        return ((ask - bid) / mid * 100.0) if mid > 0 else 0.0

    def _volume_concentration(self, ticks: List[Any]) -> float:
        """Volume concentration at mode price"""
        if len(ticks) < 5:
            return 0.0

        price_volumes = {}
        for tick in ticks[-50:]:
            p = round(self._get_price(tick), 2)
            v = self._get_volume(tick)
            price_volumes[p] = price_volumes.get(p, 0) + v

        if not price_volumes:
            return 0.0

        max_vol = max(price_volumes.values())
        total_vol = sum(price_volumes.values())

        return (max_vol / total_vol) if total_vol > 0 else 0.0

    def _buy_volume_ratio(self, ticks: List[Any]) -> float:
        """Up-tick volume / total volume"""
        if len(ticks) < 2:
            return 0.5

        buy_vol = 0
        total_vol = 0

        for i in range(1, len(ticks)):
            vol = self._get_volume(ticks[i])
            total_vol += vol

            if self._get_price(ticks[i]) > self._get_price(ticks[i - 1]):
                buy_vol += vol

        return (buy_vol / total_vol) if total_vol > 0 else 0.5

    def _volume_acceleration(self, volumes: List[int]) -> float:
        """Current bar vol change vs prev bar"""
        if len(volumes) < 2:
            return 0.0

        curr = volumes[-1]
        prev = volumes[-2]

        return ((curr - prev) / prev) if prev > 0 else 0.0

    def _spread_volatility(self, ticks: List[Any]) -> float:
        """Std dev of last 20 spreads"""
        if len(ticks) < 20:
            return 0.0

        spreads = []
        for tick in ticks[-20:]:
            bid = self._get_bid(tick)
            ask = self._get_ask(tick)
            if bid > 0 and ask > 0:
                spreads.append(ask - bid)

        if not spreads:
            return 0.0

        mean = sum(spreads) / len(spreads)
        variance = sum((x - mean) ** 2 for x in spreads) / len(spreads)
        return math.sqrt(variance)

    def _stochastic(self, ticks: List[Any], k_period: int, d_period: int) -> Dict[str, float]:
        """Stochastic oscillator %K and %D"""
        if len(ticks) < k_period:
            return {'k': 50.0, 'd': 50.0}

        recent = ticks[-k_period:]
        high = max(self._get_high(t) for t in recent)
        low = min(self._get_low(t) for t in recent)
        close = self._get_close(ticks[-1])

        if high == low:
            k = 50.0
        else:
            k = ((close - low) / (high - low)) * 100.0

        # Simplified: use same K for D (should be SMA of last 3 K values)
        d = k

        return {'k': k, 'd': d}

    def _williams_r(self, ticks: List[Any], period: int) -> float:
        """Williams %R indicator"""
        if len(ticks) < period:
            return -50.0

        recent = ticks[-period:]
        high = max(self._get_high(t) for t in recent)
        low = min(self._get_low(t) for t in recent)
        close = self._get_close(ticks[-1])

        if high == low:
            return -50.0

        return ((high - close) / (high - low)) * -100.0

    def _cci(self, ticks: List[Any], period: int) -> float:
        """Commodity Channel Index"""
        if len(ticks) < period:
            return 0.0

        recent = ticks[-period:]
        tps = [(self._get_high(t) + self._get_low(t) + self._get_close(t)) / 3.0 for t in recent]
        sma_tp = sum(tps) / len(tps)

        mean_deviation = sum(abs(tp - sma_tp) for tp in tps) / len(tps)

        if mean_deviation == 0:
            return 0.0

        return (tps[-1] - sma_tp) / (0.015 * mean_deviation)

    def _rate_of_change(self, prices: List[float], period: int) -> float:
        """Rate of change over period"""
        if len(prices) < period + 1:
            return 0.0

        old = prices[-period - 1]
        new = prices[-1]

        return ((new - old) / old * 100.0) if old > 0 else 0.0

    def _parkinson_vol(self, ticks: List[Any], period: int) -> float:
        """Parkinson high-low volatility estimator"""
        if len(ticks) < period:
            return 0.0

        hl_ratios = []
        for tick in ticks[-period:]:
            high = self._get_high(tick)
            low = self._get_low(tick)
            if low > 0:
                hl_ratios.append(math.log(high / low) ** 2)

        if not hl_ratios:
            return 0.0

        return math.sqrt(sum(hl_ratios) / (4 * len(hl_ratios) * math.log(2)))

    def _garman_klass_vol(self, ticks: List[Any], period: int) -> float:
        """Garman-Klass volatility estimator"""
        if len(ticks) < period:
            return 0.0

        gk_terms = []
        for tick in ticks[-period:]:
            high = self._get_high(tick)
            low = self._get_low(tick)
            close = self._get_close(tick)
            open_p = self._get_open(tick)

            if low > 0 and open_p > 0:
                hl = math.log(high / low) ** 2
                co = math.log(close / open_p) ** 2
                gk_terms.append(0.5 * hl - (2 * math.log(2) - 1) * co)

        if not gk_terms:
            return 0.0

        return math.sqrt(sum(gk_terms) / len(gk_terms))

    def _vol_of_vol(self, ticks: List[Any], period: int) -> float:
        """Volatility of volatility"""
        if len(ticks) < period * 2:
            return 0.0

        # Calculate rolling vol for each sub-period
        vols = []
        for i in range(len(ticks) - period, len(ticks)):
            if i >= period:
                prices = [self._get_price(ticks[j]) for j in range(i - period, i)]
                vols.append(self._rolling_std(prices, period))

        if len(vols) < 2:
            return 0.0

        mean_vol = sum(vols) / len(vols)
        variance = sum((v - mean_vol) ** 2 for v in vols) / len(vols)
        return math.sqrt(variance)

    def _squeeze_indicator(self, ticks: List[Any], period: int) -> float:
        """Bollinger Band squeeze (BB width / KC width)"""
        if len(ticks) < period:
            return 1.0

        prices = [self._get_price(t) for t in ticks[-period:]]
        sma = sum(prices) / len(prices)
        std = self._rolling_std(prices, period)

        bb_width = 4 * std  # 2 std on each side

        atr = self._atr(ticks, period)
        kc_width = 2 * atr  # 1 ATR on each side

        return (bb_width / kc_width) if kc_width > 0 else 1.0

    def _get_open(self, tick: Any) -> float:
        """Get open price (fallback to price)"""
        if isinstance(tick, dict):
            return float(tick.get('open', tick.get('price', 0.0)))
        return float(getattr(tick, 'open', getattr(tick, 'price', 0.0)))

    def _get_close(self, tick: Any) -> float:
        """Get close price (fallback to price)"""
        if isinstance(tick, dict):
            return float(tick.get('close', tick.get('price', 0.0)))
        return float(getattr(tick, 'close', getattr(tick, 'price', 0.0)))

    def _get_high(self, tick: Any) -> float:
        """Get high price (fallback to price)"""
        if isinstance(tick, dict):
            return float(tick.get('high', tick.get('price', 0.0)))
        return float(getattr(tick, 'high', getattr(tick, 'price', 0.0)))

    def _get_low(self, tick: Any) -> float:
        """Get low price (fallback to price)"""
        if isinstance(tick, dict):
            return float(tick.get('low', tick.get('price', 0.0)))
        return float(getattr(tick, 'low', getattr(tick, 'price', 0.0)))

    def _get_bid(self, tick: Any) -> float:
        """Get bid price"""
        if isinstance(tick, dict):
            return float(tick.get('bid', 0.0))
        return float(getattr(tick, 'bid', 0.0))

    def _get_ask(self, tick: Any) -> float:
        """Get ask price"""
        if isinstance(tick, dict):
            return float(tick.get('ask', 0.0))
        return float(getattr(tick, 'ask', 0.0))
