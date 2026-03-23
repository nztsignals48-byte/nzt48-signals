"""AEGIS V2 — Daily Simulated Trade Report (PDF + Telegram).

Reads WAL ndjson for today's entries/exits, telemetry snapshot for live state,
generates a professional landscape-A4 PDF, and optionally sends via Telegram.

Data sources (in priority order):
  1. WAL RoutedOrder events (enriched: symbol, qty, currency, kelly)
  2. WAL PositionClosed events (symbol, qty, final_pnl)
  3. WAL ExitSignal events (reason, priority)
  4. telemetry_snapshot.json (equity, positions, ticks, signals, vetoes)

Usage:
    python -m python_brain.ouroboros.daily_sim_report [--send-telegram]
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF (fitz) not installed. pip install pymupdf", file=sys.stderr)
    sys.exit(1)

log = logging.getLogger("daily_sim_report")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
REPORT_DIR = Path(os.environ.get("AEGIS_REPORT_DIR", _PROJECT_ROOT / "data" / "sim_reports"))

# ISA-eligible suffixes (LSE leveraged ETPs)
ISA_SUFFIXES = (".L",)

# Exchange display names
EXCHANGE_NAMES = {
    "XLON": "London (LSE)",
    "XNYS": "New York (NYSE/NASDAQ)",
    "XETR": "Frankfurt (Xetra)",
    "XPAR": "Paris (Euronext)",
    "XAMS": "Amsterdam (Euronext)",
    "XHKG": "Hong Kong (HKEX)",
    "XASX": "Sydney (ASX)",
    "XNZE": "Wellington (NZX)",
}


# ---------------------------------------------------------------------------
# WAL parsing
# ---------------------------------------------------------------------------
def _today_ns_range() -> tuple[int, int]:
    """Return (start_ns, end_ns) for today UTC."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
    return int(start.timestamp() * 1_000_000_000), int(end.timestamp() * 1_000_000_000)


def _load_ticker_map() -> dict[int, str]:
    """Load ticker_id → symbol map from active_watchlist.json."""
    wl_path = CONFIG_DIR / "active_watchlist.json"
    if not wl_path.exists():
        return {}
    try:
        data = json.loads(wl_path.read_text())
        mapping: dict[int, str] = {}
        if isinstance(data, dict) and "tickers" in data:
            for t in data["tickers"]:
                tid = t.get("id") or t.get("ticker_id")
                sym = t.get("symbol", "")
                if tid is not None and sym:
                    mapping[int(tid)] = sym
        elif isinstance(data, list):
            for t in data:
                tid = t.get("id") or t.get("ticker_id")
                sym = t.get("symbol", "")
                if tid is not None and sym:
                    mapping[int(tid)] = sym
        return mapping
    except Exception:
        return {}


