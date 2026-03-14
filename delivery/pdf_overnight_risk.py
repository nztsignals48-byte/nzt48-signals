"""
NZT-48 V8.0 -- PDF: Overnight Risk & Macro Tape
=================================================
Generated daily at 06:30 UK time (pre-market).

Contents:
  - Overnight futures snapshot (ES, NQ, FTSE placeholders)
  - Asia session close (Nikkei, HSI, ASX placeholders)
  - Macro calendar for the day ahead
  - VIX term structure assessment
  - Risk-on / Risk-off composite assessment
  - ISA portfolio implications

Output: data/reports/NZT48_OVERNIGHT_{date}_{time}.pdf
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from fpdf import FPDF
except ImportError:
    raise ImportError("fpdf2 is required: pip install fpdf2")

try:
    import yfinance as yf
    _HAS_YF = True
except ImportError:
    _HAS_YF = False

try:
    import numpy as np
    _HAS_NP = True
except ImportError:
    _HAS_NP = False

# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("nzt48.pdf_overnight_risk")

# ---------------------------------------------------------------------------
# Colour palette -- dark institutional
# ---------------------------------------------------------------------------
C_BG          = (10,  22,  40)    # #0a1628 dark navy
C_PANEL       = (16,  30,  54)    # slightly lighter panel
C_ROW_ALT     = (12,  26,  46)    # alternating row
C_GOLD        = (212, 160, 23)    # #d4a017 gold headers
C_WHITE       = (255, 255, 255)   # #ffffff
C_LIGHT_GRAY  = (192, 192, 192)   # #c0c0c0
C_GREEN       = (0,   204, 102)   # #00cc66 positive
C_RED         = (255, 68,  68)    # #ff4444 negative
C_ORANGE      = (255, 136, 0)     # #ff8800 warning
C_MUTED       = (140, 140, 160)   # muted text
C_ACCENT      = (80,  80,  120)   # border accent
C_HEADER_BAR  = (20,  36,  64)    # section header background

# ---------------------------------------------------------------------------
# Strategy metadata
# ---------------------------------------------------------------------------
_STRATEGY_VERSION = "V8.0"
_BAR_RESOLUTION   = "1d"
_DATA_VENDOR      = "yfinance"

# ---------------------------------------------------------------------------
# ISA Universe
# ---------------------------------------------------------------------------
ISA_UNIVERSE: List[str] = [
    "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L",
    "TSL3.L", "TSM3.L", "MU2.L",  "QQQS.L", "3USS.L",
    "QQQ5.L", "SP5L.L",
]

# ---------------------------------------------------------------------------
# Futures & macro reference tickers
# ---------------------------------------------------------------------------
FUTURES_TICKERS: Dict[str, str] = {
    "ES=F":   "E-mini S&P 500",
    "NQ=F":   "E-mini Nasdaq 100",
    "Z=F":    "FTSE 100 Futures",
}

ASIA_TICKERS: Dict[str, str] = {
    "^N225":  "Nikkei 225",
    "^HSI":   "Hang Seng Index",
    "^AXJO":  "ASX 200",
}

VIX_TICKER = "^VIX"

# ---------------------------------------------------------------------------
# PDF helper functions
# ---------------------------------------------------------------------------

def _dark_bg(pdf: FPDF) -> None:
    """Paint the whole page with dark navy background."""
    pdf.set_fill_color(*C_BG)
    pdf.rect(0, 0, 210, 297, "F")


def _header_bar(pdf: FPDF, title: str, subtitle: str) -> None:
    """Draw branded page header with dark panel and gold accent."""
    pdf.set_fill_color(*C_PANEL)
    pdf.rect(0, 0, 210, 22, "F")
    # Gold accent line
    pdf.set_fill_color(*C_GOLD)
    pdf.rect(0, 22, 210, 1.2, "F")
    # Title
    pdf.set_xy(6, 4)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*C_GOLD)
    pdf.cell(0, 7, title)
    # Subtitle
    pdf.set_xy(6, 13)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*C_MUTED)
    pdf.cell(0, 5, subtitle)


def _section_title(pdf: FPDF, y: float, label: str) -> float:
    """Draw a section title bar. Returns y after the bar."""
    pdf.set_fill_color(*C_HEADER_BAR)
    pdf.rect(4, y, 202, 7, "F")
    pdf.set_xy(6, y + 0.8)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*C_GOLD)
    pdf.cell(0, 5.5, label)
    return y + 8.5


def _kv_line(pdf: FPDF, y: float, label: str, value: str,
             val_color: tuple = C_WHITE, x_start: float = 8,
             label_w: float = 50) -> float:
    """Draw a key-value line. Returns y after the line."""
    pdf.set_xy(x_start, y)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*C_MUTED)
    pdf.cell(label_w, 4.5, label)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*val_color)
    pdf.cell(60, 4.5, value)
    return y + 5.5


def _table_header(pdf: FPDF, y: float, cols: List[tuple],
                  font_size: int = 7) -> float:
    """Draw a table header row. cols = [(label, width, align)]. Returns y after."""
    pdf.set_fill_color(*C_PANEL)
    pdf.rect(4, y, 202, 6, "F")
    pdf.set_xy(4, y + 0.5)
    pdf.set_font("Helvetica", "B", font_size)
    pdf.set_text_color(*C_GOLD)
    x = 6
    for label, w, align in cols:
        pdf.set_xy(x, y + 0.5)
        pdf.cell(w, 5, label, align=align)
        x += w
    return y + 7


def _table_row(pdf: FPDF, y: float, values: List[tuple],
               alt: bool = False, font_size: int = 7) -> float:
    """Draw a table data row. values = [(text, width, align, color)]. Returns y after."""
    fill = C_ROW_ALT if alt else C_BG
    pdf.set_fill_color(*fill)
    pdf.rect(4, y, 202, 5.5, "F")
    x = 6
    pdf.set_font("Helvetica", "", font_size)
    for text, w, align, color in values:
        pdf.set_xy(x, y + 0.3)
        pdf.set_text_color(*color)
        pdf.cell(w, 5, str(text), align=align)
        x += w
    return y + 5.8


def _page_number(pdf: FPDF, page_num: int, total: str = "") -> None:
    """Draw page number at bottom right."""
    pdf.set_xy(170, 285)
    pdf.set_font("Helvetica", "", 6)
    pdf.set_text_color(*C_MUTED)
    label = f"Page {page_num}" + (f" of {total}" if total else "")
    pdf.cell(30, 4, label, align="R")


def _footer_disclaimer(pdf: FPDF, y: float) -> float:
    """Draw the standard disclaimer block. Returns y after."""
    pdf.set_xy(4, y)
    pdf.set_font("Helvetica", "I", 5.5)
    pdf.set_text_color(*C_MUTED)
    disclaimer = (
        "DISCLAIMER: This report is generated by the NZT-48 algorithmic trading system "
        "for informational purposes only. It does not constitute financial advice or a "
        "recommendation to buy or sell any security. Leveraged ETPs carry substantial risk "
        "of loss. Past performance does not guarantee future results."
    )
    pdf.multi_cell(202, 3.5, disclaimer)
    return pdf.get_y() + 2


# ---------------------------------------------------------------------------
# VIX assessment logic
# ---------------------------------------------------------------------------

def _classify_vix(vix_level: float) -> tuple:
    """
    Classify VIX level into regime.

    Returns (label, color, description).
    """
    if vix_level < 12:
        return "LOW", C_GREEN, "Extreme complacency -- volatility expansion likely ahead"
    elif vix_level < 18:
        return "NORMAL", C_WHITE, "Standard market conditions -- no elevated risk"
    elif vix_level < 25:
        return "ELEVATED", C_ORANGE, "Heightened uncertainty -- tighten stops, reduce size"
    elif vix_level < 35:
        return "HIGH", C_RED, "Significant fear -- high whipsaw risk for leveraged ETPs"
    else:
        return "EXTREME", C_RED, "Panic conditions -- consider full de-risk or inverse positions"


def _risk_on_off_score(vix_level: float, futures_data: Dict) -> tuple:
    """
    Compute a composite risk-on/risk-off score (-100 to +100).
    Positive = risk-on, negative = risk-off.

    Returns (score, assessment, color, reasoning_lines).
    """
    score = 0.0
    reasoning = []

    # VIX component (weight: 40%)
    if vix_level < 15:
        vix_score = 40.0
        reasoning.append(f"VIX {vix_level:.1f} < 15 -- strong risk-on signal (+40)")
    elif vix_level < 20:
        vix_score = 20.0
        reasoning.append(f"VIX {vix_level:.1f} < 20 -- mild risk-on (+20)")
    elif vix_level < 25:
        vix_score = -10.0
        reasoning.append(f"VIX {vix_level:.1f} elevated -- mild risk-off (-10)")
    elif vix_level < 30:
        vix_score = -30.0
        reasoning.append(f"VIX {vix_level:.1f} high -- risk-off (-30)")
    else:
        vix_score = -40.0
        reasoning.append(f"VIX {vix_level:.1f} extreme -- strong risk-off (-40)")
    score += vix_score

    # Futures component (weight: 30% each for ES and NQ)
    for sym, name in [("ES=F", "S&P Futures"), ("NQ=F", "Nasdaq Futures")]:
        chg = futures_data.get(sym, {}).get("chg_pct", 0.0)
        if chg > 0.5:
            f_score = 15.0
            reasoning.append(f"{name} +{chg:.2f}% -- bullish overnight (+15)")
        elif chg > 0.0:
            f_score = 5.0
            reasoning.append(f"{name} +{chg:.2f}% -- marginally positive (+5)")
        elif chg > -0.5:
            f_score = -5.0
            reasoning.append(f"{name} {chg:+.2f}% -- marginally negative (-5)")
        else:
            f_score = -15.0
            reasoning.append(f"{name} {chg:+.2f}% -- bearish overnight (-15)")
        score += f_score

    # Clamp
    score = max(-100.0, min(100.0, score))

    if score > 30:
        assessment = "RISK-ON"
        color = C_GREEN
    elif score > 0:
        assessment = "LEAN RISK-ON"
        color = C_GREEN
    elif score > -30:
        assessment = "LEAN RISK-OFF"
        color = C_ORANGE
    else:
        assessment = "RISK-OFF"
        color = C_RED

    return score, assessment, color, reasoning


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _fetch_vix() -> float:
    """Fetch current VIX level. Returns 20.0 on failure."""
    if not _HAS_YF:
        logger.warning("yfinance not available -- using VIX default 20.0")
        return 20.0
    try:
        df = yf.download(VIX_TICKER, period="5d", interval="1d",
                         auto_adjust=True, progress=False, threads=False)
        if df is not None and not df.empty:
            if isinstance(df.columns, __import__("pandas").MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return float(df["Close"].iloc[-1])
    except Exception as exc:
        logger.warning("VIX fetch failed: %s", exc)
    return 20.0


def _fetch_futures() -> Dict[str, Dict[str, Any]]:
    """Fetch overnight futures data. Returns dict of ticker -> {price, chg_pct}."""
    result = {}
    if not _HAS_YF:
        for sym, name in FUTURES_TICKERS.items():
            result[sym] = {"name": name, "price": 0.0, "chg_pct": 0.0, "status": "NO_DATA"}
        return result

    for sym, name in FUTURES_TICKERS.items():
        try:
            df = yf.download(sym, period="5d", interval="1d",
                             auto_adjust=True, progress=False, threads=False)
            if df is not None and not df.empty:
                if isinstance(df.columns, __import__("pandas").MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                close = df["Close"]
                price = float(close.iloc[-1])
                prev  = float(close.iloc[-2]) if len(close) >= 2 else price
                chg   = ((price - prev) / prev * 100) if prev != 0 else 0.0
                result[sym] = {"name": name, "price": price, "chg_pct": chg, "status": "OK"}
            else:
                result[sym] = {"name": name, "price": 0.0, "chg_pct": 0.0, "status": "NO_DATA"}
        except Exception as exc:
            logger.warning("Futures fetch failed for %s: %s", sym, exc)
            result[sym] = {"name": name, "price": 0.0, "chg_pct": 0.0, "status": "ERROR"}

    return result


def _fetch_asia() -> Dict[str, Dict[str, Any]]:
    """Fetch Asia session close data. Returns dict of ticker -> {price, chg_pct}."""
    result = {}
    if not _HAS_YF:
        for sym, name in ASIA_TICKERS.items():
            result[sym] = {"name": name, "price": 0.0, "chg_pct": 0.0, "status": "NO_DATA"}
        return result

    for sym, name in ASIA_TICKERS.items():
        try:
            df = yf.download(sym, period="5d", interval="1d",
                             auto_adjust=True, progress=False, threads=False)
            if df is not None and not df.empty:
                if isinstance(df.columns, __import__("pandas").MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                close = df["Close"]
                price = float(close.iloc[-1])
                prev  = float(close.iloc[-2]) if len(close) >= 2 else price
                chg   = ((price - prev) / prev * 100) if prev != 0 else 0.0
                result[sym] = {"name": name, "price": price, "chg_pct": chg, "status": "OK"}
            else:
                result[sym] = {"name": name, "price": 0.0, "chg_pct": 0.0, "status": "NO_DATA"}
        except Exception as exc:
            logger.warning("Asia fetch failed for %s: %s", sym, exc)
            result[sym] = {"name": name, "price": 0.0, "chg_pct": 0.0, "status": "ERROR"}

    return result


# ---------------------------------------------------------------------------
# Main report class
# ---------------------------------------------------------------------------

class OvernightRiskPDF:
    """
    NZT-48 V8.0 -- Overnight Risk & Macro Tape (06:30 UK).

    Usage:
        report = OvernightRiskPDF()
        pdf_path = report.generate()
    """

    _REPORTS_DIR = _ROOT / "data" / "reports"

    def __init__(self):
        self._now       = datetime.now(timezone.utc)
        self._run_id    = uuid.uuid4().hex[:8].upper()
        self._param_hash = hashlib.md5(
            f"{_STRATEGY_VERSION}:{_BAR_RESOLUTION}:{_DATA_VENDOR}".encode()
        ).hexdigest()[:8]

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def generate(self, session: str = "OVERNIGHT") -> Optional[str]:
        """
        Generate the Overnight Risk & Macro Tape PDF.

        Parameters
        ----------
        session : str
            Session label embedded in the PDF (default "OVERNIGHT").

        Returns
        -------
        str or None
            Absolute path to the saved PDF file, or None on error.
        """
        ts_display = self._now.strftime("%Y-%m-%d %H:%M UTC")
        ts_file    = self._now.strftime("%Y%m%d_%H%M%S")
        output_dir = self._REPORTS_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"NZT48_OVERNIGHT_{ts_file}.pdf"

        logger.info(
            "OvernightRiskPDF.generate() session=%s run_id=%s",
            session, self._run_id,
        )

        # -- Fetch data --------------------------------------------------------
        try:
            vix_level    = _fetch_vix()
            futures_data = _fetch_futures()
            asia_data    = _fetch_asia()
        except Exception as exc:
            logger.error("Data fetch failed: %s\n%s", exc, traceback.format_exc())
            vix_level    = 20.0
            futures_data = {s: {"name": n, "price": 0.0, "chg_pct": 0.0, "status": "ERROR"}
                           for s, n in FUTURES_TICKERS.items()}
            asia_data    = {s: {"name": n, "price": 0.0, "chg_pct": 0.0, "status": "ERROR"}
                           for s, n in ASIA_TICKERS.items()}

        # -- VIX assessment ----------------------------------------------------
        vix_label, vix_color, vix_desc = _classify_vix(vix_level)

        # -- Risk-on/off -------------------------------------------------------
        ro_score, ro_assessment, ro_color, ro_reasoning = _risk_on_off_score(
            vix_level, futures_data
        )

        # -- Build PDF ---------------------------------------------------------
        try:
            pdf = FPDF(orientation="P", unit="mm", format="A4")
            pdf.set_auto_page_break(auto=False)
            pdf.set_margins(0, 0, 0)

            # ===== PAGE 1 =====================================================
            pdf.add_page()
            _dark_bg(pdf)
            _header_bar(
                pdf,
                "OVERNIGHT RISK & MACRO TAPE",
                f"NZT-48 {_STRATEGY_VERSION}  |  Session: {session}  |  {ts_display}",
            )

            # -- Title block ---------------------------------------------------
            pdf.set_fill_color(*C_PANEL)
            pdf.rect(4, 26, 202, 28, "F")
            pdf.set_xy(0, 30)
            pdf.set_font("Helvetica", "B", 22)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(210, 10, "NZT-48 OVERNIGHT RISK & MACRO TAPE", align="C")
            pdf.set_xy(0, 42)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*C_MUTED)
            pdf.cell(
                210, 5,
                f"Run ID: {self._run_id}  |  Generated: {ts_display}  |  "
                f"Config Hash: {self._param_hash}",
                align="C",
            )

            y = 60

            # -- System State --------------------------------------------------
            y = _section_title(pdf, y, "SYSTEM STATE")
            y = _kv_line(pdf, y, "Strategy Version:", _STRATEGY_VERSION)
            y = _kv_line(pdf, y, "Session:", session)
            y = _kv_line(pdf, y, "Data Vendor:", _DATA_VENDOR)
            y = _kv_line(pdf, y, "Generated At:", ts_display)
            y = _kv_line(pdf, y, "Run ID:", self._run_id)
            y += 3

            # -- Overnight Futures ---------------------------------------------
            y = _section_title(pdf, y, "OVERNIGHT FUTURES")
            cols = [("Instrument", 60, "L"), ("Name", 50, "L"),
                    ("Price", 30, "R"), ("Change %", 30, "R"), ("Status", 30, "C")]
            y = _table_header(pdf, y, cols)

            for i, (sym, info) in enumerate(futures_data.items()):
                chg = info.get("chg_pct", 0.0)
                chg_color = C_GREEN if chg > 0 else (C_RED if chg < 0 else C_WHITE)
                price_str = f"{info['price']:.2f}" if info["price"] > 0 else "--"
                chg_str   = f"{chg:+.2f}%" if info["price"] > 0 else "--"
                status    = info.get("status", "UNKNOWN")
                status_color = C_GREEN if status == "OK" else C_ORANGE

                row_vals = [
                    (sym, 60, "L", C_WHITE),
                    (info.get("name", sym), 50, "L", C_LIGHT_GRAY),
                    (price_str, 30, "R", C_WHITE),
                    (chg_str, 30, "R", chg_color),
                    (status, 30, "C", status_color),
                ]
                y = _table_row(pdf, y, row_vals, alt=(i % 2 == 1))

            y += 4

            # -- Asia Close ----------------------------------------------------
            y = _section_title(pdf, y, "ASIA SESSION CLOSE")
            cols = [("Index", 60, "L"), ("Name", 50, "L"),
                    ("Close", 30, "R"), ("Change %", 30, "R"), ("Status", 30, "C")]
            y = _table_header(pdf, y, cols)

            for i, (sym, info) in enumerate(asia_data.items()):
                chg = info.get("chg_pct", 0.0)
                chg_color = C_GREEN if chg > 0 else (C_RED if chg < 0 else C_WHITE)
                price_str = f"{info['price']:.2f}" if info["price"] > 0 else "--"
                chg_str   = f"{chg:+.2f}%" if info["price"] > 0 else "--"
                status    = info.get("status", "UNKNOWN")
                status_color = C_GREEN if status == "OK" else C_ORANGE

                row_vals = [
                    (sym, 60, "L", C_WHITE),
                    (info.get("name", sym), 50, "L", C_LIGHT_GRAY),
                    (price_str, 30, "R", C_WHITE),
                    (chg_str, 30, "R", chg_color),
                    (status, 30, "C", status_color),
                ]
                y = _table_row(pdf, y, row_vals, alt=(i % 2 == 1))

            y += 4

            # -- Macro Calendar Today ------------------------------------------
            y = _section_title(pdf, y, "MACRO CALENDAR TODAY")
            macro_events = [
                ("08:00 UK", "UK GDP (MoM)", "MEDIUM", "Affects FTSE positioning"),
                ("10:00 UK", "EU CPI Flash", "HIGH", "EUR volatility driver"),
                ("13:30 UK", "US Initial Jobless Claims", "MEDIUM", "Labour market signal"),
                ("15:00 UK", "US ISM Manufacturing", "HIGH", "Leading indicator"),
            ]

            cols = [("Time", 25, "L"), ("Event", 60, "L"),
                    ("Impact", 25, "C"), ("Notes", 90, "L")]
            y = _table_header(pdf, y, cols)

            pdf.set_xy(6, y)
            pdf.set_font("Helvetica", "I", 6.5)
            pdf.set_text_color(*C_MUTED)
            pdf.cell(200, 4, "[Placeholder -- integrate with economic calendar API for live data]")
            y += 5

            for i, (time, event, impact, notes) in enumerate(macro_events):
                impact_color = C_RED if impact == "HIGH" else (C_ORANGE if impact == "MEDIUM" else C_WHITE)
                row_vals = [
                    (time, 25, "L", C_LIGHT_GRAY),
                    (event, 60, "L", C_WHITE),
                    (impact, 25, "C", impact_color),
                    (notes, 90, "L", C_MUTED),
                ]
                y = _table_row(pdf, y, row_vals, alt=(i % 2 == 1))

            _page_number(pdf, 1, "2")

            # ===== PAGE 2 =====================================================
            pdf.add_page()
            _dark_bg(pdf)
            _header_bar(
                pdf,
                "OVERNIGHT RISK & MACRO TAPE",
                f"NZT-48 {_STRATEGY_VERSION}  |  VIX & Risk Assessment  |  {ts_display}",
            )

            y = 28

            # -- VIX Term Structure --------------------------------------------
            y = _section_title(pdf, y, "VIX TERM STRUCTURE")

            # VIX big number display
            pdf.set_fill_color(*C_PANEL)
            pdf.rect(4, y, 96, 40, "F")
            pdf.set_draw_color(*C_ACCENT)
            pdf.rect(4, y, 96, 40)

            pdf.set_xy(6, y + 2)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(92, 5, "CURRENT VIX LEVEL", align="C")

            pdf.set_xy(6, y + 10)
            pdf.set_font("Helvetica", "B", 32)
            pdf.set_text_color(*vix_color)
            pdf.cell(92, 14, f"{vix_level:.1f}", align="C")

            pdf.set_xy(6, y + 26)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(*vix_color)
            pdf.cell(92, 5, vix_label, align="C")

            pdf.set_xy(6, y + 33)
            pdf.set_font("Helvetica", "", 6.5)
            pdf.set_text_color(*C_MUTED)
            pdf.cell(92, 4, "Spot VIX (CBOE)", align="C")

            # VIX assessment panel (right)
            pdf.set_fill_color(*C_PANEL)
            pdf.rect(110, y, 96, 40, "F")
            pdf.set_draw_color(*C_ACCENT)
            pdf.rect(110, y, 96, 40)

            pdf.set_xy(112, y + 2)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(92, 5, "ASSESSMENT", align="C")

            pdf.set_xy(112, y + 10)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*C_WHITE)
            pdf.multi_cell(90, 4.5, vix_desc)

            # VIX thresholds reference
            thresholds = [
                ("< 12", "LOW", C_GREEN),
                ("12-18", "NORMAL", C_WHITE),
                ("18-25", "ELEVATED", C_ORANGE),
                ("25-35", "HIGH", C_RED),
                ("> 35", "EXTREME", C_RED),
            ]
            th_y = y + 24
            for vix_range, vix_lbl, vix_c in thresholds:
                pdf.set_xy(114, th_y)
                pdf.set_font("Helvetica", "", 6)
                pdf.set_text_color(*C_MUTED)
                pdf.cell(20, 3.5, vix_range)
                pdf.set_text_color(*vix_c)
                pdf.set_font("Helvetica", "B", 6)
                pdf.cell(20, 3.5, vix_lbl)
                th_y += 3.2

            y += 46

            # -- Risk-On / Risk-Off Assessment ---------------------------------
            y = _section_title(pdf, y, "RISK-ON / RISK-OFF ASSESSMENT")

            # Score display
            pdf.set_fill_color(*C_PANEL)
            pdf.rect(4, y, 202, 16, "F")

            pdf.set_xy(6, y + 1)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(40, 5, "Composite Score:")
            pdf.set_font("Helvetica", "B", 14)
            pdf.set_text_color(*ro_color)
            pdf.cell(30, 5, f"{ro_score:+.0f}")

            pdf.set_xy(80, y + 1)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(30, 5, "Assessment:")
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(*ro_color)
            pdf.cell(60, 5, ro_assessment)

            # Score bar visualisation
            bar_x = 8
            bar_y = y + 9
            bar_w = 194
            bar_h = 5
            pdf.set_fill_color(40, 40, 60)
            pdf.rect(bar_x, bar_y, bar_w, bar_h, "F")

            # Fill bar from center
            center_x = bar_x + bar_w / 2
            fill_w   = abs(ro_score) / 100.0 * (bar_w / 2)
            if ro_score > 0:
                pdf.set_fill_color(*C_GREEN)
                pdf.rect(center_x, bar_y, fill_w, bar_h, "F")
            elif ro_score < 0:
                pdf.set_fill_color(*C_RED)
                pdf.rect(center_x - fill_w, bar_y, fill_w, bar_h, "F")

            # Center marker
            pdf.set_fill_color(*C_WHITE)
            pdf.rect(center_x - 0.3, bar_y, 0.6, bar_h, "F")

            # Labels
            pdf.set_xy(bar_x, bar_y + bar_h + 0.5)
            pdf.set_font("Helvetica", "", 5)
            pdf.set_text_color(*C_RED)
            pdf.cell(bar_w / 2, 3, "RISK-OFF (-100)")
            pdf.set_text_color(*C_GREEN)
            pdf.cell(bar_w / 2, 3, "RISK-ON (+100)", align="R")

            y += 22

            # Reasoning lines
            for line in ro_reasoning:
                pdf.set_xy(8, y)
                pdf.set_font("Helvetica", "", 6.5)
                pdf.set_text_color(*C_LIGHT_GRAY)
                pdf.cell(4, 4, ">")
                pdf.cell(190, 4, line)
                y += 4.5

            y += 4

            # -- ISA Portfolio Implications ------------------------------------
            y = _section_title(pdf, y, "ISA PORTFOLIO IMPLICATIONS")

            implications = self._derive_implications(
                vix_label, ro_assessment, ro_score, futures_data
            )

            for i, (category, message, msg_color) in enumerate(implications):
                pdf.set_fill_color(*(C_ROW_ALT if i % 2 == 1 else C_BG))
                pdf.rect(4, y, 202, 9, "F")
                pdf.set_xy(6, y + 0.5)
                pdf.set_font("Helvetica", "B", 7)
                pdf.set_text_color(*C_GOLD)
                pdf.cell(40, 4, category)
                pdf.set_font("Helvetica", "", 6.5)
                pdf.set_text_color(*msg_color)
                pdf.multi_cell(158, 4, message)
                y = max(y + 9.5, pdf.get_y() + 1)

            y += 6

            # -- Truth Manifest ------------------------------------------------
            y = _section_title(pdf, y, "TRUTH MANIFEST")
            plays_hash = hashlib.md5(
                f"{self._run_id}:{ts_display}:{session}".encode()
            ).hexdigest()[:12]
            config_hash = self._param_hash

            y = _kv_line(pdf, y, "Run ID:", self._run_id)
            y = _kv_line(pdf, y, "Plays Hash:", plays_hash)
            y = _kv_line(pdf, y, "Config Hash:", config_hash)
            y = _kv_line(pdf, y, "Generated At:", ts_display)
            y = _kv_line(pdf, y, "Session:", session)
            y = _kv_line(pdf, y, "VIX at Generation:", f"{vix_level:.1f}")
            y += 3

            # -- Footer --------------------------------------------------------
            _footer_disclaimer(pdf, y)
            _page_number(pdf, 2, "2")

            # -- Save ----------------------------------------------------------
            pdf.output(str(output_path))
            logger.info("PDF saved: %s (%d pages)", output_path, pdf.page)
            return str(output_path)

        except Exception as exc:
            logger.error(
                "OvernightRiskPDF generation failed: %s\n%s",
                exc, traceback.format_exc(),
            )
            return None

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _derive_implications(
        self,
        vix_label: str,
        ro_assessment: str,
        ro_score: float,
        futures_data: Dict,
    ) -> List[tuple]:
        """
        Derive ISA portfolio implications from the overnight data.

        Returns list of (category, message, color) tuples.
        """
        implications = []

        # Position sizing
        if vix_label in ("HIGH", "EXTREME"):
            implications.append((
                "POSITION SIZING",
                "Reduce all position sizes by 50%. Max single position 2% of equity. "
                "VIX regime demands defensive posture.",
                C_RED,
            ))
        elif vix_label == "ELEVATED":
            implications.append((
                "POSITION SIZING",
                "Reduce position sizes by 25%. Tighten stop-loss by 0.5x ATR. "
                "Elevated VIX increases whipsaw risk on leveraged ETPs.",
                C_ORANGE,
            ))
        else:
            implications.append((
                "POSITION SIZING",
                "Standard sizing applies. VIX conditions are favourable for "
                "leveraged ETP positioning.",
                C_GREEN,
            ))

        # Direction bias
        if ro_score > 20:
            implications.append((
                "DIRECTION BIAS",
                "Overnight tape is risk-on. Favour LONG leveraged ETPs "
                "(QQQ3, 3LUS, NVD3, 3SEM). Shorts (QQQS, 3USS) face headwind.",
                C_GREEN,
            ))
        elif ro_score < -20:
            implications.append((
                "DIRECTION BIAS",
                "Overnight tape is risk-off. Favour SHORT/INVERSE ETPs "
                "(QQQS, 3USS) or flat. Long leveraged ETPs face gap-down risk.",
                C_RED,
            ))
        else:
            implications.append((
                "DIRECTION BIAS",
                "Mixed overnight signals. No strong directional bias. "
                "Wait for London open price action before committing.",
                C_ORANGE,
            ))

        # Gap risk
        es_chg = abs(futures_data.get("ES=F", {}).get("chg_pct", 0.0))
        nq_chg = abs(futures_data.get("NQ=F", {}).get("chg_pct", 0.0))
        max_gap = max(es_chg, nq_chg)

        if max_gap > 1.0:
            implications.append((
                "GAP RISK",
                f"Futures show {max_gap:.1f}% overnight move. Leveraged 3x ETPs may "
                f"gap {max_gap * 3:.1f}%. Consider limit orders, not market orders at open.",
                C_RED,
            ))
        elif max_gap > 0.5:
            implications.append((
                "GAP RISK",
                f"Moderate overnight move ({max_gap:.1f}%). Monitor LSE open for "
                f"gap fill or continuation before entry.",
                C_ORANGE,
            ))
        else:
            implications.append((
                "GAP RISK",
                "Minimal overnight gap expected. Standard entry protocols apply.",
                C_GREEN,
            ))

        # 2% Daily Target
        if ro_assessment in ("RISK-ON", "LEAN RISK-ON") and vix_label in ("LOW", "NORMAL"):
            implications.append((
                "2% TARGET",
                "Conditions are favourable for the daily 2% target trade. "
                "S15 should find a clean candidate today.",
                C_GREEN,
            ))
        elif vix_label in ("HIGH", "EXTREME"):
            implications.append((
                "2% TARGET",
                "S15 daily target may be difficult to achieve cleanly. "
                "Wider stops needed -- accept smaller position or skip today.",
                C_RED,
            ))
        else:
            implications.append((
                "2% TARGET",
                "Mixed conditions for daily target. Be selective with entry -- "
                "demand tier-1 or tier-2 bias score before committing.",
                C_ORANGE,
            ))

        return implications


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    report = OvernightRiskPDF()
    try:
        pdf_path = report.generate()
        if pdf_path:
            print(f"PDF written: {pdf_path}")
        else:
            print("Report generation failed -- check logs")
            raise SystemExit(1)
    except Exception as exc:
        print(f"Report generation failed: {exc}")
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
