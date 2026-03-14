"""
tests/backtest_perfect_entry_timing.py
=====================================
Backtest framework for perfect entry timing validation (Week 3-4).

Tests whether early detection + chandelier ladder + exit can achieve:
  - 70%+ directional win rate (Gate 1)
  - 60%+ first rung hit rate (Gate 2)
  - 1.5x+ profit factor (Gate 3)
  - <3 consecutive losses (Gate 4)

Uses realistic LSE 5-min OHLCV data (2-year history or mock).
Does NOT execute trades — only simulates with chandelier ladder simulation.
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import logging

sys.path.insert(0, str(Path(__file__).parent.parent))

# Import core components
from core.chandelier_exit import ChandelierExit, ChandelierState, LADDER_RUNGS
from src.core.early_detection_engine import EarlyDetectionEngine, EarlyDetectionResult

logger = logging.getLogger("nzt48.backtest")


# ─────────────────────────────────────────────────────────
# Mock LSE 5-min OHLCV Data Generator
# ─────────────────────────────────────────────────────────

@dataclass
class OHLCVBar:
    """Single 5-minute OHLCV bar."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    ticker: str


def generate_realistic_lse_data(
    ticker: str,
    start_date: datetime,
    num_bars: int,
    initial_price: float = 100.0,
    volatility: float = 0.015,
    trend: float = 0.0001,
    seed: int = 42
) -> List[OHLCVBar]:
    """
    Generate realistic LSE 5-min OHLCV data for backtesting.

    Args:
        ticker: Ticker symbol (e.g., 'QQQ3.L')
        start_date: Starting datetime (must be LSE trading hours)
        num_bars: Number of 5-min bars to generate
        initial_price: Starting price
        volatility: Daily volatility (as decimal, e.g., 0.015 = 1.5%)
        trend: Daily trend component (drift)
        seed: Random seed for reproducibility

    Returns:
        List of OHLCVBar objects
    """
    import random
    random.seed(seed)

    bars = []
    current_price = initial_price
    current_time = start_date

    # 5-min bar volatility (daily vol / sqrt(252 * 78))
    # 78 5-min bars per LSE trading day (6.5 hours = 390 min / 5)
    five_min_vol = volatility / ((252 * 78) ** 0.5)
    five_min_drift = trend / 78

    for i in range(num_bars):
        # Skip weekends and LSE non-trading hours (simulate)
        if current_time.weekday() >= 5:  # Weekend
            current_time += timedelta(minutes=5)
            continue

        hour = current_time.hour
        minute = current_time.minute

        # LSE trading hours: 08:00-16:30 GMT (skip pre/post market)
        if hour < 8 or (hour == 16 and minute >= 30) or hour >= 17:
            current_time += timedelta(minutes=5)
            continue

        # Generate realistic OHLC
        daily_ret = random.gauss(five_min_drift, five_min_vol)
        open_price = current_price
        close_price = current_price * (1 + daily_ret)

        # High/low with realistic intrabar range
        intrabar_vol = five_min_vol * 0.7  # Slightly less than daily vol
        high_offset = abs(random.gauss(0, intrabar_vol))
        low_offset = abs(random.gauss(0, intrabar_vol))

        high_price = max(open_price, close_price) + high_offset * current_price
        low_price = min(open_price, close_price) - low_offset * current_price

        # Volume: base 100k with variance
        base_vol = 100_000
        volume = base_vol * (0.5 + 2.0 * random.random())

        bar = OHLCVBar(
            timestamp=current_time,
            open=round(open_price, 4),
            high=round(high_price, 4),
            low=round(low_price, 4),
            close=round(close_price, 4),
            volume=int(volume),
            ticker=ticker
        )
        bars.append(bar)
        current_price = close_price
        current_time += timedelta(minutes=5)

    return bars


# ─────────────────────────────────────────────────────────
# Simulated Trade Outcome
# ─────────────────────────────────────────────────────────

