"""
NZT-48 V8.0 -- PDF: Mid-Session Risk Check
============================================
Generated daily at 16:40 UK time (mid-session check).

Contents:
  - Open positions status with exit scores
  - Regime shift detection (since morning)
  - P&L snapshot (daily)
  - Exit recommendations for high exit-score positions
  - Remaining opportunity window assessment
  - Risk metrics (drawdown, consecutive losses, portfolio heat)

Output: data/reports/NZT48_MID_SESSION_{date}_{time}.pdf
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

try:
    import sqlite3
    _HAS_SQLITE = True
except ImportError:
    _HAS_SQLITE = False

# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("nzt48.pdf_mid_session")

# ---------------------------------------------------------------------------
# Optional project imports (fail gracefully)
# ---------------------------------------------------------------------------
try:
    from uk_isa.volatility_regime import VolatilityRegimeClassifier
    _HAS_VOL_REGIME = True
except Exception:
    _HAS_VOL_REGIME = False
    logger.warning("volatility_regime unavailable -- using inline fallback")

try:
    from uk_isa.predictive_scoring import PredictiveScoringEngine
    _HAS_SCORING = True
except Exception:
    _HAS_SCORING = False
    logger.warning("predictive_scoring unavailable -- using inline fallback")

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
C_CRITICAL    = (220, 30,  30)    # critical alert

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

TICKER_NAMES: Dict[str, str] = {
    "QQQ3.L":  "QQQ 3x Long",
    "3LUS.L":  "US 3x Long",
    "3SEM.L":  "Semis 3x Long",
    "GPT3.L":  "AI/GPT 3x Long",
    "NVD3.L":  "NVDA 3x Long",
    "TSL3.L":  "Tesla 3x Long",
    "TSM3.L":  "TSMC 3x Long",
    "MU2.L":   "Micron 2x Long",
    "QQQS.L":  "QQQ 3x Short",
    "3USS.L":  "US 3x Short",
    "QQQ5.L":  "QQQ 5x Long",
    "SP5L.L":  "S&P 5x Long",
}

# Database path
_DB_PATH = _ROOT / "data" / "nzt48.db"

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
    pdf.set_fill_color(*C_GOLD)
    pdf.rect(0, 22, 210, 1.2, "F")
    pdf.set_xy(6, 4)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*C_GOLD)
    pdf.cell(0, 7, title)
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
# Exit score computation
# ---------------------------------------------------------------------------

def _compute_exit_score(entry_price: float, current_price: float,
                        atr: float, holding_hours: float) -> float:
    """
    Compute an exit urgency score (0-100) for an open position.

    Factors:
    - Distance from entry (profit target / stop distance)
    - Time in trade (holding decay)
    - ATR-relative move
    """
    if entry_price <= 0 or atr <= 0:
        return 50.0

    pnl_pct  = (current_price - entry_price) / entry_price * 100
    atr_move = abs(current_price - entry_price) / atr

    score = 0.0

    # Profit component: reward taking profits
    if pnl_pct >= 2.0:
        score += 40.0  # At target -- strong exit signal
    elif pnl_pct >= 1.0:
        score += 25.0
    elif pnl_pct >= 0.5:
        score += 10.0

    # Loss component: penalise holding losers
    if pnl_pct <= -2.0:
        score += 35.0  # Below stop -- urgent exit
    elif pnl_pct <= -1.0:
        score += 20.0
    elif pnl_pct <= -0.5:
        score += 10.0

    # ATR component: large moves are regime signals
    if atr_move >= 2.0:
        score += 15.0
    elif atr_move >= 1.5:
        score += 10.0

    # Time decay: trades held too long lose edge
    if holding_hours >= 48:
        score += 10.0
    elif holding_hours >= 24:
        score += 5.0

    return min(100.0, score)


def _exit_recommendation(exit_score: float, pnl_pct: float) -> tuple:
    """
    Returns (action, color) based on exit score and P&L.
    """
    if exit_score >= 70:
        if pnl_pct > 0:
            return "TAKE PROFIT NOW", C_GREEN
        else:
            return "CUT LOSS NOW", C_RED
    elif exit_score >= 50:
        if pnl_pct > 0:
            return "TRAIL STOP TIGHT", C_GREEN
        else:
            return "REVIEW & CONSIDER EXIT", C_ORANGE
    elif exit_score >= 30:
        return "MONITOR CLOSELY", C_ORANGE
    else:
        return "HOLD -- NO ACTION", C_WHITE


# ---------------------------------------------------------------------------
# Regime classification (inline fallback)
# ---------------------------------------------------------------------------

def _classify_regime_inline(ticker: str) -> Dict[str, Any]:
    """
    Classify the current regime for a ticker using inline logic.
    Returns dict with regime info.
    """
    if not _HAS_YF:
        return {"regime": "UNKNOWN", "confidence": 0.0, "detail": "yfinance unavailable"}

    try:
        df = yf.download(ticker, period="60d", interval="1d",
                         auto_adjust=True, progress=False, threads=False)
        if df is None or df.empty or len(df) < 20:
            return {"regime": "UNKNOWN", "confidence": 0.0, "detail": "insufficient data"}

        if isinstance(df.columns, __import__("pandas").MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close = df["Close"].values.astype(float)
        high  = df["High"].values.astype(float)
        low   = df["Low"].values.astype(float)

        # Simple regime: compare recent volatility to longer-term
        recent_vol = float(np.std(np.diff(np.log(close[-6:]))) * np.sqrt(252) * 100) if _HAS_NP and len(close) > 6 else 0.0
        longer_vol = float(np.std(np.diff(np.log(close[-21:]))) * np.sqrt(252) * 100) if _HAS_NP and len(close) > 21 else 0.0

        # ATR
        if len(close) > 14:
            tr = []
            for i in range(1, len(close)):
                tr.append(max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1])))
            atr = float(np.mean(tr[-14:])) if _HAS_NP else 0.0
            atr_pct = atr / close[-1] * 100 if close[-1] > 0 else 0.0
        else:
            atr_pct = 0.0

        # 5d return
        chg_5d = (close[-1] - close[-6]) / close[-6] * 100 if len(close) > 5 and close[-6] != 0 else 0.0

        if recent_vol > longer_vol * 1.5 and atr_pct > 5:
            regime = "BLOW_OFF"
        elif recent_vol < longer_vol * 0.6:
            regime = "COMPRESSION"
        elif chg_5d > 3 and recent_vol > longer_vol:
            regime = "EXPANSION"
        elif chg_5d < -5:
            regime = "BREAKDOWN"
        else:
            regime = "NORMAL"

        confidence = min(1.0, abs(recent_vol - longer_vol) / max(longer_vol, 1.0))

        return {
            "regime": regime,
            "confidence": confidence,
            "recent_vol": recent_vol,
            "longer_vol": longer_vol,
            "atr_pct": atr_pct,
            "chg_5d": chg_5d,
            "detail": f"5d_chg={chg_5d:.1f}%, vol_ratio={recent_vol/(longer_vol+0.01):.2f}",
        }

    except Exception as exc:
        logger.warning("Regime classification failed for %s: %s", ticker, exc)
        return {"regime": "UNKNOWN", "confidence": 0.0, "detail": str(exc)[:60]}


# ---------------------------------------------------------------------------
# Simulated data loaders (positions, P&L)
# ---------------------------------------------------------------------------

def _load_open_positions() -> List[Dict[str, Any]]:
    """
    Load open positions from the database.
    Falls back to placeholder data if DB is unavailable.
    """
    positions = []

    # Try loading from database
    if _HAS_SQLITE and _DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(_DB_PATH))
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ticker, side, entry_price, quantity, entry_time, strategy
                FROM positions
                WHERE status = 'OPEN'
                ORDER BY entry_time DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            for row in rows:
                positions.append({
                    "ticker": row[0],
                    "side": row[1],
                    "entry_price": float(row[2]),
                    "quantity": int(row[3]),
                    "entry_time": row[4],
                    "strategy": row[5] or "S15",
                })
            if positions:
                return positions
        except Exception as exc:
            logger.debug("DB position load failed (expected in paper mode): %s", exc)

    # Placeholder positions for report structure demonstration
    logger.info("Using placeholder position data for report structure")
    return [
        {
            "ticker": "QQQ3.L", "side": "LONG", "entry_price": 82.50,
            "quantity": 100, "entry_time": "2025-01-27 09:15:00", "strategy": "S15",
        },
        {
            "ticker": "NVD3.L", "side": "LONG", "entry_price": 145.20,
            "quantity": 50, "entry_time": "2025-01-27 10:30:00", "strategy": "S15",
        },
        {
            "ticker": "3SEM.L", "side": "LONG", "entry_price": 28.40,
            "quantity": 200, "entry_time": "2025-01-26 14:20:00", "strategy": "S15",
        },
    ]


def _load_daily_pnl() -> Dict[str, Any]:
    """
    Load daily P&L data.
    Falls back to placeholder structure if unavailable.
    """
    if _HAS_SQLITE and _DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(_DB_PATH))
            cursor = conn.cursor()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            cursor.execute("""
                SELECT SUM(pnl) as total_pnl,
                       COUNT(*) as trade_count,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses
                FROM trades
                WHERE date(exit_time) = ?
            """, (today,))
            row = cursor.fetchone()
            conn.close()

            if row and row[0] is not None:
                return {
                    "total_pnl": float(row[0]),
                    "trade_count": int(row[1]),
                    "wins": int(row[2]),
                    "losses": int(row[3]),
                    "source": "database",
                }
        except Exception as exc:
            logger.debug("DB P&L load failed: %s", exc)

    # Placeholder
    return {
        "total_pnl": 0.0,
        "trade_count": 0,
        "wins": 0,
        "losses": 0,
        "source": "placeholder",
    }


def _load_risk_metrics() -> Dict[str, Any]:
    """
    Load portfolio risk metrics.
    Falls back to computed defaults if unavailable.
    """
    if _HAS_SQLITE and _DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(_DB_PATH))
            cursor = conn.cursor()
            cursor.execute("""
                SELECT value FROM system_state WHERE key = 'max_drawdown'
            """)
            row = cursor.fetchone()
            max_dd = float(row[0]) if row else 0.0

            cursor.execute("""
                SELECT value FROM system_state WHERE key = 'consecutive_losses'
            """)
            row = cursor.fetchone()
            consec_losses = int(float(row[0])) if row else 0

            cursor.execute("""
                SELECT value FROM system_state WHERE key = 'portfolio_heat'
            """)
            row = cursor.fetchone()
            heat = float(row[0]) if row else 0.0

            conn.close()

            return {
                "max_drawdown_pct": max_dd,
                "consecutive_losses": consec_losses,
                "portfolio_heat_pct": heat,
                "source": "database",
            }
        except Exception as exc:
            logger.debug("DB risk metrics load failed: %s", exc)

    return {
        "max_drawdown_pct": 0.0,
        "consecutive_losses": 0,
        "portfolio_heat_pct": 0.0,
        "source": "placeholder",
    }


# ---------------------------------------------------------------------------
# Main report class
# ---------------------------------------------------------------------------

class MidSessionRiskPDF:
    """
    NZT-48 V8.0 -- Mid-Session Risk Check (16:40 UK).

    Usage:
        report = MidSessionRiskPDF()
        pdf_path = report.generate()
    """

    _REPORTS_DIR = _ROOT / "data" / "reports"

    def __init__(self):
        self._now        = datetime.now(timezone.utc)
        self._run_id     = uuid.uuid4().hex[:8].upper()
        self._param_hash = hashlib.md5(
            f"{_STRATEGY_VERSION}:{_BAR_RESOLUTION}:{_DATA_VENDOR}".encode()
        ).hexdigest()[:8]

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def generate(self, session: str = "MID_SESSION") -> Optional[str]:
        """
        Generate the Mid-Session Risk Check PDF.

        Parameters
        ----------
        session : str
            Session label embedded in the PDF (default "MID_SESSION").

        Returns
        -------
        str or None
            Absolute path to the saved PDF file, or None on error.
        """
        ts_display = self._now.strftime("%Y-%m-%d %H:%M UTC")
        ts_file    = self._now.strftime("%Y%m%d_%H%M%S")
        output_dir = self._REPORTS_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"NZT48_MID_SESSION_{ts_file}.pdf"

        logger.info(
            "MidSessionRiskPDF.generate() session=%s run_id=%s",
            session, self._run_id,
        )

        # -- Load data ---------------------------------------------------------
        try:
            positions    = _load_open_positions()
            daily_pnl    = _load_daily_pnl()
            risk_metrics = _load_risk_metrics()
        except Exception as exc:
            logger.error("Data load failed: %s\n%s", exc, traceback.format_exc())
            positions    = []
            daily_pnl    = {"total_pnl": 0.0, "trade_count": 0, "wins": 0, "losses": 0, "source": "error"}
            risk_metrics = {"max_drawdown_pct": 0.0, "consecutive_losses": 0, "portfolio_heat_pct": 0.0, "source": "error"}

        # -- Fetch current prices for open positions ---------------------------
        position_data = []
        for pos in positions:
            ticker = pos["ticker"]
            current_price = 0.0
            atr = 0.0
            if _HAS_YF:
                try:
                    df = yf.download(ticker, period="30d", interval="1d",
                                     auto_adjust=True, progress=False, threads=False)
                    if df is not None and not df.empty:
                        if isinstance(df.columns, __import__("pandas").MultiIndex):
                            df.columns = df.columns.get_level_values(0)
                        current_price = float(df["Close"].iloc[-1])
                        if _HAS_NP and len(df) > 14:
                            close = df["Close"].values.astype(float)
                            high  = df["High"].values.astype(float)
                            low   = df["Low"].values.astype(float)
                            tr = []
                            for i in range(1, len(close)):
                                tr.append(max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1])))
                            atr = float(np.mean(tr[-14:]))
                except Exception as exc:
                    logger.warning("Price fetch failed for %s: %s", ticker, exc)

            entry_price = pos["entry_price"]
            pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 and current_price > 0 else 0.0
            pnl_abs = (current_price - entry_price) * pos.get("quantity", 0) if current_price > 0 else 0.0

            # Holding time
            holding_hours = 0.0
            try:
                entry_dt = datetime.strptime(pos["entry_time"], "%Y-%m-%d %H:%M:%S")
                holding_hours = (self._now.replace(tzinfo=None) - entry_dt).total_seconds() / 3600.0
            except Exception:
                holding_hours = 24.0  # default assumption

            exit_score = _compute_exit_score(entry_price, current_price, atr, holding_hours)
            action, action_color = _exit_recommendation(exit_score, pnl_pct)

            position_data.append({
                **pos,
                "current_price": current_price,
                "pnl_pct": pnl_pct,
                "pnl_abs": pnl_abs,
                "atr": atr,
                "exit_score": exit_score,
                "action": action,
                "action_color": action_color,
                "holding_hours": holding_hours,
            })

        # -- Regime shift detection --------------------------------------------
        regime_data = {}
        # Check regime for a reference index (use QQQ3.L as proxy)
        ref_ticker = "QQQ3.L"
        regime_info = _classify_regime_inline(ref_ticker)
        regime_data["current"] = regime_info

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
                "MID-SESSION RISK CHECK",
                f"NZT-48 {_STRATEGY_VERSION}  |  Session: {session}  |  {ts_display}",
            )

            # -- Title block ---------------------------------------------------
            pdf.set_fill_color(*C_PANEL)
            pdf.rect(4, 26, 202, 24, "F")
            pdf.set_xy(0, 28)
            pdf.set_font("Helvetica", "B", 20)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(210, 10, "NZT-48 MID-SESSION RISK CHECK", align="C")
            pdf.set_xy(0, 40)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*C_MUTED)
            pdf.cell(
                210, 5,
                f"Run ID: {self._run_id}  |  Generated: {ts_display}  |  "
                f"Config Hash: {self._param_hash}",
                align="C",
            )

            y = 56

            # -- Open Positions Status -----------------------------------------
            y = _section_title(pdf, y, f"OPEN POSITIONS STATUS  ({len(position_data)} positions)")

            if not position_data:
                pdf.set_xy(8, y)
                pdf.set_font("Helvetica", "I", 7)
                pdf.set_text_color(*C_MUTED)
                pdf.cell(190, 5, "No open positions found. All flat.")
                y += 8
            else:
                cols = [
                    ("Ticker", 22, "L"), ("Side", 14, "C"), ("Entry", 20, "R"),
                    ("Current", 20, "R"), ("P&L %", 18, "R"), ("P&L Abs", 22, "R"),
                    ("Exit Score", 20, "C"), ("Action", 40, "L"), ("Hrs Held", 18, "R"),
                ]
                y = _table_header(pdf, y, cols, font_size=6)

                for i, pd_row in enumerate(position_data):
                    pnl_color = C_GREEN if pd_row["pnl_pct"] > 0 else (C_RED if pd_row["pnl_pct"] < 0 else C_WHITE)

                    # Exit score color
                    es = pd_row["exit_score"]
                    if es >= 70:
                        es_color = C_RED
                    elif es >= 50:
                        es_color = C_ORANGE
                    elif es >= 30:
                        es_color = C_ORANGE
                    else:
                        es_color = C_GREEN

                    current_str = f"{pd_row['current_price']:.2f}" if pd_row["current_price"] > 0 else "--"
                    pnl_abs_str = f"{pd_row['pnl_abs']:+.2f}" if pd_row["current_price"] > 0 else "--"

                    row_vals = [
                        (pd_row["ticker"], 22, "L", C_WHITE),
                        (pd_row["side"], 14, "C", C_GREEN if pd_row["side"] == "LONG" else C_RED),
                        (f"{pd_row['entry_price']:.2f}", 20, "R", C_LIGHT_GRAY),
                        (current_str, 20, "R", C_WHITE),
                        (f"{pd_row['pnl_pct']:+.2f}%", 18, "R", pnl_color),
                        (pnl_abs_str, 22, "R", pnl_color),
                        (f"{es:.0f}", 20, "C", es_color),
                        (pd_row["action"], 40, "L", pd_row["action_color"]),
                        (f"{pd_row['holding_hours']:.0f}h", 18, "R", C_LIGHT_GRAY),
                    ]
                    y = _table_row(pdf, y, row_vals, alt=(i % 2 == 1), font_size=6)

            y += 4

            # -- Regime Shift Detection ----------------------------------------
            y = _section_title(pdf, y, "REGIME SHIFT DETECTION")

            current_regime = regime_data.get("current", {})
            regime_label   = current_regime.get("regime", "UNKNOWN")
            regime_conf    = current_regime.get("confidence", 0.0)
            regime_detail  = current_regime.get("detail", "")

            regime_colors = {
                "EXPANSION": C_GREEN, "COMPRESSION": C_ORANGE,
                "BLOW_OFF": C_RED, "BREAKDOWN": C_RED,
                "NORMAL": C_WHITE, "UNKNOWN": C_MUTED,
                "EXHAUSTION": C_ORANGE,
            }
            regime_color = regime_colors.get(regime_label, C_WHITE)

            y = _kv_line(pdf, y, "Current Regime:", regime_label, val_color=regime_color)
            y = _kv_line(pdf, y, "Confidence:", f"{regime_conf:.0%}")
            y = _kv_line(pdf, y, "Detail:", regime_detail)

            # Shift assessment
            pdf.set_xy(8, y)
            pdf.set_font("Helvetica", "I", 6.5)
            pdf.set_text_color(*C_MUTED)
            pdf.cell(190, 4, "[Regime comparison vs. morning session requires cached morning state -- not yet implemented]")
            y += 7

            # Regime-specific warning
            if regime_label in ("BLOW_OFF", "BREAKDOWN"):
                pdf.set_fill_color(*C_CRITICAL)
                pdf.rect(4, y, 202, 8, "F")
                pdf.set_xy(6, y + 1)
                pdf.set_font("Helvetica", "B", 7)
                pdf.set_text_color(*C_WHITE)
                warning_msg = (
                    f"WARNING: {regime_label} regime detected. "
                    "Tighten all stops to 0.5x ATR. Consider partial de-risk of leveraged positions."
                )
                pdf.cell(198, 6, warning_msg)
                y += 12
            else:
                y += 2

            # -- P&L Snapshot --------------------------------------------------
            y = _section_title(pdf, y, "P&L SNAPSHOT")

            total_pnl = daily_pnl.get("total_pnl", 0.0)
            pnl_color = C_GREEN if total_pnl > 0 else (C_RED if total_pnl < 0 else C_WHITE)

            # Big P&L display
            pdf.set_fill_color(*C_PANEL)
            pdf.rect(4, y, 96, 30, "F")
            pdf.set_draw_color(*C_ACCENT)
            pdf.rect(4, y, 96, 30)

            pdf.set_xy(6, y + 2)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(92, 5, "DAILY P&L", align="C")

            pdf.set_xy(6, y + 9)
            pdf.set_font("Helvetica", "B", 24)
            pdf.set_text_color(*pnl_color)
            pdf.cell(92, 12, f"{total_pnl:+.2f}", align="C")

            pdf.set_xy(6, y + 23)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*C_MUTED)
            source_label = daily_pnl.get("source", "unknown")
            pdf.cell(92, 5, f"Source: {source_label}", align="C")

            # Trade stats (right panel)
            pdf.set_fill_color(*C_PANEL)
            pdf.rect(110, y, 96, 30, "F")
            pdf.set_draw_color(*C_ACCENT)
            pdf.rect(110, y, 96, 30)

            pdf.set_xy(112, y + 2)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(92, 5, "TRADE STATISTICS", align="C")

            stats_y = y + 10
            stats = [
                ("Total Trades:", str(daily_pnl.get("trade_count", 0))),
                ("Wins:", str(daily_pnl.get("wins", 0))),
                ("Losses:", str(daily_pnl.get("losses", 0))),
            ]
            for label, val in stats:
                pdf.set_xy(114, stats_y)
                pdf.set_font("Helvetica", "", 7)
                pdf.set_text_color(*C_MUTED)
                pdf.cell(45, 4.5, label)
                pdf.set_font("Helvetica", "B", 7)
                pdf.set_text_color(*C_WHITE)
                pdf.cell(40, 4.5, val, align="R")
                stats_y += 5.5

            y += 36

            # -- Exit Recommendations ------------------------------------------
            y = _section_title(pdf, y, "EXIT RECOMMENDATIONS")

            high_exit = [p for p in position_data if p["exit_score"] >= 50]
            if not high_exit:
                pdf.set_xy(8, y)
                pdf.set_font("Helvetica", "", 7)
                pdf.set_text_color(*C_GREEN)
                pdf.cell(190, 5, "No positions with elevated exit scores. All positions within normal parameters.")
                y += 8
            else:
                for i, pos in enumerate(sorted(high_exit, key=lambda x: -x["exit_score"])):
                    pdf.set_fill_color(*(C_ROW_ALT if i % 2 == 1 else C_BG))
                    pdf.rect(4, y, 202, 10, "F")

                    pdf.set_xy(6, y + 0.5)
                    pdf.set_font("Helvetica", "B", 7)
                    pdf.set_text_color(*C_WHITE)
                    pdf.cell(25, 4, pos["ticker"])

                    pdf.set_font("Helvetica", "", 7)
                    pdf.set_text_color(*pos["action_color"])
                    pdf.cell(45, 4, pos["action"])

                    pdf.set_text_color(*C_MUTED)
                    pdf.cell(30, 4, f"Exit Score: {pos['exit_score']:.0f}")

                    pnl_c = C_GREEN if pos["pnl_pct"] > 0 else C_RED
                    pdf.set_text_color(*pnl_c)
                    pdf.cell(25, 4, f"P&L: {pos['pnl_pct']:+.2f}%")

                    # Second line: reasoning
                    pdf.set_xy(6, y + 5)
                    pdf.set_font("Helvetica", "I", 6)
                    pdf.set_text_color(*C_MUTED)
                    reason = f"Held {pos['holding_hours']:.0f}h | Entry {pos['entry_price']:.2f} | Current {pos['current_price']:.2f}"
                    pdf.cell(196, 4, reason)

                    y += 11

            _page_number(pdf, 1, "2")

            # ===== PAGE 2 =====================================================
            pdf.add_page()
            _dark_bg(pdf)
            _header_bar(
                pdf,
                "MID-SESSION RISK CHECK",
                f"NZT-48 {_STRATEGY_VERSION}  |  Risk Metrics  |  {ts_display}",
            )

            y = 28

            # -- Remaining Opportunity Window ----------------------------------
            y = _section_title(pdf, y, "REMAINING OPPORTUNITY WINDOW")

            now_hour = self._now.hour
            # LSE closes at 16:30 UK (15:30 UTC in winter, 15:30 UTC in summer)
            # For simplicity, use 16:30 UK = roughly 16:30 UTC
            lse_close_hour = 16  # approximate in UTC terms
            lse_close_min  = 30

            hours_remaining = max(0, lse_close_hour - now_hour)
            if now_hour >= lse_close_hour:
                window_status = "CLOSED"
                window_color  = C_RED
                window_detail = "LSE is closed. No further entries today."
            elif hours_remaining <= 1:
                window_status = "CLOSING SOON"
                window_color  = C_ORANGE
                window_detail = f"~{hours_remaining}h remaining. Only take high-conviction setups."
            elif hours_remaining <= 3:
                window_status = "AFTERNOON SESSION"
                window_color  = C_ORANGE
                window_detail = f"~{hours_remaining}h remaining. Standard protocols apply."
            else:
                window_status = "ACTIVE"
                window_color  = C_GREEN
                window_detail = f"~{hours_remaining}h remaining. Full opportunity window open."

            pdf.set_fill_color(*C_PANEL)
            pdf.rect(4, y, 202, 22, "F")

            pdf.set_xy(6, y + 2)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(40, 5, "Window Status:")
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(*window_color)
            pdf.cell(60, 5, window_status)

            pdf.set_xy(6, y + 9)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*C_LIGHT_GRAY)
            pdf.cell(196, 4, window_detail)

            pdf.set_xy(6, y + 15)
            pdf.set_font("Helvetica", "", 6.5)
            pdf.set_text_color(*C_MUTED)
            pdf.cell(196, 4, f"Current time: {ts_display}  |  LSE close: ~16:30 UK")

            y += 28

            # -- Risk Metrics --------------------------------------------------
            y = _section_title(pdf, y, "RISK METRICS")

            max_dd     = risk_metrics.get("max_drawdown_pct", 0.0)
            consec_l   = risk_metrics.get("consecutive_losses", 0)
            heat       = risk_metrics.get("portfolio_heat_pct", 0.0)

            # Drawdown gauge
            metrics_panel = [
                ("Max Drawdown", f"{max_dd:.1f}%",
                 C_RED if max_dd > 10 else (C_ORANGE if max_dd > 5 else C_GREEN),
                 "CRITICAL" if max_dd > 15 else ("ELEVATED" if max_dd > 10 else ("WARNING" if max_dd > 5 else "NORMAL")),
                 "Max equity drawdown from peak. Threshold: 15% = circuit breaker."),
                ("Consecutive Losses", str(consec_l),
                 C_RED if consec_l >= 5 else (C_ORANGE if consec_l >= 3 else C_GREEN),
                 "HALT TRADING" if consec_l >= 5 else ("REDUCE SIZE" if consec_l >= 3 else "NORMAL"),
                 "Sequential losing trades. 5+ triggers mandatory pause."),
                ("Portfolio Heat", f"{heat:.1f}%",
                 C_RED if heat > 6 else (C_ORANGE if heat > 3 else C_GREEN),
                 "OVEREXPOSED" if heat > 6 else ("ELEVATED" if heat > 3 else "NORMAL"),
                 "Total capital at risk across all open positions."),
            ]

            for i, (metric_name, metric_val, metric_color, metric_status, metric_desc) in enumerate(metrics_panel):
                panel_y = y + i * 22
                pdf.set_fill_color(*C_PANEL)
                pdf.rect(4, panel_y, 202, 20, "F")
                pdf.set_draw_color(*C_ACCENT)
                pdf.rect(4, panel_y, 202, 20)

                # Metric name and value
                pdf.set_xy(6, panel_y + 1)
                pdf.set_font("Helvetica", "B", 8)
                pdf.set_text_color(*C_GOLD)
                pdf.cell(50, 5, metric_name)

                pdf.set_font("Helvetica", "B", 16)
                pdf.set_text_color(*metric_color)
                pdf.cell(30, 5, metric_val)

                # Status badge
                pdf.set_font("Helvetica", "B", 7)
                badge_w = 28
                pdf.set_fill_color(*metric_color)
                badge_x = 160
                pdf.rect(badge_x, panel_y + 2, badge_w, 5.5, "F")
                pdf.set_xy(badge_x, panel_y + 2.3)
                pdf.set_text_color(*C_BG)
                pdf.cell(badge_w, 5, metric_status, align="C")

                # Description
                pdf.set_xy(6, panel_y + 9)
                pdf.set_font("Helvetica", "", 6.5)
                pdf.set_text_color(*C_MUTED)
                pdf.cell(196, 4, metric_desc)

                # Progress bar
                bar_x = 6
                bar_y_pos = panel_y + 14
                bar_w = 196
                bar_h = 3
                pdf.set_fill_color(40, 40, 60)
                pdf.rect(bar_x, bar_y_pos, bar_w, bar_h, "F")

                # Fill based on metric severity
                if metric_name == "Max Drawdown":
                    fill_pct = min(1.0, max_dd / 20.0)
                elif metric_name == "Consecutive Losses":
                    fill_pct = min(1.0, consec_l / 7.0)
                else:
                    fill_pct = min(1.0, heat / 10.0)

                pdf.set_fill_color(*metric_color)
                pdf.rect(bar_x, bar_y_pos, bar_w * fill_pct, bar_h, "F")

            y += len(metrics_panel) * 22 + 6

            # -- Circuit breaker status ----------------------------------------
            y = _section_title(pdf, y, "CIRCUIT BREAKER STATUS")

            breakers = [
                ("Drawdown Circuit Breaker (15%)", max_dd >= 15, max_dd, 15.0),
                ("Consecutive Loss Halt (5)", consec_l >= 5, float(consec_l), 5.0),
                ("Portfolio Heat Limit (8%)", heat >= 8, heat, 8.0),
            ]

            for i, (breaker_name, is_triggered, current_val, threshold) in enumerate(breakers):
                status_color = C_RED if is_triggered else C_GREEN
                status_label = "TRIGGERED" if is_triggered else "ARMED"

                pdf.set_fill_color(*(C_ROW_ALT if i % 2 == 1 else C_BG))
                pdf.rect(4, y, 202, 6, "F")

                pdf.set_xy(6, y + 0.5)
                pdf.set_font("Helvetica", "", 7)
                pdf.set_text_color(*C_WHITE)
                pdf.cell(80, 5, breaker_name)

                pdf.set_font("Helvetica", "B", 7)
                pdf.set_text_color(*status_color)
                pdf.cell(25, 5, status_label, align="C")

                pdf.set_text_color(*C_MUTED)
                pdf.set_font("Helvetica", "", 7)
                pdf.cell(40, 5, f"Current: {current_val:.1f} / {threshold:.0f}", align="R")

                y += 6.5

            y += 6

            # -- Truth Manifest ------------------------------------------------
            y = _section_title(pdf, y, "TRUTH MANIFEST")
            plays_hash = hashlib.md5(
                f"{self._run_id}:{ts_display}:{session}".encode()
            ).hexdigest()[:12]

            y = _kv_line(pdf, y, "Run ID:", self._run_id)
            y = _kv_line(pdf, y, "Plays Hash:", plays_hash)
            y = _kv_line(pdf, y, "Config Hash:", self._param_hash)
            y = _kv_line(pdf, y, "Generated At:", ts_display)
            y = _kv_line(pdf, y, "Session:", session)
            y = _kv_line(pdf, y, "Open Positions:", str(len(position_data)))
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
                "MidSessionRiskPDF generation failed: %s\n%s",
                exc, traceback.format_exc(),
            )
            return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    report = MidSessionRiskPDF()
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
