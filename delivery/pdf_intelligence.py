"""
NZT-48 Trading System -- Daily PDF Intelligence Report Generator
================================================================
Generates two institutional-grade PDF intelligence briefs daily:

  1. Pre-LSE PDF  (06:00 UK)   -- 2 hours before London Stock Exchange opens
  2. Pre-NYSE PDF (12:30 UK / 07:30 ET) -- 2 hours before NYSE opens

Each PDF contains seven sections:
  S1: Market Overview      -- live regime, VIX, futures, DXY, 10Y, macro events
  S2: Top Plays (Long)     -- live technicals, entry zones, ATR stops, targets, ISA mapping
  S3: Top Plays (Short)    -- inverse ETPs for ISA, short setups
  S4: ISA Funds Dashboard  -- all 12+ leveraged ETPs with live prices
  S5: Learning Insights    -- regime matrix, ticker leaderboard, patterns
  S6: Risk Dashboard       -- equity, drawdown, Go/No-Go, Kelly sizing
  S7: Full Ticker Heatmap  -- all 18 tickers with technicals snapshot

Uses fpdf2 for PDF generation. Delivers via Telegram using TelegramDelivery.
All indicator calculations use live yfinance data via pandas/numpy.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import yfinance as yf
from fpdf import FPDF

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import config as cfg
from delivery.database import get_connection
from delivery.pdf_shared import (
    Lane, assign_lane, LANE_GATES,
    RunManifest, render_manifest_strip,
    render_sector_inflow_alerts, render_near_miss_table,
    render_sector_rotation_table, render_lane_grouped_table,
    render_lane_header,
    next_schedule_line,
)
from uk_isa.isa_universe import (
    EXTENDED_UNIVERSE as ISA_PRIMARY_TICKERS,
    SECTOR_RADAR_UNIVERSE,
    TICKER_NAMES,
    LEVERAGE_MAP,
    ISA_FACTOR_GROUPS,
    get_factor_group,
    INTEL_UNIVERSE,
)

logger = logging.getLogger("nzt48.pdf_intelligence")

# ---------------------------------------------------------------------------
# Brand colours
# ---------------------------------------------------------------------------
_DARK_BG       = (18, 22, 30)       # Near-black header bar
_BRAND_GREEN   = (0, 200, 120)      # NZT-48 accent
_BRAND_RED     = (220, 50, 50)      # Short / loss
_BRAND_AMBER   = (255, 180, 40)     # Warning
_TEXT_WHITE     = (255, 255, 255)
_TEXT_DARK      = (30, 30, 36)
_TEXT_MUTED     = (120, 125, 140)
_ROW_LIGHT     = (240, 242, 248)
_ROW_WHITE      = (255, 255, 255)
_BORDER_GREY    = (200, 204, 215)
_HEADER_BG      = (34, 40, 54)
_TABLE_HDR_BG   = (44, 50, 66)
_GREEN_FAINT    = (230, 250, 240)
_RED_FAINT      = (255, 235, 235)
_SECTION_LINE   = (0, 200, 120)
_BLUE_FAINT     = (230, 240, 255)

# Strategy label mapping
STRATEGY_NAMES = {
    "S1": "Regime Trend",       "S2": "Momentum Breakout",
    "S3": "Mean Reversion",     "S4": "Catalyst/Narrative",
    "S5": "PEAD Earnings",      "S6": "Macro Regime",
    "S7": "Sector Rotation",    "S8": "Vol Crush",
    "S9": "Pairs Trade",        "S10": "AI Thematic",
    "S11": "Hot Scanner",       "S12": "Rebalance Flow",
    "S13": "Trend Compound",    "S14": "Gamma Squeeze",
    "S15": "2% Daily Target",
}

# US tickers kept as reference only (NOT primary)
US_REFERENCE_TICKERS = [
    "NVDA", "TSLA", "MU", "AMD", "AVGO", "ARM",
    "TSM", "ASML", "QCOM",
]

# ISA ETP tickers is now the primary universe
ISA_ETP_TICKERS = ISA_PRIMARY_TICKERS


def _safe(text: str) -> str:
    """Ensure text is latin-1 safe for fpdf2 built-in fonts."""
    if not isinstance(text, str):
        text = str(text)
    text = text.replace("\u2014", "--")
    text = text.replace("\u2013", "-")
    text = text.replace("\u2018", "'")
    text = text.replace("\u2019", "'")
    text = text.replace("\u201c", '"')
    text = text.replace("\u201d", '"')
    text = text.replace("\u2022", "-")
    text = text.replace("\u2192", "->")
    text = text.replace("\u2264", "<=")
    text = text.replace("\u2265", ">=")
    text = text.replace("\u221e", "inf")
    return text.encode("latin-1", errors="replace").decode("latin-1")


# ---------------------------------------------------------------------------
# Technical indicator calculations (pure numpy/pandas, no TA-lib needed)
# ---------------------------------------------------------------------------

def _ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=period, adjust=False).mean()


def _sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=period, min_periods=1).mean()


def _rsi(series: pd.Series, period: int = 14) -> float:
    """Compute RSI for the latest bar."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_val = 100 - (100 / (1 + rs))
    latest = rsi_val.iloc[-1] if len(rsi_val) > 0 else 50.0
    return round(float(latest) if not np.isnan(latest) else 50.0, 2)


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """Compute MACD line, signal line, and histogram."""
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return {
        "macd": round(float(macd_line.iloc[-1]), 4) if len(macd_line) > 0 else 0,
        "signal": round(float(signal_line.iloc[-1]), 4) if len(signal_line) > 0 else 0,
        "histogram": round(float(histogram.iloc[-1]), 4) if len(histogram) > 0 else 0,
    }


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """Compute Average True Range."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_series = tr.rolling(window=period, min_periods=1).mean()
    return round(float(atr_series.iloc[-1]), 4) if len(atr_series) > 0 else 0.0


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """Compute Average Directional Index."""
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr_s = tr.ewm(alpha=1 / period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr_s.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr_s.replace(0, np.nan))

    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    adx_val = dx.ewm(alpha=1 / period, min_periods=period).mean()
    latest = adx_val.iloc[-1] if len(adx_val) > 0 else 0.0
    return round(float(latest) if not np.isnan(latest) else 0.0, 2)


def _bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> dict:
    """Compute Bollinger Bands."""
    mid = _sma(series, period)
    std = series.rolling(window=period, min_periods=1).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    latest_price = float(series.iloc[-1]) if len(series) > 0 else 0
    latest_upper = float(upper.iloc[-1]) if len(upper) > 0 else 0
    latest_lower = float(lower.iloc[-1]) if len(lower) > 0 else 0
    latest_mid = float(mid.iloc[-1]) if len(mid) > 0 else 0
    bb_width = (latest_upper - latest_lower) / latest_mid * 100 if latest_mid > 0 else 0
    bb_pct = (latest_price - latest_lower) / (latest_upper - latest_lower) if (latest_upper - latest_lower) > 0 else 0.5
    return {
        "upper": round(latest_upper, 2),
        "mid": round(latest_mid, 2),
        "lower": round(latest_lower, 2),
        "width_pct": round(bb_width, 2),
        "pct_b": round(bb_pct, 4),
    }


def _stoch_rsi(series: pd.Series, rsi_period: int = 14, stoch_period: int = 14,
               k_period: int = 3, d_period: int = 3) -> dict:
    """Compute Stochastic RSI."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / rsi_period, min_periods=rsi_period).mean()
    avg_loss = loss.ewm(alpha=1 / rsi_period, min_periods=rsi_period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))

    rsi_min = rsi_series.rolling(window=stoch_period, min_periods=1).min()
    rsi_max = rsi_series.rolling(window=stoch_period, min_periods=1).max()
    stoch_rsi = (rsi_series - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)

    k = stoch_rsi.rolling(window=k_period, min_periods=1).mean() * 100
    d = k.rolling(window=d_period, min_periods=1).mean()

    k_val = float(k.iloc[-1]) if len(k) > 0 and not np.isnan(k.iloc[-1]) else 50.0
    d_val = float(d.iloc[-1]) if len(d) > 0 and not np.isnan(d.iloc[-1]) else 50.0
    return {"k": round(k_val, 2), "d": round(d_val, 2)}


def _vwap_estimate(high: pd.Series, low: pd.Series, close: pd.Series,
                   volume: pd.Series) -> float:
    """Estimate session VWAP (typical price * volume / cumulative volume)."""
    typical = (high + low + close) / 3
    cum_vol = volume.cumsum()
    cum_tp_vol = (typical * volume).cumsum()
    vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
    latest = vwap.iloc[-1] if len(vwap) > 0 else 0.0
    return round(float(latest) if not np.isnan(latest) else 0.0, 2)


# ---------------------------------------------------------------------------
# Live data fetcher
# ---------------------------------------------------------------------------

