"""AEGIS V2 — Institutional-Grade Trade Journal PDF Generator.

Connects to EC2 Docker container via SSH, reads WAL events and SIM logs,
deduplicates trades, computes analytics, and generates a premium PDF report
using PyMuPDF (fitz.Story).

Usage:
    python -m ouroboros.institutional_report
    python ouroboros/institutional_report.py

Output: reports/trade_journal.pdf
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import fitz  # PyMuPDF
except ImportError:
    print("FATAL: PyMuPDF (fitz) not installed. pip install pymupdf", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
EC2_HOST = "ubuntu@3.230.44.22"
SSH_KEY = str(Path.home() / ".ssh" / "nzt48-key.pem")
CONTAINER = "aegis-v2"
SSH_OPTS = [
    "-i", SSH_KEY,
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=15",
    "-o", "ServerAliveInterval=5",
]
STARTING_EQUITY = 100_000.0

# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    """Unified trade record from any source (WAL or SIM log)."""
    order_id: str = ""
    symbol: str = ""
    direction: str = "Long"  # Long / Short
    qty: int = 0
    entry_price_native: float = 0.0
    entry_price_gbp: float = 0.0
    currency: str = "GBP"
    notional_gbp: float = 0.0
    confidence: float = 0.0
    kelly: float = 0.0
    strategy: str = ""
    entry_time: Optional[datetime] = None
    exit_price_native: float = 0.0
    exit_time: Optional[datetime] = None
    pnl_gbp: float = 0.0
    pnl_pct: float = 0.0
    status: str = "OPEN"  # OPEN / CLOSED / STOPPED
    source: str = ""  # "WAL" or "LOG"
    ticker_id: int = -1
    exchange: str = ""

    @property
    def hold_duration(self) -> Optional[timedelta]:
        if self.entry_time and self.exit_time:
            return self.exit_time - self.entry_time
        return None

    @property
    def hold_duration_str(self) -> str:
        d = self.hold_duration
        if d is None:
            return "-"
        total_secs = int(d.total_seconds())
        if total_secs < 60:
            return f"{total_secs}s"
        if total_secs < 3600:
            return f"{total_secs // 60}m {total_secs % 60}s"
        hrs = total_secs // 3600
        mins = (total_secs % 3600) // 60
        return f"{hrs}h {mins}m"


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

def _ssh_exec(cmd: str, timeout: int = 30) -> str:
    """Execute command on EC2 via SSH. Returns stdout."""
    full_cmd = ["ssh"] + SSH_OPTS + [EC2_HOST, cmd]
    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        print(f"  SSH timeout for: {cmd[:80]}...", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"  SSH error: {e}", file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# Data Fetching
# ---------------------------------------------------------------------------

def fetch_wal_data() -> str:
    """Fetch WAL ndjson from Docker container."""
    print("[1/4] Fetching WAL from EC2...")
    cmd = f"docker exec {CONTAINER} cat /app/events/current.ndjson 2>/dev/null"
    data = _ssh_exec(cmd, timeout=60)
    lines = data.strip().split("\n") if data.strip() else []
    print(f"  WAL: {len(lines)} lines")
    return data


def fetch_sim_logs() -> str:
    """Fetch SIM_TRADE and SIM_EXIT log lines with timestamps."""
    print("[2/4] Fetching SIM logs from EC2...")
    cmd = f"docker logs {CONTAINER} --timestamps 2>&1 | grep -E 'SIM_TRADE|SIM_EXIT'"
    data = _ssh_exec(cmd, timeout=30)
    lines = data.strip().split("\n") if data.strip() else []
    print(f"  SIM logs: {len(lines)} lines")
    return data


def fetch_watchlist() -> Dict[int, dict]:
    """Fetch active_watchlist.json for ticker_id -> symbol mapping."""
    print("[3/4] Fetching watchlist for ticker mapping...")
    cmd = f"docker exec {CONTAINER} cat /app/config/active_watchlist.json 2>/dev/null"
    data = _ssh_exec(cmd, timeout=15)
    if not data.strip():
        return {}

    try:
        wl = json.loads(data)
    except json.JSONDecodeError:
        return {}

    ticker_map: Dict[int, dict] = {}
    idx = 0
    for section in ["vanguard", "apex"]:
        for entry in wl.get(section, []):
            ticker_map[idx] = {
                "symbol": entry.get("symbol", f"T{idx}"),
                "exchange": entry.get("exchange", ""),
                "currency": entry.get("currency", "GBP"),
            }
            idx += 1

    print(f"  Watchlist: {len(ticker_map)} tickers mapped")
    return ticker_map


# ---------------------------------------------------------------------------
# Parsing: WAL Events
# ---------------------------------------------------------------------------

def _ns_to_dt(ns: int) -> Optional[datetime]:
    """Convert nanosecond epoch to datetime UTC."""
    if ns <= 0:
        return None
    try:
        return datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc)
    except (OSError, ValueError, OverflowError):
        return None


def parse_wal_events(
    wal_data: str,
    ticker_map: Dict[int, dict],
) -> Tuple[Dict[str, Trade], List[dict], float, float]:
    """Parse WAL ndjson into trades and state info.

    Returns:
        (order_id_to_trade, position_closed_list, equity, high_water)
    """
    orders: Dict[str, Trade] = {}  # order_id -> Trade (deduplicated)
    closed_positions: List[dict] = []
    equity = STARTING_EQUITY
    high_water = STARTING_EQUITY

    for line in wal_data.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        payload = event.get("payload", {})
        ts_ns = event.get("event_time_ns", 0)

        if "RoutedOrder" in payload:
            ro = payload["RoutedOrder"]
            oid = ro.get("order_id", "")
            if not oid or oid in orders:
                continue  # Deduplicate: keep first occurrence only
            tid = ro.get("ticker_id", -1)
            info = ticker_map.get(tid, {})
            symbol = ro.get("symbol") or info.get("symbol", f"T{tid}")
            exchange = info.get("exchange", _infer_exchange(symbol))
            currency = ro.get("currency") or info.get("currency", "GBP")

            orders[oid] = Trade(
                order_id=oid,
                symbol=symbol,
                direction=ro.get("side", "Long"),
                ticker_id=tid,
                confidence=ro.get("confidence", 0.0),
                kelly=ro.get("kelly_fraction", 0.0),
                strategy=ro.get("strategy", ""),
                notional_gbp=ro.get("approved_size", 0.0),
                currency=currency,
                entry_time=_ns_to_dt(ts_ns),
                source="WAL",
                exchange=exchange,
                status="OPEN",
            )

        elif "PositionClosed" in payload:
            pc = payload["PositionClosed"]
            closed_positions.append({
                "ticker_id": pc.get("ticker_id", -1),
                "final_pnl": pc.get("final_pnl", 0.0),
                "entry_time_ns": pc.get("entry_time_ns", 0),
                "exit_time_ns": pc.get("exit_time_ns", 0),
                "symbol": pc.get("symbol", ""),
                "qty": pc.get("qty", 0),
            })

        elif "StateSnapshot" in payload:
            ss = payload["StateSnapshot"]
            equity = ss.get("equity", equity)
            high_water = ss.get("high_water", high_water)

    return orders, closed_positions, equity, high_water


# ---------------------------------------------------------------------------
# Parsing: SIM Logs
# ---------------------------------------------------------------------------

# SIM_TRADE: order-N Long/Short SYMBOL xQTY @ PRICE CURRENCY (GBP GBPPRICE) val=£VALUE conf=CONF kelly=KELLY [Strategy]
_SIM_TRADE_RE = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+"
    r"SIM_TRADE:\s+(?P<order_id>order-\d+)\s+"
    r"(?P<direction>Long|Short)\s+"
    r"(?P<symbol>\S+)\s+"
    r"x(?P<qty>\d+)\s+@\s+"
    r"(?P<price>[\d.]+)\s+(?P<currency>[A-Z]+)\s+"
    r"\(GBP\s+(?P<gbp_price>[\d.]+)\)\s+"
    r"val=[^0-9]*(?P<value>[\d,.]+)\s+"
    r"conf=(?P<conf>[\d.]+)\s+"
    r"kelly=(?P<kelly>[\d.]+)\s+"
    r"\[(?P<strategy>[^\]]+)\]"
)

# SIM_EXIT: SYMBOL xQTY @ PRICE CURRENCY pnl=£PNL
_SIM_EXIT_RE = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+"
    r"SIM_EXIT:\s+(?P<symbol>\S+)\s+"
    r"x(?P<qty>\d+)\s+@\s+"
    r"(?P<price>[\d.]+)\s+(?P<currency>[A-Z]+)\s+"
    r"pnl=[^0-9-]*(?P<pnl>-?[\d,.]+)"
)


def _parse_num(s: str) -> float:
    """Parse a number string that may contain commas."""
    return float(s.replace(",", ""))


def _parse_ts(ts_str: str) -> Optional[datetime]:
    """Parse Docker timestamp."""
    try:
        # Truncate nanoseconds to microseconds for Python
        clean = re.sub(r"(\.\d{6})\d*Z", r"\1+00:00", ts_str)
        return datetime.fromisoformat(clean)
    except (ValueError, TypeError):
        return None


def parse_sim_logs(log_data: str) -> Tuple[Dict[str, Trade], List[dict]]:
    """Parse SIM_TRADE and SIM_EXIT log lines.

    Returns:
        (order_id_to_trade, exit_records)
    """
    trades: Dict[str, Trade] = {}
    exits: List[dict] = []

    for line in log_data.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        m = _SIM_TRADE_RE.match(line)
        if m:
            oid = m.group("order_id")
            symbol = m.group("symbol")
            trades[oid] = Trade(
                order_id=oid,
                symbol=symbol,
                direction=m.group("direction"),
                qty=int(m.group("qty")),
                entry_price_native=float(m.group("price")),
                entry_price_gbp=float(m.group("gbp_price")),
                currency=m.group("currency"),
                notional_gbp=_parse_num(m.group("value")),
                confidence=float(m.group("conf")),
                kelly=float(m.group("kelly")),
                strategy=m.group("strategy"),
                entry_time=_parse_ts(m.group("timestamp")),
                source="LOG",
                exchange=_infer_exchange(symbol),
                status="OPEN",
            )
            continue

        m = _SIM_EXIT_RE.match(line)
        if m:
            exits.append({
                "symbol": m.group("symbol"),
                "qty": int(m.group("qty")),
                "price": float(m.group("price")),
                "currency": m.group("currency"),
                "pnl": _parse_num(m.group("pnl")),
                "timestamp": _parse_ts(m.group("timestamp")),
            })

    return trades, exits


# ---------------------------------------------------------------------------
# Exchange inference
# ---------------------------------------------------------------------------

def _infer_exchange(symbol: str) -> str:
    """Infer exchange from symbol suffix."""
    if symbol.endswith(".L"):
        return "LSE"
    if symbol.endswith(".DE") or symbol.endswith(".F"):
        return "XETRA"
    if symbol.endswith(".PA"):
        return "EURONEXT"
    if symbol.endswith(".AS"):
        return "EURONEXT"
    if symbol.endswith(".MI"):
        return "BORSA"
    # Known XETRA blue chips (no suffix in SIM logs)
    xetra_syms = {
        "MBG", "EOAN", "VOW3", "SAN", "MUV2", "ASML", "SAP",
        "ALV", "BAS", "BAYN", "BMW", "DBK", "DTE", "FRE", "HEN3",
        "IFX", "LIN", "MRK", "RWE", "SIE", "VOW",
    }
    if symbol.upper() in xetra_syms:
        return "XETRA"
    # Known US tickers
    us_syms = {"ORCL", "MSFT", "AAPL", "AMZN", "GOOG", "GOOGL", "META",
               "NVDA", "TSLA", "AMD", "CRM", "NFLX", "INTC", "QCOM"}
    if symbol.upper() in us_syms:
        return "NASDAQ"
    return "OTHER"


# ---------------------------------------------------------------------------
# Trade Merging & Analytics
# ---------------------------------------------------------------------------

def merge_trades(
    wal_orders: Dict[str, Trade],
    wal_closed: List[dict],
    log_trades: Dict[str, Trade],
    log_exits: List[dict],
    ticker_map: Dict[int, dict],
    equity: float,
) -> List[Trade]:
    """Merge WAL and SIM log data into a unified trade list.

    SIM logs are the richer source (have prices, qty), so prefer them.
    WAL PositionClosed events provide exit PnL.
    """
    # Start with SIM log trades as base (they have actual prices)
    merged: Dict[str, Trade] = {}
    for oid, t in log_trades.items():
        merged[oid] = t

    # Supplement from WAL orders (for any orders not in SIM logs)
    for oid, t in wal_orders.items():
        if oid not in merged:
            merged[oid] = t

    # Apply SIM log exits
    exit_by_symbol: Dict[str, list] = defaultdict(list)
    for ex in log_exits:
        exit_by_symbol[ex["symbol"]].append(ex)

    for oid, trade in merged.items():
        exits = exit_by_symbol.get(trade.symbol, [])
        if exits:
            ex = exits.pop(0)
            trade.exit_price_native = ex["price"]
            trade.exit_time = ex.get("timestamp")
            trade.pnl_gbp = ex["pnl"]
            trade.status = "CLOSED"
            if trade.notional_gbp > 0:
                trade.pnl_pct = (trade.pnl_gbp / trade.notional_gbp) * 100

    # Apply WAL PositionClosed events by ticker_id
    closed_by_tid: Dict[int, list] = defaultdict(list)
    for pc in wal_closed:
        closed_by_tid[pc["ticker_id"]].append(pc)

    for oid, trade in merged.items():
        if trade.status == "CLOSED":
            continue
        closes = closed_by_tid.get(trade.ticker_id, [])
        if closes:
            pc = closes.pop(0)
            info = ticker_map.get(pc["ticker_id"], {})
            if not trade.symbol or trade.symbol.startswith("T"):
                trade.symbol = pc.get("symbol") or info.get("symbol", trade.symbol)
            trade.pnl_gbp = pc["final_pnl"]
            trade.exit_time = _ns_to_dt(pc["exit_time_ns"])
            if not trade.entry_time:
                trade.entry_time = _ns_to_dt(pc["entry_time_ns"])
            trade.status = "CLOSED"
            if trade.notional_gbp > 0:
                trade.pnl_pct = (trade.pnl_gbp / trade.notional_gbp) * 100

    result = sorted(merged.values(), key=lambda t: t.entry_time or datetime.min.replace(tzinfo=timezone.utc))
    return result


def compute_analytics(trades: List[Trade], equity: float, high_water: float) -> dict:
    """Compute institutional analytics from trade list."""
    closed = [t for t in trades if t.status == "CLOSED"]
    open_trades = [t for t in trades if t.status == "OPEN"]
    winners = [t for t in closed if t.pnl_gbp > 0]
    losers = [t for t in closed if t.pnl_gbp <= 0]

    total_pnl = sum(t.pnl_gbp for t in closed)
    total_notional = sum(t.notional_gbp for t in trades)
    win_rate = (len(winners) / len(closed) * 100) if closed else 0.0

    # Average hold time
    hold_times = [t.hold_duration.total_seconds() for t in closed if t.hold_duration]
    avg_hold_secs = sum(hold_times) / len(hold_times) if hold_times else 0.0

    # Max drawdown (simplified from PnL series)
    running_pnl = 0.0
    peak_pnl = 0.0
    max_dd = 0.0
    for t in closed:
        running_pnl += t.pnl_gbp
        peak_pnl = max(peak_pnl, running_pnl)
        dd = peak_pnl - running_pnl
        max_dd = max(max_dd, dd)

    # Sharpe estimate (annualised, from trade returns)
    if closed:
        returns = [t.pnl_pct / 100 for t in closed if t.pnl_pct != 0]
        if len(returns) >= 2:
            mean_ret = sum(returns) / len(returns)
            var = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
            std_ret = math.sqrt(var) if var > 0 else 1e-9
            # Annualise assuming ~252 trading days
            sharpe = (mean_ret / std_ret) * math.sqrt(252)
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    # Profit factor
    gross_profit = sum(t.pnl_gbp for t in winners)
    gross_loss = abs(sum(t.pnl_gbp for t in losers))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    # Average win / loss
    avg_win = (gross_profit / len(winners)) if winners else 0.0
    avg_loss = (gross_loss / len(losers)) if losers else 0.0

    return {
        "total_trades": len(trades),
        "open_trades": len(open_trades),
        "closed_trades": len(closed),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "total_notional": total_notional,
        "avg_hold_secs": avg_hold_secs,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "equity": equity,
        "high_water": high_water,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
    }


# ---------------------------------------------------------------------------
# HTML Generation (Institutional Quality)
# ---------------------------------------------------------------------------

def _fmt_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_pnl(val: float) -> str:
    cls = "profit" if val >= 0 else "loss"
    sign = "+" if val > 0 else ""
    return f'<span class="{cls}">{sign}{val:,.2f}</span>'


def _fmt_pct(val: float) -> str:
    cls = "profit" if val >= 0 else "loss"
    sign = "+" if val > 0 else ""
    return f'<span class="{cls}">{sign}{val:.2f}%</span>'


def _fmt_hold(secs: float) -> str:
    if secs <= 0:
        return "-"
    if secs < 60:
        return f"{int(secs)}s"
    if secs < 3600:
        return f"{int(secs // 60)}m {int(secs % 60)}s"
    hrs = int(secs // 3600)
    mins = int((secs % 3600) // 60)
    return f"{hrs}h {mins}m"


_CSS = """
body {
    font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
    font-size: 9px;
    color: #1a1a2e;
    margin: 0;
    padding: 20px 24px;
    line-height: 1.4;
}
.header {
    border-bottom: 3px solid #0f3460;
    padding-bottom: 8px;
    margin-bottom: 12px;
}
.header h1 {
    font-size: 18px;
    color: #0f3460;
    margin: 0 0 2px 0;
    letter-spacing: 1px;
}
.header .subtitle {
    font-size: 9px;
    color: #666;
}
.header .equity-badge {
    font-size: 12px;
    font-weight: bold;
    color: #0f3460;
}
h2 {
    font-size: 12px;
    color: #0f3460;
    border-bottom: 1px solid #dde;
    padding-bottom: 3px;
    margin: 14px 0 6px 0;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
h3 {
    font-size: 10px;
    color: #333;
    margin: 10px 0 4px 0;
}

/* Summary cards */
.summary-grid {
    border: 1px solid #0f3460;
    background: #f0f4ff;
    padding: 8px 10px;
    margin: 8px 0;
    border-radius: 4px;
}
.metric {
    display: inline-block;
    margin-right: 18px;
    margin-bottom: 4px;
    vertical-align: top;
}
.metric .label {
    font-size: 7px;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
.metric .value {
    font-size: 13px;
    font-weight: bold;
    color: #1a1a2e;
}

/* Tables */
table {
    border-collapse: collapse;
    width: 100%;
    margin: 4px 0 10px 0;
}
th {
    background: #0f3460;
    color: #fff;
    padding: 4px 5px;
    text-align: left;
    font-size: 7.5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
td {
    border-bottom: 1px solid #e0e0e0;
    padding: 3px 5px;
    font-size: 8px;
}
tr:nth-child(even) {
    background: #f8f9fc;
}
tr:hover {
    background: #eef1f8;
}

/* PnL styling */
.profit { color: #1b8a2f; font-weight: bold; }
.loss { color: #c0392b; font-weight: bold; }
.neutral { color: #666; }

/* Status badges */
.badge-open {
    background: #3498db;
    color: white;
    padding: 1px 4px;
    border-radius: 2px;
    font-size: 7px;
    font-weight: bold;
}
.badge-closed {
    background: #2ecc71;
    color: white;
    padding: 1px 4px;
    border-radius: 2px;
    font-size: 7px;
    font-weight: bold;
}
.badge-stopped {
    background: #e74c3c;
    color: white;
    padding: 1px 4px;
    border-radius: 2px;
    font-size: 7px;
    font-weight: bold;
}

/* Footer */
.footer {
    margin-top: 16px;
    padding-top: 6px;
    border-top: 1px solid #ccc;
    text-align: center;
    font-size: 7px;
    color: #999;
}
.footer .branding {
    font-weight: bold;
    color: #0f3460;
}

/* Breakdown tables */
.breakdown-table th {
    font-size: 7px;
}
.breakdown-table td {
    font-size: 7.5px;
}
"""


def generate_html(trades: List[Trade], analytics: dict) -> str:
    """Generate institutional-quality HTML for PDF conversion."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    date_str = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")

    # Determine date range
    trade_times = [t.entry_time for t in trades if t.entry_time]
    first_trade = min(trade_times).strftime("%Y-%m-%d %H:%M") if trade_times else "N/A"
    last_trade = max(trade_times).strftime("%Y-%m-%d %H:%M") if trade_times else "N/A"

    a = analytics  # shorthand

    pnl_cls = "profit" if a["total_pnl"] >= 0 else "loss"
    wr_cls = "profit" if a["win_rate"] >= 50 else ("loss" if a["win_rate"] < 30 else "neutral")
    sharpe_cls = "profit" if a["sharpe"] > 1.0 else ("loss" if a["sharpe"] < 0 else "neutral")

    html = f"""<html><head><style>{_CSS}</style></head><body>

    <!-- HEADER -->
    <div class="header">
        <h1>AEGIS V2 &mdash; Trade Journal</h1>
        <span class="subtitle">{date_str} &nbsp;|&nbsp; Generated: {now_str} &nbsp;|&nbsp; Session: {first_trade} to {last_trade}</span>
        <br/>
        <span class="equity-badge">Equity: &pound;{a['equity']:,.2f}</span>
        <span class="subtitle">&nbsp;|&nbsp; High Water: &pound;{a['high_water']:,.2f}</span>
    </div>

    <!-- EXECUTIVE SUMMARY -->
    <h2>Executive Summary</h2>
    <div class="summary-grid">
        <div class="metric">
            <div class="label">Total Trades</div>
            <div class="value">{a['total_trades']}</div>
        </div>
        <div class="metric">
            <div class="label">Open</div>
            <div class="value">{a['open_trades']}</div>
        </div>
        <div class="metric">
            <div class="label">Closed</div>
            <div class="value">{a['closed_trades']}</div>
        </div>
        <div class="metric">
            <div class="label">Win Rate</div>
            <div class="value {wr_cls}">{a['win_rate']:.1f}%</div>
        </div>
        <div class="metric">
            <div class="label">Total PnL</div>
            <div class="value {pnl_cls}">&pound;{a['total_pnl']:+,.2f}</div>
        </div>
        <div class="metric">
            <div class="label">Profit Factor</div>
            <div class="value">{a['profit_factor']:.2f}x</div>
        </div>
        <div class="metric">
            <div class="label">Sharpe (Ann.)</div>
            <div class="value {sharpe_cls}">{a['sharpe']:.2f}</div>
        </div>
        <div class="metric">
            <div class="label">Max Drawdown</div>
            <div class="value loss">&pound;{a['max_drawdown']:,.2f}</div>
        </div>
        <div class="metric">
            <div class="label">Avg Hold Time</div>
            <div class="value">{_fmt_hold(a['avg_hold_secs'])}</div>
        </div>
        <div class="metric">
            <div class="label">Total Notional</div>
            <div class="value">&pound;{a['total_notional']:,.0f}</div>
        </div>
        <div class="metric">
            <div class="label">Avg Win</div>
            <div class="value profit">&pound;{a['avg_win']:.2f}</div>
        </div>
        <div class="metric">
            <div class="label">Avg Loss</div>
            <div class="value loss">&pound;{a['avg_loss']:.2f}</div>
        </div>
    </div>

    <!-- TRADE TABLE -->
    <h2>Trade Log ({a['total_trades']} Trades)</h2>
    <table>
        <tr>
            <th>Date/Time</th>
            <th>Symbol</th>
            <th>Dir</th>
            <th>Entry (Native)</th>
            <th>Entry (GBP)</th>
            <th>Qty</th>
            <th>Notional (GBP)</th>
            <th>Exit Price</th>
            <th>Exit Time</th>
            <th>Hold</th>
            <th>PnL (&pound;)</th>
            <th>PnL (%)</th>
            <th>Status</th>
        </tr>
    """

    for t in trades:
        entry_native = f"{t.entry_price_native:,.4f} {t.currency}" if t.entry_price_native > 0 else "-"
        entry_gbp = f"&pound;{t.entry_price_gbp:,.4f}" if t.entry_price_gbp > 0 else "-"
        exit_price = f"{t.exit_price_native:,.4f} {t.currency}" if t.exit_price_native > 0 else "-"
        exit_time_str = _fmt_dt(t.exit_time) if t.exit_time else "-"
        hold_str = t.hold_duration_str

        if t.status == "CLOSED":
            pnl_html = _fmt_pnl(t.pnl_gbp)
            pnl_pct_html = _fmt_pct(t.pnl_pct) if t.pnl_pct != 0 else "-"
            badge = '<span class="badge-closed">CLOSED</span>'
        elif t.status == "STOPPED":
            pnl_html = _fmt_pnl(t.pnl_gbp)
            pnl_pct_html = _fmt_pct(t.pnl_pct) if t.pnl_pct != 0 else "-"
            badge = '<span class="badge-stopped">STOPPED</span>'
        else:
            pnl_html = '<span class="neutral">-</span>'
            pnl_pct_html = '<span class="neutral">-</span>'
            badge = '<span class="badge-open">OPEN</span>'

        qty_str = f"{t.qty:,}" if t.qty > 0 else "-"
        notional_str = f"&pound;{t.notional_gbp:,.2f}" if t.notional_gbp > 0 else "-"

        html += f"""
        <tr>
            <td>{_fmt_dt(t.entry_time)}</td>
            <td><b>{t.symbol}</b></td>
            <td>{t.direction}</td>
            <td>{entry_native}</td>
            <td>{entry_gbp}</td>
            <td>{qty_str}</td>
            <td>{notional_str}</td>
            <td>{exit_price}</td>
            <td>{exit_time_str}</td>
            <td>{hold_str}</td>
            <td>{pnl_html}</td>
            <td>{pnl_pct_html}</td>
            <td>{badge}</td>
        </tr>
        """

    html += "</table>"

    # ------------------------------------------------------------------
    # EXCHANGE BREAKDOWN
    # ------------------------------------------------------------------
    exchange_stats: Dict[str, dict] = defaultdict(lambda: {
        "count": 0, "notional": 0.0, "pnl": 0.0, "winners": 0, "losers": 0
    })
    for t in trades:
        ex = t.exchange or "OTHER"
        exchange_stats[ex]["count"] += 1
        exchange_stats[ex]["notional"] += t.notional_gbp
        if t.status == "CLOSED":
            exchange_stats[ex]["pnl"] += t.pnl_gbp
            if t.pnl_gbp > 0:
                exchange_stats[ex]["winners"] += 1
            else:
                exchange_stats[ex]["losers"] += 1

    html += """
    <h2>Exchange Breakdown</h2>
    <table class="breakdown-table">
        <tr>
            <th>Exchange</th>
            <th>Trades</th>
            <th>Notional (GBP)</th>
            <th>Realized PnL</th>
            <th>Winners</th>
            <th>Losers</th>
            <th>Win Rate</th>
        </tr>
    """
    for ex, stats in sorted(exchange_stats.items(), key=lambda x: -x[1]["count"]):
        total_closed = stats["winners"] + stats["losers"]
        wr = (stats["winners"] / total_closed * 100) if total_closed > 0 else 0.0
        html += f"""
        <tr>
            <td><b>{ex}</b></td>
            <td>{stats['count']}</td>
            <td>&pound;{stats['notional']:,.2f}</td>
            <td>{_fmt_pnl(stats['pnl'])}</td>
            <td>{stats['winners']}</td>
            <td>{stats['losers']}</td>
            <td>{wr:.0f}%</td>
        </tr>
        """
    html += "</table>"

    # ------------------------------------------------------------------
    # TIMING ANALYSIS
    # ------------------------------------------------------------------
    hour_stats: Dict[int, dict] = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for t in trades:
        if t.entry_time:
            hr = t.entry_time.hour
            hour_stats[hr]["count"] += 1
            if t.status == "CLOSED":
                hour_stats[hr]["pnl"] += t.pnl_gbp

    if hour_stats:
        html += """
        <h2>Timing Analysis (Entries by Hour, UTC)</h2>
        <table class="breakdown-table">
            <tr>
                <th>Hour (UTC)</th>
                <th>Trades</th>
                <th>Realized PnL</th>
                <th>Bar</th>
            </tr>
        """
        max_count = max(s["count"] for s in hour_stats.values()) if hour_stats else 1
        for hr in sorted(hour_stats.keys()):
            s = hour_stats[hr]
            bar_width = int((s["count"] / max_count) * 60)
            bar_color = "#2ecc71" if s["pnl"] >= 0 else "#e74c3c"
            bar_html = f'<div style="background:{bar_color}; width:{bar_width}px; height:8px; display:inline-block;"></div>'
            html += f"""
            <tr>
                <td><b>{hr:02d}:00</b></td>
                <td>{s['count']}</td>
                <td>{_fmt_pnl(s['pnl'])}</td>
                <td>{bar_html}</td>
            </tr>
            """
        html += "</table>"

    # ------------------------------------------------------------------
    # STRATEGY BREAKDOWN
    # ------------------------------------------------------------------
    strat_stats: Dict[str, dict] = defaultdict(lambda: {
        "count": 0, "pnl": 0.0, "notional": 0.0
    })
    for t in trades:
        s = t.strategy or "Unknown"
        strat_stats[s]["count"] += 1
        strat_stats[s]["notional"] += t.notional_gbp
        if t.status == "CLOSED":
            strat_stats[s]["pnl"] += t.pnl_gbp

    if strat_stats:
        html += """
        <h2>Strategy Breakdown</h2>
        <table class="breakdown-table">
            <tr>
                <th>Strategy</th>
                <th>Trades</th>
                <th>Notional (GBP)</th>
                <th>Realized PnL</th>
            </tr>
        """
        for strat, s in sorted(strat_stats.items(), key=lambda x: -x[1]["count"]):
            html += f"""
            <tr>
                <td><b>{strat}</b></td>
                <td>{s['count']}</td>
                <td>&pound;{s['notional']:,.2f}</td>
                <td>{_fmt_pnl(s['pnl'])}</td>
            </tr>
            """
        html += "</table>"

    # ------------------------------------------------------------------
    # SYMBOL PERFORMANCE
    # ------------------------------------------------------------------
    sym_stats: Dict[str, dict] = defaultdict(lambda: {
        "count": 0, "pnl": 0.0, "notional": 0.0, "currency": ""
    })
    for t in trades:
        sym_stats[t.symbol]["count"] += 1
        sym_stats[t.symbol]["notional"] += t.notional_gbp
        sym_stats[t.symbol]["currency"] = t.currency
        if t.status == "CLOSED":
            sym_stats[t.symbol]["pnl"] += t.pnl_gbp

    if sym_stats:
        html += """
        <h2>Symbol Performance</h2>
        <table class="breakdown-table">
            <tr>
                <th>Symbol</th>
                <th>Currency</th>
                <th>Trades</th>
                <th>Notional (GBP)</th>
                <th>Realized PnL</th>
            </tr>
        """
        for sym, s in sorted(sym_stats.items(), key=lambda x: -abs(x[1]["pnl"])):
            html += f"""
            <tr>
                <td><b>{sym}</b></td>
                <td>{s['currency']}</td>
                <td>{s['count']}</td>
                <td>&pound;{s['notional']:,.2f}</td>
                <td>{_fmt_pnl(s['pnl'])}</td>
            </tr>
            """
        html += "</table>"

    # ------------------------------------------------------------------
    # FOOTER
    # ------------------------------------------------------------------
    html += f"""
    <div class="footer">
        <span class="branding">AEGIS V2</span> &nbsp;|&nbsp;
        Simulation Mode &nbsp;|&nbsp;
        Runtime-verified &nbsp;|&nbsp;
        Generated {now_str} &nbsp;|&nbsp;
        PyMuPDF {fitz.__doc__.split()[1] if fitz.__doc__ else ''}
    </div>
    </body></html>
    """

    return html


# ---------------------------------------------------------------------------
# PDF Output
# ---------------------------------------------------------------------------

def html_to_pdf(html: str, output_path: str) -> None:
    """Convert HTML to PDF using PyMuPDF Story API (landscape A4)."""
    writer = fitz.DocumentWriter(output_path)
    story = fitz.Story(html)
    # Landscape A4 for wide trade tables
    page_rect = fitz.paper_rect("a4-l")
    content_rect = page_rect + (30, 30, -30, -30)  # 30pt margins

    more = True
    page_num = 0
    while more:
        dev = writer.begin_page(page_rect)
        more, _ = story.place(content_rect)
        story.draw(dev)
        writer.end_page()
        page_num += 1

    writer.close()
    return page_num


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("AEGIS V2 — Institutional Trade Journal Generator")
    print("=" * 60)

    # Step 1: Fetch data from EC2
    wal_data = fetch_wal_data()
    log_data = fetch_sim_logs()
    ticker_map = fetch_watchlist()

    # Step 2: Parse
    print("[4/4] Parsing and merging trades...")
    wal_orders, wal_closed, equity, high_water = parse_wal_events(wal_data, ticker_map)
    print(f"  WAL: {len(wal_orders)} unique orders, {len(wal_closed)} closed positions")

    log_trades, log_exits = parse_sim_logs(log_data)
    print(f"  SIM: {len(log_trades)} trades, {len(log_exits)} exits")

    # Step 3: Merge
    trades = merge_trades(wal_orders, wal_closed, log_trades, log_exits, ticker_map, equity)
    print(f"  Merged: {len(trades)} total trades")

    if not trades:
        print("NO TRADES FOUND. Cannot generate report.")
        return

    # Step 4: Analytics
    analytics = compute_analytics(trades, equity, high_water)
    print(f"  Win rate: {analytics['win_rate']:.1f}%, PnL: {analytics['total_pnl']:+.2f}")

    # Step 5: Generate HTML
    html = generate_html(trades, analytics)

    # Step 6: PDF
    output_dir = Path(__file__).resolve().parent.parent / "reports"
    output_dir.mkdir(exist_ok=True)
    pdf_path = output_dir / "trade_journal.pdf"

    pages = html_to_pdf(html, str(pdf_path))
    size_kb = pdf_path.stat().st_size / 1024
    print(f"\nPDF generated: {pdf_path}")
    print(f"  {pages} pages, {size_kb:.1f} KB")
    print("=" * 60)


if __name__ == "__main__":
    main()
