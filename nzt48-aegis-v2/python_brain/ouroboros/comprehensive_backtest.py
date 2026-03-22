#!/usr/bin/env python3
"""Comprehensive Backtest Suite for AEGIS V2 Trading System.

Runs 9 backtests (BT-001 through BT-009, BT-010 deferred) using the
backfill_simulator engine. Downloads data ONCE, then slices/filters for
each test.

BT-001: TypeB-only with FX normalization to GBP
BT-002: Regime overlay (BULL vs BEAR via SPY 50-day SMA)
BT-003: Chandelier ATR sweep (5 values)
BT-004: Time-of-day analysis
BT-005: Per-exchange TypeB performance
BT-006: Walk-forward validation (train/test split) — CRITICAL
BT-007: Slippage sensitivity (Monte Carlo)
BT-008: Kelly fraction optimization (Monte Carlo)
BT-009: Concurrent position limit (Monte Carlo)
BT-010: DEFERRED (too slow)

Usage:
    python3 -m python_brain.ouroboros.comprehensive_backtest
"""

from __future__ import annotations

import gc
import math
import os
import sys
import time
import logging
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Path setup — MUST happen before local imports
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
os.environ.setdefault("AEGIS_ROOT", str(_PROJECT_ROOT))

from python_brain.ouroboros.backfill_simulator import (
    SimTrade,
    simulate_ticker,
    fetch_historical_data_parallel,
    detect_exchange,
    classify_entries,
    simulate_chandelier_exit,
    compute_rsi,
    compute_atr,
    CHANDELIER_RUNGS,
    CHANDELIER_RUNG_PCTS,
    CHANDELIER_ATR_PERIOD,
    RSI_PERIOD,
    RVOL_ENTRY_THRESHOLD,
    VOLUME_SURGE_MULT,
    STARTING_EQUITY,
    _stats_line,
)
from python_brain.ouroboros.contract_loader import load_yfinance_symbols
from brain.indicators.hurst import classify_regime, estimate_hurst
from brain.indicators.volume_analytics import calculate_rvol

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CompBT] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("comprehensive_backtest")

# ---------------------------------------------------------------------------
# FX rates for GBP normalization (approximate mid-market 2026-03)
# ---------------------------------------------------------------------------
FX_TO_GBP = {
    "JPY": 1.0 / 190.0,   # JPY/GBP = 190
    "HKD": 1.0 / 9.9,     # HKD/GBP = 9.9
    "USD": 1.0 / 1.27,    # USD/GBP = 1.27
    "EUR": 1.0 / 1.17,    # EUR/GBP = 1.17
    "SGD": 1.0 / 1.70,    # SGD/GBP = 1.70
    "GBP": 1.0,
}

# Ticker suffix -> currency mapping
EXCHANGE_CURRENCY = {
    ".T": "JPY",
    ".HK": "HKD",
    ".L": "GBP",   # Default for .L, but many are USD — handled via contracts.toml
    ".DE": "EUR",
    ".PA": "EUR",
    ".AS": "EUR",
    ".SI": "SGD",
}


def get_ticker_currency(ticker: str) -> str:
    """Get the trading currency for a ticker."""
    for suffix, currency in EXCHANGE_CURRENCY.items():
        if ticker.endswith(suffix):
            return currency
    return "USD"  # US stocks


def load_currency_map() -> Dict[str, str]:
    """Load precise currency mapping from contracts.toml."""
    currency_map: Dict[str, str] = {}
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        contracts_path = _PROJECT_ROOT / "config" / "contracts.toml"
        if contracts_path.exists():
            with open(contracts_path, "rb") as f:
                data = tomllib.load(f)
            for c in data.get("contracts", []):
                sym = c.get("symbol", "")
                exchange = c.get("exchange", "")
                currency = c.get("currency", "USD")
                if not sym:
                    continue
                # Build yfinance symbol
                if exchange == "LSEETF":
                    yf_sym = f"{sym}.L" if not sym.endswith(".L") else sym
                elif exchange == "TSE":
                    yf_sym = f"{sym}.T"
                elif exchange == "HKEX":
                    yf_sym = f"{sym:>04s}.HK"
                elif exchange == "SGX":
                    yf_sym = f"{sym}.SI"
                else:
                    yf_sym = sym
                currency_map[yf_sym] = currency
    except Exception as e:
        log.warning("Failed to load currency map: %s", e)
    return currency_map


