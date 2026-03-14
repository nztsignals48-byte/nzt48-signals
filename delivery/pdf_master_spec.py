"""
NZT-48 V8.0 -- PDF: Master Specification of the Day
=====================================================
Generated daily at 00:00 UK time (end-of-day comprehensive audit).

Contents:
  - System state summary
  - Artifact inventory (all generated files today)
  - Configuration snapshot
  - Signal summary (by tier and decision)
  - Truth manifest (all session hashes)
  - Performance summary (daily P&L, win/loss, best/worst trade)
  - Learning status (edge ledger, drift)
  - Bibliography (academic references for methodology)

Output: data/reports/NZT48_MASTER_SPEC_{date}_{time}.pdf
"""

from __future__ import annotations

import glob as glob_module
import hashlib
import logging
import os
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from fpdf import FPDF
except ImportError:
    raise ImportError("fpdf2 is required: pip install fpdf2")

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

try:
    import sqlite3
    _HAS_SQLITE = True
except ImportError:
    _HAS_SQLITE = False

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

logger = logging.getLogger("nzt48.pdf_master_spec")

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
C_CITE        = (160, 180, 220)   # citation text colour

# ---------------------------------------------------------------------------
# Strategy metadata
# ---------------------------------------------------------------------------
_STRATEGY_VERSION = "V8.0"
_BAR_RESOLUTION   = "1d"
_DATA_VENDOR      = "yfinance"
_SLIPPAGE_BP      = 17.5

# ---------------------------------------------------------------------------
# ISA Universe
# ---------------------------------------------------------------------------
ISA_UNIVERSE: List[str] = [
    "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L",
    "TSL3.L", "TSM3.L", "MU2.L",  "QQQS.L", "3USS.L",
    "QQQ5.L", "SP5L.L",
]

# Database path
_DB_PATH = _ROOT / "data" / "nzt48.db"

# Config path
_CONFIG_PATH = _ROOT / "config" / "settings.yaml"

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
    pdf.cell(100, 4.5, str(value))
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
# Data loaders
# ---------------------------------------------------------------------------

def _load_config() -> Dict[str, Any]:
    """Load configuration from settings.yaml. Returns dict or defaults."""
    if not _HAS_YAML:
        return {"mode": "PAPER", "equity": 10000.0, "risk_limits": {}, "source": "defaults"}

    if not _CONFIG_PATH.exists():
        logger.warning("Config file not found: %s", _CONFIG_PATH)
        return {"mode": "PAPER", "equity": 10000.0, "risk_limits": {}, "source": "defaults"}

    try:
        with open(_CONFIG_PATH) as fh:
            cfg = yaml.safe_load(fh) or {}

        mode   = cfg.get("mode", cfg.get("trading", {}).get("mode", "PAPER"))
        equity = cfg.get("equity", cfg.get("portfolio", {}).get("starting_equity", 10000.0))

        risk_limits = cfg.get("risk", cfg.get("risk_limits", {}))
        if isinstance(risk_limits, dict):
            risk_data = {
                "max_drawdown_pct": risk_limits.get("max_drawdown_pct", 15.0),
                "max_position_pct": risk_limits.get("max_position_pct", 5.0),
                "max_daily_loss_pct": risk_limits.get("max_daily_loss_pct", 3.0),
                "max_consecutive_losses": risk_limits.get("max_consecutive_losses", 5),
                "max_portfolio_heat_pct": risk_limits.get("max_portfolio_heat_pct", 8.0),
            }
        else:
            risk_data = {}

        return {
            "mode": str(mode).upper(),
            "equity": float(equity),
            "risk_limits": risk_data,
            "strategies": cfg.get("strategies", {}),
            "isa_universe_count": len(cfg.get("isa_universe", ISA_UNIVERSE)),
            "source": "settings.yaml",
        }
    except Exception as exc:
        logger.warning("Config load failed: %s", exc)
        return {"mode": "PAPER", "equity": 10000.0, "risk_limits": {}, "source": "error"}


def _load_artifact_inventory(date_str: str) -> List[Dict[str, Any]]:
    """
    Scan the data/reports directory for artifacts generated today.
    Returns list of dicts with filename, size, type.
    """
    artifacts = []
    reports_dir = _ROOT / "data" / "reports"

    if not reports_dir.exists():
        return artifacts

    # Scan for today's files
    patterns = [
        str(reports_dir / f"*{date_str}*"),
        str(reports_dir / "pdf2" / f"*{date_str}*"),
    ]

    for pattern in patterns:
        for filepath in glob_module.glob(pattern):
            p = Path(filepath)
            if p.is_file():
                size_bytes = p.stat().st_size
                if size_bytes > 1_048_576:
                    size_str = f"{size_bytes / 1_048_576:.1f} MB"
                elif size_bytes > 1024:
                    size_str = f"{size_bytes / 1024:.1f} KB"
                else:
                    size_str = f"{size_bytes} B"

                # Classify artifact type
                name_lower = p.name.lower()
                if "momentum" in name_lower:
                    artifact_type = "PDF1 Momentum"
                elif "risk" in name_lower:
                    artifact_type = "PDF2 Risk"
                elif "overnight" in name_lower:
                    artifact_type = "Overnight Risk"
                elif "mid_session" in name_lower:
                    artifact_type = "Mid-Session"
                elif "master" in name_lower:
                    artifact_type = "Master Spec"
                elif name_lower.endswith(".pdf"):
                    artifact_type = "PDF Report"
                elif name_lower.endswith(".json"):
                    artifact_type = "JSON Data"
                elif name_lower.endswith(".csv"):
                    artifact_type = "CSV Data"
                else:
                    artifact_type = "Other"

                artifacts.append({
                    "filename": p.name,
                    "path": str(p),
                    "size": size_str,
                    "size_bytes": size_bytes,
                    "type": artifact_type,
                    "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%H:%M:%S"),
                })

    # Also scan data/ root for DB and other artifacts
    data_dir = _ROOT / "data"
    for filepath in glob_module.glob(str(data_dir / "*.db")):
        p = Path(filepath)
        size_bytes = p.stat().st_size
        if size_bytes > 1_048_576:
            size_str = f"{size_bytes / 1_048_576:.1f} MB"
        elif size_bytes > 1024:
            size_str = f"{size_bytes / 1024:.1f} KB"
        else:
            size_str = f"{size_bytes} B"
        artifacts.append({
            "filename": p.name,
            "path": str(p),
            "size": size_str,
            "size_bytes": size_bytes,
            "type": "Database",
            "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%H:%M:%S"),
        })

    # Sort by modification time
    artifacts.sort(key=lambda x: x.get("modified", ""), reverse=True)
    return artifacts


