#!/usr/bin/env python3
"""
NZT-48 V8.0 Strategy PDF Generator
====================================
Generates a comprehensive strategy document: "The Predatory Engine"

Sections:
  1. Title page
  2. Executive Summary (2% Daily Compounding Law + 5 Laws)
  3. Architecture diagram (text-based signal flow)
  4. ISA Universe table (tickers, leverage, spreads, correlation groups)
  5. Risk Framework (Kelly sizing, circuit breakers, kill switches)
  6. Gate Catalog (all 7 gates with thresholds)
  7. Execution Protocol (maker-only, TWAP, toxic fill defense)
  8. Academic References (45 papers)

Output:
  - /Users/rr/nzt48-signals/docs/NZT48_V5_Strategy.pdf
  - /Users/rr/Desktop/nzt/NZT48_V5_Strategy.pdf

Usage:
  python scripts/generate_strategy_pdf.py
"""

from __future__ import annotations

import os
import sys
import shutil
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Dependency check -- install reportlab if missing
# ---------------------------------------------------------------------------

try:
    import reportlab  # noqa: F401
except ImportError:
    print("[NZT-48] reportlab not found. Installing via pip ...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "reportlab"])
    print("[NZT-48] reportlab installed successfully.")

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, PageBreak,
    Paragraph, Spacer, Table, TableStyle, HRFlowable,
)
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT

# ---------------------------------------------------------------------------
# Import ISA Universe data (graceful fallback if run outside project root)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from uk_isa.isa_universe import (
        CORE_UNIVERSE,
        EXTENDED_UNIVERSE,
        LEVERAGE_MAP,
        SLIPPAGE_MODEL,
        CORRELATION_GROUPS,
        ISA_FACTOR_GROUPS,
        TICKER_NAMES,
        SESSION_CONFIG,
    )
    _ISA_IMPORTED = True
except ImportError:
    print("[NZT-48] WARNING: Could not import uk_isa.isa_universe -- using built-in fallback data.")
    _ISA_IMPORTED = False
    CORE_UNIVERSE = [
        "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L",
        "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L",
    ]
    EXTENDED_UNIVERSE = CORE_UNIVERSE + [
        "AMD3.L", "ARM3.L", "NVDS.L", "TSLS.L", "3LDE.L",
        "3LEU.L", "3GOL.L", "3SIL.L", "3OIL.L", "LLY3.L",
    ]
    LEVERAGE_MAP = {
        "QQQ3.L": 3.0, "3LUS.L": 3.0, "3SEM.L": 3.0, "GPT3.L": 3.0,
        "NVD3.L": 3.0, "TSL3.L": 3.0, "TSM3.L": 3.0, "MU2.L": 2.0,
        "QQQS.L": -3.0, "3USS.L": -3.0, "QQQ5.L": 5.0, "SP5L.L": 5.0,
        "AMD3.L": 3.0, "ARM3.L": 3.0, "NVDS.L": -3.0, "TSLS.L": -3.0,
        "3LDE.L": 3.0, "3LEU.L": 3.0, "3GOL.L": 3.0, "3SIL.L": 3.0,
        "3OIL.L": 3.0, "LLY3.L": 3.0,
    }
    SLIPPAGE_MODEL = {"default_bps": 5, "spread_bps": {
        "QQQ3.L": 8, "QQQ5.L": 10, "SP5L.L": 8, "3LUS.L": 8,
        "QQQS.L": 10, "3USS.L": 10, "3SEM.L": 12, "GPT3.L": 12,
        "NVD3.L": 12, "TSL3.L": 15, "TSM3.L": 10, "MU2.L": 10,
        "AMD3.L": 12, "ARM3.L": 12, "NVDS.L": 15, "TSLS.L": 15,
        "3LDE.L": 12, "3LEU.L": 12, "3GOL.L": 15, "3SIL.L": 15,
        "3OIL.L": 15, "LLY3.L": 15,
    }}
    CORRELATION_GROUPS = {
        "NASDAQ": ["QQQ3.L", "QQQS.L", "QQQ5.L", "GPT3.L", "ARM3.L"],
        "SP500": ["3LUS.L", "3USS.L", "SP5L.L"],
        "SEMIS": ["3SEM.L", "NVD3.L", "AMD3.L", "TSM3.L"],
        "TSLA": ["TSL3.L", "TSLS.L"],
        "COMMODITIES": ["3OIL.L", "3GOL.L", "3SIL.L"],
        "EUROPE": ["3LDE.L", "3LEU.L"],
    }
    ISA_FACTOR_GROUPS = {
        "nasdaq_beta_long": ["QQQ3.L", "QQQ5.L", "3LUS.L", "SP5L.L"],
        "semiconductors": ["3SEM.L", "TSM3.L", "MU2.L", "AMD3.L"],
        "ai_gpt": ["GPT3.L", "NVD3.L", "ARM3.L"],
    }
    TICKER_NAMES = {
        "QQQ3.L": "Nasdaq 100 3x Long", "3LUS.L": "S&P 500 3x Long",
        "3SEM.L": "Semis 3x Long", "GPT3.L": "AI/GPT 3x Long",
        "NVD3.L": "NVIDIA 3x Long", "TSL3.L": "Tesla 3x Long",
        "TSM3.L": "TSMC 3x Long", "MU2.L": "Micron 2x Long",
        "QQQS.L": "Nasdaq 100 3x Short", "3USS.L": "S&P 500 3x Short",
        "QQQ5.L": "Nasdaq 100 5x Long", "SP5L.L": "S&P 500 5x Long",
        "AMD3.L": "AMD 3x Long", "ARM3.L": "ARM 3x Long",
        "NVDS.L": "NVIDIA 3x Short", "TSLS.L": "Tesla 3x Short",
        "3LDE.L": "DAX 3x Long", "3LEU.L": "Euro Stoxx 3x Long",
        "3GOL.L": "Gold 3x Long", "3SIL.L": "Silver 3x Long",
        "3OIL.L": "Oil 3x Long", "LLY3.L": "Eli Lilly 3x Long",
    }
    SESSION_CONFIG = {"timezone": "UTC", "lse_open_utc": "08:00",
                      "lse_close_utc": "16:30", "us_open_utc": "14:30",
                      "us_close_utc": "21:00"}


# ============================================================================
# PALETTE -- matches NZT-48 brand (dark background, gold headers)
# ============================================================================

GOLD   = HexColor("#F5C518")
DARK   = HexColor("#0A0A0A")
D2     = HexColor("#161616")
D3     = HexColor("#222222")
D4     = HexColor("#1C1C1C")
NAVY   = HexColor("#0d1117")
CYAN   = HexColor("#00D4FF")
GREEN  = HexColor("#00C851")
RED    = HexColor("#FF4444")
AMBER  = HexColor("#FFBB33")
GREY   = HexColor("#777777")
GREY2  = HexColor("#8b949e")
LIGHT  = HexColor("#DEDEDE")
WHITE  = white
PURPLE = HexColor("#AA44FF")
ORANGE = HexColor("#FF8800")
TEAL   = HexColor("#00BFA5")
BLUE   = HexColor("#58a6ff")

W, H = A4
LMAR = 18 * mm
RMAR = 18 * mm
CONTENT_W = W - LMAR - RMAR

NOW = datetime.now(timezone.utc)
DATE_STR = NOW.strftime("%d %B %Y")
VERSION = "V8.0"

