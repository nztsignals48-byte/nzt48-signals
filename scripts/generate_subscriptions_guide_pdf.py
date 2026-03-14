"""
NZT-48 Data Infrastructure — Subscription & Integration Guide
Auto-opens after smoke test passes.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, PageBreak,
    Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from datetime import datetime, timezone
import os
import shutil

GOLD  = HexColor("#F5C518")
DARK  = HexColor("#0A0A0A")
D2    = HexColor("#161616")
D3    = HexColor("#222222")
CYAN  = HexColor("#00D4FF")
GREEN = HexColor("#00C851")
AMBER = HexColor("#FFBB33")
RED   = HexColor("#FF4444")
GREY  = HexColor("#777777")
LIGHT = HexColor("#DEDEDE")
WHITE = white

W, H = A4
LMAR, RMAR = 18*mm, 18*mm
CONTENT_W = W - LMAR - RMAR

OUT = os.path.join(os.path.dirname(__file__), "..", "NZT48_Data_Subscriptions_Guide.pdf")

_sc = [0]
def S(**kw):
    _sc[0] += 1
    return ParagraphStyle(f"sub{_sc[0]}", **kw)

H1   = S(fontName="Helvetica-Bold", fontSize=16, textColor=GOLD, spaceBefore=10, spaceAfter=5, leading=20)
H2   = S(fontName="Helvetica-Bold", fontSize=12, textColor=CYAN, spaceBefore=8, spaceAfter=4, leading=16)
H3   = S(fontName="Helvetica-Bold", fontSize=10, textColor=LIGHT, spaceBefore=6, spaceAfter=3, leading=14)
BODY = S(fontName="Helvetica", fontSize=8.5, textColor=LIGHT, spaceBefore=2, spaceAfter=3, leading=13)
SMALL = S(fontName="Helvetica", fontSize=7.5, textColor=GREY, spaceBefore=1, spaceAfter=1, leading=11)
CITE  = S(fontName="Helvetica-Oblique", fontSize=7.5, textColor=GREY, spaceBefore=1, spaceAfter=2, leading=11)

SP4 = Spacer(1, 4)
SP8 = Spacer(1, 8)
SP14 = Spacer(1, 14)

def b(t): return f"<b>{t}</b>"
def it(t): return f"<i>{t}</i>"
def fc(t, c): return f'<font color="{c}">{t}</font>'
def gold(t): return fc(b(t), "#F5C518")
def cyan(t): return fc(t, "#00D4FF")
def grn(t):  return fc(b(t), "#00C851")
def amb(t):  return fc(b(t), "#FFBB33")

def P(t, sty=None):
    return Paragraph(t, sty or BODY)

def dark_bg(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(DARK)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(GREY)
    canvas.drawRightString(W-RMAR, 5.5, f"Page {doc.page}  ·  NZT-48 Subscriptions Guide  ·  {datetime.now(timezone.utc).strftime('%d %b %Y')}")
    canvas.restoreState()

def TS(hcol=GOLD, fs=7.5, pad=5):
    return TableStyle([
        ("BACKGROUND",  (0,0), (-1,0), hcol),
        ("TEXTCOLOR",   (0,0), (-1,0), DARK),
        ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), fs),
        ("TEXTCOLOR",   (0,1), (-1,-1), LIGHT),
        ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
        ("BACKGROUND",  (0,1), (-1,-1), D2),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [D2, D3]),
        ("GRID",        (0,0), (-1,-1), 0.3, HexColor("#333333")),
        ("TOPPADDING",  (0,0), (-1,-1), pad),
        ("BOTTOMPADDING",(0,0),(-1,-1), pad),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING",(0,0), (-1,-1), 8),
        ("VALIGN",      (0,0), (-1,-1), "TOP"),
    ])


def build():
    doc = BaseDocTemplate(
        OUT, pagesize=A4,
        leftMargin=LMAR, rightMargin=RMAR,
        topMargin=16*mm, bottomMargin=14*mm,
        title="NZT-48 Data Subscriptions Guide",
    )
    frame = Frame(LMAR, 14*mm, CONTENT_W, H - 30*mm, id="main")
    doc.addPageTemplates([PageTemplate(id="dark", frames=[frame], onPage=dark_bg)])

    story = []

    # ── Cover ─────────────────────────────────────────────────────────────────
    story.append(SP14)
    story.append(Paragraph(
        "NZT-48",
        S(fontName="Helvetica-Bold", fontSize=38, textColor=GOLD, alignment=TA_CENTER, leading=42)
    ))
    story.append(Paragraph(
        "DATA INFRASTRUCTURE",
        S(fontName="Helvetica-Bold", fontSize=22, textColor=CYAN, alignment=TA_CENTER, leading=26)
    ))
    story.append(Paragraph(
        "Subscription & Integration Guide",
        S(fontName="Helvetica", fontSize=13, textColor=LIGHT, alignment=TA_CENTER, leading=17)
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"v1.0  ·  {datetime.now(timezone.utc).strftime('%B %Y')}  ·  Ordered by ROI Priority",
        S(fontName="Helvetica", fontSize=8.5, textColor=GREY, alignment=TA_CENTER)
    ))
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=2, color=GOLD, spaceAfter=8))
    story.append(Paragraph(
        "Sign up for data services in priority order. Tier 1 is free and available now. "
        "Tier 2 when daily P&amp;L consistently hits the £200 target. Tier 3 at £100K+ portfolio.",
        S(fontName="Helvetica", fontSize=9, textColor=GREY, alignment=TA_CENTER, leading=14)
    ))
    story.append(PageBreak())

    # ── Section 1: Current Data Sources (no sign-up needed) ───────────────────
    story.append(Paragraph("Section 1 — Current Data Sources (Already Integrated)", H1))
    story.append(Paragraph(
        "These sources are already wired into NZT-48 and require no subscription. "
        "They form the backbone of the system at paper-trading scale.",
        BODY
    ))
    story.append(SP8)

    current_sources = [
        ["Source", "Module(s)", "Data Provided", "Limitations"],
        ["yfinance (Yahoo Finance)",
         "All price/indicator modules",
         "OHLCV, fundamentals, options data, analyst info",
         "15-min delayed; rate limits on heavy use; LSE data sometimes stale"],
        ["FRED API (via yfinance proxy)",
         "liquidity_monitor.py",
         "VIX, treasury yields, economic indicators",
         "Daily updates only; SOFR not always current"],
        ["FINRA Short Interest",
         "short_squeeze_monitor.py",
         "Short interest % per ticker (T+1 delay)",
         "Reported twice monthly only; not real-time"],
        ["yfinance .calendar",
         "earnings_calendar.py",
         "Earnings announcement dates for underlying",
         "Date only, no EPS estimates"],
        ["yfinance .options",
         "iv_crush.py",
         "Implied volatility surface approximation",
         "Sparse for some LSE ETPs"],
    ]
    t = Table(
        [[P(c) for c in row] for row in current_sources],
        colWidths=[42*mm, 38*mm, 58*mm, 38*mm]
    )
    t.setStyle(TS(GOLD, fs=7))
    story.append(t)
    story.append(PageBreak())

    # ── Section 2: Tier 1 — Sign Up Now (Free) ─────────────────────────────────
    story.append(Paragraph("Section 2 — Tier 1: Sign Up Now (Free / Near-Free)", H1))
    story.append(Paragraph(
        "These services are free or have generous free tiers. "
        "Sign up immediately — they improve accuracy from day one.",
        BODY
    ))
    story.append(SP8)

    tier1_rows = [
        ["Service", "Cost", "What You Get", "Module", "Sign Up"],
        ["FRED API\n(Federal Reserve)",
         grn("FREE"),
         "SOFR rates, 10Y treasury, credit spreads, economic indicators. "
         "Direct API access (no yfinance proxy lag).",
         "liquidity_monitor.py\ncross_asset_macro.py",
         "fred.stlouisfed.org\n/docs/api/fred/"],
        ["SEC EDGAR API\n(Full-text search)",
         grn("FREE"),
         "Director buying signals (Form 4), accruals data (10-Q cash flows), "
         "earnings quality confirmation. Structured JSON output.",
         "accruals_quality_veto.py",
         "efts.sec.gov\n/LATEST/search-index"],
        ["Alternative.me\nFear & Greed API",
         grn("FREE\n1000/month"),
         "Crypto Fear &amp; Greed Index as macro sentiment proxy. "
         "Extreme fear (index &lt;25) = risk-off signal for all longs.",
         "cross_asset_macro.py",
         "alternative.me/crypto\n/fear-and-greed-index/api/"],
        ["NewsAPI.org\n(Free tier)",
         grn("FREE\n100/day"),
         "Headlines for earnings NLP sentiment. "
         "Feeds earnings_sentiment module for pre-announcement bias scoring.",
         "earnings_sentiment (module)",
         "newsapi.org/register"],
    ]
    t1 = Table(
        [[P(c) for c in row] for row in tier1_rows],
        colWidths=[32*mm, 20*mm, 58*mm, 32*mm, 36*mm]
    )
    t1.setStyle(TS(GREEN, fs=7))
    story.append(t1)

    story.append(SP14)
    story.append(Paragraph("How to Wire Tier 1 Keys", H2))
    story.append(Paragraph(
        "Add these to your environment (or <b>config/settings.yaml</b> under <b>api_keys:</b>):",
        BODY
    ))
    story.append(SP4)
    env_template = [
        ["Environment Variable", "Service", "Where to Find It"],
        ["FRED_API_KEY", "FRED API", "fred.stlouisfed.org → My Account → API Keys"],
        ["SEC_EDGAR_EMAIL", "SEC EDGAR", "Required as User-Agent header (your email)"],
        ["NEWSAPI_KEY", "NewsAPI.org", "newsapi.org → Account → API Key"],
        ["FEAR_GREED_KEY", "Alternative.me", "Not required for free tier — no key needed"],
    ]
    t_env = Table(
        [[P(c) for c in row] for row in env_template],
        colWidths=[52*mm, 40*mm, 86*mm]
    )
    t_env.setStyle(TS(CYAN, fs=7.5))
    story.append(t_env)
    story.append(PageBreak())

    # ── Section 3: Tier 2 — When £200/day target is consistently hit ───────────
    story.append(Paragraph("Section 3 — Tier 2: Upgrade When Daily Target Consistently Hit", H1))
    story.append(Paragraph(
        "Subscribe when paper trading consistently generates £200+/day (the 2% compounding target). "
        "These services provide real-time data and significantly improve signal quality.",
        BODY
    ))
    story.append(SP8)

    tier2_rows = [
        ["Service", "Cost", "ROI Case", "Module", "Sign Up"],
        ["Polygon.io\nStocks Starter",
         amb("$29/month"),
         "15-min delayed real-time prices (vs 15-min Yahoo delay). "
         "Faster gap detection, more accurate RVOL. "
         "Break-even: 1 extra £30 trade/month.",
         "realtime_feed.py",
         "polygon.io\n/dashboard/signup"],
        ["Polygon.io\nStocks Unlimited",
         amb("$199/month"),
         "True tick-by-tick data + options flow + order book. "
         "Enables genuine Order Flow Imbalance (OFI) calculation. "
         "Break-even: 1 extra £200 trade/month.",
         "realtime_feed.py\norder_flow_imbalance.py",
         "polygon.io\n/dashboard/signup"],
        ["Quandl / Nasdaq\nData Link",
         amb("$50/month"),
         "Clean fundamentals: quarterly cash flows, accruals data. "
         "More reliable than yfinance .financials for accruals quality veto.",
         "accruals_quality_veto.py\nanalyst_revision_tracker.py",
         "data.nasdaq.com\n/sign-up"],
        ["NewsAPI.org\nProduction",
         amb("$449/month"),
         "Full archive access, 24h coverage, faster delivery. "
         "Only needed if earnings sentiment NLP generates consistent edge.",
         "earnings_sentiment (module)",
         "newsapi.org\n/pricing"],
    ]
    t2 = Table(
        [[P(c) for c in row] for row in tier2_rows],
        colWidths=[32*mm, 22*mm, 60*mm, 30*mm, 34*mm]
    )
    t2.setStyle(TS(AMBER, fs=7))
    story.append(t2)
    story.append(PageBreak())

    # ── Section 4: Tier 3 — Institutional Scale ─────────────────────────────────
    story.append(Paragraph("Section 4 — Tier 3: Institutional Scale (£100K+ Portfolio)", H1))
    story.append(Paragraph(
        "These subscriptions only make economic sense at large portfolio size. "
        "The cost is justified when position sizes are large enough that data quality directly affects P&amp;L.",
        BODY
    ))
    story.append(SP8)

    tier3_rows = [
        ["Service", "Cost", "What Replaces", "ROI Trigger"],
        ["Ortex (Short Interest)",
         "$250/month",
         "FINRA T+1 data in short_squeeze_monitor.py",
         "Short squeeze signal hit rate improves when short interest is real-time vs 2-week delay"],
        ["S3 Partners",
         "$500/month",
         "Ortex (more accurate borrow rates)",
         "Most accurate short interest + borrow cost data in the industry"],
        ["Refinitiv Eikon",
         "$1,500/month",
         "All yfinance data + NewsAPI",
         "Single source of truth: real-time everything + 20Y fundamentals history"],
        ["Bloomberg Terminal",
         "$2,000/month",
         "All current data sources",
         "Gold standard: tick data, news, analytics, execution"],
    ]
    t3 = Table(
        [[P(c) for c in row] for row in tier3_rows],
        colWidths=[40*mm, 28*mm, 56*mm, 54*mm]
    )
    t3.setStyle(TS(RED, fs=7.5))
    story.append(t3)
    story.append(SP14)

    # ── Section 5: Monthly Cost Projections ──────────────────────────────────────
    story.append(Paragraph("Section 5 — Infrastructure Cost by Portfolio Scale", H1))
    story.append(SP8)

    cost_rows = [
        ["Portfolio Size", "Monthly Data Cost", "Data Sources", "Notes"],
        ["£100,000\n(Current — paper)",
         grn("£0/month"),
         "yfinance + FRED (free) + SEC EDGAR (free) + Alternative.me (free)",
         "Tier 1 sign-ups recommended immediately. All free."],
        ["£100,000\n(Live trading — Phase 1)",
         amb("£25/month\n($29)"),
         "+ Polygon.io Starter",
         "Faster price data for tighter entries. Pays for itself in 1 trade."],
        ["£50,000\n(Growing portfolio)",
         amb("£170/month\n(~$200)"),
         "+ Polygon.io Unlimited + Quandl",
         "Real-time tick data becomes meaningful at this size."],
        ["£100,000\n(Institutional scale)",
         fc("£700/month\n(~$800)", "#FF8800"),
         "+ Ortex (short interest)",
         "Short squeeze precision matters more as position sizes grow."],
        ["£500,000+\n(Fund scale)",
         fc("£1,300/month\n(~$1,500)", "#FF4444"),
         "+ Refinitiv or Bloomberg",
         "Replace all free sources. Institutional SLA required."],
    ]
    t_cost = Table(
        [[P(c) for c in row] for row in cost_rows],
        colWidths=[38*mm, 32*mm, 62*mm, 46*mm]
    )
    t_cost.setStyle(TS(GOLD, fs=7))
    story.append(t_cost)
    story.append(PageBreak())

    # ── Section 6: .env.example Template ─────────────────────────────────────────
    story.append(Paragraph("Section 6 — Environment Variable Template (.env.example)", H1))
    story.append(Paragraph(
        "Copy this template to <b>.env</b> in your project root and fill in values as you subscribe.",
        BODY
    ))
    story.append(SP8)

    env_vars = """# NZT-48 Data API Keys — .env.example
