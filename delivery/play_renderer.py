"""
delivery/play_renderer.py
==========================
Shared FPDF2 rendering helpers for Top Plays table used in all 3 PDFs.

Produces a consistent "Ranked Plays" table:
  Rank | Ticker | Dir | Stars | Score | Label | Entry | Stop | T1 | T2 | R:R | ATR% | RVOL | Setup | Reasons

All text is ASCII-safe (fpdf2 latin-1 requirement).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fpdf import FPDF

from signal_engine.scoring import PlayScore
from signal_engine.engine import SignalEngine, EngineResult

# ---------------------------------------------------------------------------
# Colour palette (shared)
# ---------------------------------------------------------------------------
C_DARK_BLUE  = (26,  39,  68)
C_MID_BLUE   = (35,  55, 100)
C_GOLD       = (201, 168,  76)
C_WHITE      = (255, 255, 255)
C_GREEN      = (0,  160,  80)
C_RED        = (200,  30,  30)
C_AMBER      = (220, 150,   0)
C_GREY       = (130, 130, 130)
C_LIGHT_BLUE = (200, 215, 245)
C_NEAR_WHITE = (245, 247, 252)

# Stars as ASCII
_STAR_MAP = {
    5: "[*****]",
    4: "[****_]",
    3: "[***__]",
    2: "[**___]",
    1: "[*____]",
}


def run_engine_for_pdf(session: str, regime: str = "NEUTRAL") -> EngineResult:
    """Run the signal engine and return the result. Used by all 3 PDF generators."""
    engine = SignalEngine(use_extended=True)
    return engine.run(
        session=session,
        regime=regime,
        n_plays_min=5,
        n_plays_max=20,
    )


def render_plays_table(
    pdf: "FPDF",
    plays: list[PlayScore],
    title: str = "RANKED PLAYS",
    session_label: str = "",
    max_plays: int = 15,
    y_start: float = None,
) -> float:
    """
    Render a full ranked plays table on the current page.
    Returns the Y position after the table.
    """
    if y_start is not None:
        pdf.set_y(y_start)

    # Ensure we have plays
    if not plays:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*C_AMBER)
        pdf.cell(0, 8, "NO PLAYS AVAILABLE — see Signal Drought panel for blockers", ln=True)
        return pdf.get_y()

    plays = plays[:max_plays]

    # Section header
    pdf.set_fill_color(*C_MID_BLUE)
    pdf.set_text_color(*C_GOLD)
    pdf.set_font("Helvetica", "B", 9)
    y = pdf.get_y()
    pdf.rect(4, y, 202, 7, "F")
    pdf.set_xy(6, y + 1)
    label_right = f"  {session_label}" if session_label else ""
    pdf.cell(0, 5, f"{title}{label_right} ({len(plays)} plays)", ln=True)

    # Column widths — fit in 202mm
    # Rank, Ticker, Dir, Stars, Score, Label, Entry, Stop, T1, R:R, ATR%, RVOL, Setup
    cols = [
        ("# ",    8),
        ("TICKER",18),
        ("DIR",    9),
        ("STARS", 18),
        ("SCORE", 12),
        ("LABEL", 28),
        ("ENTRY", 18),
        ("STOP",  18),
        ("T1",    18),
        ("R:R",    9),
        ("ATR%",  10),
        ("RVOL",  10),
        ("SETUP", 19),
    ]

    # Header row
    y = pdf.get_y() + 1
    pdf.set_fill_color(*C_DARK_BLUE)
    pdf.set_text_color(*C_WHITE)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.rect(4, y, 202, 5.5, "F")
    x = 4
    for hdr, w in cols:
        pdf.set_xy(x, y + 0.5)
        pdf.cell(w, 4.5, hdr, align="C")
        x += w

    # Data rows
    y += 5.5
    for i, ps in enumerate(plays):
        row_h = 5.5
        bg    = C_NEAR_WHITE if i % 2 == 0 else C_LIGHT_BLUE

        pdf.set_fill_color(*bg)
        pdf.rect(4, y, 202, row_h, "F")
        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_text_color(*C_DARK_BLUE)

        # Label colour
        if "WATCH" in ps.label or "LOWER" in ps.label:
            lbl_color = C_AMBER
        elif ps.fallback_step > 0:
            lbl_color = C_AMBER
        else:
            lbl_color = C_GREEN

        # Direction colour
        dir_color = C_GREEN if ps.direction == "LONG" else C_RED

        rvol_str  = f"{ps.rvol:.1f}x" if ps.rvol else "N/A"
        stars_str = _STAR_MAP.get(ps.stars, "[*____]")

        cells = [
            (f"{i+1}",                   8,  "C", None),
            (ps.ticker,                  18, "L", None),
            (ps.direction,                9, "C", dir_color),
            (stars_str,                  18, "C", C_GOLD),
            (f"{ps.composite:.0f}",      12, "C", None),
            (_trunc(ps.label, 18),       28, "L", lbl_color),
            (f"{ps.entry:.4f}",          18, "R", None),
            (f"{ps.stop:.4f}",           18, "R", C_RED),
            (f"{ps.target1:.4f}",        18, "R", C_GREEN),
            (f"{ps.rr_ratio:.2f}",        9, "C", None),
            (f"{ps.atr_pct:.2f}%",       10, "C", None),
            (rvol_str,                   10, "C", None),
            (_trunc(ps.setup_type, 14),  19, "L", None),
        ]

        x = 4
        for val, w, align, color in cells:
            pdf.set_xy(x, y + 0.8)
            if color:
                pdf.set_text_color(*color)
            else:
                pdf.set_text_color(*C_DARK_BLUE)
            pdf.cell(w, 3.5, str(val), align=align)
            x += w

        y += row_h

        # Reasons row (grey, smaller)
        if ps.reasons and i < 10:
            reason_text = "  " + " | ".join(ps.reasons[:3])
            reason_text = _ascii_safe(reason_text)[:110]
            pdf.set_fill_color(*bg)
            pdf.rect(4, y, 202, 4, "F")
            pdf.set_xy(8, y + 0.5)
            pdf.set_font("Helvetica", "I", 5.5)
            pdf.set_text_color(*C_GREY)
            pdf.cell(194, 3, reason_text)
            y += 4

        # Page break guard
        if y > 275:
            pdf.add_page()
            y = 15

    pdf.set_y(y + 2)
    return y + 2


def render_drought_panel(pdf: "FPDF", drought_text: str) -> None:
    """Render a SIGNAL DROUGHT warning box."""
    y = pdf.get_y()
    pdf.set_fill_color(180, 20, 20)
    pdf.rect(4, y, 202, 8, "F")
    pdf.set_xy(6, y + 1.5)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*C_WHITE)
    pdf.cell(0, 5, "!!! SIGNAL DROUGHT — NO PLAYS AVAILABLE !!!")
    y += 10
    pdf.set_y(y)
    lines = drought_text.split("\n")[1:]  # skip the === header
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*C_RED)
    for line in lines[:8]:
        pdf.cell(0, 4, _ascii_safe(line)[:100], ln=True)


def render_gate_funnel_panel(pdf: "FPDF", funnel: dict, blockers: list[str]) -> None:
    """Render the gate funnel diagnostic panel."""
    y = pdf.get_y()
    pdf.set_fill_color(*C_DARK_BLUE)
    pdf.rect(4, y, 202, 6, "F")
    pdf.set_xy(6, y + 1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*C_GOLD)
    pdf.cell(0, 4, "SIGNAL ENGINE GATE FUNNEL")
    y += 7

    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*C_DARK_BLUE)

    items = [
        ("Tracked",          funnel.get("tracked", 0)),
        ("Data Valid",       funnel.get("data_valid", 0)),
        ("Strict Signals",   funnel.get("signals_strict", 0)),
        ("Fallback Signals", funnel.get("signals_fallback", 0)),
        ("Total Signals",    funnel.get("total_signals", 0)),
    ]

    x = 6
    for label, val in items:
        color = C_GREEN if val > 0 else C_RED
        pdf.set_xy(x, y)
        pdf.set_text_color(*C_GREY)
        pdf.cell(28, 4, label + ":", align="R")
        pdf.set_text_color(*color)
        pdf.cell(12, 4, str(val))
        x += 42
        if x > 190:
            x = 6
            y += 5

    y += 6
    if blockers:
        pdf.set_y(y)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*C_RED)
        pdf.cell(0, 4, "Top blockers:", ln=True)
        pdf.set_font("Helvetica", "", 6.5)
        for b in blockers[:4]:
            pdf.cell(0, 3.5, "  - " + _ascii_safe(b)[:100], ln=True)

    pdf.set_y(pdf.get_y() + 3)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _trunc(s: str, n: int) -> str:
    s = _ascii_safe(s)
    return s[:n - 1] + "." if len(s) > n else s


def _ascii_safe(s: str) -> str:
    """Replace non-latin-1 chars so fpdf2 doesn't crash."""
    _MAP = {
        "\u2192": "->", "\u2190": "<-", "\u25b2": "^", "\u25bc": "v",
        "\u2014": "--", "\u2013": "-",  "\u00d7": "x", "\u2265": ">=",
        "\u2264": "<=", "\u2713": "[OK]", "\u2717": "[X]",
        "\u2500": "-",  "\u2502": "|",  "\u2022": "*",
        "\u2019": "'",  "\u201c": '"',  "\u201d": '"',
        "\u00b0": "deg","\u00b1": "+/-",
    }
    result = []
    for ch in s:
        if ch in _MAP:
            result.append(_MAP[ch])
        elif ord(ch) <= 255:
            result.append(ch)
        else:
            result.append("?")
    return "".join(result)
