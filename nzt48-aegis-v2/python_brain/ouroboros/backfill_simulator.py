"""Ouroboros v6.0 — Backfill Simulator.

Pulls 7-day historical data via yfinance for the 12 primary LSE ETPs and
simulates trades using the same indicators and strategies the live engine uses.

Generates a simulation report showing:
  - Total simulated trades, win rate, profit factor
  - Per-ticker and per-entry-type performance
  - Best/worst days
  - Hypothetical equity curve ("if we had this last week...")

Usage: python3 -m python_brain.ouroboros.backfill_simulator
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

from brain.indicators.hurst import classify_regime, estimate_hurst
from brain.indicators.volume_analytics import calculate_rvol
from python_brain.ouroboros.contract_loader import load_yfinance_symbols, load_leverage_map

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
REPORTS_DIR = DATA_DIR / "ouroboros_reports"

PRIMARY_TICKERS = load_yfinance_symbols()

LEVERAGE_MAP = load_leverage_map()

# Chandelier exit: rung progression thresholds (% gain from entry) — MUST match exit_engine.rs
CHANDELIER_RUNG_PCTS = [0.0, 0.008, 0.015, 0.025, 0.040]
# Chandelier exit: ATR multiplier per rung (trailing stop tightens as rung advances)
# Rung 0 = initial stop (widest), Rung 4 = tightest trail
# MUST match config.toml [chandelier] initial_stop_atr_mult=2.0, rung3_trail=1.0, etc.
CHANDELIER_RUNGS = [2.0, 1.8, 1.5, 1.0, 0.75]
CHANDELIER_ATR_PERIOD = 14

# Entry signal thresholds — MUST match bridge.py Sprint 5 T-04/T-05 fixes
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
RSI_PERIOD = 14
RVOL_ENTRY_THRESHOLD = 0.7  # Was 1.8 — lowered to match live (Sprint 5 T-05)
VOLUME_SURGE_MULT = 1.0  # Was 2.0 — lowered to match live (Sprint 5 T-05)

STARTING_EQUITY = 10_000.0  # GBP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Backfill] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("backfill_sim")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class SimTrade:
    """A simulated trade."""
    ticker: str
    date: str
    entry_type: str  # TypeA/B/C/D
    entry_price: float
    exit_price: float
    entry_bar: int
    exit_bar: int
    rung_achieved: int
    pnl: float
    pnl_pct: float
    hold_bars: int
    regime: str


@dataclass
class DayResult:
    """Simulation results for one day."""
    date: str
    trades: List[SimTrade] = field(default_factory=list)
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0


# ---------------------------------------------------------------------------
# Technical indicators (pure functions)
# ---------------------------------------------------------------------------
def compute_rsi(prices: np.ndarray, period: int = RSI_PERIOD) -> np.ndarray:
    """Wilder's RSI. Returns array same length as prices (NaN-padded)."""
    n = len(prices)
    rsi = np.full(n, np.nan)
    if n < period + 1:
        return rsi

    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss < 1e-10:
            rsi[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return rsi


def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = CHANDELIER_ATR_PERIOD) -> np.ndarray:
    """Average True Range (Wilder smoothing). Returns array same length as input."""
    n = len(closes)
    atr = np.full(n, np.nan)
    if n < period + 1:
        return atr

    # True Range
    tr = np.empty(n - 1)
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i - 1] = max(hl, hc, lc)

    # Wilder smoothing
    atr_val = np.mean(tr[:period])
    atr[period] = atr_val
    for i in range(period, len(tr)):
        atr_val = (atr_val * (period - 1) + tr[i]) / period
        atr[i + 1] = atr_val

    return atr