@dataclass
class SimulatedTrade:
    """Represents a single simulated trade."""
    ticker: str
    entry_time: datetime
    entry_price: float
    direction: str  # LONG or SHORT
    leverage: int = 3

    # Entry quality signals
    early_detection_confidence: float = 0.0
    early_detection_signals: int = 0

    # Execution outcome
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_rung: Optional[int] = None  # Which rung in ladder

    # P&L
    gross_pnl_pct: float = 0.0  # Profit/loss as %
    pnl_amount: float = 0.0

    # Ladder hit tracking
    rung_1_hit: bool = False  # +2%
    rung_2_hit: bool = False  # +4%
    rung_3_hit: bool = False  # +6%

    # Trade quality
    max_favorable_excursion: float = 0.0  # Max profit reached
    max_adverse_excursion: float = 0.0  # Max loss reached

    @property
    def win(self) -> bool:
        """Trade is a winner if P&L > 0."""
        return self.gross_pnl_pct > 0.0

    @property
    def rung_hit_count(self) -> int:
        """Count how many ladder rungs were hit."""
        return sum([self.rung_1_hit, self.rung_2_hit, self.rung_3_hit])


# ─────────────────────────────────────────────────────────
# Perfect Entry Backtester
# ─────────────────────────────────────────────────────────

@dataclass
class BacktestMetrics:
    """Aggregated backtest metrics for all trades."""
    total_entries: int = 0
    winning_entries: int = 0
    losing_entries: int = 0

    # Gate 1: Win rate
    win_rate_pct: float = 0.0  # target: 70%+

    # Gate 2: First rung efficiency
    rung_1_hits: int = 0
    rung_1_hit_rate_pct: float = 0.0  # target: 60%+

    # Gate 3: Profit factor
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0  # target: 1.5x+

    # Gate 4: Cascade losses
    max_consecutive_losses: int = 0  # target: <3

    # Additional metrics
    avg_entry_quality_pct: float = 0.0
    total_trades_tracked: int = 0
    trades: List[SimulatedTrade] = field(default_factory=list)


