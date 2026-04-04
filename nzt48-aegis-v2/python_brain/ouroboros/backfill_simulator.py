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

# Live strategy overlays — wired from the full strategy module set
try:
    from python_brain.strategies.vol_compression import detect_squeeze
    _HAS_VOL_COMPRESSION = True
except ImportError:
    _HAS_VOL_COMPRESSION = False

try:
    from python_brain.strategies.calendar_anomalies import get_calendar_adjustment
    _HAS_CALENDAR_ANOMALIES = True
except ImportError:
    _HAS_CALENDAR_ANOMALIES = False

try:
    from python_brain.strategies.nav_arbitrage import NAVTracker
    _HAS_NAV_ARBITRAGE = True
except ImportError:
    _HAS_NAV_ARBITRAGE = False

try:
    from python_brain.strategies.rebalancing_flow import predict_rebalancing, ETP_REBALANCING_MAP
    _HAS_REBALANCING_FLOW = True
except ImportError:
    _HAS_REBALANCING_FLOW = False

try:
    from python_brain.strategies.fomc_drift import get_drift_signal, _EVENT_PROFILES
    _HAS_FOMC_DRIFT = True
except ImportError:
    _HAS_FOMC_DRIFT = False

try:
    from python_brain.sizing.rolling_kelly import RollingKellyEstimator, DrawdownStager, STAGE_CONFIG, DrawdownStage
    _HAS_ROLLING_KELLY = True
except ImportError:
    _HAS_ROLLING_KELLY = False

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
# Entry type config (loaded from config.toml [entry_types])
# ---------------------------------------------------------------------------
def _load_entry_type_config() -> Dict[str, Any]:
    """Load entry type thresholds from config.toml. Falls back to defaults."""
    defaults = {
        "type_a_confidence": 65.0,
        "type_a_rsi_oversold": 40.0,
        "type_a_volume_spike_mult": 1.8,
        "type_a_drop_atr_mult": 2.0,
        "type_b_confidence": 82.0,
        "type_b_rsi_low": 30.0,
        "type_b_rsi_high": 70.0,
        "type_b_momentum_bars": 3,
        "type_c_confidence": 72.0,
        "type_c_rsi_overbought": 75.0,
        "type_d_confidence": 80.0,
        "type_d_price_proximity_pct": 1.0,
        "type_d_rsi_low": 20.0,
        "type_d_rsi_high": 40.0,
        "type_e_confidence": 70.0,
        "type_e_ibs_threshold": 0.10,
        "type_e_rvol_threshold": 1.0,
        "type_f_confidence": 68.0,
        "type_f_obv_rsi_threshold": 30.0,
        "type_f_rvol_threshold": 0.7,
    }
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
            et = cfg.get("entry_types", {})
            for key in defaults:
                if key in et:
                    defaults[key] = float(et[key])
            log.info("Entry type config loaded from %s", cfg_path)
    except Exception as e:
        log.warning("Failed to load entry type config: %s (using defaults)", e)
    return defaults


ENTRY_TYPE_CONFIG = _load_entry_type_config()


# ---------------------------------------------------------------------------
# Cost model (loaded from config.toml [costs])
# ---------------------------------------------------------------------------
# Per-exchange round-trip costs (spread + commission + clearing)
COSTS_PER_EXCHANGE: Dict[str, float] = {
    "LSE": 0.0035, "US": 0.0015, "TSE": 0.0025,
    "HKEX": 0.0030, "XETRA": 0.0020, "Euronext": 0.0020, "SGX": 0.0025,
}
FX_CONVERSION_COST = 0.002  # 0.2% for cross-currency (non-GBP on LSE)

# FX rates to GBP (approximate, acceptable for backtest)
FX_TO_GBP: Dict[str, float] = {
    "GBP": 1.0, "USD": 0.79, "EUR": 0.85, "JPY": 0.0042,
    "HKD": 0.10, "SGD": 0.59, "AUD": 0.52, "KRW": 0.00058,
}

