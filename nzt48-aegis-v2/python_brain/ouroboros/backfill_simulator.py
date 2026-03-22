"""Ouroboros v7.0 — Backfill Simulator.

Pulls historical data via yfinance for a configurable ticker universe and
simulates trades using the same indicators and strategies the live engine uses.

Supports parallel downloads (ThreadPoolExecutor), chunked processing for
memory management on constrained environments (4GB EC2), and exchange-aware
reporting with per-hour/per-day-of-week breakdowns.

Generates a simulation report showing:
  - Total simulated trades, win rate, profit factor
  - Per-exchange, per-ticker, per-entry-type performance
  - Per-hour-of-day, per-day-of-week breakdowns
  - Top 20 winners and top 20 losers
  - Hypothetical equity curve

Usage:
  python3 -m python_brain.ouroboros.backfill_simulator --days 730 --interval 60m
  python3 -m python_brain.ouroboros.backfill_simulator --days 730 --interval 60m --universe /app/config/universe_10k.txt
  python3 -m python_brain.ouroboros.backfill_simulator --days 730 --interval 60m --universe /app/config/universe_10k.txt --blacklist
"""

from __future__ import annotations

import gc
import json
import logging
import math
import os
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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
# MUST match config.toml [chandelier] initial_stop_atr_mult=1.5, rung3_trail=1.0, etc.
CHANDELIER_RUNGS = [1.5, 1.35, 1.125, 1.0, 0.75]
CHANDELIER_ATR_PERIOD = 14

# Entry signal thresholds — MUST match bridge.py Sprint 5 T-04/T-05 fixes
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
RSI_PERIOD = 14
RVOL_ENTRY_THRESHOLD = 0.7  # Was 1.8 — lowered to match live (Sprint 5 T-05)
VOLUME_SURGE_MULT = 2.5  # TypeB-TIGHT: only genuine volume anomalies (BT-003 validated)

STARTING_EQUITY = 10_000.0  # GBP

# Parallel download settings
DOWNLOAD_WORKERS = 10   # ThreadPoolExecutor concurrency for yfinance
CHUNK_SIZE = 100         # Tickers per processing chunk (memory management)

# Exchange detection from ticker suffix
EXCHANGE_SUFFIX_MAP = {
    ".T": "TSE",
    ".HK": "HKEX",
    ".L": "LSE",
    ".DE": "XETRA",
    ".PA": "Euronext",
    ".AS": "Euronext",
    ".SI": "SGX",
    ".NS": "NSE",
    ".KS": "KRX",
    ".AX": "ASX",
    ".TW": "TWSE",
    ".SA": "B3",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Backfill] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("backfill_sim")


# ---------------------------------------------------------------------------
# Exchange detection
# ---------------------------------------------------------------------------
def detect_exchange(ticker: str) -> str:
    """Detect exchange from ticker suffix. No suffix = US."""
    for suffix, exchange in EXCHANGE_SUFFIX_MAP.items():
        if ticker.endswith(suffix):
            return exchange
    return "US"


# ---------------------------------------------------------------------------
# Universe loading
# ---------------------------------------------------------------------------
def load_universe_file(path: str) -> List[str]:
    """Load tickers from a universe file (one per line, skip # comments and blanks).

    Deduplicates while preserving order.
    """
    tickers: List[str] = []
    seen: Set[str] = set()
    filepath = Path(path)
    if not filepath.exists():
        log.error("Universe file not found: %s", path)
        return []

    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ticker = line.split()[0]  # Handle trailing comments/whitespace
            if ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)

    log.info("Loaded %d unique tickers from %s", len(tickers), path)
    return tickers