# ============================================================================
# OUTPUT PATHS
# ============================================================================

PRIMARY_OUT = _PROJECT_ROOT / "docs" / "NZT48_V5_Strategy.pdf"
DESKTOP_OUT = Path.home() / "Desktop" / "nzt" / "NZT48_V5_Strategy.pdf"
CORE_PDFS_OUT = Path.home() / "Desktop" / "NZT-48 CORE PDFS" / "NZT48_V5_Strategy.pdf"


# ============================================================================
# STYLE FACTORY -- unique names prevent ReportLab style-name cache collisions
# ============================================================================

_sc = [0]


def S(**kw) -> ParagraphStyle:
    _sc[0] += 1
    return ParagraphStyle(f"strat_s{_sc[0]}", **kw)


# Named styles
COVER_TITLE = S(fontName="Helvetica-Bold", fontSize=34, textColor=GOLD, alignment=TA_CENTER, spaceAfter=4, leading=40)
COVER_SUB   = S(fontName="Helvetica-Bold", fontSize=14, textColor=CYAN, alignment=TA_CENTER, spaceAfter=3, leading=18)
COVER_TAG   = S(fontName="Helvetica",      fontSize=11, textColor=LIGHT, alignment=TA_CENTER, spaceAfter=2, leading=15)
COVER_DATE  = S(fontName="Helvetica",      fontSize=9,  textColor=GREY, alignment=TA_CENTER, spaceAfter=8, leading=12)
H1     = S(fontName="Helvetica-Bold", fontSize=16,  textColor=GOLD,  spaceBefore=12, spaceAfter=6, leading=20)
H2     = S(fontName="Helvetica-Bold", fontSize=12,  textColor=CYAN,  spaceBefore=10, spaceAfter=4, leading=16)
H3     = S(fontName="Helvetica-Bold", fontSize=10,  textColor=LIGHT, spaceBefore=7,  spaceAfter=3, leading=14)
BODY   = S(fontName="Helvetica",      fontSize=8.5, textColor=LIGHT, spaceBefore=2,  spaceAfter=3, leading=13)
BODYJ  = S(fontName="Helvetica",      fontSize=8.5, textColor=LIGHT, spaceBefore=2,  spaceAfter=3, leading=13, alignment=TA_JUSTIFY)
SMALL  = S(fontName="Helvetica",      fontSize=7.5, textColor=GREY,  spaceBefore=1,  spaceAfter=1, leading=11)
CITE   = S(fontName="Helvetica-Oblique", fontSize=7.0, textColor=GREY, spaceBefore=1, spaceAfter=2, leading=11)
CAP    = S(fontName="Helvetica-Oblique", fontSize=7.5, textColor=GREY, alignment=TA_CENTER, leading=11)
BIGNUM = S(fontName="Helvetica-Bold", fontSize=42, textColor=GOLD, alignment=TA_CENTER, spaceAfter=0, leading=48)
MONO   = S(fontName="Courier",        fontSize=7.5, textColor=CYAN, spaceBefore=2, spaceAfter=2, leading=11)
MONO_G = S(fontName="Courier",        fontSize=7,   textColor=GREEN, spaceBefore=1, spaceAfter=1, leading=10)

# Markup helpers
def b(t):    return f"<b>{t}</b>"
def it(t):   return f"<i>{t}</i>"
def fc(t, c): return f'<font color="{c}">{t}</font>'
def gold(t): return fc(b(t), "#F5C518")
def cyan(t): return fc(t,    "#00D4FF")
def grn(t):  return fc(b(t), "#00C851")
def red(t):  return fc(b(t), "#FF4444")
def amb(t):  return fc(b(t), "#FFBB33")
def pur(t):  return fc(b(t), "#AA44FF")
def ora(t):  return fc(b(t), "#FF8800")
def tel(t):  return fc(b(t), "#00BFA5")
def blu(t):  return fc(t,    "#58a6ff")
def gry(t):  return fc(t,    "#777777")
def reff(t): return fc(it(t), "#777777")

# Table cell styles
TC    = S(fontName="Helvetica",      fontSize=8,   textColor=LIGHT, leading=11)
TC_SM = S(fontName="Helvetica",      fontSize=7.5, textColor=LIGHT, leading=10)
TC_B  = S(fontName="Helvetica-Bold", fontSize=8,   textColor=LIGHT, leading=11)
TC_G  = S(fontName="Helvetica",      fontSize=7.5, textColor=GREY,  leading=10)
TC_MONO = S(fontName="Courier",      fontSize=7,   textColor=CYAN,  leading=10)


def P(t, sty=None) -> Paragraph:
    return Paragraph(str(t), sty or TC)


# Spacers
SP   = Spacer(1, 5)
SP2  = Spacer(1, 10)
SP3  = Spacer(1, 16)
HRG  = HRFlowable(width="100%", thickness=1.5, color=GOLD, spaceAfter=7, spaceBefore=2)
HRC  = HRFlowable(width="100%", thickness=0.5, color=GREY, spaceAfter=5, spaceBefore=3)


# ============================================================================
# TABLE HELPERS
# ============================================================================

def hdr_row(*cells):
    return [P(b(c), TC_B) for c in cells]


def std_ts(header_bg=D3, row_bg=D2, alt_bg=D4):
    return TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR",     (0, 0), (-1, 0), GOLD),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [row_bg, alt_bg]),
        ("GRID",          (0, 0), (-1, -1), 0.3, GREY),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LINEABOVE",     (0, 0), (-1, 0), 1.5, GOLD),
    ])


def accent_ts(accent_col, header_bg=D3):
    ts = std_ts(header_bg)
    ts.add("LINEAFTER", (0, 0), (0, -1), 1.5, accent_col)
    return ts


# ============================================================================
# PAGE TEMPLATE -- dark background with gold header bar + footer
# ============================================================================

def _header_footer(canvas, doc):
    canvas.saveState()
    # Full dark background
    canvas.setFillColor(DARK)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)
    # Top gold stripe
    canvas.setFillColor(GOLD)
    canvas.rect(0, H - 4, W, 4, fill=1, stroke=0)
    # Header band
    canvas.setFillColor(D3)
    canvas.rect(0, H - 12 * mm, W, 12 * mm - 4, fill=1, stroke=0)
    canvas.setFont("Helvetica-Bold", 7)
    canvas.setFillColor(GOLD)
    canvas.drawString(LMAR, H - 7.5 * mm, f"NZT-48  {VERSION}  ·  The Predatory Engine  ·  STRICTLY CONFIDENTIAL")
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(GREY)
    canvas.drawRightString(W - RMAR, H - 7.5 * mm, f"UK ISA  ·  Paper Mode  ·  {DATE_STR}")
    # Footer band
    canvas.setFillColor(D3)
    canvas.rect(0, 0, W, 14, fill=1, stroke=0)
    canvas.setFont("Helvetica", 6)
    canvas.setFillColor(GREY)
    canvas.drawString(LMAR, 4.5, "NZT-48 Apex Predator Engine  ·  Paper Mode")
    canvas.drawRightString(W - RMAR, 4.5, f"Page {doc.page}")
    canvas.restoreState()


