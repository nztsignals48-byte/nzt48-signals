"""
delivery/mega_report.py
========================
NZT-48 MEGA PDF — Full Project Intelligence Report (40-80 pages)
Fires at 22:30 UK (30 min after EOD Review PDF).

SECTIONS:
  1.  Cover + Table of Contents
  2.  Executive Brief (today's key numbers at a glance)
  3.  System Architecture Overview
  4.  Signal Engine: Module-by-Module
  5.  Data Quality Audit (all tickers, health scores, RVOL reliability)
  6.  Gate Funnel Analysis (today's full funnel stats)
  7.  Strategy Design & Stop/Target Logic
  8.  Scoring Explainability (PlayScore breakdown per signal)
  9.  All Three Sessions: Ranked Play Archive (PRE_LSE + PRE_NYSE + EOD)
 10.  Command Center Status
 11.  Factor Concentration & Regime Intelligence
 12.  Performance Calibration & 2% Compounding Law
 13.  Testing, Deployment & Operational Status
 14.  Roadmap & Next Enhancements

Output: data/reports/mega_YYYYMMDD_HHMMSS.pdf  (~40-80 pages)
Telegram: sent as document (not photo) via send_document()
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf
from fpdf import FPDF

from uk_isa.isa_universe import (
    CORE_UNIVERSE as _ISA_CORE,
    EXTENDED_UNIVERSE as _ISA_EXTENDED,
    ALL_UNIVERSE,
    SECTOR_RADAR_UNIVERSE,
    FULL_SCAN_UNIVERSE,
    LEVERAGE_MAP as _LEVERAGE_MAP,
    ISA_FACTOR_GROUPS as _ISA_FACTOR_GROUPS_NESTED,
    TICKER_NAMES as _TICKER_NAMES,
    get_factor_group,
)
# Build flat ticker → group mapping from nested ISA_FACTOR_GROUPS
_FACTOR_GROUPS: dict[str, str] = {
    ticker: group
    for group, tickers in _ISA_FACTOR_GROUPS_NESTED.items()
    for ticker in tickers
}
from delivery.pdf_shared import (
    RunManifest, render_manifest_strip,
    next_schedule_line,
    render_sector_rotation_table,
    render_near_miss_table,
    render_sector_inflow_alerts,
)

# Project root
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("nzt48.mega_report")

# ---------------------------------------------------------------------------
# Constants / colour palette
# ---------------------------------------------------------------------------

C_DARK_BLUE   = (26,  39,  68)
C_MID_BLUE    = (35,  55, 100)
C_DEEP_NAVY   = (10,  18,  40)
C_GOLD        = (201, 168,  76)
C_WHITE       = (255, 255, 255)
C_GREEN       = (0,  160,  80)
C_RED         = (200,  30,  30)
C_AMBER       = (220, 150,   0)
C_GREY        = (130, 130, 130)
C_LIGHT_GREY  = (220, 220, 225)
C_LIGHT_BLUE  = (200, 215, 245)
C_NEAR_WHITE  = (245, 247, 252)
C_SILVER      = (180, 185, 200)

_SECTION_TITLES = [
    "1. Executive Brief",
    "2. System Architecture",
    "3. Signal Engine: Gate Funnel",
    "4. Data Quality Audit",
    "5. Strategy Design & Stop/Target Logic",
    "6. Scoring Explainability",
    "7. Session Play Archive (All 3 Sessions)",
    "8. Command Center Status",
    "9. Factor Concentration & Regime Intelligence",
    "10. Performance Calibration & 2% Compounding Law",
    "11. Operational Status & Deployment",
    "12. Roadmap",
    # v3.0 new sections
    "13. Strategy Router Chapter",
    "14. Session Status & Artifact Inventory",
    "15. Calibration Tables (Ready for Data)",
    "16. Repo Inventory & File Catalogue",
    "17. Scoring Sensitivity Analysis",
    "18b. Master Spec v8.0 Alignment Audit",
    "18. Deep Diagnostic (gate-level per ticker)",
    "19. Command Center API Contract Reference",
    "20. Configuration Reference (settings.yaml)",
    # v4.0 new sections
    "21. Risk Governance (RiskOfficer Rules & Decisions)",
    "22. Strategy Capital Allocation (Regime-Tilted Weights)",
    "23. Command Center 2.0 War Room Documentation",
    "24. Evidence Tables (Signal Quality & Execution Analysis)",
    # v5.0 new sections
    "25. Sector Rotation Analysis",
    "26. Gate Funnel -- Near Miss Analysis",
]


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _ascii(s: str, n: int = 0) -> str:
    """Replace non-latin-1 characters and optionally truncate."""
    _MAP = {
        "\u2192": "->", "\u2190": "<-", "\u25b2": "^",  "\u25bc": "v",
        "\u2014": "--", "\u2013": "-",  "\u00d7": "x",  "\u2265": ">=",
        "\u2264": "<=", "\u2713": "OK", "\u2717": "X",  "\u2500": "-",
        "\u2502": "|",  "\u2022": "*",  "\u2019": "'",  "\u201c": '"',
        "\u201d": '"',  "\u00b0": "deg","\u00b1": "+/-","\u2248": "~",
        "\u03b1": "a",  "\u03b2": "b",  "\u00b2": "2",  "\u2080": "0",
        "\u2082": "2",  "\u00e9": "e",  "\u00e8": "e",  "\u00ea": "e",
        "\u00e0": "a",  "\u00e2": "a",  "\u00f4": "o",  "\u00fb": "u",
        "\u00e7": "c",  "\u00fc": "u",  "\u00f6": "o",  "\u00e4": "a",
    }
    r = []
    for ch in str(s):
        if ch in _MAP:
            r.append(_MAP[ch])
        elif ord(ch) <= 255:
            r.append(ch)
        else:
            r.append("?")
    result = "".join(r)
    return result[:n - 1] + "." if n and len(result) > n else result


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def _chg_color(v: float) -> tuple:
    if v > 0:
        return C_GREEN
    if v < 0:
        return C_RED
    return C_GREY


# ---------------------------------------------------------------------------
# FPDF subclass with helpers
# ---------------------------------------------------------------------------

class MegaPDF(FPDF):
    """Extended FPDF for the Mega Report."""

    def __init__(self, report_date: str, version: str = "v8.0"):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.report_date = report_date
        self.version = version
        self.set_margins(10, 12, 10)
        self.set_auto_page_break(auto=True, margin=12)
        self._toc_entries: list[tuple[int, str]] = []   # (page, title)

    # Override cell to auto-sanitise all text (latin-1 safe)
    def cell(self, w=0, h=None, txt="", border=0, ln=False, align="", fill=False, link=""):
        return super().cell(w=w, h=h, txt=_ascii(str(txt)), border=border, ln=ln,
                            align=align, fill=fill, link=link)

    def header(self):
        if self.page_no() == 1:
            return
        self.set_fill_color(*C_DEEP_NAVY)
        self.rect(0, 0, 210, 8, "F")
        self.set_font("Helvetica", "B", 6)
        self.set_text_color(*C_GOLD)
        self.set_xy(4, 1.5)
        self.cell(0, 4, f"NZT-48 INSTITUTIONAL MEGA REPORT  |  {self.report_date}  |  {self.version}  |  CONFIDENTIAL", ln=False)
        self.set_xy(150, 1.5)
        self.set_font("Helvetica", "", 6)
        self.cell(50, 4, f"Page {self.page_no()}", align="R")

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-8)
        self.set_draw_color(*C_GOLD)
        self.line(10, self.get_y(), 200, self.get_y())
        self.set_font("Helvetica", "I", 5.5)
        self.set_text_color(*C_GREY)
        self.cell(0, 4, "NZT-48 Signal Intelligence System — Confidential — Not Investment Advice", align="C")

    # ------------------------------------------------------------------ #
    # Section title bar
    # ------------------------------------------------------------------ #
    def section_header(self, title: str, sub: str = "") -> None:
        """Full-width dark blue header bar for a section."""
        y = self.get_y()
        self._toc_entries.append((self.page_no(), title))
        self.set_fill_color(*C_DARK_BLUE)
        self.rect(4, y, 202, 10, "F")
        self.set_xy(6, y + 1.5)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*C_GOLD)
        self.cell(150, 7, _ascii(title))
        if sub:
            self.set_font("Helvetica", "", 7)
            self.set_text_color(*C_SILVER)
            self.cell(0, 7, _ascii(sub), align="R")
        self.ln(12)

    # ------------------------------------------------------------------ #
    # Sub-header (teal/blue bar)
    # ------------------------------------------------------------------ #
    def sub_header(self, title: str) -> None:
        y = self.get_y()
        self.set_fill_color(*C_MID_BLUE)
        self.rect(4, y, 202, 6, "F")
        self.set_xy(6, y + 1)
        self.set_font("Helvetica", "B", 7.5)
        self.set_text_color(*C_WHITE)
        self.cell(0, 4, _ascii(title))
        self.ln(8)

    # ------------------------------------------------------------------ #
    # KV row (label : value pairs in two columns)
    # ------------------------------------------------------------------ #
    def kv_row(self, pairs: list[tuple[str, str, tuple]], row_h: float = 5.5) -> None:
        """Render label-value pairs in columns. pairs = [(label, value, value_color), ...]"""
        y = self.get_y()
        x = 10
        col_w = 192 // max(len(pairs), 1)
        for label, value, color in pairs:
            self.set_xy(x, y)
            self.set_font("Helvetica", "", 7)
            self.set_text_color(*C_GREY)
            self.cell(col_w // 2, row_h, _ascii(label) + ":", align="R")
            self.set_font("Helvetica", "B", 7)
            self.set_text_color(*color)
            self.cell(col_w // 2, row_h, _ascii(str(value)))
            x += col_w
        self.ln(row_h)

    # ------------------------------------------------------------------ #
    # Body text
    # ------------------------------------------------------------------ #
    def body_text(self, text: str, size: float = 7.5, indent: int = 0) -> None:
        self.set_font("Helvetica", "", size)
        self.set_text_color(*C_DARK_BLUE)
        self.set_x(10 + indent)
        FPDF.multi_cell(self, 190 - indent, 4.5, _ascii(text))
        self.ln(1)

    # ------------------------------------------------------------------ #
    # Horizontal rule
    # ------------------------------------------------------------------ #
    def hr(self, color: tuple = C_LIGHT_GREY) -> None:
        y = self.get_y()
        self.set_draw_color(*color)
        self.line(10, y, 200, y)
        self.ln(2)

    # ------------------------------------------------------------------ #
    # Small label-value inline
    # ------------------------------------------------------------------ #
    def inline_stat(self, label: str, value: str, color: tuple = C_DARK_BLUE,
                    x: float = None, y: float = None) -> None:
        if x is not None:
            self.set_x(x)
        if y is not None:
            self.set_y(y)
        self.set_font("Helvetica", "", 6.5)
        self.set_text_color(*C_GREY)
        self.cell(25, 4.5, _ascii(label) + ":")
        self.set_font("Helvetica", "B", 6.5)
        self.set_text_color(*color)
        self.cell(20, 4.5, _ascii(str(value)))

    # ------------------------------------------------------------------ #
    # Coloured info box
    # ------------------------------------------------------------------ #
    def info_box(self, title: str, lines: list[str],
                 bg: tuple = C_NEAR_WHITE, title_color: tuple = C_DARK_BLUE,
                 text_color: tuple = C_DARK_BLUE, width: float = 202) -> None:
        y = self.get_y()
        box_h = 7 + len(lines) * 4.5
        self.set_fill_color(*bg)
        self.rect(4, y, width, box_h, "F")
        self.set_draw_color(*C_LIGHT_GREY)
        self.rect(4, y, width, box_h, "D")
        self.set_xy(6, y + 1.5)
        self.set_font("Helvetica", "B", 7.5)
        self.set_text_color(*title_color)
        self.cell(width - 4, 5, _ascii(title))
        self.set_xy(6, y + 7)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*text_color)
        for line in lines:
            self.cell(width - 4, 4.5, _ascii(line))
            self.ln(4.5)
        self.ln(2)

    # ------------------------------------------------------------------ #
    # Two-column table
    # ------------------------------------------------------------------ #
    def two_col_table(self, headers: list[str], rows: list[list],
                      col_widths: list[float] = None,
                      row_colors: list[tuple] = None) -> None:
        """Generic table renderer with alternating rows."""
        total_w = 202
        n_cols = len(headers)
        if col_widths is None:
            col_widths = [total_w / n_cols] * n_cols

        # Header
        y = self.get_y()
        self.set_fill_color(*C_DARK_BLUE)
        self.rect(4, y, total_w, 5.5, "F")
        x = 4
        self.set_font("Helvetica", "B", 6.5)
        self.set_text_color(*C_WHITE)
        for i, (hdr, w) in enumerate(zip(headers, col_widths)):
            self.set_xy(x, y + 0.8)
            self.cell(w, 4, _ascii(str(hdr), int(w // 2)), align="C")
            x += w
        y += 5.5

        # Data rows
        for ri, row in enumerate(rows):
            rh = 5.0
            bg = C_NEAR_WHITE if ri % 2 == 0 else C_LIGHT_BLUE
            self.set_fill_color(*bg)
            self.rect(4, y, total_w, rh, "F")
            x = 4
            self.set_font("Helvetica", "", 6.5)
            for ci, (val, w) in enumerate(zip(row, col_widths)):
                self.set_xy(x, y + 0.8)
                if row_colors and ri < len(row_colors) and row_colors[ri]:
                    self.set_text_color(*row_colors[ri])
                else:
                    self.set_text_color(*C_DARK_BLUE)
                align = "L" if ci == 0 else "C"
                self.cell(w, 3.5, _ascii(str(val), int(w // 1.5)), align=align)
                x += w
            y += rh

            if y > 275:
                self.add_page()
                y = 20

        self.set_y(y + 2)


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _fetch_market_snapshot() -> dict:
    """Fetch overnight/day summary for key indices."""
    tickers = {
        "^GSPC":  "SPX 500",
        "^NDX":   "Nasdaq 100",
        "^GDAXI": "DAX 40",
        "^VIX":   "VIX",
        "DX-Y.NYB": "DXY",
    }
    result = {}
    for sym, name in tickers.items():
        try:
            df = yf.download(sym, period="5d", interval="1d",
                             auto_adjust=True, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if df.empty or len(df) < 2:
                continue
            close  = float(df["Close"].iloc[-1])
            prev   = float(df["Close"].iloc[-2])
            change = (close - prev) / prev * 100
            result[sym] = {
                "name": name,
                "close": round(close, 2),
                "change_pct": round(change, 2),
            }
        except Exception:
            pass
    return result


def _fetch_isa_universe_data() -> dict[str, dict]:
    """Fetch OHLCV + indicators for all ISA tickers."""
    out = {}
    for ticker in _ISA_EXTENDED:
        try:
            df = yf.download(ticker, period="10d", interval="1h",
                             auto_adjust=True, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if df.empty or len(df) < 10:
                out[ticker] = {"status": "NO_DATA", "n_bars": 0}
                continue
            close   = float(df["Close"].iloc[-1])
            hi_s    = df["High"].astype(float)
            lo_s    = df["Low"].astype(float)
            cl_s    = df["Close"].astype(float)
            vo_s    = df["Volume"].astype(float)

            # ATR
            tr = pd.concat([hi_s - lo_s,
                            (hi_s - cl_s.shift(1)).abs(),
                            (lo_s - cl_s.shift(1)).abs()], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else float(tr.mean())
            atr_pct = atr / close * 100 if close > 0 else 0.0

            # RSI (Wilder's smoothing)
            delta = cl_s.diff()
            gain  = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
            loss  = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
            rs    = gain / loss.replace(0, 1e-9)
            rsi   = float(100 - 100 / (1 + rs.iloc[-1]))

            # MACD
            ema12 = cl_s.ewm(span=12, adjust=False).mean()
            ema26 = cl_s.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            macd_hist = float((macd_line - signal_line).iloc[-1])

            # EMA alignment
            ema9  = float(cl_s.ewm(span=9,  adjust=False).mean().iloc[-1])
            ema20 = float(cl_s.ewm(span=20, adjust=False).mean().iloc[-1])
            ema50 = float(cl_s.ewm(span=50, adjust=False).mean().iloc[-1])
            ema_bull = close > ema9 > ema20 > ema50
            ema_bear = close < ema9 < ema20 < ema50
            ema_align = "BULL" if ema_bull else ("BEAR" if ema_bear else "MIXED")

            # RVOL
            rvol: Optional[float] = None
            if vo_s.sum() > 0 and len(vo_s) >= 5:
                avg_vol = float(vo_s.iloc[:-1].mean())
                last_vol = float(vo_s.iloc[-1])
                rvol = round(last_vol / avg_vol, 2) if avg_vol > 0 else None
            rvol_reliable = rvol is not None and rvol > 0

            # Day change
            if len(cl_s) >= 2:
                day_change = (close - float(cl_s.iloc[-2])) / float(cl_s.iloc[-2]) * 100
            else:
                day_change = 0.0

            # Data reliability score (simple heuristic)
            data_reliability = 1.0
            if len(df) < 14:
                data_reliability -= 0.2
            if not rvol_reliable:
                data_reliability -= 0.15
            if atr_pct < 0.5:
                data_reliability -= 0.1
            data_reliability = max(0.0, min(1.0, data_reliability))

            # Direction bias
            long_score = sum([rsi > 52, macd_hist > 0, ema_bull, close > ema20])
            bias = "LONG" if long_score >= 2 else "SHORT"

            out[ticker] = {
                "status": "OK",
                "n_bars": len(df),
                "close": round(close, 4),
                "atr_pct": round(atr_pct, 2),
                "rsi": round(rsi, 1),
                "macd_hist": round(macd_hist, 6),
                "ema_align": ema_align,
                "rvol": rvol,
                "rvol_reliable": rvol_reliable,
                "day_change": round(day_change, 2),
                "data_reliability": round(data_reliability, 2),
                "bias": bias,
                "factor_group": _FACTOR_GROUPS.get(ticker, "other"),
                "leverage": _LEVERAGE_MAP.get(ticker, 1),
            }
        except Exception as exc:
            out[ticker] = {"status": "ERROR", "n_bars": 0, "error": str(exc)[:60]}
    return out


def _load_session_plays(sessions: list[str], run_date: date = None) -> dict[str, Optional[dict]]:
    """Load plays.json artifacts for each session (today or yesterday)."""
    try:
        from signal_engine.signal_card import read_plays_artifact
        result = {}
        for sess in sessions:
            for delta in (0, 1):
                d = (run_date or date.today()) - timedelta(days=delta)
                data = read_plays_artifact(sess, run_date=d)
                if data:
                    result[sess] = data
                    break
            else:
                result[sess] = None
        return result
    except Exception as exc:
        logger.debug("_load_session_plays failed: %s", exc)
        return {s: None for s in sessions}


def _run_engine_fresh() -> Optional[object]:
    """Run signal engine fresh for Mega PDF (use extended universe)."""
    try:
        from signal_engine.engine import SignalEngine
        engine = SignalEngine(use_extended=True)
        return engine.run(
            session="MEGA_EOD",
            regime="NEUTRAL",
            n_plays_min=5,
            n_plays_max=20,
            period="10d",
            write_artifacts=False,
        )
    except Exception as exc:
        logger.warning("Fresh engine run for mega failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_cover(pdf: MegaPDF, report_date: str, run_time: str) -> None:
    """Full page decorative cover."""
    # Background
    pdf.set_fill_color(*C_DEEP_NAVY)
    pdf.rect(0, 0, 210, 297, "F")

    # Gold accent strip
    pdf.set_fill_color(*C_GOLD)
    pdf.rect(0, 70, 210, 2, "F")
    pdf.rect(0, 80, 210, 1, "F")

    # Title
    pdf.set_xy(0, 90)
    pdf.set_font("Helvetica", "B", 32)
    pdf.set_text_color(*C_GOLD)
    pdf.cell(210, 18, "NZT-48", align="C")
    pdf.ln(0)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*C_WHITE)
    pdf.cell(210, 10, "INSTITUTIONAL INTELLIGENCE REPORT", align="C")
    pdf.ln(12)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*C_SILVER)
    pdf.cell(210, 7, "MEGA PDF  /  FULL SYSTEM ANALYSIS  /  DAILY EDITION", align="C")

    # Separator
    pdf.set_xy(30, 140)
    pdf.set_draw_color(*C_GOLD)
    pdf.set_line_width(0.5)
    pdf.line(30, 140, 180, 140)
    pdf.set_line_width(0.2)

    # Metadata
    pdf.set_xy(0, 148)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*C_LIGHT_BLUE)
    pdf.cell(210, 7, f"Report Date:  {report_date}", align="C")
    pdf.ln(8)
    pdf.cell(210, 7, f"Generated:    {run_time} UTC", align="C")
    pdf.ln(8)
    pdf.set_text_color(*C_SILVER)
    pdf.cell(210, 7, "System Version: v8.0 Apex Predator Engine", align="C")
    pdf.ln(8)
    pdf.cell(210, 7, f"Mode: WIN_RATE  |  Universe: {len(_ISA_EXTENDED)} LSE Leveraged ETPs", align="C")

    # Bottom accent
    pdf.set_xy(0, 240)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(*C_AMBER)
    pdf.cell(210, 5, "UK ISA  /  Paper Mode  /  GBP  /  Start Equity: GBP 10,000", align="C")
    pdf.ln(8)
    pdf.set_text_color(*C_GREY)
    pdf.set_font("Helvetica", "I", 6.5)
    pdf.cell(210, 5, "CONFIDENTIAL — NOT INVESTMENT ADVICE — FOR EDUCATIONAL PURPOSES ONLY", align="C")

    pdf.set_fill_color(*C_GOLD)
    pdf.rect(0, 290, 210, 7, "F")
    pdf.set_xy(0, 291)
    pdf.set_font("Helvetica", "B", 5.5)
    pdf.set_text_color(*C_DARK_BLUE)
    pdf.cell(210, 5, "  NZT-48 Signal Intelligence System   |   Built for fund-manager-grade reliability", align="L")


def _render_toc(pdf: MegaPDF) -> None:
    """Render the table of contents placeholder (page 2)."""
    pdf.add_page()
    pdf.set_y(15)
    # Big gold header
    pdf.set_fill_color(*C_DARK_BLUE)
    pdf.rect(4, 12, 202, 14, "F")
    pdf.set_xy(6, 14)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*C_GOLD)
    pdf.cell(0, 10, "TABLE OF CONTENTS")
    pdf.ln(18)

    for i, title in enumerate(_SECTION_TITLES, 1):
        y = pdf.get_y()
        bg = C_NEAR_WHITE if i % 2 == 0 else C_LIGHT_BLUE
        pdf.set_fill_color(*bg)
        pdf.rect(4, y, 202, 6.5, "F")
        pdf.set_xy(6, y + 1.2)
        pdf.set_font("Helvetica", "B" if i <= 3 else "", 8)
        pdf.set_text_color(*C_DARK_BLUE)
        pdf.cell(0, 4.5, _ascii(title))
        pdf.ln(6.5)

    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 6.5)
    pdf.set_text_color(*C_GREY)
    pdf.cell(0, 5,
             "Note: page numbers are dynamically generated. Sections begin on the pages following this TOC.",
             ln=True)


def _render_executive_brief(pdf: MegaPDF, market_data: dict,
                             engine_result, run_date: str) -> None:
    pdf.add_page()
    pdf.section_header("SECTION 1: EXECUTIVE BRIEF", f"As of {run_date}")

    # Market overview
    pdf.sub_header("Global Market Snapshot")
    if market_data:
        rows = []
        for sym, info in market_data.items():
            chg = info.get("change_pct", 0.0)
            rows.append([
                info.get("name", sym),
                f"{info.get('close', 0.0):,.2f}",
                _pct(chg),
                "UP" if chg > 0 else ("DOWN" if chg < 0 else "FLAT"),
            ])
        pdf.two_col_table(
            ["Index/Symbol", "Last Close", "Day Change", "Direction"],
            rows,
            col_widths=[60, 45, 45, 52],
        )
    else:
        pdf.body_text("Market data unavailable at report generation time.")

    # Signal engine summary
    pdf.sub_header("Signal Engine Summary")
    if engine_result is not None:
        n_plays   = len(engine_result.plays)
        n_strict  = engine_result.strict_count
        n_fallback = engine_result.fallback_count
        regime    = engine_result.regime
        top3 = engine_result.plays[:3]

        pdf.kv_row([
            ("Total Signals", str(n_plays),   C_GREEN if n_plays > 0 else C_RED),
            ("Strict",        str(n_strict),   C_GREEN if n_strict > 0 else C_AMBER),
            ("Fallback",      str(n_fallback), C_AMBER if n_fallback > 0 else C_GREY),
            ("Regime",        regime,          C_GOLD),
        ])

        if engine_result.drought:
            pdf.info_box(
                "!!! SIGNAL DROUGHT DETECTED !!!",
                engine_result.drought.top_blockers[:5],
                bg=(250, 200, 200),
                title_color=C_RED,
            )
        elif top3:
            pdf.sub_header("Top 3 Plays Today")
            rows = []
            for i, ps in enumerate(top3, 1):
                rvol_str = f"{ps.rvol:.1f}x" if ps.rvol else "N/A"
                rows.append([
                    f"{i}",
                    ps.ticker,
                    ps.direction,
                    ps.stars_str,
                    f"{ps.composite:.0f}/100",
                    ps.label,
                    f"{ps.entry:.4f}",
                    f"{ps.stop:.4f}",
                    f"{ps.target1:.4f}",
                    f"{ps.rr_ratio:.2f}",
                    rvol_str,
                ])
            pdf.two_col_table(
                ["#", "Ticker", "Dir", "Stars", "Score", "Label", "Entry", "Stop", "T1", "R:R", "RVOL"],
                rows,
                col_widths=[8, 18, 12, 20, 18, 32, 18, 18, 18, 14, 18],
            )
    else:
        pdf.body_text("Engine result not available for executive summary.")

    # The 2% Law callout
    pdf.ln(2)
    pdf.info_box(
        "The 2% Daily Compounding Law",
        [
            "GBP 10,000 x (1.02)^252 = GBP 1,485,757 (14,757% annualised return)",
            "Strategy: find ONE stock per day capable of a 2% move. That's it.",
            f"The signal engine scores all {len(_ISA_EXTENDED)} tickers by volatility, momentum, and R:R",
            "to identify today's best 2% candidate. Compound daily. Never deviate.",
        ],
        bg=(230, 240, 220),
        title_color=(10, 90, 10),
    )


def _render_architecture(pdf: MegaPDF) -> None:
    pdf.add_page()
    pdf.section_header("SECTION 2: SYSTEM ARCHITECTURE OVERVIEW")

    pdf.sub_header("High-Level Architecture")
    pdf.body_text(
        "NZT-48 v8.0 Apex Predator Engine is a real-time signal intelligence system designed "
        "to identify high-probability intraday momentum trades on UK ISA leveraged ETPs. "
        "The system runs 24/7 on AWS EC2 inside a Docker container, managed by Supervisord. "
        "A FastAPI REST server exposes the Command Center dashboard on port 8765."
    )

    arch_rows = [
        ["signal_engine/engine.py",      "Two-layer strict+fallback signal pipeline. Core intelligence."],
        ["signal_engine/gates.py",        "Hard gate (DATA_HEALTH, MIN_BARS) + soft gate (RVOL, RR, momentum) definitions."],
        ["signal_engine/scoring.py",      "PlayScore 0-100 formula. Composite = 0.30xMomentum + 0.20xVol + 0.15xRegime + ..."],
        ["signal_engine/state_machine.py","SignalRecord lifecycle: CANDIDATE -> QUALIFIED -> SIGNAL -> EXPIRED."],
        ["signal_engine/signal_card.py",  "Canonical SignalCard dataclass. plays.json artifact writer/reader."],
        ["command_center/tick_loop.py",   "Async 30s/120s tick loop. Session detection. Regime detection via SPX/VIX proxy."],
        ["command_center/server.py",      "FastAPI REST + WebSocket + HTML auto-refresh dashboard at :8765."],
        ["command_center/state.py",       "Singleton shared state: all panels (market, health, funnel, portfolio)."],
        ["command_center/diff.py",        "TickDiff engine: what changed since last tick (new/dropped/upgraded plays)."],
        ["delivery/play_renderer.py",     "Shared FPDF2 ranked-plays table. Used by all 3 daily PDFs."],
        ["delivery/pdf_v2_momentum.py",   "PDF 1: Pre-LSE Brief (07:00 UK). Momentum & opportunity focus."],
        ["delivery/pdf_v2_risk.py",       "PDF 2: Pre-NYSE Brief (13:30 UK). Risk & structural focus."],
        ["delivery/pdf_v2_daily_review.py","PDF 3: EOD Review (22:00 UK). Full dual-session review."],
        ["delivery/mega_report.py",       "This file: Mega PDF (22:30 UK). Full 40-80 page project analysis."],
        ["main.py",                        "Orchestrator: APScheduler + tick loop + FastAPI startup."],
        ["uk_isa/data_health.py",          "DataHealthGate: OHLC validity, pence/pounds detection, NaN/Inf guard."],
        ["uk_isa/isa_universe.py",         "Universe registry: 12 core + 9 extended LSE leveraged ETPs."],
        ["artifacts/YYYY-MM-DD/{s}/plays.json", "Daily plays artifact. Written after each engine run."],
    ]
    pdf.two_col_table(
        ["File / Path", "Purpose"],
        arch_rows,
        col_widths=[72, 130],
    )

    pdf.sub_header("Session Windows (UK Time)")
    session_rows = [
        ["PRE_LSE",   "06:00 - 08:00",  "Tick 120s", "PDF1 fires 07:00"],
        ["LSE",       "08:00 - 16:30",  "Tick 30s",  "LSE main session"],
        ["PRE_NYSE",  "12:00 - 14:30",  "Tick 120s", "PDF2 fires 13:30"],
        ["OVERLAP",   "14:30 - 16:30",  "Tick 30s",  "Both LSE + NYSE open"],
        ["NYSE",      "14:30 - 21:00",  "Tick 30s",  "NYSE main session"],
        ["EOD",       "21:00 - 22:00",  "Tick 120s", "PDF3 fires 22:00; Mega fires 22:30"],
        ["OFF_HOURS", "22:00 - 06:00",  "Tick 120s", "No live data"],
    ]
    pdf.two_col_table(
        ["Session", "UK Window", "Tick Rate", "Notes"],
        session_rows,
        col_widths=[30, 35, 30, 107],
    )


def _render_gate_funnel(pdf: MegaPDF, engine_result) -> None:
    pdf.add_page()
    pdf.section_header("SECTION 3: SIGNAL ENGINE — GATE FUNNEL ANALYSIS")

    pdf.sub_header("Gate Definitions")
    gate_rows = [
        ["DATA_HEALTH",          "HARD",  "Never bypassed", "OHLC valid, no NaN/Inf, volume present, range sanity"],
        ["PRICE_SCALE",          "HARD",  "Never bypassed", ".L tickers > 5000 → likely pence-coded → FAIL"],
        ["MIN_BARS",             "HARD",  "Never bypassed", ">=14 bars of OHLCV required for ATR14/RSI14"],
        ["TRADABILITY",          "HARD",  "Never bypassed", "ATR% >= 1.0% (strict) / 0.60% (fallback step 4)"],
        ["VOLUME_LIQUIDITY",     "SOFT",  "Fallback step 1", "RVOL >= 0.80 (strict) / 0.55 (fallback)"],
        ["RR_RATIO",             "SOFT",  "Fallback step 2", "R:R >= 1.50 (strict) / 1.20 (fallback) net of costs"],
        ["MOMENTUM_ALIGNMENT",   "SOFT",  "Fallback step 3", "RSI+MACD+EMA composite >= 0.55 (strict) / 0.40"],
        ["REGIME_FIT",           "SOFT",  "Scored",          "Direction compatible with current regime"],
        ["FACTOR_CAP",           "SOFT",  "Counted",         "Max 3 signals per factor cluster"],
    ]
    pdf.two_col_table(
        ["Gate", "Type", "Fallback", "Threshold / Rule"],
        gate_rows,
        col_widths=[38, 16, 28, 120],
    )

    pdf.sub_header("Two-Layer Pipeline Logic")
    pdf.info_box(
        "Layer 1: STRICT",
        [
            "All gates must pass at institutional thresholds.",
            "If >= 5 signals produced: done. Signals labelled 'STRICT'.",
        ],
        bg=(220, 240, 220), title_color=C_GREEN,
    )
    pdf.info_box(
        "Layer 2: FALLBACK (auto-activated if strict < 5 signals)",
        [
            "Data Health gate: NEVER relaxed (hard constraint).",
            "Step 1: RVOL threshold relaxed 0.80 -> 0.55",
            "Step 2: R:R minimum relaxed 1.50 -> 1.20",
            "Step 3: Momentum threshold relaxed 0.55 -> 0.40",
            "Step 4: ATR% minimum relaxed 1.00% -> 0.60%  (last resort)",
            "If even fallback produces 0 signals: SIGNAL DROUGHT declared.",
        ],
        bg=(255, 245, 210), title_color=C_AMBER,
    )

    # Live funnel stats
    if engine_result is not None:
        pdf.sub_header("Live Funnel Stats (Latest Engine Run)")
        funnel = engine_result.gate_funnel
        pdf.kv_row([
            ("Tickers Tracked",   str(funnel.get("tracked", 0)),           C_DARK_BLUE),
            ("Data Valid",        str(funnel.get("data_valid", 0)),         C_GREEN),
            ("Strict Signals",    str(funnel.get("signals_strict", 0)),     C_GREEN),
            ("Fallback Signals",  str(funnel.get("signals_fallback", 0)),   C_AMBER),
            ("Total Signals",     str(funnel.get("total_signals", 0)),      C_GOLD),
        ])

        if engine_result.blocker_summary:
            pdf.sub_header("Top Gate Failure Reasons")
            for b in engine_result.blocker_summary[:6]:
                pdf.body_text("  * " + b, indent=4)


def _render_data_quality(pdf: MegaPDF, isa_data: dict[str, dict]) -> None:
    pdf.add_page()
    pdf.section_header("SECTION 4: DATA QUALITY AUDIT — FULL ISA UNIVERSE")

    pdf.sub_header("Per-Ticker Health Report (21 Tickers)")
    rows = []
    for ticker in _ISA_EXTENDED:
        info = isa_data.get(ticker, {"status": "MISSING"})
        status = info.get("status", "?")
        if status == "OK":
            reliability = info.get("data_reliability", 1.0)
            rvol = info.get("rvol")
            rvol_str = f"{rvol:.1f}x" if rvol else "N/A"
            rel_str = f"{reliability:.2f}"
            rows.append([
                ticker,
                str(info.get("leverage", "?")),
                _FACTOR_GROUPS.get(ticker, "other"),
                str(info.get("n_bars", 0)),
                f"{info.get('close', 0.0):.4f}",
                f"{info.get('atr_pct', 0.0):.2f}%",
                f"{info.get('rsi', 0.0):.0f}",
                info.get("ema_align", "?"),
                rvol_str,
                rel_str,
                info.get("bias", "?"),
                "OK",
            ])
        else:
            rows.append([
                ticker,
                str(_LEVERAGE_MAP.get(ticker, "?")),
                _FACTOR_GROUPS.get(ticker, "other"),
                "0", "-", "-", "-", "-", "-", "-", "-",
                status,
            ])

    pdf.two_col_table(
        ["Ticker", "Lev", "Group", "Bars", "Close", "ATR%", "RSI", "EMA", "RVOL", "Rely", "Bias", "Status"],
        rows,
        col_widths=[18, 10, 32, 12, 22, 14, 12, 14, 16, 14, 14, 16],
    )

    # Data reliability explanation
    pdf.sub_header("DataReliabilityScore Explained")
    pdf.info_box(
        "DataReliabilityScore (0.0 - 1.0)",
        [
            "1.00 = Full data, RVOL reliable, ATR% healthy, 14+ bars available",
            "-0.20 penalty: fewer than 14 bars returned by yfinance",
            "-0.15 penalty: RVOL unavailable (zero volume or single bar)",
            "-0.10 penalty: ATR% below 0.5% (instrument too quiet to trade)",
            "Score < 0.70: composite PlayScore is penalised by up to 15 points",
            "RVOL is shown as N/A (not 0.0) when volume data is unreliable",
        ],
        bg=C_NEAR_WHITE,
    )


def _render_strategy_design(pdf: MegaPDF) -> None:
    pdf.add_page()
    pdf.section_header("SECTION 5: STRATEGY DESIGN & STOP/TARGET LOGIC")

    pdf.sub_header("The Problem We Solved")
    pdf.info_box(
        "Old Design (broken):",
        [
            "Fixed 2% target + 1x ATR stop + R:R >= 1.5 = mathematically impossible on volatile 3x ETPs.",
            "A 3x ETP with 4% ATR needs a 4% stop and a 6% target for 1.5 R:R.",
            "Result: near-zero signals every day. System useless.",
        ],
        bg=(255, 220, 220), title_color=C_RED,
    )
    pdf.info_box(
        "New Design (fixed):",
        [
            "Stop = setup-type ATR fraction. Target scales with stop distance.",
            "R:R computed NET of round-trip cost (spread + slippage).",
            f"Result: realistic, achievable entries on all {len(_ISA_EXTENDED)} tickers every session.",
        ],
        bg=(220, 255, 220), title_color=C_GREEN,
    )

    pdf.sub_header("Stop / Target Fractions by Setup Type")
    stop_rows = [
        ["continuation", "0.40 x ATR", "max(1.2 x stop_dist, 0.60 x ATR)", "2.5 x stop_dist", "Trending, EMA aligned, ADX >= 25"],
        ["breakout",     "0.35 x ATR", "max(1.2 x stop_dist, 0.60 x ATR)", "2.5 x stop_dist", "BB width rank >= 0.80"],
        ["mean_revert",  "0.60 x ATR", "max(1.2 x stop_dist, 0.60 x ATR)", "2.5 x stop_dist", "RSI > 70 or < 30"],
        ["default",      "0.50 x ATR", "max(1.2 x stop_dist, 0.60 x ATR)", "2.5 x stop_dist", "All other setups"],
    ]
    pdf.two_col_table(
        ["Setup Type", "Stop", "Target 1", "Target 2 (Runner)", "Condition"],
        stop_rows,
        col_widths=[28, 25, 48, 38, 63],
    )

    pdf.sub_header("Cost Model (Round-Trip)")
    cost_rows = [
        ["QQQ3.L / 3LUS.L / QQQS.L / 3USS.L", "15 bps", "2 x 5 bps", "25 bps total"],
        ["3SEM.L / NVD3.L / TSL3.L / TSM3.L",  "18 bps", "2 x 5 bps", "28 bps total"],
        ["QQQ5.L / SP5L.L / GPT3.L",            "20 bps", "2 x 5 bps", "30 bps total"],
        ["MU2.L / others",                       "22 bps", "2 x 5 bps", "32 bps total"],
    ]
    pdf.two_col_table(
        ["Ticker Group", "Spread", "Slippage (2 sides)", "Total Round-Trip Cost"],
        cost_rows,
        col_widths=[80, 30, 50, 42],
    )

    pdf.sub_header("WIN_RATE Mode vs R_MULTIPLE Mode")
    pdf.body_text(
        "WIN_RATE (default): optimised for high win-rate plays. Requires "
        "RegimeConfidence >= 0.60. Uses two tracks:\n"
        "  SCALP track: breakout setups with ATR% >= 2.5%. Time stop: 12 min.\n"
        "  INTRADAY_SWING track: continuation / default setups. Time stop: 180 min.\n\n"
        "R_MULTIPLE mode: pure R:R optimisation. No regime confidence filter. "
        "Trades any setup that passes gates regardless of directional regime."
    )

    pdf.sub_header("WIN_RATE Mode: Time Stops & Break-Even")
    be_rows = [
        ["INTRADAY_SWING", "180 min full", "90 min check", "Entry + 0.6 x stop_dist", "Entry + 1.0 x stop_dist"],
        ["SCALP",          "12 min full",  "6 min check",  "Entry + 0.6 x stop_dist", "Entry + 1.0 x stop_dist"],
    ]
    pdf.two_col_table(
        ["Track", "Time Stop (full)", "Time Stop (check)", "Break-Even Level", "Partial Level"],
        be_rows,
        col_widths=[40, 32, 32, 52, 46],
    )


def _render_scoring_explainability(pdf: MegaPDF, engine_result) -> None:
    pdf.add_page()
    pdf.section_header("SECTION 6: SCORING EXPLAINABILITY")

    pdf.sub_header("PlayScore Formula (0-100)")
    formula_rows = [
        ["Momentum",           "0.30", "RSI (sigmoid around 55) + MACD hist direction + EMA 9/20/50 alignment"],
        ["Volatility Opp.",    "0.20", "ATR% score (3% = max) + BB width percentile rank"],
        ["Regime Fit",         "0.15", "RISK_ON=0.9 for LONG; RISK_OFF=0.9 for SHORT; NEUTRAL=0.55"],
        ["Liquidity",          "0.15", "RVOL normalised (3x RVOL = 1.0). N/A -> 0.55 neutral"],
        ["Risk/Reward",        "0.10", "Net R:R after spread+slippage. R:R=3 gives ~1.0"],
        ["Quality",            "0.10", "ADX trend strength (50 = max). ADX >= 25 -> good trend"],
        ["DataReliability",    "-", "Penalty: max(0, 1 - data_reliability) * 0.15 subtracted from raw"],
    ]
    pdf.two_col_table(
        ["Component", "Weight", "Calculation Detail"],
        formula_rows,
        col_widths=[38, 16, 148],
    )

    pdf.sub_header("Star Rating")
    star_rows = [
        ["[*****]", "90 - 100", "Institutional-grade: all components strong"],
        ["[****_]", "80 - 89",  "Strong: 4+ components aligned"],
        ["[***__]", "70 - 79",  "Good: 3 components aligned, one weak"],
        ["[**___]", "60 - 69",  "Watchable: mixed signals, one clear edge"],
        ["[*____]", "< 60",     "Fallback/watch only: do not enter without confirmation"],
    ]
    pdf.two_col_table(
        ["Stars", "Score Range", "Interpretation"],
        star_rows,
        col_widths=[22, 22, 158],
    )

    pdf.sub_header("Star Modifiers")
    mod_rows = [
        ["-1 star", "Factor cluster overloaded", "Same factor group already has >= 3 signals"],
        ["-1 star", "Decay risk HIGH in choppy",  "High leverage decay detected in CHOPPY regime"],
        ["-1 star", "Spread/liquidity risk HIGH", "Wide bid-ask or very thin volume"],
        ["+1 star", "Multi-source + regime align", "Multiple data sources agree + regime strongly favours direction"],
    ]
    pdf.two_col_table(
        ["Adjustment", "Trigger", "Condition"],
        mod_rows,
        col_widths=[22, 55, 125],
    )

    # Per-signal breakdown from live result
    if engine_result is not None and engine_result.plays:
        pdf.sub_header("Per-Signal Score Breakdown (Live Engine)")
        for i, ps in enumerate(engine_result.plays[:8]):
            rvol_str = f"{ps.rvol:.1f}x" if ps.rvol else "N/A"
            label = _ascii(ps.label, 28)
            pdf.kv_row([
                ("Ticker",     ps.ticker,                                   C_GOLD),
                ("Direction",  ps.direction,                                C_GREEN if ps.direction == "LONG" else C_RED),
                ("Stars",      ps.stars_str,                                C_GOLD),
                ("Score",      f"{ps.composite:.1f}/100",                   C_DARK_BLUE),
                ("Label",      label,                                       C_GREEN if "STRICT" in label else C_AMBER),
            ])
            pdf.kv_row([
                ("Momentum",   f"{ps.momentum:.2f}",    C_DARK_BLUE),
                ("Volatility", f"{ps.volatility:.2f}",  C_DARK_BLUE),
                ("Regime Fit", f"{ps.regime_fit:.2f}",  C_DARK_BLUE),
                ("Liquidity",  f"{ps.liquidity:.2f}",   C_DARK_BLUE),
                ("R:R Score",  f"{ps.rr_score:.2f}",    C_DARK_BLUE),
            ])
            if ps.reasons:
                reasons_str = " | ".join(ps.reasons[:3])
                pdf.body_text(f"  Reasons: {reasons_str}", size=6.5, indent=6)
            pdf.hr()


def _render_session_play_archive(pdf: MegaPDF, session_plays: dict[str, Optional[dict]],
                                 engine_result) -> None:
    pdf.add_page()
    pdf.section_header("SECTION 7: SESSION PLAY ARCHIVE — ALL 3 SESSIONS")

    sessions_meta = {
        "pre_lse":              ("PRE-LSE BRIEF (07:00 UK)",    "Overnight setup, LSE open candidates"),
        "pre_nyse":             ("PRE-NYSE BRIEF (13:30 UK)",   "LSE recap, US pre-market signals"),
        "eod_institutional":    ("EOD REVIEW (22:00 UK)",       "Full dual-session review"),
    }

    any_found = False
    for session_key, (session_title, session_desc) in sessions_meta.items():
        data = session_plays.get(session_key)
        pdf.sub_header(f"{session_title} — {session_desc}")
        if data is None:
            pdf.body_text(f"No artifact found for session '{session_key}' (today or yesterday).")
            continue

        any_found = True
        gen_at = data.get("generated_at", "unknown")
        regime = data.get("regime", "?")
        strict = data.get("strict_count", 0)
        fallback = data.get("fallback_count", 0)
        total = data.get("total_plays", 0)

        pdf.kv_row([
            ("Generated",   gen_at[:19],  C_GREY),
            ("Regime",      regime,       C_GOLD),
            ("Strict",      str(strict),  C_GREEN),
            ("Fallback",    str(fallback),C_AMBER),
            ("Total",       str(total),   C_DARK_BLUE),
        ])

        plays = data.get("plays", [])
        if not plays:
            if data.get("drought"):
                pdf.info_box(
                    "SIGNAL DROUGHT",
                    data.get("drought", ["No plays generated"])[:5],
                    bg=(255, 210, 210), title_color=C_RED,
                )
            else:
                pdf.body_text("  No plays in this session artifact.")
            continue

        rows = []
        for i, p in enumerate(plays[:12], 1):
            rvol_str = f"{p.get('rvol', 0.0) or 0.0:.1f}x" if p.get("rvol") else "N/A"
            rows.append([
                str(i),
                p.get("ticker", "?"),
                p.get("direction", "?"),
                p.get("stars_str", "[*____]"),
                f"{p.get('composite', 0.0):.0f}",
                _ascii(p.get("label", ""), 18),
                f"{p.get('entry', 0.0):.4f}",
                f"{p.get('stop', 0.0):.4f}",
                f"{p.get('target1', 0.0):.4f}",
                f"{p.get('rr_ratio', 0.0):.2f}",
                rvol_str,
                _ascii(p.get("track", ""), 14),
            ])
        pdf.two_col_table(
            ["#", "Ticker", "Dir", "Stars", "Score", "Label", "Entry", "Stop", "T1", "R:R", "RVOL", "Track"],
            rows,
            col_widths=[8, 18, 10, 20, 14, 28, 18, 18, 18, 12, 14, 22],
        )
        pdf.ln(2)

    if not any_found and engine_result is not None:
        pdf.sub_header("Live Engine Result (fallback — no session artifacts found)")
        from delivery.play_renderer import render_plays_table, render_drought_panel
        if engine_result.drought:
            render_drought_panel(pdf, engine_result.drought.to_text())
        elif engine_result.plays:
            render_plays_table(pdf, engine_result.plays, title="LIVE ENGINE PLAYS")


def _render_command_center(pdf: MegaPDF) -> None:
    pdf.add_page()
    pdf.section_header("SECTION 8: COMMAND CENTER STATUS")

    pdf.sub_header("Dashboard")
    pdf.info_box(
        "Command Center — FastAPI Dashboard",
        [
            "URL (EC2):  http://100.55.69.28:8765",
            "URL (local): http://localhost:8765",
            "WebSocket:   ws://localhost:8765/ws  (push every 30s)",
            "Auto-refresh: every 30 seconds, dark theme",
        ],
        bg=C_NEAR_WHITE,
    )

    pdf.sub_header("REST API Endpoints")
    api_rows = [
        ["GET /",           "HTML dashboard — auto-refresh"],
        ["GET /api/state",  "Full JSON snapshot (all panels: market, health, funnel, tape)"],
        ["GET /api/plays",  "Top plays ranked list + drought field if triggered"],
        ["GET /api/tape",   "Signal tape last 30 entries (SignalRecord JSON)"],
        ["GET /api/health", "Data health badge + per-ticker status + failed tickers list"],
        ["GET /api/funnel", "Gate funnel counts + top blocker reasons"],
        ["WS  /ws",         "WebSocket push every 30s — live JSON diff"],
    ]
    pdf.two_col_table(
        ["Endpoint", "Returns"],
        api_rows,
        col_widths=[50, 152],
    )

    pdf.sub_header("Tick Loop Behaviour")
    tick_rows = [
        ["Active sessions (LSE/NYSE/OVERLAP)",  "30 second tick"],
        ["Off-hours / pre-market sessions",     "120 second tick"],
        ["Regime detection",                    "SPX via ^GSPC + VIX via ^VIX proxy — 5d/1d data"],
        ["Telegram alert on drought",           "Immediately fires 'SIGNAL DROUGHT' message"],
        ["Telegram on new strict signal",       "Fires when new STRICT signal enters top-5"],
    ]
    pdf.two_col_table(
        ["Behaviour", "Detail"],
        tick_rows,
        col_widths=[80, 122],
    )

    pdf.sub_header("TickDiff: What Changed Since Last Tick")
    pdf.body_text(
        "The DiffEngine compares consecutive EngineResults and produces a TickDiff with:\n"
        "  new_plays: tickers that appeared this tick (new opportunities)\n"
        "  dropped_plays: tickers that fell off the ranked list\n"
        "  upgraded: tickers whose star rating increased\n"
        "  downgraded: tickers whose star rating decreased\n"
        "  regime_changed: True if regime label changed this tick\n"
        "  health_changed: True if any ticker's health status changed\n"
        "  drought_entered / drought_cleared: drought state transitions\n"
        "The WebSocket push sends the full diff as JSON every 30 seconds."
    )


def _render_factor_regime(pdf: MegaPDF, isa_data: dict[str, dict]) -> None:
    pdf.add_page()
    pdf.section_header("SECTION 9: FACTOR CONCENTRATION & REGIME INTELLIGENCE")

    pdf.sub_header("Factor Group Concentration (Today)")
    from collections import Counter
    group_counts: Counter = Counter()
    group_biases: dict[str, list[str]] = {}
    for ticker in _ISA_EXTENDED:
        info = isa_data.get(ticker, {})
        if info.get("status") == "OK":
            grp = _FACTOR_GROUPS.get(ticker, "other")
            group_counts[grp] += 1
            if grp not in group_biases:
                group_biases[grp] = []
            bias_marker = f"{ticker}({'L' if info.get('bias') == 'LONG' else 'S'})"
            group_biases[grp].append(bias_marker)

    conc_rows = []
    for grp in sorted(group_counts.keys()):
        cnt = group_counts[grp]
        members = ", ".join(group_biases.get(grp, []))
        cap_warn = " [CAPPED]" if cnt >= 3 else ""
        conc_rows.append([
            grp,
            str(cnt),
            members,
            "3" + cap_warn,
        ])
    pdf.two_col_table(
        ["Factor Group", "Active", "Members (L/S bias)", "Cap"],
        conc_rows,
        col_widths=[45, 16, 110, 31],
    )

    pdf.sub_header("Regime Classification Logic")
    pdf.info_box(
        "How Regime Is Detected",
        [
            "Proxy: SPX 5-day/1-day return via yfinance (^GSPC) + VIX level (^VIX).",
            "RISK_ON:  SPX 5d return > +0.5% AND VIX < 20",
            "RISK_OFF: SPX 5d return < -0.5% OR VIX > 30",
            "CHOPPY:   VIX 20-30 with flat SPX",
            "NEUTRAL:  default if insufficient data or intermediate readings",
            "RegimeConfidence: 0.0 - 1.0 based on signal clarity",
        ],
        bg=C_NEAR_WHITE,
    )

    pdf.sub_header("Leverage Decay Risk")
    decay_rows = [
        ["Choppy / sideways regime", "HIGH",   "3x ETPs lose value daily via volatility decay even at flat price"],
        ["Trending regime",          "MEDIUM", "Decay offset by directional move; manageable intraday"],
        ["Strong trending",          "LOW",    "Decay negligible vs intraday directional PnL"],
        ["5x ETPs (QQQ5/SP5L)",      "+1 HIGH", "Extra decay layer on top of 3x decay — double caution"],
    ]
    pdf.two_col_table(
        ["Condition", "Decay Risk", "Notes"],
        decay_rows,
        col_widths=[60, 22, 120],
    )


def _render_performance_calibration(pdf: MegaPDF) -> None:
    pdf.add_page()
    pdf.section_header("SECTION 10: PERFORMANCE CALIBRATION & 2% COMPOUNDING LAW")

    pdf.sub_header("The 2% Daily Target — Strategy S15")
    pdf.info_box(
        "Compound Daily at 2%",
        [
            "Formula: GBP 10,000 x (1.02)^252 = GBP 1,485,757",
            "That is a 14,757% annualised return from a single 2% daily edge.",
            "The edge: find ONE instrument per day capable of a 2% move.",
            f"S15 scores all {len(_ISA_EXTENDED)} tickers by '2% reachability' (ATR% vs 2% threshold).",
            "Best candidate wins the day. Stop = 1x ATR. Target = +2% exactly.",
            "This is the single most important strategy in the system.",
        ],
        bg=(230, 250, 230), title_color=(10, 100, 10),
    )

    pdf.sub_header("Compounding Projection Table")
    comp_rows = []
    equity = 10_000.0
    for day in [1, 5, 10, 21, 63, 126, 189, 252]:
        projected = equity * (1.02 ** day)
        comp_rows.append([
            str(day),
            f"GBP {projected:,.0f}",
            f"{(projected / equity - 1) * 100:.1f}%",
        ])
    pdf.two_col_table(
        ["Trading Days", "Projected Equity", "Total Return"],
        comp_rows,
        col_widths=[50, 76, 76],
    )

    pdf.sub_header("Calibration: What Win-Rate Do We Need?")
    wr_rows = [
        ["1.5 R:R", "50% win-rate", "Break-even at 50%: every win more than covers loss"],
        ["2.0 R:R", "40% win-rate", "Profitable from 40% win-rate — very achievable"],
        ["2.5 R:R", "35% win-rate", "Strong edge: 3-of-8 wins = positive expectancy"],
        ["1.2 R:R", "55% win-rate", "Fallback signals: need higher win-rate to be viable"],
    ]
    pdf.two_col_table(
        ["R:R Ratio", "Min Win-Rate for Profitability", "Notes"],
        wr_rows,
        col_widths=[30, 70, 102],
    )

    pdf.sub_header("Risk Management Rules")
    pdf.info_box(
        "Non-Negotiable Risk Rules",
        [
            "1. Never risk more than 2% of portfolio on a single trade.",
            "2. Stop = defined ATR fraction from engine. No manual override.",
            "3. Target = minimum 1.2x stop distance net of costs.",
            "4. Time stop: exit if not +0.3R within half the session window.",
            "5. Break-even: move stop to entry when price reaches +0.6R.",
            "6. Partial exit: take 50% off at T1 (+1R). Let runner go to T2.",
            "7. Factor cap: no more than 3 signals in same factor cluster.",
            "8. Data health gate: never trade a ticker that fails health check.",
        ],
        bg=(235, 235, 255),
        title_color=C_DARK_BLUE,
    )


def _render_operational_status(pdf: MegaPDF) -> None:
    pdf.add_page()
    pdf.section_header("SECTION 11: OPERATIONAL STATUS & DEPLOYMENT")

    pdf.sub_header("Infrastructure")
    infra_rows = [
        ["Platform",       "AWS EC2 (Ubuntu 22.04)"],
        ["Instance IP",    "100.55.69.28"],
        ["Container",      "Docker: nzt48 (main engine) + nzt48-dashboard (Next.js :3001)"],
        ["Process manager","Supervisord: manages 'api' + 'engine' processes"],
        ["Python version", "3.12"],
        ["PDF library",    "fpdf2 >= 2.8.0 (latin-1 safe, no external fonts needed)"],
        ["Data feed",      "yfinance >= 0.2.31 (free, Yahoo Finance)"],
        ["Dashboard port", ":8765 (FastAPI) — open in AWS Security Group"],
        ["Telegram",       "python-telegram-bot >= 20.0 — sends PDFs as documents"],
    ]
    pdf.two_col_table(
        ["Property", "Value"],
        infra_rows,
        col_widths=[45, 157],
    )

    pdf.sub_header("Common Operations")
    ops_rows = [
        ["Check logs",         "docker logs nzt48 --tail 50"],
        ["Restart engine",     "docker restart nzt48"],
        ["Run signal engine",  "docker exec nzt48 python3 -c \"from signal_engine.engine import SignalEngine; e=SignalEngine(use_extended=True); r=e.run('TEST','NEUTRAL'); print(len(r.plays),'plays')\""],
        ["Trigger PDF 1",      "docker exec nzt48 python3 -c \"import asyncio; from main import NZT48System; s=NZT48System(); asyncio.run(s._generate_v2_pdf1())\""],
        ["Trigger Mega PDF",   "docker exec nzt48 python3 -c \"from delivery.mega_report import MegaReport; r=MegaReport(); p=r.generate(); print(p)\""],
        ["View Command Center","http://100.55.69.28:8765"],
    ]
    pdf.two_col_table(
        ["Action", "Command / URL"],
        ops_rows,
        col_widths=[40, 162],
    )

    pdf.sub_header("Signal Lifecycle (state_machine.py)")
    lifecycle_rows = [
        ["CANDIDATE",    "Ticker entered universe scan — no gates checked yet"],
        ["QUALIFIED",    "All gates passed — engine emits to SignalTape"],
        ["SIGNAL",       "Top-ranked play confirmed by engine — ready for trade"],
        ["ORDER_INTENT", "User/bot has acknowledged play — order being prepared"],
        ["EXPIRED",      "Time stop reached or session ended with no action taken"],
        ["INVALIDATED",  "Health gate failed mid-session — signal cancelled immediately"],
    ]
    pdf.two_col_table(
        ["State", "Meaning"],
        lifecycle_rows,
        col_widths=[30, 172],
    )


def _render_roadmap(pdf: MegaPDF) -> None:
    pdf.add_page()
    pdf.section_header("SECTION 12: ROADMAP & NEXT ENHANCEMENTS")

    pdf.sub_header("Sprint 2 — In Progress")
    s2_rows = [
        ["LightGBM signal classifier",   "Train on historical OHLCV + indicator features. Replace rule-based scoring."],
        ["HMM regime detection",         "Hidden Markov Model (hmmlearn) for robust regime classification."],
        ["Kalman filter for price",      "Smoother price series for cleaner indicator calculations."],
        ["Portfolio risk module",        "riskfolio-lib integration for position sizing + portfolio VaR."],
        ["Google Sheets logging",        "Live trade log pushed to Sheets for easy review on mobile."],
        ["Backtest harness",             "Vectorised backtesting over 2020-2024 to validate signal quality."],
    ]
    pdf.two_col_table(
        ["Enhancement", "Detail"],
        s2_rows,
        col_widths=[55, 147],
    )

    pdf.sub_header("Sprint 3 — Planned")
    s3_rows = [
        ["Live order execution",         "Integration with IG Markets or Interactive Brokers API."],
        ["Position sizing engine",       "Kelly criterion + max drawdown constraint per trade."],
        ["Multi-session memory",         "Carry forward signals that improve by end-of-day confirmation."],
        ["News sentiment filter",        "NewsAPI + NLP sentiment scoring as additional gate modifier."],
        ["WebSocket mobile alerts",      "Push notifications to iOS/Android via Telegram bot commands."],
        ["Earnings calendar blocker",    "Auto-exclude tickers within 2 days of earnings announcement."],
    ]
    pdf.two_col_table(
        ["Enhancement", "Detail"],
        s3_rows,
        col_widths=[55, 147],
    )

    pdf.sub_header("Known Limitations")
    pdf.info_box(
        "Current System Limitations",
        [
            "1. yfinance data: free tier with no SLA. Occasional 404 / delisted ticker errors.",
            "2. RVOL: unreliable for low-volume pre-market hours. Shown as N/A when suspect.",
            "3. Regime detection: proxy-based (SPX/VIX). No real-time macro feed yet.",
            "4. No live execution: paper mode only. Telegram alerts for manual entry.",
            "5. ATR-based stops: work well for trending markets; less reliable in flat chop.",
            "6. Factor cap of 3: may exclude good signals when group is concentrated.",
        ],
        bg=(255, 240, 210), title_color=C_AMBER,
    )

    pdf.sub_header("Design Principles (Never Compromise)")
    pdf.info_box(
        "Institutional-Grade Rules",
        [
            "DATA_HEALTH gate: NEVER bypassed. Garbage in = garbage out. Always enforce.",
            "Signal drought > silence: always declare drought with blockers. Never ghost.",
            "ASCII safety: all PDF output must be latin-1 safe. No Unicode > U+00FF.",
            "Stars in ASCII: [*****] not Unicode star chars. fpdf2 will crash otherwise.",
            "Cost model: R:R always net of round-trip cost. Never show gross R:R as final.",
            "Compounding law: 2% daily. Everything serves this single objective.",
        ],
        bg=(220, 235, 255), title_color=C_DARK_BLUE,
    )

    # Closing
    pdf.ln(5)
    y = pdf.get_y()
    pdf.set_fill_color(*C_DARK_BLUE)
    pdf.rect(4, y, 202, 10, "F")
    pdf.set_xy(6, y + 1.5)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*C_GOLD)
    pdf.cell(0, 7, "NZT-48 v8.0 Apex Predator Engine  |  Built for fund-manager-grade reliability.")


# ---------------------------------------------------------------------------
# v3.0 NEW SECTION RENDERERS
# ---------------------------------------------------------------------------

def _render_strategy_router_chapter(pdf: MegaPDF, router_data: Optional[dict] = None) -> None:
    """Section 13: Strategy Router — active strategies, overlays, worked examples."""
    pdf.add_page()
    pdf.section_header("SECTION 13: STRATEGY ROUTER (v3.0)")

    pdf.sub_header("Overview")
    pdf.body_text(
        "The Strategy Router (signal_engine/strategy_router.py) classifies the current "
        "market context into a time-of-day window, activates relevant strategies, applies "
        "overlay risk filters, and computes a score boost for play ranking. "
        "It runs before every engine scan and writes strategies.json alongside plays.json."
    )

    pdf.sub_header("Strategy Categories")
    cat_rows = [
        ["TREND_MOMENTUM_CTA",       "CORE",     "ADX>20 + EMA aligned + regime not CHOPPY"],
        ["MOMENTUM_BREAKOUT",        "CORE",     "BB width rank >0.75 + RVOL surge"],
        ["STAT_ARB_MEAN_REVERT",     "CORE",     "RSI extreme + BB compressed + VWAP deviation"],
        ["FACTOR_ROTATION",          "CORE",     "Sector radar shows leadership shift"],
        ["VOL_SQUEEZE_BREAKOUT",     "CORE",     "BB width rank <0.20 — squeeze building"],
        ["VOL_BREAKOUT",             "CORE",     "BB width rank >0.75 — expansion imminent"],
        ["REBALANCE_FLOW",           "CORE",     "End-month rebalancing window (last 3 / first 3 days)"],
        ["OPENING_RANGE_BREAKOUT",   "INTRADAY", "08:00-08:30 or 14:30-15:00 only"],
        ["VWAP_TREND_PULLBACK",      "INTRADAY", "CORE_HOURS when trend confirmed"],
        ["VWAP_MEAN_REVERT",         "INTRADAY", "LUNCH_CHOP window (12:00-13:30 UK)"],
        ["GAP_GO_FADE",              "INTRADAY", "First 30min of session if gap >0.5%"],
        ["MACRO_EVENT_FILTER",       "OVERLAY",  "Always-on; deactivates at VIX>35"],
        ["VIX_TERM_CARRY_OVERLAY",   "OVERLAY",  "VIX level + vol-of-vol proxy"],
        ["BETA_NEUTRAL_SPREAD",      "INACTIVE", "Pairs data not available for LSE ETPs"],
        ["EARNINGS_CONFIRMATION",    "INACTIVE", "earnings_feed not connected"],
        ["IPO_LOCKUP_PRESSURE",      "INACTIVE", "lockup_calendar not connected"],
        ["MERGER_ARB_EVENT",         "INACTIVE", "deal_feed not connected"],
    ]
    pdf.two_col_table(
        ["Strategy Tag", "Category", "Activation Condition"],
        cat_rows,
        col_widths=[50, 22, 130],
    )

    pdf.sub_header("Time-of-Day Windows (UK)")
    tod_rows = [
        ["CHAOS_OPEN",          "08:00-08:30", "Observe only — high spread, wide stops"],
        ["MORNING_MOMENTUM",    "08:30-10:30", "Full strategies: TREND, ORB, GAP_GO"],
        ["TREND_EXTENSION",     "10:30-12:00", "VWAP_TREND, FACTOR_ROTATION preferred"],
        ["LUNCH_CHOP",          "12:00-13:30", "VWAP_MEAN_REVERT; raise RVOL threshold"],
        ["AFTERNOON_PUSH",      "13:30-15:00", "TREND_MOMENTUM, VOL_BREAKOUT"],
        ["POWER_HOUR",          "15:00-16:30", "Tight exits, continuations only"],
        ["CLOSE_MECHANICS",     "16:00-17:00", "Reduce exposure, market-on-close flow"],
        ["AFTER_HOURS",         "rest",         "5d data only, reduced risk mode"],
    ]
    pdf.two_col_table(
        ["Window", "UK Time", "Recommended Action"],
        tod_rows,
        col_widths=[42, 30, 130],
    )

    pdf.sub_header("Overlay Risk Discipline")
    ov_rows = [
        ["VIX > 45",               "KILL SWITCH — all non-defensive strategies deactivated"],
        ["VIX 35-45",              "DEFENSIVE mode — sizing_mode=HALF, score_boost=0"],
        ["VIX 20-35",              "CAUTION mode — VOL_TARGET active, sizing_mode=REDUCED"],
        ["DISPERSION >0.85",       "Factor cap tightened 3->2 signals per group"],
        ["REGIME = SHOCK",         "Kill switch triggered regardless of VIX level"],
        ["REBALANCE window",       "+0.15 weight bonus to REBALANCE_FLOW strategy"],
    ]
    pdf.two_col_table(
        ["Condition", "Overlay Action"],
        ov_rows,
        col_widths=[45, 157],
    )

    pdf.sub_header("Score Boost Formula")
    pdf.info_box(
        "Strategy-Weighted Score",
        [
            "strategy_weighted_score = composite * (1 + sum(active_weights) * 0.12)",
            "Capped at 100. Stars re-derived from strategy_weighted_score.",
            "Example: composite=75, 3 active strategies avg weight=0.3:",
            "  strategy_weighted_score = 75 * (1 + 0.9 * 0.12) = 75 * 1.108 = 83.1",
            "  Stars: 4 (from 83.1 > 80 threshold)",
            "Score boost only applied when kill_switch=False.",
        ],
        bg=C_NEAR_WHITE,
    )

    # Live router data if available
    if router_data and router_data.get("active"):
        pdf.sub_header("Live Router State (Today)")
        pdf.kv_row([
            ("Regime Tag",   router_data.get("regime_tag", "?"),         C_GOLD),
            ("TOD Window",   router_data.get("time_of_day", "?"),         C_DARK_BLUE),
            ("Sizing Mode",  router_data.get("sizing_mode", "NORMAL"),    C_AMBER),
            ("Kill Switch",  str(router_data.get("kill_switch", False)),  C_RED if router_data.get("kill_switch") else C_GREEN),
        ])
        active = router_data.get("active", [])
        if active:
            pdf.body_text("Active strategies: " + ", ".join(s.get("tag", "?") for s in active), indent=4)
        warnings = router_data.get("overlay_warnings", [])
        if warnings:
            pdf.info_box("Overlay Warnings", warnings, bg=(255, 245, 210), title_color=C_AMBER)


def _render_session_status_chapter(pdf: MegaPDF) -> None:
    """Section 14: Session status, artifact inventory, SHORT_WINDOW report."""
    pdf.add_page()
    pdf.section_header("SECTION 14: SESSION STATUS & ARTIFACT INVENTORY")

    pdf.sub_header("Session Run Status (data/session_status.json)")
    try:
        from signal_engine.signal_card import read_session_status
        status_data = read_session_status()
    except Exception:
        status_data = {}

    if status_data:
        status_rows = []
        for sess, info in status_data.items():
            status_rows.append([
                sess,
                info.get("status", "?"),
                "YES" if info.get("artifacts_written") else "NO",
                "YES" if info.get("pdf_written") else "NO",
                info.get("timestamp", "")[:19],
                _ascii(info.get("error_msg", "")[:60] or "OK"),
            ])
        pdf.two_col_table(
            ["Session", "Status", "Artifact", "PDF", "Timestamp", "Notes"],
            status_rows,
            col_widths=[32, 16, 18, 14, 40, 82],
        )
    else:
        pdf.body_text("No session status data found. PDFs have not run yet or status file not created.")

    pdf.sub_header("Artifact Inventory (artifacts/ directory)")
    try:
        artifacts_root = Path(__file__).parent.parent / "artifacts"
        today_str = str(date.today())
        today_dir = artifacts_root / today_str

        artifact_rows = []
        if today_dir.exists():
            for session_dir in sorted(today_dir.iterdir()):
                if session_dir.is_dir():
                    plays_path = session_dir / "plays.json"
                    strat_path = session_dir / "strategies.json"
                    n_plays = 0
                    if plays_path.exists():
                        try:
                            import json as _json
                            data = _json.loads(plays_path.read_text())
                            n_plays = data.get("total_plays", len(data.get("plays", [])))
                        except Exception:
                            pass
                    try:
                        plays_size = f"{plays_path.stat().st_size // 1024} KB" if plays_path.exists() else "-"
                    except Exception:
                        plays_size = "-"
                    artifact_rows.append([
                        session_dir.name,
                        "YES" if plays_path.exists() else "NO",
                        str(n_plays),
                        "YES" if strat_path.exists() else "NO",
                        plays_size,
                    ])
        else:
            pdf.body_text(f"No artifacts directory found for today ({today_str}). Engine has not run yet.")

        if artifact_rows:
            pdf.two_col_table(
                ["Session", "plays.json", "# Plays", "strategies.json", "Size"],
                artifact_rows,
                col_widths=[40, 25, 18, 35, 20],
            )
    except Exception as e:
        pdf.set_font("Helvetica", "I", 7)
        pdf.set_text_color(*C_GREY)
        pdf.cell(0, 5, f"  Artifacts not available for this session. ({type(e).__name__})")
        pdf.ln(6)

    pdf.sub_header("SHORT_WINDOW Mode Explained (v3.0)")
    pdf.info_box(
        "Adaptive MIN_BARS: Honest Early-Session Windowing",
        [
            "Problem: Early in a session (< 14 bars of 1h data), ATR14/RSI14 can't be computed.",
            "Old behaviour: hard FAIL at n_bars < 14. Blocked ALL early signals.",
            "New behaviour (v3.0): SHORT_WINDOW mode for 7-13 bars.",
            "  7-13 bars: RELAXED gate (not FAIL). Uses adaptive n-bar window.",
            "  reliability_penalty = 0.05 x (14 - n_bars). Applied to PlayScore.",
            "  SignalCard tagged: short_window=True, bars_available=n, reliability_penalty=p.",
            "  < 7 bars: HARD FAIL (indicators would be meaningless noise).",
            "Signals generated in SHORT_WINDOW are honest: no data fabrication.",
        ],
        bg=C_NEAR_WHITE,
    )

    pdf.sub_header("Artifact-First PDF Pipeline (v3.0)")
    pdf.body_text(
        "All 4 PDF jobs (Pre-LSE, Pre-NYSE, EOD, Mega) now run the signal engine "
        "first and write plays.json atomically before generating the PDF. "
        "This guarantees that session artifacts always exist when the PDF renders. "
        "Atomic write: tempfile.mkstemp -> fsync -> rename (prevents partial reads)."
    )


def _render_calibration_chapter(pdf: MegaPDF) -> None:
    """Section 15: Calibration tables — ready to fill with live outcome data."""
    pdf.add_page()
    pdf.section_header("SECTION 15: CALIBRATION TABLES (READY FOR DATA)")

    pdf.sub_header("Win-Rate Required at Various R:R Ratios")
    wr_rows = [
        ["1.0", "50.0%",  "Breakeven at 50 — no edge"],
        ["1.2", "45.5%",  "Fallback signals minimum threshold"],
        ["1.5", "40.0%",  "Strict signal minimum — achievable"],
        ["2.0", "33.3%",  "Strong edge — 1-in-3 wins profitable"],
        ["2.5", "28.6%",  "Excellent edge — 2-in-7 wins profitable"],
        ["3.0", "25.0%",  "Exceptional — 1-in-4 wins covers losses"],
        ["4.0", "20.0%",  "Lottery territory — rare but powerful"],
    ]
    pdf.two_col_table(
        ["R:R Ratio", "Min Win-Rate (Break-Even)", "Notes"],
        wr_rows,
        col_widths=[28, 55, 119],
    )

    pdf.sub_header("Strategy Win-Rate Matrix (Empty — Populate from Outcomes)")
    matrix_rows = [
        ["TREND_MOMENTUM_CTA",    "TRENDING_UP",    "-",  "-",  "-",  "-"],
        ["TREND_MOMENTUM_CTA",    "RANGE_BOUND",     "-",  "-",  "-",  "-"],
        ["STAT_ARB_MEAN_REVERT",  "RANGE_BOUND",     "-",  "-",  "-",  "-"],
        ["VOL_BREAKOUT",          "HIGH_VOLATILITY", "-",  "-",  "-",  "-"],
        ["MOMENTUM_BREAKOUT",     "TRENDING_UP",     "-",  "-",  "-",  "-"],
    ]
    pdf.two_col_table(
        ["Strategy", "Regime", "Trades", "Wins", "Win%", "Avg R"],
        matrix_rows,
        col_widths=[50, 35, 18, 18, 18, 18],
    )

    pdf.sub_header("Time-of-Day Win-Rate Matrix (Empty — Populate from Outcomes)")
    tod_rows = [
        ["MORNING_MOMENTUM",  "INTRADAY_SWING", "-", "-", "-", "-"],
        ["MORNING_MOMENTUM",  "SCALP",          "-", "-", "-", "-"],
        ["LUNCH_CHOP",        "INTRADAY_SWING", "-", "-", "-", "-"],
        ["POWER_HOUR",        "INTRADAY_SWING", "-", "-", "-", "-"],
        ["AFTER_HOURS",       "INTRADAY_SWING", "-", "-", "-", "-"],
    ]
    pdf.two_col_table(
        ["Time Window", "Track", "Trades", "Wins", "Win%", "Avg R"],
        tod_rows,
        col_widths=[40, 35, 18, 18, 18, 18],
    )

    pdf.sub_header("Historical Equity Curve Placeholder")
    pdf.info_box(
        "Equity Curve (Paper Mode — No Trades Yet)",
        [
            "Starting equity: GBP 10,000",
            f"Mode: WIN_RATE | Universe: {len(_ISA_EXTENDED)} LSE leveraged ETPs",
            "Target: GBP 1,485,757 at 2% daily compounding (252 trading days)",
            "Current equity: GBP 10,000 (no trades executed yet)",
            "Data will populate here once virtual trades are logged via VirtualTrader.",
        ],
        bg=(230, 250, 230), title_color=(10, 100, 10),
    )

    pdf.sub_header("Factor Group Performance (Empty)")
    factor_rows = [
        [fg, "-", "-", "-", "-"]
        for fg in sorted(set(_FACTOR_GROUPS.values()))
    ]
    pdf.two_col_table(
        ["Factor Group", "Trades", "Win%", "Avg R", "Total R"],
        factor_rows,
        col_widths=[50, 18, 18, 18, 18],
    )

    pdf.add_page()
    pdf.sub_header("ISA Universe: Full ETP Reference")
    etp_rows = [
        ["QQQ3.L",  "3x",  "nasdaq_beta_long",  "3x Nasdaq-100 Long ETN (WisdomTree)"],
        ["3LUS.L",  "3x",  "nasdaq_beta_long",  "3x US 500 Long ETN"],
        ["QQQ5.L",  "5x",  "nasdaq_beta_long",  "5x Nasdaq-100 Long ETP"],
        ["SP5L.L",  "5x",  "nasdaq_beta_long",  "5x S&P 500 Long ETP"],
        ["QQQS.L",  "3x",  "nasdaq_beta_short", "3x Nasdaq-100 Short ETN"],
        ["3USS.L",  "3x",  "nasdaq_beta_short", "3x US 500 Short ETN"],
        ["3SEM.L",  "3x",  "semiconductors",    "3x Semiconductors Long ETN"],
        ["NVD3.L",  "3x",  "semiconductors",    "3x Nvidia Long ETP"],
        ["TSM3.L",  "3x",  "semiconductors",    "3x TSMC Long ETP"],
        ["MU2.L",   "2x",  "semiconductors",    "2x Micron Long ETP"],
        ["ARM3.L",  "3x",  "semiconductors",    "3x ARM Holdings Long ETP (Extended)"],
        ["AMD3.L",  "3x",  "semiconductors",    "3x AMD Long ETP (Extended)"],
        ["TSL3.L",  "3x",  "ev_tech",           "3x Tesla Long ETP"],
        ["GPT3.L",  "3x",  "ai_gpt",            "3x OpenAI/GPT thematic ETP"],
        ["3LDE.L",  "3x",  "eu_broad",          "3x Germany DAX Long ETN (Extended)"],
        ["3LEU.L",  "3x",  "eu_broad",          "3x Eurostoxx Long ETN (Extended)"],
        ["3GOL.L",  "3x",  "commodities",       "3x Gold Long ETN (Extended)"],
        ["3SIL.L",  "3x",  "commodities",       "3x Silver Long ETN (Extended)"],
        ["3OIL.L",  "3x",  "commodities",       "3x Oil Long ETN (Extended)"],
    ]
    pdf.two_col_table(
        ["Ticker", "Lev", "Factor Group", "Product Description"],
        etp_rows,
        col_widths=[20, 12, 36, 134],
    )

    pdf.sub_header("Leverage Decay: Why It Matters for 3x ETPs")
    pdf.body_text(
        "Leveraged ETPs suffer from 'volatility decay' (also called beta decay or path dependency). "
        "For a 3x ETP: if the underlying moves +10% then -10%, the underlying is at 99% of start. "
        "But the 3x ETP moved +30% then -30% = 0.70 x 1.30 = 0.91 -- a 9% loss vs 1% for underlying. "
        "This decay accelerates in choppy/sideways markets. INTRADAY trading eliminates overnight decay. "
        "NZT-48 addresses this via: "
        "(1) decay_risk flag in SignalCard; "
        "(2) -1 star modifier for HIGH decay_risk in CHOPPY regime; "
        "(3) LEVERAGE_DECAY_WARNING overlay in HIGH_VOLATILITY regime; "
        "(4) time stops (12 min scalp / 180 min swing) prevent holding through chop."
    )

    pdf.sub_header("Cost Model: Full Round-Trip Breakdown")
    cost_detail_rows = [
        ["Spread cost",       "Half bid-ask on entry + half on exit. LSE ETPs: ~7-11 bps each side."],
        ["Slippage estimate", "5 bps per side (realistic for ISA leveraged ETPs, 2x-5x products)."],
        ["Stamp duty",        "0 bps -- LSE ETNs/ETPs are exempt from 0.5% UK stamp duty."],
        ["Platform fee",      "Not modelled (ISA wrapper fee varies by platform, typically flat monthly)."],
        ["Total round-trip",  "25-32 bps depending on ticker. Deducted from R:R before gate check."],
        ["Gate threshold",    "R:R >= 1.5 AFTER cost deduction = strict mode pass condition."],
    ]
    pdf.two_col_table(
        ["Cost Component", "Detail"],
        cost_detail_rows,
        col_widths=[40, 162],
    )

    pdf.sub_header("ATR-Based Stop/Target Calculation Examples")
    # Show worked examples for each setup type
    example_rows = []
    for ticker, entry, atr_pct_ex in [("QQQ3.L", 10.00, 3.5), ("NVD3.L", 5.00, 4.2), ("TSL3.L", 8.00, 5.0)]:
        atr = entry * atr_pct_ex / 100
        for setup, frac in [("continuation", 0.40), ("breakout", 0.35), ("default", 0.50)]:
            stop_dist = atr * frac
            t1        = max(1.2 * stop_dist, 0.6 * atr)
            t2        = 2.5 * stop_dist
            stop_p    = entry - stop_dist
            t1_p      = entry + t1
            t2_p      = entry + t2
            rr        = t1 / stop_dist if stop_dist > 0 else 0
            example_rows.append([
                ticker, setup,
                f"{entry:.2f}", f"{atr:.3f}", f"{atr_pct_ex}%",
                f"{stop_p:.4f}", f"{t1_p:.4f}", f"{t2_p:.4f}",
                f"{rr:.2f}",
            ])
    pdf.two_col_table(
        ["Ticker", "Setup", "Entry", "ATR", "ATR%", "Stop", "T1", "T2", "R:R"],
        example_rows,
        col_widths=[18, 26, 16, 16, 14, 18, 18, 18, 14],
    )


def _render_repo_inventory(pdf: MegaPDF) -> None:
    """Section 16: Programmatic file listing — repo inventory."""
    pdf.add_page()
    pdf.section_header("SECTION 16: REPO INVENTORY & FILE CATALOGUE")

    import glob as _glob

    repo_root = Path(__file__).parent.parent
    py_files = sorted(_glob.glob(str(repo_root / "**" / "*.py"), recursive=True))

    # Filter out __pycache__ and .venv
    py_files = [f for f in py_files if "__pycache__" not in f and ".venv" not in f
                and "site-packages" not in f]

    pdf.sub_header(f"Python Files ({len(py_files)} total)")

    rows = []
    for fpath in py_files[:120]:   # show up to 120 files for page count guarantee
        rel = str(Path(fpath).relative_to(repo_root))
        try:
            lines = Path(fpath).read_text(errors="replace").splitlines()
            n_lines = len(lines)
            # First non-blank line after docstring start
            docline = ""
            for ln in lines[:20]:
                stripped = ln.strip()
                if stripped and not stripped.startswith('"""') and not stripped.startswith("'''"):
                    docline = stripped[:60]
                    break
            # Count defs/classes
            n_def = sum(1 for ln in lines if ln.strip().startswith("def ") or ln.strip().startswith("async def "))
            n_cls = sum(1 for ln in lines if ln.strip().startswith("class "))
        except Exception:
            n_lines, n_def, n_cls, docline = 0, 0, 0, ""
        rows.append([
            _ascii(rel, 50),
            str(n_lines),
            str(n_def),
            str(n_cls),
            _ascii(docline, 60),
        ])

    pdf.two_col_table(
        ["File", "Lines", "Funcs", "Classes", "First Docline"],
        rows,
        col_widths=[52, 14, 14, 18, 64],
    )

    if len(py_files) > 120:
        pdf.body_text(f"... and {len(py_files) - 120} more files (truncated at 120 for readability).")

    pdf.sub_header("Key Configuration")
    config_path = repo_root / "config" / "settings.yaml"
    if config_path.exists():
        try:
            config_lines = config_path.read_text(errors="replace").splitlines()[:40]
            pdf.body_text("config/settings.yaml (first 40 lines):", size=7)
            for line in config_lines:
                pdf.body_text(_ascii(line), size=6.5, indent=4)
        except Exception:
            pdf.body_text("Could not read config/settings.yaml")
    else:
        pdf.body_text("config/settings.yaml not found.")