# Currency map: ticker -> trading currency (loaded from contracts.toml)
_CURRENCY_MAP: Optional[Dict[str, str]] = None


def _load_currency_map() -> Dict[str, str]:
    """Load ticker->currency mapping from contracts.toml."""
    global _CURRENCY_MAP
    if _CURRENCY_MAP is not None:
        return _CURRENCY_MAP
    _CURRENCY_MAP = {}
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        cfg_path = Path(os.environ.get("AEGIS_CONFIG_DIR", "/app/config")) / "contracts.toml"
        if not cfg_path.exists():
            cfg_path = _PROJECT_ROOT / "config" / "contracts.toml"
        if cfg_path.exists():
            with open(cfg_path, "rb") as f:
                contracts = tomllib.load(f)
            for region_data in contracts.values():
                if not isinstance(region_data, dict):
                    continue
                tickers = region_data.get("tickers", [])
                if isinstance(tickers, list):
                    for entry in tickers:
                        if isinstance(entry, dict):
                            sym = entry.get("yf_symbol") or entry.get("symbol", "")
                            ccy = entry.get("currency", "USD")
                            if sym:
                                _CURRENCY_MAP[sym] = ccy
    except Exception as e:
        log.warning("Failed to load currency map from contracts.toml: %s", e)
    return _CURRENCY_MAP


def _load_costs_from_config() -> None:
    """Override per-exchange costs from config.toml [costs.per_exchange] if available."""
    global COSTS_PER_EXCHANGE, FX_CONVERSION_COST
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
            costs = cfg.get("costs", {})
            per_ex = costs.get("per_exchange", {})
            for ex, cost_val in per_ex.items():
                # Config stores as fraction (0.0035 = 0.35%), use directly
                COSTS_PER_EXCHANGE[ex] = float(cost_val)
            fx_val = costs.get("fx_conversion_pct")
            if fx_val is not None:
                # Config stores as fraction (0.002 = 0.2%), use directly
                FX_CONVERSION_COST = float(fx_val)
            log.info("Costs loaded: %s, FX=%.4f", {k: f"{v*100:.2f}%" for k, v in COSTS_PER_EXCHANGE.items()}, FX_CONVERSION_COST)
    except Exception:
        pass


_load_costs_from_config()


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
    entry_type: str  # TypeA/B/C/D/E/F
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
    confidence: float = 0.0    # Base confidence from entry type config
    cost_pct: float = 0.0      # Round-trip cost percentage applied
    net_pnl: float = 0.0       # PnL after costs (per share)
    net_pnl_pct: float = 0.0   # Net PnL percentage after costs
    currency: str = "USD"      # Trading currency of the instrument
    gbp_pnl: float = 0.0       # PnL normalized to GBP


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