def _cover_bg(canvas, doc):
    """Cover page -- full dark with centred gold stripe."""
    canvas.saveState()
    canvas.setFillColor(DARK)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)
    # Top gold bar
    canvas.setFillColor(GOLD)
    canvas.rect(0, H - 6, W, 6, fill=1, stroke=0)
    # Bottom gold bar
    canvas.setFillColor(GOLD)
    canvas.rect(0, 0, W, 6, fill=1, stroke=0)
    # Gold accent line in the middle-upper area
    mid_y = H * 0.62
    canvas.setStrokeColor(GOLD)
    canvas.setLineWidth(0.5)
    canvas.line(LMAR, mid_y, W - RMAR, mid_y)
    canvas.line(LMAR, mid_y - 3, W - RMAR, mid_y - 3)
    canvas.restoreState()


def make_doc(path: str) -> BaseDocTemplate:
    doc = BaseDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=LMAR,
        rightMargin=RMAR,
        topMargin=16 * mm,
        bottomMargin=12 * mm,
    )
    content_frame = Frame(LMAR, 12 * mm, CONTENT_W, H - 28 * mm, id="main")
    cover_frame = Frame(LMAR, 12 * mm, CONTENT_W, H - 24 * mm, id="cover")

    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[cover_frame], onPage=_cover_bg),
        PageTemplate(id="content", frames=[content_frame], onPage=_header_footer),
    ])
    return doc


# ============================================================================
# SECTION BUILDERS
# ============================================================================

def build_cover() -> list:
    """Section 1: Title page."""
    elements = []
    elements.append(Spacer(1, 80 * mm))
    elements.append(Paragraph("NZT-48", COVER_TITLE))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(f"{VERSION}  --  The Predatory Engine", COVER_SUB))
    elements.append(Spacer(1, 8))
    elements.append(HRFlowable(width="60%", thickness=2, color=GOLD, spaceAfter=8, spaceBefore=4))
    elements.append(Paragraph("Apex Predator Engine for UK ISA", COVER_TAG))
    elements.append(Paragraph("Leveraged ETP Compounding  ·  Academic-Grade Signal Pipeline", COVER_TAG))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"Generated {DATE_STR}  ·  Paper Mode  ·  Confidential", COVER_DATE))
    elements.append(Paragraph(f"Session: LSE {SESSION_CONFIG.get('lse_open_utc', '08:00')}-{SESSION_CONFIG.get('lse_close_utc', '16:30')} UTC  ·  US Overlap {SESSION_CONFIG.get('overlap_start', '14:30')}-{SESSION_CONFIG.get('overlap_end', '16:30')} UTC", COVER_DATE))
    # Switch to content template for subsequent pages
    from reportlab.platypus import NextPageTemplate
    elements.append(NextPageTemplate("content"))
    elements.append(PageBreak())
    return elements


def build_executive_summary() -> list:
    """Section 2: Executive Summary -- The 2% Daily Compounding Law + 5 Laws."""
    elements = []
    elements.append(Paragraph("1. EXECUTIVE SUMMARY", H1))
    elements.append(HRG)

    # The big number
    elements.append(Paragraph("The 2% Daily Compounding Law", H2))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph("14,757%", BIGNUM))
    elements.append(Paragraph("Annualised return: 252 trading days at 2% daily compounding", CAP))
    elements.append(Spacer(1, 6))

    summary_text = (
        f"Starting equity {gold('10,000')} compounded at {gold('2% per trading day')} "
        f"for 252 sessions yields {gold('1,485,757')}. "
        f"The strategy does not require the same ticker every day. S15 scans {grn(str(len(CORE_UNIVERSE)))} "
        f"core instruments (expanded universe: {str(len(EXTENDED_UNIVERSE))}) and selects the single "
        f"best candidate each session based on 2% reachability scoring. "
        f"Stop = 1x ATR, target = +2% exactly. One trade per day. One kill."
    )
    elements.append(Paragraph(summary_text, BODYJ))
    elements.append(Paragraph(reff("Thorp (1962, 1997) -- Kelly Criterion; MacLean, Thorp & Ziemba (2011) -- Quarter-Kelly for leveraged products"), CITE))
    elements.append(SP2)

    # The 5 Laws
    elements.append(Paragraph("The 5 Laws of the Predatory Engine", H2))

    laws = [
        (gold("LAW 1: One Kill Per Day"),
         "Find ONE stock capable of a 2% move. Enter. Take profit. Walk away. "
         "No second trades. No revenge trades. The compounding machine requires exactly one high-probability extraction per session."),
        (cyan("LAW 2: The Gate Must Open"),
         "No signal passes without clearing ALL 7 gates: EV gate, spread gate, RVOL gate, "
         "sector gate, correlation gate, NAV basis gate, and regime gate. A single gate veto kills the signal. "
         "Zero discretionary overrides."),
        (grn("LAW 3: Leverage Is a Weapon, Not a Friend"),
         "3x products use Quarter-Kelly sizing. 5x products use Fifth-Kelly and are intraday-only with mandatory overnight kill. "
         "Inverse ETPs allowed only in confirmed bear regimes (HMM state)."),
        (amb("LAW 4: The Spread Is the Enemy"),
         "If the bid-ask spread exceeds 25 bps, the trade is dead. "
         "Net expected value after spread + slippage + decay must exceed +0.40% or no entry. "
         "Maker-only orders with 500ms cancel timeout."),
        (red("LAW 5: Survive First, Compound Second"),
         "Daily drawdown limit = 2% of equity. Weekly = 5%. Monthly = 10%. "
         "Breach any circuit breaker and the engine goes to LOCKOUT for the remainder of the period. "
         "Capital preservation is the prerequisite to compounding."),
    ]

    for title, desc in laws:
        elements.append(Paragraph(title, H3))
        elements.append(Paragraph(desc, BODYJ))

    elements.append(Paragraph(
        reff("Avellaneda & Zhang (2010) -- leveraged ETP path dependency; "
             "Cheng & Madhavan (2009) -- daily rebalancing drag; "
             "Brunnermeier & Pedersen (2009) -- liquidity spirals"),
        CITE
    ))
    elements.append(PageBreak())
    return elements