def load_blacklist_from_config() -> Set[str]:
    """Load blacklisted tickers from config.toml [blacklist] section."""
    blacklist: Set[str] = set()
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        cfg_path = Path(os.environ.get("AEGIS_CONFIG_DIR", "/app/config")) / "config.toml"
        if not cfg_path.exists():
            cfg_path = _PROJECT_ROOT / "config" / "config.toml"
        if cfg_path.exists():
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
            blacklist = set(cfg.get("blacklist", {}).get("tickers", []))
            if blacklist:
                log.info("Blacklist loaded: %d tickers (%s)", len(blacklist), ", ".join(sorted(blacklist)))
    except Exception as e:
        log.warning("Failed to load blacklist from config.toml: %s", e)
    return blacklist


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
    exchange: str = "US"
    entry_hour: int = -1       # Hour of day (0-23) at entry
    entry_weekday: int = -1    # Day of week (0=Mon, 6=Sun) at entry


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

    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + float(gains[i])) / period
        avg_loss = (avg_loss * (period - 1) + float(losses[i])) / period
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
# Entry signal classification — NO COOLDOWN, NO DAILY CAP
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

    No cooldown between entries and no daily cap — simulator captures ALL
    possible signals to provide maximum data for Ouroboros learning.

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
    # No cooldown, no daily cap — capture every signal for maximum data
    for i in range(21, n - 5):  # Leave room for exit simulation
        if np.isnan(rsi[i]) or np.isnan(rvol_arr[i]):
            continue

        # Type A: Momentum breakout
        if rsi[i] > 50 and rvol_arr[i] > RVOL_ENTRY_THRESHOLD and regime == "trending":
            entries.append((i, "TypeA"))
            continue

        # Type B: Volume anomaly
        if rvol_arr[i] > VOLUME_SURGE_MULT:
            entries.append((i, "TypeB"))
            continue

        # Type C: Oversold bounce
        if rsi[i] < RSI_OVERSOLD and regime == "mean_reverting":
            entries.append((i, "TypeC"))
            continue

        # Type D: Continuation
        if closes[i] > ema20[i] and regime == "trending" and rsi[i] > 40:
            # Only trigger if price just crossed above EMA
            if i > 0 and closes[i - 1] <= ema20[i - 1]:
                entries.append((i, "TypeD"))

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
# Parallel data fetching
# ---------------------------------------------------------------------------
def _fetch_single_ticker(ticker: str, period: str, interval: str) -> Tuple[str, Any]:
    """Fetch a single ticker via yfinance. Returns (ticker, df_or_None).

    Thread-safe: each call creates its own yfinance download session.
    """
    try:
        import yfinance as yf
    except ImportError:
        return ticker, None

    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return ticker, None
        # Flatten MultiIndex columns if present
        if hasattr(df.columns, 'levels'):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return ticker, df
    except Exception:
        return ticker, None


def fetch_historical_data_parallel(
    tickers: List[str],
    period: str = "7d",
    interval: str = "5m",
    max_workers: int = DOWNLOAD_WORKERS,
) -> Dict[str, Any]:
    """Fetch historical data via yfinance using parallel ThreadPoolExecutor.

    Downloads up to max_workers tickers simultaneously for ~10x speedup
    over sequential fetching.
    """
    try:
        import yfinance as yf  # noqa: F401 — verify import before spawning threads
    except ImportError:
        log.error("yfinance not installed. Run: pip install yfinance")
        return {}

    data: Dict[str, Any] = {}
    total = len(tickers)
    fetched = 0
    failed = 0

    log.info("Fetching %d tickers (%s, %s) with %d parallel workers...", total, period, interval, max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch_single_ticker, ticker, period, interval): ticker
            for ticker in tickers
        }

        for future in as_completed(futures):
            ticker, df = future.result()
            fetched += 1
            if df is not None:
                data[ticker] = df
            else:
                failed += 1
            if fetched % 100 == 0 or fetched == total:
                log.info("  Progress: %d/%d fetched (%d with data, %d empty/failed)",
                         fetched, total, len(data), failed)

    log.info("Download complete: %d/%d tickers returned data (%d failed)", len(data), total, failed)
    return data