def _compute_obv(closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    """On-Balance Volume. Returns array same length as prices."""
    n = len(closes)
    obv = np.zeros(n, dtype=np.float64)
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            obv[i] = obv[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            obv[i] = obv[i - 1] - volumes[i]
        else:
            obv[i] = obv[i - 1]
    return obv


# ---------------------------------------------------------------------------
# Entry signal classification — NO COOLDOWN, NO DAILY CAP
# ---------------------------------------------------------------------------
def classify_entries(
    closes: np.ndarray,
    volumes: np.ndarray,
    rsi: np.ndarray,
    rvol_arr: np.ndarray,
    regime: str,
    highs: Optional[np.ndarray] = None,
    lows: Optional[np.ndarray] = None,
    atr: Optional[np.ndarray] = None,
    dates: Optional[List[Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> List[Tuple[int, str]]:
    """Identify entry signals and classify as Type A/B/C/D/E/F.

    CORRECTED to match Rust entry_engine.rs logic exactly:
      Type A (DipRecovery): RSI < oversold + RVOL > vol_ma20 * spike_mult + drop >= atr_mult * ATR
      Type B (EarlyRunner): RVOL rising for N consecutive bars + RSI in [low, high]
      Type C (OverboughtFade): RSI > overbought + price up + volume < vol_ma20 (divergence)
      Type D (SupportBounce): Price within proximity_pct of daily low + RSI in [low, high]
      Type E (IBSMeanReversion): IBS < threshold + RVOL > threshold
      Type F (OBVDivergence): OBV-RSI(5) < threshold + RVOL > threshold

    Each type is evaluated independently per bar (a bar can produce multiple signals).
    No cooldown, no daily cap — captures ALL possible signals for Ouroboros learning.

    Returns list of (bar_index, entry_type).
    """
    if cfg is None:
        cfg = ENTRY_TYPE_CONFIG

    entries: List[Tuple[int, str]] = []
    n = len(closes)
    if n < 25:
        return entries

    # Config thresholds
    a_rsi_oversold = cfg.get("type_a_rsi_oversold", 40.0)
    a_vol_spike_mult = cfg.get("type_a_volume_spike_mult", 1.8)
    a_drop_atr_mult = cfg.get("type_a_drop_atr_mult", 2.0)
    b_rsi_low = cfg.get("type_b_rsi_low", 30.0)
    b_rsi_high = cfg.get("type_b_rsi_high", 70.0)
    b_momentum_bars = int(cfg.get("type_b_momentum_bars", 3))
    c_rsi_overbought = cfg.get("type_c_rsi_overbought", 75.0)
    d_proximity_pct = cfg.get("type_d_price_proximity_pct", 1.0)
    d_rsi_low = cfg.get("type_d_rsi_low", 20.0)
    d_rsi_high = cfg.get("type_d_rsi_high", 40.0)
    e_ibs_threshold = cfg.get("type_e_ibs_threshold", 0.10)
    e_rvol_threshold = cfg.get("type_e_rvol_threshold", 1.0)
    f_obv_rsi_threshold = cfg.get("type_f_obv_rsi_threshold", 30.0)
    f_rvol_threshold = cfg.get("type_f_rvol_threshold", 0.7)

    # --- Precompute indicators for corrected entry logic ---

    # 20-bar volume moving average (for Type A spike detection and Type C divergence)
    vol_ma20 = np.full(n, np.nan)
    for i in range(20, n):
        vol_ma20[i] = np.mean(volumes[i - 20:i])

    # 20-bar rolling max of highs (for Type A price drop calculation)
    recent_high_20 = np.full(n, np.nan)
    if highs is not None:
        for i in range(20, n):
            recent_high_20[i] = np.max(highs[i - 20:i])

    # Precompute OBV and OBV-RSI(5) for TypeF detection
    obv = _compute_obv(closes, volumes)
    obv_rsi5 = compute_rsi(obv, period=5)

    # Daily low tracking for Type D (cumulative min of lows within each calendar day)
    daily_low = np.full(n, np.nan)
    if lows is not None and dates is not None:
        current_day = None
        current_day_low = float('inf')
        for i in range(n):
            try:
                day = str(dates[i])[:10]
            except Exception:
                day = str(i)
            if day != current_day:
                current_day = day
                current_day_low = lows[i]
            else:
                current_day_low = min(current_day_low, lows[i])
            daily_low[i] = current_day_low

    # --- Scan for entries ---
    # Skip first 25 bars for indicator warmup; leave 5 bars for exit simulation
    start_bar = max(25, b_momentum_bars + 1)
    for i in range(start_bar, n - 5):
        if np.isnan(rsi[i]) or np.isnan(rvol_arr[i]):
            continue

        # Type A (DipRecovery): RSI < oversold + raw_volume > vol_ma20 * spike_mult + price drop >= atr_mult * ATR
        # Matches Rust entry_engine.rs detect_dip_recovery()
        # Note: Rust `rvol` param is raw volume, not relative. vol_ma20 is 20-bar volume MA.
        if (atr is not None and highs is not None
                and not np.isnan(atr[i]) and atr[i] > 0
                and not np.isnan(recent_high_20[i]) and not np.isnan(vol_ma20[i])):
            if (rsi[i] < a_rsi_oversold
                    and vol_ma20[i] > 0
                    and volumes[i] > vol_ma20[i] * a_vol_spike_mult
                    and (recent_high_20[i] - closes[i]) / atr[i] >= a_drop_atr_mult):
                entries.append((i, "TypeA"))

        # Type B (EarlyRunner): RVOL rising for N consecutive bars + RSI in range
        # Matches Rust entry_engine.rs detect_early_runner()
        if i >= b_momentum_bars and rsi[i] >= b_rsi_low and rsi[i] <= b_rsi_high:
            rvol_rising = True
            for j in range(1, b_momentum_bars):
                if (np.isnan(rvol_arr[i - j]) or np.isnan(rvol_arr[i - j + 1])
                        or rvol_arr[i - j] >= rvol_arr[i - j + 1]):
                    # Check: rvol[i-2] < rvol[i-1] < rvol[i] for momentum_bars=3
                    # Window is [i-2, i-1, i], check pairs: (i-2,i-1) and (i-1,i)
                    rvol_rising = False
                    break
            # Also check the last pair: rvol[i-1] < rvol[i]
            if rvol_rising and not np.isnan(rvol_arr[i - 1]) and rvol_arr[i - 1] < rvol_arr[i]:
                entries.append((i, "TypeB"))

        # Type C (OverboughtFade): DISABLED — 39.04% WR, 0.805x PF (negative edge in backtest)
        # Short-side fades conflict with ISA long-only structure; overbought RSI in trending markets
        # if (i > 0 and not np.isnan(vol_ma20[i])
        #         and rsi[i] > c_rsi_overbought
        #         and closes[i] > closes[i - 1]
        #         and volumes[i] < vol_ma20[i]):
        #     entries.append((i, "TypeC"))

        # Type D (SupportBounce): Price within proximity_pct of daily low + RSI in range
        # Matches Rust entry_engine.rs detect_support_bounce()
        if (not np.isnan(daily_low[i]) and daily_low[i] > 0
                and rsi[i] >= d_rsi_low and rsi[i] <= d_rsi_high):
            pct_above_low = ((closes[i] - daily_low[i]) / daily_low[i]) * 100.0
            if pct_above_low <= d_proximity_pct:
                entries.append((i, "TypeD"))

        # Type E (IBSMeanReversion): IBS < threshold + RVOL > threshold
        # Matches Rust entry_engine.rs detect_ibs_mean_reversion()
        # Note: Rust does NOT check regime internally
        if highs is not None and lows is not None:
            bar_range = highs[i] - lows[i]
            if bar_range > 1e-9 and rvol_arr[i] > e_rvol_threshold:
                ibs_val = (closes[i] - lows[i]) / bar_range
                if ibs_val < e_ibs_threshold:
                    entries.append((i, "TypeE"))

        # Type F (OBVDivergence): OBV-RSI(5) < threshold + RVOL > threshold
        if not np.isnan(obv_rsi5[i]) and obv_rsi5[i] < f_obv_rsi_threshold and rvol_arr[i] > f_rvol_threshold:
            entries.append((i, "TypeF"))

        # ── S1: Microstructure Momentum ── DISABLED — 40.05% WR, 0.532x PF (negative edge)
        # Bar-based tick proxy is too noisy; needs real tick data for meaningful signal
        # if i >= 20:
        #     up_bars = sum(1 for j in range(i-20, i) if closes[j] > closes[j-1])
        #     tick_ratio = up_bars / 20.0
        #     sma20_val = np.mean(closes[i-20:i])
        #     std20_val = np.std(closes[i-20:i])
        #     if std20_val > 1e-9:
        #         z = (closes[i] - sma20_val) / std20_val
        #         if tick_ratio > 0.58 and rvol_arr[i] > 1.0 and closes[i] > sma20_val and abs(z) < 2.5:
        #             if not np.isnan(atr[i]) if atr is not None else True:
        #                 entries.append((i, "S1_Microstructure"))

        # ── S2: Statistical Reversion (BB z-score + RSI oversold) ──
        if i >= 20 and regime != "trending":
            sma20_s2 = np.mean(closes[i-20:i])
            std20_s2 = np.std(closes[i-20:i])
            if std20_s2 > 1e-9:
                z_s2 = (closes[i] - sma20_s2) / std20_s2
                rsi2 = compute_rsi(closes[max(0,i-10):i+1], period=2)
                rsi2_val = rsi2[-1] if len(rsi2) > 0 and not np.isnan(rsi2[-1]) else 50.0
                if z_s2 < -1.5 and rsi2_val < 20.0:
                    entries.append((i, "S2_Reversion"))

        # ── S3: Macro Trend (SMA crossover + momentum) ──
        if i >= 20 and regime != "mean_reverting":
            sma5 = np.mean(closes[i-5:i])
            sma20_s3 = np.mean(closes[i-20:i])
            if sma5 > sma20_s3 and closes[i] > sma5:
                # 12-bar momentum
                if i >= 12 and closes[i-12] > 0:
                    mom12 = (closes[i] - closes[i-12]) / closes[i-12]
                    if mom12 > 0.005:
                        entries.append((i, "S3_MacroTrend"))

        # ── S6: Catalyst (gap continuation) ── DISABLED — 12.95% WR, 0.007x PF (catastrophic)
        # Gap continuation is mean-reverting in liquid markets; needs fundamental redesign
        # if i > 0 and closes[i-1] > 0:
        #     gap = (closes[i] - closes[i-1]) / closes[i-1] * 100.0
        #     if gap > 1.5 and rvol_arr[i] > 2.0:
        #         entries.append((i, "S6_Catalyst"))

    # ── VolCompression (Book 22): Keltner squeeze breakout — per-bar scan ──
    # Live system scans for squeeze on every bar. We replicate by running
    # detect_squeeze on a sliding 120-bar window.
    if _HAS_VOL_COMPRESSION and highs is not None and lows is not None and n >= 130:
        vc_window = 120
        for vc_i in range(vc_window + 5, n - 5, 5):  # Step by 5 bars for performance
            try:
                vc_start = max(0, vc_i - vc_window)
                sig = detect_squeeze(
                    closes[vc_start:vc_i + 1],
                    highs[vc_start:vc_i + 1],
                    lows[vc_start:vc_i + 1],
                    volumes[vc_start:vc_i + 1],
                )
                if (sig is not None
                        and sig.squeeze_score >= 0.7
                        and sig.breakout_direction == "up"):
                    entries.append((vc_i, "VolCompression"))
            except Exception:
                pass

    # ── S5: Overnight Carry / IBS_OvernightGap (Book 40) ──
    # Detect overnight gaps on leveraged ETPs and ISA-eligible instruments.
    # If a 3x+ ETP gaps down >1% at open, buy the mean-reversion bounce.
    if highs is not None and lows is not None and dates is not None:
        prev_day = None
        for oc_i in range(1, n - 5):
            try:
                day = str(dates[oc_i])[:10]
            except Exception:
                continue
            try:
                prev_day_str = str(dates[oc_i - 1])[:10]
            except Exception:
                continue
            if day != prev_day_str and prev_day_str != prev_day:
                # New day boundary detected — check gap
                prev_day = prev_day_str
                if closes[oc_i - 1] > 0:
                    gap_pct = (closes[oc_i] - closes[oc_i - 1]) / closes[oc_i - 1]
                    # Gap down >1% + IBS < 0.2 = overnight carry bounce signal
                    bar_range = highs[oc_i] - lows[oc_i]
                    if bar_range > 1e-9:
                        ibs = (closes[oc_i] - lows[oc_i]) / bar_range
                        if gap_pct < -0.01 and ibs < 0.20:
                            entries.append((oc_i, "S5_OvernightCarry"))

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

    # Classify entry signals — corrected to match Rust entry_engine.rs
    entries = classify_entries(
        closes, volumes, rsi, rvol_arr, regime,
        highs=highs, lows=lows, atr=atr, dates=dates,
    )

    # Cost model: determine per-exchange cost and currency
    cost_pct = COSTS_PER_EXCHANGE.get(exchange, 0.003)
    currency_map = _load_currency_map()
    currency = currency_map.get(ticker, "USD")
    # Add FX conversion cost for non-GBP instruments on LSE
    fx_cost = FX_CONVERSION_COST if (exchange == "LSE" and currency != "GBP") else 0.0
    total_cost_pct = cost_pct + fx_cost
    fx_rate = FX_TO_GBP.get(currency, FX_TO_GBP.get("USD", 0.79))

    # Confidence per entry type (baseline before calendar adjustment)
    confidence_map = {
        "TypeA": ENTRY_TYPE_CONFIG.get("type_a_confidence", 65.0),
        "TypeB": ENTRY_TYPE_CONFIG.get("type_b_confidence", 82.0),
        "TypeC": ENTRY_TYPE_CONFIG.get("type_c_confidence", 72.0),
        "TypeD": ENTRY_TYPE_CONFIG.get("type_d_confidence", 80.0),
        "TypeE": ENTRY_TYPE_CONFIG.get("type_e_confidence", 70.0),
        "TypeF": ENTRY_TYPE_CONFIG.get("type_f_confidence", 68.0),
        "S2_Reversion": 62.0,
        "S3_MacroTrend": 60.0,
        "VolCompression": 74.0,  # Book 22: squeeze breakouts are high-confidence
        "RebalancingFlow": 65.0,
        "NAVArbitrage": 62.0,   # Book 132: ETP discount/premium
        "S5_OvernightCarry": 64.0,  # Book 40: overnight gap carry
        "CalendarAnomaly": 63.0,
        "FOmcDrift": 66.0,     # Book 24: post-FOMC drift
    }

    # Pre-compute FOMC dates in the data range (third Wednesday of Jan/Mar/May/Jul/Sep/Nov)
    # These are approximate — real FOMC dates come from event_calendar.json in live mode.
    # In backtest we use the rule: 3rd Wednesday of FOMC months.
    _fomc_months = {1, 3, 5, 7, 9, 11}

    def _is_fomc_day(date_str: str) -> bool:
        """Approximate FOMC day detection: 3rd Wednesday of FOMC months."""
        try:
            from datetime import datetime as _dt
            d = _dt.strptime(date_str[:10], "%Y-%m-%d")
            if d.month not in _fomc_months:
                return False
            # 3rd Wednesday: weekday()==2 and 15 <= day <= 21
            return d.weekday() == 2 and 15 <= d.day <= 21
        except Exception:
            return False

    # ── FOMC Drift entries (Book 24): buy on FOMC day + next day ──
    # In live mode, the exact time window is 15-45 min post-announcement.
    # In backtest with 60m bars, we enter on the FOMC bar closest to 19:00 UTC
    # (2pm ET announcement) and the bar after.
    if has_datetime and _HAS_FOMC_DRIFT:
        for fi in range(25, n - 5):
            try:
                date_str = dates[fi]
                if _is_fomc_day(date_str):
                    ts = index_list[fi]
                    if hasattr(ts, 'hour') and 18 <= ts.hour <= 20:
                        entries.append((fi, "FOmcDrift"))
            except Exception:
                pass

    # ── NAV Arbitrage entries (Book 132): ETP discount/premium for LSE ETPs ──
    if _HAS_NAV_ARBITRAGE and ticker.endswith(".L") and n >= 30:
        # For ETP tickers, compute rolling premium/discount vs SMA proxy
        # In live mode, NAVTracker uses real underlying returns.
        # In backtest, we use price-to-SMA20 deviation as a NAV proxy.
        sma20_nav = np.convolve(closes, np.ones(20) / 20, mode='valid')
        for ni in range(len(sma20_nav)):
            bar_idx = ni + 19  # offset from convolution
            if bar_idx < 25 or bar_idx >= n - 5:
                continue
            if sma20_nav[ni] > 0:
                discount_pct = (closes[bar_idx] - sma20_nav[ni]) / sma20_nav[ni] * 100
                # ETP trading at >2% discount to SMA20 = potential NAV arb
                if discount_pct < -2.0 and rvol_arr[bar_idx] > 0.5:
                    entries.append((bar_idx, "NAVArbitrage"))

    for entry_bar, entry_type in entries:
        entry_price = closes[entry_bar]
        if entry_price <= 0 or not np.isfinite(entry_price):
            continue

        exit_bar, exit_price, rung = simulate_chandelier_exit(
            closes, highs, lows, atr, entry_bar, entry_price,
        )

        if not np.isfinite(exit_price):
            continue

        pnl = exit_price - entry_price
        pnl_pct = pnl / entry_price * 100.0

        # Apply cost model
        net_pnl = pnl - (entry_price * total_cost_pct)
        net_pnl_pct = pnl_pct - (total_cost_pct * 100.0)
        gbp_pnl = net_pnl * fx_rate

        # Guard against NaN/Inf propagation from extreme prices
        if not (np.isfinite(net_pnl) and np.isfinite(net_pnl_pct) and np.isfinite(gbp_pnl)):
            continue

        # Extract hour and weekday from index if available
        entry_hour = -1
        entry_weekday = -1
        entry_date_str = dates[entry_bar] if entry_bar < len(dates) else "unknown"
        if has_datetime and entry_bar < len(index_list):
            ts = index_list[entry_bar]
            try:
                entry_hour = ts.hour
                entry_weekday = ts.weekday()
            except AttributeError:
                pass

        # Calendar anomaly confidence adjustment (Book 171)
        base_confidence = confidence_map.get(entry_type, 70.0)
        cal_confidence = base_confidence
        if _HAS_CALENDAR_ANOMALIES and entry_weekday >= 0:
            try:
                from datetime import datetime as _dt2
                _d = _dt2.strptime(entry_date_str[:10], "%Y-%m-%d")
                cal_adj = get_calendar_adjustment(
                    year=_d.year, month=_d.month, day=_d.day,
                    weekday=entry_weekday, hour=max(entry_hour, 0),
                )
                cal_confidence = base_confidence + cal_adj.confidence_delta
            except Exception:
                cal_confidence = base_confidence

        # FOMC day: suppress signals on FOMC announcement days
        # (live engine blocks entries; backtest marks them for transparency)
        is_fomc = _is_fomc_day(entry_date_str)
        if is_fomc:
            # Reduce confidence on FOMC day (market regime uncertain)
            cal_confidence = max(cal_confidence - 10, 40.0)

        # Rebalancing flow window (Book 36): 19:00-20:00 UTC is ETP rebalancing
        # Boost confidence for entries in this window — institutional flow is predictable
        if entry_hour == 19:
            cal_confidence = min(cal_confidence + 5, 100.0)
            if entry_type not in confidence_map:
                entry_type = "RebalancingFlow"  # Re-label only if unclassified

        trades.append(SimTrade(
            ticker=ticker,
            date=entry_date_str,
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
            confidence=round(cal_confidence, 1),
            cost_pct=total_cost_pct * 100.0,
            net_pnl=net_pnl,
            net_pnl_pct=net_pnl_pct,
            currency=currency,
            gbp_pnl=gbp_pnl,
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
    for etype in ["TypeA", "TypeB", "TypeC", "TypeD", "TypeE", "TypeF",
                   "S2_Reversion", "S3_MacroTrend", "S5_OvernightCarry",
                   "VolCompression", "NAVArbitrage", "FOmcDrift", "RebalancingFlow"]:
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
        for entry_type in ["TypeA", "TypeB", "TypeC", "TypeD", "TypeE", "TypeF"]:
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