# ---------------------------------------------------------------------------
# Entry signal classification
# ---------------------------------------------------------------------------
def classify_entries(
    closes: np.ndarray,
    volumes: np.ndarray,
    rsi: np.ndarray,
    rvol_arr: np.ndarray,
    regime: str,
) -> List[Tuple[int, str]]:
    """Identify entry signals and classify as Type A/B/C/D.

    Type A: Momentum breakout (RSI > 50 + RVOL surge + trending regime)
    Type B: Volume anomaly (RVOL > 2x + any regime)
    Type C: Oversold bounce (RSI < 30 + mean_reverting regime)
    Type D: Continuation (price above 20-bar EMA + trending regime)

    Returns list of (bar_index, entry_type).
    """
    entries: List[Tuple[int, str]] = []
    n = len(closes)
    if n < 21:
        return entries

    # 20-bar EMA
    ema20 = np.full(n, np.nan)
    ema20[0] = closes[0]
    alpha = 2.0 / 21.0
    for i in range(1, n):
        ema20[i] = alpha * closes[i] + (1 - alpha) * ema20[i - 1]

    # Scan for entries (skip first 21 bars for indicator warmup)
    # SIM_MODE: Load entry cooldown from config.toml [simulation] section.
    # Default 0 = no cooldown between entries for maximum signal generation.
    _entry_cooldown = 0
    try:
        try:
            import tomllib as _tl
        except ImportError:
            import tomli as _tl
        _cfg_path = Path(os.environ.get("AEGIS_CONFIG_DIR", "/app/config")) / "config.toml"
        if not _cfg_path.exists():
            _cfg_path = _PROJECT_ROOT / "config" / "config.toml"
        if _cfg_path.exists():
            with open(_cfg_path, "rb") as _f:
                _sim_cfg = _tl.load(_f).get("simulation", {})
                _entry_cooldown = int(_sim_cfg.get("entry_cooldown_bars", 0))
    except Exception:
        pass
    last_entry_bar = -(_entry_cooldown + 1)
    for i in range(21, n - 5):  # Leave room for exit simulation
        if _entry_cooldown > 0 and i - last_entry_bar < _entry_cooldown:
            continue
        if np.isnan(rsi[i]) or np.isnan(rvol_arr[i]):
            continue

        # Type A: Momentum breakout
        if rsi[i] > 50 and rvol_arr[i] > RVOL_ENTRY_THRESHOLD and regime == "trending":
            entries.append((i, "TypeA"))
            last_entry_bar = i
            continue

        # Type B: Volume anomaly
        if rvol_arr[i] > VOLUME_SURGE_MULT:
            entries.append((i, "TypeB"))
            last_entry_bar = i
            continue

        # Type C: Oversold bounce
        if rsi[i] < RSI_OVERSOLD and regime == "mean_reverting":
            entries.append((i, "TypeC"))
            last_entry_bar = i
            continue

        # Type D: Continuation
        if closes[i] > ema20[i] and regime == "trending" and rsi[i] > 40:
            # Only trigger if price just crossed above EMA
            if i > 0 and closes[i - 1] <= ema20[i - 1]:
                entries.append((i, "TypeD"))
                last_entry_bar = i

    return entries


# ---------------------------------------------------------------------------
# Chandelier exit simulation
# ---------------------------------------------------------------------------
def simulate_chandelier_exit(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    atr: np.ndarray,
    entry_bar: int,
    entry_price: float,
) -> Tuple[int, float, int]:
    """Simulate Chandelier exit with 5-rung trailing stop ladder.

    Returns (exit_bar, exit_price, highest_rung_achieved).
    """
    n = len(closes)
    highest_since_entry = entry_price
    current_rung = 0

    for i in range(entry_bar + 1, min(entry_bar + 60, n)):  # Max hold: 60 bars
        if np.isnan(atr[i]):
            continue

        highest_since_entry = max(highest_since_entry, highs[i])

        # Check rung progression (based on % gain from entry — uses CHANDELIER_RUNG_PCTS)
        pct_gain = (highest_since_entry - entry_price) / max(entry_price, 1e-9)
        for r in range(len(CHANDELIER_RUNG_PCTS) - 1, 0, -1):
            if pct_gain >= CHANDELIER_RUNG_PCTS[r]:
                current_rung = max(current_rung, r)
                break

        # Chandelier stop based on current rung
        rung_mult = CHANDELIER_RUNGS[min(current_rung, len(CHANDELIER_RUNGS) - 1)]
        stop_price = highest_since_entry - rung_mult * atr[i]

        if closes[i] <= stop_price or lows[i] <= stop_price:
            exit_price = max(stop_price, lows[i])  # Slippage-conservative
            return i, exit_price, current_rung

    # Force exit at end of simulation window
    exit_bar = min(entry_bar + 59, n - 1)
    return exit_bar, closes[exit_bar], current_rung