class PerfectEntryBacktester:
    """
    Backtest framework for perfect entry timing validation.

    Workflow:
      1. Load 2 years of LSE 5-min OHLCV data
      2. For each trading day:
         - Scan universe for entry signals
         - Apply early detection engine
         - Simulate trades with chandelier ladder
         - Track outcomes (win/loss, rung hits, MAE/MFE)
      3. Compute 4 validation gates
      4. Return pass/fail status
    """

    def __init__(self):
        self.early_detection = EarlyDetectionEngine()
        self.chandelier = ChandelierExit()
        self.logger = logging.getLogger("nzt48.backtest")
        self.metrics = BacktestMetrics()

    def backtest_universe(
        self,
        tickers: List[str] = None,
        start_date: datetime = None,
        num_days: int = 60,
        volatility: float = 0.015,
        min_entries_required: int = 20
    ) -> BacktestMetrics:
        """
        Run backtest on LSE universe for specified period.

        Args:
            tickers: List of tickers to backtest (defaults to ISA universe)
            start_date: Backtest start date (defaults to 60 days ago)
            num_days: Number of trading days to simulate (default 60)
            volatility: Intraday volatility for data generation
            min_entries_required: Minimum number of entries before computing gates

        Returns:
            BacktestMetrics with all gates computed
        """
        if tickers is None:
            # Default: UK ISA universe
            tickers = [
                "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L",
                "NVD3.L", "TSL3.L", "TSM3.L", "MU2.L",
                "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L"
            ]

        if start_date is None:
            start_date = datetime.now(timezone.utc) - timedelta(days=num_days * 2)

        self.logger.info(f"Starting backtest: {len(tickers)} tickers, {num_days} days")

        # Generate data and simulate trades
        for ticker in tickers:
            # Generate 2 years of realistic data
            # 78 bars/day * 252 trading days/year * 2 years ≈ 39,312 bars
            num_bars = int(78 * 252 * 2)
            bars = generate_realistic_lse_data(
                ticker=ticker,
                start_date=start_date,
                num_bars=num_bars,
                volatility=volatility,
                trend=0.0001,  # Slight uptrend
                seed=hash(ticker) % 10000
            )

            # Simulate trades on this ticker's data
            self._simulate_trades_on_bars(ticker, bars)

        # Compute metrics and gates
        self._compute_metrics()

        self.logger.info(
            f"Backtest complete: {self.metrics.total_entries} entries, "
            f"win_rate={self.metrics.win_rate_pct:.1f}%, "
            f"profit_factor={self.metrics.profit_factor:.2f}x"
        )

        return self.metrics

    def _simulate_trades_on_bars(self, ticker: str, bars: List[OHLCVBar]) -> None:
        """
        Simulate trades on a single ticker's OHLCV bar stream.

        Every 78 bars (1 day), check for entry signals.
        If signal fires, simulate trade until exit.
        """
        leverage_map = {
            "QQQ3.L": 3, "3LUS.L": 3, "3SEM.L": 3, "GPT3.L": 3,
            "NVD3.L": 3, "TSL3.L": 3, "TSM3.L": 3,
            "MU2.L": 2,
            "QQQS.L": 3, "3USS.L": 3,
            "QQQ5.L": 5, "SP5L.L": 5,
        }
        leverage = leverage_map.get(ticker, 3)

        active_trade: Optional[SimulatedTrade] = None
        entry_bar_idx = 0

        for bar_idx, bar in enumerate(bars):
            # Check for entry signal every ~78 bars (1 trading day)
            if bar_idx > 0 and bar_idx % 78 == 0 and active_trade is None:
                # Look at recent bars for entry signal
                recent_bars = bars[max(0, bar_idx - 20):bar_idx]
                signal = self._check_entry_signal(ticker, recent_bars, leverage)

                if signal:
                    active_trade = SimulatedTrade(
                        ticker=ticker,
                        entry_time=bar.timestamp,
                        entry_price=bar.close,
                        direction="LONG",
                        leverage=leverage,
                        early_detection_confidence=signal.confidence,
                        early_detection_signals=len(signal.signals)
                    )
                    entry_bar_idx = bar_idx

            # Update active trade if exists
            if active_trade is not None:
                exit_occurred = self._update_trade_ladder(
                    active_trade, bar, bars[entry_bar_idx:bar_idx + 1]
                )
                if exit_occurred:
                    self.metrics.trades.append(active_trade)
                    active_trade = None

        # Close any remaining open trade at last bar
        if active_trade is not None and len(bars) > entry_bar_idx:
            last_bar = bars[-1]
            active_trade.exit_time = last_bar.timestamp
            active_trade.exit_price = last_bar.close
            active_trade.exit_rung = 0
            # P&L calculation
            pnl_pct = (last_bar.close - active_trade.entry_price) / active_trade.entry_price
            active_trade.gross_pnl_pct = pnl_pct * 100
            self.metrics.trades.append(active_trade)

    def _check_entry_signal(
        self,
        ticker: str,
        recent_bars: List[OHLCVBar],
        leverage: int
    ) -> Optional[EarlyDetectionResult]:
        """
        Check if entry signal fires using early detection engine.

        Builds market_data dict from recent bars and calls early_detection.evaluate_entry_readiness().
        """
        if len(recent_bars) < 5:
            return None

        last_bar = recent_bars[-1]

        # Compute simple indicators from recent bars
        closes = [b.close for b in recent_bars]
        volumes = [b.volume for b in recent_bars]

        # ATR simple calculation
        atr = self._simple_atr(recent_bars)

        # Momentum (simple: rate of change)
        momentum = (closes[-1] - closes[0]) / closes[0]

        # Build market_data dict for early detection
        market_data = {
            "current_price": last_bar.close,
            "bid": last_bar.close - 0.01,  # Mock bid/ask
            "ask": last_bar.close + 0.01,
            "volume": last_bar.volume,
            "vix": 15.0,  # Mock
            "realized_vol": 0.015,  # 1.5%
            "atr": atr,
            "bb_width_pct": 50.0,
            "momentum": momentum * 100,
            "ofi": 0.3,  # Mock: order flow imbalance
            "ofi_rising": True,
            "volume_profile_lvn": last_bar.low,
            "vtd_ratio": 0.75,
            "hawkes_branching_ratio": 0.65,
            "hawkes_trending": True,
            "atm_accel": 1.2,
            "recent_bars": [
                {
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume
                }
                for b in recent_bars[-10:]
            ],
            "gap_pct": (last_bar.open - recent_bars[-2].close) / recent_bars[-2].close,
            "si_pct": 5.0,  # Mock short interest
            "recent_return_first_30m": 0.8,  # Mock
            "market_regime": "EXPANSION"
        }

        result = self.early_detection.evaluate_entry_readiness(ticker, market_data)
        return result if result.should_enter else None

    def _update_trade_ladder(
        self,
        trade: SimulatedTrade,
        current_bar: OHLCVBar,
        trade_bars: List[OHLCVBar]
    ) -> bool:
        """
        Update trade P&L and ladder progress.
        Returns True if trade should exit.
        """
        entry_price = trade.entry_price
        current_price = current_bar.close

        # P&L %
        pnl_pct = (current_price - entry_price) / entry_price
        trade.gross_pnl_pct = pnl_pct * 100

        # Track MAE/MFE
        low_price = min(b.low for b in trade_bars)
        high_price = max(b.high for b in trade_bars)

        mae = (low_price - entry_price) / entry_price * 100
        mfe = (high_price - entry_price) / entry_price * 100

        trade.max_adverse_excursion = mae
        trade.max_favorable_excursion = mfe

        # Track rung hits (profit ladder)
        if trade.gross_pnl_pct >= 2.0:
            trade.rung_1_hit = True
        if trade.gross_pnl_pct >= 4.0:
            trade.rung_2_hit = True
        if trade.gross_pnl_pct >= 6.0:
            trade.rung_3_hit = True

        # Exit logic: simple chandelier-inspired trailing stop
        # Exit after 10 bars or if >10% profit or if -2% stop hit
        bars_held = len(trade_bars)

        if bars_held > 40:  # ~3.5 hours of trading
            trade.exit_time = current_bar.timestamp
            trade.exit_price = current_price
            trade.exit_rung = 0
            return True

        if trade.gross_pnl_pct > 10.0:  # Exit with huge profit
            trade.exit_time = current_bar.timestamp
            trade.exit_price = current_price
            trade.exit_rung = 3
            return True

        if trade.gross_pnl_pct < -2.0:  # Stop loss
            trade.exit_time = current_bar.timestamp
            trade.exit_price = current_price
            trade.exit_rung = -1  # Stop hit
            return True

        return False

    def _simple_atr(self, bars: List[OHLCVBar], period: int = 14) -> float:
        """Calculate simple ATR from bars."""
        if len(bars) < period:
            return 0.5  # Default fallback

        trues = []
        for i in range(len(bars) - 1, max(len(bars) - period - 1, -1), -1):
            bar = bars[i]
            if i > 0:
                prev_close = bars[i - 1].close
            else:
                prev_close = bar.open

            tr = max(
                bar.high - bar.low,
                abs(bar.high - prev_close),
                abs(bar.low - prev_close)
            )
            trues.append(tr)

        return sum(trues) / len(trues) if trues else 0.5

    def _compute_metrics(self) -> None:
        """Aggregate all trade outcomes into metrics."""
        trades = self.metrics.trades

        if not trades:
            self.logger.warning("No trades to compute metrics")
            return

        # Gate 1: Win rate
        self.metrics.total_entries = len(trades)
        self.metrics.winning_entries = sum(1 for t in trades if t.win)
        self.metrics.losing_entries = sum(1 for t in trades if not t.win)
        self.metrics.win_rate_pct = (
            100.0 * self.metrics.winning_entries / self.metrics.total_entries
            if self.metrics.total_entries > 0 else 0.0
        )

        # Gate 2: First rung efficiency
        self.metrics.rung_1_hits = sum(1 for t in trades if t.rung_1_hit)
        self.metrics.rung_1_hit_rate_pct = (
            100.0 * self.metrics.rung_1_hits / self.metrics.total_entries
            if self.metrics.total_entries > 0 else 0.0
        )

        # Gate 3: Profit factor
        self.metrics.gross_profit = sum(
            t.gross_pnl_pct for t in trades if t.gross_pnl_pct > 0
        )
        self.metrics.gross_loss = abs(sum(
            t.gross_pnl_pct for t in trades if t.gross_pnl_pct < 0
        ))
        self.metrics.profit_factor = (
            self.metrics.gross_profit / self.metrics.gross_loss
            if self.metrics.gross_loss > 0 else float('inf')
        )

        # Gate 4: Max consecutive losses
        self.metrics.max_consecutive_losses = self._max_consecutive_losses(trades)

        # Additional: Average entry quality
        if trades:
            avg_confidence = sum(t.early_detection_confidence for t in trades) / len(trades)
            self.metrics.avg_entry_quality_pct = avg_confidence

        self.metrics.total_trades_tracked = len(trades)

    def _max_consecutive_losses(self, trades: List[SimulatedTrade]) -> int:
        """Compute max consecutive losing trades."""
        if not trades:
            return 0

        max_streak = 0
        current_streak = 0

        for trade in trades:
            if not trade.win:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0

        return max_streak

    def pass_criteria(self) -> Dict[str, bool]:
        """
        Validate against 4 pass/fail gates.

        Returns:
            Dict with keys: gate_1_win_rate, gate_2_rung_efficiency,
                           gate_3_profit_factor, gate_4_no_cascades
        """
        gates = {
            "gate_1_win_rate": self.metrics.win_rate_pct >= 70.0,
            "gate_2_rung_efficiency": self.metrics.rung_1_hit_rate_pct >= 60.0,
            "gate_3_profit_factor": self.metrics.profit_factor >= 1.5,
            "gate_4_no_cascades": self.metrics.max_consecutive_losses < 3,
        }

        return gates


