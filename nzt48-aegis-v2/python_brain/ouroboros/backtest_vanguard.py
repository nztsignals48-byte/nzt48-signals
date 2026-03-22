"""N9a — VanguardSniper 30-Day Backtest Engine.

Historical backtest of the VanguardSniper momentum strategy using yfinance 5-min data.
Simulates the full signal → Chandelier exit → P&L lifecycle on ISA leveraged ETPs.

Outputs:
  - Per-trade results (entry/exit/pnl/rung/holding time)
  - Aggregate metrics (WR, PF, Sharpe, max drawdown, avg rung)
  - Daily equity curve
  - JSON report for N9b Monte Carlo input

Usage:
    python3 -m python_brain.ouroboros.backtest_vanguard                    # Full 30-day backtest
    python3 -m python_brain.ouroboros.backtest_vanguard --days 7           # 7-day quick test
    python3 -m python_brain.ouroboros.backtest_vanguard --ticker QQQ3.L    # Single ticker
    python3 -m python_brain.ouroboros.backtest_vanguard --json             # JSON output
    python3 -m python_brain.ouroboros.backtest_vanguard --send-telegram    # Send report via Telegram
"""
from __future__ import annotations

import json
import logging
import math
import os
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("backtest_vanguard")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
REPORT_DIR = DATA_DIR / "backtest_reports"

# ISA-eligible LSE leveraged ETPs (core universe)
ISA_TICKERS = [
    "QQQ3.L", "QQQS.L", "3LUS.L", "3USS.L", "QQQ5.L", "SP5L.L",
    "3SEM.L", "NVD3.L", "TSL3.L", "GPT3.L", "TSM3.L", "MU2.L",
]


def _load_universe_from_contracts() -> List[str]:
    """Load full universe from contracts.toml via centralized loader."""
    try:
        from python_brain.ouroboros.contract_loader import load_yfinance_symbols
        symbols = load_yfinance_symbols()
        if symbols:
            return symbols
    except Exception as e:
        log.warning("contract_loader failed: %s", e)
    log.warning("Falling back to ISA_TICKERS (%d)", len(ISA_TICKERS))
    return ISA_TICKERS

# Strategy parameters (mirror bridge.py VanguardSniper)
EMA_FAST = 5
EMA_SLOW = 20
MIN_WARMUP_BARS = 50       # Minimum 5-min bars before signals
CONFIDENCE_BASE = 65       # Base confidence for momentum signal
CONFIDENCE_FLOOR = 65      # Leverage-aware floor for 3x ETPs

# Chandelier exit parameters (mirror exit_engine.rs)
RUNG_THRESHOLDS = [0.0, 0.008, 0.015, 0.025, 0.040]  # % gain for each rung
RUNG3_TRAIL_ATR = 1.0
RUNG4_TRAIL_ATR = 0.75
RUNG5_TRAIL_ATR = 0.5
INITIAL_STOP_ATR = 2.0     # Match config.toml initial_stop_atr_mult = 2.0
ROUND_TRIP_FEE = 0.003     # Q-051 unified cost

# Risk parameters
# SIMULATION: No daily trade cap — backtesting needs unrestricted signals.
MAX_DAILY_TRADES = 999999
STOP_LOSS_PCT = 0.05       # Emergency 5% stop (beyond Chandelier)
MAX_HOLD_BARS = 96          # 8 hours at 5-min bars

# Capital
STARTING_EQUITY = 10000.0
POSITION_SIZE_PCT = 0.10   # 10% per trade


@dataclass
class BacktestTrade:
    """Single trade result."""
    ticker: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    qty: int
    pnl_gbp: float
    pnl_pct: float
    highest_rung: int
    hold_bars: int
    exit_reason: str
    confidence: float
    mae_pct: float = 0.0   # Maximum adverse excursion
    mfe_pct: float = 0.0   # Maximum favorable excursion