# ---------------------------------------------------------------------------
# Main simulation
# ---------------------------------------------------------------------------
def fetch_historical_data(tickers: List[str], period: str = "7d", interval: str = "5m") -> Dict[str, Any]:
    """Fetch historical data via yfinance.

    Interval options: 1m (7d max), 2m/5m/15m/30m (60d max),
                      60m/1h (730d max), 1d (unlimited).
    """
    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance not installed. Run: pip install yfinance")
        return {}

    data = {}
    for ticker in tickers:
        log.info("Fetching %s (%s)...", ticker, period)
        try:
            df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
            if df is None or df.empty:
                log.warning("No data for %s (may be delisted or illiquid)", ticker)
                continue
            # Flatten MultiIndex columns if present
            if hasattr(df.columns, 'levels'):
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            data[ticker] = df
            log.info("  %s: %d bars fetched", ticker, len(df))
        except Exception as e:
            log.warning("Failed to fetch %s: %s", ticker, e)
    return data


def simulate_ticker(ticker: str, df: Any) -> List[SimTrade]:
    """Simulate trades for one ticker across all available data."""
    trades: List[SimTrade] = []

    closes = df["Close"].values.astype(np.float64)
    highs = df["High"].values.astype(np.float64)
    lows = df["Low"].values.astype(np.float64)
    volumes = df["Volume"].values.astype(np.float64)

    if len(closes) < 30:
        log.warning("%s: insufficient data (%d bars), skipping", ticker, len(closes))
        return trades

    # Compute indicators
    rsi = compute_rsi(closes, RSI_PERIOD)
    atr = compute_atr(highs, lows, closes, CHANDELIER_ATR_PERIOD)

    # RVOL array (rolling relative volume)
    rvol_arr = np.zeros(len(volumes))
    for i in range(21, len(volumes)):
        vol_list = volumes[i - 21:i].tolist()
        vol_list.append(volumes[i])
        rvol_arr[i] = calculate_rvol(vol_list, window=20)

    # Hurst / regime (calculated over entire series)
    hurst = estimate_hurst(closes.tolist(), max_lag=20)
    regime = classify_regime(hurst)

    # Get dates for reporting
    if hasattr(df.index, 'date'):
        dates = [str(d.date()) if hasattr(d, 'date') else str(d)[:10] for d in df.index]
    else:
        dates = [str(i) for i in range(len(df))]

    # Classify entry signals
    entries = classify_entries(closes, volumes, rsi, rvol_arr, regime)

    for entry_bar, entry_type in entries:
        entry_price = closes[entry_bar]
        if entry_price <= 0:
            continue

        exit_bar, exit_price, rung = simulate_chandelier_exit(
            closes, highs, lows, atr, entry_bar, entry_price,
        )

        pnl = exit_price - entry_price
        pnl_pct = pnl / entry_price * 100.0

        trades.append(SimTrade(
            ticker=ticker,
            date=dates[entry_bar] if entry_bar < len(dates) else "unknown",
            entry_type=entry_type,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_bar=entry_bar,
            exit_bar=exit_bar,
            rung_achieved=rung,
            pnl=pnl,
            pnl_pct=pnl_pct,
            hold_bars=exit_bar - entry_bar,
            regime=regime,
        ))

    return trades