# ---------------------------------------------------------------------------
# Helper: TypeB stats
# ---------------------------------------------------------------------------
def typeb_stats(trades: List[SimTrade]) -> Tuple[int, int, float, float, float, float]:
    """Return (count, wins, WR, PF, total_pnl, avg_rung) for TypeB trades."""
    tb = [t for t in trades if t.entry_type == "TypeB"]
    if not tb:
        return 0, 0, 0.0, 0.0, 0.0, 0.0
    n = len(tb)
    wins = sum(1 for t in tb if t.pnl > 0)
    wr = wins / n if n > 0 else 0.0
    gross_w = sum(t.pnl for t in tb if t.pnl > 0)
    gross_l = abs(sum(t.pnl for t in tb if t.pnl <= 0))
    pf = gross_w / max(gross_l, 1e-9)
    total_pnl = sum(t.pnl for t in tb)
    avg_rung = sum(t.rung_achieved for t in tb) / n
    return n, wins, wr, pf, total_pnl, avg_rung


def all_stats(trades: List[SimTrade]) -> Dict[str, Any]:
    """Full stats dict for a list of trades. Uses pnl_pct for cross-exchange comparison."""
    n = len(trades)
    if n == 0:
        return {"trades": 0, "wins": 0, "wr": 0.0, "pf": 0.0, "pnl_pct": 0.0,
                "avg_pnl_pct": 0.0, "avg_rung": 0.0}
    wins = sum(1 for t in trades if t.pnl > 0)
    # Filter out NaN/Inf pnl_pct values for stable stats
    valid_pnl_pcts = [t.pnl_pct for t in trades if math.isfinite(t.pnl_pct)]
    if not valid_pnl_pcts:
        valid_pnl_pcts = [0.0]
    gross_w = sum(p for p in valid_pnl_pcts if p > 0)
    gross_l = abs(sum(p for p in valid_pnl_pcts if p <= 0))
    return {
        "trades": n,
        "wins": wins,
        "wr": wins / n,
        "pf": gross_w / max(gross_l, 1e-9),
        "pnl_pct": sum(valid_pnl_pcts),
        "avg_pnl_pct": sum(valid_pnl_pcts) / len(valid_pnl_pcts),
        "avg_rung": sum(t.rung_achieved for t in trades) / n,
    }


# ---------------------------------------------------------------------------
# Custom Chandelier exit with variable initial ATR mult
# ---------------------------------------------------------------------------
def simulate_chandelier_exit_custom(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    atr: np.ndarray,
    entry_bar: int,
    entry_price: float,
    initial_atr_mult: float = 2.0,
) -> Tuple[int, float, int]:
    """Chandelier exit with configurable initial ATR multiplier.

    The rung multipliers are scaled proportionally from the initial value:
    Default rungs: [2.0, 1.8, 1.5, 1.0, 0.75]
    With initial=1.5: [1.5, 1.35, 1.125, 0.75, 0.5625]
    """
    n = len(closes)
    scale = initial_atr_mult / 2.0  # Default initial is 2.0
    rungs = [r * scale for r in CHANDELIER_RUNGS]

    highest_since_entry = entry_price
    current_rung = 0

    for i in range(entry_bar + 1, min(entry_bar + 60, n)):
        if np.isnan(atr[i]):
            continue

        highest_since_entry = max(highest_since_entry, highs[i])

        pct_gain = (highest_since_entry - entry_price) / max(entry_price, 1e-9)
        for r in range(len(CHANDELIER_RUNG_PCTS) - 1, 0, -1):
            if pct_gain >= CHANDELIER_RUNG_PCTS[r]:
                current_rung = max(current_rung, r)
                break

        rung_mult = rungs[min(current_rung, len(rungs) - 1)]
        stop_price = highest_since_entry - rung_mult * atr[i]

        if closes[i] <= stop_price or lows[i] <= stop_price:
            exit_price = max(stop_price, lows[i])
            return i, exit_price, current_rung

    exit_bar = min(entry_bar + 59, n - 1)
    return exit_bar, closes[exit_bar], current_rung