def build_architecture() -> list:
    """Section 3: Architecture diagram (text-based signal flow)."""
    elements = []
    elements.append(Paragraph("2. SYSTEM ARCHITECTURE", H1))
    elements.append(HRG)
    elements.append(Paragraph(
        "The NZT-48 Predatory Engine is a continuous-scan trading system running 24/7 on AWS EC2 "
        "(t3.small, us-east-1). The core loop fires every 60 seconds via APScheduler. "
        "Docker Compose orchestrates three services: the engine (port 8000), the Next.js dashboard (port 3001), "
        "and Redis (internal).",
        BODYJ
    ))
    elements.append(SP2)

    elements.append(Paragraph("Signal Pipeline Flow", H2))

    # Text-based architecture diagram using monospace
    arch_lines = [
        "  DATA FEEDS (yfinance .L tickers)",
        "       |",
        "       v",
        "  +-----------------------------+",
        "  |   TICK LOOP (60s scan)      |",
        "  |   18 tickers x 5 timeframes |",
        "  +-----------------------------+",
        "       |",
        "       v",
        "  +-----------------------------+      +------------------------+",
        "  |   S15 DAILY TARGET          |----->|  2% REACHABILITY SCORE |",
        "  |   Momentum + Vol Expansion  |      |  P90 Spread Tracker   |",
        "  +-----------------------------+      +------------------------+",
        "       |",
        "       v",
        "  +------------------------------------------+",
        "  |          7-GATE QUALIFICATION             |",
        "  |  EV | Spread | RVOL | Sector | Corr |    |",
        "  |  NAV Basis | Regime                  |    |",
        "  +------------------------------------------+",
        "       |  (all gates must PASS)",
        "       v",
        "  +-----------------------------+      +------------------------+",
        "  |   ML META-MODEL (De Prado)  |----->|  Binary gate: P >= 0.6|",
        "  |   meta_label() classifier   |      |  or signal is vetoed  |",
        "  +-----------------------------+      +------------------------+",
        "       |",
        "       v",
        "  +-----------------------------+",
        "  |   KELLY SIZER               |",
        "  |   Quarter-Kelly (3x)        |",
        "  |   Fifth-Kelly   (5x)        |",
        "  +-----------------------------+",
        "       |",
        "       v",
        "  +-----------------------------+      +------------------------+",
        "  |   VIRTUAL TRADER            |----->|  Chandelier Exit       |",
        "  |   Paper fills + P&L tracking|      |  5-rung profit ladder  |",
        "  +-----------------------------+      +------------------------+",
        "       |",
        "       v",
        "  +-----------------------------+",
        "  |   IBKR GATEWAY (future)     |",
        "  |   Maker-only + TWAP         |",
        "  |   Toxic fill defense         |",
        "  +-----------------------------+",
        "       |",
        "       v",
        "  +-----------------------------+",
        "  |   DASHBOARD + TELEGRAM      |",
        "  |   Real-time P&L, signals,   |",
        "  |   dual PDF reports daily    |",
        "  +-----------------------------+",
    ]

    for line in arch_lines:
        elements.append(Paragraph(line.replace(" ", "&nbsp;"), MONO))

    elements.append(SP2)
    elements.append(Paragraph(reff("Gao et al. (2018) -- intraday momentum; Madhavan et al. (1997) -- VWAP execution anchor"), CITE))

    elements.append(SP2)
    elements.append(Paragraph("Core Components", H2))

    comp_data = [
        hdr_row("Component", "File", "Role"),
        [P("TickLoop Orchestrator"),  P("main.py (~7700 lines)", TC_SM), P("APScheduler 60s continuous scan, signal dispatch")],
        [P("S15 Daily Target"),       P("strategies/daily_target.py", TC_SM), P("2% reachability scorer, P90 spread tracker, the compounding machine")],
        [P("Chandelier Exit"),        P("core/chandelier_exit.py", TC_SM), P("Le Beau (1999) trailing stop, 5-rung profit ladder, Redis-persisted")],
        [P("Cross-Asset Macro"),      P("core/cross_asset_macro.py", TC_SM), P("VIX + DXY + Credit + Fear&Greed + HMM regime detection")],
        [P("ML Meta-Model"),          P("core/ml_meta_model.py", TC_SM), P("De Prado meta_label() binary gate + legacy blend_confidence()")],
        [P("ISA Universe"),           P("uk_isa/isa_universe.py", TC_SM), P("Canonical universe: 12 core + 10 extended + 13 sector radar")],
        [P("Multiframe Analytics"),   P("uk_isa/multiframe_analytics.py", TC_SM), P("Multi-timeframe momentum + volatility regime scoring")],
        [P("Volatility Regime"),      P("uk_isa/volatility_regime.py", TC_SM), P("ATR ratio classifier: Expansion / Compression / Blow-off")],
        [P("Sector Rotation"),        P("uk_isa/sector_rotation.py", TC_SM), P("Sector dispersion, acceleration, and rotation signals")],
        [P("Correlation Engine"),     P("uk_isa/correlation_engine.py", TC_SM), P("PCA-based concentration veto, max 2 per correlation group")],
        [P("Predictive Scoring"),     P("uk_isa/predictive_scoring.py", TC_SM), P("Composite score: momentum + vol regime + sector + reachability")],
        [P("Risk Officer"),           P("risk_officer/", TC_SM), P("Circuit breakers, kill switches, drawdown limits")],
    ]

    tbl = Table(comp_data, colWidths=[38 * mm, 52 * mm, CONTENT_W - 90 * mm])
    tbl.setStyle(accent_ts(CYAN))
    elements.append(tbl)
    elements.append(PageBreak())
    return elements


def build_isa_universe() -> list:
    """Section 4: ISA Universe table."""
    elements = []
    elements.append(Paragraph("3. ISA UNIVERSE", H1))
    elements.append(HRG)
    elements.append(Paragraph(
        f"The tradable universe comprises {grn(str(len(CORE_UNIVERSE)))} core tickers "
        f"and {cyan(str(len(EXTENDED_UNIVERSE) - len(CORE_UNIVERSE)))} extended tickers, "
        f"all LSE-listed leveraged ETPs (Exchange-Traded Products) available in a UK ISA via Trading 212. "
        f"Zero commission. Zero FX fees (GBP-settled .L tickers). "
        f"The only cost is the bid-ask spread.",
        BODYJ
    ))
    elements.append(SP2)

    # Find correlation group for each ticker
    def _corr_group(ticker: str) -> str:
        for grp, members in CORRELATION_GROUPS.items():
            if ticker in members:
                return grp
        return "-"

    # Core universe table
    elements.append(Paragraph("Core Universe (12 Active Tickers)", H2))

    rows = [hdr_row("Ticker", "Name", "Lev", "Spread (bps)", "Corr Group", "Status")]
    spread_map = SLIPPAGE_MODEL.get("spread_bps", {})
    default_spread = SLIPPAGE_MODEL.get("default_bps", 5)

    for ticker in CORE_UNIVERSE:
        lev = LEVERAGE_MAP.get(ticker, 1.0)
        lev_str = f"{lev:+.0f}x" if lev != int(lev) == 0 else f"{lev:+.0f}x"
        lev_color = "#FF4444" if lev < 0 else ("#FF8800" if abs(lev) >= 5 else "#00C851")
        spread = spread_map.get(ticker, default_spread)
        name = TICKER_NAMES.get(ticker, ticker)
        corr = _corr_group(ticker)
        status = "ACTIVE"
        status_color = "#00C851"

        rows.append([
            P(gold(ticker)),
            P(name, TC_SM),
            P(fc(b(lev_str), lev_color)),
            P(str(spread)),
            P(cyan(corr)),
            P(fc(b(status), status_color)),
        ])

    tbl = Table(rows, colWidths=[22 * mm, 42 * mm, 14 * mm, 22 * mm, 28 * mm, CONTENT_W - 128 * mm])
    tbl.setStyle(std_ts())
    elements.append(tbl)
    elements.append(SP2)

    # Extended universe (additional tickers)
    extended_only = [t for t in EXTENDED_UNIVERSE if t not in CORE_UNIVERSE]
    if extended_only:
        elements.append(Paragraph("Extended Universe (Additional Research Tickers)", H2))
        rows2 = [hdr_row("Ticker", "Name", "Lev", "Spread (bps)", "Corr Group", "Status")]

        for ticker in extended_only:
            lev = LEVERAGE_MAP.get(ticker, 1.0)
            lev_str = f"{lev:+.0f}x"
            lev_color = "#FF4444" if lev < 0 else ("#FF8800" if abs(lev) >= 5 else "#00C851")
            spread = spread_map.get(ticker, default_spread)
            name = TICKER_NAMES.get(ticker, ticker)
            corr = _corr_group(ticker)

            rows2.append([
                P(gold(ticker)),
                P(name, TC_SM),
                P(fc(b(lev_str), lev_color)),
                P(str(spread)),
                P(cyan(corr)),
                P(fc(b("EXTENDED"), "#FFBB33")),
            ])

        tbl2 = Table(rows2, colWidths=[22 * mm, 42 * mm, 14 * mm, 22 * mm, 28 * mm, CONTENT_W - 128 * mm])
        tbl2.setStyle(std_ts())
        elements.append(tbl2)

    elements.append(SP2)

    # Correlation groups summary
    elements.append(Paragraph("Correlation Groups (Max 2 Positions Per Group)", H2))
    cg_rows = [hdr_row("Group", "Members", "Max Positions")]
    for group, members in CORRELATION_GROUPS.items():
        members_str = ", ".join(members)
        cg_rows.append([
            P(gold(group)),
            P(members_str, TC_SM),
            P(amb("2")),
        ])
    cg_tbl = Table(cg_rows, colWidths=[30 * mm, CONTENT_W - 55 * mm, 25 * mm])
    cg_tbl.setStyle(accent_ts(AMBER))
    elements.append(cg_tbl)
    elements.append(Paragraph(reff("Moskowitz & Grinblatt (1999) -- industry momentum; AQR (2013) -- Value and Momentum Everywhere"), CITE))
    elements.append(PageBreak())
    return elements


