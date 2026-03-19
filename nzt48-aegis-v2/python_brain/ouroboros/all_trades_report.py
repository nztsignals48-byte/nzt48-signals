"""Generate PDF report of ALL simulated trades from WAL history.

Resolves ticker_id → symbol via engine startup log mapping.
Includes timestamps on every trade.

Usage:
    python -m python_brain.ouroboros.all_trades_report
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF (fitz) not installed", file=sys.stderr)
    sys.exit(1)


def build_ticker_map() -> dict[int, str]:
    """Build ticker_id -> symbol mapping from active_watchlist.json."""
    wl_path = Path("/app/config/active_watchlist.json")
    if not wl_path.exists():
        wl_path = Path("config/active_watchlist.json")
    if not wl_path.exists():
        return {}

    wl = json.loads(wl_path.read_text())
    ticker_map = {}
    idx = 0
    # The engine registers tickers in order: all from active_watchlist
    # vanguard first, then apex — matching config_loader.rs
    for section in ["vanguard", "apex"]:
        for entry in wl.get(section, []):
            sym = entry.get("symbol", f"T{idx}")
            ticker_map[idx] = sym
            idx += 1
    return ticker_map


def ns_to_datetime(ns: int) -> str:
    """Convert nanosecond timestamp to human-readable UTC datetime."""
    if ns <= 0:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (OSError, ValueError, OverflowError):
        return "N/A"


def parse_all_wal_events(wal_dir: Path, ticker_map: dict[int, str]):
    """Parse ALL WAL events (not date-filtered)."""
    entries = []
    exit_orders = []
    position_closed = []

    wal_files = sorted(wal_dir.glob("*.ndjson"))

    for wal_path in wal_files:
        if not wal_path.exists():
            continue
        for line in wal_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = event.get("event_time_ns", 0)
            payload = event.get("payload", {})

            if "RoutedOrder" in payload:
                d = payload["RoutedOrder"]
                tid = d.get("ticker_id", -1)
                # Use enriched symbol field if present, else resolve from map
                symbol = d.get("symbol", "") or ticker_map.get(tid, f"T{tid}")
                side = d.get("side", "?")
                record = {
                    "timestamp": ns_to_datetime(ts),
                    "ts_ns": ts,
                    "order_id": d.get("order_id", "?"),
                    "side": side,
                    "symbol": symbol,
                    "qty": d.get("qty", 0),
                    "confidence": d.get("confidence", 0),
                    "kelly": d.get("kelly_fraction", 0),
                    "value_gbp": d.get("approved_size", 0),
                    "currency": d.get("currency", "") or "?",
                    "strategy": d.get("strategy", "?"),
                }
                if side == "Sell":
                    exit_orders.append(record)
                else:
                    entries.append(record)

            elif "PositionClosed" in payload:
                d = payload["PositionClosed"]
                tid = d.get("ticker_id", -1)
                symbol = d.get("symbol", "") or ticker_map.get(tid, f"T{tid}")
                position_closed.append({
                    "timestamp": ns_to_datetime(ts),
                    "ts_ns": ts,
                    "symbol": symbol,
                    "qty": d.get("qty", 0),
                    "pnl_gbp": d.get("final_pnl", 0),
                })

    return entries, exit_orders, position_closed


def generate_html(entries, exit_orders, position_closed):
    """Generate full HTML report."""
    total_entries = len(entries)
    total_exits = len(position_closed)
    total_pnl = sum(e["pnl_gbp"] for e in position_closed)
    winners = sum(1 for e in position_closed if e["pnl_gbp"] > 0)
    losers = sum(1 for e in position_closed if e["pnl_gbp"] <= 0)
    win_rate = (winners / total_exits * 100) if total_exits > 0 else 0.0
    total_deployed = sum(e["value_gbp"] for e in entries)
    avg_conf = sum(e["confidence"] for e in entries) / len(entries) if entries else 0
    avg_kelly = sum(e["kelly"] for e in entries) / len(entries) if entries else 0

    # Unique symbols traded
    symbols_traded = sorted(set(e["symbol"] for e in entries))

    # Per-symbol stats
    sym_stats = {}
    for e in entries:
        s = e["symbol"]
        if s not in sym_stats:
            sym_stats[s] = {"count": 0, "value": 0.0}
        sym_stats[s]["count"] += 1
        sym_stats[s]["value"] += e["value_gbp"]

    pnl_class = "positive" if total_pnl >= 0 else "negative"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Date range
    all_ts = [e["ts_ns"] for e in entries if e["ts_ns"] > 0]
    first_trade = ns_to_datetime(min(all_ts)) if all_ts else "N/A"
    last_trade = ns_to_datetime(max(all_ts)) if all_ts else "N/A"

    html = f"""
    <html><head><style>
        body {{ font-family: Helvetica, Arial, sans-serif; font-size: 9px; margin: 15px; }}
        h1 {{ font-size: 16px; color: #1a1a2e; border-bottom: 2px solid #0f3460; padding-bottom: 4px; margin-bottom: 8px; }}
        h2 {{ font-size: 12px; color: #0f3460; margin-top: 12px; margin-bottom: 4px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 4px 0; }}
        th {{ background: #0f3460; color: white; padding: 3px 5px; text-align: left; font-size: 8px; }}
        td {{ border: 1px solid #ddd; padding: 2px 5px; font-size: 8px; }}
        tr:nth-child(even) {{ background: #f8f8f8; }}
        .positive {{ color: #28a745; font-weight: bold; }}
        .negative {{ color: #dc3545; font-weight: bold; }}
        .summary-box {{ background: #f0f4ff; border: 1px solid #0f3460; padding: 6px; margin: 6px 0; border-radius: 4px; }}
        .stat {{ display: inline-block; margin-right: 15px; }}
        .stat-value {{ font-size: 12px; font-weight: bold; }}
        .stat-label {{ font-size: 7px; color: #666; }}
        .section-info {{ color: #666; font-size: 8px; margin-bottom: 4px; }}
    </style></head><body>

    <h1>AEGIS V2 — Complete Simulated Trade History</h1>
    <p style="color: #666; font-size: 8px;">Generated: {now_str} | SIMULATION MODE | All trades from WAL</p>
    <p style="color: #666; font-size: 8px;">Period: {first_trade} to {last_trade}</p>

    <div class="summary-box">
        <div class="stat"><span class="stat-label">Entries</span><br><span class="stat-value">{total_entries:,}</span></div>
        <div class="stat"><span class="stat-label">Exits</span><br><span class="stat-value">{total_exits}</span></div>
        <div class="stat"><span class="stat-label">Win Rate</span><br><span class="stat-value">{win_rate:.0f}%</span></div>
        <div class="stat"><span class="stat-label">Total PnL</span><br><span class="stat-value {pnl_class}">£{total_pnl:.2f}</span></div>
        <div class="stat"><span class="stat-label">Capital Deployed</span><br><span class="stat-value">£{total_deployed:,.0f}</span></div>
        <div class="stat"><span class="stat-label">Symbols</span><br><span class="stat-value">{len(symbols_traded)}</span></div>
        <div class="stat"><span class="stat-label">Avg Conf</span><br><span class="stat-value">{avg_conf:.0f}</span></div>
        <div class="stat"><span class="stat-label">Avg Kelly</span><br><span class="stat-value">{avg_kelly:.4f}</span></div>
    </div>
    """

    # Top symbols by trade count
    if sym_stats:
        top_syms = sorted(sym_stats.items(), key=lambda x: -x[1]["count"])[:20]
        html += "<h2>Top 20 Symbols by Trade Count</h2><table>"
        html += "<tr><th>Symbol</th><th>Trades</th><th>Total Value (GBP)</th></tr>"
        for sym, stats in top_syms:
            html += f"<tr><td><b>{sym}</b></td><td>{stats['count']}</td><td>£{stats['value']:,.2f}</td></tr>"
        html += "</table>"

    # Position Closed (exits with PnL)
    if position_closed:
        html += f"<h2>Closed Positions ({len(position_closed)} total)</h2><table>"
        html += "<tr><th>Date/Time</th><th>Symbol</th><th>Qty</th><th>PnL</th></tr>"
        for e in position_closed:
            pnl_cls = "positive" if e["pnl_gbp"] >= 0 else "negative"
            html += (
                f"<tr><td>{e['timestamp']}</td><td><b>{e['symbol']}</b></td>"
                f"<td>{e['qty']}</td>"
                f"<td class='{pnl_cls}'>£{e['pnl_gbp']:.2f}</td></tr>"
            )
        html += "</table>"

    # Last 100 entries (most recent first)
    if entries:
        recent = sorted(entries, key=lambda x: x["ts_ns"], reverse=True)[:100]
        html += f"<h2>Latest 100 Entries (of {total_entries:,} total)</h2><table>"
        html += "<tr><th>Date/Time</th><th>Symbol</th><th>Qty</th><th>Value</th><th>Conf</th><th>Kelly</th><th>Strategy</th><th>Currency</th></tr>"
        for t in recent:
            html += (
                f"<tr><td>{t['timestamp']}</td><td><b>{t['symbol']}</b></td>"
                f"<td>{t['qty']}</td><td>£{t['value_gbp']:.2f}</td>"
                f"<td>{t['confidence']:.0f}</td><td>{t['kelly']:.4f}</td>"
                f"<td>{t['strategy']}</td><td>{t['currency']}</td></tr>"
            )
        html += "</table>"

    # Exit orders (force-closes from regime escalation)
    if exit_orders:
        html += f"<h2>Exit Orders ({len(exit_orders)} total)</h2><table>"
        html += "<tr><th>Date/Time</th><th>Symbol</th><th>Reason</th><th>Value</th></tr>"
        for e in exit_orders:
            html += (
                f"<tr><td>{e['timestamp']}</td><td><b>{e['symbol']}</b></td>"
                f"<td>{e['strategy']}</td><td>£{e['value_gbp']:.2f}</td></tr>"
            )
        html += "</table>"

    html += "<p style='color: #999; font-size: 7px; margin-top: 15px; text-align: center;'>Generated by AEGIS V2 — NZT-48 Trading System</p>"
    html += "</body></html>"
    return html


def html_to_pdf(html: str, output_path: str) -> None:
    """Convert HTML to PDF using PyMuPDF Story API."""
    writer = fitz.DocumentWriter(output_path)
    story = fitz.Story(html)
    page_rect = fitz.paper_rect("a4-l")
    content_rect = page_rect + (36, 36, -36, -36)

    more = True
    while more:
        dev = writer.begin_page(page_rect)
        more, _ = story.place(content_rect)
        story.draw(dev)
        writer.end_page()

    writer.close()


def main():
    wal_dir = Path("/app/events") if Path("/app/events").exists() else Path("events")

    print("Building ticker map...")
    ticker_map = build_ticker_map()
    print(f"Ticker map: {len(ticker_map)} entries")

    print("Parsing ALL WAL events...")
    entries, exit_orders, position_closed = parse_all_wal_events(wal_dir, ticker_map)
    print(f"Found {len(entries)} entries, {len(exit_orders)} exit orders, {len(position_closed)} closed positions")

    if not entries and not position_closed:
        print("No trades found in WAL.")
        return

    html = generate_html(entries, exit_orders, position_closed)

    output_dir = Path("/app/reports") if Path("/app").exists() else Path("reports")
    output_dir.mkdir(exist_ok=True)
    pdf_path = output_dir / "all_trades_report.pdf"

    html_to_pdf(html, str(pdf_path))
    print(f"PDF: {pdf_path} ({pdf_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