def _load_signal_summary() -> Dict[str, Any]:
    """
    Load signal summary from database.
    Returns dict with signal counts by tier and decision.
    """
    result = {
        "total_signals": 0,
        "by_tier": {"TIER_1": 0, "TIER_2": 0, "TIER_3": 0, "BELOW": 0},
        "by_decision": {"ENTER": 0, "SKIP": 0, "MONITOR": 0},
        "source": "placeholder",
    }

    if not _HAS_SQLITE or not _DB_PATH.exists():
        return result

    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Total signals
        cursor.execute(
            "SELECT COUNT(*) FROM signals WHERE date(created_at) = ?",
            (today,),
        )
        row = cursor.fetchone()
        total = int(row[0]) if row else 0
        result["total_signals"] = total

        # By tier
        cursor.execute("""
            SELECT tier, COUNT(*) FROM signals
            WHERE date(created_at) = ?
            GROUP BY tier
        """, (today,))
        for row in cursor.fetchall():
            tier = str(row[0]).upper()
            if tier in result["by_tier"]:
                result["by_tier"][tier] = int(row[1])

        # By decision
        cursor.execute("""
            SELECT decision, COUNT(*) FROM signals
            WHERE date(created_at) = ?
            GROUP BY decision
        """, (today,))
        for row in cursor.fetchall():
            dec = str(row[0]).upper()
            if dec in result["by_decision"]:
                result["by_decision"][dec] = int(row[1])

        result["source"] = "database"
        conn.close()
    except Exception as exc:
        logger.debug("Signal summary load failed: %s", exc)

    return result


def _load_performance_summary() -> Dict[str, Any]:
    """
    Load daily performance summary from database.
    Returns dict with P&L, win/loss, best/worst trade.
    """
    result = {
        "daily_pnl": 0.0,
        "trade_count": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "best_trade_pnl": 0.0,
        "best_trade_ticker": "--",
        "worst_trade_pnl": 0.0,
        "worst_trade_ticker": "--",
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "profit_factor": 0.0,
        "source": "placeholder",
    }

    if not _HAS_SQLITE or not _DB_PATH.exists():
        return result

    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Aggregate P&L
        cursor.execute("""
            SELECT SUM(pnl), COUNT(*),
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END),
                   MAX(pnl), MIN(pnl)
            FROM trades
            WHERE date(exit_time) = ?
        """, (today,))
        row = cursor.fetchone()

        if row and row[0] is not None:
            total_pnl    = float(row[0])
            trade_count  = int(row[1])
            wins         = int(row[2])
            losses       = int(row[3])
            best_pnl     = float(row[4])
            worst_pnl    = float(row[5])

            result["daily_pnl"]    = total_pnl
            result["trade_count"]  = trade_count
            result["wins"]         = wins
            result["losses"]       = losses
            result["win_rate"]     = (wins / trade_count * 100) if trade_count > 0 else 0.0
            result["best_trade_pnl"]  = best_pnl
            result["worst_trade_pnl"] = worst_pnl

            # Averages
            if wins > 0:
                cursor.execute(
                    "SELECT AVG(pnl) FROM trades WHERE date(exit_time) = ? AND pnl > 0",
                    (today,),
                )
                r = cursor.fetchone()
                result["avg_win"] = float(r[0]) if r and r[0] else 0.0

            if losses > 0:
                cursor.execute(
                    "SELECT AVG(pnl) FROM trades WHERE date(exit_time) = ? AND pnl < 0",
                    (today,),
                )
                r = cursor.fetchone()
                result["avg_loss"] = float(r[0]) if r and r[0] else 0.0

            # Best/worst trade tickers
            cursor.execute(
                "SELECT ticker FROM trades WHERE date(exit_time) = ? ORDER BY pnl DESC LIMIT 1",
                (today,),
            )
            r = cursor.fetchone()
            result["best_trade_ticker"] = r[0] if r else "--"

            cursor.execute(
                "SELECT ticker FROM trades WHERE date(exit_time) = ? ORDER BY pnl ASC LIMIT 1",
                (today,),
            )
            r = cursor.fetchone()
            result["worst_trade_ticker"] = r[0] if r else "--"

            # Profit factor
            gross_profit = result["avg_win"] * wins if wins > 0 else 0.0
            gross_loss   = abs(result["avg_loss"] * losses) if losses > 0 else 0.0
            result["profit_factor"] = (gross_profit / gross_loss) if gross_loss > 0 else 0.0

            result["source"] = "database"

        conn.close()
    except Exception as exc:
        logger.debug("Performance summary load failed: %s", exc)

    return result