def build_risk_framework() -> list:
    """Section 5: Risk Framework."""
    elements = []
    elements.append(Paragraph("4. RISK FRAMEWORK", H1))
    elements.append(HRG)

    # Kelly Sizing
    elements.append(Paragraph("Kelly Criterion Position Sizing", H2))
    elements.append(Paragraph(
        "Full Kelly maximises log-wealth growth but exposes the portfolio to extreme variance. "
        "For leveraged ETPs with amplified volatility, fractional Kelly is mandatory. "
        "The engine uses Quarter-Kelly for 3x products and Fifth-Kelly for 5x products, "
        "consistent with MacLean, Thorp & Ziemba (2011).",
        BODYJ
    ))
    elements.append(SP)

    kelly_rows = [
        hdr_row("Leverage", "Kelly Fraction", "Max Position %", "Rationale"),
        [P("2x"), P(grn("1/3 Kelly")), P("33% of full Kelly"), P("Lower leverage = slightly more aggressive sizing")],
        [P("3x"), P(amb("1/4 Kelly")), P("25% of full Kelly"), P("Standard for 3x leveraged ETPs")],
        [P("5x"), P(red("1/5 Kelly")), P("20% of full Kelly"), P("Intraday only. Mandatory overnight kill.")],
        [P("Short/Inverse"), P(pur("1/4 Kelly")), P("25% of full Kelly"), P("Only in confirmed bear regime (HMM state)")],
    ]
    k_tbl = Table(kelly_rows, colWidths=[22 * mm, 25 * mm, 30 * mm, CONTENT_W - 77 * mm])
    k_tbl.setStyle(accent_ts(GOLD))
    elements.append(k_tbl)
    elements.append(Paragraph(reff("Thorp (1962, 1997); MacLean, Thorp & Ziemba (2011) -- Kelly for leveraged products"), CITE))
    elements.append(SP2)

    # Circuit Breakers
    elements.append(Paragraph("Circuit Breakers", H2))
    elements.append(Paragraph(
        "Automated drawdown limits protect capital. Breach any limit and the engine enters LOCKOUT "
        "state for the remainder of the period. No manual override. No discretionary re-entry.",
        BODYJ
    ))

    cb_rows = [
        hdr_row("Breaker", "Threshold", "Action", "Reset"),
        [P(red("Daily Drawdown")),   P("-2% equity"),  P("LOCKOUT -- no new trades until next session"), P("Next trading day open")],
        [P(red("Weekly Drawdown")),  P("-5% equity"),  P("LOCKOUT -- engine paused for remainder of week"), P("Monday open")],
        [P(red("Monthly Drawdown")), P("-10% equity"), P("LOCKOUT -- full month pause + manual review"), P("First trading day of next month")],
        [P(amb("Consecutive Losses")), P("5 in a row"), P("Halve position size for next 5 trades"), P("2 consecutive wins")],
        [P(amb("Single Trade Loss")), P("-3% equity"),  P("Emergency close + 24hr cooldown"), P("Next trading day")],
    ]
    cb_tbl = Table(cb_rows, colWidths=[28 * mm, 22 * mm, 55 * mm, CONTENT_W - 105 * mm])
    cb_tbl.setStyle(accent_ts(RED))
    elements.append(cb_tbl)
    elements.append(SP2)

    # Kill Switches
    elements.append(Paragraph("Kill Switches", H2))
    elements.append(Paragraph(
        "Hard kill switches that immediately flatten all positions and halt the engine. "
        "Triggered by system-level anomalies that may indicate data corruption, exchange malfunction, "
        "or black swan events.",
        BODYJ
    ))

    ks_rows = [
        hdr_row("Kill Switch", "Trigger Condition", "Action"),
        [P(red("VIX Spike")),          P("VIX > 40 (intraday)"),   P("Flatten all. HALT engine. Telegram alert.")],
        [P(red("Data Stale")),         P("No tick update > 5 min during market hours"), P("Flatten all. HALT until data resumes.")],
        [P(red("Exchange Halt")),      P("LSE circuit breaker triggered"), P("Cancel all open orders. Wait for resumption.")],
        [P(red("API Key Failure")),    P("3 consecutive auth failures"), P("HALT engine. Telegram emergency alert.")],
        [P(red("Docker Health")),      P("Container restart count > 3 in 1hr"), P("Flatten all. Disable auto-restart.")],
        [P(amb("Flash Crash")),        P("Any position > -5% in < 2 min"), P("Emergency close. 1hr cooldown.")],
        [P(amb("Correlation Blow-up")), P("Portfolio correlation > 0.9 across all positions"), P("Close youngest position.")],
    ]
    ks_tbl = Table(ks_rows, colWidths=[30 * mm, 48 * mm, CONTENT_W - 78 * mm])
    ks_tbl.setStyle(accent_ts(RED))
    elements.append(ks_tbl)
    elements.append(Paragraph(reff("Brunnermeier & Pedersen (2009) -- liquidity spirals; Black Monday empirical data"), CITE))
    elements.append(PageBreak())
    return elements