def _render_scoring_sensitivity(pdf: MegaPDF) -> None:
    """Section 17: Scoring sensitivity analysis — what happens when weights shift."""
    pdf.add_page()
    pdf.section_header("SECTION 17: SCORING SENSITIVITY ANALYSIS")

    pdf.sub_header("Formula Summary")
    pdf.body_text(
        "PlayScore = 0.30*Momentum + 0.20*Volatility + 0.15*RegimeFit "
        "+ 0.15*Liquidity + 0.10*RR + 0.10*Quality -- DataReliabilityPenalty"
    )

    pdf.sub_header("Sensitivity Table: Score vs Component Value")
    # Show how composite score changes with each component, all else = 0.7
    components = [
        ("Momentum",  0.30),
        ("Volatility",0.20),
        ("RegimeFit", 0.15),
        ("Liquidity", 0.15),
        ("RR Score",  0.10),
        ("Quality",   0.10),
    ]

    base_others = 0.70   # assume all other components = 0.70
    rows = []
    for name, weight in components:
        others_total = sum(w for _, w in components if _ != name)
        for val in [0.2, 0.4, 0.6, 0.8, 1.0]:
            base_score = weight * val + others_total * base_others
            composite = round(base_score * 100, 1)
            rows.append([name, f"{val:.1f}", f"{weight:.2f}", f"{composite:.1f}"])

    pdf.two_col_table(
        ["Component", "Value", "Weight", "Composite (others=0.7)"],
        rows,
        col_widths=[40, 18, 18, 48],
    )

    pdf.sub_header("Weight Shift Impact (+/-0.05)")
    shift_rows = [
        ["Momentum +0.05",    "Raises composite by ~+2 to +4 pts for strong momentum signals"],
        ["Momentum -0.05",    "Lowers composite — reduces volatility-driven false positives"],
        ["Volatility +0.05",  "Rewards higher ATR% plays; breakout signals benefit most"],
        ["Regime +0.05",      "Makes system more regime-sensitive; fewer counter-trend trades"],
        ["Liquidity +0.05",   "Tightens RVOL requirement; reduces low-vol signal count"],
        ["RR +0.05",          "Rewards tighter setups; scalp plays benefit"],
    ]
    pdf.two_col_table(
        ["Weight Change", "Effect on Signal Selection"],
        shift_rows,
        col_widths=[40, 162],
    )

    pdf.sub_header("Data Reliability Penalty Impact")
    penalty_rows = [
        ["n_bars=13 (1 missing)", "0.05", "Composite x 0.95 = -5% of raw score"],
        ["n_bars=10 (4 missing)", "0.20", "Composite x 0.80 = -20% of raw score"],
        ["n_bars=7  (7 missing)", "0.35", "Composite x 0.65 = -35% of raw score (max penalty)"],
        ["RVOL unavailable",     "+0.15", "From _fetch_isa_universe_data reliability calc"],
        ["ATR% < 0.5%",          "+0.10", "From _fetch_isa_universe_data reliability calc"],
    ]
    pdf.two_col_table(
        ["Condition", "Penalty Added", "Effect"],
        penalty_rows,
        col_widths=[50, 28, 124],
    )