def generate_simulation_report(
    all_trades: List[SimTrade],
    elapsed_secs: float,
) -> str:
    """Generate comprehensive simulation report."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"backfill_sim_{today}.txt"

    total = len(all_trades)
    wins = [t for t in all_trades if t.pnl > 0]
    losses = [t for t in all_trades if t.pnl <= 0]
    win_rate = len(wins) / total if total > 0 else 0.0
    gross_wins = sum(t.pnl for t in wins)
    gross_losses = abs(sum(t.pnl for t in losses))
    profit_factor = gross_wins / max(gross_losses, 1e-9)
    total_pnl = sum(t.pnl for t in all_trades)
    avg_rung = sum(t.rung_achieved for t in all_trades) / total if total > 0 else 0.0

    # Per-ticker
    by_ticker: Dict[str, List[SimTrade]] = defaultdict(list)
    for t in all_trades:
        by_ticker[t.ticker].append(t)

    # Per-entry-type
    by_type: Dict[str, List[SimTrade]] = defaultdict(list)
    for t in all_trades:
        by_type[t.entry_type].append(t)

    # Per-day
    by_day: Dict[str, List[SimTrade]] = defaultdict(list)
    for t in all_trades:
        day_key = t.date[:10]
        by_day[day_key].append(t)

    # Hypothetical equity curve
    equity = STARTING_EQUITY
    kelly_frac = 0.10  # Conservative Kelly for simulation
    equity_curve = [equity]
    for t in sorted(all_trades, key=lambda x: x.entry_bar):
        position_size = equity * kelly_frac
        shares = math.floor(position_size / max(t.entry_price, 1e-9))
        if shares <= 0:
            continue
        trade_pnl = shares * t.pnl
        equity += trade_pnl
        equity_curve.append(equity)

    lines = [
        f"{'=' * 70}",
        f"  OUROBOROS v6.0 BACKFILL SIMULATION REPORT",
        f"  Generated: {today}  |  Elapsed: {elapsed_secs:.1f}s",
        f"{'=' * 70}",
        "",
        "SUMMARY",
        f"  Total simulated trades: {total}",
        f"  Wins:                   {len(wins)}",
        f"  Losses:                 {len(losses)}",
        f"  Win rate:               {win_rate:.1%}",
        f"  Profit factor:          {profit_factor:.2f}",
        f"  Total PnL (per share):  GBP {total_pnl:+.4f}",
        f"  Avg rung achieved:      {avg_rung:.1f}",
        f"  Avg hold (bars):        {sum(t.hold_bars for t in all_trades) / max(total, 1):.0f}",
        "",
        "HYPOTHETICAL EQUITY (10K starting, 10% Kelly fraction)",
        f"  Starting equity:  GBP {STARTING_EQUITY:,.2f}",
        f"  Ending equity:    GBP {equity:,.2f}",
        f"  Return:           {((equity - STARTING_EQUITY) / STARTING_EQUITY) * 100:+.2f}%",
        "",
    ]

    # Per-ticker table
    lines += ["PER-TICKER PERFORMANCE", "-" * 70]
    lines.append(f"  {'Ticker':12s} {'Trades':>6s} {'Wins':>5s} {'WR':>6s} {'PnL/sh':>10s} {'Regime':>14s}")
    for ticker in PRIMARY_TICKERS:
        tt = by_ticker.get(ticker, [])
        if not tt:
            lines.append(f"  {ticker:12s} {'0':>6s} {'--':>5s} {'--':>6s} {'--':>10s} {'no data':>14s}")
            continue
        tw = sum(1 for t in tt if t.pnl > 0)
        tp = sum(t.pnl for t in tt)
        regime = tt[0].regime if tt else "?"
        lines.append(
            f"  {ticker:12s} {len(tt):6d} {tw:5d} {tw / len(tt):6.0%} "
            f"{tp:+10.4f} {regime:>14s}"
        )

    # Per-entry-type table
    lines += ["", "PER-ENTRY-TYPE PERFORMANCE", "-" * 70]
    lines.append(f"  {'Type':10s} {'Trades':>6s} {'Wins':>5s} {'WR':>6s} {'PnL/sh':>10s} {'Avg Rung':>10s}")
    for etype in ["TypeA", "TypeB", "TypeC", "TypeD"]:
        tt = by_type.get(etype, [])
        if not tt:
            lines.append(f"  {etype:10s} {'0':>6s} {'--':>5s} {'--':>6s} {'--':>10s} {'--':>10s}")
            continue
        tw = sum(1 for t in tt if t.pnl > 0)
        tp = sum(t.pnl for t in tt)
        ar = sum(t.rung_achieved for t in tt) / len(tt)
        lines.append(
            f"  {etype:10s} {len(tt):6d} {tw:5d} {tw / len(tt):6.0%} "
            f"{tp:+10.4f} {ar:10.1f}"
        )

    # Per-day table
    lines += ["", "PER-DAY PERFORMANCE", "-" * 70]
    lines.append(f"  {'Date':12s} {'Trades':>6s} {'Wins':>5s} {'WR':>6s} {'PnL/sh':>10s}")
    for day, day_trades in sorted(by_day.items()):
        dw = sum(1 for t in day_trades if t.pnl > 0)
        dp = sum(t.pnl for t in day_trades)
        lines.append(
            f"  {day:12s} {len(day_trades):6d} {dw:5d} "
            f"{dw / len(day_trades):6.0%} {dp:+10.4f}"
        )

    # Best/worst
    if all_trades:
        best = max(all_trades, key=lambda t: t.pnl)
        worst = min(all_trades, key=lambda t: t.pnl)
        lines += [
            "",
            f"BEST TRADE:  {best.ticker} on {best.date} — {best.entry_type} "
            f"PnL={best.pnl:+.4f} ({best.pnl_pct:+.2f}%) rung={best.rung_achieved}",
            f"WORST TRADE: {worst.ticker} on {worst.date} — {worst.entry_type} "
            f"PnL={worst.pnl:+.4f} ({worst.pnl_pct:+.2f}%) rung={worst.rung_achieved}",
        ]

    lines += [
        "",
        f"  If we had these improvements last week, GBP {STARTING_EQUITY:,.0f} "
        f"would be GBP {equity:,.2f} now",
        "",
        f"{'=' * 70}",
        "",
    ]

    report_text = "\n".join(lines)
    report_path.write_text(report_text)
    log.info("Simulation report written: %s", report_path)

    # Also save JSON sidecar
    json_path = REPORTS_DIR / f"backfill_sim_{today}.json"
    json_data = {
        "date": today,
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "total_pnl_per_share": total_pnl,
        "avg_rung": avg_rung,
        "starting_equity": STARTING_EQUITY,
        "ending_equity": equity,
        "return_pct": ((equity - STARTING_EQUITY) / STARTING_EQUITY) * 100,
        "per_ticker": {
            t: {
                "trades": len(tt),
                "wins": sum(1 for x in tt if x.pnl > 0),
                "total_pnl": sum(x.pnl for x in tt),
            }
            for t, tt in by_ticker.items()
        },
        "per_entry_type": {
            et: {
                "trades": len(tt),
                "wins": sum(1 for x in tt if x.pnl > 0),
                "total_pnl": sum(x.pnl for x in tt),
            }
            for et, tt in by_type.items()
        },
    }
    json_path.write_text(json.dumps(json_data, indent=2))

    return report_text


# ---------------------------------------------------------------------------
# Feedback export for nightly learning loop (ISS-018)
# ---------------------------------------------------------------------------
FEEDBACK_FILE = DATA_DIR / "backfill_feedback.json"


def export_backfill_feedback(all_trades: List[SimTrade]) -> bool:
    """Export backfill simulation results as structured feedback for the nightly loop.

    Writes a JSON summary to data/backfill_feedback.json using atomic write
    (tempfile + os.rename) to prevent partial reads. The nightly_v6 loop reads
    this file to incorporate backfill insights into parameter recommendations.

    QUARANTINE: This function is READ-ONLY to WAL, config, and live trading
    state. It only writes to its own feedback file.

    Args:
        all_trades: List of SimTrade results from the backfill simulation.

    Returns:
        True if feedback file was written successfully, False otherwise.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = len(all_trades)

    if total == 0:
        log.warning("No simulated trades to export as feedback")
        return False

    # --- Core metrics ---
    wins = [t for t in all_trades if t.pnl > 0]
    losses = [t for t in all_trades if t.pnl <= 0]
    win_rate = len(wins) / total
    avg_return = sum(t.pnl_pct for t in all_trades) / total

    # --- Per-entry-type performance for strategy confidence delta ---
    by_type: Dict[str, List[SimTrade]] = defaultdict(list)
    for t in all_trades:
        by_type[t.entry_type].append(t)

    strategy_confidence_delta: Dict[str, float] = {}
    for entry_type, trades in by_type.items():
        n = len(trades)
        if n < 3:
            # Insufficient data — no adjustment
            strategy_confidence_delta[entry_type] = 0.0
            continue
        type_wr = sum(1 for t in trades if t.pnl > 0) / n
        type_avg_pnl_pct = sum(t.pnl_pct for t in trades) / n

        # Confidence delta: scale from -5 to +5 based on win rate and avg return.
        # Neutral at 50% WR / 0% avg return. Clamped to [-5, +5].
        wr_component = (type_wr - 0.5) * 6.0   # -3 to +3 range
        pnl_component = max(-2.0, min(2.0, type_avg_pnl_pct * 2.0))  # -2 to +2 range
        delta = max(-5.0, min(5.0, round(wr_component + pnl_component, 1)))
        strategy_confidence_delta[entry_type] = delta

    # --- Per-ticker performance for recommended parameter changes ---
    by_ticker: Dict[str, List[SimTrade]] = defaultdict(list)
    for t in all_trades:
        by_ticker[t.ticker].append(t)

    recommended_parameter_changes: List[Dict[str, Any]] = []
    for ticker, trades in by_ticker.items():
        n = len(trades)
        if n < 3:
            continue
        ticker_wr = sum(1 for t in trades if t.pnl > 0) / n
        ticker_avg_rung = sum(t.rung_achieved for t in trades) / n
        ticker_avg_pnl = sum(t.pnl_pct for t in trades) / n

        # Suggest chandelier tightening if avg rung is high (profits being left)
        if ticker_avg_rung > 3.0 and ticker_wr > 0.5:
            recommended_parameter_changes.append({
                "ticker": ticker,
                "parameter": "chandelier_atr_mult",
                "direction": "tighten",
                "reason": f"Avg rung {ticker_avg_rung:.1f} > 3.0 with WR {ticker_wr:.0%} — capture profits earlier",
                "magnitude": round(min(0.3, (ticker_avg_rung - 3.0) * 0.1), 2),
            })

        # Suggest widening if avg rung is low and losses are from early stops
        if ticker_avg_rung < 1.0 and ticker_wr < 0.4:
            recommended_parameter_changes.append({
                "ticker": ticker,
                "parameter": "chandelier_atr_mult",
                "direction": "widen",
                "reason": f"Avg rung {ticker_avg_rung:.1f} < 1.0 with WR {ticker_wr:.0%} — let trades breathe",
                "magnitude": round(min(0.3, (1.0 - ticker_avg_rung) * 0.15), 2),
            })

        # Suggest entry type filter if a type has very poor performance
        for entry_type in ["TypeA", "TypeB", "TypeC", "TypeD"]:
            type_trades = [t for t in trades if t.entry_type == entry_type]
            if len(type_trades) >= 3:
                type_wr = sum(1 for t in type_trades if t.pnl > 0) / len(type_trades)
                if type_wr < 0.2:
                    recommended_parameter_changes.append({
                        "ticker": ticker,
                        "parameter": "entry_filter",
                        "direction": "disable",
                        "reason": f"{entry_type} on {ticker}: WR {type_wr:.0%} over {len(type_trades)} sim trades",
                        "magnitude": 0,
                    })

    # --- Build feedback payload ---
    feedback = {
        "backfill_date": today,
        "simulated_trades_count": total,
        "simulated_win_rate": round(win_rate, 4),
        "simulated_avg_return": round(avg_return, 4),
        "strategy_confidence_delta": strategy_confidence_delta,
        "recommended_parameter_changes": recommended_parameter_changes,
    }

    # --- Atomic write: tempfile + os.rename ---
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(DATA_DIR), suffix=".tmp", prefix="backfill_feedback_"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(feedback, f, indent=2)
            os.rename(tmp_path, str(FEEDBACK_FILE))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        log.info(
            "Backfill feedback exported: %s (trades=%d wr=%.1f%% avg_ret=%.2f%% deltas=%d recs=%d)",
            FEEDBACK_FILE, total, win_rate * 100, avg_return,
            len(strategy_confidence_delta), len(recommended_parameter_changes),
        )
        return True

    except Exception as e:
        log.error("Failed to export backfill feedback: %s", e)
        return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_backfill(days: int = 7, interval: str = "5m") -> int:
    """Execute the backfill simulation."""
    start = time.monotonic()
    log.info("Ouroboros v6.0 Backfill Simulator starting (%dd, %s bars)...", days, interval)

    period = f"{days}d"
    data = fetch_historical_data(PRIMARY_TICKERS, period=period, interval=interval)
    if not data:
        log.error("No historical data fetched. Aborting.")
        return 1

    log.info("Data fetched for %d/%d tickers", len(data), len(PRIMARY_TICKERS))

    # Load blacklist from config.toml (skip proven losers)
    blacklist = set()
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        cfg_path = Path(os.environ.get("AEGIS_CONFIG_DIR", "/app/config")) / "config.toml"
        if cfg_path.exists():
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
            blacklist = set(cfg.get("blacklist", {}).get("tickers", []))
            if blacklist:
                log.info("Blacklist loaded: %d tickers (%s)", len(blacklist), ", ".join(sorted(blacklist)))
    except Exception:
        pass

    # Simulate trades for each ticker
    all_trades: List[SimTrade] = []
    for ticker, df in data.items():
        if ticker in blacklist:
            log.info("  %s: SKIPPED (blacklisted)", ticker)
            continue
        trades = simulate_ticker(ticker, df)
        all_trades.extend(trades)
        log.info("  %s: %d simulated trades", ticker, len(trades))

    elapsed = time.monotonic() - start
    log.info("Simulation complete: %d total trades in %.1fs", len(all_trades), elapsed)

    # Generate report
    report = generate_simulation_report(all_trades, elapsed)
    print(report)

    # Export feedback for nightly learning loop (ISS-018)
    export_backfill_feedback(all_trades)

    return 0


def main():
    """CLI entry point."""
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Backfill] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Ouroboros v6.0 Backfill Simulator")
    parser.add_argument("--days", type=int, default=7, help="Lookback days")
    parser.add_argument("--interval", type=str, default="5m",
                        help="Bar interval: 1m (7d max), 5m (59d max), 60m/1h (730d max), 1d (unlimited)")
    args = parser.parse_args()

    # Enforce yfinance limits
    max_days = {"1m": 7, "2m": 59, "5m": 59, "15m": 59, "30m": 59,
                "60m": 730, "1h": 730, "90m": 59, "1d": 9999}
    limit = max_days.get(args.interval, 59)

    try:
        sys.exit(run_backfill(days=min(args.days, limit), interval=args.interval))
    except Exception as e:
        log.error("Backfill simulator crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
