#!/usr/bin/env python3
"""
Market Simulator - Realistic Tick Data Generation (Part B.2)

Generates realistic OHLCV data with:
- Gap opens (overnight jumps)
- Volatility regime changes (trending vs range-bound)
- Volume spikes
- Slippage model

Usage:
    from market_simulator import TickDataGenerator
    gen = TickDataGenerator()
    ticks = gen.generate_lse_ticks("QQQ3.L", num_ticks=1000)
"""

import random
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from enum import Enum


class VolatilityRegime(Enum):
    """Market volatility states"""
    LOW = 0.01  # 1% daily volatility
    NORMAL = 0.02  # 2% daily volatility
    HIGH = 0.04  # 4% daily volatility
    EXTREME = 0.08  # 8% daily volatility


class TrendDirection(Enum):
    """Market trend"""
    DOWN = -1
    SIDEWAYS = 0
    UP = 1


@dataclass
class Tick:
    """Individual tick data point"""
    timestamp: datetime
    symbol: str
    bid: float
    ask: float
    last: float
    volume: int
    slippage: float = 0.0  # For backtesting impact


class TickDataGenerator:
    """Generates realistic LSE tick data"""

    def __init__(self, seed: int = 42):
        random.seed(seed)
        try:
            from python_brain.ouroboros.contract_loader import load_yfinance_symbols
            self.lse_symbols = [s for s in load_yfinance_symbols() if s.endswith(".L")]
            if not self.lse_symbols:
                raise ValueError("No LSE symbols")
        except Exception:
            self.lse_symbols = ["QQQ3.L", "NVD3.L", "TSL3.L", "QQQS.L"]  # Minimal fallback
        self.volatility_regime = VolatilityRegime.NORMAL
        self.trend_direction = TrendDirection.UP

    def generate_lse_ticks(
        self,
        symbol: str,
        num_ticks: int = 1000,
        base_price: float = 100.0,
        start_time: Optional[datetime] = None,
    ) -> List[Tick]:
        """
        Generate realistic LSE tick data

        Args:
            symbol: LSE ticker (e.g., "QQQ3.L")
            num_ticks: Number of ticks to generate
            base_price: Starting price
            start_time: Start datetime (default: now)

        Returns:
            List of Tick objects
        """
        if start_time is None:
            start_time = datetime.now()

        ticks = []
        current_price = base_price
        current_time = start_time
        tick_interval = timedelta(seconds=5)  # 5 second bars

        # Volatility regime tracking
        ticks_in_regime = 0
        regime_length = random.randint(50, 200)

        for i in range(num_ticks):
            # Change regime periodically
            if ticks_in_regime >= regime_length:
                self.volatility_regime = random.choice(list(VolatilityRegime))
                self.trend_direction = random.choice(list(TrendDirection))
                ticks_in_regime = 0
                regime_length = random.randint(50, 200)

            # Generate price movement
            drift = self.trend_direction.value * 0.0002
            volatility = self.volatility_regime.value / math.sqrt(252 * 78)  # Annualize
            random_shock = random.gauss(drift, volatility)

            # Apply gap at market open (every 78 ticks = 1 trading day)
            if i > 0 and i % 78 == 0:
                gap = random.uniform(-0.03, 0.03)  # ±3% gap
                current_price *= (1 + gap)

            # Price movement
            current_price *= (1 + random_shock)

            # Bid-ask spread (realistic for LSE leveraged ETPs: 0.01-0.05%)
            spread = base_price * random.uniform(0.0001, 0.0005)
            bid = current_price - spread / 2
            ask = current_price + spread / 2

            # Volume (inversely correlated with volatility)
            base_volume = 1_000_000
            if self.volatility_regime == VolatilityRegime.LOW:
                volume = int(base_volume * random.uniform(0.5, 1.5))
            elif self.volatility_regime == VolatilityRegime.NORMAL:
                volume = int(base_volume * random.uniform(0.8, 1.5))
            elif self.volatility_regime == VolatilityRegime.HIGH:
                volume = int(base_volume * random.uniform(1.5, 3.0))
            else:  # EXTREME
                volume = int(base_volume * random.uniform(2.0, 5.0))

            # Volume spike (5% chance)
            if random.random() < 0.05:
                volume = int(volume * random.uniform(2.0, 5.0))

            tick = Tick(
                timestamp=current_time,
                symbol=symbol,
                bid=bid,
                ask=ask,
                last=(bid + ask) / 2,
                volume=volume,
                slippage=random.uniform(0.0, 0.01),  # 0-1bp slippage
            )

            ticks.append(tick)
            current_time += tick_interval
            ticks_in_regime += 1

        return ticks

    def generate_ohlcv_bars(
        self,
        symbol: str,
        num_bars: int = 20,
        base_price: float = 100.0,
    ) -> List[Dict]:
        """
        Generate OHLCV bars (1-hour aggregation)

        Args:
            symbol: LSE ticker
            num_bars: Number of bars
            base_price: Starting price

        Returns:
            List of OHLCV dictionaries
        """
        bars = []
        ticks = self.generate_lse_ticks(symbol, num_ticks=num_bars * 12, base_price=base_price)

        # Aggregate into bars (12 ticks per bar = 1 hour)
        for bar_idx in range(num_bars):
            bar_ticks = ticks[bar_idx * 12 : (bar_idx + 1) * 12]

            if not bar_ticks:
                continue

            prices = [t.last for t in bar_ticks]
            volumes = [t.volume for t in bar_ticks]

            bar = {
                "timestamp": bar_ticks[0].timestamp,
                "symbol": symbol,
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1],
                "volume": sum(volumes),
                "volatility_regime": self.volatility_regime.name,
                "trend": self.trend_direction.name,
            }

            bars.append(bar)

        return bars

    def calculate_atr(self, bars: List[Dict], period: int = 14) -> List[float]:
        """
        Calculate Average True Range for bars

        Args:
            bars: List of OHLCV bars
            period: ATR period

        Returns:
            List of ATR values
        """
        atr_values = []

        for i in range(len(bars)):
            if i < period:
                atr_values.append(0.0)
                continue

            true_ranges = []
            for j in range(i - period + 1, i + 1):
                bar = bars[j]

                # True range = max(high - low, |high - prev_close|, |low - prev_close|)
                prev_close = bars[j - 1]["close"] if j > 0 else bar["close"]
                tr = max(
                    bar["high"] - bar["low"],
                    abs(bar["high"] - prev_close),
                    abs(bar["low"] - prev_close),
                )
                true_ranges.append(tr)

            atr = sum(true_ranges) / len(true_ranges)
            atr_values.append(atr)

        return atr_values

    def calculate_slippage(
        self,
        order_size: int,
        market_volume: int,
        market_spread: float,
    ) -> float:
        """
        Calculate slippage based on order size and market conditions

        Args:
            order_size: Number of shares to buy/sell
            market_volume: Current market volume
            market_spread: Current bid-ask spread

        Returns:
            Slippage percentage
        """
        # Participation rate: order size as % of volume
        participation = order_size / market_volume if market_volume > 0 else 0

        # Base slippage from spread
        base_slippage = market_spread / 2

        # Additional slippage from large orders
        if participation > 0.1:
            base_slippage *= (1 + participation * 5)  # Non-linear penalty

        return min(base_slippage, 0.01)  # Cap at 1%