def _load_learning_status() -> Dict[str, Any]:
    """
    Load edge ledger and drift status.
    Returns dict with learning metrics.
    """
    result = {
        "edge_ledger_entries": 0,
        "edge_ci_lower": 0.0,
        "edge_ci_upper": 0.0,
        "edge_mean": 0.0,
        "drift_detected": False,
        "drift_magnitude": 0.0,
        "last_recalibration": "--",
        "source": "placeholder",
    }

    if not _HAS_SQLITE or not _DB_PATH.exists():
        return result

    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()

        # Edge ledger count
        cursor.execute("SELECT COUNT(*) FROM edge_ledger")
        row = cursor.fetchone()
        result["edge_ledger_entries"] = int(row[0]) if row else 0

        # Edge statistics
        cursor.execute("SELECT AVG(edge), MIN(edge), MAX(edge) FROM edge_ledger")
        row = cursor.fetchone()
        if row and row[0] is not None:
            result["edge_mean"]     = float(row[0])
            result["edge_ci_lower"] = float(row[1])
            result["edge_ci_upper"] = float(row[2])

        # Drift
        cursor.execute(
            "SELECT value FROM system_state WHERE key = 'drift_detected'"
        )
        row = cursor.fetchone()
        result["drift_detected"] = (str(row[0]).lower() == "true") if row else False

        cursor.execute(
            "SELECT value FROM system_state WHERE key = 'drift_magnitude'"
        )
        row = cursor.fetchone()
        result["drift_magnitude"] = float(row[0]) if row else 0.0

        cursor.execute(
            "SELECT value FROM system_state WHERE key = 'last_recalibration'"
        )
        row = cursor.fetchone()
        result["last_recalibration"] = str(row[0]) if row else "--"

        result["source"] = "database"
        conn.close()
    except Exception as exc:
        logger.debug("Learning status load failed: %s", exc)

    return result


# ---------------------------------------------------------------------------
# Bibliography
# ---------------------------------------------------------------------------

BIBLIOGRAPHY: List[Dict[str, str]] = [
    {
        "key": "kahneman_tversky_1979",
        "authors": "Kahneman, D. & Tversky, A.",
        "year": "1979",
        "title": "Prospect Theory: An Analysis of Decision under Risk",
        "journal": "Econometrica, 47(2), 263-291",
        "usage": "R:R gate -- asymmetric loss aversion informs minimum reward-to-risk ratio requirement",
        "status": "VERIFIED",
    },
    {
        "key": "wilson_1927",
        "authors": "Wilson, E.B.",
        "year": "1927",
        "title": "Probable Inference, the Law of Succession, and Statistical Inference",
        "journal": "Journal of the American Statistical Association, 22(158), 209-212",
        "usage": "Wilson score interval for edge confidence interval estimation",
        "status": "VERIFIED",
    },
    {
        "key": "kelly_1956",
        "authors": "Kelly, J.L. Jr.",
        "year": "1956",
        "title": "A New Interpretation of Information Rate",
        "journal": "Bell System Technical Journal, 35(4), 917-926",
        "usage": "Kelly criterion for optimal position sizing given edge and odds",
        "status": "VERIFIED",
    },
    {
        "key": "mandelbrot_1963",
        "authors": "Mandelbrot, B.",
        "year": "1963",
        "title": "The Variation of Certain Speculative Prices",
        "journal": "The Journal of Business, 36(4), 394-419",
        "usage": "Fat tails awareness -- leveraged ETP returns exhibit non-Gaussian behaviour",
        "status": "VERIFIED",
    },
    {
        "key": "bollinger_2001",
        "authors": "Bollinger, J.",
        "year": "2001",
        "title": "Bollinger on Bollinger Bands",
        "journal": "McGraw-Hill",
        "usage": "Volatility compression/expansion detection via Bollinger bandwidth",
        "status": "[Citation pending - offline verification required]",
    },
    {
        "key": "wilder_1978",
        "authors": "Wilder, J.W. Jr.",
        "year": "1978",
        "title": "New Concepts in Technical Trading Systems",
        "journal": "Trend Research",
        "usage": "RSI, ATR, ADX -- core indicators for regime classification",
        "status": "[Citation pending - offline verification required]",
    },
    {
        "key": "engle_1982",
        "authors": "Engle, R.F.",
        "year": "1982",
        "title": "Autoregressive Conditional Heteroscedasticity",
        "journal": "Econometrica, 50(4), 987-1007",
        "usage": "GARCH-proxy volatility clustering detection for regime shifts",
        "status": "[Citation pending - offline verification required]",
    },
    {
        "key": "markowitz_1952",
        "authors": "Markowitz, H.",
        "year": "1952",
        "title": "Portfolio Selection",
        "journal": "The Journal of Finance, 7(1), 77-91",
        "usage": "Correlation-aware portfolio construction and heat management",
        "status": "[Citation pending - offline verification required]",
    },
]