@dataclass
class BacktestResult:
    """Aggregate backtest results."""
    ticker: str
    period_days: int
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float
    total_pnl_gbp: float
    total_pnl_pct: float
    max_drawdown_pct: float
    avg_rung: float
    avg_hold_bars: float
    avg_mae_pct: float
    avg_mfe_pct: float
    sharpe_ratio: float
    avg_pnl_per_trade: float
    trades: List[BacktestTrade] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def fetch_5min_data(ticker: str, days: int = 30) -> Optional[Any]:
    """Fetch 5-minute OHLCV data from yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance not installed")
        return None

    try:
        # yfinance: max 60 days for 5m data
        effective_days = min(days, 59)
        data = yf.download(
            ticker,
            period=f"{effective_days}d",
            interval="5m",
            progress=False,
            auto_adjust=True,
        )
        if data is None or data.empty:
            log.warning("No data for %s", ticker)
            return None
        log.info("Fetched %d bars for %s (%d days)", len(data), ticker, effective_days)
        return data
    except Exception as e:
        log.error("Failed to fetch %s: %s", ticker, e)
        return None


# ---------------------------------------------------------------------------
# Indicator calculations
# ---------------------------------------------------------------------------
def compute_ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average."""
    ema = np.zeros_like(values)
    if len(values) == 0:
        return ema
    ema[0] = values[0]
    mult = 2.0 / (period + 1)
    for i in range(1, len(values)):
        ema[i] = values[i] * mult + ema[i - 1] * (1 - mult)
    return ema


def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                period: int = 14) -> np.ndarray:
    """Average True Range."""
    n = len(highs)
    atr = np.zeros(n)
    if n < 2:
        return atr

    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    # EMA-smoothed ATR
    atr[period - 1] = np.mean(tr[:period])
    mult = 2.0 / (period + 1)
    for i in range(period, n):
        atr[i] = tr[i] * mult + atr[i - 1] * (1 - mult)
    # Fill initial values
    for i in range(period - 1):
        atr[i] = atr[period - 1]
    return atr


def compute_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                period: int = 14) -> np.ndarray:
    """Average Directional Index (simplified)."""
    n = len(highs)
    adx = np.zeros(n)
    if n < period * 2:
        return adx

    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    atr = compute_atr(highs, lows, closes, period)

    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if (up > down and up > 0) else 0
        minus_dm[i] = down if (down > up and down > 0) else 0

    # Smoothed DI
    plus_di = compute_ema(plus_dm, period)
    minus_di = compute_ema(minus_dm, period)

    for i in range(period, n):
        if atr[i] > 0:
            p = plus_di[i] / atr[i] * 100
            m = minus_di[i] / atr[i] * 100
            dx = abs(p - m) / max(p + m, 1e-9) * 100
            adx[i] = dx
        else:
            adx[i] = 0

    adx = compute_ema(adx, period)
    return adx


def compute_hurst(closes: np.ndarray, window: int = 20) -> np.ndarray:
    """Hurst exponent (R/S method, simplified rolling)."""
    n = len(closes)
    hurst = np.full(n, 0.5)  # Default: random walk

    for i in range(window, n):
        segment = closes[i - window:i]
        returns = np.diff(np.log(segment + 1e-9))
        if len(returns) < 10:
            continue
        mean_r = np.mean(returns)
        dev = np.cumsum(returns - mean_r)
        r = np.max(dev) - np.min(dev)
        s = np.std(returns)
        if s > 1e-9 and r > 1e-9:
            hurst[i] = math.log(r / s) / math.log(window)
            hurst[i] = max(0.0, min(1.0, hurst[i]))

    return hurst