def simulate_ticker_custom_atr(ticker: str, df: Any, initial_atr_mult: float) -> List[SimTrade]:
    """Simulate trades with a custom Chandelier ATR multiplier."""
    trades: List[SimTrade] = []
    exchange = detect_exchange(ticker)

    closes = df["Close"].values.astype(np.float64).flatten()
    highs = df["High"].values.astype(np.float64).flatten()
    lows = df["Low"].values.astype(np.float64).flatten()
    volumes = df["Volume"].values.astype(np.float64).flatten()

    if len(closes) < 30:
        return trades

    rsi = compute_rsi(closes, RSI_PERIOD)
    atr = compute_atr(highs, lows, closes, CHANDELIER_ATR_PERIOD)

    rvol_arr = np.zeros(len(volumes))
    for i in range(21, len(volumes)):
        vol_list = volumes[i - 21:i].tolist()
        vol_list.append(volumes[i])
        rvol_arr[i] = calculate_rvol(vol_list, window=20)

    hurst = estimate_hurst(closes.tolist(), max_lag=20)
    regime = classify_regime(hurst)

    index_list = list(df.index)
    has_datetime = len(index_list) > 0 and hasattr(index_list[0], 'hour')

    if hasattr(df.index, 'date'):
        dates = [str(d.date()) if hasattr(d, 'date') else str(d)[:10] for d in df.index]
    else:
        dates = [str(i) for i in range(len(df))]

    entries = classify_entries(closes, volumes, rsi, rvol_arr, regime)

    for entry_bar, entry_type in entries:
        entry_price = closes[entry_bar]
        if entry_price <= 0:
            continue

        exit_bar, exit_price, rung = simulate_chandelier_exit_custom(
            closes, highs, lows, atr, entry_bar, entry_price, initial_atr_mult,
        )

        pnl = exit_price - entry_price
        pnl_pct = pnl / entry_price * 100.0

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
# Monte Carlo equity simulation
# ---------------------------------------------------------------------------
def monte_carlo_equity(
    trades: List[SimTrade],
    kelly_frac: float = 0.10,
    cost_pct: float = 0.0,
    max_concurrent: int = 999,
    n_sims: int = 200,
    starting_equity: float = STARTING_EQUITY,
) -> Dict[str, float]:
    """Monte Carlo equity simulation using pnl_pct (percentage returns).

    Shuffles trade order, applies costs and position limits.
    cost_pct is in percentage points (e.g. 0.1 means 0.1% round-trip cost).
    Returns dict with median/p10/p90 equity and Sharpe.
    """
    if not trades:
        return {"median_equity": starting_equity, "p10": starting_equity,
                "p90": starting_equity, "sharpe": 0.0, "max_dd_median": 0.0}

    # Use pnl_pct (percentage) converted to decimal fraction
    # Filter out NaN/Inf values and extreme outliers
    pnl_pcts = []
    for t in trades:
        val = t.pnl_pct / 100.0
        if math.isfinite(val) and abs(val) < 1.0:  # Cap at +/-100% per trade
            pnl_pcts.append(val)

    if not pnl_pcts:
        return {"median_equity": starting_equity, "p10": starting_equity,
                "p90": starting_equity, "sharpe": 0.0, "max_dd_median": 0.0}

    # Cap at 5000 trades per simulation (realistic: ~7 trades/day * 730 days)
    # Use random sample if more trades than cap
    MAX_TRADES_PER_SIM = 5000

    equities = []
    drawdowns = []
    cost_decimal = cost_pct / 100.0  # Convert cost_pct to decimal

    for _ in range(n_sims):
        if len(pnl_pcts) > MAX_TRADES_PER_SIM:
            shuffled = random.sample(pnl_pcts, MAX_TRADES_PER_SIM)
        else:
            shuffled = list(pnl_pcts)
        random.shuffle(shuffled)
        eq = starting_equity
        peak = eq
        max_dd = 0.0
        positions_taken = 0

        # Model concurrent positions: divide trades into batches of max_concurrent
        # Within each batch, only max_concurrent trades are taken
        batch_idx = 0
        for pnl_pct_val in shuffled:
            if batch_idx >= max_concurrent:
                batch_idx = 0  # Start new batch (simulate sequential batches)
            batch_idx += 1

            # Apply round-trip cost (slippage + commission)
            net_return = pnl_pct_val - cost_decimal

            # Position sizing: kelly fraction of current equity, divided among concurrent positions
            alloc_per_position = kelly_frac / min(max_concurrent, 10)
            trade_pnl = eq * alloc_per_position * net_return

            # Overflow protection
            if not math.isfinite(trade_pnl):
                continue
            if abs(trade_pnl) > eq * 2:  # Sanity cap
                trade_pnl = max(-eq * 0.5, min(eq * 2, trade_pnl))

            eq += trade_pnl
            positions_taken += 1

            if eq > peak:
                peak = eq
            if peak > 0:
                dd = (peak - eq) / peak
                max_dd = max(max_dd, dd)

            if eq <= 0:
                eq = 0
                break

        equities.append(eq)
        drawdowns.append(max_dd)

    equities.sort()
    drawdowns.sort()
    n = len(equities)

    # Filter out NaN/Inf from equities
    valid_equities = [e for e in equities if math.isfinite(e)]
    if not valid_equities:
        valid_equities = [starting_equity]
    vn = len(valid_equities)

    # Sharpe from trade return distribution
    if len(pnl_pcts) > 1:
        arr = np.array(pnl_pcts)
        mean_r = float(np.nanmean(arr))
        std_r = float(np.nanstd(arr, ddof=1))
        # Annualize: assume ~7 trades per day, 252 trading days
        sharpe = (mean_r / std_r) * math.sqrt(252 * 7) if std_r > 1e-9 else 0.0
    else:
        sharpe = 0.0

    valid_dd = [d for d in drawdowns if math.isfinite(d)]
    if not valid_dd:
        valid_dd = [0.0]
    vdn = len(valid_dd)

    return {
        "median_equity": valid_equities[vn // 2],
        "p10": valid_equities[vn // 10],
        "p90": valid_equities[int(vn * 0.9)],
        "sharpe": sharpe,
        "max_dd_median": valid_dd[vdn // 2],
    }


# ===================================================================
# BACKTEST FUNCTIONS
# ===================================================================

def bt001_typeb_fx(all_trades: List[SimTrade], currency_map: Dict[str, str]) -> str:
    """BT-001: TypeB-only with FX normalization to GBP."""
    log.info("Running BT-001: TypeB-only with FX normalization...")
    tb_trades = [t for t in all_trades if t.entry_type == "TypeB"]
    n, wins, wr, pf, total_pnl_raw, avg_rung = typeb_stats(all_trades)

    # Compute GBP-normalized PnL
    total_gbp_pnl = 0.0
    by_currency: Dict[str, List[SimTrade]] = defaultdict(list)

    for t in tb_trades:
        ccy = currency_map.get(t.ticker, get_ticker_currency(t.ticker))
        fx_rate = FX_TO_GBP.get(ccy, 1.0 / 1.27)  # Default USD if unknown
        gbp_pnl = t.pnl * fx_rate
        total_gbp_pnl += gbp_pnl
        by_currency[ccy].append(t)

    lines = [
        "=" * 72,
        "  BT-001: TypeB-ONLY with FX Normalization to GBP",
        "=" * 72,
        f"  Total TypeB trades:     {n:,}",
        f"  Wins:                   {wins:,}",
        f"  Win rate:               {wr:.1%}",
        f"  Profit factor:          {pf:.3f}",
        f"  Avg rung achieved:      {avg_rung:.2f}",
        f"  Raw PnL (per-share):    {total_pnl_raw:+,.4f}",
        f"  GBP-normalized PnL:     GBP {total_gbp_pnl:+,.2f}",
        "",
        "  PER-CURRENCY BREAKDOWN:",
        f"  {'Currency':>10s} {'Trades':>8s} {'Wins':>7s} {'WR':>7s} {'PF':>7s} {'AvgPnL%':>10s} {'GBP PnL':>12s}",
    ]
    for ccy in sorted(by_currency.keys()):
        trades = by_currency[ccy]
        fx = FX_TO_GBP.get(ccy, 1.0 / 1.27)
        sn, sw, swr, spf, spnl, _ = typeb_stats(trades)
        # These are already TypeB, so use all_stats
        st = all_stats(trades)
        gbp = sum(t.pnl for t in trades) * fx
        lines.append(
            f"  {ccy:>10s} {st['trades']:8,d} {st['wins']:7,d} {st['wr']:6.1%} "
            f"{st['pf']:7.3f} {st['avg_pnl_pct']:+10.4f}% {gbp:+12.2f}"
        )
    lines.append("")
    return "\n".join(lines)


def bt002_regime_overlay(all_trades: List[SimTrade], all_data: Dict[str, Any]) -> str:
    """BT-002: Regime overlay using SPY 50-day SMA."""
    log.info("Running BT-002: Regime overlay (SPY 50-day SMA)...")

    # Get SPY data
    import yfinance as yf
    spy_df = yf.download("SPY", period="730d", interval="1d", progress=False, auto_adjust=True)
    if spy_df is None or spy_df.empty:
        return "BT-002: FAILED — Could not download SPY data\n"

    # Flatten MultiIndex if present
    if hasattr(spy_df.columns, 'levels'):
        spy_df.columns = [c[0] if isinstance(c, tuple) else c for c in spy_df.columns]

    spy_close = spy_df["Close"].values.astype(np.float64).flatten()

    # Compute 50-day SMA
    sma50 = np.full(len(spy_close), np.nan)
    for i in range(49, len(spy_close)):
        sma50[i] = np.mean(spy_close[i - 49:i + 1])

    # Build date -> regime mapping
    spy_dates = [str(d.date()) if hasattr(d, 'date') else str(d)[:10] for d in spy_df.index]
    regime_map: Dict[str, str] = {}
    for i, dt_str in enumerate(spy_dates):
        if not np.isnan(sma50[i]):
            regime_map[dt_str] = "BULL" if spy_close[i] > sma50[i] else "BEAR"

    # Split trades by macro regime
    tb_trades = [t for t in all_trades if t.entry_type == "TypeB"]
    bull_trades = [t for t in tb_trades if regime_map.get(t.date[:10], "UNKNOWN") == "BULL"]
    bear_trades = [t for t in tb_trades if regime_map.get(t.date[:10], "UNKNOWN") == "BEAR"]
    unknown_trades = [t for t in tb_trades if regime_map.get(t.date[:10], "UNKNOWN") == "UNKNOWN"]

    bull_st = all_stats(bull_trades)
    bear_st = all_stats(bear_trades)

    lines = [
        "=" * 72,
        "  BT-002: Regime Overlay (SPY 50-day SMA)",
        "=" * 72,
        f"  Total TypeB trades:     {len(tb_trades):,}",
        f"  BULL regime trades:     {len(bull_trades):,}",
        f"  BEAR regime trades:     {len(bear_trades):,}",
        f"  Unclassified:           {len(unknown_trades):,}",
        "",
        f"  {'Regime':>10s} {'Trades':>8s} {'Wins':>7s} {'WR':>7s} {'PF':>7s} {'AvgPnL%':>10s} {'AvgRung':>8s}",
        f"  {'BULL':>10s} {bull_st['trades']:8,d} {bull_st['wins']:7,d} {bull_st['wr']:6.1%} "
        f"{bull_st['pf']:7.3f} {bull_st['avg_pnl_pct']:+10.4f}% {bull_st['avg_rung']:8.2f}",
        f"  {'BEAR':>10s} {bear_st['trades']:8,d} {bear_st['wins']:7,d} {bear_st['wr']:6.1%} "
        f"{bear_st['pf']:7.3f} {bear_st['avg_pnl_pct']:+10.4f}% {bear_st['avg_rung']:8.2f}",
        "",
    ]
    return "\n".join(lines)


def bt003_atr_sweep(tickers: List[str], all_data: Dict[str, Any]) -> str:
    """BT-003: Chandelier ATR sweep with 5 different initial_stop_atr values."""
    log.info("Running BT-003: Chandelier ATR sweep...")
    atr_values = [1.0, 1.5, 2.0, 2.5, 3.0]

    lines = [
        "=" * 72,
        "  BT-003: Chandelier ATR Sweep (TypeB only)",
        "=" * 72,
        f"  {'ATR Mult':>10s} {'Trades':>8s} {'Wins':>7s} {'WR':>7s} {'PF':>8s} {'AvgPnL%':>10s} {'AvgRung':>8s}",
    ]

    best_pf = 0.0
    best_atr = 2.0

    for atr_mult in atr_values:
        log.info("  ATR sweep: %.1f ...", atr_mult)
        all_trades = []
        for ticker, df in all_data.items():
            trades = simulate_ticker_custom_atr(ticker, df, atr_mult)
            all_trades.extend(trades)

        tb = [t for t in all_trades if t.entry_type == "TypeB"]
        st = all_stats(tb)

        if st["pf"] > best_pf:
            best_pf = st["pf"]
            best_atr = atr_mult

        lines.append(
            f"  {atr_mult:10.1f} {st['trades']:8,d} {st['wins']:7,d} {st['wr']:6.1%} "
            f"{st['pf']:8.3f} {st['avg_pnl_pct']:+10.4f}% {st['avg_rung']:8.2f}"
        )

    lines.extend([
        "",
        f"  >>> OPTIMAL ATR for TypeB: {best_atr:.1f} (PF={best_pf:.3f})",
        "",
    ])
    return "\n".join(lines)


def bt004_time_of_day(all_trades: List[SimTrade]) -> str:
    """BT-004: Time-of-day analysis for TypeB trades."""
    log.info("Running BT-004: Time-of-day analysis...")
    tb = [t for t in all_trades if t.entry_type == "TypeB"]

    by_hour: Dict[int, List[SimTrade]] = defaultdict(list)
    for t in tb:
        if t.entry_hour >= 0:
            by_hour[t.entry_hour].append(t)

    lines = [
        "=" * 72,
        "  BT-004: Time-of-Day Analysis (TypeB only)",
        "=" * 72,
        f"  {'Hour':>6s} {'Trades':>8s} {'Wins':>7s} {'WR':>7s} {'PF':>8s} {'AvgPnL%':>10s}",
    ]

    best_wr_hour = -1
    best_wr = 0.0

    for hour in sorted(by_hour.keys()):
        st = all_stats(by_hour[hour])
        if st["trades"] >= 10 and st["wr"] > best_wr:
            best_wr = st["wr"]
            best_wr_hour = hour
        lines.append(
            f"  {hour:02d}:00  {st['trades']:8,d} {st['wins']:7,d} {st['wr']:6.1%} "
            f"{st['pf']:8.3f} {st['avg_pnl_pct']:+10.4f}%"
        )

    if best_wr_hour >= 0:
        lines.append(f"\n  >>> Best hour for TypeB: {best_wr_hour:02d}:00 (WR={best_wr:.1%})")
    lines.append("")
    return "\n".join(lines)


def bt005_per_exchange(all_trades: List[SimTrade]) -> str:
    """BT-005: Per-exchange TypeB performance."""
    log.info("Running BT-005: Per-exchange TypeB performance...")
    tb = [t for t in all_trades if t.entry_type == "TypeB"]

    by_exchange: Dict[str, List[SimTrade]] = defaultdict(list)
    for t in tb:
        by_exchange[t.exchange].append(t)

    lines = [
        "=" * 72,
        "  BT-005: Per-Exchange TypeB Performance",
        "=" * 72,
        f"  {'Exchange':>12s} {'Trades':>8s} {'Wins':>7s} {'WR':>7s} {'PF':>8s} {'AvgPnL%':>10s} {'AvgRung':>8s}",
    ]

    for exchange in sorted(by_exchange.keys()):
        st = all_stats(by_exchange[exchange])
        lines.append(
            f"  {exchange:>12s} {st['trades']:8,d} {st['wins']:7,d} {st['wr']:6.1%} "
            f"{st['pf']:8.3f} {st['avg_pnl_pct']:+10.4f}% {st['avg_rung']:8.2f}"
        )

    lines.append("")
    return "\n".join(lines)


def bt006_walk_forward(tickers: List[str], all_data: Dict[str, Any]) -> str:
    """BT-006: Walk-forward validation — split 730 days into train (first 365) and test (last 365).

    THIS IS THE MOST IMPORTANT TEST. It detects overfitting.
    """
    log.info("Running BT-006: Walk-forward validation (CRITICAL)...")

    train_trades: List[SimTrade] = []
    test_trades: List[SimTrade] = []

    for ticker, df in all_data.items():
        n_bars = len(df)
        if n_bars < 60:
            continue

        mid = n_bars // 2

        # Train: first half
        df_train = df.iloc[:mid]
        if len(df_train) >= 30:
            train = simulate_ticker(ticker, df_train)
            train_trades.extend(train)

        # Test: second half
        df_test = df.iloc[mid:]
        if len(df_test) >= 30:
            test = simulate_ticker(ticker, df_test)
            test_trades.extend(test)

    # TypeB stats for each period
    train_all = all_stats([t for t in train_trades if t.entry_type == "TypeB"])
    test_all = all_stats([t for t in test_trades if t.entry_type == "TypeB"])

    # Also get per-type stats for both periods
    train_by_type = defaultdict(list)
    test_by_type = defaultdict(list)
    for t in train_trades:
        train_by_type[t.entry_type].append(t)
    for t in test_trades:
        test_by_type[t.entry_type].append(t)

    lines = [
        "=" * 72,
        "  BT-006: WALK-FORWARD VALIDATION (CRITICAL)",
        "=" * 72,
        f"  Data split: first half = TRAIN, second half = TEST",
        f"  Train trades (all types): {len(train_trades):,}",
        f"  Test trades (all types):  {len(test_trades):,}",
        "",
        "  TypeB COMPARISON:",
        f"  {'Period':>10s} {'Trades':>8s} {'Wins':>7s} {'WR':>7s} {'PF':>8s} {'AvgPnL%':>10s} {'AvgRung':>8s}",
        f"  {'TRAIN':>10s} {train_all['trades']:8,d} {train_all['wins']:7,d} {train_all['wr']:6.1%} "
        f"{train_all['pf']:8.3f} {train_all['avg_pnl_pct']:+10.4f}% {train_all['avg_rung']:8.2f}",
        f"  {'TEST':>10s} {test_all['trades']:8,d} {test_all['wins']:7,d} {test_all['wr']:6.1%} "
        f"{test_all['pf']:8.3f} {test_all['avg_pnl_pct']:+10.4f}% {test_all['avg_rung']:8.2f}",
        "",
    ]

    # WR degradation check
    wr_diff = test_all["wr"] - train_all["wr"]
    pf_diff = test_all["pf"] - train_all["pf"]
    lines.append(f"  WR degradation (test - train):  {wr_diff:+.1%}")
    lines.append(f"  PF degradation (test - train):  {pf_diff:+.3f}")

    if abs(wr_diff) < 0.05:
        lines.append("  >>> PASS: WR is stable across train/test (<5% difference)")
    elif wr_diff < -0.05:
        lines.append("  >>> WARNING: WR degrades significantly in test period (possible overfitting)")
    else:
        lines.append("  >>> INTERESTING: WR improves in test period (regime change or robustness)")

    # All entry types comparison
    lines.extend([
        "",
        "  ALL ENTRY TYPES:",
        f"  {'Type':>10s} {'Train_Tr':>8s} {'Train_WR':>8s} {'Train_PF':>8s}  |  {'Test_Tr':>8s} {'Test_WR':>8s} {'Test_PF':>8s}",
    ])
    for etype in ["TypeA", "TypeB", "TypeC", "TypeD"]:
        tr_st = all_stats(train_by_type.get(etype, []))
        te_st = all_stats(test_by_type.get(etype, []))
        lines.append(
            f"  {etype:>10s} {tr_st['trades']:8,d} {tr_st['wr']:7.1%} {tr_st['pf']:8.3f}  |  "
            f"{te_st['trades']:8,d} {te_st['wr']:7.1%} {te_st['pf']:8.3f}"
        )

    lines.append("")
    return "\n".join(lines)


def bt007_slippage_sensitivity(all_trades: List[SimTrade]) -> str:
    """BT-007: Slippage/cost sensitivity via Monte Carlo."""
    log.info("Running BT-007: Slippage sensitivity...")
    tb = [t for t in all_trades if t.entry_type == "TypeB"]
    cost_levels = [0.0, 0.1, 0.2, 0.3, 0.5, 1.0]

    lines = [
        "=" * 72,
        "  BT-007: Slippage/Cost Sensitivity (TypeB, Monte Carlo 200 sims)",
        "=" * 72,
        f"  {'Cost %':>8s} {'Median Eq':>12s} {'P10':>12s} {'P90':>12s} {'MaxDD Med':>10s}",
    ]

    breakeven_cost = None
    for cost in cost_levels:
        mc = monte_carlo_equity(tb, kelly_frac=0.10, cost_pct=cost)
        lines.append(
            f"  {cost:7.2f}% {mc['median_equity']:12,.2f} {mc['p10']:12,.2f} "
            f"{mc['p90']:12,.2f} {mc['max_dd_median']*100:8.1f}%"
        )
        if mc["median_equity"] < STARTING_EQUITY and breakeven_cost is None:
            breakeven_cost = cost

    if breakeven_cost is not None:
        lines.append(f"\n  >>> BREAKEVEN cost level: ~{breakeven_cost:.2f}%")
    else:
        lines.append(f"\n  >>> System profitable even at 1.0% total cost")

    lines.append("")
    return "\n".join(lines)


def bt008_kelly_optimization(all_trades: List[SimTrade]) -> str:
    """BT-008: Kelly fraction optimization via Monte Carlo."""
    log.info("Running BT-008: Kelly fraction optimization...")
    tb = [t for t in all_trades if t.entry_type == "TypeB"]
    fractions = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]

    lines = [
        "=" * 72,
        "  BT-008: Kelly Fraction Optimization (TypeB, Monte Carlo 200 sims)",
        "=" * 72,
        f"  {'Kelly %':>8s} {'Median Eq':>12s} {'P10':>12s} {'P90':>12s} {'Sharpe':>8s} {'MaxDD':>8s}",
    ]

    best_sharpe = 0.0
    best_frac = 0.10

    for frac in fractions:
        mc = monte_carlo_equity(tb, kelly_frac=frac, cost_pct=0.1)
        lines.append(
            f"  {frac*100:6.0f}% {mc['median_equity']:12,.2f} {mc['p10']:12,.2f} "
            f"{mc['p90']:12,.2f} {mc['sharpe']:8.2f} {mc['max_dd_median']*100:6.1f}%"
        )
        # Pick best risk-adjusted (use median equity / max_dd as proxy)
        risk_adj = mc["median_equity"] / max(mc["max_dd_median"], 0.001)
        if math.isfinite(risk_adj) and risk_adj > best_sharpe:
            best_sharpe = risk_adj
            best_frac = frac

    lines.append(f"\n  >>> OPTIMAL Kelly fraction: {best_frac*100:.0f}%")
    lines.append("")
    return "\n".join(lines)


def bt009_concurrent_positions(all_trades: List[SimTrade]) -> str:
    """BT-009: Concurrent position limit via Monte Carlo."""
    log.info("Running BT-009: Concurrent position limit...")
    tb = [t for t in all_trades if t.entry_type == "TypeB"]
    limits = [1, 2, 3, 5, 10]

    lines = [
        "=" * 72,
        "  BT-009: Concurrent Position Limit (TypeB, Monte Carlo 200 sims)",
        "=" * 72,
        f"  {'MaxPos':>8s} {'Median Eq':>12s} {'P10':>12s} {'P90':>12s} {'MaxDD':>8s}",
    ]

    for limit in limits:
        mc = monte_carlo_equity(tb, kelly_frac=0.10, max_concurrent=limit, cost_pct=0.1)
        lines.append(
            f"  {limit:8d} {mc['median_equity']:12,.2f} {mc['p10']:12,.2f} "
            f"{mc['p90']:12,.2f} {mc['max_dd_median']*100:6.1f}%"
        )

    lines.append("")
    return "\n".join(lines)


# ===================================================================
# MAIN
# ===================================================================

def main():
    """Run all 9 backtests."""
    overall_start = time.monotonic()

    print("=" * 72)
    print("  AEGIS V2 COMPREHENSIVE BACKTEST SUITE")
    print("  Running 9 backtests on 264 contracts.toml tickers")
    print("  Data: 730 days, 60-minute bars")
    print("=" * 72)
    print()

    # --- Step 1: Load universe ---
    log.info("Step 1: Loading ticker universe from contracts.toml...")
    tickers = load_yfinance_symbols()
    log.info("Loaded %d tickers", len(tickers))

    # --- Step 2: Load currency map ---
    currency_map = load_currency_map()
    log.info("Loaded currency map for %d tickers", len(currency_map))

    # --- Step 3: Download all data ONCE ---
    log.info("Step 2: Downloading 730 days of 60m data for %d tickers...", len(tickers))
    fetch_start = time.monotonic()
    all_data = fetch_historical_data_parallel(tickers, period="730d", interval="60m", max_workers=10)
    fetch_elapsed = time.monotonic() - fetch_start
    log.info("Downloaded data for %d/%d tickers in %.1fs", len(all_data), len(tickers), fetch_elapsed)

    if not all_data:
        log.error("No data downloaded. Aborting.")
        sys.exit(1)

    # --- Step 4: Simulate all trades with default parameters ---
    log.info("Step 3: Simulating trades with default parameters...")
    sim_start = time.monotonic()
    all_trades: List[SimTrade] = []
    for ticker, df in all_data.items():
        trades = simulate_ticker(ticker, df)
        all_trades.extend(trades)
    sim_elapsed = time.monotonic() - sim_start
    log.info("Simulated %d trades in %.1fs", len(all_trades), sim_elapsed)

    # Quick summary before backtests
    tb_count, tb_wins, tb_wr, tb_pf, tb_pnl, tb_rung = typeb_stats(all_trades)
    print(f"\n  DATA SUMMARY:")
    print(f"  Tickers with data: {len(all_data):,} / {len(tickers):,}")
    print(f"  Total trades:      {len(all_trades):,}")
    print(f"  TypeB trades:      {tb_count:,} (WR={tb_wr:.1%}, PF={tb_pf:.3f})")
    print(f"  Download time:     {fetch_elapsed:.1f}s")
    print(f"  Simulation time:   {sim_elapsed:.1f}s")
    print()

    # ===================================================================
    # Run all backtests
    # ===================================================================
    results = []

    # BT-006 FIRST (most important)
    r = bt006_walk_forward(tickers, all_data)
    results.append(r)
    print(r)

    # BT-001: TypeB + FX
    r = bt001_typeb_fx(all_trades, currency_map)
    results.append(r)
    print(r)

    # BT-003: ATR sweep
    r = bt003_atr_sweep(tickers, all_data)
    results.append(r)
    print(r)

    # BT-002: Regime overlay
    r = bt002_regime_overlay(all_trades, all_data)
    results.append(r)
    print(r)

    # BT-004: Time of day
    r = bt004_time_of_day(all_trades)
    results.append(r)
    print(r)

    # BT-005: Per-exchange
    r = bt005_per_exchange(all_trades)
    results.append(r)
    print(r)

    # BT-007: Slippage
    r = bt007_slippage_sensitivity(all_trades)
    results.append(r)
    print(r)

    # BT-008: Kelly
    r = bt008_kelly_optimization(all_trades)
    results.append(r)
    print(r)

    # BT-009: Concurrent positions
    r = bt009_concurrent_positions(all_trades)
    results.append(r)
    print(r)

    # ===================================================================
    # Final summary
    # ===================================================================
    total_elapsed = time.monotonic() - overall_start

    summary = [
        "=" * 72,
        "  FINAL SUMMARY",
        "=" * 72,
        f"  Total elapsed time:       {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)",
        f"  Tickers:                  {len(all_data):,} / {len(tickers):,}",
        f"  Total trades simulated:   {len(all_trades):,}",
        f"  TypeB trades:             {tb_count:,}",
        f"  TypeB WR:                 {tb_wr:.1%}",
        f"  TypeB PF:                 {tb_pf:.3f}",
        "",
        "  BT-010: DEFERRED (full production backtest via bridge.py — too slow for batch)",
        "",
        "=" * 72,
    ]
    summary_text = "\n".join(summary)
    print(summary_text)

    # Save full report to file
    report_dir = _PROJECT_ROOT / "data" / "backtest_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"comprehensive_backtest_{ts}.txt"
    full_report = "\n".join(results) + "\n" + summary_text
    report_path.write_text(full_report)
    log.info("Full report saved to: %s", report_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