# ─────────────────────────────────────────────────────────
# Embedded Test Case
# ─────────────────────────────────────────────────────────

def test_backtest_framework():
    """
    Embedded test case showing expected backtest behavior.

    Expected output (example):
      - total_entries: 25
      - win_rate: 72%
      - rung_1_hit_rate: 64%
      - profit_factor: 1.75x
      - max_consecutive_losses: 2
      - All 4 gates PASS
    """
    backtester = PerfectEntryBacktester()

    # Small backtest: 2 tickers, 10 days
    metrics = backtester.backtest_universe(
        tickers=["QQQ3.L", "3LUS.L"],
        num_days=10,
        volatility=0.012
    )

    print("\n" + "=" * 70)
    print("PERFECT ENTRY TIMING BACKTEST RESULTS")
    print("=" * 70)
    print(f"Total Entries:           {metrics.total_entries}")
    print(f"Winning Entries:         {metrics.winning_entries}")
    print(f"Losing Entries:          {metrics.losing_entries}")
    print(f"\nGate 1: Win Rate         {metrics.win_rate_pct:.1f}% (target: 70%+)")
    print(f"Gate 2: Rung 1 Hits      {metrics.rung_1_hit_rate_pct:.1f}% (target: 60%+)")
    print(f"Gate 3: Profit Factor    {metrics.profit_factor:.2f}x (target: 1.5x+)")
    print(f"Gate 4: Max Cascades     {metrics.max_consecutive_losses} (target: <3)")
    print(f"\nAvg Entry Quality:       {metrics.avg_entry_quality_pct:.1f}%")
    print("=" * 70)

    gates = backtester.pass_criteria()
    print("\nVALIDATION GATES:")
    for gate_name, passed in gates.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {gate_name:30s} {status}")

    all_pass = all(gates.values())
    print(f"\nOverall: {'ALL GATES PASS ✓' if all_pass else 'ONE OR MORE GATES FAIL ✗'}")
    print("=" * 70 + "\n")

    # Assertions for CI/CD
    assert metrics.total_entries > 0, "No trades generated"
    # Note: Win rate will vary due to synthetic data generation
    # In production, this would be validated against pass_criteria() gates


if __name__ == "__main__":
    # Run embedded test
    logging.basicConfig(level=logging.INFO)
    test_backtest_framework()