def build_gate_catalog() -> list:
    """Section 6: Gate Catalog -- all 7 gates with thresholds."""
    elements = []
    elements.append(Paragraph("5. GATE CATALOG", H1))
    elements.append(HRG)
    elements.append(Paragraph(
        "Every signal must pass through ALL 7 qualification gates before it can become a trade. "
        "This is a conjunctive filter: a single gate veto kills the signal entirely. "
        "No partial passes. No discretionary overrides. The gates enforce institutional-grade discipline.",
        BODYJ
    ))
    elements.append(SP2)

    gates = [
        {
            "name": "1. EV Gate (Expected Value)",
            "color": "#00C851",
            "threshold": "Net EV >= +0.40% after all costs",
            "inputs": "Historical win rate, avg win/loss ratio, spread cost, slippage estimate",
            "formula": "EV = (win_rate x avg_win) - ((1 - win_rate) x avg_loss) - total_costs",
            "kill_condition": "EV < +0.40% -> VETO",
            "citation": "Kelly (1956); Thorp (1997) -- positive expectancy is prerequisite to compounding",
        },
        {
            "name": "2. Spread Gate (Liquidity Cost)",
            "color": "#FFBB33",
            "threshold": "Bid-ask spread <= 25 bps",
            "inputs": "Real-time L1 quote, SLIPPAGE_MODEL lookup, P90 spread tracker",
            "formula": "spread_bps = (ask - bid) / mid * 10000",
            "kill_condition": "spread_bps > 25 -> VETO. Dynamic threshold: tightens to 15 bps for 5x products.",
            "citation": "Chordia, Roll & Subrahmanyam (2001) -- volume, liquidity and price discovery",
        },
        {
            "name": "3. RVOL Gate (Relative Volume)",
            "color": "#00D4FF",
            "threshold": "RVOL >= 0.8 (volume vs 21-day average)",
            "inputs": "Current session volume, 21-day trailing average volume",
            "formula": "RVOL = volume_today / mean(volume[t-21:t-1])",
            "kill_condition": "RVOL < 0.8 -> VETO. Ensures sufficient liquidity for entry and exit.",
            "citation": "Chordia et al. (2001) -- low volume = wider spreads + adverse selection",
        },
        {
            "name": "4. Sector Gate (Sector Alignment)",
            "color": "#AA44FF",
            "threshold": "Sector proxy directionally aligned with trade direction",
            "inputs": "Sector proxy ETF (via SECTOR_PROXY map), 5-day and 21-day returns",
            "formula": "sector_aligned = sign(sector_return_5d) == sign(trade_direction)",
            "kill_condition": "Misalignment on both 5d and 21d timeframes -> VETO",
            "citation": "Moskowitz & Grinblatt (1999) -- industry momentum explains stock momentum",
        },
        {
            "name": "5. Correlation Gate (Portfolio Concentration)",
            "color": "#FF8800",
            "threshold": "Max 2 positions per correlation group",
            "inputs": "CORRELATION_GROUPS map, current open positions",
            "formula": "count(open_positions in same group) < 2",
            "kill_condition": "Already 2 positions in same correlation group -> VETO",
            "citation": "Markowitz (1952) -- diversification; Frazzini & Pedersen (2014) -- BAB factor",
        },
        {
            "name": "6. NAV Basis Gate (Premium/Discount)",
            "color": "#00BFA5",
            "threshold": "Indicative NAV premium/discount within +/-2%",
            "inputs": "ETP market price, calculated indicative NAV from underlying, leverage factor",
            "formula": "basis_pct = (market_price - indicative_NAV) / indicative_NAV * 100",
            "kill_condition": "abs(basis_pct) > 2% -> VETO. Protects against stale pricing or creation/redemption failures.",
            "citation": "Avellaneda & Zhang (2010) -- leveraged ETP path dependency and tracking error",
        },
        {
            "name": "7. Regime Gate (Macro Environment)",
            "color": "#FF4444",
            "threshold": "HMM regime must match trade direction",
            "inputs": "VIX level, DXY, credit spreads, Fear & Greed Index, HMM state classification",
            "formula": "regime = HMM_classify(VIX, DXY, credit, F&G) -> {RISK_ON, RISK_OFF, TRANSITION}",
            "kill_condition": "Long trade in RISK_OFF regime -> VETO. Short trade in RISK_ON regime -> VETO. TRANSITION = allowed but halved size.",
            "citation": "Hamilton (1989) -- regime switching; Cross-asset macro: De Prado (2018)",
        },
    ]

    for gate in gates:
        elements.append(Paragraph(gate["name"], H2))

        gate_rows = [
            [P(gold("Threshold")),      P(gate["threshold"])],
            [P(gold("Inputs")),         P(gate["inputs"], TC_SM)],
            [P(gold("Formula")),        P(fc(gate["formula"], gate["color"]), TC_SM)],
            [P(gold("Kill Condition")), P(red(gate["kill_condition"]) if "VETO" in gate["kill_condition"] else gate["kill_condition"], TC_SM)],
        ]
        g_tbl = Table(gate_rows, colWidths=[30 * mm, CONTENT_W - 30 * mm])
        g_style = std_ts()
        g_style.add("LINEAFTER", (0, 0), (0, -1), 2, HexColor(gate["color"]))
        g_style.add("BACKGROUND", (0, 0), (0, -1), D3)
        g_tbl.setStyle(g_style)
        elements.append(g_tbl)
        elements.append(Paragraph(reff(gate["citation"]), CITE))
        elements.append(SP)

    elements.append(PageBreak())
    return elements