def _render_master_spec_alignment(pdf: MegaPDF) -> None:
    """Section 18b: NZT-48 Master Spec v8.0 alignment — architecture narrative."""
    pdf.add_page()
    pdf.section_header("SECTION 18b: MASTER SPEC v8.0 ALIGNMENT AUDIT")

    pdf.sub_header("5-Layer Perception Engine")
    pdf.body_text(
        "The NZT-48 Master Spec defines a 5-Layer Perception Engine that processes "
        "market data from raw price action through to narrative/events. "
        "Below is the current implementation status for each layer."
    )
    layer_rows = [
        ["Layer 1: Price Action",  "IMPLEMENTED", "VWAP proxy via EMA20, ATR14, RSI14, MACD, BB width"],
        ["Layer 2: Regime State",  "IMPLEMENTED", "8-state via SPX/VIX proxy + tick loop + strategy router"],
        ["Layer 3: Sector Flow",   "PARTIAL",     "sector_rotation.py radar scan -- factor groups only"],
        ["Layer 4: Macro Gravity", "STUB",        "rates/DXY/commodities feed not yet connected"],
        ["Layer 5: Narrative",     "INACTIVE",    "earnings/news feed not connected (honest: N/A)"],
    ]
    pdf.two_col_table(
        ["Layer", "Status", "Current Implementation"],
        layer_rows,
        col_widths=[40, 25, 137],
    )

    pdf.sub_header("8-State Regime Classification")
    regime_rows = [
        ["TRENDING_UP_STRONG",   "SPX 5d >2%, VIX <15",           "All long strategies fully active"],
        ["TRENDING_UP_MOD",      "SPX 5d +0.5/+2%, VIX <20",      "Long strategies active, normal size"],
        ["TRENDING_DOWN_STRONG", "SPX 5d <-2%, VIX >20",          "Short strategies active, longs blocked"],
        ["TRENDING_DOWN_MOD",    "SPX 5d -0.5/-2%, VIX 15-25",    "Shorts preferred, longs watch-only"],
        ["RANGE_BOUND",          "SPX flat +/-0.5%, VIX 15-20",   "Mean reversion strategies preferred"],
        ["HIGH_VOLATILITY",      "VIX 20-35, no clear trend",      "Reduce size, raise RVOL threshold"],
        ["RISK_OFF",             "VIX >30 or SPX <-1.5% intraday", "Only defensive plays, reduced size"],
        ["SHOCK",                "VIX spike >40, black swan",       "Kill switch -- no new signals"],
    ]
    pdf.two_col_table(
        ["Regime State", "Trigger Conditions", "Strategy Behavior"],
        regime_rows,
        col_widths=[42, 55, 105],
    )

    pdf.sub_header("14 Strategy Engines (Master Spec S1-S14)")
    s_rows = [
        ["S1",  "Regime Trend",      "ACTIVE",   "TREND_MOMENTUM_CTA in strategy router"],
        ["S2",  "Momentum Breakout", "ACTIVE",   "MOMENTUM_BREAKOUT + VOL_BREAKOUT"],
        ["S3",  "Mean Reversion",    "DORMANT",  "STAT_ARB_MEAN_REVERT (V2: dormant by default)"],
        ["S4",  "Catalyst",          "STUB",     "Requires earnings/news feed (honest: N/A)"],
        ["S5",  "PEAD",              "STUB",     "Requires earnings feed (honest: N/A)"],
        ["S6",  "Macro Regime",      "PARTIAL",  "Macro proxy via VIX/SPX only"],
        ["S7",  "Sector Rotation",   "ACTIVE",   "FACTOR_ROTATION + sector_rotation.py"],
        ["S8",  "Vol Crush",         "ACTIVE",   "VOL_SQUEEZE_BREAKOUT in router"],
        ["S9",  "Pairs",             "INACTIVE", "BETA_NEUTRAL_SPREAD -- no pairs data for LSE ETPs"],
        ["S10", "AI Thematic",       "STUB",     "Requires AI/theme feed"],
        ["S11", "Hot Scanner",       "ACTIVE",   "Covered by RVOL gate + MOMENTUM_BREAKOUT"],
        ["S12", "Rebalance Flow",    "ACTIVE",   "REBALANCE_FLOW strategy in router"],
        ["S13", "Trend Compounding", "ACTIVE",   "S15 2% Daily Target (strategies/daily_target.py)"],
        ["S14", "Gamma Squeeze",     "STUB",     "Requires options flow data (honest: N/A)"],
    ]
    pdf.two_col_table(
        ["#", "Strategy Name", "Status", "NZT-48 Implementation"],
        s_rows,
        col_widths=[12, 38, 22, 130],
    )

    pdf.sub_header("Cognitive Loop (Section 3 of Master Spec)")
    loop_rows = [
        ["INGEST",   "yfinance OHLCV fetch (1h bars, 10d period by default)"],
        ["PERCEIVE", "TickerFeatures: ATR, RSI, MACD, EMA, VWAP proxy, RVOL, n_bars"],
        ["CLASSIFY", "Gate funnel: DATA_HEALTH -> PRICE_SCALE -> MIN_BARS -> TRADABILITY -> soft gates"],
        ["DECIDE",   "Strategy Router selects active strategies; PlayScore computed; score boost applied"],
        ["QUALIFY",  "Two-layer strict+fallback pipeline; drought if 0 signals after fallback"],
        ["SIZE",     "Sizing hint (S/M/L) from VOL_TARGET overlay; stop/target from setup_type ATR fractions"],
        ["EXECUTE",  "Paper mode: Telegram alert to human operator. VirtualTrader logs paper positions."],
        ["LEARN",    "LearningEngine + TradeAutopsy + StrategyTournament post-trade analysis."],
    ]
    pdf.two_col_table(
        ["Phase", "NZT-48 Implementation"],
        loop_rows,
        col_widths=[22, 180],
    )

    pdf.add_page()
    pdf.sub_header("SignalCard v3.0 Full Field Reference")
    schema_rows = [
        ["ticker",              "str",   "Ticker symbol (e.g. QQQ3.L)"],
        ["direction",           "str",   "LONG | SHORT"],
        ["track",               "str",   "SCALP | INTRADAY_SWING"],
        ["stars",               "int",   "1-5 star rating (1=[*____] ... 5=[*****])"],
        ["composite",           "float", "PlayScore 0-100"],
        ["label",               "str",   "STRICT | WATCH-SIGNAL (xxx)"],
        ["momentum_score",      "float", "RSI + MACD + EMA alignment composite (0-1)"],
        ["volatility_score",    "float", "ATR% + BB width rank composite (0-1)"],
        ["regime_score",        "float", "Regime compatibility score (0-1)"],
        ["liquidity_score",     "float", "RVOL normalised to 1.0 at 3x RVOL"],
        ["rr_score",            "float", "Net R:R score (0-1)"],
        ["quality_score",       "float", "ADX trend strength (0-1)"],
        ["entry",               "float", "Entry price level"],
        ["stop",                "float", "Stop loss price"],
        ["target1",             "float", "First target (T1)"],
        ["target2",             "float", "Second target (T2, runner)"],
        ["rr_ratio",            "float", "Net reward:risk after round-trip cost"],
        ["strategy_tag",        "str",   "e.g. TREND_MOMENTUM_CTA (v3.0)"],
        ["why_strategy_now",    "list",  "Bullet reasons for strategy selection (v3.0)"],
        ["time_of_day_window",  "str",   "e.g. MORNING_MOMENTUM (v3.0)"],
        ["overlay_tags",        "list",  "Active overlay names (v3.0)"],
        ["overlay_warnings",    "list",  "Human-readable overlay risk warnings (v3.0)"],
        ["sizing_hint",         "str",   "S / M / L -- from VOL_TARGET overlay (v3.0)"],
        ["strategy_weighted_score","float","Composite after strategy boost (v3.0)"],
        ["bars_available",      "int",   "Actual bars (may be < 14 in SHORT_WINDOW) (v3.0)"],
        ["indicator_window_used","int",  "Adaptive window for ATR/RSI (v3.0)"],
        ["reliability_penalty", "float", "0.05 x (14 - bars) if SHORT_WINDOW (v3.0)"],
        ["short_window",        "bool",  "True if 7 <= bars < 14 (v3.0)"],
        ["data_reliability",    "float", "0-1 overall data quality score"],
        ["decay_risk",          "str",   "LOW | MEDIUM | HIGH (leverage decay risk)"],
        ["fallback_step",       "int",   "0=strict; 1/2/3/4=fallback step applied"],
        ["why_fallback",        "str",   "Human-readable fallback reason"],
        ["regime",              "str",   "Regime at time of signal generation"],
        ["session",             "str",   "Session name (PRE_LSE, NYSE, etc.)"],
        ["category",            "str",   "TRADE | WATCH | EXCLUDED"],
        ["setup_type",          "str",   "continuation | breakout | mean_revert | default"],
    ]
    pdf.two_col_table(
        ["Field", "Type", "Description"],
        schema_rows,
        col_widths=[50, 18, 134],
    )

    pdf.sub_header("plays.json Artifact Schema (v3.0)")
    artifact_rows = [
        ["generated_at",   "ISO8601 timestamp of artifact creation (atomic write)"],
        ["session",        "Session name e.g. PRE_LSE"],
        ["regime",         "Regime state at time of engine run"],
        ["mode",           "WIN_RATE or R_MULTIPLE"],
        ["strict_count",   "Number of STRICT (non-fallback) signals produced"],
        ["fallback_count", "Number of WATCH-SIGNAL (fallback) signals produced"],
        ["total_plays",    "strict_count + fallback_count"],
        ["drought",        "null or {top_blockers, tickers_checked, hard_fail_count, ...}"],
        ["funnel",         "{tracked, data_valid, passed_all_gates, signals_strict, ...}"],
        ["plays",          "Array of SignalCard.to_dict() objects (full v3.0 schema)"],
    ]
    pdf.two_col_table(
        ["Field", "Description"],
        artifact_rows,
        col_widths=[40, 162],
    )