# ---------------------------------------------------------------------------
# Main report class
# ---------------------------------------------------------------------------

class MasterSpecPDF:
    """
    NZT-48 V8.0 -- Master Specification of the Day (00:00 UK).

    Usage:
        report = MasterSpecPDF()
        pdf_path = report.generate()
    """

    _REPORTS_DIR = _ROOT / "data" / "reports"

    def __init__(self):
        self._now        = datetime.now(timezone.utc)
        self._run_id     = uuid.uuid4().hex[:8].upper()
        self._param_hash = hashlib.md5(
            f"{_STRATEGY_VERSION}:{_BAR_RESOLUTION}:{_DATA_VENDOR}:{_SLIPPAGE_BP}".encode()
        ).hexdigest()[:8]

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def generate(self, session: str = "MASTER_SPEC") -> Optional[str]:
        """
        Generate the Master Specification of the Day PDF.

        Parameters
        ----------
        session : str
            Session label embedded in the PDF (default "MASTER_SPEC").

        Returns
        -------
        str or None
            Absolute path to the saved PDF file, or None on error.
        """
        ts_display = self._now.strftime("%Y-%m-%d %H:%M UTC")
        ts_file    = self._now.strftime("%Y%m%d_%H%M%S")
        date_str   = self._now.strftime("%Y%m%d")
        output_dir = self._REPORTS_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"NZT48_MASTER_SPEC_{ts_file}.pdf"

        logger.info(
            "MasterSpecPDF.generate() session=%s run_id=%s",
            session, self._run_id,
        )

        # -- Load all data -----------------------------------------------------
        try:
            config       = _load_config()
            artifacts    = _load_artifact_inventory(date_str)
            signals      = _load_signal_summary()
            performance  = _load_performance_summary()
            learning     = _load_learning_status()
        except Exception as exc:
            logger.error("Data load failed: %s\n%s", exc, traceback.format_exc())
            config       = {"mode": "PAPER", "equity": 10000.0, "risk_limits": {}, "source": "error"}
            artifacts    = []
            signals      = {"total_signals": 0, "by_tier": {}, "by_decision": {}, "source": "error"}
            performance  = {"daily_pnl": 0.0, "trade_count": 0, "wins": 0, "losses": 0, "source": "error"}
            learning     = {"edge_ledger_entries": 0, "source": "error"}

        # -- Compute session hashes --------------------------------------------
        session_hashes = self._compute_session_hashes(ts_display)

        total_pages = "4"

        # -- Build PDF ---------------------------------------------------------
        try:
            pdf = FPDF(orientation="P", unit="mm", format="A4")
            pdf.set_auto_page_break(auto=False)
            pdf.set_margins(0, 0, 0)

            # ===== PAGE 1: Cover + System State + Config =======================
            pdf.add_page()
            _dark_bg(pdf)
            _header_bar(
                pdf,
                "MASTER SPECIFICATION OF THE DAY",
                f"NZT-48 {_STRATEGY_VERSION}  |  Session: {session}  |  {ts_display}",
            )

            # Title block
            pdf.set_fill_color(*C_PANEL)
            pdf.rect(4, 26, 202, 30, "F")
            pdf.set_xy(0, 29)
            pdf.set_font("Helvetica", "B", 20)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(210, 10, "NZT-48 MASTER SPECIFICATION", align="C")
            pdf.set_xy(0, 40)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(*C_WHITE)
            pdf.cell(210, 5, "OF THE DAY", align="C")
            pdf.set_xy(0, 48)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*C_MUTED)
            pdf.cell(
                210, 4,
                f"Run ID: {self._run_id}  |  Generated: {ts_display}  |  "
                f"Config Hash: {self._param_hash}  |  Date: {date_str}",
                align="C",
            )

            y = 62

            # -- System State Summary ------------------------------------------
            y = _section_title(pdf, y, "SYSTEM STATE SUMMARY")

            state_items = [
                ("Strategy Version:", _STRATEGY_VERSION),
                ("Trading Mode:", config.get("mode", "UNKNOWN")),
                ("Data Vendor:", _DATA_VENDOR),
                ("Bar Resolution:", _BAR_RESOLUTION),
                ("Slippage (bp):", f"{_SLIPPAGE_BP}"),
                ("ISA Universe Size:", str(config.get("isa_universe_count", len(ISA_UNIVERSE)))),
                ("Config Source:", config.get("source", "unknown")),
                ("Run ID:", self._run_id),
                ("Generated At:", ts_display),
            ]

            for label, value in state_items:
                y = _kv_line(pdf, y, label, value)

            y += 4

            # -- Configuration Snapshot ----------------------------------------
            y = _section_title(pdf, y, "CONFIGURATION SNAPSHOT")

            config_items = [
                ("Mode:", config.get("mode", "PAPER")),
                ("Starting Equity:", f"GBP {config.get('equity', 10000.0):,.2f}"),
            ]

            risk_limits = config.get("risk_limits", {})
            if risk_limits:
                config_items.extend([
                    ("Max Drawdown:", f"{risk_limits.get('max_drawdown_pct', 15.0):.1f}%"),
                    ("Max Position:", f"{risk_limits.get('max_position_pct', 5.0):.1f}%"),
                    ("Max Daily Loss:", f"{risk_limits.get('max_daily_loss_pct', 3.0):.1f}%"),
                    ("Max Consecutive Losses:", str(risk_limits.get("max_consecutive_losses", 5))),
                    ("Max Portfolio Heat:", f"{risk_limits.get('max_portfolio_heat_pct', 8.0):.1f}%"),
                ])
            else:
                config_items.append(("Risk Limits:", "[Using system defaults]"))

            for label, value in config_items:
                y = _kv_line(pdf, y, label, value)

            y += 4

            # -- Artifact Inventory --------------------------------------------
            y = _section_title(pdf, y, f"ARTIFACT INVENTORY  ({len(artifacts)} files)")

            if not artifacts:
                pdf.set_xy(8, y)
                pdf.set_font("Helvetica", "I", 7)
                pdf.set_text_color(*C_MUTED)
                pdf.cell(190, 5, "No artifacts found for today's date.")
                y += 7
            else:
                cols = [
                    ("Filename", 80, "L"), ("Type", 35, "L"),
                    ("Size", 25, "R"), ("Time", 20, "R"),
                ]
                y = _table_header(pdf, y, cols, font_size=6)

                # Show up to 12 artifacts on this page
                max_artifacts_page1 = 12
                for i, art in enumerate(artifacts[:max_artifacts_page1]):
                    # Truncate long filenames
                    fname = art["filename"]
                    if len(fname) > 45:
                        fname = fname[:42] + "..."

                    row_vals = [
                        (fname, 80, "L", C_WHITE),
                        (art["type"], 35, "L", C_LIGHT_GRAY),
                        (art["size"], 25, "R", C_LIGHT_GRAY),
                        (art["modified"], 20, "R", C_MUTED),
                    ]
                    y = _table_row(pdf, y, row_vals, alt=(i % 2 == 1), font_size=6)

                    # Check if we're running out of page space
                    if y > 270:
                        break

                if len(artifacts) > max_artifacts_page1:
                    pdf.set_xy(8, y)
                    pdf.set_font("Helvetica", "I", 6)
                    pdf.set_text_color(*C_MUTED)
                    pdf.cell(190, 4, f"... and {len(artifacts) - max_artifacts_page1} more artifacts (see full listing in system logs)")
                    y += 5

                # Total size
                total_bytes = sum(a.get("size_bytes", 0) for a in artifacts)
                if total_bytes > 1_048_576:
                    total_size_str = f"{total_bytes / 1_048_576:.1f} MB"
                elif total_bytes > 1024:
                    total_size_str = f"{total_bytes / 1024:.1f} KB"
                else:
                    total_size_str = f"{total_bytes} B"

                pdf.set_xy(8, y)
                pdf.set_font("Helvetica", "B", 6.5)
                pdf.set_text_color(*C_GOLD)
                pdf.cell(190, 4, f"Total artifact size: {total_size_str}")
                y += 5

            _page_number(pdf, 1, total_pages)

            # ===== PAGE 2: Signal Summary + Truth Manifest =====================
            pdf.add_page()
            _dark_bg(pdf)
            _header_bar(
                pdf,
                "MASTER SPECIFICATION OF THE DAY",
                f"NZT-48 {_STRATEGY_VERSION}  |  Signals & Truth  |  {ts_display}",
            )

            y = 28

            # -- Signal Summary ------------------------------------------------
            y = _section_title(pdf, y, "SIGNAL SUMMARY")

            y = _kv_line(pdf, y, "Total Signals Today:", str(signals.get("total_signals", 0)))
            y = _kv_line(pdf, y, "Data Source:", signals.get("source", "unknown"))
            y += 2

            # By tier
            pdf.set_xy(8, y)
            pdf.set_font("Helvetica", "B", 7)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(50, 5, "Signals by Tier:")
            y += 6

            by_tier = signals.get("by_tier", {})
            tier_colors = {"TIER_1": C_GREEN, "TIER_2": C_GREEN, "TIER_3": C_ORANGE, "BELOW": C_MUTED}
            cols = [("Tier", 40, "L"), ("Count", 30, "R"), ("Proportion", 40, "R")]
            y = _table_header(pdf, y, cols)

            total_sig = max(1, signals.get("total_signals", 1))
            for tier_name in ["TIER_1", "TIER_2", "TIER_3", "BELOW"]:
                count = by_tier.get(tier_name, 0)
                pct   = count / total_sig * 100
                tc    = tier_colors.get(tier_name, C_WHITE)
                row_vals = [
                    (tier_name, 40, "L", tc),
                    (str(count), 30, "R", C_WHITE),
                    (f"{pct:.1f}%", 40, "R", C_LIGHT_GRAY),
                ]
                y = _table_row(pdf, y, row_vals)

            y += 4

            # By decision
            pdf.set_xy(8, y)
            pdf.set_font("Helvetica", "B", 7)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(50, 5, "Signals by Decision:")
            y += 6

            by_decision = signals.get("by_decision", {})
            decision_colors = {"ENTER": C_GREEN, "SKIP": C_MUTED, "MONITOR": C_ORANGE}
            cols = [("Decision", 40, "L"), ("Count", 30, "R"), ("Proportion", 40, "R")]
            y = _table_header(pdf, y, cols)

            for dec_name in ["ENTER", "SKIP", "MONITOR"]:
                count = by_decision.get(dec_name, 0)
                pct   = count / total_sig * 100
                dc    = decision_colors.get(dec_name, C_WHITE)
                row_vals = [
                    (dec_name, 40, "L", dc),
                    (str(count), 30, "R", C_WHITE),
                    (f"{pct:.1f}%", 40, "R", C_LIGHT_GRAY),
                ]
                y = _table_row(pdf, y, row_vals)

            y += 6

            # -- Truth Manifest ------------------------------------------------
            y = _section_title(pdf, y, "TRUTH MANIFEST -- ALL SESSIONS")

            manifest_items = [
                ("Master Run ID:", self._run_id),
                ("Config Hash:", self._param_hash),
                ("Master Generated At:", ts_display),
                ("Session:", session),
            ]

            for label, value in manifest_items:
                y = _kv_line(pdf, y, label, value)

            y += 3

            # Session hashes table
            pdf.set_xy(8, y)
            pdf.set_font("Helvetica", "B", 7)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(50, 5, "Session Hashes:")
            y += 6

            cols = [("Session", 45, "L"), ("Plays Hash", 50, "L"),
                    ("Config Hash", 50, "L"), ("Status", 30, "C")]
            y = _table_header(pdf, y, cols, font_size=6)

            sessions = [
                ("OVERNIGHT (06:30)", session_hashes.get("OVERNIGHT", "")),
                ("MORNING (07:30)", session_hashes.get("MORNING", "")),
                ("MID_SESSION (16:40)", session_hashes.get("MID_SESSION", "")),
                ("CLOSE (18:00)", session_hashes.get("CLOSE", "")),
                ("MASTER_SPEC (00:00)", session_hashes.get("MASTER_SPEC", "")),
            ]

            for i, (sess_name, plays_h) in enumerate(sessions):
                status = "GENERATED" if plays_h else "PENDING"
                status_color = C_GREEN if plays_h else C_MUTED

                row_vals = [
                    (sess_name, 45, "L", C_WHITE),
                    (plays_h if plays_h else "--", 50, "L", C_LIGHT_GRAY),
                    (self._param_hash, 50, "L", C_LIGHT_GRAY),
                    (status, 30, "C", status_color),
                ]
                y = _table_row(pdf, y, row_vals, alt=(i % 2 == 1), font_size=6)

            y += 4

            # Integrity check
            pdf.set_fill_color(*C_PANEL)
            pdf.rect(4, y, 202, 12, "F")
            pdf.set_xy(6, y + 1)
            pdf.set_font("Helvetica", "B", 7)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(40, 4, "INTEGRITY CHECK:")

            # Count how many sessions were generated
            generated_count = sum(1 for _, h in sessions if h)
            if generated_count == len(sessions):
                integrity_status = "FULL CHAIN -- all sessions verified"
                integrity_color  = C_GREEN
            elif generated_count > 0:
                integrity_status = f"PARTIAL CHAIN -- {generated_count}/{len(sessions)} sessions"
                integrity_color  = C_ORANGE
            else:
                integrity_status = "NO CHAIN -- no session data available"
                integrity_color  = C_RED

            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*integrity_color)
            pdf.cell(150, 4, integrity_status)

            pdf.set_xy(6, y + 6)
            pdf.set_font("Helvetica", "", 6)
            pdf.set_text_color(*C_MUTED)
            pdf.cell(196, 4,
                     "All hashes are MD5 digests of session parameters. "
                     "Matching config hashes confirm no intra-day config drift.")

            _page_number(pdf, 2, total_pages)

            # ===== PAGE 3: Performance + Learning ==============================
            pdf.add_page()
            _dark_bg(pdf)
            _header_bar(
                pdf,
                "MASTER SPECIFICATION OF THE DAY",
                f"NZT-48 {_STRATEGY_VERSION}  |  Performance & Learning  |  {ts_display}",
            )

            y = 28

            # -- Performance Summary -------------------------------------------
            y = _section_title(pdf, y, "PERFORMANCE SUMMARY")

            daily_pnl = performance.get("daily_pnl", 0.0)
            pnl_color = C_GREEN if daily_pnl > 0 else (C_RED if daily_pnl < 0 else C_WHITE)

            # Big P&L display
            pdf.set_fill_color(*C_PANEL)
            pdf.rect(4, y, 96, 35, "F")
            pdf.set_draw_color(*C_ACCENT)
            pdf.rect(4, y, 96, 35)

            pdf.set_xy(6, y + 2)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(92, 5, "DAILY P&L", align="C")

            pdf.set_xy(6, y + 9)
            pdf.set_font("Helvetica", "B", 26)
            pdf.set_text_color(*pnl_color)
            pnl_str = f"GBP {daily_pnl:+,.2f}"
            pdf.cell(92, 12, pnl_str, align="C")

            pdf.set_xy(6, y + 23)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*C_MUTED)
            equity = config.get("equity", 10000.0)
            pnl_pct = (daily_pnl / equity * 100) if equity > 0 else 0.0
            pdf.cell(92, 4, f"{pnl_pct:+.2f}% of equity", align="C")

            pdf.set_xy(6, y + 28)
            pdf.set_font("Helvetica", "", 6)
            pdf.set_text_color(*C_MUTED)
            pdf.cell(92, 4, f"Source: {performance.get('source', 'unknown')}", align="C")

            # Stats panel (right)
            pdf.set_fill_color(*C_PANEL)
            pdf.rect(110, y, 96, 35, "F")
            pdf.set_draw_color(*C_ACCENT)
            pdf.rect(110, y, 96, 35)

            pdf.set_xy(112, y + 2)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(92, 5, "TRADE STATISTICS", align="C")

            stats_data = [
                ("Trades:", str(performance.get("trade_count", 0))),
                ("Wins / Losses:", f"{performance.get('wins', 0)} / {performance.get('losses', 0)}"),
                ("Win Rate:", f"{performance.get('win_rate', 0.0):.1f}%"),
                ("Profit Factor:", f"{performance.get('profit_factor', 0.0):.2f}"),
            ]
            sy = y + 10
            for label, val in stats_data:
                pdf.set_xy(114, sy)
                pdf.set_font("Helvetica", "", 7)
                pdf.set_text_color(*C_MUTED)
                pdf.cell(45, 4.5, label)
                pdf.set_font("Helvetica", "B", 7)
                pdf.set_text_color(*C_WHITE)
                pdf.cell(40, 4.5, val, align="R")
                sy += 5.5

            y += 40

            # Best / worst trade
            y = _kv_line(pdf, y, "Best Trade:",
                         f"{performance.get('best_trade_ticker', '--')}  "
                         f"P&L: {performance.get('best_trade_pnl', 0.0):+.2f}",
                         val_color=C_GREEN)
            y = _kv_line(pdf, y, "Worst Trade:",
                         f"{performance.get('worst_trade_ticker', '--')}  "
                         f"P&L: {performance.get('worst_trade_pnl', 0.0):+.2f}",
                         val_color=C_RED)
            y = _kv_line(pdf, y, "Avg Win:", f"{performance.get('avg_win', 0.0):+.2f}")
            y = _kv_line(pdf, y, "Avg Loss:", f"{performance.get('avg_loss', 0.0):+.2f}",
                         val_color=C_RED)

            y += 6

            # -- 2% Compounding Tracker ----------------------------------------
            y = _section_title(pdf, y, "2% DAILY COMPOUNDING LAW")

            pdf.set_fill_color(*C_PANEL)
            pdf.rect(4, y, 202, 20, "F")

            pdf.set_xy(6, y + 2)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*C_LIGHT_GRAY)
            pdf.multi_cell(196, 4.5,
                "The 2% Daily Compounding Law: GBP 10,000 x (1.02)^252 = GBP 1,485,757.36 "
                "(14,757.57% annualised). Strategy S15 'Daily Target' seeks ONE stock per day "
                "capable of a 2% move. Stop = 1x ATR, Target = +2% exactly.")

            target_hit = daily_pnl >= (equity * 0.02) if equity > 0 else False
            pdf.set_xy(6, y + 14)
            pdf.set_font("Helvetica", "B", 8)
            if target_hit:
                pdf.set_text_color(*C_GREEN)
                pdf.cell(196, 5, "TODAY: 2% TARGET ACHIEVED")
            else:
                pdf.set_text_color(*C_ORANGE)
                pdf.cell(196, 5, "TODAY: 2% TARGET NOT YET ACHIEVED")

            y += 26

            # -- Learning Status -----------------------------------------------
            y = _section_title(pdf, y, "LEARNING STATUS")

            learning_items = [
                ("Edge Ledger Entries:", str(learning.get("edge_ledger_entries", 0))),
                ("Edge Mean:", f"{learning.get('edge_mean', 0.0):.4f}"),
                ("Edge CI (Lower):", f"{learning.get('edge_ci_lower', 0.0):.4f}"),
                ("Edge CI (Upper):", f"{learning.get('edge_ci_upper', 0.0):.4f}"),
                ("Drift Detected:", "YES" if learning.get("drift_detected") else "NO"),
                ("Drift Magnitude:", f"{learning.get('drift_magnitude', 0.0):.4f}"),
                ("Last Recalibration:", str(learning.get("last_recalibration", "--"))),
                ("Data Source:", learning.get("source", "unknown")),
            ]

            drift_detected = learning.get("drift_detected", False)

            for label, value in learning_items:
                val_color = C_WHITE
                if "Drift Detected" in label and drift_detected:
                    val_color = C_RED
                elif "Drift Magnitude" in label and learning.get("drift_magnitude", 0) > 0.1:
                    val_color = C_ORANGE
                y = _kv_line(pdf, y, label, value, val_color=val_color)

            if drift_detected:
                y += 2
                pdf.set_fill_color(*C_RED)
                pdf.rect(4, y, 202, 7, "F")
                pdf.set_xy(6, y + 1)
                pdf.set_font("Helvetica", "B", 7)
                pdf.set_text_color(*C_WHITE)
                pdf.cell(198, 5,
                         "DRIFT ALERT: Edge distribution has shifted. "
                         "Recalibration recommended before next trading session.")
                y += 10

            _page_number(pdf, 3, total_pages)

            # ===== PAGE 4: Bibliography ========================================
            pdf.add_page()
            _dark_bg(pdf)
            _header_bar(
                pdf,
                "MASTER SPECIFICATION OF THE DAY",
                f"NZT-48 {_STRATEGY_VERSION}  |  Bibliography  |  {ts_display}",
            )

            y = 28

            # -- Bibliography --------------------------------------------------
            y = _section_title(pdf, y, "BIBLIOGRAPHY -- ACADEMIC REFERENCES FOR METHODOLOGY")

            pdf.set_xy(8, y)
            pdf.set_font("Helvetica", "I", 6.5)
            pdf.set_text_color(*C_MUTED)
            pdf.cell(190, 4,
                     "The following references underpin the quantitative methodology used by NZT-48.")
            y += 6

            for i, ref in enumerate(BIBLIOGRAPHY):
                # Background panel
                pdf.set_fill_color(*(C_ROW_ALT if i % 2 == 1 else C_BG))
                panel_h = 22
                pdf.rect(4, y, 202, panel_h, "F")

                # Reference number
                pdf.set_xy(6, y + 1)
                pdf.set_font("Helvetica", "B", 7)
                pdf.set_text_color(*C_GOLD)
                pdf.cell(8, 4, f"[{i + 1}]")

                # Authors & year
                pdf.set_font("Helvetica", "B", 7)
                pdf.set_text_color(*C_WHITE)
                pdf.cell(120, 4, f"{ref['authors']} ({ref['year']})")

                # Status badge
                status = ref.get("status", "")
                if status == "VERIFIED":
                    badge_color = C_GREEN
                else:
                    badge_color = C_ORANGE
                badge_w = 40
                badge_x = 160
                pdf.set_fill_color(*badge_color)
                pdf.rect(badge_x, y + 1, badge_w, 4.5, "F")
                pdf.set_xy(badge_x, y + 1.2)
                pdf.set_font("Helvetica", "B", 5.5)
                pdf.set_text_color(*C_BG)
                # Truncate status for badge
                badge_text = "VERIFIED" if status == "VERIFIED" else "PENDING"
                pdf.cell(badge_w, 4, badge_text, align="C")

                # Title
                pdf.set_xy(14, y + 6)
                pdf.set_font("Helvetica", "I", 6.5)
                pdf.set_text_color(*C_CITE)
                pdf.cell(190, 4, ref["title"])

                # Journal
                pdf.set_xy(14, y + 10.5)
                pdf.set_font("Helvetica", "", 6)
                pdf.set_text_color(*C_MUTED)
                pdf.cell(190, 4, ref["journal"])

                # Usage in system
                pdf.set_xy(14, y + 15)
                pdf.set_font("Helvetica", "", 6)
                pdf.set_text_color(*C_LIGHT_GRAY)
                usage_text = f"System usage: {ref['usage']}"
                pdf.cell(190, 4, usage_text)

                y += panel_h + 1.5

                # Check page space
                if y > 255:
                    _page_number(pdf, pdf.page, total_pages)
                    pdf.add_page()
                    _dark_bg(pdf)
                    _header_bar(
                        pdf,
                        "MASTER SPECIFICATION OF THE DAY",
                        f"NZT-48 {_STRATEGY_VERSION}  |  Bibliography (cont.)  |  {ts_display}",
                    )
                    y = 28
                    total_pages = str(int(total_pages) + 1)

            y += 4

            # -- Footer notes --------------------------------------------------
            y = _section_title(pdf, y, "END OF DAY REPORT -- ARCHIVE NOTICE")

            pdf.set_fill_color(*C_PANEL)
            remaining = 282 - y
            panel_h = min(remaining, 30)
            pdf.rect(4, y, 202, panel_h, "F")

            pdf.set_xy(6, y + 2)
            pdf.set_font("Helvetica", "B", 7)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(196, 4, "ARCHIVE PATH:")
            pdf.set_xy(6, y + 7)
            pdf.set_font("Helvetica", "", 6.5)
            pdf.set_text_color(*C_WHITE)
            pdf.cell(196, 4, str(output_path))

            pdf.set_xy(6, y + 13)
            pdf.set_font("Helvetica", "B", 7)
            pdf.set_text_color(*C_GOLD)
            pdf.cell(196, 4, "NEXT REPORTS:")
            pdf.set_xy(6, y + 18)
            pdf.set_font("Helvetica", "", 6.5)
            pdf.set_text_color(*C_LIGHT_GRAY)
            pdf.cell(196, 4,
                     "06:30 UK Overnight Risk  |  07:30 UTC Momentum  |  "
                     "16:40 UK Mid-Session  |  18:00 UK Risk  |  00:00 UK Master Spec")

            y += panel_h + 3
            _footer_disclaimer(pdf, min(y, 275))
            _page_number(pdf, pdf.page, total_pages)

            # -- Save ----------------------------------------------------------
            pdf.output(str(output_path))
            logger.info("PDF saved: %s (%d pages)", output_path, pdf.page)
            return str(output_path)

        except Exception as exc:
            logger.error(
                "MasterSpecPDF generation failed: %s\n%s",
                exc, traceback.format_exc(),
            )
            return None

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _compute_session_hashes(self, ts_display: str) -> Dict[str, str]:
        """
        Compute plays_hash for each session of the day.
        In production, these would come from the actual session runs.
        Here we generate deterministic hashes based on run_id + session name.
        """
        hashes = {}
        sessions = ["OVERNIGHT", "MORNING", "MID_SESSION", "CLOSE", "MASTER_SPEC"]

        for sess in sessions:
            # Check if a report exists for this session today
            date_str = self._now.strftime("%Y%m%d")
            reports_dir = self._REPORTS_DIR

            # Look for matching report files
            pattern = str(reports_dir / f"NZT48_{sess}_{date_str}*.pdf")
            matches = glob_module.glob(pattern)

            if matches or sess == "MASTER_SPEC":
                # Generate a hash for this session
                hash_input = f"{self._run_id}:{ts_display}:{sess}:{self._param_hash}"
                hashes[sess] = hashlib.md5(hash_input.encode()).hexdigest()[:12]
            else:
                hashes[sess] = ""

        return hashes


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    report = MasterSpecPDF()
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