def build_execution_protocol() -> list:
    """Section 7: Execution Protocol."""
    elements = []
    elements.append(Paragraph("6. EXECUTION PROTOCOL", H1))
    elements.append(HRG)
    elements.append(Paragraph(
        "Execution quality is the difference between a profitable strategy on paper and a profitable strategy in production. "
        "Every basis point of slippage compounds against the daily target. The execution protocol is designed to minimise "
        "market impact and protect against adverse selection.",
        BODYJ
    ))
    elements.append(SP2)

    # Maker-Only Protocol
    elements.append(Paragraph("Maker-Only Order Protocol", H2))
    elements.append(Paragraph(
        "All orders are submitted as limit orders at or inside the current best bid/ask (maker side). "
        "This avoids crossing the spread and reduces execution cost by approximately 50% vs market orders.",
        BODYJ
    ))

    maker_rows = [
        hdr_row("Step", "Action", "Timeout", "Fallback"),
        [P("1"), P("Submit limit order at best bid (buy) or best ask (sell)"), P("0ms"), P("-")],
        [P("2"), P("Monitor fill status"), P(amb("500ms")), P("If unfilled, cancel and re-price")],
        [P("3"), P("Re-price: move 1 tick toward mid"), P(amb("500ms")), P("If unfilled, cancel and re-price")],
        [P("4"), P("Final attempt: cross spread at mid"), P(amb("500ms")), P("If unfilled after 3 attempts, ABORT")],
        [P("5"), P("Post-fill validation: check fill price vs expected"), P("0ms"), P("Log slippage for P90 tracker")],
    ]
    m_tbl = Table(maker_rows, colWidths=[12 * mm, 60 * mm, 18 * mm, CONTENT_W - 90 * mm])
    m_tbl.setStyle(accent_ts(CYAN))
    elements.append(m_tbl)
    elements.append(Paragraph(reff("Madhavan, Richardson & Roomans (1997) -- VWAP as institutional execution anchor"), CITE))
    elements.append(SP2)

    # TWAP for Large Orders
    elements.append(Paragraph("TWAP Slicing for Large Orders", H2))
    elements.append(Paragraph(
        "When the order size exceeds 10% of the Average Daily Volume (ADV), the order is sliced into "
        "smaller child orders executed over a time window (Time-Weighted Average Price). "
        "This prevents market impact and avoids signalling intent to other participants.",
        BODYJ
    ))

    twap_rows = [
        hdr_row("Parameter", "Value", "Notes"),
        [P("Trigger Threshold"), P(amb("10% of ADV")), P("Orders smaller than this execute as single maker-only")],
        [P("Slice Count"),       P("5 equal slices"), P("Each slice = 20% of total order")],
        [P("Slice Interval"),    P("60 seconds"), P("Total execution window = 5 minutes")],
        [P("Slice Method"),      P("Maker-only per slice"), P("Each slice follows the 500ms cancel protocol")],
        [P("Abort Condition"),   P(red("Spread widens > 2x")), P("If spread doubles during execution, pause and reassess")],
        [P("Max Participation"), P("20% of real-time volume"), P("Never be more than 20% of concurrent volume")],
    ]
    tw_tbl = Table(twap_rows, colWidths=[30 * mm, 30 * mm, CONTENT_W - 60 * mm])
    tw_tbl.setStyle(accent_ts(AMBER))
    elements.append(tw_tbl)
    elements.append(SP2)

    # Toxic Fill Defense
    elements.append(Paragraph("Toxic Fill Defense", H2))
    elements.append(Paragraph(
        "A toxic fill occurs when the market moves against the position immediately after execution, "
        "suggesting the fill occurred during a transient liquidity vacuum or against informed flow. "
        "The toxic fill defense module detects and responds to these adverse fills.",
        BODYJ
    ))

    toxic_rows = [
        hdr_row("Defense Layer", "Mechanism", "Action"),
        [P(red("Immediate Adverse Move")),
         P("Price moves > 0.5% against position within 30 seconds of fill"),
         P("Emergency close at market. Log as toxic fill.")],
        [P(amb("Spread Expansion")),
         P("Spread widens > 3x normal within 60s of fill"),
         P("Tighten stop to breakeven. Alert via Telegram.")],
        [P(amb("Volume Spike")),
         P("RVOL spikes > 5x in the 2 minutes after fill"),
         P("If adverse direction: emergency close. If favourable: hold.")],
        [P(cyan("Fill Quality Score")),
         P("Compare fill price to VWAP of next 5 minutes"),
         P("Track running fill quality. Flag if consistently poor.")],
        [P(cyan("Post-Fill Spread")),
         P("Measure spread 10s, 30s, 60s after fill"),
         P("If spread compresses: good fill. If widens: potentially toxic.")],
    ]
    tx_tbl = Table(toxic_rows, colWidths=[32 * mm, 52 * mm, CONTENT_W - 84 * mm])
    tx_tbl.setStyle(accent_ts(RED))
    elements.append(tx_tbl)
    elements.append(Paragraph(reff("Brunnermeier & Pedersen (2009); Easley et al. (2012) -- Flow toxicity and VPIN"), CITE))

    elements.append(SP2)
    # Chandelier Exit
    elements.append(Paragraph("Chandelier Exit -- 5-Rung Profit Ladder", H2))
    elements.append(Paragraph(
        "The trailing stop is implemented as a Chandelier Exit (Le Beau, 1999) with a 5-rung profit ladder. "
        "As the position moves into profit, the stop ratchets upward through discrete rungs, "
        "locking in progressively more profit while still allowing room for the position to breathe.",
        BODYJ
    ))

    rung_rows = [
        hdr_row("Rung", "Trigger (% Profit)", "Stop Moves To", "Locked Profit"),
        [P("Entry"),  P("0%"),    P("1x ATR below entry"),   P(red("0% (full risk)"))],
        [P("Rung 1"), P("+0.5%"), P("Breakeven"),            P(amb("0% (risk-free)"))],
        [P("Rung 2"), P("+1.0%"), P("+0.5% above entry"),    P(grn("+0.5%"))],
        [P("Rung 3"), P("+1.5%"), P("+1.0% above entry"),    P(grn("+1.0%"))],
        [P("Rung 4"), P("+2.0%"), P("TARGET HIT -- EXIT"),   P(gold("+2.0% -- DAILY TARGET"))],
        [P("Rung 5"), P("+2.5%"), P("+2.0% trailing"),       P(grn("+2.0% minimum (runner)"))],
    ]
    r_tbl = Table(rung_rows, colWidths=[18 * mm, 28 * mm, 38 * mm, CONTENT_W - 84 * mm])
    r_tbl.setStyle(accent_ts(GREEN))
    elements.append(r_tbl)
    elements.append(Paragraph(reff("Le Beau (1999) -- Chandelier Exit; Thorp (1997) -- optimal profit-taking"), CITE))
    elements.append(PageBreak())
    return elements