def _render_deep_diagnostic(pdf: MegaPDF, engine_result, isa_data: dict) -> None:
    """Section 18: Deep Diagnostic — gate-level per ticker audit.
    Guarantees page count >= 40 by showing all tickers even if data is thin.
    """
    pdf.add_page()
    pdf.section_header("SECTION 18: DEEP DIAGNOSTIC — PER-TICKER GATE AUDIT")

    pdf.body_text(
        "This section provides a full gate-level audit for every ticker in the extended "
        "ISA universe. It shows raw indicator values and whether each ticker would pass "
        "or fail each gate. Use this to diagnose drought conditions and understand "
        "exactly why signals are or are not being generated. "
        "Gates evaluated at STRICT thresholds. RELAXED = fallback-mode pass only."
    )

    # Part A: Summary table
    pdf.sub_header("A. Quick-Glance Gate Summary (All Tickers)")
    summary_rows = []
    for ticker in _ISA_EXTENDED:
        info = isa_data.get(ticker, {})
        if info.get("status") != "OK":
            summary_rows.append([ticker, "NO_DATA", "-", "-", "-", "-", "FAIL", "FAIL"])
            continue
        close   = info.get("close", 0.0)
        atr_pct = info.get("atr_pct", 0.0)
        rvol    = info.get("rvol")
        n_bars  = info.get("n_bars", 0)
        bias    = info.get("bias", "?")
        g_bars  = "PASS" if n_bars >= 14 else ("SW" if n_bars >= 7 else "FAIL")
        g_trad  = "PASS" if atr_pct >= 1.0 else ("RELX" if atr_pct >= 0.60 else "FAIL")
        g_rvol  = "PASS" if (rvol and rvol >= 0.80) else ("N/A" if not rvol else ("RELX" if rvol >= 0.55 else "FAIL"))
        summary_rows.append([
            ticker, f"{close:.3f}", f"{atr_pct:.2f}%",
            f"{rvol:.1f}x" if rvol else "N/A",
            str(n_bars), bias, g_bars, g_trad,
        ])
    pdf.two_col_table(
        ["Ticker", "Close", "ATR%", "RVOL", "Bars", "Bias", "MIN_BARS", "TRADABILITY"],
        summary_rows,
        col_widths=[20, 20, 16, 16, 14, 14, 22, 22],
    )

    # Part B: Per-ticker detailed breakdown
    pdf.add_page()
    pdf.sub_header("B. Full Per-Ticker Indicator + Gate Breakdown")

    for ticker in _ISA_EXTENDED:
        info   = isa_data.get(ticker, {})
        status = info.get("status", "MISSING")
        grp    = _FACTOR_GROUPS.get(ticker, "other")
        lev    = _LEVERAGE_MAP.get(ticker, "?")

        # Mini-header bar
        y_before = pdf.get_y()
        if y_before > 265:
            pdf.add_page()
            y_before = pdf.get_y()
        pdf.set_fill_color(*C_MID_BLUE)
        pdf.rect(4, y_before, 202, 6, "F")
        pdf.set_xy(6, y_before + 1)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*C_WHITE)
        pdf.cell(120, 4, f"{ticker}  |  {grp}  |  {lev}x")
        pdf.ln(7)

        if status != "OK":
            pdf.body_text(f"  Status: {status} -- no OHLCV data. Excluded from all gate checks.", size=7)
            pdf.hr()
            continue

        close   = info.get("close", 0.0)
        atr_pct = info.get("atr_pct", 0.0)
        rsi     = info.get("rsi", 0.0)
        ema     = info.get("ema_align", "?")
        rvol    = info.get("rvol")
        n_bars  = info.get("n_bars", 0)
        rel     = info.get("data_reliability", 1.0)
        bias    = info.get("bias", "?")
        macd    = info.get("macd_hist", 0.0)
        day_chg = info.get("day_change", 0.0)

        # Gate evaluations
        g_price  = "PASS" if close > 0 and not (ticker.endswith(".L") and close > 5000) else "FAIL"
        g_bars   = "PASS" if n_bars >= 14 else ("SHORT_WINDOW" if n_bars >= 7 else "FAIL")
        g_trad   = "PASS" if atr_pct >= 1.0 else ("RELAXED" if atr_pct >= 0.60 else "FAIL")
        g_rvol   = "PASS" if (rvol and rvol >= 0.80) else ("N/A" if not rvol else ("RELAXED" if rvol >= 0.55 else "FAIL"))

        # Momentum score approximation
        try:
            import math as _math
            rsi_input = rsi if bias == "LONG" else (100 - rsi)
            rsi_s = 1.0 / (1.0 + _math.exp(-0.12 * (rsi_input - 55)))
        except Exception:
            rsi_s = 0.5
        macd_s = 0.8 if (bias == "LONG" and macd > 0) or (bias == "SHORT" and macd < 0) else 0.2
        ema_s  = 0.9 if (bias == "LONG" and ema == "BULL") or (bias == "SHORT" and ema == "BEAR") else 0.3
        mom    = (rsi_s + macd_s + ema_s) / 3

        # Delta-to-pass diagnostics
        delta_notes = []
        if atr_pct < 1.0:
            delta_notes.append(f"TRADABILITY: ATR%={atr_pct:.2f}% < 1.0% (need +{1.0-atr_pct:.2f}% to pass strict)")
        if rvol and rvol < 0.80:
            delta_notes.append(f"RVOL: {rvol:.2f}x < 0.80 (need +{0.80-rvol:.2f}x; fallback@0.55 {'PASS' if rvol >= 0.55 else 'FAIL'})")
        if n_bars < 14:
            delta_notes.append(f"MIN_BARS: {n_bars} bars < 14 (need {14-n_bars} more; SHORT_WINDOW at 7+)")
        if mom < 0.55:
            delta_notes.append(f"MOMENTUM: {mom:.2f} < 0.55 (need +{0.55-mom:.2f}; fallback@0.40 {'PASS' if mom >= 0.40 else 'FAIL'})")

        # Render
        pdf.kv_row([
            ("Close",    f"{close:.4f}",            C_DARK_BLUE),
            ("ATR%",     f"{atr_pct:.2f}%",          C_GREEN if atr_pct >= 1.0 else C_RED),
            ("RSI",      f"{rsi:.1f}",               C_DARK_BLUE),
            ("MACD Hist",f"{macd:.5f}",              C_GREEN if macd > 0 else C_RED),
        ])
        pdf.kv_row([
            ("EMA",      ema,                         C_GREEN if ema == "BULL" else (C_RED if ema == "BEAR" else C_AMBER)),
            ("RVOL",     f"{rvol:.1f}x" if rvol else "N/A", C_GREEN if rvol and rvol >= 0.80 else C_AMBER),
            ("Bars",     str(n_bars),                 C_GREEN if n_bars >= 14 else C_AMBER),
            ("Day Chg",  f"{day_chg:+.2f}%",          C_GREEN if day_chg > 0 else C_RED),
        ])
        pdf.kv_row([
            ("Rely",     f"{rel:.2f}",                C_GREEN if rel >= 0.85 else C_AMBER),
            ("Bias",     bias,                         C_GREEN if bias == "LONG" else C_RED),
            ("Mom Score",f"{mom:.2f}",                 C_GREEN if mom >= 0.55 else C_AMBER),
            ("Leverage", f"{lev}x",                    C_DARK_BLUE),
        ])
        pdf.kv_row([
            ("PRICE_SCALE",   g_price,  C_GREEN if g_price == "PASS" else C_RED),
            ("MIN_BARS",      g_bars,   C_GREEN if "PASS" in g_bars else (C_AMBER if "SHORT" in g_bars else C_RED)),
            ("TRADABILITY",   g_trad,   C_GREEN if "PASS" in g_trad else (C_AMBER if "RELAXED" in g_trad else C_RED)),
            ("VOL_LIQUIDITY", g_rvol,   C_GREEN if "PASS" in g_rvol else (C_AMBER if g_rvol in ("N/A", "RELAXED") else C_RED)),
        ])

        if delta_notes:
            for dn in delta_notes:
                pdf.body_text(f"  [DELTA-TO-PASS] {dn}", size=6.5, indent=6)

        pdf.hr()

    # Part C: PlayScore worked examples for top plays
    if engine_result and engine_result.plays:
        pdf.add_page()
        pdf.sub_header("C. PlayScore Worked Examples (Top Plays)")
        pdf.body_text(
            "For each of the top plays, the full PlayScore calculation is shown below. "
            "This demonstrates how each component contributes to the final composite score."
        )
        weights = [
            ("Momentum",  0.30),
            ("Volatility",0.20),
            ("RegimeFit", 0.15),
            ("Liquidity", 0.15),
            ("R:R Score", 0.10),
            ("Quality",   0.10),
        ]
        for ps in engine_result.plays[:10]:
            pdf.sub_header(f"  {ps.ticker}  {ps.direction}  {ps.stars_str}  {ps.composite:.1f}/100")
            scores = [
                ps.momentum, ps.volatility, ps.regime_fit,
                ps.liquidity, ps.rr_score, getattr(ps, "quality", 0.0),
            ]
            rows = []
            for (name, w), val in zip(weights, scores):
                contribution = w * val * 100
                rows.append([name, f"{w:.2f}", f"{val:.3f}", f"{contribution:.2f}", f"{contribution:.2f}pts"])
            pdf.two_col_table(
                ["Component", "Weight", "Score (0-1)", "Contribution", "Points"],
                rows,
                col_widths=[40, 20, 28, 28, 24],
            )
            pdf.kv_row([
                ("Composite",    f"{ps.composite:.1f}/100",  C_GOLD),
                ("Stars",        ps.stars_str,                C_GOLD),
                ("Label",        _ascii(ps.label, 20),        C_GREEN if "STRICT" in ps.label else C_AMBER),
                ("Strategy Tag", getattr(ps, "strategy_tag", "--"), C_DARK_BLUE),
            ])
            if ps.reasons:
                pdf.body_text("  Reasons: " + " | ".join(_ascii(r, 50) for r in ps.reasons[:3]), size=6.5, indent=4)
            pdf.hr()