class ScenarioGenerator:
    """Generates specific market scenarios"""

    def __init__(self):
        self.gen = TickDataGenerator()

    def trending_market(self, symbol: str, num_bars: int = 20) -> List[Dict]:
        """Strong uptrend with low volatility"""
        self.gen.volatility_regime = VolatilityRegime.LOW
        self.gen.trend_direction = TrendDirection.UP
        return self.gen.generate_ohlcv_bars(symbol, num_bars)

    def ranging_market(self, symbol: str, num_bars: int = 20) -> List[Dict]:
        """Sideways market with medium volatility"""
        self.gen.volatility_regime = VolatilityRegime.NORMAL
        self.gen.trend_direction = TrendDirection.SIDEWAYS
        return self.gen.generate_ohlcv_bars(symbol, num_bars)

    def volatile_market(self, symbol: str, num_bars: int = 20) -> List[Dict]:
        """High volatility with mixed trend"""
        self.gen.volatility_regime = VolatilityRegime.HIGH
        self.gen.trend_direction = random.choice([TrendDirection.UP, TrendDirection.DOWN])
        return self.gen.generate_ohlcv_bars(symbol, num_bars)

    def gap_down_scenario(self, symbol: str, gap_size: float = -0.05) -> List[Dict]:
        """Market gaps down overnight and recovers"""
        bars = self.gen.generate_ohlcv_bars(symbol, num_bars=20, base_price=100.0)

        # Apply gap to first bar of "next day"
        if len(bars) > 10:
            gap_idx = 10
            gap_multiplier = 1 + gap_size
            for i in range(gap_idx, len(bars)):
                bars[i]["open"] *= gap_multiplier
                bars[i]["high"] *= gap_multiplier
                bars[i]["low"] *= gap_multiplier
                bars[i]["close"] *= gap_multiplier

        return bars

    def flash_crash_scenario(self, symbol: str) -> List[Dict]:
        """Sudden crash and quick recovery"""
        bars = self.gen.generate_ohlcv_bars(symbol, num_bars=20)

        # Crash at bar 10
        crash_idx = 10
        recovery_idx = 15

        for i in range(crash_idx, recovery_idx):
            decline_pct = (recovery_idx - i) / (recovery_idx - crash_idx) * 0.10
            bars[i]["open"] *= (1 - decline_pct)
            bars[i]["high"] *= (1 - decline_pct)
            bars[i]["low"] *= (1 - decline_pct)
            bars[i]["close"] *= (1 - decline_pct)

        # Recovery
        for i in range(recovery_idx, len(bars)):
            recovery_pct = (i - recovery_idx) / (len(bars) - recovery_idx) * 0.10
            bars[i]["open"] *= (1 + recovery_pct)
            bars[i]["high"] *= (1 + recovery_pct)
            bars[i]["low"] *= (1 + recovery_pct)
            bars[i]["close"] *= (1 + recovery_pct)

        return bars


def main():
    """Demo: Generate and display market data"""
    gen = TickDataGenerator()
    scenario_gen = ScenarioGenerator()

    print("Generating LSE tick data...")
    ticks = gen.generate_lse_ticks("QQQ3.L", num_ticks=100)
    print(f"Generated {len(ticks)} ticks")
    print(f"Price range: {min(t.last for t in ticks):.2f} - {max(t.last for t in ticks):.2f}")

    print("\nGenerating OHLCV bars...")
    bars = gen.generate_ohlcv_bars("TSL3.L", num_bars=20)
    for i, bar in enumerate(bars[:5]):
        print(
            f"Bar {i}: O={bar['open']:.2f} H={bar['high']:.2f} "
            f"L={bar['low']:.2f} C={bar['close']:.2f} V={bar['volume']}"
        )

    print("\nGenerating scenario: Gap down...")
    gap_scenario = scenario_gen.gap_down_scenario("NVD3.L")
    print(f"Scenario bars: {len(gap_scenario)}")


if __name__ == "__main__":
    main()
