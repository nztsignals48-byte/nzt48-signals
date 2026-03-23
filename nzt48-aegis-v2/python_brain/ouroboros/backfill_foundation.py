"""Ouroboros v6.0 — Backfill Foundation (Synthetic WAL Generator).

Creates a synthetic WAL-format dataset from historical OHLCV data so the
nightly learning loop (nightly_v6.py) can be tested and calibrated without
waiting for live trades.  It is a "what would have happened" simulator.

Simulation strategy:
  - 5-period EMA crosses above 20-period EMA on 5-min bars → enter long
  - Exit when EMA crosses back, price drops 2% from entry, or held 4 hours

Output:
  - One ndjson file per day in /data/backfill/  (backfill_{date}.ndjson)
  - Summary JSON: /data/backfill/backfill_summary.json

Usage:
  python3 -m python_brain.ouroboros.backfill_foundation --days 30 --dry-run
  python3 -m python_brain.ouroboros.backfill_foundation --days 30
  python3 -m python_brain.ouroboros.backfill_foundation --tickers QQQ3.L,NVD3.L --days 7
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
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

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
BACKFILL_DIR = DATA_DIR / "backfill"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Backfill-Foundation] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("backfill_foundation")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
from python_brain.ouroboros.contract_loader import load_all_symbols

DEFAULT_TICKERS = load_all_symbols()

TICKER_ID_MAP = {sym: i for i, sym in enumerate(DEFAULT_TICKERS)}

# EMA periods for the momentum crossover strategy
EMA_FAST = 5
EMA_SLOW = 20

# Exit parameters
STOP_LOSS_PCT = 0.02       # 2% stop loss
MAX_HOLD_BARS = 48         # 4 hours at 5-min bars = 48 bars
COMMISSION_PER_TRADE = 1.50  # GBP per leg — round trip is 3.00

# Rung thresholds based on MFE percentage
RUNG_THRESHOLDS = [
    (0.03, 5),   # MFE > 3% → rung 5
    (0.02, 4),   # MFE > 2% → rung 4
    (0.01, 3),   # MFE > 1% → rung 3
    (0.005, 2),  # MFE > 0.5% → rung 2
]

# Default quantity per trade
DEFAULT_QTY = 10

# Strategies — we label all backfill trades with this
BACKFILL_STRATEGY = "Backfill"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class SimulatedTrade:
    """A single simulated trade before WAL serialisation."""
    ticker: str
    ticker_id: int
    entry_price: float
    exit_price: float
    entry_time_ns: int
    exit_time_ns: int
    qty: int
    mae: float              # worst unrealised PnL (negative)
    mfe: float              # best unrealised PnL (positive)
    spread_at_entry_pct: float
    spread_at_exit_pct: float
    entry_rvol: float
    entry_hurst: float
    entry_adx: float
    entry_session_phase: str
    hold_time_mins: int
    vwap_dist_at_entry_pct: float
    atr_pct_at_entry: float
    vol_slope_at_entry: float
    date_str: str           # YYYY-MM-DD for file grouping


# ---------------------------------------------------------------------------
# EMA computation
# ---------------------------------------------------------------------------
def compute_ema(prices: np.ndarray, period: int) -> np.ndarray:
    """Compute Exponential Moving Average. Returns array same length as input."""
    n = len(prices)
    ema = np.full(n, np.nan)
    if n < period:
        return ema
    # Seed with SMA
    ema[period - 1] = np.mean(prices[:period])
    alpha = 2.0 / (period + 1)
    for i in range(period, n):
        ema[i] = alpha * prices[i] + (1.0 - alpha) * ema[i - 1]
    return ema


# ---------------------------------------------------------------------------
# Indicator approximations
# ---------------------------------------------------------------------------
def estimate_spread_pct(high: float, low: float, close: float) -> float:
    """Rough spread estimate from bar range: (high - low) / close * 0.1."""
    if close <= 0:
        return 0.0
    return max(0.0, (high - low) / close * 0.1)


def compute_rvol(volumes: np.ndarray, idx: int, window: int = 20) -> float:
    """Relative volume: current bar volume / trailing average."""
    if idx < window or volumes[idx] <= 0:
        return 1.0
    avg = np.mean(volumes[idx - window:idx])
    if avg <= 0:
        return 1.0
    return float(volumes[idx] / avg)


def estimate_hurst(returns: np.ndarray) -> float:
    """Simple Hurst exponent estimate from returns autocorrelation.

    Uses R/S analysis over a short window. Returns ~0.5 for random walk,
    >0.5 for trending, <0.5 for mean-reverting.
    """
    n = len(returns)
    if n < 20:
        return 0.5
    # Simplified R/S over the last 20 returns
    segment = returns[-20:]
    mean_r = np.mean(segment)
    deviations = np.cumsum(segment - mean_r)
    r = np.max(deviations) - np.min(deviations)
    s = np.std(segment, ddof=1)
    if s < 1e-10:
        return 0.5
    rs = r / s
    if rs <= 0:
        return 0.5
    # H = log(R/S) / log(n)
    h = np.log(rs) / np.log(n)
    return float(np.clip(h, 0.0, 1.0))


def approximate_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                     idx: int, period: int = 14) -> float:
    """Approximate ADX from directional movement over a lookback window.

    Returns a 0-100 value.  This is a simplified calculation — sufficient
    for synthetic backfill data but not suitable for live trading signals.
    """
    if idx < period + 1:
        return 25.0  # Default neutral value

    plus_dm_sum = 0.0
    minus_dm_sum = 0.0
    tr_sum = 0.0

    for i in range(idx - period, idx):
        up_move = highs[i + 1] - highs[i]
        down_move = lows[i] - lows[i + 1]

        plus_dm = max(up_move, 0.0) if up_move > down_move else 0.0
        minus_dm = max(down_move, 0.0) if down_move > up_move else 0.0

        hl = highs[i + 1] - lows[i + 1]
        hc = abs(highs[i + 1] - closes[i])
        lc = abs(lows[i + 1] - closes[i])
        tr = max(hl, hc, lc)

        plus_dm_sum += plus_dm
        minus_dm_sum += minus_dm
        tr_sum += tr

    if tr_sum < 1e-10:
        return 25.0

    plus_di = (plus_dm_sum / tr_sum) * 100.0
    minus_di = (minus_dm_sum / tr_sum) * 100.0
    dx = abs(plus_di - minus_di) / max(plus_di + minus_di, 1e-10) * 100.0
    # ADX is a smoothed DX — for backfill we just return DX as the approximation
    return float(np.clip(dx, 0.0, 100.0))


def compute_atr_pct(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                     idx: int, period: int = 14) -> float:
    """ATR as a percentage of price over the trailing period."""
    if idx < period + 1 or closes[idx] <= 0:
        return 2.5  # Default for leveraged ETPs
    tr_values = []
    for i in range(idx - period + 1, idx + 1):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr_values.append(max(hl, hc, lc))
    atr = np.mean(tr_values)
    return float(atr / closes[idx] * 100.0)


def compute_volume_slope(volumes: np.ndarray, idx: int, window: int = 10) -> float:
    """Linear regression slope of volume over the lookback window, normalised."""
    if idx < window:
        return 0.0
    segment = volumes[idx - window:idx].astype(np.float64)
    avg_vol = np.mean(segment)
    if avg_vol <= 0:
        return 0.0
    x = np.arange(window, dtype=np.float64)
    x_mean = np.mean(x)
    numerator = np.sum((x - x_mean) * (segment - avg_vol))
    denominator = np.sum((x - x_mean) ** 2)
    if abs(denominator) < 1e-10:
        return 0.0
    slope = numerator / denominator
    return float(slope / avg_vol)


def classify_session_phase(hour_utc: int) -> str:
    """Map UTC hour to LSE session phase."""
    if hour_utc < 8:
        return "open_auction"
    elif hour_utc < 10:
        return "morning"
    elif hour_utc < 13:
        return "midday"
    elif hour_utc < 16:
        return "afternoon"
    else:
        return "close_auction"


def classify_regime(hurst: float) -> str:
    """Map Hurst to regime label."""
    if hurst > 0.6:
        return "Trending"
    elif hurst < 0.4:
        return "MeanReverting"
    return "Normal"


# ---------------------------------------------------------------------------
# Core simulation
# ---------------------------------------------------------------------------
def simulate_trades_for_ticker(
    ticker: str,
    df: Any,
    ticker_id: int,
) -> List[SimulatedTrade]:
    """Run the EMA crossover strategy on 5-min bars for one ticker.

    Entry: 5-period EMA crosses above 20-period EMA.
    Exit: (a) EMA crosses back, (b) -2% from entry, (c) 4h max hold.
    """
    trades: List[SimulatedTrade] = []

    closes = df["Close"].values.astype(np.float64)
    highs = df["High"].values.astype(np.float64)
    lows = df["Low"].values.astype(np.float64)
    volumes = df["Volume"].values.astype(np.float64)

    n = len(closes)
    if n < EMA_SLOW + 5:
        log.warning("%s: only %d bars, need %d+ — skipping", ticker, n, EMA_SLOW + 5)
        return trades

    # Compute EMAs
    ema_fast = compute_ema(closes, EMA_FAST)
    ema_slow = compute_ema(closes, EMA_SLOW)

    # Returns for Hurst estimation
    log_returns = np.diff(np.log(np.maximum(closes, 1e-10)))

    # Get timestamps from DataFrame index
    timestamps = df.index

    # Scan for crossover entries
    i = EMA_SLOW  # Start after slow EMA is valid
    while i < n - 2:
        # Skip if EMAs not yet valid
        if np.isnan(ema_fast[i]) or np.isnan(ema_slow[i]):
            i += 1
            continue
        if np.isnan(ema_fast[i - 1]) or np.isnan(ema_slow[i - 1]):
            i += 1
            continue

        # Detect bullish crossover: fast crosses above slow
        prev_above = ema_fast[i - 1] > ema_slow[i - 1]
        curr_above = ema_fast[i] > ema_slow[i]

        if not prev_above and curr_above:
            entry_bar = i
            entry_price = closes[entry_bar]
            if entry_price <= 0:
                i += 1
                continue

            # Compute indicators at entry
            entry_rvol = compute_rvol(volumes, entry_bar)
            entry_hurst = estimate_hurst(log_returns[:entry_bar]) if entry_bar > 20 else 0.5
            entry_adx = approximate_adx(highs, lows, closes, entry_bar)
            entry_atr_pct = compute_atr_pct(highs, lows, closes, entry_bar)
            entry_vol_slope = compute_volume_slope(volumes, entry_bar)
            entry_spread = estimate_spread_pct(highs[entry_bar], lows[entry_bar], closes[entry_bar])

            # VWAP proxy: distance from intraday mean price
            if entry_bar >= 20:
                vwap_proxy = np.mean(closes[entry_bar - 20:entry_bar + 1])
                vwap_dist = abs(entry_price - vwap_proxy) / vwap_proxy * 100.0
            else:
                vwap_dist = 0.0

            # Entry timestamp
            entry_ts = timestamps[entry_bar]
            if hasattr(entry_ts, 'timestamp'):
                entry_time_ns = int(entry_ts.timestamp() * 1e9)
                entry_hour = entry_ts.hour
            else:
                entry_time_ns = int(time.time() * 1e9)
                entry_hour = 10

            session_phase = classify_session_phase(entry_hour)

            # Simulate exit
            stop_price = entry_price * (1.0 - STOP_LOSS_PCT)
            exit_bar = None
            exit_price = None
            track_mae = 0.0
            track_mfe = 0.0

            for j in range(entry_bar + 1, min(entry_bar + MAX_HOLD_BARS + 1, n)):
                # Track MAE/MFE
                bar_pnl_low = (lows[j] - entry_price)
                bar_pnl_high = (highs[j] - entry_price)
                track_mae = min(track_mae, bar_pnl_low)
                track_mfe = max(track_mfe, bar_pnl_high)

                # Check stop loss
                if lows[j] <= stop_price:
                    exit_bar = j
                    exit_price = stop_price  # Assume stop fills at stop price
                    break

                # Check EMA cross back (fast below slow)
                if not np.isnan(ema_fast[j]) and not np.isnan(ema_slow[j]):
                    if ema_fast[j] < ema_slow[j]:
                        exit_bar = j
                        exit_price = closes[j]
                        break

            # Max hold time exit
            if exit_bar is None:
                exit_bar = min(entry_bar + MAX_HOLD_BARS, n - 1)
                exit_price = closes[exit_bar]
                # Complete MAE/MFE up to forced exit
                for j in range(entry_bar + 1, exit_bar + 1):
                    track_mae = min(track_mae, lows[j] - entry_price)
                    track_mfe = max(track_mfe, highs[j] - entry_price)

            # Exit timestamp
            exit_ts = timestamps[exit_bar]
            if hasattr(exit_ts, 'timestamp'):
                exit_time_ns = int(exit_ts.timestamp() * 1e9)
            else:
                exit_time_ns = entry_time_ns + (exit_bar - entry_bar) * 5 * 60 * 1_000_000_000

            hold_mins = (exit_bar - entry_bar) * 5

            # Date string from entry timestamp
            if hasattr(entry_ts, 'strftime'):
                date_str = entry_ts.strftime("%Y-%m-%d")
            elif hasattr(entry_ts, 'date'):
                date_str = str(entry_ts.date())
            else:
                date_str = datetime.fromtimestamp(
                    entry_time_ns / 1e9, tz=timezone.utc
                ).strftime("%Y-%m-%d")

            exit_spread = estimate_spread_pct(highs[exit_bar], lows[exit_bar], closes[exit_bar])

            trades.append(SimulatedTrade(
                ticker=ticker,
                ticker_id=ticker_id,
                entry_price=entry_price,
                exit_price=exit_price,
                entry_time_ns=entry_time_ns,
                exit_time_ns=exit_time_ns,
                qty=DEFAULT_QTY,
                mae=track_mae,
                mfe=track_mfe,
                spread_at_entry_pct=entry_spread,
                spread_at_exit_pct=exit_spread,
                entry_rvol=entry_rvol,
                entry_hurst=entry_hurst,
                entry_adx=entry_adx,
                entry_session_phase=session_phase,
                hold_time_mins=hold_mins,
                vwap_dist_at_entry_pct=vwap_dist,
                atr_pct_at_entry=entry_atr_pct,
                vol_slope_at_entry=entry_vol_slope,
                date_str=date_str,
            ))

            # Jump past the exit bar to avoid overlapping trades
            i = exit_bar + 1
            continue

        i += 1

    return trades


# ---------------------------------------------------------------------------
# WAL event generation
# ---------------------------------------------------------------------------
def compute_rung(mfe_pct: float) -> int:
    """Map MFE percentage to chandelier rung (1-5)."""
    for threshold, rung in RUNG_THRESHOLDS:
        if mfe_pct >= threshold:
            return rung
    return 1


def make_wal_event(trade: SimulatedTrade) -> dict:
    """Convert a SimulatedTrade into a WAL PositionClosed event dict.

    Follows the exact schema from rust_core/src/types/wal.rs.
    """
    gross_pnl = (trade.exit_price - trade.entry_price) * trade.qty
    total_commission = COMMISSION_PER_TRADE * 2  # round trip
    final_pnl = gross_pnl - total_commission

    # MFE percentage for rung calculation
    mfe_pct = trade.mfe / max(trade.entry_price, 1e-10)
    rung = compute_rung(mfe_pct)

    # Confidence: base 60 + proportional to MFE/entry ratio, capped at 95
    confidence = min(95.0, max(50.0, 60.0 + mfe_pct * 500.0))

    regime = classify_regime(trade.entry_hurst)

    event_id = str(uuid.uuid4())
    event_time_ns = trade.entry_time_ns
    write_time_ns = trade.entry_time_ns + 100  # ~100ns later

    return {
        "event_id": event_id,
        "schema_version": 1,
        "event_time_ns": event_time_ns,
        "write_time_ns": write_time_ns,
        "checksum": 0,
        "payload": {
            "PositionClosed": {
                "ticker_id": trade.ticker_id,
                "final_pnl": round(final_pnl, 4),
                "entry_time_ns": trade.entry_time_ns,
                "exit_time_ns": trade.exit_time_ns,
                "gross_pnl": round(gross_pnl, 4),
                "total_commission": round(total_commission, 2),
                "spread_at_entry_pct": round(trade.spread_at_entry_pct, 4),
                "spread_at_exit_pct": round(trade.spread_at_exit_pct, 4),
                "daily_trade_number": 1,
                "symbol": trade.ticker,
                "qty": trade.qty,
                "regime_at_entry": regime,
                "confidence": round(confidence, 1),
                "highest_rung": rung,
                "strategy": BACKFILL_STRATEGY,
                "exchange": "LSEETF",
                "entry_price": round(trade.entry_price, 4),
                "exit_price": round(trade.exit_price, 4),
                "entry_rvol": round(trade.entry_rvol, 2),
                "entry_hurst": round(trade.entry_hurst, 3),
                "entry_adx": round(trade.entry_adx, 1),
                "mae": round(trade.mae, 4),
                "mfe": round(trade.mfe, 4),
                "hold_time_mins": trade.hold_time_mins,
                "entry_session_phase": trade.entry_session_phase,
                "vwap_dist_at_entry_pct": round(trade.vwap_dist_at_entry_pct, 3),
                "atr_pct_at_entry": round(trade.atr_pct_at_entry, 2),
                "vix_at_entry": 18.0,  # Placeholder — no live VIX in backfill
                "vol_slope_at_entry": round(trade.vol_slope_at_entry, 4),
                "trade_class": "",  # Left empty — filled by nightly classification
            }
        }
    }


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def fetch_data(tickers: List[str], days: int) -> Dict[str, Any]:
    """Fetch 5-minute OHLCV data via yfinance for the requested lookback."""
    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance not installed. Run: pip install yfinance")
        sys.exit(1)

    # yfinance limits 5-min data: max 60 days, must use period string
    # For > 7d we need to use start/end dates
    period_map = {
        range(1, 8): "7d",
        range(8, 31): "1mo",
        range(31, 61): "60d",
    }
    yf_period = "1mo"
    for r, p in period_map.items():
        if days in r:
            yf_period = p
            break

    data = {}
    for ticker in tickers:
        log.info("Fetching %s (period=%s, interval=5m)...", ticker, yf_period)
        try:
            df = yf.download(
                ticker,
                period=yf_period,
                interval="5m",
                progress=False,
                auto_adjust=True,
            )
            if df is None or df.empty:
                log.warning("No data for %s (may be delisted or illiquid)", ticker)
                continue
            # Flatten MultiIndex columns if present (yfinance quirk)
            if hasattr(df.columns, 'levels'):
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            data[ticker] = df
            log.info("  %s: %d bars fetched, date range %s to %s",
                     ticker, len(df),
                     str(df.index[0])[:16], str(df.index[-1])[:16])
        except Exception as e:
            log.warning("Failed to fetch %s: %s", ticker, e)

    return data


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------
def write_backfill_files(
    all_trades: List[SimulatedTrade],
    dry_run: bool,
) -> Dict[str, Any]:
    """Group trades by date, generate WAL events, write ndjson files.

    Returns a summary dict.
    """
    # Group by date
    by_date: Dict[str, List[SimulatedTrade]] = defaultdict(list)
    for t in all_trades:
        by_date[t.date_str].append(t)

    # Compute summary
    total_trades = len(all_trades)
    wins = sum(1 for t in all_trades if (t.exit_price - t.entry_price) * t.qty - COMMISSION_PER_TRADE * 2 > 0)
    total_pnl = sum(
        (t.exit_price - t.entry_price) * t.qty - COMMISSION_PER_TRADE * 2
        for t in all_trades
    )
    win_rate = wins / total_trades if total_trades > 0 else 0.0

    # Per-ticker summary
    per_ticker: Dict[str, Dict[str, Any]] = {}
    by_ticker: Dict[str, List[SimulatedTrade]] = defaultdict(list)
    for t in all_trades:
        by_ticker[t.ticker].append(t)

    for ticker, ticker_trades in sorted(by_ticker.items()):
        n = len(ticker_trades)
        t_wins = sum(
            1 for t in ticker_trades
            if (t.exit_price - t.entry_price) * t.qty - COMMISSION_PER_TRADE * 2 > 0
        )
        t_pnl = sum(
            (t.exit_price - t.entry_price) * t.qty - COMMISSION_PER_TRADE * 2
            for t in ticker_trades
        )
        avg_hold = sum(t.hold_time_mins for t in ticker_trades) / max(n, 1)
        avg_mfe = sum(t.mfe for t in ticker_trades) / max(n, 1)
        avg_mae = sum(t.mae for t in ticker_trades) / max(n, 1)
        per_ticker[ticker] = {
            "trades": n,
            "wins": t_wins,
            "win_rate": round(t_wins / n, 3) if n > 0 else 0.0,
            "total_pnl": round(t_pnl, 2),
            "avg_pnl": round(t_pnl / n, 2) if n > 0 else 0.0,
            "avg_hold_mins": round(avg_hold, 1),
            "avg_mfe": round(avg_mfe, 4),
            "avg_mae": round(avg_mae, 4),
        }

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_trades": total_trades,
        "wins": wins,
        "losses": total_trades - wins,
        "win_rate": round(win_rate, 3),
        "total_pnl_gbp": round(total_pnl, 2),
        "avg_pnl_per_trade": round(total_pnl / total_trades, 2) if total_trades > 0 else 0.0,
        "days_covered": len(by_date),
        "tickers_traded": len(by_ticker),
        "per_ticker": per_ticker,
    }

    # Print summary
    log.info("=" * 70)
    log.info("  BACKFILL FOUNDATION SUMMARY")
    log.info("=" * 70)
    log.info("  Total trades:      %d", total_trades)
    log.info("  Wins / Losses:     %d / %d", wins, total_trades - wins)
    log.info("  Win rate:          %.1f%%", win_rate * 100)
    log.info("  Total PnL:         GBP %+.2f", total_pnl)
    log.info("  Avg PnL/trade:     GBP %+.2f",
             total_pnl / total_trades if total_trades > 0 else 0.0)
    log.info("  Days covered:      %d", len(by_date))
    log.info("")
    log.info("  %-12s %6s %5s %6s %10s %8s", "TICKER", "TRADES", "WINS", "WR", "PNL", "AVG_HOLD")
    log.info("  %s", "-" * 55)
    for ticker, stats in sorted(per_ticker.items(), key=lambda x: -x[1]["total_pnl"]):
        log.info("  %-12s %6d %5d %5.0f%% %+10.2f %7.0fmin",
                 ticker, stats["trades"], stats["wins"],
                 stats["win_rate"] * 100, stats["total_pnl"],
                 stats["avg_hold_mins"])
    log.info("=" * 70)

    if dry_run:
        log.info("DRY RUN — no files written.")
        return summary

    # Write ndjson files grouped by date
    BACKFILL_DIR.mkdir(parents=True, exist_ok=True)
    files_written = 0
    events_written = 0

    for date_str, day_trades in sorted(by_date.items()):
        # Assign daily_trade_number per ticker per day
        ticker_counters: Dict[str, int] = defaultdict(int)
        output_path = BACKFILL_DIR / f"backfill_{date_str}.ndjson"

        with open(output_path, "w") as f:
            for trade in sorted(day_trades, key=lambda t: t.entry_time_ns):
                ticker_counters[trade.ticker] += 1
                event = make_wal_event(trade)
                event["payload"]["PositionClosed"]["daily_trade_number"] = ticker_counters[trade.ticker]
                f.write(json.dumps(event) + "\n")
                events_written += 1

        files_written += 1
        log.info("  Wrote %s (%d events)", output_path.name, len(day_trades))

    # Write summary
    summary_path = BACKFILL_DIR / "backfill_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    log.info("Summary written to %s", summary_path)
    log.info("Total: %d files, %d WAL events written to %s",
             files_written, events_written, BACKFILL_DIR)

    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(days: int, tickers: List[str], dry_run: bool) -> int:
    """Execute the backfill foundation pipeline."""
    start = time.monotonic()
    log.info("Backfill Foundation starting: %d days, %d tickers, dry_run=%s",
             days, len(tickers), dry_run)

    # Assign ticker IDs (use known map for defaults, sequential for custom)
    ticker_ids: Dict[str, int] = {}
    for i, t in enumerate(tickers):
        ticker_ids[t] = TICKER_ID_MAP.get(t, 100 + i)

    # Fetch data
    data = fetch_data(tickers, days)
    if not data:
        log.error("No data fetched for any ticker. Aborting.")
        return 1
    log.info("Data fetched for %d / %d tickers", len(data), len(tickers))

    # Simulate trades
    all_trades: List[SimulatedTrade] = []
    for ticker, df in data.items():
        tid = ticker_ids.get(ticker, 0)
        trades = simulate_trades_for_ticker(ticker, df, tid)
        all_trades.extend(trades)
        log.info("  %s: %d simulated trades", ticker, len(trades))

    if not all_trades:
        log.warning("No trades simulated. Check data quality or strategy parameters.")
        return 1

    # Write output
    summary = write_backfill_files(all_trades, dry_run)

    elapsed = time.monotonic() - start
    log.info("Backfill Foundation complete in %.1fs", elapsed)
    return 0


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate synthetic WAL PositionClosed events from historical OHLCV data.",
        prog="python3 -m python_brain.ouroboros.backfill_foundation",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Number of days to look back (default: 30, max: 60 per yfinance limits)",
    )
    parser.add_argument(
        "--tickers", type=str, default=None,
        help="Comma-separated ticker list (default: 12 ISA tickers)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print summary without writing files",
    )
    args = parser.parse_args()

    days = min(args.days, 60)  # yfinance 5-min ceiling
    tickers = args.tickers.split(",") if args.tickers else DEFAULT_TICKERS

    try:
        sys.exit(run(days, tickers, args.dry_run))
    except KeyboardInterrupt:
        log.info("Interrupted.")
        sys.exit(130)
    except Exception as e:
        log.error("Backfill Foundation crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