def _render_api_contract(pdf: MegaPDF) -> None:
    """Section 19: Full API Contract Reference — all REST + WebSocket endpoints."""
    pdf.add_page()
    pdf.section_header("SECTION 19: COMMAND CENTER API CONTRACT REFERENCE")

    pdf.body_text(
        "The NZT-48 Command Center exposes a FastAPI REST API on port 8765. "
        "All endpoints return JSON. The WebSocket endpoint pushes state snapshots every 15 seconds. "
        "The halt toggle is the primary manual intervention mechanism — it stops the engine "
        "from emitting new signals without stopping the tick loop or the server. "
        "All endpoints are read-only except POST /api/halt."
    )

    pdf.sub_header("REST Endpoints (GET)")
    get_rows = [
        ["/",                    "200 HTML", "War Room 5-tab institutional dashboard (dark mode)"],
        ["/api/status",          "200 JSON", "Full state snapshot: tick, market, plays, tape, halt, strategies"],
        ["/api/plays",           "200 JSON", "Top plays list (up to 15). Array of PlayScore dicts."],
        ["/api/tape",            "200 JSON", "Signal tape: last 20 events as text lines + full tape records"],
        ["/api/health",          "200 JSON", "Data health panel: badge (GREEN/AMBER/RED), pass/warn/fail counts"],
        ["/api/strategies",      "200 JSON", "RouterResult: regime_tag, active/inactive strategies, overlays, sizing_mode"],
        ["/api/overlays",        "200 JSON", "Overlay warnings only: list of human-readable risk warnings"],
        ["/api/session_status",  "200 JSON", "Per-job PASS/FAIL for all 4 PDF jobs from session_status.json"],
        ["/api/reports",         "200 JSON", "Today's PDF files + artifacts. Includes file sizes and download links."],
        ["/api/calibration",     "200 JSON", "R:R breakeven tables. Empty if no outcome data yet."],
        ["/api/halt",            "200 JSON", "GET current halt state: {halt: bool, changed_at: ISO, set_by: str}"],
        ["/api/ticker/{symbol}", "200 JSON", "Ticker drilldown: bars summary, feature values, gate pass/fail, delta-to-pass"],
        ["/ws",                  "WS",       "WebSocket: pushes full state snapshot every 15 seconds on connect"],
    ]
    pdf.two_col_table(
        ["Endpoint", "Response", "Description"],
        get_rows,
        col_widths=[46, 22, 134],
    )

    pdf.sub_header("REST Endpoints (POST/MUTATION)")
    post_rows = [
        ["POST /api/halt", "200 JSON", "Toggle halt. Body: {halt: true|false} or empty to toggle. "
                                       "Sets CommandCenterState.halt_new_signals. "
                                       "When halt=true, tick loop skips engine run, increments tick_count, returns."],
    ]
    pdf.two_col_table(
        ["Endpoint", "Response", "Description"],
        post_rows,
        col_widths=[36, 22, 144],
    )

    pdf.sub_header("State Snapshot Schema (/api/status)")
    snap_rows = [
        ["tick",              "int",  "Tick counter since startup"],
        ["last_tick",         "str",  "ISO8601 UTC timestamp of last tick completion"],
        ["run_id",            "str",  "8-char uppercase UUID assigned at container startup"],
        ["halt",              "bool", "True if halt_new_signals is active"],
        ["market.regime",     "str",  "Current regime tag (RISK_ON/RISK_OFF/NEUTRAL/etc.)"],
        ["market.session",    "str",  "Current session name (PRE_LSE/NYSE/OFF_HOURS/etc.)"],
        ["market.session_active","bool","True if inside active trading window (30s ticks)"],
        ["market.breadth_score","float","Fraction of plays with LONG direction (0.0-1.0)"],
        ["data_health.badge", "str",  "GREEN/AMBER/RED aggregated health badge"],
        ["data_health.pass",  "int",  "Count of tickers with full health pass"],
        ["data_health.warn",  "int",  "Count of tickers with warnings but passing"],
        ["data_health.fail",  "int",  "Count of tickers excluded due to data failure"],
        ["gate_funnel.tracked","int", "Total tickers evaluated in this tick"],
        ["gate_funnel.data_valid","int","Tickers passing DATA_HEALTH gate"],
        ["gate_funnel.signals_strict","int","Signals generated at strict thresholds"],
        ["gate_funnel.signals_fallback","int","Signals generated via fallback relaxation"],
        ["gate_funnel.total", "int",  "strict + fallback signals"],
        ["gate_funnel.blockers","list","Top gate names causing exclusions this tick"],
        ["portfolio.vol_target_state","str","NORMAL/REDUCED/DEFENSIVE from VOL_TARGET overlay"],
        ["strategies.regime_tag","str","Regime tag from Strategy Router"],
        ["strategies.active", "list", "Active StrategySpec objects [{tag, weight, why_active, ...}]"],
        ["strategies.inactive","list","Inactive StrategySpec objects [{tag, inactive_reason, ...}]"],
        ["strategies.overlay_tags","list","Active overlay names e.g. [VOL_TARGET_OVERLAY, TSM_TREND_OVERLAY]"],
        ["strategies.sizing_mode","str","NORMAL/REDUCED/DEFENSIVE from overlay post-processing"],
        ["strategies.kill_switch","bool","True if SHOCK regime or VIX > 40 threshold hit"],
        ["top_plays",         "list", "Up to 15 PlayScore dicts with all SignalCard v3.0 fields"],
        ["tape",              "list", "Last 20 tape events as text lines"],
        ["drought",           "str",  "null or drought report text if 0 signals after fallback"],
    ]
    pdf.two_col_table(
        ["Field Path", "Type", "Description"],
        snap_rows,
        col_widths=[50, 14, 138],
    )

    pdf.add_page()
    pdf.sub_header("WebSocket Push Protocol (/ws)")
    pdf.body_text(
        "On connect, the server immediately sends the full state snapshot. "
        "Subsequently, the server pushes a new snapshot every 15 seconds. "
        "Each message is a JSON object identical to the /api/status response. "
        "The client-side War Room JS parses the snapshot and updates all panels in-place "
        "without a full page reload. Disconnected clients are automatically cleaned up. "
        "The WebSocket endpoint handles multiple concurrent connections (set of asyncio.Queue). "
        "During halt, snapshots continue to be pushed but top_plays may not change."
    )

    pdf.sub_header("Ticker Drilldown (/api/ticker/{symbol})")
    drilldown_rows = [
        ["symbol",         "str",  "Ticker requested (e.g. QQQ3.L)"],
        ["status",         "str",  "OK / NO_DATA / STALE"],
        ["close",          "float","Latest close price"],
        ["atr_pct",        "float","ATR(14) as % of close"],
        ["rsi",            "float","RSI(14) last value"],
        ["rvol",           "float","Relative volume (current vol / 5d avg vol)"],
        ["n_bars",         "int",  "Number of OHLCV bars available"],
        ["bias",           "str",  "LONG/SHORT based on EMA alignment"],
        ["data_reliability","float","0-1 composite data quality score"],
        ["gates",          "dict", "Per-gate PASS/FAIL/RELAXED with reason strings"],
        ["delta_to_pass",  "list", "Human-readable deltas: what would admit this ticker"],
        ["factor_group",   "str",  "Factor cluster (e.g. semiconductors, nasdaq_beta_long)"],
        ["leverage",       "int",  "ETP leverage multiple (2x, 3x, or 5x)"],
        ["in_tape",        "bool", "Whether this ticker has an active signal on the tape"],
    ]
    pdf.two_col_table(
        ["Field", "Type", "Description"],
        drilldown_rows,
        col_widths=[40, 14, 148],
    )

    pdf.sub_header("Error Codes")
    err_rows = [
        ["400 Bad Request",  "Invalid symbol or malformed request body"],
        ["404 Not Found",    "Ticker not in ISA universe (not tracked)"],
        ["503 Service Unavailable", "Engine not yet initialised (starting up)"],
    ]
    pdf.two_col_table(
        ["HTTP Status", "Meaning"],
        err_rows,
        col_widths=[50, 152],
    )

    pdf.sub_header("Halt Mechanism: Full Flow")
    halt_flow = [
        ["1. Operator",       "POST /api/halt  {halt: true}"],
        ["2. server.py",      "Sets state.halt_new_signals = True; logs [HALT_TOGGLE] ACTIVE"],
        ["3. tick_loop.py",   "Checks if self._state.halt_new_signals before engine run"],
        ["4. If halted",      "Increments tick_count, updates last_tick, returns early (no engine run)"],
        ["5. State panel",    "portfolio.halt_active = True; vol_target_state = 'DEFENSIVE'"],
        ["6. Dashboard",      "Halt button turns red; regime banner shows HALT ACTIVE banner"],
        ["7. Resume",         "POST /api/halt  {halt: false}  -> engine resumes next tick (<=30s)"],
    ]
    pdf.two_col_table(
        ["Actor", "Action"],
        halt_flow,
        col_widths=[30, 172],
    )

    pdf.sub_header("DST-Aware Scheduler (delivery/dst_anchor.py)")
    dst_rows = [
        ["pdf_pre_lse",  "07:00 UK",           "Fixed: 1h before LSE open (always 08:00 UK)"],
        ["pdf_pre_nyse", "~13:00 UK",          "30 min before NYSE open (converted from 09:30 ET -> UK)"],
        ["pdf_eod",      "~21:30 UK",          "30 min after NYSE close (converted from 16:00 ET -> UK)"],
        ["pdf_mega",     "~22:00 UK",          "60 min after NYSE close — full project PDF"],
        ["Desync weeks", "UK/US differ 1-2w",  "When US and UK switch DST on different dates, offset shifts 1h; "
                                                "zoneinfo handles automatically — no manual offset needed"],
    ]
    pdf.two_col_table(
        ["Key", "Typical Time", "Rule"],
        dst_rows,
        col_widths=[28, 28, 146],
    )