# ---------------------------------------------------------------------------
# Per-ticker simulation
# ---------------------------------------------------------------------------
def simulate_ticker(ticker: str, df: Any) -> List[SimTrade]:
    """Simulate trades for one ticker across all available data.

    No daily cap, no cooldown — captures ALL signals for maximum Ouroboros data.
    """
    trades: List[SimTrade] = []
    exchange = detect_exchange(ticker)

    closes = df["Close"].values.astype(np.float64).flatten()
    highs = df["High"].values.astype(np.float64).flatten()
    lows = df["Low"].values.astype(np.float64).flatten()
    volumes = df["Volume"].values.astype(np.float64).flatten()

    if len(closes) < 30:
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

    # Extract datetime info for hour-of-day / day-of-week reporting
    index_list = list(df.index)
    has_datetime = len(index_list) > 0 and hasattr(index_list[0], 'hour')

    # Get dates for reporting
    if hasattr(df.index, 'date'):
        dates = [str(d.date()) if hasattr(d, 'date') else str(d)[:10] for d in df.index]
    else:
        dates = [str(i) for i in range(len(df))]

    # Classify entry signals (no cooldown, no daily cap)
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

        # Extract hour and weekday from index if available
        entry_hour = -1
        entry_weekday = -1
        if has_datetime and entry_bar < len(index_list):
            ts = index_list[entry_bar]
            try:
                entry_hour = ts.hour
                entry_weekday = ts.weekday()
            except AttributeError:
                pass

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
            exchange=exchange,
            entry_hour=entry_hour,
            entry_weekday=entry_weekday,
        ))

    return trades


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------
def _stats_line(trades: List[SimTrade]) -> Tuple[int, int, float, float, float]:
    """Return (count, wins, win_rate, total_pnl, profit_factor) for a list of trades."""
    n = len(trades)
    if n == 0:
        return 0, 0, 0.0, 0.0, 0.0
    wins = sum(1 for t in trades if t.pnl > 0)
    wr = wins / n
    gross_w = sum(t.pnl for t in trades if t.pnl > 0)
    gross_l = abs(sum(t.pnl for t in trades if t.pnl <= 0))
    pf = gross_w / max(gross_l, 1e-9)
    total_pnl = sum(t.pnl for t in trades)
    return n, wins, wr, total_pnl, pf


WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def generate_simulation_report(
    all_trades: List[SimTrade],
    elapsed_secs: float,
    ticker_list: List[str],
    num_tickers_requested: int,
    num_tickers_fetched: int,
) -> str:
    """Generate comprehensive simulation report with exchange/hour/weekday breakdowns."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
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
    avg_pnl_pct = sum(t.pnl_pct for t in all_trades) / total if total > 0 else 0.0

    # Group by various dimensions
    by_ticker: Dict[str, List[SimTrade]] = defaultdict(list)
    by_type: Dict[str, List[SimTrade]] = defaultdict(list)
    by_day: Dict[str, List[SimTrade]] = defaultdict(list)
    by_exchange: Dict[str, List[SimTrade]] = defaultdict(list)
    by_hour: Dict[int, List[SimTrade]] = defaultdict(list)
    by_weekday: Dict[int, List[SimTrade]] = defaultdict(list)

    for t in all_trades:
        by_ticker[t.ticker].append(t)
        by_type[t.entry_type].append(t)
        by_day[t.date[:10]].append(t)
        by_exchange[t.exchange].append(t)
        if t.entry_hour >= 0:
            by_hour[t.entry_hour].append(t)
        if t.entry_weekday >= 0:
            by_weekday[t.entry_weekday].append(t)

    # Hypothetical equity curve
    equity = STARTING_EQUITY
    kelly_frac = 0.10  # Conservative Kelly for simulation
    equity_curve = [equity]
    max_equity = equity
    max_drawdown = 0.0
    for t in sorted(all_trades, key=lambda x: (x.date, x.entry_bar)):
        position_size = equity * kelly_frac
        shares = math.floor(position_size / max(t.entry_price, 1e-9))
        if shares <= 0:
            continue
        trade_pnl = shares * t.pnl
        equity += trade_pnl
        equity_curve.append(equity)
        max_equity = max(max_equity, equity)
        dd = (max_equity - equity) / max_equity if max_equity > 0 else 0
        max_drawdown = max(max_drawdown, dd)

    lines = [
        f"{'=' * 80}",
        f"  OUROBOROS v7.0 BACKFILL SIMULATION REPORT",
        f"  Generated: {today}  |  Elapsed: {elapsed_secs:.1f}s",
        f"{'=' * 80}",
        "",
        "UNIVERSE",
        f"  Tickers requested:  {num_tickers_requested:,}",
        f"  Tickers with data:  {num_tickers_fetched:,}",
        f"  Exchanges:          {', '.join(sorted(by_exchange.keys()))}",
        "",
        "SUMMARY",
        f"  Total simulated trades: {total:,}",
        f"  Wins:                   {len(wins):,}",
        f"  Losses:                 {len(losses):,}",
        f"  Win rate:               {win_rate:.1%}",
        f"  Profit factor:          {profit_factor:.2f}",
        f"  Total PnL (per share):  {total_pnl:+,.4f}",
        f"  Avg PnL %:              {avg_pnl_pct:+.4f}%",
        f"  Avg rung achieved:      {avg_rung:.1f}",
        f"  Avg hold (bars):        {sum(t.hold_bars for t in all_trades) / max(total, 1):.0f}",
        "",
        "HYPOTHETICAL EQUITY (10K starting, 10% Kelly fraction)",
        f"  Starting equity:  GBP {STARTING_EQUITY:,.2f}",
        f"  Ending equity:    GBP {equity:,.2f}",
        f"  Return:           {((equity - STARTING_EQUITY) / STARTING_EQUITY) * 100:+.2f}%",
        f"  Max drawdown:     {max_drawdown:.1%}",
        "",
    ]

    # --- PER-EXCHANGE BREAKDOWN ---
    lines += ["PER-EXCHANGE PERFORMANCE", "-" * 80]
    lines.append(f"  {'Exchange':12s} {'Trades':>8s} {'Wins':>7s} {'WR':>6s} {'PF':>7s} {'PnL/sh':>12s} {'Avg Rung':>10s}")
    for exchange in sorted(by_exchange.keys()):
        tt = by_exchange[exchange]
        n, w, wr, tp, pf = _stats_line(tt)
        ar = sum(t.rung_achieved for t in tt) / n if n > 0 else 0
        lines.append(
            f"  {exchange:12s} {n:8,d} {w:7,d} {wr:6.1%} {pf:7.2f} {tp:+12.4f} {ar:10.1f}"
        )

    # --- PER-HOUR-OF-DAY BREAKDOWN ---
    if by_hour:
        lines += ["", "PER-HOUR-OF-DAY PERFORMANCE", "-" * 80]
        lines.append(f"  {'Hour':6s} {'Trades':>8s} {'Wins':>7s} {'WR':>6s} {'PF':>7s} {'PnL/sh':>12s}")
        for hour in sorted(by_hour.keys()):
            tt = by_hour[hour]
            n, w, wr, tp, pf = _stats_line(tt)
            lines.append(
                f"  {hour:02d}:00  {n:8,d} {w:7,d} {wr:6.1%} {pf:7.2f} {tp:+12.4f}"
            )

    # --- PER-DAY-OF-WEEK BREAKDOWN ---
    if by_weekday:
        lines += ["", "PER-DAY-OF-WEEK PERFORMANCE", "-" * 80]
        lines.append(f"  {'Day':6s} {'Trades':>8s} {'Wins':>7s} {'WR':>6s} {'PF':>7s} {'PnL/sh':>12s}")
        for wd in sorted(by_weekday.keys()):
            tt = by_weekday[wd]
            n, w, wr, tp, pf = _stats_line(tt)
            name = WEEKDAY_NAMES[wd] if 0 <= wd < 7 else f"Day{wd}"
            lines.append(
                f"  {name:6s} {n:8,d} {w:7,d} {wr:6.1%} {pf:7.2f} {tp:+12.4f}"
            )

    # --- PER-ENTRY-TYPE ---
    lines += ["", "PER-ENTRY-TYPE PERFORMANCE", "-" * 80]
    lines.append(f"  {'Type':10s} {'Trades':>8s} {'Wins':>7s} {'WR':>6s} {'PF':>7s} {'PnL/sh':>12s} {'Avg Rung':>10s}")
    for etype in ["TypeA", "TypeB", "TypeC", "TypeD"]:
        tt = by_type.get(etype, [])
        if not tt:
            lines.append(f"  {etype:10s} {'0':>8s} {'--':>7s} {'--':>6s} {'--':>7s} {'--':>12s} {'--':>10s}")
            continue
        n, w, wr, tp, pf = _stats_line(tt)
        ar = sum(t.rung_achieved for t in tt) / n
        lines.append(
            f"  {etype:10s} {n:8,d} {w:7,d} {wr:6.1%} {pf:7.2f} {tp:+12.4f} {ar:10.1f}"
        )

    # --- TOP 20 WINNERS ---
    if all_trades:
        sorted_by_pnl_pct = sorted(all_trades, key=lambda t: t.pnl_pct, reverse=True)
        top_winners = sorted_by_pnl_pct[:20]
        lines += ["", "TOP 20 WINNERS", "-" * 80]
        lines.append(f"  {'#':>3s} {'Ticker':14s} {'Date':12s} {'Type':8s} {'Exch':8s} {'PnL%':>8s} {'PnL/sh':>10s} {'Rung':>5s} {'Regime':>14s}")
        for i, t in enumerate(top_winners, 1):
            lines.append(
                f"  {i:3d} {t.ticker:14s} {t.date[:10]:12s} {t.entry_type:8s} {t.exchange:8s} "
                f"{t.pnl_pct:+8.2f} {t.pnl:+10.4f} {t.rung_achieved:5d} {t.regime:>14s}"
            )

    # --- TOP 20 LOSERS ---
    if all_trades:
        top_losers = sorted_by_pnl_pct[-20:]
        lines += ["", "TOP 20 LOSERS", "-" * 80]
        lines.append(f"  {'#':>3s} {'Ticker':14s} {'Date':12s} {'Type':8s} {'Exch':8s} {'PnL%':>8s} {'PnL/sh':>10s} {'Rung':>5s} {'Regime':>14s}")
        for i, t in enumerate(top_losers, 1):
            lines.append(
                f"  {i:3d} {t.ticker:14s} {t.date[:10]:12s} {t.entry_type:8s} {t.exchange:8s} "
                f"{t.pnl_pct:+8.2f} {t.pnl:+10.4f} {t.rung_achieved:5d} {t.regime:>14s}"
            )

    # --- PER-TICKER (top 50 by trade count) ---
    ticker_by_count = sorted(by_ticker.items(), key=lambda x: len(x[1]), reverse=True)
    lines += ["", "PER-TICKER PERFORMANCE (top 50 by trade count)", "-" * 80]
    lines.append(f"  {'Ticker':14s} {'Exch':8s} {'Trades':>8s} {'Wins':>7s} {'WR':>6s} {'PF':>7s} {'PnL/sh':>12s} {'Regime':>14s}")
    for ticker, tt in ticker_by_count[:50]:
        n, w, wr, tp, pf = _stats_line(tt)
        regime = tt[0].regime if tt else "?"
        exchange = tt[0].exchange if tt else "?"
        lines.append(
            f"  {ticker:14s} {exchange:8s} {n:8,d} {w:7,d} {wr:6.1%} {pf:7.2f} {tp:+12.4f} {regime:>14s}"
        )

    # --- PER-DAY (last 30 days shown to keep report manageable) ---
    lines += ["", "PER-DAY PERFORMANCE (last 30 days shown)", "-" * 80]
    lines.append(f"  {'Date':12s} {'Trades':>8s} {'Wins':>7s} {'WR':>6s} {'PnL/sh':>12s}")
    day_items = sorted(by_day.items())
    for day, day_trades in day_items[-30:]:
        n, w, wr, tp, pf = _stats_line(day_trades)
        lines.append(
            f"  {day:12s} {n:8,d} {w:7,d} {wr:6.1%} {tp:+12.4f}"
        )
    if len(day_items) > 30:
        lines.append(f"  ... ({len(day_items) - 30} earlier days omitted)")

    # Best/worst
    if all_trades:
        best = max(all_trades, key=lambda t: t.pnl_pct)
        worst = min(all_trades, key=lambda t: t.pnl_pct)
        lines += [
            "",
            f"BEST TRADE:  {best.ticker} ({best.exchange}) on {best.date} - {best.entry_type} "
            f"PnL={best.pnl:+.4f} ({best.pnl_pct:+.2f}%) rung={best.rung_achieved}",
            f"WORST TRADE: {worst.ticker} ({worst.exchange}) on {worst.date} - {worst.entry_type} "
            f"PnL={worst.pnl:+.4f} ({worst.pnl_pct:+.2f}%) rung={worst.rung_achieved}",
        ]

    lines += [
        "",
        f"{'=' * 80}",
        "",
    ]

    report_text = "\n".join(lines)
    report_path.write_text(report_text)
    log.info("Simulation report written: %s", report_path)

    # Also save JSON sidecar
    json_path = REPORTS_DIR / f"backfill_sim_{today}.json"
    json_data = {
        "date": today,
        "universe_size": num_tickers_requested,
        "tickers_with_data": num_tickers_fetched,
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "total_pnl_per_share": total_pnl,
        "avg_pnl_pct": avg_pnl_pct,
        "avg_rung": avg_rung,
        "starting_equity": STARTING_EQUITY,
        "ending_equity": equity,
        "return_pct": ((equity - STARTING_EQUITY) / STARTING_EQUITY) * 100,
        "max_drawdown_pct": max_drawdown * 100,
        "per_exchange": {
            ex: {
                "trades": len(tt),
                "wins": sum(1 for x in tt if x.pnl > 0),
                "win_rate": sum(1 for x in tt if x.pnl > 0) / len(tt) if tt else 0,
                "total_pnl": sum(x.pnl for x in tt),
            }
            for ex, tt in by_exchange.items()
        },
        "per_entry_type": {
            et: {
                "trades": len(tt),
                "wins": sum(1 for x in tt if x.pnl > 0),
                "total_pnl": sum(x.pnl for x in tt),
            }
            for et, tt in by_type.items()
        },
        "per_hour": {
            str(h): {
                "trades": len(tt),
                "wins": sum(1 for x in tt if x.pnl > 0),
                "win_rate": sum(1 for x in tt if x.pnl > 0) / len(tt) if tt else 0,
                "total_pnl": sum(x.pnl for x in tt),
            }
            for h, tt in sorted(by_hour.items())
        },
        "per_weekday": {
            WEEKDAY_NAMES[wd] if 0 <= wd < 7 else f"Day{wd}": {
                "trades": len(tt),
                "wins": sum(1 for x in tt if x.pnl > 0),
                "win_rate": sum(1 for x in tt if x.pnl > 0) / len(tt) if tt else 0,
                "total_pnl": sum(x.pnl for x in tt),
            }
            for wd, tt in sorted(by_weekday.items())
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

    # --- Per-exchange summary for feedback ---
    by_exchange: Dict[str, List[SimTrade]] = defaultdict(list)
    for t in all_trades:
        by_exchange[t.exchange].append(t)

    exchange_summary = {}
    for ex, trades in by_exchange.items():
        n = len(trades)
        ex_wr = sum(1 for t in trades if t.pnl > 0) / n if n > 0 else 0
        exchange_summary[ex] = {
            "trades": n,
            "win_rate": round(ex_wr, 4),
            "avg_pnl_pct": round(sum(t.pnl_pct for t in trades) / n, 4) if n > 0 else 0,
        }

    # --- Build feedback payload ---
    feedback = {
        "backfill_date": today,
        "simulated_trades_count": total,
        "simulated_win_rate": round(win_rate, 4),
        "simulated_avg_return": round(avg_return, 4),
        "strategy_confidence_delta": strategy_confidence_delta,
        "recommended_parameter_changes": recommended_parameter_changes,
        "per_exchange_summary": exchange_summary,
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
def run_backfill(
    days: int = 7,
    interval: str = "5m",
    universe_path: Optional[str] = None,
    use_blacklist: bool = False,
) -> int:
    """Execute the backfill simulation with chunked processing for memory management.

    Args:
        days: Lookback period in days.
        interval: Bar interval (1m, 5m, 60m, 1d, etc.).
        universe_path: Path to universe file (one ticker per line). If None, uses PRIMARY_TICKERS.
        use_blacklist: If True, load and apply blacklist from config.toml.
    """
    start = time.monotonic()

    # --- Determine ticker list ---
    if universe_path:
        tickers = load_universe_file(universe_path)
        if not tickers:
            log.error("No tickers loaded from universe file: %s", universe_path)
            return 1
    else:
        tickers = list(PRIMARY_TICKERS)

    # --- Load and apply blacklist ---
    blacklist: Set[str] = set()
    if use_blacklist:
        blacklist = load_blacklist_from_config()

    if blacklist:
        before = len(tickers)
        tickers = [t for t in tickers if t not in blacklist]
        log.info("Blacklist removed %d tickers (%d -> %d)", before - len(tickers), before, len(tickers))

    num_tickers_requested = len(tickers)
    log.info(
        "Ouroboros v7.0 Backfill Simulator starting (%dd, %s bars, %d tickers, chunks of %d)...",
        days, interval, num_tickers_requested, CHUNK_SIZE,
    )

    period = f"{days}d"

    # --- Chunked processing: download and simulate in chunks to limit memory ---
    all_trades: List[SimTrade] = []
    num_tickers_fetched = 0
    num_chunks = math.ceil(len(tickers) / CHUNK_SIZE)

    for chunk_idx in range(num_chunks):
        chunk_start = chunk_idx * CHUNK_SIZE
        chunk_end = min(chunk_start + CHUNK_SIZE, len(tickers))
        chunk_tickers = tickers[chunk_start:chunk_end]

        log.info(
            "--- Chunk %d/%d: tickers %d-%d (%d tickers) ---",
            chunk_idx + 1, num_chunks, chunk_start + 1, chunk_end, len(chunk_tickers),
        )

        # Parallel download for this chunk
        data = fetch_historical_data_parallel(chunk_tickers, period=period, interval=interval)
        num_tickers_fetched += len(data)

        if not data:
            log.warning("Chunk %d: no data fetched, skipping", chunk_idx + 1)
            continue

        # Simulate trades for each ticker in the chunk
        chunk_trades = 0
        for ticker, df in data.items():
            trades = simulate_ticker(ticker, df)
            all_trades.extend(trades)
            chunk_trades += len(trades)

        log.info(
            "Chunk %d complete: %d tickers with data, %d trades simulated",
            chunk_idx + 1, len(data), chunk_trades,
        )

        # Free chunk data to manage memory on 4GB EC2
        del data
        gc.collect()

    elapsed = time.monotonic() - start
    log.info(
        "Simulation complete: %d total trades from %d/%d tickers in %.1fs",
        len(all_trades), num_tickers_fetched, num_tickers_requested, elapsed,
    )

    if not all_trades:
        log.error("No trades simulated. Check ticker data availability.")
        return 1

    # Generate report
    report = generate_simulation_report(
        all_trades, elapsed, tickers, num_tickers_requested, num_tickers_fetched,
    )
    print(report)

    # Export feedback for nightly learning loop (ISS-018)
    export_backfill_feedback(all_trades)

    return 0


def main():
    """CLI entry point."""
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Backfill] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Ouroboros v7.0 Backfill Simulator")
    parser.add_argument("--days", type=int, default=7, help="Lookback days (default: 7)")
    parser.add_argument("--interval", type=str, default="5m",
                        help="Bar interval: 1m (7d max), 5m (59d max), 60m/1h (730d max), 1d (unlimited)")
    parser.add_argument("--universe", type=str, default=None,
                        help="Path to universe file with one ticker per line (skip # comments)")
    parser.add_argument("--blacklist", action="store_true", default=False,
                        help="Apply blacklist from config.toml [blacklist] section")
    parser.add_argument("--workers", type=int, default=DOWNLOAD_WORKERS,
                        help=f"Parallel download workers (default: {DOWNLOAD_WORKERS})")
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE,
                        help=f"Tickers per processing chunk (default: {CHUNK_SIZE})")
    args = parser.parse_args()

    # Allow runtime override of concurrency settings
    # Note: We modify the module-level variables directly for downstream functions
    import python_brain.ouroboros.backfill_simulator as _self_mod
    _self_mod.DOWNLOAD_WORKERS = args.workers
    _self_mod.CHUNK_SIZE = args.chunk_size

    # Enforce yfinance limits
    max_days = {"1m": 7, "2m": 59, "5m": 59, "15m": 59, "30m": 59,
                "60m": 730, "1h": 730, "90m": 59, "1d": 9999}
    limit = max_days.get(args.interval, 59)

    try:
        sys.exit(run_backfill(
            days=min(args.days, limit),
            interval=args.interval,
            universe_path=args.universe,
            use_blacklist=args.blacklist,
        ))
    except Exception as e:
        log.error("Backfill simulator crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