def _fetch_live_technicals(ticker: str, period: str = "6mo", interval: str = "1d") -> Optional[dict]:
    """Fetch live data from yfinance and compute all technicals for a ticker.

    Returns a dict with price data + all computed indicators, or None on failure.
    """
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period=period, interval=interval)
        if hist is None or hist.empty or len(hist) < 20:
            logger.warning("Insufficient data for %s (%d bars)", ticker, len(hist) if hist is not None else 0)
            return None

        close = hist["Close"]
        high = hist["High"]
        low = hist["Low"]
        volume = hist["Volume"]
        opn = hist["Open"]

        current_price = float(close.iloc[-1])
        prev_close = float(close.iloc[-2]) if len(close) >= 2 else current_price
        change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close > 0 else 0

        # EMAs
        ema9 = float(_ema(close, 9).iloc[-1])
        ema20 = float(_ema(close, 20).iloc[-1])
        ema50 = float(_ema(close, 50).iloc[-1])
        ema200_val = float(_ema(close, 200).iloc[-1]) if len(close) >= 200 else float(_ema(close, min(len(close), 200)).iloc[-1])

        # RSI
        rsi_val = _rsi(close, 14)

        # MACD
        macd_data = _macd(close)

        # ATR
        atr_val = _atr(high, low, close, 14)

        # ADX
        adx_val = _adx(high, low, close, 14)

        # Bollinger Bands
        bb_data = _bollinger_bands(close, 20, 2.0)

        # Stochastic RSI
        stoch_data = _stoch_rsi(close)

        # VWAP estimate (last 20 bars for daily)
        vwap_val = _vwap_estimate(
            high.tail(20), low.tail(20), close.tail(20), volume.tail(20)
        )

        # Volume vs 20d average (RVOL)
        avg_vol_20 = float(volume.tail(20).mean()) if len(volume) >= 20 else float(volume.mean())
        current_vol = float(volume.iloc[-1])
        rvol = round(current_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0

        # 52-week high/low
        hist_52w = close.tail(252) if len(close) >= 252 else close
        high_52w = float(hist_52w.max())
        low_52w = float(hist_52w.min())
        pct_from_52w_high = round((current_price - high_52w) / high_52w * 100, 2) if high_52w > 0 else 0

        # Market cap (from fast_info)
        try:
            fi = tk.fast_info
            market_cap = getattr(fi, "market_cap", 0) or 0
        except Exception:
            market_cap = 0

        return {
            "ticker": ticker,
            "price": round(current_price, 2),
            "prev_close": round(prev_close, 2),
            "change_pct": round(change_pct, 2),
            "open": round(float(opn.iloc[-1]), 2),
            "high_today": round(float(high.iloc[-1]), 2),
            "low_today": round(float(low.iloc[-1]), 2),
            "volume": int(current_vol),
            "avg_vol_20d": int(avg_vol_20),
            "rvol": rvol,
            "vwap": vwap_val,
            "ema9": round(ema9, 2),
            "ema20": round(ema20, 2),
            "ema50": round(ema50, 2),
            "ema200": round(ema200_val, 2),
            "rsi": rsi_val,
            "macd": macd_data["macd"],
            "macd_signal": macd_data["signal"],
            "macd_histogram": macd_data["histogram"],
            "atr": atr_val,
            "atr_pct": round(atr_val / current_price * 100, 2) if current_price > 0 else 0,
            "adx": adx_val,
            "bb_upper": bb_data["upper"],
            "bb_mid": bb_data["mid"],
            "bb_lower": bb_data["lower"],
            "bb_width_pct": bb_data["width_pct"],
            "bb_pct_b": bb_data["pct_b"],
            "stoch_rsi_k": stoch_data["k"],
            "stoch_rsi_d": stoch_data["d"],
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            "pct_from_52w_high": pct_from_52w_high,
            "market_cap": market_cap,
        }

    except Exception as e:
        logger.error("Failed to fetch technicals for %s: %s", ticker, e)
        return None


def _classify_signal(tech: dict, regime_confidence: float = 100.0) -> dict:
    """Classify a ticker as STRONG LONG / LONG / NEUTRAL / SHORT / STRONG SHORT
    based on indicator alignment. Returns classification + confidence score.

    Scoring system (max 100):
      - EMA stack alignment:       up to 20 pts
      - RSI zone:                  up to 15 pts
      - MACD alignment:            up to 15 pts
      - Bollinger Band position:   up to 10 pts
      - Volume (RVOL):             up to 10 pts
      - ADX (trend strength):      up to 10 pts
      - Stoch RSI:                 up to 10 pts
      - 52W position:              up to 10 pts
    """
    long_score = 0
    short_score = 0
    signals_detail = []

    price = tech["price"]
    ema9 = tech["ema9"]
    ema20 = tech["ema20"]
    ema50 = tech["ema50"]
    ema200 = tech["ema200"]

    # --- EMA Stack (20 pts) ---
    # Perfect bull stack: price > ema9 > ema20 > ema50 > ema200
    bull_stack = int(price > ema9) + int(ema9 > ema20) + int(ema20 > ema50) + int(ema50 > ema200)
    bear_stack = int(price < ema9) + int(ema9 < ema20) + int(ema20 < ema50) + int(ema50 < ema200)

    if bull_stack == 4:
        long_score += 20
        signals_detail.append("EMA stack bullish (4/4)")
    elif bull_stack == 3:
        long_score += 14
        signals_detail.append(f"EMA partial bull ({bull_stack}/4)")
    elif bull_stack == 2:
        long_score += 8

    if bear_stack == 4:
        short_score += 20
        signals_detail.append("EMA stack bearish (4/4)")
    elif bear_stack == 3:
        short_score += 14
        signals_detail.append(f"EMA partial bear ({bear_stack}/4)")
    elif bear_stack == 2:
        short_score += 8

    # --- RSI (15 pts) ---
    rsi = tech["rsi"]
    if rsi > 60 and rsi < 80:
        long_score += 15
        signals_detail.append(f"RSI bullish ({rsi})")
    elif rsi >= 50 and rsi <= 60:
        long_score += 8
    elif rsi >= 80:
        short_score += 10  # Overbought, potential short
        signals_detail.append(f"RSI overbought ({rsi})")
    elif rsi < 40 and rsi > 20:
        short_score += 15
        signals_detail.append(f"RSI bearish ({rsi})")
    elif rsi >= 40 and rsi < 50:
        short_score += 8
    elif rsi <= 20:
        long_score += 10  # Oversold, potential long bounce
        signals_detail.append(f"RSI oversold ({rsi})")

    # --- MACD (15 pts) ---
    macd_hist = tech["macd_histogram"]
    macd_line = tech["macd"]
    macd_sig = tech["macd_signal"]
    if macd_line > macd_sig and macd_hist > 0:
        long_score += 15
        signals_detail.append("MACD bullish cross")
    elif macd_line > macd_sig:
        long_score += 8
    elif macd_line < macd_sig and macd_hist < 0:
        short_score += 15
        signals_detail.append("MACD bearish cross")
    elif macd_line < macd_sig:
        short_score += 8

    # --- Bollinger Bands (10 pts) ---
    bb_pct = tech["bb_pct_b"]
    if bb_pct > 0.8:
        long_score += 10
        signals_detail.append(f"BB upper band ({bb_pct:.2f})")
    elif bb_pct > 0.5:
        long_score += 5
    elif bb_pct < 0.2:
        short_score += 10
        signals_detail.append(f"BB lower band ({bb_pct:.2f})")
    elif bb_pct < 0.5:
        short_score += 5

    # --- RVOL (10 pts) ---
    rvol = tech["rvol"]
    if rvol >= 2.0:
        # High volume confirms whatever direction
        if long_score > short_score:
            long_score += 10
        else:
            short_score += 10
        signals_detail.append(f"RVOL high ({rvol}x)")
    elif rvol >= 1.5:
        if long_score > short_score:
            long_score += 6
        else:
            short_score += 6

    # --- ADX (10 pts) ---
    adx = tech["adx"]
    if adx > 25:
        # Strong trend, boost dominant direction
        if long_score > short_score:
            long_score += 10
        else:
            short_score += 10
        signals_detail.append(f"ADX trending ({adx})")
    elif adx > 20:
        if long_score > short_score:
            long_score += 5
        else:
            short_score += 5

    # --- Stochastic RSI (10 pts) ---
    stoch_k = tech["stoch_rsi_k"]
    stoch_d = tech["stoch_rsi_d"]
    if stoch_k > 80 and stoch_k > stoch_d:
        long_score += 5
        short_score += 5  # Momentum but overbought
    elif stoch_k > 50 and stoch_k > stoch_d:
        long_score += 10
        signals_detail.append("StochRSI bullish")
    elif stoch_k < 20 and stoch_k < stoch_d:
        short_score += 5
        long_score += 5  # Oversold potential bounce
    elif stoch_k < 50 and stoch_k < stoch_d:
        short_score += 10
        signals_detail.append("StochRSI bearish")

    # --- 52W position (10 pts) ---
    pct_52w = tech["pct_from_52w_high"]
    if pct_52w > -5:
        long_score += 10
        signals_detail.append("Near 52W high")
    elif pct_52w > -15:
        long_score += 5
    elif pct_52w < -30:
        short_score += 10
        signals_detail.append("Far from 52W high")
    elif pct_52w < -20:
        short_score += 5

    # --- Net score and classification ---
    net = long_score - short_score
    confidence = max(long_score, short_score)
    confidence = min(95, max(35, confidence))

    if net >= 40:
        classification = "STRONG LONG"
        direction = "LONG"
    elif net >= 15:
        classification = "LONG"
        direction = "LONG"
    elif net <= -40:
        classification = "STRONG SHORT"
        direction = "SHORT"
    elif net <= -15:
        classification = "SHORT"
        direction = "SHORT"
    else:
        classification = "NEUTRAL"
        direction = "LONG" if net >= 0 else "SHORT"

    # Regime confidence gate: if regime is uncertain, cap classification
    if regime_confidence < 20:
        if classification in ("STRONG LONG", "LONG"):
            classification = "WATCH-LONG"
            confidence = min(confidence, 55)
        elif classification in ("STRONG SHORT", "SHORT"):
            classification = "WATCH-SHORT"
            confidence = min(confidence, 55)

    return {
        "classification": classification,
        "direction": direction,
        "confidence": confidence,
        "long_score": long_score,
        "short_score": short_score,
        "net_score": net,
        "signals_detail": signals_detail,
    }


# ---------------------------------------------------------------------------
# Custom PDF class with header / footer
# ---------------------------------------------------------------------------

class _IntelPDF(FPDF):
    """Custom FPDF subclass with NZT-48 branded header and footer."""

    def __init__(self, session_label: str = "", timestamp_str: str = ""):
        super().__init__(orientation="P", unit="mm", format="A4")
        self._session_label = session_label
        self._timestamp_str = timestamp_str

    # -- Header --
    def header(self):
        # Dark header bar
        self.set_fill_color(*_DARK_BG)
        self.rect(0, 0, 210, 18, style="F")

        # Brand name
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(*_BRAND_GREEN)
        self.set_xy(8, 3)
        self.cell(0, 6, "NZT-48 INTELLIGENCE BRIEF", new_x="LMARGIN", new_y="NEXT")

        # Session + timestamp
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*_TEXT_WHITE)
        self.set_xy(8, 10)
        label = _safe(f"{self._session_label}  |  {self._timestamp_str}  |  LIVE DATA")
        self.cell(0, 5, label, new_x="LMARGIN", new_y="NEXT")

        # Green accent line
        self.set_draw_color(*_BRAND_GREEN)
        self.set_line_width(0.6)
        self.line(0, 18, 210, 18)

        self.set_y(22)

    # -- Footer --
    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 6.5)
        self.set_text_color(*_TEXT_MUTED)
        self.cell(
            0, 8,
            _safe(f"NZT-48 Trading System  |  CONFIDENTIAL  |  Page {self.page_no()}/{{nb}}"),
            align="C",
        )


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class PDFIntelligenceReport:
    """Generates and delivers daily PDF intelligence reports with live market data.

    Usage::

        report = PDFIntelligenceReport()
        path = report.generate_pre_lse_report()   # returns PDF file path
        path = report.generate_pre_nyse_report()
    """

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = db_path
        self._output_dir = Path(__file__).parent.parent / "data" / "reports"
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Pre-load ISA mapping from config
        self._isa_mapping: dict[str, str] = cfg.get("bot_a_universe.isa_mapping", {}) or {}

        # Leveraged ETP metadata for display
        self._etp_meta = self._build_etp_meta()

        # Cache for live technicals (populated once per report generation)
        self._live_cache: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_pre_lse_report(self) -> str:
        """Generate the Pre-LSE PDF (06:00 UK). Returns file path."""
        return self._build_report(session="PRE-LSE")

    def generate_pre_nyse_report(self) -> str:
        """Generate the Pre-NYSE PDF (12:30 UK / 07:30 ET). Returns file path."""
        return self._build_report(session="PRE-NYSE")

    async def send_via_telegram(self, pdf_path: str) -> bool:
        """Deliver the generated PDF via Telegram."""
        return await self._send_via_telegram(pdf_path)

    # ------------------------------------------------------------------
    # Internal builder
    # ------------------------------------------------------------------

    def _build_report(self, session: str) -> str:
        """Internal: assemble all sections and render the PDF."""
        import time as _time
        _build_start = _time.monotonic()
        logger.info("PDF report build started: session=%s", session)

        now_utc = datetime.now(timezone.utc)
        ts_str = now_utc.strftime("%Y-%m-%d %H:%M UTC")
        date_str = now_utc.strftime("%Y%m%d_%H%M")

        pdf = _IntelPDF(session_label=session, timestamp_str=ts_str)
        pdf.alias_nb_pages()
        pdf.set_auto_page_break(auto=True, margin=16)
        pdf.add_page()

        # ---------- Fetch all live data first ----------
        logger.info("Fetching live data for %d ISA tickers...", len(ISA_PRIMARY_TICKERS))
        self._live_cache = {}
        for ticker in ISA_PRIMARY_TICKERS:
            tech = _fetch_live_technicals(ticker)
            if tech:
                self._live_cache[ticker] = tech
        logger.info("Live data fetched: %d/%d tickers", len(self._live_cache), len(ISA_PRIMARY_TICKERS))

        # Gather data
        market = self._gather_market_data()
        self._market_data = market  # Store for regime confidence gate
        plays = self._gather_potential_plays(session)
        learning = self._gather_learning_insights()
        risk = self._gather_risk_dashboard()
        etp_data = self._gather_isa_etp_prices()

        # --- MANIFEST STRIP ---
        manifest = RunManifest(
            universe_name="ISA_EXTENDED",
            universe_count=len(ISA_PRIMARY_TICKERS),
            data_vendor="yfinance",
            strategy_version="V8.0",
            go_nogo=risk.get("go_nogo", "N/A") if isinstance(risk, dict) else "N/A",
            data_health="PASS",
        )
        render_manifest_strip(pdf, manifest)

        # --- SUMMARY STATISTICS (top of report) ---
        self._render_summary_statistics(pdf, plays, market, risk)

        # --- SECTION 1: MARKET OVERVIEW ---
        self._render_section_header(pdf, "1", "MARKET OVERVIEW")
        self._render_market_overview(pdf, market)

        # --- GO / NO-GO gate for play rendering ---
        go_nogo = risk.get("go_nogo", "NO-GO")

        if go_nogo == "NO-GO":
            # Red NO-GO banner
            self._render_nogo_banner(pdf, risk)
            # Show plays as intel-only (no entry/stop/target)
            self._render_plays_intel_only(pdf, plays)
        else:
            # --- SECTION 2: TOP POTENTIAL PLAYS (LONG) ---
            self._render_section_header(pdf, "2", "TOP PLAYS -- LONG")
            self._render_plays_table(pdf, plays.get("longs", []), direction="LONG")
            self._render_play_details(pdf, plays.get("longs", [])[:5])

            # --- SECTION 3: TOP POTENTIAL PLAYS (SHORT) ---
            self._render_section_header(pdf, "3", "TOP PLAYS -- SHORT")
            self._render_plays_table(pdf, plays.get("shorts", []), direction="SHORT")
            self._render_play_details(pdf, plays.get("shorts", [])[:3])

        # --- SECTION 4: ISA FUNDS DASHBOARD ---
        self._render_section_header(pdf, "4", "ISA LEVERAGED ETPs -- LIVE PRICES")
        self._render_isa_funds(pdf, etp_data)

        # --- SECTION 5: FULL TICKER HEATMAP ---
        self._render_section_header(pdf, "5", f"FULL TICKER HEATMAP ({len(ISA_PRIMARY_TICKERS)} ISA TICKERS)")
        self._render_ticker_heatmap(pdf)

        # --- SECTION 6: SELF-LEARNING INSIGHTS ---
        self._render_section_header(pdf, "6", "SELF-LEARNING INSIGHTS")
        self._render_learning_insights(pdf, learning)

        # --- SECTION 7: RISK DASHBOARD ---
        self._render_section_header(pdf, "7", "RISK DASHBOARD")
        self._render_risk_dashboard(pdf, risk)

        # --- DISCLAIMER & GENERATION TIMESTAMP (last page footer) ---
        self._render_disclaimer(pdf, ts_str)

        # Write PDF
        filename = f"NZT48_INTEL_{session.replace('-', '')}_{date_str}.pdf"
        pdf_path = str(self._output_dir / filename)
        pdf.output(pdf_path)
        _build_elapsed = _time.monotonic() - _build_start
        logger.info(
            "PDF intelligence report generated: %s (session=%s, pages=%d, elapsed=%.2fs)",
            pdf_path, session, pdf.page_no(), _build_elapsed,
        )
        return pdf_path

    # ------------------------------------------------------------------
    # Data gathering
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        return get_connection(self._db_path)

    def _gather_market_data(self) -> dict:
        """Gather live market overview: VIX, futures, DXY, 10Y yield, regime from DB."""
        data: dict[str, Any] = {
            "regime": "N/A",
            "regime_confidence": 0.0,
            "vix": 0.0,
            "vix_change": 0.0,
            "gex": "N/A",
            "dix": 0.0,
            "internals": 0,
            "sp500_futures": 0.0,
            "sp500_price": 0.0,
            "nasdaq_futures": 0.0,
            "nasdaq_price": 0.0,
            "dxy": 0.0,
            "dxy_change": 0.0,
            "us10y": 0.0,
            "us10y_change": 0.0,
            "market_bias": "NEUTRAL",
            "macro_events": [],
        }

        # DB data for regime and premarket brief
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT state, confidence, vix, gex, dix, internals_composite, trigger_reason "
                "FROM regime_history ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if row:
                data["regime"] = row["state"] or "N/A"
                data["regime_confidence"] = row["confidence"] or 0.0
                gex_val = row["gex"]
                if gex_val is not None:
                    try:
                        gex_num = float(gex_val)
                        data["gex"] = "POSITIVE" if gex_num > 0 else "NEGATIVE"
                    except (ValueError, TypeError):
                        data["gex"] = str(gex_val)
                data["dix"] = row["dix"] or 0.0
                data["internals"] = row["internals_composite"] or 0

            brief_row = conn.execute(
                "SELECT brief_json FROM premarket_briefs ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if brief_row and brief_row["brief_json"]:
                import json
                try:
                    brief = json.loads(brief_row["brief_json"])
                    data["market_bias"] = brief.get("market_bias", "NEUTRAL")
                    data["macro_events"] = brief.get("risk_flags", [])
                except (json.JSONDecodeError, TypeError):
                    pass
        finally:
            conn.close()

        # --- LIVE DATA from yfinance ---
        _live_market_tickers = {
            "^VIX": "vix",
            "ES=F": "sp500",
            "NQ=F": "nasdaq",
            "DX-Y.NYB": "dxy",
            "^TNX": "us10y",
        }

        for yf_ticker, label in _live_market_tickers.items():
            try:
                tk = yf.Ticker(yf_ticker)
                fi = tk.fast_info
                last = getattr(fi, "last_price", None)
                prev = getattr(fi, "previous_close", None)
                if last and prev and prev > 0:
                    chg_pct = round((last - prev) / prev * 100, 2)
                    if label == "vix":
                        data["vix"] = round(last, 2)
                        data["vix_change"] = chg_pct
                    elif label == "sp500":
                        data["sp500_price"] = round(last, 2)
                        data["sp500_futures"] = chg_pct
                    elif label == "nasdaq":
                        data["nasdaq_price"] = round(last, 2)
                        data["nasdaq_futures"] = chg_pct
                    elif label == "dxy":
                        data["dxy"] = round(last, 2)
                        data["dxy_change"] = chg_pct
                    elif label == "us10y":
                        data["us10y"] = round(last, 3)
                        data["us10y_change"] = chg_pct
            except Exception as e:
                logger.debug("Failed to fetch %s: %s", yf_ticker, e)

        return data

    def _gather_potential_plays(self, session: str) -> dict:
        """Generate real trading plays from live yfinance data + technical analysis."""
        plays: dict[str, Any] = {"longs": [], "shorts": []}
        conn = self._get_conn()

        try:
            for ticker in ISA_PRIMARY_TICKERS:
                tech = self._live_cache.get(ticker)
                if tech is None:
                    continue

                _rc = getattr(self, '_market_data', {}).get("regime_confidence", 100.0)
                signal = _classify_signal(tech, regime_confidence=_rc)
                play = self._build_live_play(tech, signal, conn, ticker)
                if play is None:
                    continue

                if play["direction"] == "LONG" and signal["classification"] in ("STRONG LONG", "LONG"):
                    plays["longs"].append(play)
                elif play["direction"] == "SHORT" and signal["classification"] in ("STRONG SHORT", "SHORT"):
                    plays["shorts"].append(play)
                elif signal["classification"] in ("WATCH-LONG", "WATCH-SHORT"):
                    # Regime confidence gate capped these -- include with reduced confidence
                    play["confidence"] = max(35, play["confidence"] - 15)
                    if play["direction"] == "LONG":
                        plays["longs"].append(play)
                    else:
                        plays["shorts"].append(play)
                elif signal["classification"] == "NEUTRAL":
                    # Slight lean determines bucket, lower confidence
                    play["confidence"] = max(35, play["confidence"] - 15)
                    if play["direction"] == "LONG":
                        plays["longs"].append(play)
                    else:
                        plays["shorts"].append(play)

            # Sort by confidence descending
            plays["longs"] = sorted(plays["longs"], key=lambda p: p["confidence"], reverse=True)[:8]
            plays["shorts"] = sorted(plays["shorts"], key=lambda p: p["confidence"], reverse=True)[:5]

        finally:
            conn.close()

        return plays

    def _build_live_play(self, tech: dict, signal: dict, conn: sqlite3.Connection,
                         ticker: str) -> Optional[dict]:
        """Build a complete play from live technicals and signal classification."""
        price = tech["price"]
        atr = tech["atr"]
        direction = signal["direction"]
        classification = signal["classification"]
        confidence = signal["confidence"]

        if price <= 0 or atr <= 0:
            return None

        stop_mult = cfg.get_ticker_override(ticker, "stop_mult", 1.5)

        # Entry, stop, targets (ATR-based)
        if direction == "LONG":
            # Entry zone: current price to slight pullback
            entry_low = round(price - atr * 0.3, 2)
            entry_high = round(price + atr * 0.1, 2)
            stop = round(price - atr * stop_mult, 2)
            risk = price - stop
            target_1r = round(price + risk, 2)
            target_2r = round(price + risk * 2, 2)
        else:
            entry_low = round(price - atr * 0.1, 2)
            entry_high = round(price + atr * 0.3, 2)
            stop = round(price + atr * stop_mult, 2)
            risk = stop - price
            target_1r = round(price - risk, 2)
            target_2r = round(price - risk * 2, 2)

        risk_reward = round(risk / (atr * stop_mult) if atr * stop_mult > 0 else 0, 2)

        # ISA mapping
        isa_key = f"{ticker}_{direction}"
        isa_ticker = self._isa_mapping.get(isa_key, "")
        isa_leverage = ""
        if isa_ticker:
            meta = self._etp_meta.get(isa_ticker, {})
            isa_leverage = meta.get("leverage", "3x")

        # Infer strategy from DB or technicals
        best_strategy = ""
        tp_row = conn.execute(
            "SELECT best_strategy, rolling_60d_wr, priority_score FROM ticker_profiles WHERE ticker = ?",
            (ticker,),
        ).fetchone()
        win_rate_60d = 0.0
        priority_score = 0.0
        if tp_row:
            best_strategy = tp_row["best_strategy"] or ""
            win_rate_60d = tp_row["rolling_60d_wr"] or 0.0
            priority_score = tp_row["priority_score"] or 0.0

        # Get regime for strategy inference
        regime_row = conn.execute(
            "SELECT state FROM regime_history ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        regime = regime_row["state"] if regime_row else "RANGE_BOUND"

        if not best_strategy:
            best_strategy = self._infer_strategy(regime, direction)

        # Recent win count
        recent_wins = 0
        try:
            recent_wins = conn.execute(
                "SELECT COUNT(*) FROM ("
                "  SELECT net_pnl FROM virtual_trades "
                "  WHERE ticker = ? AND exit_time IS NOT NULL "
                "  ORDER BY exit_time DESC LIMIT 10"
                ") WHERE net_pnl > 0",
                (ticker,),
            ).fetchone()[0]
        except Exception:
            pass

        return {
            "ticker": ticker,
            "direction": direction,
            "classification": classification,
            "entry": round(price, 2),
            "entry_zone": f"${entry_low} - ${entry_high}",
            "stop": stop,
            "target_1r": target_1r,
            "target_2r": target_2r,
            "confidence": confidence,
            "risk_reward": f"1:{round(1.0, 1)}",
            "strategy": best_strategy,
            "strategy_name": STRATEGY_NAMES.get(best_strategy, best_strategy),
            "isa_ticker": isa_ticker,
            "isa_leverage": isa_leverage,
            "priority_score": priority_score,
            "win_rate_60d": win_rate_60d,
            "recent_wins_10": recent_wins,
            "atr": atr,
            "atr_pct": tech["atr_pct"],
            "rsi": tech["rsi"],
            "macd_hist": tech["macd_histogram"],
            "rvol": tech["rvol"],
            "adx": tech["adx"],
            "ema_stack": f"{tech['ema9']}/{tech['ema20']}/{tech['ema50']}",
            "bb_pct_b": tech["bb_pct_b"],
            "stoch_k": tech["stoch_rsi_k"],
            "change_pct": tech["change_pct"],
            "price": price,
            "volume": tech["volume"],
            "signals_detail": signal["signals_detail"],
            "long_score": signal["long_score"],
            "short_score": signal["short_score"],
        }

    def _gather_isa_etp_prices(self) -> list[dict]:
        """Fetch live prices for all ISA leveraged ETPs."""
        results = []
        for etp_ticker in ISA_ETP_TICKERS:
            try:
                tk = yf.Ticker(etp_ticker)
                fi = tk.fast_info
                last = getattr(fi, "last_price", None)
                prev = getattr(fi, "previous_close", None)
                if last and prev and prev > 0:
                    change_pct = round((last - prev) / prev * 100, 2)
                else:
                    last = last or 0
                    change_pct = 0

                meta = self._etp_meta.get(etp_ticker, {})
                results.append({
                    "ticker": etp_ticker,
                    "price": round(float(last), 2) if last else 0.0,
                    "change_pct": change_pct,
                    "leverage": meta.get("leverage", "3x"),
                    "direction": meta.get("direction", "LONG"),
                    "underlying": meta.get("underlying", meta.get("index", "")),
                    "provider": meta.get("provider", ""),
                })
            except Exception as e:
                logger.debug("Failed to fetch ETP %s: %s", etp_ticker, e)
                meta = self._etp_meta.get(etp_ticker, {})
                results.append({
                    "ticker": etp_ticker,
                    "price": 0.0,
                    "change_pct": 0.0,
                    "leverage": meta.get("leverage", "3x"),
                    "direction": meta.get("direction", "LONG"),
                    "underlying": meta.get("underlying", meta.get("index", "")),
                    "provider": meta.get("provider", ""),
                })

        return results

    def _infer_strategy(self, regime: str, direction: str) -> str:
        """Infer the most likely strategy from regime + direction."""
        regime_upper = (regime or "").upper()
        if "TRENDING_UP" in regime_upper and direction == "LONG":
            return "S1"
        elif "TRENDING_DOWN" in regime_upper and direction == "SHORT":
            return "S1"
        elif "RANGE" in regime_upper:
            return "S3"
        elif "HIGH_VOL" in regime_upper:
            return "S8"
        elif "RISK_OFF" in regime_upper:
            return "S6"
        elif direction == "LONG":
            return "S2"
        else:
            return "S3"

    def _gather_learning_insights(self) -> dict:
        """Gather self-learning engine insights from the database."""
        insights: dict[str, Any] = {
            "top_strategy_current_regime": "N/A",
            "regime_matrix_top": [],
            "ticker_leaderboard": [],
            "strong_repeaters": [],
            "patterns_to_watch": [],
        }

        conn = self._get_conn()
        try:
            regime_row = conn.execute(
                "SELECT state FROM regime_history ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()

            # Top strategy in current regime from strategy_daily_stats
            strat_rows = conn.execute(
                "SELECT strategy, SUM(net_pnl) as total_pnl, "
                "AVG(win_rate) as avg_wr, SUM(trades) as total_trades "
                "FROM strategy_daily_stats "
                "WHERE date >= date('now', '-30 days') "
                "GROUP BY strategy "
                "HAVING total_trades >= 3 "
                "ORDER BY total_pnl DESC LIMIT 5",
            ).fetchall()

            if strat_rows:
                top = strat_rows[0]
                insights["top_strategy_current_regime"] = (
                    f"{top['strategy']} ({STRATEGY_NAMES.get(top['strategy'], top['strategy'])}) "
                    f"-- {top['total_trades']} trades, "
                    f"WR {(top['avg_wr'] or 0) * 100:.0f}%, "
                    f"P&L ${top['total_pnl'] or 0:+.0f}"
                )
                insights["regime_matrix_top"] = [
                    {
                        "strategy": r["strategy"],
                        "name": STRATEGY_NAMES.get(r["strategy"], r["strategy"]),
                        "trades": r["total_trades"] or 0,
                        "wr": round((r["avg_wr"] or 0) * 100, 0),
                        "pnl": round(r["total_pnl"] or 0, 2),
                    }
                    for r in strat_rows
                ]

            # Ticker leaderboard from ticker_profiles
            tp_rows = conn.execute(
                "SELECT ticker, priority_score, rolling_60d_wr, best_strategy "
                "FROM ticker_profiles ORDER BY priority_score DESC LIMIT 10"
            ).fetchall()
            insights["ticker_leaderboard"] = [
                {
                    "ticker": r["ticker"],
                    "priority": round(r["priority_score"] or 0, 2),
                    "wr_60d": round((r["rolling_60d_wr"] or 0) * 100, 0),
                    "best_strat": r["best_strategy"] or "N/A",
                }
                for r in tp_rows
            ]

            # Strong repeaters: tickers with 3+ wins in last 10 trades
            for ticker in ISA_PRIMARY_TICKERS:
                recent = conn.execute(
                    "SELECT net_pnl, r_multiple FROM virtual_trades "
                    "WHERE ticker = ? ORDER BY exit_time DESC LIMIT 10",
                    (ticker,),
                ).fetchall()
                wins = sum(1 for r in recent if (r["net_pnl"] or 0) > 0)
                if wins >= 3 and len(recent) >= 5:
                    avg_r = sum(r["r_multiple"] or 0 for r in recent) / len(recent) if recent else 0
                    insights["strong_repeaters"].append({
                        "ticker": ticker,
                        "wins_last_10": wins,
                        "trades_last_10": len(recent),
                        "avg_r": round(avg_r, 2),
                    })
            insights["strong_repeaters"].sort(key=lambda x: x["wins_last_10"], reverse=True)

            # Patterns to watch from trade_autopsies
            lesson_rows = conn.execute(
                "SELECT primary_lesson, COUNT(*) as cnt "
                "FROM trade_autopsies "
                "WHERE primary_lesson IS NOT NULL AND primary_lesson != '' "
                "AND created_at >= date('now', '-7 days') "
                "GROUP BY primary_lesson "
                "ORDER BY cnt DESC LIMIT 5"
            ).fetchall()
            insights["patterns_to_watch"] = [
                {"pattern": r["primary_lesson"], "occurrences": r["cnt"]}
                for r in lesson_rows
            ]

        finally:
            conn.close()

        return insights

    def _gather_risk_dashboard(self) -> dict:
        """Gather equity, drawdown, Go/No-Go, Kelly sizing."""
        risk: dict[str, Any] = {
            "equity": 10000.0,
            "starting_equity": 10000.0,
            "daily_pnl": 0.0,
            "weekly_pnl": 0.0,
            "daily_pnl_pct": 0.0,
            "weekly_pnl_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "current_drawdown_pct": 0.0,
            "portfolio_heat": 0.0,
            "open_positions": 0,
            "go_nogo": "NO-GO",
            "go_nogo_detail": {},
            "kelly_recommendation": 0.0075,
        }

        starting = cfg.get("system.starting_equity", 10000)
        risk["starting_equity"] = starting

        conn = self._get_conn()
        try:
            eq_row = conn.execute(
                "SELECT date, starting_equity, ending_equity, realised_pnl, "
                "unrealised_pnl, max_drawdown_pct, open_positions "
                "FROM equity_snapshots ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if eq_row:
                risk["equity"] = eq_row["ending_equity"] or starting
                risk["max_drawdown_pct"] = eq_row["max_drawdown_pct"] or 0
                risk["open_positions"] = eq_row["open_positions"] or 0

            equity = risk["equity"]

            daily_row = conn.execute(
                "SELECT COALESCE(SUM(net_pnl), 0) as daily "
                "FROM virtual_trades WHERE date(exit_time) = date('now')"
            ).fetchone()
            risk["daily_pnl"] = daily_row["daily"] if daily_row else 0.0
            risk["daily_pnl_pct"] = (risk["daily_pnl"] / equity * 100) if equity > 0 else 0

            weekly_row = conn.execute(
                "SELECT COALESCE(SUM(net_pnl), 0) as weekly "
                "FROM virtual_trades WHERE exit_time >= datetime('now', '-7 days')"
            ).fetchone()
            risk["weekly_pnl"] = weekly_row["weekly"] if weekly_row else 0.0
            risk["weekly_pnl_pct"] = (risk["weekly_pnl"] / equity * 100) if equity > 0 else 0

            peak_row = conn.execute(
                "SELECT MAX(ending_equity) as peak FROM equity_snapshots"
            ).fetchone()
            peak = peak_row["peak"] if peak_row and peak_row["peak"] else equity
            risk["current_drawdown_pct"] = round(
                ((peak - equity) / peak * 100) if peak > 0 else 0, 2
            )

            heat_row = conn.execute(
                "SELECT COALESCE(SUM(risk_dollars), 0) as total_risk FROM positions"
            ).fetchone()
            total_risk = heat_row["total_risk"] if heat_row else 0
            risk["portfolio_heat"] = round((total_risk / equity * 100) if equity > 0 else 0, 2)

            go_data = self._compute_go_nogo(conn)
            risk["go_nogo"] = "GO" if go_data["pass"] else "NO-GO"
            risk["go_nogo_detail"] = go_data

            risk["kelly_recommendation"] = self._compute_kelly(conn)

        finally:
            conn.close()

        return risk

    def _compute_go_nogo(self, conn: sqlite3.Connection) -> dict:
        """Compute Go/No-Go criteria."""
        total_row = conn.execute("SELECT COUNT(*) as cnt FROM virtual_trades").fetchone()
        total_trades = total_row["cnt"] if total_row else 0

        win_row = conn.execute("SELECT COUNT(*) as cnt FROM virtual_trades WHERE net_pnl > 0").fetchone()
        wins = win_row["cnt"] if win_row else 0
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        gross_win = conn.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) FROM virtual_trades WHERE net_pnl > 0"
        ).fetchone()[0]
        gross_loss = abs(conn.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) FROM virtual_trades WHERE net_pnl < 0"
        ).fetchone()[0])
        pf = (gross_win / gross_loss) if gross_loss > 0 else 0

        dd_row = conn.execute(
            "SELECT MAX(max_drawdown_pct) as dd FROM equity_snapshots"
        ).fetchone()
        max_dd = abs(dd_row["dd"]) if dd_row and dd_row["dd"] else 0

        criteria = {
            "total_trades": {"value": total_trades, "target": 50, "pass": total_trades >= 50},
            "win_rate": {"value": round(win_rate, 1), "target": 50, "pass": win_rate > 50},
            "profit_factor": {"value": round(pf, 2), "target": 1.3, "pass": pf > 1.3},
            "max_drawdown": {"value": round(max_dd, 1), "target": 8, "pass": max_dd < 8},
        }

        all_pass = all(c["pass"] for c in criteria.values())
        return {"pass": all_pass, "criteria": criteria}

    def _compute_kelly(self, conn: sqlite3.Connection) -> float:
        """Compute half-Kelly recommendation from recent trades."""
        rows = conn.execute(
            "SELECT r_multiple FROM virtual_trades "
            "WHERE r_multiple IS NOT NULL "
            "ORDER BY exit_time DESC LIMIT 60"
        ).fetchall()

        r_multiples = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
        if len(r_multiples) < 20:
            return 0.0075

        winners = [r for r in r_multiples if r > 0]
        losers = [r for r in r_multiples if r <= 0]
        if not losers:
            return 0.0075

        win_rate = len(winners) / len(r_multiples)
        avg_win = sum(winners) / len(winners) if winners else 0
        avg_loss = abs(sum(losers) / len(losers))
        if avg_loss == 0:
            return 0.0075

        wl_ratio = avg_win / avg_loss
        full_kelly = win_rate - (1 - win_rate) / wl_ratio
        half_kelly = full_kelly / 2

        return round(min(max(half_kelly, 0.002), 0.0075), 4)

    # ------------------------------------------------------------------
    # PDF rendering helpers
    # ------------------------------------------------------------------

    def _render_summary_statistics(self, pdf: _IntelPDF, plays: dict, market: dict, risk: dict):
        """Render summary statistics panel at the top of the report."""
        total_longs = len(plays.get("longs", []))
        total_shorts = len(plays.get("shorts", []))
        total_plays = total_longs + total_shorts
        all_plays = plays.get("longs", []) + plays.get("shorts", [])
        avg_confidence = (
            sum(p.get("confidence", 0) for p in all_plays) / len(all_plays)
            if all_plays else 0
        )

        # Top play
        top_play_str = "None"
        if all_plays:
            top = sorted(all_plays, key=lambda p: p["confidence"], reverse=True)[0]
            top_play_str = f"{top['ticker']} {top['direction']} ({top['confidence']}%)"

        # Summary box
        pdf.set_fill_color(235, 238, 245)
        pdf.set_draw_color(*_BORDER_GREY)
        y_start = pdf.get_y()
        pdf.rect(10, y_start, 190, 24, style="DF")

        pdf.set_xy(14, y_start + 2)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_TEXT_DARK)
        pdf.cell(0, 5, _safe("EXECUTIVE SUMMARY"), new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "", 8)
        pdf.set_x(14)
        summary_text = (
            f"Plays: {total_plays} ({total_longs}L / {total_shorts}S)  |  "
            f"Avg Conf: {avg_confidence:.0f}%  |  "
            f"Top: {top_play_str}  |  "
            f"VIX: {market.get('vix', 0):.1f}"
        )
        pdf.cell(0, 5, _safe(summary_text), new_x="LMARGIN", new_y="NEXT")

        pdf.set_x(14)
        summary_text2 = (
            f"Regime: {market.get('regime', 'N/A')}  |  "
            f"Equity: ${risk.get('equity', 0):,.0f}  |  "
            f"Go/No-Go: {risk.get('go_nogo', 'N/A')}  |  "
            f"Heat: {risk.get('portfolio_heat', 0):.1f}%  |  "
            f"Kelly: {risk.get('kelly_recommendation', 0.0075) * 100:.2f}%"
        )
        pdf.cell(0, 5, _safe(summary_text2), new_x="LMARGIN", new_y="NEXT")

        pdf.set_xy(14, y_start + 17)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*_TEXT_MUTED)
        sp = market.get("sp500_futures", 0)
        nq = market.get("nasdaq_futures", 0)
        pdf.cell(0, 4, _safe(
            f"S&P Futs: {sp:+.2f}%  |  NQ Futs: {nq:+.2f}%  |  "
            f"DXY: {market.get('dxy', 0):.2f} ({market.get('dxy_change', 0):+.2f}%)  |  "
            f"10Y: {market.get('us10y', 0):.3f}%"
        ), new_x="LMARGIN", new_y="NEXT")

        pdf.set_text_color(*_TEXT_DARK)
        pdf.set_y(y_start + 27)

    def _render_disclaimer(self, pdf: _IntelPDF, timestamp_str: str):
        """Render disclaimer and generation timestamp on the last page."""
        if pdf.get_y() > 245:
            pdf.add_page()

        pdf.ln(6)

        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*_TEXT_MUTED)
        pdf.cell(0, 4, _safe(f"Generated at: {timestamp_str}  |  Data source: yfinance (live)"),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

        pdf.set_draw_color(*_BORDER_GREY)
        pdf.set_fill_color(250, 250, 252)
        y_start = pdf.get_y()

        disclaimer_text = (
            "DISCLAIMER: This report is generated by the NZT-48 automated trading system "
            "for informational and educational purposes only. It does not constitute financial "
            "advice, investment recommendation, or solicitation to buy or sell any securities. "
            "Past performance is not indicative of future results. All trading involves risk "
            "of loss. The signals, confidence scores, and analysis contained herein are "
            "model outputs and should not be relied upon as the sole basis for any trading "
            "decision. Always conduct your own research and consider your financial situation "
            "before making investment decisions."
        )

        pdf.set_font("Helvetica", "I", 6)
        pdf.set_text_color(100, 100, 110)
        pdf.rect(10, y_start, 190, 22, style="DF")
        pdf.set_xy(12, y_start + 1)
        pdf.multi_cell(186, 3.2, _safe(disclaimer_text), align="L")
        pdf.set_text_color(*_TEXT_DARK)

    def _render_section_header(self, pdf: _IntelPDF, number: str, title: str):
        """Render a section header with green accent line."""
        if pdf.get_y() > 255:
            pdf.add_page()

        pdf.ln(4)
        pdf.set_draw_color(*_SECTION_LINE)
        pdf.set_line_width(0.5)
        y = pdf.get_y()
        pdf.line(10, y, 200, y)
        pdf.ln(2)

        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*_BRAND_GREEN)
        pdf.cell(0, 7, _safe(f"SECTION {number}: {title}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    def _render_market_overview(self, pdf: _IntelPDF, data: dict):
        """Render Section 1: Market Overview with live data."""
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*_TEXT_DARK)

        # Regime state
        regime = data.get("regime", "N/A")
        regime_conf = data.get("regime_confidence", 0)

        if "UP" in str(regime).upper():
            pdf.set_text_color(*_BRAND_GREEN)
        elif "DOWN" in str(regime).upper() or "RISK_OFF" in str(regime).upper():
            pdf.set_text_color(*_BRAND_RED)
        else:
            pdf.set_text_color(*_BRAND_AMBER)

        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, _safe(f"Regime: {regime} (conf: {regime_conf:.0f}%)"),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_TEXT_DARK)
        pdf.ln(1)

        # Live market indicators grid (2 rows)
        pdf.set_font("Helvetica", "", 8)

        # Row 1: VIX, S&P Futures, Nasdaq Futures
        vix = data.get("vix", 0)
        vix_chg = data.get("vix_change", 0)
        sp = data.get("sp500_futures", 0)
        nq = data.get("nasdaq_futures", 0)

        # Compact metric boxes
        _metrics_row1 = [
            ("VIX", f"{vix:.1f}", f"{vix_chg:+.1f}%", _BRAND_RED if vix > 20 else _BRAND_GREEN if vix < 15 else _BRAND_AMBER),
            ("S&P Futs", f"{data.get('sp500_price', 0):,.0f}", f"{sp:+.2f}%", _BRAND_GREEN if sp > 0 else _BRAND_RED),
            ("NQ Futs", f"{data.get('nasdaq_price', 0):,.0f}", f"{nq:+.2f}%", _BRAND_GREEN if nq > 0 else _BRAND_RED),
            ("DXY", f"{data.get('dxy', 0):.2f}", f"{data.get('dxy_change', 0):+.2f}%", _TEXT_DARK),
            ("10Y Yield", f"{data.get('us10y', 0):.3f}%", f"{data.get('us10y_change', 0):+.2f}%", _TEXT_DARK),
        ]

        self._render_metric_boxes(pdf, _metrics_row1)

        pdf.ln(2)

        # Row 2: Internal indicators from DB
        pdf.set_font("Helvetica", "", 8)
        metrics2 = [
            f"GEX: {data.get('gex', 'N/A')}",
            f"DIX: {data.get('dix', 0):.3f}",
            f"Internals: {data.get('internals', 0)}/4",
            f"Bias: {data.get('market_bias', 'NEUTRAL')}",
        ]
        pdf.cell(0, 5, _safe("  |  ".join(metrics2)), new_x="LMARGIN", new_y="NEXT")

        # Macro events
        events = data.get("macro_events", [])
        if events:
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(0, 5, "Key Events / Risk Flags:", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 7.5)
            for event in events[:5]:
                pdf.set_x(14)
                pdf.cell(0, 4, _safe(f"- {event}"), new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.ln(1)
            pdf.set_font("Helvetica", "I", 7.5)
            pdf.set_text_color(*_TEXT_MUTED)
            pdf.cell(0, 4, "No significant macro events flagged.", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*_TEXT_DARK)

        pdf.ln(2)

    def _render_metric_boxes(self, pdf: _IntelPDF, metrics: list):
        """Render a row of compact metric boxes."""
        box_w = 37
        box_h = 14
        start_x = 10
        y = pdf.get_y()

        for i, (label, value, change, color) in enumerate(metrics):
            x = start_x + i * (box_w + 1)
            # Box background
            pdf.set_fill_color(245, 247, 252)
            pdf.set_draw_color(*_BORDER_GREY)
            pdf.rect(x, y, box_w, box_h, style="DF")

            # Label
            pdf.set_xy(x + 1, y + 1)
            pdf.set_font("Helvetica", "", 6)
            pdf.set_text_color(*_TEXT_MUTED)
            pdf.cell(box_w - 2, 3, _safe(label), align="L")

            # Value
            pdf.set_xy(x + 1, y + 4.5)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*_TEXT_DARK)
            pdf.cell(box_w - 2, 4, _safe(str(value)), align="L")

            # Change
            pdf.set_xy(x + 1, y + 9)
            pdf.set_font("Helvetica", "", 6.5)
            pdf.set_text_color(*color)
            pdf.cell(box_w - 2, 3.5, _safe(str(change)), align="L")

        pdf.set_text_color(*_TEXT_DARK)
        pdf.set_y(y + box_h + 2)

    def _render_nogo_banner(self, pdf, risk):
        """Full-width red banner when system is in NO-GO mode."""
        y = pdf.get_y()
        pdf.set_fill_color(180, 20, 20)
        pdf.rect(0, y, 210, 16, "F")
        pdf.set_xy(6, y + 1)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 7, _safe("SYSTEM STATUS: NO-GO -- OBSERVATION ONLY"),
                 new_x="LMARGIN", new_y="NEXT")

        detail = risk.get("go_nogo_detail", {}).get("criteria", {})
        fails = [k for k, v in detail.items() if not v.get("pass", True)]
        pdf.set_xy(6, y + 9)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(0, 5, _safe(f"Failed criteria: {', '.join(fails) if fails else 'insufficient trade history'}"),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.set_y(y + 18)

    def _render_plays_intel_only(self, pdf, plays):
        """Render plays in observation-only mode (NO-GO). No entry/stop/target.

        Never renders a blank page -- always shows INTEL cards, sector rotation,
        and near-miss candidates even when no plays qualify.
        """
        all_plays = plays.get("longs", []) + plays.get("shorts", [])

        self._render_section_header(pdf, "2", "MARKET OBSERVATIONS (NO-GO -- READ ONLY)")

        if all_plays:
            # Simplified table: Ticker, Price, Change%, Classification, RSI, RVOL, ADX
            col_widths = [20, 22, 18, 28, 14, 14, 14]
            headers = ["Ticker", "Price", "Chg%", "Classification", "RSI", "RVOL", "ADX"]

            pdf.set_font("Helvetica", "B", 6)
            pdf.set_fill_color(60, 60, 80)
            pdf.set_text_color(255, 255, 255)
            for i, hdr in enumerate(headers):
                pdf.cell(col_widths[i], 5, _safe(hdr), border=1, fill=True, align="C")
            pdf.ln()

            for idx, play in enumerate(all_plays[:20]):
                if pdf.get_y() > 270:
                    pdf.add_page()

                bg = (245, 247, 252) if idx % 2 == 0 else (255, 255, 255)
                pdf.set_fill_color(*bg)
                pdf.set_font("Helvetica", "", 6)
                pdf.set_text_color(40, 40, 60)

                chg = play.get("change_pct", 0)
                classification = play.get("classification", "NEUTRAL")

                pdf.cell(col_widths[0], 5, _safe(play.get("ticker", "")), border=1, fill=True, align="C")
                pdf.cell(col_widths[1], 5, _safe(f"${play.get('price', 0):.2f}"), border=1, fill=True, align="C")

                chg_col = (20, 140, 60) if chg > 0 else (180, 30, 30) if chg < 0 else (40, 40, 60)
                pdf.set_text_color(*chg_col)
                pdf.cell(col_widths[2], 5, _safe(f"{chg:+.1f}%"), border=1, fill=True, align="C")
                pdf.set_text_color(40, 40, 60)

                pdf.cell(col_widths[3], 5, _safe(classification), border=1, fill=True, align="C")
                pdf.cell(col_widths[4], 5, _safe(f"{play.get('rsi', 50):.0f}"), border=1, fill=True, align="C")
                pdf.cell(col_widths[5], 5, _safe(f"{play.get('rvol', 1.0):.1f}"), border=1, fill=True, align="C")
                pdf.cell(col_widths[6], 5, _safe(f"{play.get('adx', 0):.0f}"), border=1, fill=True, align="C")
                pdf.ln()

            pdf.ln(2)
        else:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(100, 100, 110)
            pdf.cell(0, 6, _safe("No qualified plays in current scan."), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

        # --- INTEL cards: show all tickers with basic data (no entry/stop/target) ---
        self._render_section_header(pdf, "2b", "INTEL CARDS -- ISA UNIVERSE SNAPSHOT")
        intel_cards = []
        for ticker in ISA_PRIMARY_TICKERS:
            tech = self._live_cache.get(ticker)
            if tech is None:
                continue
            _rc = getattr(self, '_market_data', {}).get("regime_confidence", 100.0)
            signal = _classify_signal(tech, regime_confidence=_rc)
            name = TICKER_NAMES.get(ticker, ticker)
            factor = get_factor_group(ticker)
            lev = LEVERAGE_MAP.get(ticker, 1.0)
            intel_cards.append({
                "ticker": ticker,
                "name": name,
                "price": tech["price"],
                "change_pct": tech["change_pct"],
                "rsi": tech["rsi"],
                "rvol": tech["rvol"],
                "adx": tech["adx"],
                "classification": signal["classification"],
                "confidence": signal["confidence"],
                "factor_group": factor,
                "leverage": lev,
            })

        if intel_cards:
            # Compact INTEL table
            ic_widths = [22, 16, 14, 10, 10, 10, 24, 10, 22]
            ic_headers = ["Ticker", "Price", "Chg%", "RSI", "RVOL", "ADX", "Signal", "Conf", "Factor"]
            pdf.set_font("Helvetica", "B", 5.5)
            pdf.set_fill_color(44, 50, 66)
            pdf.set_text_color(255, 255, 255)
            for i, hdr in enumerate(ic_headers):
                pdf.cell(ic_widths[i], 4.5, _safe(hdr), border=1, fill=True, align="C")
            pdf.ln()

            for idx, card in enumerate(intel_cards):
                if pdf.get_y() > 270:
                    pdf.add_page()
                    pdf.set_font("Helvetica", "B", 5.5)
                    pdf.set_fill_color(44, 50, 66)
                    pdf.set_text_color(255, 255, 255)
                    for i, hdr in enumerate(ic_headers):
                        pdf.cell(ic_widths[i], 4.5, _safe(hdr), border=1, fill=True, align="C")
                    pdf.ln()

                bg = (240, 242, 248) if idx % 2 == 0 else (255, 255, 255)
                pdf.set_fill_color(*bg)
                pdf.set_font("Helvetica", "", 5.5)
                pdf.set_text_color(30, 30, 36)

                pdf.cell(ic_widths[0], 4, _safe(card["ticker"]), border=1, fill=True, align="C")
                chg = card["change_pct"]
                price_col = (0, 200, 120) if chg > 0 else (220, 50, 50) if chg < 0 else (30, 30, 36)
                pdf.set_text_color(*price_col)
                pdf.cell(ic_widths[1], 4, _safe(f"{card['price']:.2f}"), border=1, fill=True, align="C")
                pdf.cell(ic_widths[2], 4, _safe(f"{chg:+.1f}%"), border=1, fill=True, align="C")
                pdf.set_text_color(30, 30, 36)
                pdf.cell(ic_widths[3], 4, _safe(f"{card['rsi']:.0f}"), border=1, fill=True, align="C")
                pdf.cell(ic_widths[4], 4, _safe(f"{card['rvol']:.1f}"), border=1, fill=True, align="C")
                pdf.cell(ic_widths[5], 4, _safe(f"{card['adx']:.0f}"), border=1, fill=True, align="C")
                pdf.cell(ic_widths[6], 4, _safe(card["classification"]), border=1, fill=True, align="C")
                pdf.cell(ic_widths[7], 4, _safe(f"{card['confidence']:.0f}"), border=1, fill=True, align="C")
                pdf.cell(ic_widths[8], 4, _safe(card["factor_group"][:12]), border=1, fill=True, align="C")
                pdf.ln()

            pdf.ln(2)

        # --- Near-miss candidates: tickers closest to qualifying ---
        near_misses = []
        for card in intel_cards:
            if card["classification"] in ("NEUTRAL", "WATCH-LONG", "WATCH-SHORT"):
                near_misses.append(card)
        near_misses.sort(key=lambda x: x["confidence"], reverse=True)

        if near_misses:
            self._render_section_header(pdf, "2c", "NEAR-MISS CANDIDATES -- CLOSEST TO GO")
            pdf.set_font("Helvetica", "", 6)
            pdf.set_text_color(40, 40, 60)
            for nm in near_misses[:8]:
                if pdf.get_y() > 270:
                    pdf.add_page()
                line = (
                    f"{nm['ticker']}  |  {nm['price']:.2f}  |  "
                    f"RSI {nm['rsi']:.0f}  |  RVOL {nm['rvol']:.1f}  |  "
                    f"ADX {nm['adx']:.0f}  |  {nm['classification']}  |  "
                    f"Conf {nm['confidence']:.0f}  |  {nm['factor_group']}"
                )
                pdf.cell(0, 4.5, _safe(line), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

        # --- Sector rotation snapshot ---
        self._render_section_header(pdf, "2d", "SECTOR ROTATION SNAPSHOT (NO-GO)")
        sector_data = {}
        for card in intel_cards:
            grp = card["factor_group"]
            sector_data.setdefault(grp, []).append(card)

        if sector_data:
            pdf.set_font("Helvetica", "", 6)
            for grp, members in sorted(sector_data.items()):
                if pdf.get_y() > 270:
                    pdf.add_page()
                avg_chg = sum(m["change_pct"] for m in members) / len(members) if members else 0
                tickers_str = ", ".join(m["ticker"] for m in members)
                grp_col = (0, 200, 120) if avg_chg > 0 else (220, 50, 50) if avg_chg < 0 else (120, 125, 140)
                pdf.set_text_color(*grp_col)
                line = f"{grp}: avg {avg_chg:+.1f}%  [{tickers_str}]"
                pdf.cell(0, 4.5, _safe(line), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
            pdf.ln(2)

        # NO-GO warning footer
        pdf.set_font("Helvetica", "I", 7)
        pdf.set_text_color(180, 30, 30)
        pdf.cell(0, 5, _safe("NOTE: Entry/Stop/Target columns suppressed -- system is in NO-GO mode. "
                              "Observation only until GO criteria are met."),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    def _render_plays_table(self, pdf: _IntelPDF, plays: list[dict], direction: str):
        """Render a plays table (long or short) with live data."""
        if not plays:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(*_TEXT_MUTED)
            pdf.cell(0, 6, _safe(f"No {direction.lower()} setups meeting criteria."),
                     new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*_TEXT_DARK)
            pdf.ln(2)
            return

        if pdf.get_y() > 220:
            pdf.add_page()

        # Column headers
        col_widths = [16, 18, 18, 18, 18, 18, 11, 11, 11, 11, 40]
        headers = ["Ticker", "Price", "Entry", "Stop", "T1(1R)", "T2(2R)",
                    "Conf", "RSI", "RVOL", "ADX", "ISA Equiv"]

        # Header row
        pdf.set_font("Helvetica", "B", 6)
        pdf.set_fill_color(*_TABLE_HDR_BG)
        pdf.set_text_color(*_TEXT_WHITE)
        for i, hdr in enumerate(headers):
            pdf.cell(col_widths[i], 5, _safe(hdr), border=1, fill=True, align="C")
        pdf.ln()

        # Data rows
        for idx, play in enumerate(plays):
            if pdf.get_y() > 270:
                pdf.add_page()
                pdf.set_font("Helvetica", "B", 6)
                pdf.set_fill_color(*_TABLE_HDR_BG)
                pdf.set_text_color(*_TEXT_WHITE)
                for i, hdr in enumerate(headers):
                    pdf.cell(col_widths[i], 5, _safe(hdr), border=1, fill=True, align="C")
                pdf.ln()

            bg = _GREEN_FAINT if direction == "LONG" else _RED_FAINT
            row_bg = bg if idx % 2 == 0 else _ROW_WHITE

            pdf.set_fill_color(*row_bg)
            pdf.set_font("Helvetica", "B", 6)
            pdf.set_text_color(*_TEXT_DARK)

            # Ticker (bold)
            pdf.cell(col_widths[0], 5, _safe(play["ticker"]), border=1, fill=True, align="C")

            # Price with change colour
            chg = play.get("change_pct", 0)
            price_color = _BRAND_GREEN if chg > 0 else _BRAND_RED if chg < 0 else _TEXT_DARK
            pdf.set_text_color(*price_color)
            pdf.set_font("Helvetica", "", 6)
            pdf.cell(col_widths[1], 5, _safe(f"${play['price']:.2f}"), border=1, fill=True, align="C")
            pdf.set_text_color(*_TEXT_DARK)

            # Entry, Stop, T1, T2
            pdf.cell(col_widths[2], 5, _safe(f"${play['entry']:.2f}"), border=1, fill=True, align="C")

            # Stop in red
            pdf.set_text_color(*_BRAND_RED)
            pdf.cell(col_widths[3], 5, _safe(f"${play['stop']:.2f}"), border=1, fill=True, align="C")
            pdf.set_text_color(*_TEXT_DARK)

            # Targets in green
            pdf.set_text_color(*_BRAND_GREEN)
            pdf.cell(col_widths[4], 5, _safe(f"${play['target_1r']:.2f}"), border=1, fill=True, align="C")
            pdf.cell(col_widths[5], 5, _safe(f"${play['target_2r']:.2f}"), border=1, fill=True, align="C")
            pdf.set_text_color(*_TEXT_DARK)

            # Confidence (colour-coded)
            conf = play["confidence"]
            if conf >= 75:
                pdf.set_text_color(*_BRAND_GREEN)
            elif conf >= 60:
                pdf.set_text_color(*_BRAND_AMBER)
            else:
                pdf.set_text_color(*_BRAND_RED)
            pdf.set_font("Helvetica", "B", 6)
            pdf.cell(col_widths[6], 5, _safe(f"{conf:.0f}"), border=1, fill=True, align="C")
            pdf.set_text_color(*_TEXT_DARK)
            pdf.set_font("Helvetica", "", 6)

            # RSI (colour-coded)
            rsi = play.get("rsi", 50)
            if rsi > 70:
                pdf.set_text_color(*_BRAND_RED)
            elif rsi < 30:
                pdf.set_text_color(*_BRAND_GREEN)
            else:
                pdf.set_text_color(*_TEXT_DARK)
            pdf.cell(col_widths[7], 5, _safe(f"{rsi:.0f}"), border=1, fill=True, align="C")
            pdf.set_text_color(*_TEXT_DARK)

            # RVOL
            rvol = play.get("rvol", 1.0)
            if rvol >= 2.0:
                pdf.set_text_color(*_BRAND_GREEN)
            elif rvol >= 1.5:
                pdf.set_text_color(*_BRAND_AMBER)
            pdf.cell(col_widths[8], 5, _safe(f"{rvol:.1f}"), border=1, fill=True, align="C")
            pdf.set_text_color(*_TEXT_DARK)

            # ADX
            adx = play.get("adx", 0)
            if adx > 25:
                pdf.set_font("Helvetica", "B", 6)
            pdf.cell(col_widths[9], 5, _safe(f"{adx:.0f}"), border=1, fill=True, align="C")
            pdf.set_font("Helvetica", "", 6)

            # ISA equivalent
            isa_str = play.get("isa_ticker", "")
            if isa_str and play.get("isa_leverage"):
                isa_str = f"{isa_str} ({play['isa_leverage']})"
            elif not isa_str:
                isa_str = "SB only"
            if len(isa_str) > 26:
                isa_str = isa_str[:25] + ".."
            pdf.cell(col_widths[10], 5, _safe(isa_str), border=1, fill=True, align="C")

            pdf.ln()

        pdf.ln(2)

    def _render_play_details(self, pdf: _IntelPDF, plays: list[dict]):
        """Render detailed play cards for top plays."""
        if not plays:
            return

        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*_TEXT_DARK)
        pdf.cell(0, 5, "PLAY DETAILS:", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

        for play in plays:
            if pdf.get_y() > 250:
                pdf.add_page()

            # Play card box
            y_start = pdf.get_y()
            direction = play.get("direction", "LONG")
            card_bg = _GREEN_FAINT if direction == "LONG" else _RED_FAINT
            pdf.set_fill_color(*card_bg)
            pdf.set_draw_color(*_BORDER_GREY)
            pdf.rect(10, y_start, 190, 18, style="DF")

            # Ticker + classification
            pdf.set_xy(12, y_start + 1)
            dir_color = _BRAND_GREEN if direction == "LONG" else _BRAND_RED
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*dir_color)
            classification = play.get("classification", direction)
            pdf.cell(50, 4, _safe(f"{play['ticker']}  [{classification}]"), align="L")

            # Confidence badge
            conf = play["confidence"]
            pdf.set_xy(65, y_start + 1)
            pdf.set_font("Helvetica", "B", 8)
            if conf >= 75:
                pdf.set_text_color(*_BRAND_GREEN)
            elif conf >= 60:
                pdf.set_text_color(*_BRAND_AMBER)
            else:
                pdf.set_text_color(*_BRAND_RED)
            pdf.cell(20, 4, _safe(f"Conf: {conf}%"), align="L")

            # Strategy
            pdf.set_xy(88, y_start + 1)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*_TEXT_MUTED)
            strat = play.get("strategy", "")
            strat_name = play.get("strategy_name", "")
            pdf.cell(40, 4, _safe(f"Strategy: {strat} {strat_name}"), align="L")

            # Change %
            chg = play.get("change_pct", 0)
            pdf.set_xy(140, y_start + 1)
            chg_color = _BRAND_GREEN if chg > 0 else _BRAND_RED if chg < 0 else _TEXT_DARK
            pdf.set_text_color(*chg_color)
            pdf.set_font("Helvetica", "B", 7)
            pdf.cell(30, 4, _safe(f"Chg: {chg:+.2f}%"), align="L")

            # Price levels row
            pdf.set_xy(12, y_start + 5.5)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*_TEXT_DARK)
            entry_zone = play.get("entry_zone", f"${play['entry']:.2f}")
            pdf.cell(0, 4, _safe(
                f"Entry: {entry_zone}  |  "
                f"Stop: ${play['stop']:.2f}  |  "
                f"T1(1R): ${play['target_1r']:.2f}  |  "
                f"T2(2R): ${play['target_2r']:.2f}  |  "
                f"ATR: ${play.get('atr', 0):.2f} ({play.get('atr_pct', 0):.1f}%)"
            ), new_x="LMARGIN", new_y="NEXT")

            # Technicals row
            pdf.set_xy(12, y_start + 10)
            pdf.set_font("Helvetica", "", 6.5)
            pdf.set_text_color(*_TEXT_MUTED)
            signals = play.get("signals_detail", [])
            signals_str = " | ".join(signals[:4]) if signals else "No strong signals"
            pdf.cell(0, 4, _safe(
                f"RSI: {play.get('rsi', 0):.0f}  |  "
                f"MACD-H: {play.get('macd_hist', 0):.4f}  |  "
                f"RVOL: {play.get('rvol', 1.0):.1f}x  |  "
                f"ADX: {play.get('adx', 0):.0f}  |  "
                f"BB%: {play.get('bb_pct_b', 0.5):.2f}  |  "
                f"StochK: {play.get('stoch_k', 50):.0f}"
            ), new_x="LMARGIN", new_y="NEXT")

            # Signal summary row
            pdf.set_xy(12, y_start + 14)
            pdf.set_font("Helvetica", "I", 6)
            pdf.cell(0, 3.5, _safe(f"Signals: {signals_str}"), new_x="LMARGIN", new_y="NEXT")

            pdf.set_text_color(*_TEXT_DARK)
            pdf.set_y(y_start + 20)

    def _render_isa_funds(self, pdf: _IntelPDF, etp_data: list[dict]):
        """Render Section 4: ISA Funds Dashboard with live ETP prices."""
        if not etp_data:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(*_TEXT_MUTED)
            pdf.cell(0, 6, "No ETP data available.", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*_TEXT_DARK)
            return

        if pdf.get_y() > 220:
            pdf.add_page()

        # Table
        col_widths = [22, 22, 20, 15, 15, 50, 46]
        headers = ["Ticker", "Price (p)", "Chg %", "Lev", "Dir", "Underlying", "Provider"]

        pdf.set_font("Helvetica", "B", 6.5)
        pdf.set_fill_color(*_TABLE_HDR_BG)
        pdf.set_text_color(*_TEXT_WHITE)
        for i, hdr in enumerate(headers):
            pdf.cell(col_widths[i], 5, _safe(hdr), border=1, fill=True, align="C")
        pdf.ln()

        pdf.set_font("Helvetica", "", 6.5)
        for idx, etp in enumerate(etp_data):
            if pdf.get_y() > 270:
                pdf.add_page()
                pdf.set_font("Helvetica", "B", 6.5)
                pdf.set_fill_color(*_TABLE_HDR_BG)
                pdf.set_text_color(*_TEXT_WHITE)
                for i, hdr in enumerate(headers):
                    pdf.cell(col_widths[i], 5, _safe(hdr), border=1, fill=True, align="C")
                pdf.ln()
                pdf.set_font("Helvetica", "", 6.5)

            direction = etp.get("direction", "LONG")
            row_bg = _GREEN_FAINT if direction == "LONG" else _RED_FAINT
            row_bg = row_bg if idx % 2 == 0 else _ROW_WHITE
            pdf.set_fill_color(*row_bg)
            pdf.set_text_color(*_TEXT_DARK)

            # Ticker
            pdf.set_font("Helvetica", "B", 6.5)
            pdf.cell(col_widths[0], 4.5, _safe(etp["ticker"]), border=1, fill=True, align="C")
            pdf.set_font("Helvetica", "", 6.5)

            # Price
            price = etp.get("price", 0)
            pdf.cell(col_widths[1], 4.5, _safe(f"{price:.2f}" if price > 0 else "N/A"),
                     border=1, fill=True, align="C")

            # Change %
            chg = etp.get("change_pct", 0)
            chg_color = _BRAND_GREEN if chg > 0 else _BRAND_RED if chg < 0 else _TEXT_DARK
            pdf.set_text_color(*chg_color)
            pdf.cell(col_widths[2], 4.5, _safe(f"{chg:+.2f}%"), border=1, fill=True, align="C")
            pdf.set_text_color(*_TEXT_DARK)

            # Leverage
            pdf.cell(col_widths[3], 4.5, _safe(etp.get("leverage", "3x")),
                     border=1, fill=True, align="C")

            # Direction
            dir_color = _BRAND_GREEN if direction == "LONG" else _BRAND_RED
            pdf.set_text_color(*dir_color)
            pdf.cell(col_widths[4], 4.5, _safe(direction), border=1, fill=True, align="C")
            pdf.set_text_color(*_TEXT_DARK)

            # Underlying
            underlying = etp.get("underlying", "")
            if len(underlying) > 32:
                underlying = underlying[:31] + ".."
            pdf.cell(col_widths[5], 4.5, _safe(underlying), border=1, fill=True, align="C")

            # Provider
            provider = etp.get("provider", "")
            if len(provider) > 28:
                provider = provider[:27] + ".."
            pdf.cell(col_widths[6], 4.5, _safe(provider), border=1, fill=True, align="C")

            pdf.ln()

        pdf.set_text_color(*_TEXT_DARK)
        pdf.ln(2)

    def _render_ticker_heatmap(self, pdf: _IntelPDF):
        """Render a compact heatmap of all 18 tickers with key technicals."""
        if not self._live_cache:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(*_TEXT_MUTED)
            pdf.cell(0, 6, "No live data available for heatmap.", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(*_TEXT_DARK)
            return

        if pdf.get_y() > 200:
            pdf.add_page()

        col_widths = [15, 18, 14, 12, 14, 14, 14, 14, 12, 12, 14, 14, 23]
        headers = ["Tick", "Price", "Chg%", "RSI", "EMA9", "EMA20", "EMA50",
                    "EMA200", "ADX", "RVOL", "BB%B", "StK", "Signal"]

        pdf.set_font("Helvetica", "B", 5.5)
        pdf.set_fill_color(*_TABLE_HDR_BG)
        pdf.set_text_color(*_TEXT_WHITE)
        for i, hdr in enumerate(headers):
            pdf.cell(col_widths[i], 4.5, _safe(hdr), border=1, fill=True, align="C")
        pdf.ln()

        for idx, ticker in enumerate(ISA_PRIMARY_TICKERS):
            tech = self._live_cache.get(ticker)
            if tech is None:
                continue

            if pdf.get_y() > 272:
                pdf.add_page()
                pdf.set_font("Helvetica", "B", 5.5)
                pdf.set_fill_color(*_TABLE_HDR_BG)
                pdf.set_text_color(*_TEXT_WHITE)
                for i, hdr in enumerate(headers):
                    pdf.cell(col_widths[i], 4.5, _safe(hdr), border=1, fill=True, align="C")
                pdf.ln()

            _rc = getattr(self, '_market_data', {}).get("regime_confidence", 100.0)
            signal = _classify_signal(tech, regime_confidence=_rc)
            classification = signal["classification"]

            # Row colour based on signal
            if "LONG" in classification:
                row_bg = _GREEN_FAINT
            elif "SHORT" in classification:
                row_bg = _RED_FAINT
            else:
                row_bg = _BLUE_FAINT

            if idx % 2 == 1:
                row_bg = _ROW_WHITE

            pdf.set_fill_color(*row_bg)
            pdf.set_font("Helvetica", "B", 5.5)
            pdf.set_text_color(*_TEXT_DARK)

            # Ticker
            pdf.cell(col_widths[0], 4, _safe(ticker), border=1, fill=True, align="C")

            # Price
            chg = tech["change_pct"]
            price_color = _BRAND_GREEN if chg > 0 else _BRAND_RED if chg < 0 else _TEXT_DARK
            pdf.set_text_color(*price_color)
            pdf.set_font("Helvetica", "", 5.5)
            pdf.cell(col_widths[1], 4, _safe(f"${tech['price']:.2f}"), border=1, fill=True, align="C")

            # Change%
            pdf.cell(col_widths[2], 4, _safe(f"{chg:+.1f}%"), border=1, fill=True, align="C")
            pdf.set_text_color(*_TEXT_DARK)

            # RSI
            rsi = tech["rsi"]
            if rsi > 70:
                pdf.set_text_color(*_BRAND_RED)
            elif rsi < 30:
                pdf.set_text_color(*_BRAND_GREEN)
            pdf.cell(col_widths[3], 4, _safe(f"{rsi:.0f}"), border=1, fill=True, align="C")
            pdf.set_text_color(*_TEXT_DARK)

            # EMAs
            pdf.cell(col_widths[4], 4, _safe(f"{tech['ema9']:.0f}"), border=1, fill=True, align="C")
            pdf.cell(col_widths[5], 4, _safe(f"{tech['ema20']:.0f}"), border=1, fill=True, align="C")
            pdf.cell(col_widths[6], 4, _safe(f"{tech['ema50']:.0f}"), border=1, fill=True, align="C")
            pdf.cell(col_widths[7], 4, _safe(f"{tech['ema200']:.0f}"), border=1, fill=True, align="C")

            # ADX
            adx = tech["adx"]
            if adx > 25:
                pdf.set_font("Helvetica", "B", 5.5)
            pdf.cell(col_widths[8], 4, _safe(f"{adx:.0f}"), border=1, fill=True, align="C")
            pdf.set_font("Helvetica", "", 5.5)

            # RVOL
            rvol = tech["rvol"]
            if rvol >= 2.0:
                pdf.set_text_color(*_BRAND_GREEN)
            elif rvol >= 1.5:
                pdf.set_text_color(*_BRAND_AMBER)
            pdf.cell(col_widths[9], 4, _safe(f"{rvol:.1f}"), border=1, fill=True, align="C")
            pdf.set_text_color(*_TEXT_DARK)

            # BB%B
            bb = tech["bb_pct_b"]
            pdf.cell(col_widths[10], 4, _safe(f"{bb:.2f}"), border=1, fill=True, align="C")

            # Stoch K
            stk = tech["stoch_rsi_k"]
            pdf.cell(col_widths[11], 4, _safe(f"{stk:.0f}"), border=1, fill=True, align="C")

            # Signal classification (colour-coded)
            if "STRONG LONG" in classification:
                sig_color = _BRAND_GREEN
            elif "LONG" in classification:
                sig_color = (0, 160, 100)
            elif "STRONG SHORT" in classification:
                sig_color = _BRAND_RED
            elif "SHORT" in classification:
                sig_color = (180, 60, 60)
            else:
                sig_color = _BRAND_AMBER

            pdf.set_text_color(*sig_color)
            pdf.set_font("Helvetica", "B", 5.5)
            short_class = classification.replace("STRONG ", "S.")
            pdf.cell(col_widths[12], 4, _safe(short_class), border=1, fill=True, align="C")

            pdf.set_text_color(*_TEXT_DARK)
            pdf.set_font("Helvetica", "", 5.5)
            pdf.ln()

        pdf.set_text_color(*_TEXT_DARK)
        pdf.ln(2)

    def _render_learning_insights(self, pdf: _IntelPDF, data: dict):
        """Render Section 6: Self-Learning Insights."""
        pdf.set_text_color(*_TEXT_DARK)

        # Top performing strategy
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(0, 5, "Top Strategy (Current Regime, 30d):", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_x(14)
        pdf.cell(0, 5, _safe(data.get("top_strategy_current_regime", "N/A")),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        # Strategy leaderboard
        regime_top = data.get("regime_matrix_top", [])
        if regime_top:
            if pdf.get_y() > 250:
                pdf.add_page()
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(0, 5, "Strategy Leaderboard (30d):", new_x="LMARGIN", new_y="NEXT")

            strat_cols = [20, 40, 18, 18, 25]
            pdf.set_font("Helvetica", "B", 6.5)
            pdf.set_fill_color(*_TABLE_HDR_BG)
            pdf.set_text_color(*_TEXT_WHITE)
            for i, hdr in enumerate(["Strategy", "Name", "Trades", "WR %", "P&L"]):
                pdf.cell(strat_cols[i], 4.5, hdr, border=1, fill=True, align="C")
            pdf.ln()

            pdf.set_font("Helvetica", "", 6.5)
            for idx, s in enumerate(regime_top):
                row_bg = _ROW_LIGHT if idx % 2 == 0 else _ROW_WHITE
                pdf.set_fill_color(*row_bg)
                pdf.set_text_color(*_TEXT_DARK)
                pdf.cell(strat_cols[0], 4.5, _safe(s["strategy"]), border=1, fill=True, align="C")
                name = s.get("name", "")
                if len(name) > 22:
                    name = name[:21] + ".."
                pdf.cell(strat_cols[1], 4.5, _safe(name), border=1, fill=True, align="C")
                pdf.cell(strat_cols[2], 4.5, _safe(str(s.get("trades", 0))), border=1, fill=True, align="C")
                pdf.cell(strat_cols[3], 4.5, _safe(f"{s.get('wr', 0):.0f}%"), border=1, fill=True, align="C")
                pnl = s.get("pnl", 0)
                pnl_color = _BRAND_GREEN if pnl > 0 else _BRAND_RED if pnl < 0 else _TEXT_DARK
                pdf.set_text_color(*pnl_color)
                pdf.cell(strat_cols[4], 4.5, _safe(f"${pnl:+.0f}"), border=1, fill=True, align="C")
                pdf.ln()
            pdf.set_text_color(*_TEXT_DARK)
            pdf.ln(2)

        # Ticker leaderboard
        ticker_lb = data.get("ticker_leaderboard", [])
        if ticker_lb:
            if pdf.get_y() > 245:
                pdf.add_page()
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(0, 5, "Ticker Priority Leaderboard:", new_x="LMARGIN", new_y="NEXT")

            tl_cols = [22, 25, 22, 30]
            pdf.set_font("Helvetica", "B", 6.5)
            pdf.set_fill_color(*_TABLE_HDR_BG)
            pdf.set_text_color(*_TEXT_WHITE)
            for i, hdr in enumerate(["Ticker", "Priority", "WR 60d", "Best Strategy"]):
                pdf.cell(tl_cols[i], 4.5, hdr, border=1, fill=True, align="C")
            pdf.ln()

            pdf.set_font("Helvetica", "", 6.5)
            for idx, t in enumerate(ticker_lb[:8]):
                row_bg = _ROW_LIGHT if idx % 2 == 0 else _ROW_WHITE
                pdf.set_fill_color(*row_bg)
                pdf.set_text_color(*_TEXT_DARK)
                pdf.cell(tl_cols[0], 4.5, _safe(t["ticker"]), border=1, fill=True, align="C")
                pdf.cell(tl_cols[1], 4.5, _safe(f"{t.get('priority', 0):.2f}"), border=1, fill=True, align="C")
                pdf.cell(tl_cols[2], 4.5, _safe(f"{t.get('wr_60d', 0):.0f}%"), border=1, fill=True, align="C")
                bs = t.get("best_strat", "N/A")
                if bs in STRATEGY_NAMES:
                    bs = f"{bs} {STRATEGY_NAMES[bs]}"
                if len(bs) > 20:
                    bs = bs[:19] + ".."
                pdf.cell(tl_cols[3], 4.5, _safe(bs), border=1, fill=True, align="C")
                pdf.ln()
            pdf.set_text_color(*_TEXT_DARK)
            pdf.ln(2)

        # Strong repeaters
        repeaters = data.get("strong_repeaters", [])
        if repeaters:
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(0, 5, "Strong Repeaters (3+ wins in last 10):", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 7.5)
            for rep in repeaters[:5]:
                pdf.set_x(14)
                pdf.cell(
                    0, 4,
                    _safe(f"{rep['ticker']}: {rep['wins_last_10']}/{rep['trades_last_10']} wins, "
                          f"Avg R: {rep['avg_r']:+.2f}"),
                    new_x="LMARGIN", new_y="NEXT",
                )
            pdf.ln(2)

        # Patterns to watch
        patterns = data.get("patterns_to_watch", [])
        if patterns:
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(0, 5, "Patterns to Watch (from autopsies):", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 7.5)
            for pat in patterns[:5]:
                pdf.set_x(14)
                lesson = pat.get("pattern", "N/A")
                if len(lesson) > 90:
                    lesson = lesson[:89] + ".."
                pdf.cell(0, 4, _safe(f"- {lesson} (x{pat.get('occurrences', 0)})"),
                         new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

    def _render_risk_dashboard(self, pdf: _IntelPDF, data: dict):
        """Render Section 7: Risk Dashboard."""
        if pdf.get_y() > 220:
            pdf.add_page()

        pdf.set_text_color(*_TEXT_DARK)

        # Equity and P&L
        equity = data.get("equity", 10000)
        starting = data.get("starting_equity", 10000)
        total_return = ((equity - starting) / starting * 100) if starting > 0 else 0

        pdf.set_font("Helvetica", "B", 10)
        eq_color = _BRAND_GREEN if equity >= starting else _BRAND_RED
        pdf.set_text_color(*eq_color)
        pdf.cell(0, 6, _safe(f"Current Equity: ${equity:,.2f} ({total_return:+.1f}%)"),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_TEXT_DARK)

        pdf.set_font("Helvetica", "", 8.5)

        # Daily / Weekly P&L
        daily = data.get("daily_pnl", 0)
        weekly = data.get("weekly_pnl", 0)
        daily_pct = data.get("daily_pnl_pct", 0)
        weekly_pct = data.get("weekly_pnl_pct", 0)

        pdf.cell(95, 5, _safe(f"Daily P&L: ${daily:+.2f} ({daily_pct:+.2f}%)"),
                 new_x="RIGHT", new_y="LAST")
        pdf.cell(0, 5, _safe(f"Weekly P&L: ${weekly:+.2f} ({weekly_pct:+.2f}%)"),
                 new_x="LMARGIN", new_y="NEXT")

        # Drawdown and heat
        dd = data.get("current_drawdown_pct", 0)
        heat = data.get("portfolio_heat", 0)
        max_dd = data.get("max_drawdown_pct", 0)

        dd_color = _BRAND_GREEN if dd < 3 else _BRAND_AMBER if dd < 5 else _BRAND_RED
        pdf.set_text_color(*dd_color)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.cell(95, 5, _safe(f"Current Drawdown: {dd:.1f}% (max: {max_dd:.1f}%)"),
                 new_x="RIGHT", new_y="LAST")

        heat_color = _BRAND_GREEN if heat < 2 else _BRAND_AMBER if heat < 3 else _BRAND_RED
        pdf.set_text_color(*heat_color)
        pdf.cell(0, 5, _safe(f"Portfolio Heat: {heat:.1f}% / 3.0%"),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_TEXT_DARK)

        pdf.set_font("Helvetica", "", 8.5)
        pdf.cell(0, 5, _safe(f"Open Positions: {data.get('open_positions', 0)}"),
                 new_x="LMARGIN", new_y="NEXT")

        pdf.ln(3)

        # Go/No-Go status
        go_status = data.get("go_nogo", "NO-GO")
        go_detail = data.get("go_nogo_detail", {})

        pdf.set_font("Helvetica", "B", 9)
        if go_status == "GO":
            pdf.set_fill_color(*_BRAND_GREEN)
            pdf.set_text_color(*_TEXT_WHITE)
        else:
            pdf.set_fill_color(*_BRAND_RED)
            pdf.set_text_color(*_TEXT_WHITE)
        pdf.cell(50, 7, _safe(f"  Go/No-Go: {go_status}  "), fill=True,
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_TEXT_DARK)

        # Criteria detail
        criteria = go_detail.get("criteria", {})
        if criteria:
            pdf.set_font("Helvetica", "", 7.5)
            pdf.ln(1)
            for name, crit in criteria.items():
                passed = crit.get("pass", False)
                value = crit.get("value", "N/A")
                target = crit.get("target", "N/A")
                icon = "PASS" if passed else "FAIL"
                icon_color = _BRAND_GREEN if passed else _BRAND_RED

                pdf.set_x(14)
                pdf.set_text_color(*icon_color)
                pdf.set_font("Helvetica", "B", 7)
                pdf.cell(12, 4, _safe(f"[{icon}]"), new_x="RIGHT", new_y="LAST")
                pdf.set_text_color(*_TEXT_DARK)
                pdf.set_font("Helvetica", "", 7)
                label = name.replace("_", " ").title()
                pdf.cell(0, 4, _safe(f" {label}: {value} (target: {target})"),
                         new_x="LMARGIN", new_y="NEXT")

        pdf.ln(3)

        # Kelly sizing
        kelly = data.get("kelly_recommendation", 0.0075)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.cell(0, 5, _safe(f"Kelly Sizing Recommendation: {kelly * 100:.2f}% risk per trade"),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*_TEXT_MUTED)
        pdf.cell(0, 4, _safe(f"(Half-Kelly, capped at 0.75% immutable maximum)"),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_TEXT_DARK)

        pdf.ln(2)

    # ------------------------------------------------------------------
    # Telegram delivery
    # ------------------------------------------------------------------

    async def _send_via_telegram(self, pdf_path: str) -> bool:
        """Send the PDF via Telegram using the existing bot."""
        if not os.path.exists(pdf_path):
            logger.error("PDF file not found: %s", pdf_path)
            return False

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        if not token or not chat_id:
            logger.warning("Telegram not configured. PDF at: %s", pdf_path)
            return False

        try:
            from telegram import Bot
            bot = Bot(token=token)
            filename = Path(pdf_path).name
            with open(pdf_path, "rb") as f:
                await bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=filename,
                    caption=f"NZT-48 Intelligence Brief: {filename}",
                )
            logger.info("PDF sent to Telegram: %s", filename)
            return True
        except ImportError:
            logger.warning("python-telegram-bot not installed. PDF at: %s", pdf_path)
            return False
        except Exception as e:
            logger.error("Failed to send PDF via Telegram: %s", e)
            return False

    # ------------------------------------------------------------------
    # ETP metadata helper
    # ------------------------------------------------------------------

    def _build_etp_meta(self) -> dict[str, dict]:
        """Build a lookup of ETP ticker -> metadata from config."""
        meta: dict[str, dict] = {}

        for section_key in ("long_3x", "inverse_3x"):
            items = cfg.get(f"bot_a_universe.{section_key}", []) or []
            for item in items:
                if isinstance(item, dict):
                    t = item.get("ticker", "")
                    meta[t] = {
                        "provider": item.get("provider", ""),
                        "index": item.get("index", ""),
                        "underlying": item.get("index", ""),
                        "leverage": "3x",
                        "direction": "LONG" if "inverse" not in section_key else "SHORT",
                    }

        lev_items = cfg.get("bot_a_universe.leveraged_4x_5x", []) or []
        for item in lev_items:
            if isinstance(item, dict):
                t = item.get("ticker", "")
                meta[t] = {
                    "leverage": item.get("leverage", "3x"),
                    "underlying": item.get("underlying", ""),
                    "direction": item.get("direction", "LONG"),
                    "provider": item.get("provider", "Leverage Shares"),
                }

        return meta


# ---------------------------------------------------------------------------
# Standalone execution for testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    report = PDFIntelligenceReport()

    # Generate both reports
    lse_path = report.generate_pre_lse_report()
    print(f"Pre-LSE PDF: {lse_path}")

    nyse_path = report.generate_pre_nyse_report()
    print(f"Pre-NYSE PDF: {nyse_path}")

    # Optionally send via Telegram
    # asyncio.run(report.send_via_telegram(lse_path))