def parse_wal_events(wal_dir: Path) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Parse WAL for today's entries, exits, exit signals, and latest snapshot.

    Returns: (entries, exits, exit_signals, telemetry)
    """
    entries: list[dict] = []
    exits: list[dict] = []
    exit_signals: list[dict] = []
    snapshot: dict = {}

    start_ns, end_ns = _today_ns_range()
    ticker_map = _load_ticker_map()

    # AUDIT-FIX: Ensure wal_dir is a Path, then read current + all archive files.
    wal_dir = Path(wal_dir) if not isinstance(wal_dir, Path) else wal_dir
    wal_files = []
    current = wal_dir / "current.ndjson"
    if current.exists():
        wal_files.append(current)
    # Also check date-specific files
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for f in wal_dir.glob(f"{today_str}*.ndjson"):
        if f != current:
            wal_files.append(f)
    # Scan archive directory for all WAL files
    archive_dir = wal_dir / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.ndjson")):
            if f not in wal_files:
                wal_files.append(f)

    seen_order_ids: set[str] = set()

    for wal_path in wal_files:
        if not wal_path.exists():
            continue
        try:
            text = wal_path.read_text()
        except OSError as e:
            log.warning("Cannot read %s: %s", wal_path, e)
            continue

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_time = event.get("event_time_ns", 0)
            is_today = start_ns <= event_time <= end_ns
            payload = event.get("payload", {})

            if "RoutedOrder" in payload and is_today:
                data = payload["RoutedOrder"]
                side = data.get("side", "Long")
                # Skip sell/exit orders in entry list
                if side == "Sell":
                    continue
                order_id = data.get("order_id", "?")
                if order_id in seen_order_ids:
                    continue  # Dedup
                seen_order_ids.add(order_id)

                tid = data.get("ticker_id", -1)
                symbol = data.get("symbol") or ticker_map.get(tid, f"T{tid}")
                entries.append({
                    "order_id": order_id,
                    "symbol": symbol,
                    "ticker_id": tid,
                    "direction": side,
                    "qty": data.get("qty", 0),
                    "currency": data.get("currency", "GBP"),
                    "value_gbp": data.get("approved_size", 0.0),
                    "confidence": data.get("confidence", 0.0),
                    "kelly": data.get("kelly_fraction", 0.0),
                    "strategy": data.get("strategy", "?"),
                    "timestamp_ns": event_time,
                })

            elif "PositionClosed" in payload and is_today:
                data = payload["PositionClosed"]
                tid = data.get("ticker_id", -1)
                symbol = data.get("symbol") or ticker_map.get(tid, f"T{tid}")
                exits.append({
                    "symbol": symbol,
                    "ticker_id": tid,
                    "qty": data.get("qty", 0),
                    "pnl_gbp": data.get("final_pnl", 0.0),
                    "entry_ns": data.get("entry_time_ns", 0),
                    "exit_ns": data.get("exit_time_ns", event_time),
                })

            elif "ExitSignal" in payload and is_today:
                data = payload["ExitSignal"]
                exit_signals.append({
                    "ticker_id": data.get("ticker_id", -1),
                    "reason": data.get("reason", "?"),
                    "priority": data.get("priority", "?"),
                })

            elif "StateSnapshot" in payload:
                data = payload["StateSnapshot"]
                snapshot["equity"] = data.get("equity", 10000.0)
                snapshot["high_water"] = data.get("high_water", 10000.0)

    # Enrich from telemetry_snapshot.json (live engine state)
    tel_path = wal_dir / "telemetry_snapshot.json"
    if tel_path.exists():
        try:
            tel = json.loads(tel_path.read_text())
            snapshot.update({
                "equity": tel.get("equity", snapshot.get("equity", 10000.0)),
                "positions": tel.get("positions", 0),
                "ticks": tel.get("ticks_received", 0),
                "signals": tel.get("signals_generated", 0),
                "vetoes": tel.get("signals_vetoed", 0),
                "orders": tel.get("orders_submitted", 0),
                "regime": tel.get("regime", "Normal"),
                "session_mode": tel.get("session_mode", "?"),
            })
        except (json.JSONDecodeError, OSError):
            pass

    # Filter out zero-size phantom entries from old engine builds
    entries = [e for e in entries if e["value_gbp"] > 0 or e["qty"] > 0]

    return entries, exits, exit_signals, snapshot


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------
def _build_html(entries: list[dict], exits: list[dict],
                exit_signals: list[dict], telemetry: dict, date_str: str) -> str:
    """Build the full HTML report."""

    # ── Computed stats ──────────────────────────────────────────────────────
    total_deployed = sum(e["value_gbp"] for e in entries)
    realized_pnl = sum(x["pnl_gbp"] for x in exits)
    winners = sum(1 for x in exits if x["pnl_gbp"] > 0)
    losers = sum(1 for x in exits if x["pnl_gbp"] <= 0)
    win_rate = (winners / len(exits) * 100) if exits else 0.0
    avg_conf = sum(e["confidence"] for e in entries) / len(entries) if entries else 0.0
    avg_kelly = sum(e["kelly"] for e in entries) / len(entries) if entries else 0.0
    equity = telemetry.get("equity", 10000.0)
    unrealized_pnl = equity - 10000.0 - realized_pnl
    total_pnl = realized_pnl + unrealized_pnl
    open_positions = telemetry.get("positions", len(entries) - len(exits))
    ticks = telemetry.get("ticks", 0)
    signals = telemetry.get("signals", len(entries))
    vetoes = telemetry.get("vetoes", 0)
    regime = telemetry.get("regime", "Normal")

    # Exit reason breakdown
    exit_reasons = Counter(s["reason"] for s in exit_signals)

    # Exchange breakdown from entries
    exchange_counts: dict[str, int] = {}
    exchange_values: dict[str, float] = {}
    for e in entries:
        # Infer exchange from symbol suffix or currency
        ex = _infer_exchange(e["symbol"], e["currency"])
        exchange_counts[ex] = exchange_counts.get(ex, 0) + 1
        exchange_values[ex] = exchange_values.get(ex, 0.0) + e["value_gbp"]

    # ISA positions
    isa_entries = [e for e in entries if any(e["symbol"].endswith(s) for s in ISA_SUFFIXES)]
    isa_value = sum(e["value_gbp"] for e in isa_entries)

    # Evidence maturity
    total_trades = len(entries) + len(exits)
    evidence = "bootstrap" if total_trades < 50 else "low-confidence" if total_trades < 250 else "mature"

    pnl_color = "#28a745" if total_pnl >= 0 else "#dc3545"
    rpnl_color = "#28a745" if realized_pnl >= 0 else "#dc3545"

    # ── Build trade rows ────────────────────────────────────────────────────
    trade_rows = ""
    for i, e in enumerate(entries):
        bg = "#f0f4ff" if i % 2 == 0 else "#ffffff"
        # Highlight ISA ETPs
        if any(e["symbol"].endswith(s) for s in ISA_SUFFIXES):
            bg = "#e8f5e9"
        trade_rows += (
            f'<tr style="background-color:{bg};">'
            f'<td>{i+1}</td><td><b>{e["symbol"]}</b></td><td>{e["direction"]}</td>'
            f'<td>{e["qty"]}</td><td>{e["value_gbp"]:,.2f}</td>'
            f'<td>{e["confidence"]:.0f}</td><td>{e["kelly"]:.3f}</td>'
            f'<td>{e["currency"]}</td><td>{e["strategy"]}</td></tr>\n'
        )

    # ── Build exit rows ─────────────────────────────────────────────────────
    exit_rows = ""
    for i, x in enumerate(exits):
        bg = "#f0f4ff" if i % 2 == 0 else "#ffffff"
        pc = "#28a745" if x["pnl_gbp"] >= 0 else "#dc3545"
        exit_rows += (
            f'<tr style="background-color:{bg};">'
            f'<td>{i+1}</td><td><b>{x["symbol"]}</b></td><td>{x["qty"]}</td>'
            f'<td style="color:{pc}; font-weight:bold;">£{x["pnl_gbp"]:.2f}</td></tr>\n'
        )

    # ── Build exchange rows ─────────────────────────────────────────────────
    exch_rows = ""
    for ex in sorted(exchange_counts.keys(), key=lambda k: -exchange_values.get(k, 0)):
        c, v = exchange_counts[ex], exchange_values[ex]
        pct = v / total_deployed * 100 if total_deployed > 0 else 0
        name = EXCHANGE_NAMES.get(ex, ex)
        exch_rows += (
            f'<tr><td><b>{ex}</b></td><td>{name}</td>'
            f'<td>{c}</td><td>{v:,.2f}</td><td>{pct:.1f}%</td></tr>\n'
        )

    # ── Build exit reason rows ──────────────────────────────────────────────
    reason_rows = ""
    for reason, count in exit_reasons.most_common(10):
        reason_rows += f'<tr><td>{reason}</td><td>{count}</td></tr>\n'

    # ── ISA rows ────────────────────────────────────────────────────────────
    isa_rows = ""
    for e in isa_entries:
        pct_of_limit = e["value_gbp"] / 20000 * 100
        isa_rows += (
            f'<tr><td><b>{e["symbol"]}</b></td><td>{e["qty"]}</td>'
            f'<td>{e["value_gbp"]:,.2f}</td><td>{pct_of_limit:.1f}%</td></tr>\n'
        )

    # ── Full HTML ───────────────────────────────────────────────────────────
    html = f"""<html><head><style>