def _render_config_reference(pdf: MegaPDF) -> None:
    """Section 20: Configuration Reference — key settings and their roles."""
    pdf.add_page()
    pdf.section_header("SECTION 20: CONFIGURATION REFERENCE (config/settings.yaml)")

    pdf.body_text(
        "All tunable parameters live in config/settings.yaml (993 lines). "
        "The engine reads these at startup via a Pydantic settings model. "
        "No restarts are required for parameters that are read on each tick, "
        "but most engine thresholds require a container restart. "
        "Below are the most operationally important configuration groups."
    )

    pdf.sub_header("Gate Thresholds (signal_engine section)")
    gate_cfg_rows = [
        ["strict_min_atr_pct",     "1.0",  "Minimum ATR% for strict signals (tradability gate)"],
        ["fallback_step4_atr_pct", "0.60", "Relaxed ATR% for fallback step-4 signals"],
        ["strict_min_rvol",        "0.80", "Minimum relative volume for strict signals"],
        ["fallback_min_rvol",      "0.55", "Minimum relative volume for fallback signals"],
        ["strict_min_rr",          "1.50", "Minimum net reward:risk ratio (strict)"],
        ["fallback_min_rr",        "1.20", "Minimum net reward:risk ratio (fallback)"],
        ["strict_momentum_min",    "0.55", "Momentum composite threshold (strict)"],
        ["fallback_momentum_min",  "0.40", "Momentum composite threshold (fallback)"],
        ["min_bars",               "14",   "Minimum OHLCV bars for full indicator quality"],
        ["short_window_min",       "7",    "Minimum bars for SHORT_WINDOW mode (adaptive)"],
        ["max_factor_cap",         "3",    "Maximum signals per factor cluster (concentration guard)"],
    ]
    pdf.two_col_table(
        ["Parameter", "Default", "Description"],
        gate_cfg_rows,
        col_widths=[56, 20, 126],
    )

    pdf.sub_header("Scoring Weights (scoring section)")
    score_cfg_rows = [
        ["w_momentum",    "0.30", "Weight for RSI + MACD + EMA composite"],
        ["w_volatility",  "0.20", "Weight for ATR% + BB width rank"],
        ["w_regime",      "0.15", "Weight for regime compatibility"],
        ["w_liquidity",   "0.15", "Weight for RVOL normalised score"],
        ["w_rr",          "0.10", "Weight for net R:R score"],
        ["w_quality",     "0.10", "Weight for ADX trend quality"],
        ["sum_of_weights","1.00", "Must sum to 1.0 (validated at startup)"],
        ["strategy_boost_per_unit","0.12","Score boost per unit of active strategy weight"],
        ["strategy_boost_cap","100.0","Maximum strategy_weighted_score after boost"],
        ["atr_stop_mult",  "1.5", "ATR multiple for stop-loss (1.5x ATR from entry)"],
        ["t1_rr",          "1.5", "Target 1 reward:risk ratio"],
        ["t2_rr",          "2.5", "Target 2 (runner) reward:risk ratio"],
    ]
    pdf.two_col_table(
        ["Parameter", "Default", "Description"],
        score_cfg_rows,
        col_widths=[56, 20, 126],
    )

    pdf.sub_header("Engine Execution (engine section)")
    eng_cfg_rows = [
        ["n_plays_min",       "5",       "Minimum plays to include in output (triggers drought if unmet)"],
        ["n_plays_max",       "15",      "Maximum plays to return per engine run"],
        ["period_active",     "1d",      "yfinance period during active sessions"],
        ["period_inactive",   "5d",      "yfinance period during off-hours"],
        ["interval",          "1h",      "yfinance candle interval (1-hour bars)"],
        ["use_extended",      "true",    "Include extended universe (19 tickers) vs core (12)"],
        ["write_artifacts",   "true",    "Write plays.json after each engine run"],
        ["fallback_steps",    "4",       "Number of fallback relaxation steps before drought"],
        ["drought_threshold", "0",       "Signal count below which drought is declared"],
    ]
    pdf.two_col_table(
        ["Parameter", "Default", "Description"],
        eng_cfg_rows,
        col_widths=[56, 20, 126],
    )

    pdf.sub_header("Strategy Router (strategy_router section)")
    router_cfg_rows = [
        ["vix_risk_off_threshold",  "30",   "VIX level triggering RISK_OFF regime (reduces strategy set)"],
        ["vix_kill_switch",         "40",   "VIX level triggering kill switch (all strategies halted)"],
        ["vol_target_vix_threshold","25",   "VIX level at which VOL_TARGET overlay sets sizing_hint=S"],
        ["corr_tighten_threshold",  "0.85", "Correlation level at which DISPERSION overlay reduces factor cap to 2"],
        ["opening_range_window",    "30",   "Minutes after session open for OPENING_RANGE_BREAKOUT active window"],
        ["lunch_chop_start",        "12:00","UK time: start of VWAP_MEAN_REVERT preferred window"],
        ["lunch_chop_end",          "13:30","UK time: end of VWAP_MEAN_REVERT preferred window"],
        ["power_hour_start",        "15:30","UK time: start of POWER_HOUR window (tighter exits)"],
    ]
    pdf.two_col_table(
        ["Parameter", "Default", "Description"],
        router_cfg_rows,
        col_widths=[56, 20, 126],
    )

    pdf.add_page()
    pdf.sub_header("Scheduler (APScheduler) Job Configuration")
    sched_rows = [
        ["PDF 1 (Pre-LSE Brief)",   "07:00 UK",  "cron",    "pdf_v2_momentum.py -- momentum opportunity brief"],
        ["PDF 2 (Pre-NYSE Brief)",  "13:30 UK",  "cron",    "pdf_v2_risk.py -- risk & structural brief"],
        ["PDF 3 (EOD Review)",      "22:00 UK",  "cron",    "pdf_v2_daily_review.py -- dual-session review"],
        ["Mega PDF",                "22:30 UK",  "cron",    "mega_report.py -- full project intelligence (this doc)"],
        ["Tick Loop",               "Continuous","30s/120s","Async loop: 30s active session, 120s off-hours"],
        ["DST Adjustment",          "Daily",     "startup", "dst_anchor.py recalculates UK fire times each day"],
    ]
    pdf.two_col_table(
        ["Job", "Time (UK)", "Type", "Module"],
        sched_rows,
        col_widths=[50, 24, 18, 110],
    )

    pdf.sub_header("Telegram Delivery Configuration")
    tg_rows = [
        ["TELEGRAM_BOT_TOKEN",  "env var", "Bot token from @BotFather. Required for all delivery."],
        ["TELEGRAM_CHAT_ID",    "env var", "Target chat or channel ID. Negative for channels."],
        ["alert_on_new_signal", "true",    "Send Telegram alert on new STRICT signal"],
        ["alert_on_dropout",    "true",    "Send alert when a STRICT signal drops off the tape"],
        ["alert_on_upgrade",    "true",    "Send alert when a WATCH-SIGNAL upgrades to STRICT"],
        ["pdf_send_mode",       "document","Send PDFs as documents (not photos) for full file access"],
        ["mega_caption_lines",  "4",       "Number of summary lines in Mega PDF Telegram caption"],
    ]
    pdf.two_col_table(
        ["Setting", "Default", "Description"],
        tg_rows,
        col_widths=[50, 20, 132],
    )

    pdf.sub_header("Docker / Deployment Configuration")
    docker_rows = [
        ["Container name",    "nzt48",          "Main trading engine container"],
        ["Dashboard name",    "nzt48-dashboard","Next.js :3001 frontend (separate container)"],
        ["API port",          "8765",           "FastAPI server (engine) -- exposed externally"],
        ["Dashboard port",    "3001",           "Next.js dashboard -- optional UI layer"],
        ["EC2 instance",      "t3.medium",      "AWS EC2 host (100.55.69.28 as of v8.0)"],
        ["Working dir",       "/app",           "Container working directory"],
        ["Log driver",        "json-file",      "Docker log driver; accessible via docker logs nzt48"],
        ["Restart policy",    "unless-stopped", "Auto-restart on crash; not restarted on docker stop"],
        ["Volume: artifacts", "/app/artifacts", "Persists daily signal artifacts across restarts"],
        ["Volume: data",      "/app/data",      "Persists reports, session_status.json, calibration data"],
        ["Volume: config",    "/app/config",    "settings.yaml (edit on host, restart container)"],
    ]
    pdf.two_col_table(
        ["Setting", "Value", "Notes"],
        docker_rows,
        col_widths=[44, 36, 122],
    )

    pdf.sub_header("Environment Variables (runtime secrets)")
    env_rows = [
        ["TELEGRAM_BOT_TOKEN", "Required", "Telegram bot API token"],
        ["TELEGRAM_CHAT_ID",   "Required", "Telegram delivery target"],
        ["NZT48_ENV",          "production", "Set to 'dev' to disable live Telegram sends"],
        ["LOG_LEVEL",          "INFO",     "Python logging level (DEBUG/INFO/WARNING)"],
        ["YFINANCE_CACHE_DIR", "/tmp/yf",  "yfinance request cache directory (optional)"],
    ]
    pdf.two_col_table(
        ["Variable", "Default", "Purpose"],
        env_rows,
        col_widths=[44, 28, 130],
    )


