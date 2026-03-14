"""
NZT-48 V8.0 -- Shared PDF Infrastructure
==========================================
Single source of truth for:
  - Lane assignment engine (TRADE / WATCH / INTEL / ABSTAIN)
  - Run manifest header
  - Exposure map calculator
  - Score decomposition renderer
  - PDF schedule constants
  - Re-exports from uk_isa.isa_universe

Every PDF generator imports from this module instead of defining
its own universe, lane logic, or scoring constants.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Canonical universe re-exports (single source of truth)
# ---------------------------------------------------------------------------
from uk_isa.isa_universe import (  # noqa: F401
    CORE_UNIVERSE,
    EXTENDED_UNIVERSE,
    ALL_UNIVERSE,
    INTEL_UNIVERSE,
    SECTOR_RADAR_UNIVERSE,
    FULL_SCAN_UNIVERSE,
    ISA_FACTOR_GROUPS,
    LEVERAGE_MAP,
    SLIPPAGE_MODEL,
    EXPECTED_PRICE_RANGES,
    SESSION_CONFIG,
    METRIC_DEFINITIONS,
    TICKER_NAMES,
    get_factor_group,
    get_net_return,
    is_short,
    get_leverage,
)


# ---------------------------------------------------------------------------
# Lane Assignment Engine
# ---------------------------------------------------------------------------

class Lane(str, Enum):
    """Mutually exclusive action lane for each ticker."""
    TRADE   = "TRADE"     # Actionable: meets ALL hard gates
    WATCH   = "WATCH"     # Almost qualifies: one soft-fail, or regime uncertain
    INTEL   = "INTEL"     # Information only: below thresholds
    ABSTAIN = "ABSTAIN"   # Hard reject: liquidity/data fail, NO-GO, regime kill


# Institutional-grade gate thresholds
LANE_GATES: dict[str, float] = {
    "rr_min_trade":       1.5,    # R:R minimum for TRADE lane
    "rr_min_watch":       1.0,    # R:R minimum for WATCH lane
    "conf_min_trade":     65.0,   # Confidence minimum for TRADE
    "conf_min_watch":     50.0,   # Confidence minimum for WATCH
    "regime_conf_min":    20.0,   # Regime confidence minimum (below = cap at WATCH)
    "rvol_min_trade":     0.8,    # RVOL minimum for TRADE
    "rvol_min_watch":     0.5,    # RVOL minimum for WATCH
    "atr_pct_min":        0.8,    # ATR% minimum for any action lane
    "data_bars_min":      30,     # Minimum historical bars for any lane except ABSTAIN
}

# Lane display colours (RGB tuples for PDF rendering)
LANE_COLORS: dict[Lane, tuple[int, int, int]] = {
    Lane.TRADE:   (20, 140, 60),    # Green
    Lane.WATCH:   (210, 160, 30),   # Amber
    Lane.INTEL:   (60, 100, 180),   # Blue
    Lane.ABSTAIN: (180, 30, 30),    # Red
}

LANE_BG_COLORS: dict[Lane, tuple[int, int, int]] = {
    Lane.TRADE:   (230, 248, 235),  # Light green
    Lane.WATCH:   (255, 248, 220),  # Light amber
    Lane.INTEL:   (230, 238, 250),  # Light blue
    Lane.ABSTAIN: (250, 230, 230),  # Light red
}


def assign_lane(
    ticker: str,
    rr: float,
    confidence: float,
    regime_confidence: float,
    rvol: float,
    atr_pct: float,
    data_bars: int,
    go_nogo: str = "GO",
    data_ok: bool = True,
) -> Lane:
    """
    Assign a ticker to exactly ONE lane.

    Logic is SEQUENTIAL -- strictest first, first match wins.
    This guarantees every ticker gets exactly one lane with no overlap.

    Parameters
    ----------
    ticker : str
        Yahoo Finance ticker symbol.
    rr : float
        Risk:Reward ratio (target / stop distance).
    confidence : float
        Composite confidence score 0-100.
    regime_confidence : float
        Regime detection confidence 0-100 (from DB or model).
    rvol : float
        Relative volume (current / 21d avg).
    atr_pct : float
        Average True Range as % of price.
    data_bars : int
        Number of historical bars available.
    go_nogo : str
        System-level GO/NO-GO status ("GO" or "NO-GO").
    data_ok : bool
        Whether data fetch succeeded for this ticker.

    Returns
    -------
    Lane
        Exactly one of TRADE, WATCH, INTEL, ABSTAIN.
    """
    # ── ABSTAIN: hard kills ────────────────────────────────────────
    if not data_ok or data_bars < LANE_GATES["data_bars_min"]:
        return Lane.ABSTAIN
    if go_nogo == "NO-GO":
        return Lane.ABSTAIN
    if rvol < LANE_GATES["rvol_min_watch"]:
        return Lane.ABSTAIN

    # ── INTEL: insufficient range ──────────────────────────────────
    if atr_pct < LANE_GATES["atr_pct_min"]:
        return Lane.INTEL

    # ── Regime confidence gate ─────────────────────────────────────
    # If regime is uncertain, cap the maximum lane at WATCH
    max_lane = Lane.TRADE
    if regime_confidence < LANE_GATES["regime_conf_min"]:
        max_lane = Lane.WATCH

    # ── TRADE: all hard gates pass ─────────────────────────────────
    if (max_lane == Lane.TRADE
            and rr >= LANE_GATES["rr_min_trade"]
            and confidence >= LANE_GATES["conf_min_trade"]
            and rvol >= LANE_GATES["rvol_min_trade"]):
        return Lane.TRADE

    # ── WATCH: softer gates ────────────────────────────────────────
    if (rr >= LANE_GATES["rr_min_watch"]
            and confidence >= LANE_GATES["conf_min_watch"]
            and rvol >= LANE_GATES["rvol_min_watch"]):
        return Lane.WATCH

    # ── Everything else ────────────────────────────────────────────
    return Lane.INTEL


# ---------------------------------------------------------------------------
# Run Manifest
# ---------------------------------------------------------------------------

@dataclass
class RunManifest:
    """Metadata header rendered on every PDF for audit trail."""
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    generated_utc: str = ""
    generated_uk: str = ""
    universe_name: str = "EXTENDED_UNIVERSE"
    universe_count: int = 0
    data_vendor: str = "yfinance"
    strategy_version: str = "V8.0"
    go_nogo: str = "N/A"
    data_health: str = "PASS"
    failed_tickers: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        now_utc = datetime.now(timezone.utc)
        from core.clock import UK_TZ
        now_uk = now_utc.astimezone(UK_TZ)
        if not self.generated_utc:
            self.generated_utc = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        if not self.generated_uk:
            self.generated_uk = now_uk.strftime("%d %b %Y %H:%M UK")
        if self.universe_count == 0:
            self.universe_count = len(EXTENDED_UNIVERSE)

    def header_line(self) -> str:
        """Single-line manifest for PDF header strip."""
        return (
            f"NZT-48 {self.strategy_version}  |  Run: {self.run_id}  |  "
            f"Universe: {self.universe_count} tickers ({self.universe_name})  |  "
            f"Data: {self.data_health}  |  GO/NO-GO: {self.go_nogo}  |  "
            f"{self.generated_uk}"
        )

    def short_line(self) -> str:
        """Abbreviated manifest for page footers."""
        return (
            f"Run {self.run_id}  |  {self.universe_count} tickers  |  "
            f"{self.data_health}  |  {self.go_nogo}  |  {self.generated_uk}"
        )


def render_manifest_strip(pdf: Any, manifest: RunManifest) -> None:
    """
    Render a dark-blue manifest header strip on the current page.
    Must be called after pdf.add_page() and before other content.
    """
    y = pdf.get_y()
    pdf.set_fill_color(15, 25, 55)
    pdf.rect(0, y, 210, 6, "F")
    pdf.set_xy(4, y + 0.5)
    pdf.set_font("Helvetica", "", 5.5)
    pdf.set_text_color(180, 190, 210)
    pdf.cell(0, 5, manifest.header_line(), align="L")
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 7)


# ---------------------------------------------------------------------------
# Exposure Map Calculator
# ---------------------------------------------------------------------------

def compute_exposure_map(
    picks: list[dict],
) -> dict[str, dict]:
    """
    Compute factor group exposure for a list of picks.

    Parameters
    ----------
    picks : list[dict]
        Each dict must have at minimum: {"ticker": str}
        Optional: {"direction": "LONG"|"SHORT", "confidence": float}

    Returns
    -------
    dict[str, dict]
        {group_name: {"count": int, "tickers": list[str],
                      "net_leverage": float, "pct_of_picks": float,
                      "concentrated": bool}}
    """
    if not picks:
        return {}

    group_picks: dict[str, list[str]] = {}
    for p in picks:
        ticker = p.get("ticker", "")
        group = get_factor_group(ticker)
        group_picks.setdefault(group, []).append(ticker)

    total = len(picks)
    result: dict[str, dict] = {}

    for group, tickers in sorted(group_picks.items(), key=lambda x: -len(x[1])):
        net_lev = sum(abs(get_leverage(t)) for t in tickers)
        pct = len(tickers) / total * 100 if total > 0 else 0
        result[group] = {
            "count": len(tickers),
            "tickers": tickers,
            "net_leverage": net_lev,
            "pct_of_picks": round(pct, 1),
            "concentrated": pct >= 50.0 or len(tickers) >= 3,
        }

    return result


def render_exposure_map(pdf: Any, exposure: dict[str, dict]) -> None:
    """
    Render a compact exposure map section in the PDF.
    Call after rendering TRADE lane picks.
    """
    if not exposure:
        return

    y = pdf.get_y() + 2
    pdf.set_fill_color(15, 25, 55)
    pdf.rect(0, y, 210, 5.5, "F")
    pdf.set_xy(4, y + 0.5)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.set_text_color(200, 180, 80)  # Gold
    pdf.cell(0, 4.5, "  FACTOR EXPOSURE -- TRADE LANE")
    pdf.set_text_color(0, 0, 0)
    y += 6

    has_concentration = False
    for group, data in exposure.items():
        if y > 270:
            break
        is_conc = data["concentrated"]
        if is_conc:
            has_concentration = True

        bg = (250, 230, 230) if is_conc else (245, 247, 252)
        pdf.set_fill_color(*bg)
        pdf.rect(4, y, 202, 5.5, "F")
        pdf.set_xy(6, y + 0.5)
        pdf.set_font("Helvetica", "B", 6)
        txt_col = (180, 30, 30) if is_conc else (40, 40, 60)
        pdf.set_text_color(*txt_col)
        ticker_str = ", ".join(data["tickers"][:5])
        conc_flag = "  CONCENTRATED" if is_conc else ""
        pdf.cell(0, 4.5,
                 f"  {group.upper()}: {data['count']} picks "
                 f"({ticker_str}) -- {data['net_leverage']:.0f}x leverage"
                 f"  ({data['pct_of_picks']:.0f}% of picks){conc_flag}")
        y += 5.5

    if has_concentration:
        pdf.set_fill_color(180, 30, 30)
        pdf.rect(4, y + 1, 202, 6, "F")
        pdf.set_xy(6, y + 2)
        pdf.set_font("Helvetica", "B", 6.5)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 4, "  CONCENTRATION WARNING: >50% of TRADE picks in single factor group. "
                 "Treat as ONE position for sizing.")
        y += 8

    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 1)


# ---------------------------------------------------------------------------
# Score Decomposition Renderer
# ---------------------------------------------------------------------------

def render_score_decomposition(
    pdf: Any,
    components: list[tuple[str, float, float]],
    x_start: float = 6.0,
    bar_width: float = 60.0,
    row_height: float = 4.5,
) -> None:
    """
    Render inline score decomposition bars.

    Parameters
    ----------
    pdf : FPDF
        Active PDF object.
    components : list[tuple[str, float, float]]
        List of (label, value, max_value) tuples.
        Example: [("ATR%", 35, 50), ("Momentum", 24, 30), ("RVOL", 13, 20)]
    """
    if not components:
        return

    y = pdf.get_y()
    total_val = sum(v for _, v, _ in components)
    total_max = sum(m for _, _, m in components)

    for label, value, max_val in components:
        if y > 275:
            break
        # Label
        pdf.set_xy(x_start, y)
        pdf.set_font("Helvetica", "", 5.5)
        pdf.set_text_color(100, 100, 120)
        pdf.cell(22, row_height, f"  {label}", align="L")

        # Bar background
        bar_x = x_start + 22
        frac = min(value / max_val, 1.0) if max_val > 0 else 0
        full_w = bar_width
        fill_w = full_w * frac

        # Background (grey)
        pdf.set_fill_color(220, 225, 235)
        pdf.rect(bar_x, y + 0.5, full_w, row_height - 1, "F")

        # Fill (green gradient based on fill %)
        if frac >= 0.7:
            fill_col = (20, 140, 60)
        elif frac >= 0.4:
            fill_col = (210, 160, 30)
        else:
            fill_col = (180, 60, 60)
        pdf.set_fill_color(*fill_col)
        if fill_w > 0:
            pdf.rect(bar_x, y + 0.5, fill_w, row_height - 1, "F")

        # Value text
        pdf.set_xy(bar_x + full_w + 2, y)
        pdf.set_font("Helvetica", "B", 5.5)
        pdf.set_text_color(40, 40, 60)
        pdf.cell(18, row_height, f"{value:.0f}/{max_val:.0f}", align="L")

        y += row_height

    # Total line
    pdf.set_xy(x_start, y)
    pdf.set_font("Helvetica", "B", 6)
    pdf.set_text_color(15, 25, 55)
    pdf.cell(22 + bar_width + 20, row_height,
             f"  TOTAL: {total_val:.0f}/{total_max:.0f}", align="L")
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + row_height + 1)


# ---------------------------------------------------------------------------
# PDF Schedule Constants
# ---------------------------------------------------------------------------

PDF_SCHEDULE: dict[str, dict[str, str]] = {
    "pdf_v2_momentum": {
        "name": "Momentum & Opportunity",
        "cron_uk": "07:00",
        "cron_utc": "07:00 (winter) / 06:00 (summer)",
    },
    "pdf_v2_risk_overnight": {
        "name": "Risk & Structural (Overnight)",
        "cron_uk": "06:30",
        "cron_utc": "06:30 (winter) / 05:30 (summer)",
    },
    "pdf_v2_risk_eod": {
        "name": "Risk & Structural (EOD)",
        "cron_uk": "22:00",
        "cron_utc": "22:00 (winter) / 21:00 (summer)",
    },
    "pdf_intelligence_pre_lse": {
        "name": "Pre-LSE Intelligence Brief",
        "cron_uk": "07:00",
        "cron_utc": "07:00 (winter) / 06:00 (summer)",
    },
    "pdf_intelligence_pre_nyse": {
        "name": "Pre-NYSE Intelligence Brief",
        "cron_uk": "13:30",
        "cron_utc": "13:30 (winter) / 12:30 (summer)",
    },
    "pdf_v2_daily_review": {
        "name": "Daily Review",
        "cron_uk": "22:00",
        "cron_utc": "22:00 (winter) / 21:00 (summer)",
    },
    "mega_report": {
        "name": "MEGA Report",
        "cron_uk": "22:30",
        "cron_utc": "22:30 (winter) / 21:30 (summer)",
    },
}


def next_schedule_line(report_key: str) -> str:
    """Return human-readable next run time string for PDF footer."""
    entry = PDF_SCHEDULE.get(report_key, {})
    if not entry:
        return "Schedule: see APScheduler config"
    return f"Next: {entry['cron_uk']} UK ({entry['cron_utc']})"


# ---------------------------------------------------------------------------
# Lane rendering helpers
# ---------------------------------------------------------------------------

def render_lane_header(
    pdf: Any,
    lane: Lane,
    count: int,
    page_width: float = 210.0,
) -> None:
    """Render a full-width lane header banner."""
    color = LANE_COLORS[lane]
    pdf.set_fill_color(*color)
    pdf.rect(0, pdf.get_y(), page_width, 6, "F")
    pdf.set_y(pdf.get_y() + 0.5)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(255, 255, 255)
    desc = {
        Lane.TRADE:   "Actionable -- meets ALL hard gates (R:R >= 1.5, Conf >= 65, RVOL >= 0.8)",
        Lane.WATCH:   "Monitor -- one soft gate fail, or regime confidence < 20%",
        Lane.INTEL:   "Information only -- below action thresholds",
        Lane.ABSTAIN: "No action -- data fail, liquidity fail, or system NO-GO",
    }
    pdf.cell(0, 5, f"  {lane.value} ({count} instruments) -- {desc[lane]}")
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(pdf.get_y() + 6)


# ---------------------------------------------------------------------------
# Winner Banner -- the #1 TRADE "Money Page" hero card
# ---------------------------------------------------------------------------

def render_winner_banner(
    pdf: Any,
    ticker: str,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    rr: float,
    composite: float,
    stars: int = 0,
    reason_bullets: Optional[list[str]] = None,
) -> None:
    """
    Render the big GREEN/AMBER/RED #1 trade banner.

    Colour rules:
      GREEN  -- composite >= 65  (high conviction, actionable)
      AMBER  -- 50 <= composite < 65  (moderate, proceed with caution)
      RED    -- composite < 50  (low conviction or NO-GO)
    """
    # Choose colour scheme
    if composite >= 65:
        bg = (15, 120, 50)      # dark green
        label = "#1 TRADE"
    elif composite >= 50:
        bg = (180, 130, 15)     # dark amber
        label = "BEST AVAILABLE -- MODERATE CONVICTION"
    else:
        bg = (160, 30, 30)      # dark red
        label = "NO HIGH-CONVICTION CANDIDATE"

    name = TICKER_NAMES.get(ticker, ticker)
    y = pdf.get_y()

    # ── Main banner box ────────────────────────────────────────────
    box_h = 28
    pdf.set_fill_color(*bg)
    pdf.rect(4, y, 202, box_h, "F")

    # Label
    pdf.set_xy(8, y + 1.5)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 6, label)

    # Ticker + name
    pdf.set_xy(8, y + 8)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 5, f"{ticker} -- {name}")

    # Action line
    dir_word = "BUY" if direction.upper() == "LONG" else "SELL"
    pdf.set_xy(8, y + 14)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5,
             f"{dir_word} @ {entry:.2f}  |  Stop {stop:.2f}  |  "
             f"Target {target:.2f}  |  R:R {rr:.1f}:1")

    # Score + stars
    star_str = "*" * stars + "_" * (5 - stars)
    pdf.set_xy(8, y + 20)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 5, f"Score {composite:.0f}/100  [{star_str}]")

    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + box_h + 2)


def render_no_trade_banner(pdf: Any, reason: str = "") -> None:
    """Render a RED banner when no candidate passes R:R >= 1.5."""
    y = pdf.get_y()
    pdf.set_fill_color(160, 30, 30)
    pdf.rect(4, y, 202, 16, "F")
    pdf.set_xy(8, y + 2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 6, "NO QUALIFYING TRADE TODAY")
    pdf.set_xy(8, y + 9)
    pdf.set_font("Helvetica", "", 7.5)
    msg = reason or "All tickers failed R:R >= 1.5 gate (ATR too wide for 2% target)"
    pdf.cell(0, 5, msg)
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 18)


# ---------------------------------------------------------------------------
# Execution Plan Renderer
# ---------------------------------------------------------------------------

def render_execution_plan(
    pdf: Any,
    entry: float,
    stop: float,
    target1: float,
    target2: float = 0.0,
    be_level: float = 0.0,
    partial_at: float = 0.0,
    order_type: str = "LIMIT",
    time_stop_min: int = 180,
    max_slippage_bps: float = 10.0,
) -> None:
    """Render a compact execution plan block."""
    y = pdf.get_y()
    pdf.set_fill_color(235, 238, 248)
    pdf.rect(4, y, 202, 20, "F")

    pdf.set_xy(8, y + 1)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(15, 25, 55)
    pdf.cell(0, 4, "EXECUTION PLAN")

    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(40, 40, 60)

    # Row 1
    entry_lo = entry * 0.999
    entry_hi = entry * 1.001
    pdf.set_xy(8, y + 5.5)
    pdf.cell(0, 4,
             f"Entry Zone: {entry_lo:.2f} - {entry_hi:.2f}  |  "
             f"Order: {order_type}  |  Max Slippage: {max_slippage_bps:.0f} bps")

    # Row 2
    stop_pct = abs(entry - stop) / entry * 100 if entry > 0 else 0
    pdf.set_xy(8, y + 10)
    pdf.cell(0, 4,
             f"Stop: {stop:.2f} (-{stop_pct:.1f}%)  |  "
             f"Break-even: {be_level:.2f}  |  "
             f"Partial: {partial_at:.2f}")

    # Row 3
    pdf.set_xy(8, y + 14.5)
    t2_str = f"  |  Runner: {target2:.2f}" if target2 > 0 else ""
    pdf.cell(0, 4,
             f"Target: {target1:.2f}{t2_str}  |  "
             f"Time Stop: {time_stop_min} min")

    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 22)


# ---------------------------------------------------------------------------
# Sector Inflow Alert Renderer
# ---------------------------------------------------------------------------

def render_sector_inflow_alerts(
    pdf: Any,
    sector_rankings: list[dict],
) -> None:
    """
    Render sector inflow/outflow alert banners.

    Each sector_ranking dict should have:
      sector, composite_score, rotation_signal, leadership_status,
      best_instrument, best_instrument_score, instruments
    """
    inflows = [s for s in sector_rankings
               if s.get("rotation_signal") == "INFLOW"]
    risings = [s for s in sector_rankings
               if s.get("leadership_status") == "RISING"
               and s.get("rotation_signal") != "INFLOW"]

    if not inflows and not risings:
        return

    y = pdf.get_y() + 1
    # Header
    pdf.set_fill_color(15, 25, 55)
    pdf.rect(4, y, 202, 5.5, "F")
    pdf.set_xy(8, y + 0.5)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(200, 180, 80)
    pdf.cell(0, 4.5, "SECTOR ROTATION ALERTS")
    y += 6

    for s in inflows:
        if y > 272:
            break
        pdf.set_fill_color(220, 248, 230)
        pdf.rect(4, y, 202, 5.5, "F")
        pdf.set_xy(8, y + 0.5)
        pdf.set_font("Helvetica", "B", 6.5)
        pdf.set_text_color(15, 100, 40)
        best = s.get("best_instrument", "")
        score = s.get("composite_score", 0)
        instruments = ", ".join(s.get("instruments", [])[:4])
        pdf.cell(0, 4.5,
                 f"INFLOW: {s['sector']} (score {score:.0f}) -- "
                 f"{instruments}")
        y += 5.5

    for s in risings[:3]:
        if y > 272:
            break
        pdf.set_fill_color(255, 248, 220)
        pdf.rect(4, y, 202, 5.5, "F")
        pdf.set_xy(8, y + 0.5)
        pdf.set_font("Helvetica", "B", 6.5)
        pdf.set_text_color(160, 120, 10)
        instruments = ", ".join(s.get("instruments", [])[:4])
        pdf.cell(0, 4.5,
                 f"RISING: {s['sector']} -- {instruments}")
        y += 5.5

    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 1)


# ---------------------------------------------------------------------------
# Near-Miss Table Renderer
# ---------------------------------------------------------------------------

def render_near_miss_table(
    pdf: Any,
    closest_misses: list[dict],
    max_rows: int = 8,
) -> None:
    """
    Render institutional-grade near-miss candidates table.

    Each miss dict should have:
      ticker, failed_gate, observed, required, delta, fallback_step_admits
    """
    if not closest_misses:
        return

    y = pdf.get_y() + 2
    # Header
    pdf.set_fill_color(15, 25, 55)
    pdf.rect(4, y, 202, 5.5, "F")
    pdf.set_xy(8, y + 0.5)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(200, 180, 80)
    pdf.cell(0, 4.5, "NEAR-MISS CANDIDATES -- Almost Qualified")
    y += 6.5

    # Column headers
    pdf.set_fill_color(235, 238, 248)
    pdf.rect(4, y, 202, 4.5, "F")
    pdf.set_font("Helvetica", "B", 5.5)
    pdf.set_text_color(40, 40, 60)
    cols = [
        (8, "Ticker"),
        (38, "Failed Gate"),
        (80, "Observed"),
        (105, "Required"),
        (130, "Delta"),
        (155, "Admits At"),
    ]
    for cx, label in cols:
        pdf.set_xy(cx, y + 0.5)
        pdf.cell(20, 3.5, label)
    y += 5

    # Rows
    for i, miss in enumerate(closest_misses[:max_rows]):
        if y > 272:
            break
        bg = (248, 250, 255) if i % 2 == 0 else (240, 242, 248)
        pdf.set_fill_color(*bg)
        pdf.rect(4, y, 202, 4.5, "F")

        pdf.set_font("Helvetica", "B", 5.5)
        pdf.set_text_color(40, 40, 60)
        pdf.set_xy(8, y + 0.5)
        pdf.cell(20, 3.5, str(miss.get("ticker", "")))

        pdf.set_font("Helvetica", "", 5.5)
        pdf.set_xy(38, y + 0.5)
        pdf.cell(30, 3.5, str(miss.get("failed_gate", "")))

        pdf.set_xy(80, y + 0.5)
        pdf.cell(20, 3.5, f"{miss.get('observed', 0):.2f}")

        pdf.set_xy(105, y + 0.5)
        pdf.cell(20, 3.5, f"{miss.get('required', 0):.2f}")

        # Delta -- green if small (close to admission)
        delta = miss.get("delta", 999)
        if delta < 0.1:
            pdf.set_text_color(15, 100, 40)
        elif delta < 0.3:
            pdf.set_text_color(160, 120, 10)
        else:
            pdf.set_text_color(160, 30, 30)
        pdf.set_font("Helvetica", "B", 5.5)
        pdf.set_xy(130, y + 0.5)
        pdf.cell(20, 3.5, f"{delta:.3f}")

        pdf.set_text_color(40, 40, 60)
        pdf.set_font("Helvetica", "", 5.5)
        step = miss.get("fallback_step_admits", -1)
        step_str = f"Step {step}" if step > 0 else "Never"
        pdf.set_xy(155, y + 0.5)
        pdf.cell(20, 3.5, step_str)

        y += 4.5

    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 1)


# ---------------------------------------------------------------------------
# Sector Rotation Radar Table
# ---------------------------------------------------------------------------

def render_sector_rotation_table(
    pdf: Any,
    sector_rankings: list[dict],
) -> None:
    """
    Render the full sector rotation radar table.

    Each ranking dict should have:
      sector, composite_score, rotation_signal, leadership_status,
      momentum_score, capital_inflow_score, best_instrument, instruments
    """
    if not sector_rankings:
        # Show "no data" message
        pdf.set_font("Helvetica", "I", 7)
        pdf.set_text_color(120, 120, 140)
        pdf.cell(0, 6, "  Sector rotation data unavailable this session.")
        pdf.ln(7)
        pdf.set_text_color(0, 0, 0)
        return

    y = pdf.get_y()
    # Header
    pdf.set_fill_color(15, 25, 55)
    pdf.rect(4, y, 202, 5.5, "F")
    pdf.set_xy(8, y + 0.5)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(200, 180, 80)
    n_sectors = len(sector_rankings)
    n_inflows = sum(1 for s in sector_rankings if s.get("rotation_signal") == "INFLOW")
    pdf.cell(0, 4.5,
             f"SECTOR ROTATION RADAR -- {n_sectors} SECTORS TRACKED"
             f"  ({n_inflows} with INFLOW)")
    y += 6.5

    # Column headers
    pdf.set_fill_color(235, 238, 248)
    pdf.rect(4, y, 202, 4.5, "F")
    pdf.set_font("Helvetica", "B", 5.5)
    pdf.set_text_color(40, 40, 60)
    hdr_cols = [
        (8, "Sector"), (45, "Score"), (65, "Signal"),
        (90, "Leader"), (118, "Best Instrument"), (165, "Instruments"),
    ]
    for cx, label in hdr_cols:
        pdf.set_xy(cx, y + 0.5)
        pdf.cell(25, 3.5, label)
    y += 5

    # Sort by composite score descending
    sorted_sectors = sorted(sector_rankings,
                            key=lambda s: s.get("composite_score", 0),
                            reverse=True)

    for i, s in enumerate(sorted_sectors):
        if y > 270:
            break
        sig = s.get("rotation_signal", "NEUTRAL")
        # Row background colour based on signal
        if sig == "INFLOW":
            bg = (220, 248, 230)
        elif sig == "OUTFLOW":
            bg = (250, 230, 230)
        else:
            bg = (248, 250, 255) if i % 2 == 0 else (240, 242, 248)
        pdf.set_fill_color(*bg)
        pdf.rect(4, y, 202, 4.5, "F")

        pdf.set_font("Helvetica", "B", 5.5)
        if sig == "INFLOW":
            pdf.set_text_color(15, 100, 40)
        elif sig == "OUTFLOW":
            pdf.set_text_color(160, 30, 30)
        else:
            pdf.set_text_color(40, 40, 60)

        pdf.set_xy(8, y + 0.5)
        pdf.cell(35, 3.5, str(s.get("sector", "")))

        pdf.set_font("Helvetica", "B", 6)
        pdf.set_xy(45, y + 0.5)
        pdf.cell(15, 3.5, f"{s.get('composite_score', 0):.0f}")

        pdf.set_font("Helvetica", "B", 5.5)
        pdf.set_xy(65, y + 0.5)
        pdf.cell(20, 3.5, sig)

        pdf.set_font("Helvetica", "", 5.5)
        pdf.set_text_color(40, 40, 60)
        pdf.set_xy(90, y + 0.5)
        pdf.cell(25, 3.5, str(s.get("leadership_status", "")))

        pdf.set_xy(118, y + 0.5)
        best = s.get("best_instrument", "")
        best_name = TICKER_NAMES.get(best, best)
        pdf.cell(45, 3.5, f"{best} ({best_name[:15]})")

        pdf.set_xy(165, y + 0.5)
        pdf.set_font("Helvetica", "", 5)
        instruments = s.get("instruments", [])
        pdf.cell(40, 3.5, ", ".join(instruments[:4]))

        y += 4.5

    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 1)


# ---------------------------------------------------------------------------
# Lane-grouped ticker table renderer
# ---------------------------------------------------------------------------

def render_lane_grouped_table(
    pdf: Any,
    lane_groups: dict,
    render_row_fn: Any = None,
) -> None:
    """
    Render tickers grouped by lane (TRADE/WATCH/INTEL/ABSTAIN).

    Parameters
    ----------
    pdf : FPDF
    lane_groups : dict
        {Lane.TRADE: [(ticker, data_dict), ...], Lane.WATCH: [...], ...}
    render_row_fn : callable, optional
        Function(pdf, ticker, data, lane) -> None that renders one row.
        If None, uses a default compact renderer.
    """
    lane_order = [Lane.TRADE, Lane.WATCH, Lane.INTEL, Lane.ABSTAIN]

    for lane in lane_order:
        items = lane_groups.get(lane, [])
        if not items:
            continue

        # Lane header
        render_lane_header(pdf, lane, len(items))

        # Render rows
        for ticker, data in items:
            if pdf.get_y() > 268:
                pdf.add_page()
            if render_row_fn:
                render_row_fn(pdf, ticker, data, lane)
            else:
                _default_lane_row(pdf, ticker, data, lane)


def _default_lane_row(pdf: Any, ticker: str, data: dict, lane: Lane) -> None:
    """Default compact row for lane-grouped table."""
    y = pdf.get_y()
    bg = LANE_BG_COLORS[lane]
    pdf.set_fill_color(*bg)
    pdf.rect(4, y, 202, 5, "F")

    pdf.set_xy(8, y + 0.5)
    pdf.set_font("Helvetica", "B", 6)
    pdf.set_text_color(40, 40, 60)
    name = TICKER_NAMES.get(ticker, ticker)
    pdf.cell(35, 4, f"{ticker}")

    pdf.set_font("Helvetica", "", 5.5)
    pdf.set_xy(43, y + 0.5)
    pdf.cell(30, 4, name[:20])

    if data and lane in (Lane.TRADE, Lane.WATCH):
        # Show entry/stop/target for actionable lanes
        entry = data.get("entry", data.get("price", 0))
        stop = data.get("stop", 0)
        target = data.get("target", data.get("target1", 0))
        rr = data.get("rr", 0)
        score = data.get("composite", data.get("score", 0))

        pdf.set_xy(75, y + 0.5)
        action = "BUY" if not is_short(ticker) else "SELL"
        if lane == Lane.WATCH:
            action = "MONITOR"
        pdf.cell(15, 4, action)

        pdf.set_xy(92, y + 0.5)
        pdf.cell(20, 4, f"{entry:.2f}")
        pdf.set_xy(112, y + 0.5)
        pdf.cell(20, 4, f"S:{stop:.2f}" if stop else "")
        pdf.set_xy(132, y + 0.5)
        pdf.cell(20, 4, f"T:{target:.2f}" if target else "")

        # R:R colour
        if rr >= 1.5:
            pdf.set_text_color(15, 100, 40)
        elif rr >= 1.0:
            pdf.set_text_color(160, 120, 10)
        else:
            pdf.set_text_color(160, 30, 30)
        pdf.set_font("Helvetica", "B", 6)
        pdf.set_xy(152, y + 0.5)
        pdf.cell(15, 4, f"R:R {rr:.1f}")

        pdf.set_text_color(40, 40, 60)
        pdf.set_font("Helvetica", "", 5.5)
        pdf.set_xy(170, y + 0.5)
        pdf.cell(20, 4, f"{score:.0f}/100")

    elif data and lane == Lane.INTEL:
        # Minimal info for INTEL
        price = data.get("price", data.get("entry", 0))
        rsi = data.get("rsi", 0)
        rvol = data.get("rvol", 0)
        regime = data.get("regime", "")
        pdf.set_xy(75, y + 0.5)
        pdf.cell(15, 4, "INFO")
        pdf.set_xy(92, y + 0.5)
        pdf.cell(20, 4, f"{price:.2f}")
        pdf.set_xy(115, y + 0.5)
        pdf.cell(20, 4, f"RSI:{rsi:.0f}")
        pdf.set_xy(135, y + 0.5)
        pdf.cell(20, 4, f"RVOL:{rvol:.2f}")
        pdf.set_xy(160, y + 0.5)
        pdf.cell(30, 4, str(regime)[:12])

    elif data and lane == Lane.ABSTAIN:
        reason = data.get("abstain_reason", data.get("reason", "Data insufficient"))
        pdf.set_text_color(140, 40, 40)
        pdf.set_xy(75, y + 0.5)
        pdf.cell(120, 4, str(reason)[:60])

    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 5)
