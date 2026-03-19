"""AEGIS V2 — Session Briefing PDF Generator.

Generates a pre-session PDF report showing:
- Which tickers are active for this session
- Exchange breakdown and market hours
- Session schedule (Asian/European/American/US-Only)
- Top opportunities ranked by composite score
- Core 12 ISA ETP status
- Risk parameters and portfolio state

Uses fpdf2 (pure Python, no system deps).

Usage:
    python3 -m python_brain.ouroboros.session_pdf --session asian
    python3 -m python_brain.ouroboros.session_pdf --session european
    python3 -m python_brain.ouroboros.session_pdf --session us_only
    python3 -m python_brain.ouroboros.session_pdf  # generates all-session overview
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
REPORTS_DIR = DATA_DIR / "session_reports"
WATCHLIST_FILE = CONFIG_DIR / "active_watchlist.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SessionPDF] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("session_pdf")

# ---------------------------------------------------------------------------
# Session definitions
# ---------------------------------------------------------------------------

SESSION_EXCHANGES = {
    "asian": {"HKEX", "TSE", "SGX", "KRX", "ASX", "NZX", "XNZE"},
    "european": {"LSE", "XETRA", "EURONEXT_PA", "EURONEXT_AS", "SIX"},
    "american": {"NYSE", "NASDAQ", "AMEX"},
    "us_only": {"NYSE", "NASDAQ", "AMEX"},
}

SESSION_HOURS = {
    "asian": {
        "NZX":  ("21:00 UTC (Sun)", "05:00 UTC"),
        "TSE":  ("00:00 UTC", "06:00 UTC"),
        "KRX":  ("00:00 UTC", "06:30 UTC"),
        "SGX":  ("01:00 UTC", "09:00 UTC"),
        "HKEX": ("01:30 UTC", "08:00 UTC"),
    },
    "european": {
        "LSE":       ("08:00 UTC", "16:30 UTC"),
        "XETRA":     ("07:00 UTC", "15:30 UTC"),
        "EURONEXT":  ("07:00 UTC", "15:30 UTC"),
        "SIX":       ("07:00 UTC", "15:20 UTC"),
        "NYSE":      ("14:30 UTC", "21:00 UTC"),
        "NASDAQ":    ("14:30 UTC", "21:00 UTC"),
    },
    "american": {
        "NYSE":   ("14:30 UTC", "21:00 UTC"),
        "NASDAQ": ("14:30 UTC", "21:00 UTC"),
        "AMEX":   ("14:30 UTC", "21:00 UTC"),
    },
    "us_only": {
        "NYSE":   ("16:30 UTC", "21:00 UTC"),
        "NASDAQ": ("16:30 UTC", "21:00 UTC"),
        "AMEX":   ("16:30 UTC", "21:00 UTC"),
        "Note":   ("EU markets closed", "US-only trading"),
    },
}

CORE_12 = {
    "QQQ3.L", "QQQS.L", "3LUS.L", "3USS.L", "QQQ5.L", "5SPY.L",
    "3SEM.L", "NVD3.L", "TSL3.L", "GPT3.L", "TSM3.L", "MU2.L",
}

# ---------------------------------------------------------------------------
# Colours (institutional dark theme adapted for PDF)
# ---------------------------------------------------------------------------

NAVY = (10, 22, 40)
GOLD = (212, 160, 23)
WHITE = (255, 255, 255)
LIGHT_GREY = (240, 240, 245)
DARK_GREY = (60, 60, 70)
GREEN = (34, 139, 34)
RED = (180, 30, 30)
BLUE = (41, 98, 255)

# ---------------------------------------------------------------------------
# PDF builder
# ---------------------------------------------------------------------------

class SessionBriefingPDF(FPDF):
    """Custom PDF with AEGIS branding."""

    def __init__(self, session: str):
        super().__init__()
        self.session = session
        self.set_auto_page_break(auto=True, margin=15)

    def header(self):
        self.set_fill_color(*NAVY)
        self.rect(0, 0, 210, 25, 'F')
        self.set_text_color(*GOLD)
        self.set_font("Helvetica", "B", 14)
        self.set_xy(10, 6)
        self.cell(0, 8, f"AEGIS V2 - {self.session.upper()} SESSION BRIEFING", ln=True)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*WHITE)
        self.set_xy(10, 15)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self.cell(0, 5, f"Generated: {ts}  |  UK ISA Paper Mode  |  CONFIDENTIAL", ln=True)
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"AEGIS V2 | Page {self.page_no()}/{{nb}} | "
                  f"{self.session.upper()} Session", align="C")

    def section_title(self, title: str):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*NAVY)
        self.set_fill_color(*LIGHT_GREY)
        self.cell(0, 8, f"  {title}", fill=True, ln=True)
        self.ln(2)

    def kv_row(self, key: str, value: str, bold_value: bool = False):
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*DARK_GREY)
        self.cell(50, 5, key)
        self.set_font("Helvetica", "B" if bold_value else "", 8)
        self.set_text_color(0, 0, 0)
        self.cell(0, 5, str(value), ln=True)


def _load_watchlist(session: str = "all") -> Dict[str, Any]:
    """Load the active watchlist JSON. Prefers session-specific file if available."""
    if session != "all":
        session_file = CONFIG_DIR / f"active_watchlist_{session}.json"
        if session_file.exists():
            log.info("Using session-specific watchlist: %s", session_file)
            with open(session_file) as f:
                return json.load(f)

    if not WATCHLIST_FILE.exists():
        log.error("Watchlist not found: %s", WATCHLIST_FILE)
        return {}
    with open(WATCHLIST_FILE) as f:
        return json.load(f)


def _filter_session_tickers(
    tickers: List[Dict[str, Any]], session: str
) -> List[Dict[str, Any]]:
    """Filter tickers to session-appropriate exchanges."""
    if session not in SESSION_EXCHANGES:
        return tickers
    exchanges = SESSION_EXCHANGES[session]
    return [t for t in tickers if t.get("exchange", "Unknown") in exchanges]


def generate_session_pdf(session: str = "all") -> Optional[Path]:
    """Generate the session briefing PDF. Returns path to PDF file."""
    if FPDF is None:
        log.error("fpdf2 not installed — run: pip install fpdf2")
        return None

    watchlist = _load_watchlist(session)
    if not watchlist:
        log.error("Empty or missing watchlist — cannot generate PDF")
        return None

    vanguard = watchlist.get("vanguard", [])
    warm = watchlist.get("warm", [])

    # Filter to session
    if session != "all":
        session_vanguard = _filter_session_tickers(vanguard, session)
        session_warm = _filter_session_tickers(warm, session)
    else:
        session_vanguard = vanguard
        session_warm = warm

    # Sort by composite score descending
    session_vanguard.sort(key=lambda t: t.get("composite_score", 0), reverse=True)

    # Build PDF
    pdf = SessionBriefingPDF(session)
    pdf.alias_nb_pages()
    pdf.add_page()

    # ── Section 1: Session Overview ──
    pdf.section_title("SESSION OVERVIEW")
    now = datetime.now(timezone.utc)
    pdf.kv_row("Session:", session.upper(), bold_value=True)
    pdf.kv_row("Date:", now.strftime("%A, %d %B %Y"))
    pdf.kv_row("Generated:", now.strftime("%H:%M:%S UTC"))
    pdf.kv_row("Vanguard Tickers:", str(len(session_vanguard)))
    pdf.kv_row("Warm Tickers:", str(len(session_warm)))
    pdf.kv_row("Total Active:", str(len(session_vanguard) + len(session_warm)))

    # Exchange breakdown
    exchange_counts = {}
    for t in session_vanguard:
        ex = t.get("exchange", "Unknown")
        exchange_counts[ex] = exchange_counts.get(ex, 0) + 1
    if exchange_counts:
        pdf.kv_row("Exchange Breakdown:",
                    "  ".join(f"{k}: {v}" for k, v in sorted(exchange_counts.items(),
                                                              key=lambda x: -x[1])))
    pdf.ln(3)

    # ── Section 2: Market Hours ──
    pdf.section_title("MARKET HOURS")
    hours = SESSION_HOURS.get(session, {})
    if hours:
        pdf.set_font("Helvetica", "", 8)
        for exchange, (open_t, close_t) in sorted(hours.items()):
            pdf.set_text_color(*DARK_GREY)
            pdf.cell(30, 5, exchange)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(0, 5, f"{open_t}  -  {close_t}", ln=True)
    else:
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 5, "All exchanges (full overview mode)", ln=True)
    pdf.ln(3)

    # ── Section 3: Core 12 ISA ETPs ──
    core_in_session = [t for t in session_vanguard if t.get("symbol") in CORE_12]
    if core_in_session:
        pdf.section_title(f"CORE 12 ISA ETPs ({len(core_in_session)} in session)")
        # Table header
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_fill_color(*NAVY)
        pdf.set_text_color(*WHITE)
        col_widths = [22, 50, 12, 18, 22, 22, 22, 22]
        headers = ["Symbol", "Name", "Lev", "Exchange", "Price", "Vol%", "Mom%", "Score"]
        for i, h in enumerate(headers):
            pdf.cell(col_widths[i], 5, h, fill=True, border=1)
        pdf.ln()

        # Table rows
        pdf.set_font("Helvetica", "", 7)
        for j, t in enumerate(core_in_session):
            bg = LIGHT_GREY if j % 2 == 0 else WHITE
            pdf.set_fill_color(*bg)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(col_widths[0], 5, t.get("symbol", ""), fill=True, border=1)
            name = t.get("name", "")[:25]
            pdf.cell(col_widths[1], 5, name, fill=True, border=1)
            pdf.cell(col_widths[2], 5, f"{t.get('leverage_factor', 1)}x", fill=True, border=1)
            pdf.cell(col_widths[3], 5, t.get("exchange", ""), fill=True, border=1)
            price = t.get("last_price", 0)
            pdf.cell(col_widths[4], 5, f"{price:.2f}" if price else "N/A", fill=True, border=1)
            vol = t.get("volatility_ann", 0) * 100
            pdf.cell(col_widths[5], 5, f"{vol:.1f}%", fill=True, border=1)
            mom = t.get("momentum_pct", 0)
            color = GREEN if mom > 0 else RED if mom < 0 else DARK_GREY
            pdf.set_text_color(*color)
            pdf.cell(col_widths[6], 5, f"{mom:+.1f}%", fill=True, border=1)
            pdf.set_text_color(0, 0, 0)
            score = t.get("composite_score", 0)
            pdf.cell(col_widths[7], 5, f"{score:.3f}", fill=True, border=1)
            pdf.ln()
        pdf.ln(3)

    # ── Section 4: Top 30 Opportunities ──
    non_core = [t for t in session_vanguard if t.get("symbol") not in CORE_12]
    top_n = non_core[:30]
    if top_n:
        pdf.section_title(f"TOP {len(top_n)} OPPORTUNITIES (by composite score)")
        # Table header
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_fill_color(*NAVY)
        pdf.set_text_color(*WHITE)
        col_widths = [5, 22, 42, 12, 18, 20, 20, 18, 18, 15]
        headers = ["#", "Symbol", "Name", "Lev", "Exchange", "Price", "Vol%", "Mom%", "AvgVol", "Score"]
        for i, h in enumerate(headers):
            pdf.cell(col_widths[i], 5, h, fill=True, border=1)
        pdf.ln()

        # Table rows
        pdf.set_font("Helvetica", "", 6.5)
        for j, t in enumerate(top_n):
            bg = LIGHT_GREY if j % 2 == 0 else WHITE
            pdf.set_fill_color(*bg)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(col_widths[0], 4.5, str(j + 1), fill=True, border=1)
            pdf.cell(col_widths[1], 4.5, t.get("symbol", ""), fill=True, border=1)
            name = t.get("name", "")[:22]
            pdf.cell(col_widths[2], 4.5, name, fill=True, border=1)
            pdf.cell(col_widths[3], 4.5, f"{t.get('leverage_factor', 1)}x", fill=True, border=1)
            pdf.cell(col_widths[4], 4.5, t.get("exchange", ""), fill=True, border=1)
            price = t.get("last_price", 0)
            pdf.cell(col_widths[5], 4.5, f"{price:.2f}" if price else "N/A", fill=True, border=1)
            vol = t.get("volatility_ann", 0) * 100
            pdf.cell(col_widths[6], 4.5, f"{vol:.1f}%", fill=True, border=1)
            mom = t.get("momentum_pct", 0)
            color = GREEN if mom > 0 else RED if mom < 0 else DARK_GREY
            pdf.set_text_color(*color)
            pdf.cell(col_widths[7], 4.5, f"{mom:+.1f}%", fill=True, border=1)
            pdf.set_text_color(0, 0, 0)
            avg_vol = t.get("avg_daily_volume", 0)
            if avg_vol >= 1_000_000:
                vol_str = f"{avg_vol / 1_000_000:.1f}M"
            elif avg_vol >= 1_000:
                vol_str = f"{avg_vol / 1_000:.0f}K"
            else:
                vol_str = str(avg_vol)
            pdf.cell(col_widths[8], 4.5, vol_str, fill=True, border=1)
            score = t.get("composite_score", 0)
            pdf.cell(col_widths[9], 4.5, f"{score:.3f}", fill=True, border=1)
            pdf.ln()
        pdf.ln(3)

    # ── Section 5: Session Schedule ──
    pdf.section_title("FULL SESSION SCHEDULE")
    schedule = [
        ("Asian",    "01:00 - 08:00 UTC", "NZX, TSE, KRX, SGX, HKEX"),
        ("European", "08:00 - 16:30 UTC", "LSE, XETRA, Euronext, SIX + US overlap"),
        ("American", "14:30 - 21:00 UTC", "NYSE, NASDAQ, AMEX (full US session)"),
        ("US-Only",  "16:30 - 21:00 UTC", "NYSE, NASDAQ, AMEX (EU closed)"),
    ]
    pdf.set_font("Helvetica", "", 8)
    for name, hours, exchanges in schedule:
        is_current = name.lower().replace("-", "_") == session
        pdf.set_font("Helvetica", "B" if is_current else "", 8)
        prefix = ">> " if is_current else "   "
        pdf.set_text_color(*(BLUE if is_current else DARK_GREY))
        pdf.cell(25, 5, f"{prefix}{name}")
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(40, 5, hours)
        pdf.cell(0, 5, exchanges, ln=True)
    pdf.ln(3)

    # ── Section 6: Sector Distribution ──
    sector_counts = {}
    for t in session_vanguard:
        sector = t.get("sector", "Unknown")
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
    if sector_counts:
        pdf.section_title("SECTOR DISTRIBUTION (Vanguard)")
        pdf.set_font("Helvetica", "", 8)
        for sector, count in sorted(sector_counts.items(), key=lambda x: -x[1]):
            pct = count / len(session_vanguard) * 100
            pdf.cell(40, 5, sector)
            pdf.cell(15, 5, str(count))
            # Simple bar
            bar_width = pct * 0.8
            pdf.set_fill_color(*BLUE)
            pdf.cell(bar_width, 4, "", fill=True)
            pdf.set_fill_color(*WHITE)
            pdf.cell(2, 4, "")
            pdf.set_font("Helvetica", "", 7)
            pdf.cell(0, 5, f"{pct:.0f}%", ln=True)
            pdf.set_font("Helvetica", "", 8)
        pdf.ln(3)

    # ── Section 7: Risk Parameters ──
    pdf.section_title("RISK PARAMETERS (Active)")
    params = [
        ("Max Positions", "6"),
        ("Max Per-Ticker", "6% (Tier 1) / 4% (Tier 2) / 3% (Tier 3)"),
        ("Portfolio Heat Limit", "15%"),
        ("Daily Drawdown Halt", "2%"),
        ("Confidence Floor", "65/100"),
        ("Momentum Re-Entry", "60%+ WR = 2 positions, 70%+ WR = 3 positions"),
        ("Chandelier Exit", "5-rung trailing ladder (IOC gap-down)"),
        ("Kelly Ramp", "25% fractional, ramps to 100% over 250 trades"),
        ("ISA Annual Limit", "20,000 GBP"),
        ("Mode", "PAPER (10,000 GBP starting equity)"),
    ]
    for key, val in params:
        pdf.kv_row(key + ":", val)

    # ── Save ──
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    filename = f"session_{session}_{today}.pdf"
    output_path = REPORTS_DIR / filename
    pdf.output(str(output_path))
    log.info("Session PDF generated: %s (%d pages)", output_path, pdf.page_no())
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="AEGIS V2 Session Briefing PDF")
    parser.add_argument("--session", type=str, default="all",
                        choices=["asian", "european", "american", "us_only", "all"],
                        help="Which session to generate the briefing for")
    parser.add_argument("--send-telegram", action="store_true",
                        help="Also send the PDF via Telegram")
    args = parser.parse_args()

    path = generate_session_pdf(session=args.session)

    if path:
        print(f"PDF generated: {path}")
        if args.send_telegram:
            from python_brain.ouroboros.telegram_notify import send_document, send_session_start
            session_vanguard_count = 0
            watchlist = _load_watchlist()
            if watchlist:
                vanguard = watchlist.get("vanguard", [])
                if args.session != "all":
                    vanguard = _filter_session_tickers(vanguard, args.session)
                session_vanguard_count = len(vanguard)

            exchanges = sorted(SESSION_EXCHANGES.get(args.session, set()))
            send_session_start(args.session, session_vanguard_count, exchanges, str(path))
            print("Sent to Telegram")
    else:
        print("Failed to generate PDF", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