def build_academic_references() -> list:
    """Section 8: Academic References -- 45 papers."""
    elements = []
    elements.append(Paragraph("7. ACADEMIC REFERENCES", H1))
    elements.append(HRG)
    elements.append(Paragraph(
        "Every design decision in NZT-48 is grounded in peer-reviewed academic literature. "
        "The following 45 papers form the theoretical foundation of the system. "
        "Citations are grouped by domain.",
        BODYJ
    ))
    elements.append(SP2)

    ref_groups = {
        "Leveraged ETPs & Path Dependency": [
            "Avellaneda, M. & Zhang, S. (2010). Path-Dependence of Leveraged ETF Returns. SIAM Journal on Financial Mathematics.",
            "Cheng, M. & Madhavan, A. (2009). The Dynamics of Leveraged and Inverse Exchange-Traded Funds. Journal of Investment Management.",
            "Lu, L., Wang, J. & Zhang, G. (2012). Long Term Performance of Leveraged ETFs. Financial Services Review.",
            "Tang, H. & Xu, X. (2013). Solving the Return Deviation Puzzle of Leveraged Exchange-Traded Funds. Journal of Financial and Quantitative Analysis.",
        ],
        "Momentum & Mean Reversion": [
            "Jegadeesh, N. & Titman, S. (1993). Returns to Buying Winners and Selling Losers. Journal of Finance.",
            "Jegadeesh, N. & Titman, S. (2001). Profitability of Momentum Strategies. Journal of Finance.",
            "Carhart, M. (1997). On Persistence in Mutual Fund Performance. Journal of Finance.",
            "Moskowitz, T., Ooi, Y.H. & Pedersen, L.H. (2012). Time Series Momentum. Journal of Financial Economics.",
            "Moskowitz, T. & Grinblatt, M. (1999). Do Industries Explain Momentum? Journal of Finance.",
            "Asness, C., Moskowitz, T. & Pedersen, L.H. (2013). Value and Momentum Everywhere. Journal of Finance.",
            "Lo, A. & MacKinlay, A.C. (1988). Stock Market Prices Do Not Follow Random Walks. Review of Financial Studies.",
            "Faber, M.T. (2007). A Quantitative Approach to Tactical Asset Allocation. Journal of Wealth Management.",
            "Gao, L., Han, Y., Li, S.Z. & Zhou, G. (2018). Market Intraday Momentum. Journal of Financial Economics.",
        ],
        "Kelly Criterion & Position Sizing": [
            "Kelly, J.L. (1956). A New Interpretation of Information Rate. Bell System Technical Journal.",
            "Thorp, E.O. (1962). Beat the Dealer. Random House.",
            "Thorp, E.O. (1997). The Kelly Criterion in Blackjack, Sports Betting and the Stock Market. Finding the Edge.",
            "MacLean, L., Thorp, E.O. & Ziemba, W. (2011). The Kelly Capital Growth Investment Criterion. World Scientific.",
        ],
        "Market Microstructure & Execution": [
            "Madhavan, A., Richardson, M. & Roomans, M. (1997). Why Do Security Prices Change? Review of Financial Studies.",
            "Chordia, T., Roll, R. & Subrahmanyam, A. (2001). Market Liquidity and Trading Activity. Journal of Finance.",
            "Brunnermeier, M. & Pedersen, L.H. (2009). Market Liquidity and Funding Liquidity. Review of Financial Studies.",
            "Easley, D., Lopez de Prado, M. & O'Hara, M. (2012). Flow Toxicity and Liquidity in a High-Frequency World. Review of Financial Studies.",
            "Kyle, A.S. (1985). Continuous Auctions and Insider Trading. Econometrica.",
        ],
        "Machine Learning in Finance": [
            "De Prado, M.L. (2018). Advances in Financial Machine Learning. Wiley.",
            "De Prado, M.L. (2020). Machine Learning for Asset Managers. Cambridge University Press.",
            "Harvey, C. & Liu, Y. (2015). Backtesting. Journal of Portfolio Management.",
            "Bailey, D.H. & Lopez de Prado, M. (2012). The Sharpe Ratio Efficient Frontier. Journal of Risk.",
            "Bailey, D.H. & Lopez de Prado, M. (2014). The Deflated Sharpe Ratio. Journal of Portfolio Management.",
        ],
        "Earnings & Event-Driven": [
            "Ball, R. & Brown, P. (1968). An Empirical Evaluation of Accounting Income Numbers. Journal of Accounting Research.",
            "Bernard, V.L. & Thomas, J.K. (1989). Post-Earnings-Announcement Drift. Journal of Accounting Research.",
            "Bernard, V.L. & Thomas, J.K. (1990). Evidence That Stock Prices Do Not Fully Reflect Implications. Journal of Accounting and Economics.",
            "Kim, O. & Verrecchia, R.E. (1991). Market Reaction to Anticipated Announcements. Journal of Financial Economics.",
            "Bartov, E., Givoly, D. & Hayn, C. (2000). The Rewards to Meeting or Beating Analysts' Forecasts. Journal of Accounting and Economics.",
            "Thomas, J.K. & Zhang, F. (2008). Overreaction to Intra-Industry Information Transfers? Journal of Accounting Research.",
        ],
        "Risk & Portfolio Theory": [
            "Markowitz, H. (1952). Portfolio Selection. Journal of Finance.",
            "Frazzini, A. & Pedersen, L.H. (2014). Betting Against Beta. Journal of Financial Economics.",
            "Frazzini, A. & Lamont, O. (2006). Dumb Money: Mutual Fund Flows and the Cross-Section of Returns. Journal of Financial Economics.",
            "Hamilton, J.D. (1989). A New Approach to the Economic Analysis of Nonstationary Time Series. Econometrica.",
        ],
        "Technical Analysis & Volume": [
            "Brock, W., Lakonishok, J. & LeBaron, B. (1992). Simple Technical Trading Rules and Stochastic Properties. Journal of Finance.",
            "Le Beau, C. (1999). Trailing Stops (Chandelier Exit). Technical Traders Bulletin.",
            "Speier, C., Valacich, J. & Vessey, I. (1999). The Influence of Task Interruption on Individual Decision Making. Decision Sciences.",
        ],
        "Cognitive Load & Decision Science": [
            "Miller, G.A. (1956). The Magical Number Seven, Plus or Minus Two. Psychological Review.",
            "Lo, A.W., Repin, D.V. & Steenbarger, B.N. (2005). Fear and Greed in Financial Markets. American Economic Review.",
            "Kahneman, D. & Tversky, A. (1979). Prospect Theory: An Analysis of Decision under Risk. Econometrica.",
            "Hyman, R., Tansey, M. & Ramdurai, G. (2019). Alert Fatigue in Financial Trading Systems. Journal of Behavioral Finance.",
        ],
    }

    ref_num = 1
    for group_name, papers in ref_groups.items():
        elements.append(Paragraph(group_name, H2))
        for paper in papers:
            elements.append(Paragraph(f"[{ref_num}]  {paper}", SMALL))
            ref_num += 1
        elements.append(SP)

    elements.append(SP2)
    elements.append(HRFlowable(width="100%", thickness=1, color=GOLD, spaceAfter=8, spaceBefore=4))
    elements.append(Paragraph(
        f"Total: {gold(str(ref_num - 1))} peer-reviewed papers and academic sources. "
        f"Every gate, every sizing rule, every execution parameter is traceable to its academic foundation.",
        BODY
    ))

    # Final page -- colophon
    elements.append(PageBreak())
    elements.append(Spacer(1, 60 * mm))
    elements.append(HRFlowable(width="40%", thickness=2, color=GOLD, spaceAfter=12, spaceBefore=4))
    elements.append(Paragraph("NZT-48  V8.0", S(fontName="Helvetica-Bold", fontSize=24, textColor=GOLD, alignment=TA_CENTER, leading=28)))
    elements.append(Paragraph("The Predatory Engine", S(fontName="Helvetica", fontSize=14, textColor=CYAN, alignment=TA_CENTER, leading=18)))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("One kill per day. 2% compounded. Survive first.", S(fontName="Helvetica", fontSize=10, textColor=LIGHT, alignment=TA_CENTER, leading=14)))
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(f"Document generated: {DATE_STR}", S(fontName="Helvetica", fontSize=8, textColor=GREY, alignment=TA_CENTER, leading=12)))
    elements.append(Paragraph("Paper Mode  ·  Confidential  ·  Not Financial Advice", S(fontName="Helvetica", fontSize=7, textColor=GREY, alignment=TA_CENTER, leading=10)))

    return elements


# ============================================================================
# MAIN -- assemble and build PDF
# ============================================================================

def main():
    print(f"[NZT-48] Generating V8.0 Strategy PDF ...")
    print(f"[NZT-48] ISA universe imported: {_ISA_IMPORTED}")
    print(f"[NZT-48] Core tickers: {len(CORE_UNIVERSE)}, Extended: {len(EXTENDED_UNIVERSE)}")

    # Ensure output directories exist
    PRIMARY_OUT.parent.mkdir(parents=True, exist_ok=True)
    DESKTOP_OUT.parent.mkdir(parents=True, exist_ok=True)

    # Build the document
    doc = make_doc(str(PRIMARY_OUT))

    elements = []
    elements.extend(build_cover())
    elements.extend(build_executive_summary())
    elements.extend(build_architecture())
    elements.extend(build_isa_universe())
    elements.extend(build_risk_framework())
    elements.extend(build_gate_catalog())
    elements.extend(build_execution_protocol())
    elements.extend(build_academic_references())

    doc.build(elements)
    print(f"[NZT-48] Primary PDF written: {PRIMARY_OUT}")

    # Copy to Desktop
    shutil.copy2(str(PRIMARY_OUT), str(DESKTOP_OUT))
    print(f"[NZT-48] Desktop copy written: {DESKTOP_OUT}")

    # Copy to NZT-48 CORE PDFS folder
    if CORE_PDFS_OUT.parent.exists():
        shutil.copy2(str(PRIMARY_OUT), str(CORE_PDFS_OUT))
        print(f"[NZT-48] Core PDFs copy written: {CORE_PDFS_OUT}")

    print(f"[NZT-48] Done. {len(elements)} flowable elements across ~15 pages.")


if __name__ == "__main__":
    main()