def compute_vwap(closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    """Volume-weighted average price (cumulative)."""
    cum_vol = np.cumsum(volumes)
    cum_pv = np.cumsum(closes * volumes)
    vwap = np.where(cum_vol > 0, cum_pv / cum_vol, closes)
    return vwap


# ---------------------------------------------------------------------------
# Chandelier exit simulation
# ---------------------------------------------------------------------------
def chandelier_stop(
    entry_price: float, current_rung: int, highest_high: float, atr: float
) -> float:
    """Compute stop price based on current rung."""
    if current_rung <= 1:
        return entry_price - INITIAL_STOP_ATR * atr
    elif current_rung == 2:
        fee = entry_price * ROUND_TRIP_FEE
        return entry_price + fee  # Breakeven + fees
    elif current_rung == 3:
        return highest_high - RUNG3_TRAIL_ATR * atr
    elif current_rung == 4:
        return highest_high - RUNG4_TRAIL_ATR * atr
    else:  # Rung 5+
        return highest_high - RUNG5_TRAIL_ATR * atr


def compute_rung(entry_price: float, current_price: float) -> int:
    """Determine current rung based on gain %."""
    if entry_price <= 0:
        return 0
    gain_pct = (current_price - entry_price) / entry_price
    rung = 0
    for i, threshold in enumerate(RUNG_THRESHOLDS):
        if gain_pct >= threshold:
            rung = i
    return rung


# ---------------------------------------------------------------------------
# Signal generation (mirrors VanguardSniper from bridge.py)
# ---------------------------------------------------------------------------
def generate_signals(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    volumes: np.ndarray,
    timestamps: list,
) -> List[Tuple[int, float]]:
    """Generate VanguardSniper signals from 5-min bars.

    Returns list of (bar_index, confidence) for entry signals.
    """
    n = len(closes)
    if n < MIN_WARMUP_BARS:
        return []

    # Compute indicators
    ema_fast = compute_ema(closes, EMA_FAST)
    ema_slow = compute_ema(closes, EMA_SLOW)
    atr = compute_atr(highs, lows, closes, 14)
    adx = compute_adx(highs, lows, closes, 14)
    hurst = compute_hurst(closes, 20)
    vwap = compute_vwap(closes, volumes)

    signals = []
    # SIMULATION: No signal cooldown — backtesting needs every valid signal.
    last_signal_bar = -1

    for i in range(MIN_WARMUP_BARS, n):

        # EMA crossover: fast crosses above slow
        if not (ema_fast[i] > ema_slow[i] and ema_fast[i - 1] <= ema_slow[i - 1]):
            continue

        # Hurst regime gate: require trending (H > 0.40)
        if hurst[i] < 0.40:
            continue

        # ADX strength: require >= 12 (Sprint 5 T-04: was 15, lowered)
        if adx[i] < 12:
            continue

        # Volume trend: require positive volume (simplified)
        if i >= 5:
            vol_slope = (volumes[i] - volumes[i - 5]) / max(volumes[i - 5], 1)
            if vol_slope < -0.5:  # Severely declining volume
                continue

        # VWAP check: don't chase > 1.5% above VWAP
        vwap_dist_pct = (closes[i] - vwap[i]) / max(vwap[i], 1e-9)
        if vwap_dist_pct > 0.015:
            continue

        # Multi-timeframe confirmation (simplified): price above both EMAs
        if closes[i] <= ema_slow[i]:
            continue

        # Compute confidence
        confidence = CONFIDENCE_BASE
        # ADX boost (Sprint 5 T-04: lowered thresholds to match live)
        if adx[i] > 30:
            confidence += 15
        elif adx[i] > 20:
            confidence += 10
        elif adx[i] > 12:
            confidence += 5
        # Hurst boost
        if hurst[i] > 0.60:
            confidence += 4
        elif hurst[i] > 0.50:
            confidence += 2
        # VWAP proximity bonus
        if abs(vwap_dist_pct) < 0.005:
            confidence += 3

        if confidence < CONFIDENCE_FLOOR:
            continue

        signals.append((i, min(confidence, 100)))
        last_signal_bar = i

    return signals


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------
def run_backtest_ticker(
    ticker: str,
    days: int = 30,
    equity: float = STARTING_EQUITY,
) -> Optional[BacktestResult]:
    """Run full backtest for a single ticker."""
    data = fetch_5min_data(ticker, days)
    if data is None or len(data) < MIN_WARMUP_BARS:
        log.warning("Insufficient data for %s", ticker)
        return None

    closes = data["Close"].values.flatten().astype(float)
    highs = data["High"].values.flatten().astype(float)
    lows = data["Low"].values.flatten().astype(float)
    volumes = data["Volume"].values.flatten().astype(float)
    timestamps = [str(t) for t in data.index]

    # Replace any NaN with forward fill
    for arr in [closes, highs, lows, volumes]:
        for i in range(1, len(arr)):
            if np.isnan(arr[i]):
                arr[i] = arr[i - 1]

    # Compute ATR for exit
    atr = compute_atr(highs, lows, closes, 14)

    # Generate signals
    signals = generate_signals(closes, highs, lows, volumes, timestamps)
    log.info("%s: %d signals from %d bars", ticker, len(signals), len(closes))

    if not signals:
        return BacktestResult(
            ticker=ticker, period_days=days, total_trades=0, wins=0, losses=0,
            win_rate=0, profit_factor=0, total_pnl_gbp=0, total_pnl_pct=0,
            max_drawdown_pct=0, avg_rung=0, avg_hold_bars=0, avg_mae_pct=0,
            avg_mfe_pct=0, sharpe_ratio=0, avg_pnl_per_trade=0,
        )

    # Simulate trades
    trades: List[BacktestTrade] = []
    daily_trade_count: Dict[str, int] = {}
    current_equity = equity

    for sig_bar, confidence in signals:
        # Daily trade cap
        day_key = timestamps[sig_bar][:10] if sig_bar < len(timestamps) else "?"
        daily_trade_count[day_key] = daily_trade_count.get(day_key, 0) + 1
        if daily_trade_count[day_key] > MAX_DAILY_TRADES:
            continue

        entry_price = closes[sig_bar]
        if entry_price <= 0:
            continue

        # Position sizing
        position_gbp = current_equity * POSITION_SIZE_PCT
        qty = max(1, int(position_gbp / entry_price))

        # Simulate exit
        highest_high = entry_price
        lowest_low = entry_price
        highest_rung = 1
        exit_bar = None
        exit_price = entry_price
        exit_reason = "max_hold"

        for j in range(sig_bar + 1, min(sig_bar + MAX_HOLD_BARS, len(closes))):
            bar_high = highs[j]
            bar_low = lows[j]
            bar_close = closes[j]
            bar_atr = max(atr[j], entry_price * 0.001)  # Floor ATR at 0.1%

            # Track MAE/MFE
            highest_high = max(highest_high, bar_high)
            lowest_low = min(lowest_low, bar_low)

            # Compute rung
            new_rung = compute_rung(entry_price, bar_high)
            highest_rung = max(highest_rung, new_rung)

            # Compute stop
            stop = chandelier_stop(entry_price, highest_rung, highest_high, bar_atr)

            # Check stop hit
            if bar_low <= stop:
                exit_price = max(stop, bar_low)  # Exit at stop or bar low
                exit_bar = j
                exit_reason = f"chandelier_rung{highest_rung}"
                break

            # Emergency stop (5% loss)
            loss_pct = (bar_low - entry_price) / entry_price
            if loss_pct < -STOP_LOSS_PCT:
                exit_price = entry_price * (1 - STOP_LOSS_PCT)
                exit_bar = j
                exit_reason = "emergency_stop"
                break

            # EMA cross back (exit signal)
            if j >= MIN_WARMUP_BARS:
                ema_f = compute_ema(closes[:j + 1], EMA_FAST)
                ema_s = compute_ema(closes[:j + 1], EMA_SLOW)
                if ema_f[-1] < ema_s[-1] and highest_rung >= 2:
                    exit_price = bar_close
                    exit_bar = j
                    exit_reason = "ema_cross_exit"
                    break

        if exit_bar is None:
            exit_bar = min(sig_bar + MAX_HOLD_BARS - 1, len(closes) - 1)
            exit_price = closes[exit_bar]
            exit_reason = "max_hold"

        # Calculate P&L (including round-trip fees)
        gross_pnl_pct = (exit_price - entry_price) / entry_price
        fee_pnl_pct = gross_pnl_pct - ROUND_TRIP_FEE
        pnl_gbp = qty * entry_price * fee_pnl_pct

        # MAE/MFE
        mae_pct = (lowest_low - entry_price) / entry_price
        mfe_pct = (highest_high - entry_price) / entry_price

        trade = BacktestTrade(
            ticker=ticker,
            entry_time=timestamps[sig_bar] if sig_bar < len(timestamps) else "?",
            exit_time=timestamps[exit_bar] if exit_bar < len(timestamps) else "?",
            entry_price=round(entry_price, 4),
            exit_price=round(exit_price, 4),
            qty=qty,
            pnl_gbp=round(pnl_gbp, 2),
            pnl_pct=round(fee_pnl_pct * 100, 2),
            highest_rung=highest_rung,
            hold_bars=exit_bar - sig_bar,
            exit_reason=exit_reason,
            confidence=confidence,
            mae_pct=round(mae_pct * 100, 2),
            mfe_pct=round(mfe_pct * 100, 2),
        )
        trades.append(trade)
        current_equity += pnl_gbp

    # Aggregate metrics
    return _aggregate_results(ticker, days, trades, equity)


def _aggregate_results(
    ticker: str, days: int, trades: List[BacktestTrade], starting_equity: float
) -> BacktestResult:
    """Compute aggregate metrics from trade list."""
    if not trades:
        return BacktestResult(
            ticker=ticker, period_days=days, total_trades=0, wins=0, losses=0,
            win_rate=0, profit_factor=0, total_pnl_gbp=0, total_pnl_pct=0,
            max_drawdown_pct=0, avg_rung=0, avg_hold_bars=0, avg_mae_pct=0,
            avg_mfe_pct=0, sharpe_ratio=0, avg_pnl_per_trade=0,
        )

    wins = [t for t in trades if t.pnl_gbp > 0]
    losses = [t for t in trades if t.pnl_gbp <= 0]
    total_pnl = sum(t.pnl_gbp for t in trades)
    gross_wins = sum(t.pnl_gbp for t in wins) if wins else 0
    gross_losses = abs(sum(t.pnl_gbp for t in losses)) if losses else 0

    # Equity curve for drawdown
    equity_curve = [starting_equity]
    for t in trades:
        equity_curve.append(equity_curve[-1] + t.pnl_gbp)
    peak = equity_curve[0]
    max_dd = 0
    for eq in equity_curve:
        peak = max(peak, eq)
        dd = (peak - eq) / peak
        max_dd = max(max_dd, dd)

    # Sharpe ratio (daily returns approximation)
    pnl_list = [t.pnl_pct for t in trades]
    sharpe = 0
    if len(pnl_list) >= 2:
        avg_r = statistics.mean(pnl_list)
        std_r = statistics.stdev(pnl_list)
        if std_r > 0:
            # Annualize: assume ~1 trade/day
            sharpe = (avg_r / std_r) * math.sqrt(252)

    return BacktestResult(
        ticker=ticker,
        period_days=days,
        total_trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / len(trades) if trades else 0,
        profit_factor=gross_wins / max(gross_losses, 0.01),
        total_pnl_gbp=round(total_pnl, 2),
        total_pnl_pct=round(total_pnl / starting_equity * 100, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        avg_rung=statistics.mean([t.highest_rung for t in trades]),
        avg_hold_bars=statistics.mean([t.hold_bars for t in trades]),
        avg_mae_pct=statistics.mean([t.mae_pct for t in trades]),
        avg_mfe_pct=statistics.mean([t.mfe_pct for t in trades]),
        sharpe_ratio=round(sharpe, 2),
        avg_pnl_per_trade=round(total_pnl / len(trades), 2),
        trades=trades,
    )


# ---------------------------------------------------------------------------
# Multi-ticker orchestrator
# ---------------------------------------------------------------------------
def run_full_backtest(
    tickers: Optional[List[str]] = None,
    days: int = 30,
    equity: float = STARTING_EQUITY,
) -> Dict[str, Any]:
    """Run backtest across full universe and produce unified report."""
    tickers = tickers or _load_universe_from_contracts()
    results: Dict[str, BacktestResult] = {}
    all_trades: List[BacktestTrade] = []

    for ticker in tickers:
        log.info("Backtesting %s (%d days)...", ticker, days)
        result = run_backtest_ticker(ticker, days, equity)
        if result:
            results[ticker] = result
            all_trades.extend(result.trades)

    # Aggregate across all tickers
    if all_trades:
        combined = _aggregate_results("ALL", days, all_trades, equity)
    else:
        combined = BacktestResult(
            ticker="ALL", period_days=days, total_trades=0, wins=0, losses=0,
            win_rate=0, profit_factor=0, total_pnl_gbp=0, total_pnl_pct=0,
            max_drawdown_pct=0, avg_rung=0, avg_hold_bars=0, avg_mae_pct=0,
            avg_mfe_pct=0, sharpe_ratio=0, avg_pnl_per_trade=0,
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_days": days,
        "starting_equity": equity,
        "tickers_tested": len(results),
        "combined": {
            "total_trades": combined.total_trades,
            "win_rate": round(combined.win_rate, 3),
            "profit_factor": round(combined.profit_factor, 2),
            "total_pnl_gbp": combined.total_pnl_gbp,
            "total_pnl_pct": combined.total_pnl_pct,
            "max_drawdown_pct": combined.max_drawdown_pct,
            "sharpe_ratio": combined.sharpe_ratio,
            "avg_rung": round(combined.avg_rung, 2),
            "avg_hold_bars": round(combined.avg_hold_bars, 1),
            "avg_pnl_per_trade": combined.avg_pnl_per_trade,
        },
        "per_ticker": {},
        "go_live_gate": {},
    }

    for ticker, res in results.items():
        report["per_ticker"][ticker] = {
            "trades": res.total_trades,
            "win_rate": round(res.win_rate, 3),
            "profit_factor": round(res.profit_factor, 2),
            "pnl_gbp": res.total_pnl_gbp,
            "max_drawdown_pct": res.max_drawdown_pct,
            "avg_rung": round(res.avg_rung, 2),
            "sharpe": res.sharpe_ratio,
        }

    # Go-live gate check
    gate = {
        "wr_pass": combined.win_rate >= 0.40,
        "pf_pass": combined.profit_factor >= 1.3,
        "dd_pass": combined.max_drawdown_pct <= 10.0,
        "trades_pass": combined.total_trades >= 20,
        "all_pass": False,
    }
    gate["all_pass"] = all(gate[k] for k in ["wr_pass", "pf_pass", "dd_pass", "trades_pass"])
    gate["details"] = {
        "win_rate": f"{combined.win_rate:.1%} (need >= 40%)",
        "profit_factor": f"{combined.profit_factor:.2f} (need >= 1.3)",
        "max_drawdown": f"{combined.max_drawdown_pct:.1f}% (need <= 10%)",
        "total_trades": f"{combined.total_trades} (need >= 20)",
    }
    report["go_live_gate"] = gate

    # Save report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"vanguard_backtest_{days}d_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    # Save trades as separate file (for Monte Carlo input)
    trades_list = [asdict(t) for t in all_trades]
    report["_trades_file"] = str(report_path.with_suffix(".trades.json"))

    try:
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        with open(report_path.with_suffix(".trades.json"), "w") as f:
            json.dump(trades_list, f, indent=2, default=str)
        log.info("Report saved: %s", report_path)
    except OSError as e:
        log.error("Failed to save report: %s", e)

    return report


def format_report(report: Dict[str, Any]) -> str:
    """Format backtest report for Telegram."""
    c = report["combined"]
    gate = report["go_live_gate"]
    gate_icon = "\u2705" if gate["all_pass"] else "\U0001f534"

    lines = [
        f"\U0001f4ca <b>VANGUARD BACKTEST ({report['period_days']}d)</b>",
        "",
        f"Tickers: {report['tickers_tested']} | Trades: {c['total_trades']}",
        f"Win Rate: {c['win_rate']:.1%} | PF: {c['profit_factor']:.2f}",
        f"P&L: {c['total_pnl_gbp']:+.2f} GBP ({c['total_pnl_pct']:+.1f}%)",
        f"Max DD: {c['max_drawdown_pct']:.1f}% | Sharpe: {c['sharpe_ratio']:.1f}",
        f"Avg Rung: {c['avg_rung']:.1f} | Avg Hold: {c['avg_hold_bars']:.0f} bars",
        "",
        f"{gate_icon} <b>GO-LIVE GATE: {'PASS' if gate['all_pass'] else 'FAIL'}</b>",
    ]

    for k, v in gate.get("details", {}).items():
        icon = "\u2705" if gate.get(f"{k}_pass", gate.get(k.split('_')[0] + "_pass", False)) else "\u274c"
        lines.append(f"  {icon} {v}")

    # Top/bottom tickers
    tickers = report.get("per_ticker", {})
    if tickers:
        lines.append("")
        sorted_t = sorted(tickers.items(), key=lambda x: x[1].get("pnl_gbp", 0), reverse=True)
        if len(sorted_t) > 0:
            best = sorted_t[0]
            lines.append(f"\U0001f947 Best: {best[0]} ({best[1]['pnl_gbp']:+.2f} GBP, WR {best[1]['win_rate']:.0%})")
        if len(sorted_t) > 1:
            worst = sorted_t[-1]
            lines.append(f"\U0001f4a9 Worst: {worst[0]} ({worst[1]['pnl_gbp']:+.2f} GBP, WR {worst[1]['win_rate']:.0%})")

    lines.append(f"\n<i>{report['generated_at']}</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Backtest] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="VanguardSniper 30-Day Backtest (N9a)")
    parser.add_argument("--days", type=int, default=30, help="Lookback days (max 59)")
    parser.add_argument("--ticker", type=str, help="Single ticker")
    parser.add_argument("--isa-only", action="store_true", help="Only test original 12 ISA ETPs")
    parser.add_argument("--json", action="store_true", help="JSON output to stdout")
    parser.add_argument("--send-telegram", action="store_true", help="Send report via Telegram")
    args = parser.parse_args()

    if args.ticker:
        tickers = [args.ticker]
    elif args.isa_only:
        tickers = ISA_TICKERS
    else:
        tickers = None  # Full universe from contracts.toml
    report = run_full_backtest(tickers=tickers, days=args.days)

    if args.json:
        # Strip trades for compact output
        report.pop("_trades_file", None)
        print(json.dumps(report, indent=2, default=str))
    else:
        c = report["combined"]
        gate = report["go_live_gate"]
        print(f"\n{'='*60}")
        print(f"  VANGUARDSNIPER BACKTEST ({report['period_days']}d)")
        print(f"{'='*60}")
        print(f"  Tickers: {report['tickers_tested']}")
        print(f"  Total Trades: {c['total_trades']}")
        print(f"  Win Rate: {c['win_rate']:.1%}")
        print(f"  Profit Factor: {c['profit_factor']:.2f}")
        print(f"  Total P&L: {c['total_pnl_gbp']:+.2f} GBP ({c['total_pnl_pct']:+.1f}%)")
        print(f"  Max Drawdown: {c['max_drawdown_pct']:.1f}%")
        print(f"  Sharpe Ratio: {c['sharpe_ratio']:.2f}")
        print(f"  Avg Rung: {c['avg_rung']:.1f}")
        print(f"\n  GO-LIVE GATE: {'PASS' if gate['all_pass'] else 'FAIL'}")
        for k, v in gate.get("details", {}).items():
            pass_k = k.replace("_", "_") + "_pass"
            passed = gate.get(pass_k, False)
            print(f"    {'PASS' if passed else 'FAIL'}: {v}")

        print(f"\n  Per-ticker summary:")
        for ticker, data in sorted(report.get("per_ticker", {}).items(),
                                    key=lambda x: x[1]["pnl_gbp"], reverse=True):
            print(f"    {ticker:<10} trades={data['trades']:>3d} WR={data['win_rate']:.0%} "
                  f"PF={data['profit_factor']:.1f} PnL={data['pnl_gbp']:>+8.2f}")

    if args.send_telegram:
        try:
            from python_brain.ouroboros.telegram_notify import send_message
            msg = format_report(report)
            send_message(msg)
            log.info("Report sent via Telegram")
        except Exception as e:
            log.error("Telegram send failed: %s", e)


if __name__ == "__main__":
    main()