# Copy to .env and fill in. Never commit .env to git.

# === TIER 1 (FREE — sign up now) ===
FRED_API_KEY=your_fred_api_key_here
# Get it at: fred.stlouisfed.org → My Account → API Keys

SEC_EDGAR_EMAIL=your_email@example.com
# Required as User-Agent header for SEC EDGAR API (no key needed)

NEWSAPI_KEY=your_newsapi_key_here
# Get it at: newsapi.org/register (100 requests/day free)

# Fear & Greed API: no key needed (free, 1000 req/month)

# === TIER 2 (PAID — subscribe when hitting £200/day target) ===
POLYGON_API_KEY=your_polygon_api_key_here
# Get it at: polygon.io/dashboard/signup
# Starter: $29/month · Unlimited: $199/month

QUANDL_API_KEY=your_quandl_api_key_here
# Get it at: data.nasdaq.com/sign-up

# === TELEGRAM (REQUIRED — already set up) ===
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

# === OPENAI (for AI Research Engine fallback) ===
OPENAI_API_KEY=your_openai_api_key_here
# Only needed if GEMINI_API_KEY is not set

GEMINI_API_KEY=your_gemini_api_key_here
# Recommended: Gemini 2.5 Flash is primary AI for research engine"""

    story.append(Paragraph(
        f'<font name="Courier" size="7.5" color="{CYAN.hexval()}">{env_vars.replace(chr(10), "<br/>")}</font>',
        S(fontName="Helvetica", fontSize=7.5, textColor=CYAN, leading=11,
          backColor=D3, borderPadding=8)
    ))

    # ── Mandate footer ─────────────────────────────────────────────────────────
    story.append(SP14)
    story.append(HRFlowable(width="100%", thickness=2, color=GOLD, spaceAfter=8))
    story.append(Paragraph(
        "SIGN UP SEQUENCE",
        S(fontName="Helvetica-Bold", fontSize=13, textColor=GOLD, alignment=TA_CENTER, spaceAfter=5)
    ))
    story.append(Paragraph(
        "1. FRED API (free) → 2. SEC EDGAR email (free) → 3. Alternative.me (free) → "
        "4. NewsAPI.org free tier → 5. When £200/day: Polygon.io Starter → "
        "6. When consistently profitable: Polygon.io Unlimited → 7. At £100K+: Ortex",
        S(fontName="Helvetica", fontSize=9, textColor=LIGHT, alignment=TA_CENTER, leading=14)
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=GOLD, spaceAfter=4))

    doc.build(story)
    _publish_pdf(OUT)
    return OUT

def _publish_pdf(path: str) -> None:
    """Delete old version at path, then copy fresh file to NZT-48 CORE PDFS on Desktop."""
    desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop", "NZT-48 CORE PDFS")
    os.makedirs(desktop_dir, exist_ok=True)
    dest = os.path.join(desktop_dir, os.path.basename(path))
    if os.path.exists(dest):
        os.remove(dest)
        print(f"✓ Deleted old: {dest}")
    shutil.copy2(path, dest)
    print(f"✓ PDF → {path}")
    print(f"✓ Copied → {dest}")

if __name__ == "__main__":
    path = build()
    import subprocess
    import sys
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform.startswith("linux"):
        subprocess.Popen(["xdg-open", path])
    print("PDF opened.")