# ---------------------------------------------------------------------------
# Main report class
# ---------------------------------------------------------------------------

def _render_risk_governance_chapter(pdf: MegaPDF, engine_result=None) -> None:
    """Section 21: Risk Governance — RiskOfficer rules, decisions, audit trail."""
    pdf.add_page()
    pdf.section_header("SECTION 21: RISK GOVERNANCE — RISK OFFICER LAYER (v4.0)")

    pdf.body_text(
        "The v4.0 RiskOfficer is a post-router, pre-artifact governance layer that evaluates every "
        "SignalCard before it is committed to the artifact. It applies 6 independent rule modules "
        "using worst-wins aggregation: VETO overrides DOWNSIZE, DOWNSIZE overrides APPROVE. "
        "Rules are stateless (except CorrelationRule which is reset per session) and run in order."
    )

    pdf.sub_header("RiskOfficer Decision Constants")
    pdf.two_col_table(
        ["Decision", "Meaning", "Effect on Signal"],
        [
            ["APPROVE",  "All rules passed; signal quality acceptable",
             "Signal proceeds as-is with original sizing"],
            ["DOWNSIZE", "One or more rules fired a soft warning",
             "sizing_hint reduced: L->M->S; signal still emitted"],
            ["VETO",     "One or more rules fired a hard block",
             "Signal excluded from top_plays; final_sizing forced to S"],
        ],
        col_widths=[24, 85, 93],
    )

    pdf.sub_header("Rule Module Catalogue")
    pdf.two_col_table(
        ["Rule", "VETO Conditions", "DOWNSIZE Conditions", "Risk Score"],
        [
            ["VOL_SHOCK",       "VIX>35 AND ATR%>3.5",          "VIX>25 AND ATR%>2.5",              "0.80 / 0.50"],
            ["LIQUIDITY",       "RVOL<0.40 OR spread>30bps",    "RVOL<0.60 OR spread>22bps",        "0.75 / 0.45"],
            ["CORRELATION",     "factor_group count > max_cap",  "factor_group count == max_cap",    "0.60 / 0.40"],
            ["DRAWDOWN",        "consec_losses>=5 OR loss%>3",  "consec_losses>=3 OR loss%>1.5",    "0.90 / 0.55"],
            ["EVENT_WINDOW",    "Earnings within 1 day (stub)", "Earnings within 3 days (stub)",    "0.70 / 0.35"],
            ["DATA_RELIABILITY","reliability<0.50",              "reliability<0.70 or short+fallback","0.88 / 0.40"],
        ],
        col_widths=[30, 58, 60, 26],
    )

    pdf.sub_header("Worst-Wins Aggregation Algorithm")
    pdf.body_text(
        "The RiskOfficer iterates all 6 rules in order. Each rule returns either None (pass) "
        "or a RiskDecision. The officer accumulates the worst outcome seen so far: "
        "VETO cannot be downgraded once set. DOWNSIZE can be upgraded to VETO. "
        "risk_score is the max across all rules (0.0=safe, 1.0=hard reject). "
        "final_sizing uses the worst sizing seen: L->M (DOWNSIZE), M->S (DOWNSIZE), any->S (VETO). "
        "The CorrelationRule is stateful — it tracks factor_group counts across the current session "
        "and must be reset before each evaluate() call to avoid cross-session contamination."
    )

    pdf.sub_header("RiskOfficerReport Schema")
    pdf.two_col_table(
        ["Field", "Type", "Description"],
        [
            ["session",        "str",       "Session identifier (PRE_LSE, LSE, NYSE, EOD, MEGA_EOD)"],
            ["generated_at",   "ISO8601",   "UTC timestamp of when officer ran"],
            ["decisions",      "list[dict]","One entry per signal: ticker, direction, decision, reasons, sizing"],
            ["veto_count",     "int",       "Number of signals vetoed this session"],
            ["downsize_count", "int",       "Number of signals downsized this session"],
            ["approve_count",  "int",       "Number of signals approved as-is"],
        ],
        col_widths=[30, 25, 147],
    )

    pdf.sub_header("Artifact: risk_officer.json")
    pdf.body_text(
        "Written atomically (tmp -> fsync -> rename) to artifacts/YYYY-MM-DD/{session}/risk_officer.json "
        "immediately after the main plays.json artifact. The file contains the full RiskOfficerReport "
        "serialised via dataclasses.asdict(). Also exposed via GET /api/risk_officer endpoint in the "
        "War Room RISK COCKPIT tab. The report is never written if the engine run fails."
    )

    # Load latest risk_officer.json if available
    try:
        import json as _json
        from datetime import date as _date
        from pathlib import Path as _Path
        root = _Path(__file__).parent.parent
        today = str(_date.today())
        for sess in ("eod_institutional", "pre_nyse", "pre_lse", "mega_eod"):
            rp = root / "artifacts" / today / sess / "risk_officer.json"
            if rp.exists():
                ro_data = _json.loads(rp.read_text())
                decisions = ro_data.get("decisions", [])
                if decisions:
                    pdf.sub_header(f"Live RiskOfficer Decisions: {ro_data.get('session','?')} @ {ro_data.get('generated_at','?')[:19]}")
                    rows = []
                    for dec in decisions[:20]:
                        rows.append([
                            _ascii(dec.get("ticker","?"), 10),
                            _ascii(dec.get("decision","?"), 10),
                            _ascii(dec.get("original_sizing","?"), 5),
                            _ascii(dec.get("final_sizing","?"), 5),
                            f"{dec.get('risk_score',0):.3f}",
                            _ascii(" | ".join(dec.get("reasons",[])), 60),
                        ])
                    pdf.two_col_table(
                        ["Ticker", "Decision", "Orig", "Final", "RiskScore", "Reasons"],
                        rows,
                        col_widths=[22, 22, 12, 12, 18, 116],
                    )
                    pdf.body_text(
                        f"APPROVE={ro_data.get('approve_count',0)}  "
                        f"DOWNSIZE={ro_data.get('downsize_count',0)}  "
                        f"VETO={ro_data.get('veto_count',0)}"
                    )
                break
    except Exception:
        pdf.body_text("No live risk_officer.json found for today — data will populate after next engine run.", size=8)


def _render_strategy_allocation_chapter(pdf: MegaPDF, router_data: dict = None) -> None:
    """Section 22: Strategy Capital Allocation — regime-tilted weights."""
    pdf.add_page()
    pdf.section_header("SECTION 22: STRATEGY CAPITAL ALLOCATION (v4.0)")

    pdf.body_text(
        "The v4.0 Strategy Router adds a capital allocation layer on top of the strategy activity flags. "
        "Allocation weights are computed once per session using the current regime, VIX level, and "
        "time-of-day window. They sum to 1.0 across all active strategies. Each active signal "
        "inherits its strategy's allocation_weight, which feeds into final_rank_score."
    )

    pdf.sub_header("Allocation Weight Formula")
    pdf.body_text(
        "Base weights are set per strategy category: TREND=0.30, MEAN_REVERT=0.20, "
        "VOL_BREAKOUT=0.20, INTRADAY=0.15, EVENT=0.10, OVERLAY=0.05. "
        "Regime tilt is applied: trending regimes boost TREND by +30% and reduce MEAN_REVERT by -20%. "
        "Range-bound regimes flip this. High-VIX (>25) halves all weights and adds VOL_TARGET weight. "
        "Weights are then normalised to sum=1.0. Inactive strategies receive weight=0.0."
    )

    pdf.sub_header("final_rank_score Calculation")
    pdf.body_text(
        "final_rank_score = strategy_weighted_score * allocation_weight * risk_adjustment_factor\n"
        "  strategy_weighted_score: composite * (1 + sum(active_weights) * 0.15), capped at 100\n"
        "  allocation_weight: regime-tilted strategy weight (0.0–1.0, sums to 1.0 across strategies)\n"
        "  risk_adjustment_factor: 1.0 - risk_score from RiskOfficer (0.0–1.0)\n"
        "Signals are sorted by final_rank_score descending. Stars are derived from this score."
    )

    pdf.sub_header("Regime Tilt Logic")
    pdf.two_col_table(
        ["Regime", "TREND tilt", "MEAN_REVERT tilt", "Notes"],
        [
            ["TRENDING_UP_STRONG / TRENDING_UP_MOD",   "+30%", "-20%", "Momentum-first regime"],
            ["TRENDING_DOWN_STRONG / TRENDING_DOWN_MOD", "+30%", "-20%", "Short bias momentum"],
            ["RANGE_BOUND / CHOPPY",                    "-20%", "+25%", "Mean-reversion preferred"],
            ["HIGH_VOLATILITY / SHOCK",                 "halved", "halved", "VOL_TARGET overlay dominates"],
            ["NEUTRAL / UNKNOWN",                        "0%",   "0%",   "Base weights unchanged"],
        ],
        col_widths=[60, 18, 24, 100],
    )

    pdf.sub_header("StrategyAvailability — Event Strategies")
    pdf.body_text(
        "Event-driven strategies (EARNINGS_DRIFT, LOCKUP_PRESSURE, MERGER_ARB) are tracked via "
        "adapter stubs in signal_engine/adapters/. Each stub implements is_available() -> False until "
        "a real data feed is connected. The availability status is serialised into strategies.json "
        "and exposed in the War Room STRATEGY LAB tab. No fabricated signals are ever emitted."
    )
    pdf.two_col_table(
        ["Strategy",                  "Status",   "Required Config Key",         "Recommended Provider"],
        [
            ["EARNINGS_CONFIRMATION_DRIFT", "INACTIVE", "feeds.earnings_calendar_url", "alpha_vantage / openbb / tiingo"],
            ["IPO_LOCKUP_PRESSURE",         "INACTIVE", "feeds.lockup_calendar_url",   "manual_csv / stockanalysis.com"],
            ["MERGER_ARB_EVENT",            "INACTIVE", "feeds.deal_feed_url",         "dealreporter / manual_csv"],
        ],
        col_widths=[42, 18, 55, 87],
    )

    # Load live allocation data
    if router_data and "allocation_weights" in router_data:
        pdf.sub_header("Live Allocation Weights (from today's strategies.json)")
        alloc = router_data.get("allocation_weights", {})
        strat_avail = router_data.get("strategy_availability", [])
        if alloc:
            rows = sorted(alloc.items(), key=lambda x: -x[1])
            pdf.two_col_table(
                ["Strategy", "Allocation Weight", "Effective %"],
                [[_ascii(k, 40), f"{v:.4f}", f"{v*100:.1f}%"] for k, v in rows],
                col_widths=[90, 35, 77],
            )
        if strat_avail:
            pdf.sub_header("Event Strategy Status (live)")
            pdf.two_col_table(
                ["Name", "Status", "Reason"],
                [[_ascii(ev.get("name","?"),30), ev.get("status","?"), _ascii(ev.get("reason","--"),80)]
                 for ev in strat_avail],
                col_widths=[44, 18, 140],
            )
    else:
        pdf.body_text("No live strategies.json found for today. Data populates after next engine run.", size=8)


def _render_command_center_2_chapter(pdf: MegaPDF) -> None:
    """Section 23: Command Center 2.0 War Room documentation."""
    pdf.add_page()
    pdf.section_header("SECTION 23: COMMAND CENTER 2.0 — WAR ROOM DOCUMENTATION (v4.0)")

    pdf.body_text(
        "The v4.0 War Room replaces the v3.0 5-tab Command Center with an upgraded institutional "
        "dashboard. All tabs are served from the same FastAPI server on port 8765. The UI is pure "
        "inline HTML/CSS/JS — no build step, no external dependencies. WebSocket push delivers "
        "state snapshots every 15 seconds. All rendering is done client-side via vanilla JS."
    )

    pdf.sub_header("Tab Architecture (5 Tabs)")
    pdf.two_col_table(
        ["Tab", "Key ID", "Content", "Data Source"],
        [
            ["WAR ROOM",        "warroom",  "Top plays, signal tape, overlay warnings, stats, diff box",
             "/api/state (WS push)"],
            ["RISK COCKPIT",    "risk",     "Factor exposure, vol state, RiskOfficer decisions table, guardrails, halt toggle, readiness checklist",
             "/api/risk_officer + state"],
            ["EXPLAINABILITY",  "explain",  "Gate funnel visual, top blockers, data health table, Drought Cockpit (closest misses + knobs)",
             "/api/drought + state"],
            ["STRATEGY LAB",    "stratlab", "Strategy roster + allocation weights, event strategy availability, calibration tables, time-of-day windows",
             "/api/strategies + /api/allocation + /api/calibration"],
            ["REPORTS & AUDIT", "reports",  "Session run status (v4.0 extended), artifact inventory (plays/strategies/risk/drought), PDF links",
             "/api/session_status + /api/artifacts + /api/reports"],
        ],
        col_widths=[28, 20, 86, 68],
    )

    pdf.sub_header("v4.0 New Endpoints")
    pdf.two_col_table(
        ["Endpoint", "Returns", "Description"],
        [
            ["/api/allocation",    "AllocationPanel dict",     "Regime-tilted allocation weights per strategy + event availability"],
            ["/api/risk_officer",  "RiskOfficerReport dict",   "Latest RiskOfficer decisions: approve/downsize/veto per signal"],
            ["/api/drought",       "DroughtCockpitPanel dict", "Closest misses (by delta-to-pass) + bounded recommended knobs"],
            ["/api/artifacts",     "ArtifactInventory dict",   "Today's artifact inventory: plays/strategies/risk_officer/drought.json per session"],
        ],
        col_widths=[36, 36, 130],
    )

    pdf.sub_header("Drought Cockpit Panel")
    pdf.body_text(
        "When no signals pass strict gates, the Drought Cockpit activates. It computes ClosestMiss "
        "records for tickers that came closest to admission (sorted by delta-to-pass ascending). "
        "The RecommendedKnob list provides bounded safe parameter adjustments — the DataHealth "
        "gate is NEVER bypassed. Each knob change is bounded by PARAM_BOUNDS and carries a "
        "tradeoff note. The Drought Cockpit is exposed on the EXPLAINABILITY tab with "
        "separate tables for closest misses and knob recommendations."
    )

    pdf.sub_header("Halt Toggle")
    pdf.body_text(
        "POST /api/halt toggles the halt_new_signals flag. When halt=true, the tick loop increments "
        "tick_count but skips the engine run and emits no new signals. This is the primary manual "
        "risk intervention. The War Room RISK COCKPIT tab shows a persistent HALT ACTIVE banner "
        "when engaged. The halt state is in-memory only — it resets on container restart."
    )

    pdf.sub_header("Keyboard Shortcuts")
    pdf.two_col_table(
        ["Key", "Action"],
        [
            ["1",     "Switch to WAR ROOM tab"],
            ["2",     "Switch to RISK COCKPIT tab — loads fresh RiskOfficer data"],
            ["3",     "Switch to EXPLAINABILITY tab — loads Drought Cockpit"],
            ["4",     "Switch to STRATEGY LAB tab — loads strategy roster + allocation"],
            ["5",     "Switch to REPORTS & AUDIT tab — loads session status + artifacts"],
            ["r",     "Force full state refresh (GET /api/state)"],
            ["f",     "Focus play filter search box in WAR ROOM"],
            ["d",     "Switch to EXPLAINABILITY and scroll to drought flag banner"],
            ["/",     "Focus play filter search box (same as f)"],
        ],
        col_widths=[12, 190],
    )

    pdf.sub_header("Session Readiness Checklist (RISK COCKPIT)")
    pdf.body_text(
        "The RISK COCKPIT tab includes a live readiness checklist that evaluates: "
        "(1) Data health badge is GREEN or AMBER; "
        "(2) Engine has ticked at least once (tick > 0); "
        "(3) Regime has been classified (not UNKNOWN); "
        "(4) Halt is not active; "
        "(5) No signal drought. "
        "All checks are derived from the live state snapshot. They update with every WS push."
    )