body {{ font-family: Helvetica, Arial, sans-serif; font-size: 8.5px; color: #1a1a2e; }}
h1 {{ font-size: 18px; color: #0f3460; text-align: center; margin-bottom: 1px; }}
h2 {{ font-size: 11px; color: #16213e; border-bottom: 1.5px solid #0f3460; padding-bottom: 2px; margin-top: 8px; margin-bottom: 4px; }}
.sub {{ text-align: center; font-size: 9px; color: #555; margin-bottom: 2px; }}
.date {{ text-align: center; font-size: 8px; color: #777; margin-bottom: 6px; }}
table.s {{ width: 100%; border-collapse: collapse; margin-bottom: 4px; }}
table.s td {{ padding: 2px 5px; font-size: 8.5px; border-bottom: 1px solid #e8e8e8; }}
td.l {{ font-weight: bold; color: #0f3460; width: 15%; background-color: #f5f7ff; }}
td.v {{ color: #1a1a2e; width: 35%; }}
table.t {{ width: 100%; border-collapse: collapse; margin-top: 2px; }}
table.t th {{ background-color: #0f3460; color: white; padding: 2px 2px; text-align: center; font-size: 6.5px; }}
table.t td {{ padding: 1.5px 2px; text-align: center; border-bottom: 0.5px solid #ddd; font-size: 6.5px; }}
table.e {{ border-collapse: collapse; margin-top: 2px; font-size: 8px; }}
table.e th {{ background-color: #16213e; color: white; padding: 2px 5px; text-align: center; font-size: 7px; }}
table.e td {{ padding: 2px 5px; text-align: center; border-bottom: 1px solid #ddd; font-size: 7px; }}
.notes {{ font-size: 7px; color: #333; margin-top: 3px; padding: 3px 5px; background-color: #fff8e1; border-left: 2px solid #ff9800; }}
.notes p {{ margin: 1px 0; }}
.ft {{ text-align: center; font-size: 10px; font-weight: bold; color: #c62828; margin-top: 6px; padding: 3px; border-top: 2px solid #c62828; border-bottom: 2px solid #c62828; letter-spacing: 2px; }}
.fs {{ text-align: center; font-size: 6px; color: #999; margin-top: 2px; }}
</style></head><body>

<h1>AEGIS V2 — Daily Simulation Report</h1>
<p class="sub">TypeA-F Strategies | Crucible Mode (Paper) | {regime} Regime</p>
<p class="date">{date_str} | Generated by Ouroboros</p>

<h2>Executive Summary</h2>
<table class="s">
<tr><td class="l">Equity</td><td class="v">£{equity:,.2f}</td><td class="l">Total P&amp;L</td><td class="v" style="color:{pnl_color}; font-weight:bold;">£{total_pnl:,.2f}</td></tr>
<tr><td class="l">Entries / Exits</td><td class="v">{len(entries)} / {len(exits)}</td><td class="l">Realised P&amp;L</td><td class="v" style="color:{rpnl_color};">£{realized_pnl:,.2f}</td></tr>
<tr><td class="l">Win Rate</td><td class="v">{win_rate:.0f}% ({winners}W / {losers}L)</td><td class="l">Unrealised P&amp;L</td><td class="v">£{unrealized_pnl:,.2f}</td></tr>
<tr><td class="l">Capital Deployed</td><td class="v">£{total_deployed:,.2f}</td><td class="l">Open Positions</td><td class="v">{open_positions}</td></tr>
<tr><td class="l">Signals / Vetoes</td><td class="v">{signals:,} / {vetoes:,}</td><td class="l">Ticks Received</td><td class="v">{ticks:,}</td></tr>
<tr><td class="l">Avg Confidence</td><td class="v">{avg_conf:.0f}%</td><td class="l">Evidence Tier</td><td class="v">{evidence}</td></tr>
</table>
"""

    # ── Persistent memory context (cumulative stats) ─────────────────────
    try:
        from python_brain.ouroboros.persistent_memory import load_memory
        mem = load_memory()
        if mem.total_sessions > 0:
            html += f"""
<h2>System Memory (Cumulative)</h2>
<table class="s">
<tr><td class="l">Sessions</td><td class="v">{mem.total_sessions}</td><td class="l">First Session</td><td class="v">{mem.first_session_date or 'N/A'}</td></tr>
<tr><td class="l">All-Time Trades</td><td class="v">{mem.total_exits}</td><td class="l">All-Time PnL</td><td class="v">£{mem.cumulative_pnl:,.2f}</td></tr>
<tr><td class="l">All-Time WR</td><td class="v">{mem.all_time_win_rate:.1%}</td><td class="l">Tickers Tracked</td><td class="v">{len(mem.ticker_stats)}</td></tr>
<tr><td class="l">Lessons</td><td class="v">{len(mem.lessons)}</td><td class="l">Regimes</td><td class="v">{len(mem.regime_stats)}</td></tr>
</table>
"""
    except Exception:
        pass  # Memory not yet initialized — skip section

    # ── Entry table ─────────────────────────────────────────────────────────
    if entries:
        html += f"""
<h2>Simulated Entries ({len(entries)})</h2>
<table class="t">
<tr><th>#</th><th>Symbol</th><th>Dir</th><th>Qty</th><th>Value £</th><th>Conf</th><th>Kelly</th><th>CCY</th><th>Strategy</th></tr>
{trade_rows}
<tr style="background-color:#e8eaf6; font-weight:bold;"><td colspan="4" style="text-align:right;">TOTAL</td><td>{total_deployed:,.2f}</td><td colspan="4"></td></tr>
</table>
"""

    # ── Exit table ──────────────────────────────────────────────────────────
    if exits:
        html += f"""
<h2>Simulated Exits ({len(exits)})</h2>
<table class="e">
<tr><th>#</th><th>Symbol</th><th>Qty</th><th>P&amp;L</th></tr>
{exit_rows}
<tr style="background-color:#e8eaf6; font-weight:bold;"><td colspan="3" style="text-align:right;">TOTAL</td><td style="color:{rpnl_color}; font-weight:bold;">£{realized_pnl:,.2f}</td></tr>
</table>
"""

    # ── Exit reasons ────────────────────────────────────────────────────────
    if exit_reasons:
        html += f"""
<h2>Exit Reasons</h2>
<table class="e">
<tr><th>Reason</th><th>Count</th></tr>
{reason_rows}
</table>
"""

    # ── Exchange breakdown ──────────────────────────────────────────────────
    if exchange_counts:
        html += f"""
<h2>Exchange Breakdown</h2>
<table class="e">
<tr><th>Exchange</th><th>Location</th><th>Trades</th><th>Value £</th><th>%</th></tr>
{exch_rows}
<tr style="background-color:#e8eaf6; font-weight:bold;"><td colspan="2">TOTAL</td><td>{len(entries)}</td><td>{total_deployed:,.2f}</td><td>100%</td></tr>
</table>
"""

    # ── ISA positions ───────────────────────────────────────────────────────
    if isa_entries:
        html += f"""
<h2>ISA-Eligible Positions</h2>
<table class="e">
<tr><th>Symbol</th><th>Qty</th><th>Value £</th><th>% of £20k</th></tr>
{isa_rows}
<tr style="background-color:#e8eaf6; font-weight:bold;"><td colspan="2">ISA Total</td><td>{isa_value:,.2f}</td><td>{isa_value/20000*100:.1f}%</td></tr>
</table>
"""

    # ── Notes ───────────────────────────────────────────────────────────────
    html += f"""
<div class="notes">
<p><b>Evidence:</b> {evidence} tier ({total_trades} total trades). {'Preliminary estimates — 100+ trades needed for validated mode.' if evidence == 'bootstrap' else 'Building confidence.'}</p>
<p><b>Leverage:</b> £{total_deployed:,.0f} deployed = {total_deployed/10000*100:.0f}% of starting equity. Simulation runs without margin constraints.</p>
</div>

<p class="ft">SIMULATION ONLY — No real orders were submitted</p>
<p class="fs">AEGIS V2 | TypeA-F | Crucible Mode | {date_str} | {len(entries)} entries, {len(exits)} exits | £{total_pnl:,.2f} P&amp;L</p>

</body></html>"""

    return html


def _infer_exchange(symbol: str, currency: str) -> str:
    """Infer MIC exchange code from symbol suffix and currency."""
    if symbol.endswith(".L"):
        return "XLON"
    if symbol.endswith(".AX"):
        return "XASX"
    if symbol.endswith(".NZ"):
        return "XNZE"
    if symbol.endswith(".HK"):
        return "XHKG"
    # Currency-based inference for non-suffixed symbols
    if currency == "USD":
        return "XNYS"
    if currency == "EUR":
        # Could be XETR, XPAR, XAMS — default to XETR (most common)
        return "XETR"
    if currency == "GBP":
        return "XLON"
    if currency == "CHF":
        return "XSWX"
    return "OTHER"


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------
def html_to_pdf(html: str, output_path: str) -> int:
    """Render HTML to landscape-A4 PDF via fitz.Story. Returns page count."""
    pw, ph, m = 842, 595, 28  # Landscape A4, tight margins
    writer = fitz.DocumentWriter(output_path)
    story = fitz.Story(html)
    where = fitz.Rect(m, m, pw - m, ph - m)

    pages = 0
    more = True
    while more:
        pages += 1
        dev = writer.begin_page(fitz.Rect(0, 0, pw, ph))
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()

    writer.close()
    return pages


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    send_telegram = "--send-telegram" in sys.argv

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%A, %d %B %Y %H:%M UTC")
    date_file = now.strftime("%Y-%m-%d")

    entries, exits, exit_signals, telemetry = parse_wal_events(WAL_DIR)

    if not entries and not exits:
        log.info("No simulated trades found in WAL for today. Skipping report.")
        return

    log.info("Found %d entries, %d exits, %d exit signals for today", len(entries), len(exits), len(exit_signals))

    html = _build_html(entries, exits, exit_signals, telemetry, date_str)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = REPORT_DIR / f"sim_report_{date_file}.pdf"

    pages = html_to_pdf(html, str(pdf_path))
    size_kb = pdf_path.stat().st_size / 1024
    log.info("PDF generated: %s (%d pages, %.1f KB)", pdf_path, pages, size_kb)

    if send_telegram:
        try:
            from python_brain.ouroboros.telegram_notify import send_document
            caption = f"Daily Sim Report — {date_file} | {len(entries)}E {len(exits)}X"
            send_document(str(pdf_path), caption)
            log.info("Sent to Telegram")
        except Exception as e:
            log.error("Telegram send failed: %s", e, exc_info=True)


if __name__ == "__main__":
    main()