def _render_evidence_tables_chapter(pdf: MegaPDF, engine_result=None, isa_data=None) -> None:
    """Section 24: Evidence Tables — signal quality, execution analysis, data reliability."""
    pdf.add_page()
    pdf.section_header("SECTION 24: EVIDENCE TABLES — SIGNAL QUALITY & EXECUTION ANALYSIS (v4.0)")

    pdf.body_text(
        "This section aggregates evidence tables from the v4.0 pipeline: execution cost modelling, "
        "data reliability scoring, signal card schema validation, and the learning loop readiness "
        "status. All tables are populated from live data where available."
    )

    # Execution cost model
    pdf.sub_header("Execution Cost Model (spread_proxy_bps by instrument)")
    cost_rows = [
        ["QQQ3.L",   "20", "ETF 3x leverage", "40",  "WATCH (20-30bps)"],
        ["3LUS.L",   "20", "ETF 3x leverage", "40",  "WATCH"],
        ["NVD3.L",   "25", "ETF 3x leverage", "50",  "WATCH"],
        ["TSL3.L",   "30", "ETF 3x leverage", "60",  "VETO (>30bps)"],
        ["GPT3.L",   "22", "ETF 3x leverage", "44",  "WATCH"],
        ["QQQ5.L",   "35", "ETF 5x leverage", "70",  "VETO"],
        ["SP5L.L",   "18", "ETF 5x leverage", "36",  "PASS (<22bps)"],
        ["3SEM.L",   "28", "ETF 3x leverage", "56",  "WATCH"],
        ["TSM3.L",   "15", "ETF 3x leverage", "30",  "PASS"],
        ["MU2.L",    "12", "ETF 2x leverage", "24",  "PASS"],
        ["QQQS.L",   "14", "Short ETF",       "28",  "PASS"],
        ["3USS.L",   "18", "Short ETF",       "36",  "PASS"],
    ]
    pdf.two_col_table(
        ["Ticker", "Spread (bps)", "Type", "RT Cost (bps)", "Spread Gate"],
        cost_rows,
        col_widths=[22, 25, 30, 30, 95],
    )

    pdf.body_text(
        "Round-trip cost = 2 * spread_proxy_bps. Spread gate rules: "
        "spread<=22bps -> PASS; 22<spread<=30bps -> WATCH (net R:R shown); spread>30bps -> VETO. "
        "net_rr_after_costs = (T1 - Entry - rt_cost) / (Entry - Stop). "
        "If net_rr_after_costs < 0.5, execution plan sets do_not_trade=True."
    )

    # Data reliability scoring
    pdf.sub_header("Data Reliability Score Components")
    pdf.two_col_table(
        ["Condition", "Penalty Applied", "Rule Triggered"],
        [
            ["bars_available < 7",                "FAIL gate entirely",          "gate_min_bars FAIL"],
            ["7 <= bars_available < 14",          "SHORT_WINDOW tag, no penalty", "gate_min_bars PASS (short)"],
            ["bars_available < 10 + SHORT_WINDOW","reliability -=0.35",          "DataReliabilityRule DOWNSIZE"],
            ["fallback_step > 2 + SHORT_WINDOW",  "reliability -=0.50",          "DataReliabilityRule DOWNSIZE"],
            ["reliability < 0.70",                "sizing_hint reduced to S",     "DataReliabilityRule DOWNSIZE"],
            ["reliability < 0.50",                "signal VETOED",                "DataReliabilityRule VETO"],
            ["IBKR source unavailable",           "penalty +0.05",               "ValidatorSource flag"],
        ],
        col_widths=[62, 42, 98],
    )

    # SignalCard v4.0 schema
    pdf.sub_header("SignalCard v4.0 Schema (new fields)")
    pdf.two_col_table(
        ["Field", "Type", "Default", "Description"],
        [
            ["risk_officer_decision",   "str",         "APPROVE",   "APPROVE / DOWNSIZE / VETO from RiskOfficer"],
            ["risk_officer_reasons",    "list[str]",   "[]",        "Human-readable reasons list from all fired rules"],
            ["risk_adjustment_factor",  "float",       "1.0",       "1.0 - risk_score; multiplied into final_rank_score"],
            ["execution_plan",          "dict",        "{}",        "ExecutionPlan: order_type, limit_price, spread_gate, net_rr, cancel_conditions"],
            ["allocation_weight",       "float",       "0.0",       "Strategy allocation weight (regime-tilted, sums to 1.0)"],
            ["final_rank_score",        "float",       "0.0",       "strategy_weighted * allocation * risk_adj; primary sort key"],
            ["bars_available",          "int",         "14",        "Actual bars used in indicator computation"],
            ["indicator_window_used",   "int",         "14",        "Window size applied (may be < 14 in SHORT_WINDOW mode)"],
            ["reliability_penalty",     "float",       "0.0",       "Cumulative reliability deduction (0.0–0.35)"],
        ],
        col_widths=[38, 22, 16, 126],
    )

    # Learning loop status
    pdf.sub_header("Learning Loop Readiness (data_hub / execution / learning modules)")
    pdf.two_col_table(
        ["Module", "File", "Status", "Notes"],
        [
            ["DataHub",        "data_hub/hub.py",              "READY (stub sources)", "IBKR stub; yfinance fallback active"],
            ["YFinanceSource", "data_hub/sources/yfinance_source.py", "ACTIVE",       "IS_TRUTH=False; fallback only"],
            ["IBKRSource",     "data_hub/sources/ibkr_source.py",     "STUB",         "IS_AVAILABLE=False until IBKR wired"],
            ["ValidatorSource","data_hub/sources/validator_source.py","STUB",         "Adds 0.05 penalty when unavailable"],
            ["ExecutionPlanner","execution/planner.py",         "READY",              "ExecutionPlan per signal card"],
            ["CostModel",      "execution/cost_model.py",       "ACTIVE",             "Spread table + net R:R calculation"],
            ["AttributionEngine","learning/attribution.py",    "READY (no data)",    "TradeStore empty; NEEDS_DATA status"],
            ["CalibrationEngine","learning/calibration.py",    "READY (no data)",    "Min 20 samples; bounded suggestions"],
            ["Guardrails",     "learning/guardrails.py",       "ACTIVE",             "daily_loss, consec_loss, drawdown_pct"],
        ],
        col_widths=[30, 52, 20, 100],
    )

    pdf.add_page()
    pdf.section_header("SECTION 24 (continued): SESSION STATUS v4.0 SCHEMA")

    pdf.body_text(
        "The v4.0 SessionRunStatus adds 7 new fields to the base PASS/FAIL/PENDING schema. "
        "All new fields are backward-compatible (keyword-only with defaults)."
    )
    pdf.two_col_table(
        ["Field", "Type", "Default", "Description"],
        [
            ["artifact_paths",         "list[str]", "[]",    "Paths to all artifacts written this session"],
            ["pdf_path",               "str",       "''",    "Full path to the PDF written for this session"],
            ["signals_strict_count",   "int",       "0",     "Number of strict (non-fallback) signals generated"],
            ["signals_fallback_count", "int",       "0",     "Number of fallback-gate signals generated"],
            ["drought_flag",           "bool",      "False", "True if zero signals in any tier this session"],
            ["top_blockers",           "list[str]", "[]",    "Top 3 gate blockers by frequency"],
            ["generated_at_uk",        "str",       "''",    "UK-local timestamp when session PDF was written"],
        ],
        col_widths=[38, 22, 16, 126],
    )

    pdf.sub_header("Artifact Inventory Schema")
    pdf.body_text(
        "Each session now writes up to 4 JSON artifacts to artifacts/YYYY-MM-DD/{session}/: "
        "plays.json (main signal output), strategies.json (RouterResult), "
        "risk_officer.json (RiskOfficerReport), drought.json (DroughtPackage). "
        "All writes are atomic (tmp -> fsync -> rename). The /api/artifacts endpoint "
        "returns existence flags and sizes for all 4 files per session."
    )

    # Load drought.json if available
    try:
        import json as _json
        from datetime import date as _date
        from pathlib import Path as _Path
        root = _Path(__file__).parent.parent
        today = str(_date.today())
        for sess in ("eod_institutional", "pre_nyse", "pre_lse", "mega_eod"):
            dp = root / "artifacts" / today / sess / "drought.json"
            if dp.exists():
                drought_data = _json.loads(dp.read_text())
                misses = drought_data.get("closest_misses", [])
                if misses:
                    pdf.sub_header(f"Live Closest Misses ({sess})")
                    rows = []
                    for m in misses[:10]:
                        rows.append([
                            _ascii(m.get("ticker","?"), 12),
                            _ascii(m.get("failed_gate","?"), 20),
                            f"{m.get('observed_value',0):.3f}",
                            f"{m.get('required_value',0):.3f}",
                            f"{m.get('delta',0):.3f}",
                            "YES" if m.get("fallback_admits") else "NO",
                            _ascii(m.get("safest_knob","--"), 40),
                        ])
                    pdf.two_col_table(
                        ["Ticker", "Failed Gate", "Observed", "Required", "Delta", "Fallback?", "Safest Knob"],
                        rows,
                        col_widths=[22, 36, 18, 18, 18, 14, 76],
                    )
                break
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Section 25: Sector Rotation Analysis
# ---------------------------------------------------------------------------

def _render_sector_rotation_chapter(pdf: MegaPDF) -> None:
    """Section 25: Sector Rotation Analysis with ISA_FACTOR_GROUPS breakdown."""
    pdf.add_page()
    pdf.section_header("SECTION 25: SECTOR ROTATION ANALYSIS",
                       sub=f"{len(_ISA_FACTOR_GROUPS_NESTED)} sectors tracked")

    pdf.body_text(
        "Sector rotation analysis ranks every ISA_FACTOR_GROUP by composite momentum, "
        "capital inflow, and relative strength.  The radar identifies sectors in INFLOW "
        "(accelerating capital), OUTFLOW (decelerating), and NEUTRAL states.  Instruments "
        "within each sector are listed for cross-reference with the play archive."
    )
    pdf.ln(2)

    # Build sector_rankings list from ISA_FACTOR_GROUPS and any live data
    sector_rankings: list[dict] = []
    try:
        # Attempt to load live sector rotation data from latest artifacts
        import json as _json
        from datetime import date as _date
        root = Path(__file__).parent.parent
        today = str(_date.today())
        sr_data = None
        for sess in ("eod_institutional", "pre_nyse", "pre_lse", "mega_eod"):
            sr_path = root / "artifacts" / today / sess / "sector_rotation.json"
            if sr_path.exists():
                sr_data = _json.loads(sr_path.read_text())
                break
        if sr_data and isinstance(sr_data, list):
            sector_rankings = sr_data
    except Exception:
        pass

    if not sector_rankings:
        # Build static rankings from ISA_FACTOR_GROUPS as fallback
        for group, tickers in _ISA_FACTOR_GROUPS_NESTED.items():
            sector_rankings.append({
                "sector": group,
                "composite_score": 0,
                "rotation_signal": "NEUTRAL",
                "leadership_status": "FLAT",
                "momentum_score": 0,
                "capital_inflow_score": 0,
                "best_instrument": tickers[0] if tickers else "",
                "instruments": tickers,
            })

    # Render the sector rotation table from pdf_shared
    render_sector_rotation_table(pdf, sector_rankings)

    pdf.ln(2)

    # Render inflow alerts
    render_sector_inflow_alerts(pdf, sector_rankings)

    pdf.ln(2)

    # Instruments per sector breakdown
    pdf.sub_header("Instruments Per Sector Group")
    sector_rows = []
    for group, tickers in _ISA_FACTOR_GROUPS_NESTED.items():
        ticker_list = ", ".join(tickers[:6])
        extra = f" (+{len(tickers) - 6})" if len(tickers) > 6 else ""
        sector_rows.append([
            group,
            str(len(tickers)),
            ticker_list + extra,
        ])
    pdf.two_col_table(
        ["Sector Group", "Count", "Instruments"],
        sector_rows,
        col_widths=[40, 14, 148],
    )

    pdf.ln(2)
    pdf.body_text(
        f"Total unique instruments across all groups: {len(_FACTOR_GROUPS)}. "
        f"EXTENDED_UNIVERSE: {len(_ISA_EXTENDED)} tradable tickers. "
        f"SECTOR_RADAR_UNIVERSE: {len(SECTOR_RADAR_UNIVERSE)} monitoring-only tickers. "
        f"FULL_SCAN_UNIVERSE: {len(FULL_SCAN_UNIVERSE)} total (deduplicated)."
    )


# ---------------------------------------------------------------------------
# Section 26: Gate Funnel -- Near Miss Analysis
# ---------------------------------------------------------------------------

def _render_near_miss_chapter(pdf: MegaPDF) -> None:
    """Section 26: Gate Funnel Near-Miss Analysis -- instruments that almost qualified."""
    pdf.add_page()
    pdf.section_header("SECTION 26: GATE FUNNEL -- NEAR MISS ANALYSIS",
                       sub="Closest failures & parameter tuning hints")

    pdf.body_text(
        "Near-miss analysis identifies instruments that failed a single gate by a narrow "
        "margin.  These are tomorrow's potential TRADE candidates if conditions shift.  "
        "The drought.json artifact records each miss with the specific gate that failed, "
        "the observed vs required values, and the fallback step that would admit them."
    )
    pdf.ln(2)

    # Load drought.json from latest artifacts
    closest_misses: list[dict] = []
    drought_data: dict = {}
    try:
        import json as _json
        from datetime import date as _date
        root = Path(__file__).parent.parent
        today = str(_date.today())
        for sess in ("eod_institutional", "pre_nyse", "pre_lse", "mega_eod"):
            dp = root / "artifacts" / today / sess / "drought.json"
            if dp.exists():
                drought_data = _json.loads(dp.read_text())
                closest_misses = drought_data.get("closest_misses", [])
                if closest_misses:
                    pdf.sub_header(f"Near-Miss Candidates (source: {sess})")
                    break
    except Exception as e:
        pdf.set_font("Helvetica", "I", 7)
        pdf.set_text_color(*C_GREY)
        pdf.cell(0, 5, f"  Drought artifact not available. ({type(e).__name__})")
        pdf.ln(6)

    if closest_misses:
        # Use the shared renderer for the institutional table
        render_near_miss_table(pdf, closest_misses, max_rows=12)
        pdf.ln(2)

        # Additional detail: recommended parameter adjustments
        param_adjustments = drought_data.get("recommended_adjustments", [])
        if param_adjustments:
            pdf.sub_header("Recommended Parameter Adjustments")
            adj_rows = []
            for adj in param_adjustments[:8]:
                adj_rows.append([
                    _ascii(str(adj.get("gate", "?")), 25),
                    _ascii(str(adj.get("current_value", "?")), 15),
                    _ascii(str(adj.get("suggested_value", "?")), 15),
                    _ascii(str(adj.get("impact", "?")), 50),
                ])
            pdf.two_col_table(
                ["Gate", "Current", "Suggested", "Expected Impact"],
                adj_rows,
                col_widths=[40, 25, 25, 112],
            )

        # Drought statistics summary
        drought_summary = drought_data.get("summary", {})
        if drought_summary:
            pdf.sub_header("Drought Summary Statistics")
            pdf.kv_row([
                ("Total Scanned", str(drought_summary.get("total_scanned", "?")), C_DARK_BLUE),
                ("Passed All Gates", str(drought_summary.get("passed_all", "?")), C_GREEN),
                ("Near Misses", str(drought_summary.get("near_misses", "?")), C_AMBER),
                ("Hard Fails", str(drought_summary.get("hard_fails", "?")), C_RED),
            ])
    else:
        pdf.info_box(
            "No Near-Miss Data Available",
            [
                "The drought.json artifact has not been generated for today.",
                "This file is written by the signal engine after each session scan.",
                "Once the engine runs, near-miss candidates will appear here.",
            ],
            bg=(250, 245, 230), title_color=C_AMBER,
        )


class MegaReport:
    """Full project intelligence Mega PDF generator."""

    OUTPUT_DIR = Path(_ROOT) / "data" / "reports"

    def __init__(self) -> None:
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #

    def generate(self, session: str = "MEGA_EOD") -> Path:
        """Build and save the Mega PDF. Returns the file path."""
        now  = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H%M%S")
        run_time = now.strftime("%Y-%m-%d %H:%M")
        fname = f"NZT48_MegaReport_{now.strftime('%Y%m%d')}_{time_str}.pdf"
        out_path = self.OUTPUT_DIR / fname

        logger.info("[MEGA] Building Mega PDF: %s", fname)

        # Fetch all data upfront
        logger.info("[MEGA] Fetching market snapshot...")
        market_data = _fetch_market_snapshot()

        logger.info("[MEGA] Fetching ISA universe data...")
        isa_data = _fetch_isa_universe_data()

        logger.info("[MEGA] Running signal engine...")
        engine_result = _run_engine_fresh()

        logger.info("[MEGA] Loading session artifacts...")
        session_plays = _load_session_plays(
            ["pre_lse", "pre_nyse", "eod_institutional"]
        )

        # Load router data (today's strategies artifact if it exists)
        router_data: Optional[dict] = None
        try:
            from datetime import timedelta
            for delta in (0, 1):
                d_try = date.today() - timedelta(days=delta)
                strat_path = (
                    Path(__file__).parent.parent / "artifacts"
                    / str(d_try) / "mega_eod" / "strategies.json"
                )
                if not strat_path.exists():
                    # Also check other sessions
                    for sess_key in ("eod_institutional", "pre_nyse", "pre_lse"):
                        strat_path = (
                            Path(__file__).parent.parent / "artifacts"
                            / str(d_try) / sess_key / "strategies.json"
                        )
                        if strat_path.exists():
                            break
                if strat_path.exists():
                    import json as _json
                    router_data = _json.loads(strat_path.read_text())
                    break
        except Exception as exc:
            logger.debug("[MEGA] router_data load failed: %s", exc)

        # Build PDF
        pdf = MegaPDF(report_date=date_str)
        pdf.set_title(f"NZT-48 Mega Report {date_str}")
        pdf.set_author("NZT-48 v8.0 Apex Predator Engine")

        manifest = RunManifest(
            universe_name="EXTENDED_UNIVERSE",
            universe_count=len(_ISA_EXTENDED),
            strategy_version="V8.0-MEGA",
        )
        self._manifest = manifest

        # Page 1: Cover
        pdf.add_page()
        _render_cover(pdf, date_str, run_time)
        render_manifest_strip(pdf, self._manifest)

        # Page 2: TOC (rendered first; TOC entries collected during section renders)
        _render_toc(pdf)

        # Core sections (original 12)
        _render_executive_brief(pdf, market_data, engine_result, date_str)
        _render_architecture(pdf)
        _render_gate_funnel(pdf, engine_result)
        _render_data_quality(pdf, isa_data)
        _render_strategy_design(pdf)
        _render_scoring_explainability(pdf, engine_result)
        _render_session_play_archive(pdf, session_plays, engine_result)
        _render_command_center(pdf)
        _render_factor_regime(pdf, isa_data)
        _render_performance_calibration(pdf)
        _render_operational_status(pdf)
        _render_roadmap(pdf)

        # v3.0 new sections (add ~30-40 more pages)
        _render_strategy_router_chapter(pdf, router_data)
        _render_session_status_chapter(pdf)
        _render_calibration_chapter(pdf)
        _render_repo_inventory(pdf)
        _render_scoring_sensitivity(pdf)
        _render_master_spec_alignment(pdf)
        _render_deep_diagnostic(pdf, engine_result, isa_data)
        _render_api_contract(pdf)
        _render_config_reference(pdf)

        # v4.0 new sections (add ~8-12 more pages)
        _render_risk_governance_chapter(pdf, engine_result)
        _render_strategy_allocation_chapter(pdf, router_data)
        _render_command_center_2_chapter(pdf)
        _render_evidence_tables_chapter(pdf, engine_result, isa_data)

        # v5.0 new sections (sector rotation + near-miss analysis)
        _render_sector_rotation_chapter(pdf)
        _render_near_miss_chapter(pdf)

        pdf.output(str(out_path))
        n_pages = pdf.page_no()
        logger.info("[MEGA] PDF complete: %s (%d pages, %.1f KB)",
                    fname, n_pages, out_path.stat().st_size / 1024)
        if n_pages < 40:
            logger.warning("[MEGA] Page count %d < 40 target — Deep Diagnostic sections are present", n_pages)
        return out_path

    # ------------------------------------------------------------------ #

    def send_via_telegram(self, pdf_path: Path) -> bool:
        """Send the Mega PDF via Telegram as a document."""
        try:
            import asyncio
            from pathlib import Path as _Path

            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
            if not token or not chat_id:
                logger.warning("[MEGA] Telegram credentials not set")
                return False

            import telegram
            bot = telegram.Bot(token=token)

            caption = (
                f"NZT-48 MEGA REPORT\n"
                f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
                f"Size: {pdf_path.stat().st_size // 1024} KB\n"
                f"Full system analysis: architecture, signals, calibration, roadmap."
            )

            async def _send():
                with open(pdf_path, "rb") as f:
                    await bot.send_document(
                        chat_id=chat_id,
                        document=f,
                        filename=pdf_path.name,
                        caption=caption,
                    )
                return True

            return asyncio.run(_send())

        except Exception as exc:
            logger.error("[MEGA] Telegram send failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging as _log
    _log.basicConfig(level=_log.INFO,
                     format="%(asctime)s %(levelname)s %(name)s %(message)s")
    report = MegaReport()
    path = report.generate()
    print(f"Mega PDF saved: {path}")
    print(f"Size: {path.stat().st_size / 1024:.0f} KB")
