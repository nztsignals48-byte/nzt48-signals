"""AEGIS V2 Terminal Dashboard — WAL tail + telemetry snapshot viewer.

Reads WAL (current.ndjson) from EC2 via SSH or local path.
Displays: equity, P&L, regime, positions, signals, trades, exchange status.
Zero impact on engine — read-only, separate process.

Usage:
    python dashboard/wal_dashboard.py --wal-path events/current.ndjson
    python dashboard/wal_dashboard.py --wal-host 3.230.44.22 --ssh-key ~/.ssh/nzt48-key.pem
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("ERROR: 'rich' library required. Install: pip install rich")
    sys.exit(1)


@dataclass
class Position:
    ticker_id: int
    entry_price: float
    qty: int
    current_price: float = 0.0
    stop_price: float = 0.0
    rung: int = 0
    pnl_pct: float = 0.0


@dataclass
class Signal:
    timestamp: str
    ticker_id: int
    approved: bool
    confidence: float = 0.0
    reason: str = ""
    strategy: str = ""


@dataclass
class Trade:
    trade_num: int
    ticker_id: int
    pnl: float
    pnl_pct: float = 0.0
    reason: str = ""
    duration: str = ""


@dataclass
class DashboardState:
    # Equity
    starting_equity: float = 10000.0
    current_equity: float = 10000.0
    daily_pnl: float = 0.0
    total_pnl: float = 0.0
    high_water: float = 10000.0

    # Stats
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    max_dd_pct: float = 0.0

    # System state
    regime: str = "NORMAL"
    mode: str = "DARK"
    python_alive: bool = True
    wal_depth: int = 0

    # Positions
    positions: Dict[int, Position] = field(default_factory=dict)

    # Recent signals and trades
    recent_signals: List[Signal] = field(default_factory=list)
    recent_trades: List[Trade] = field(default_factory=list)

    # Telemetry
    ticks_received: int = 0
    signals_generated: int = 0
    signals_vetoed: int = 0
    t2t_p50_ms: float = 0.0
    t2t_p99_ms: float = 0.0

    # P20: Performance telemetry
    daily_returns: List[float] = field(default_factory=list)
    trade_pnls: List[float] = field(default_factory=list)

    # P24: Advanced dashboard state
    session_mode: str = "DARK"
    last_regime_trigger: str = ""
    sector_exposure: Dict[str, float] = field(default_factory=dict)
    latency_p50_ms: float = 0.0
    latency_p99_ms: float = 0.0

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.wins / self.total_trades * 100.0

    @property
    def sharpe(self) -> float:
        """P20: Rolling 30-day Sharpe from daily returns."""
        if len(self.daily_returns) < 2:
            return 0.0
        recent = self.daily_returns[-30:]
        n = len(recent)
        mean_r = sum(recent) / n
        variance = sum((r - mean_r) ** 2 for r in recent) / max(n - 1, 1)
        std_r = variance ** 0.5
        if std_r < 1e-10:
            return 0.0
        return mean_r / std_r * (252 ** 0.5)  # Annualized

    @property
    def win_rate_30(self) -> float:
        """P20: Win rate over last 30 trades."""
        if len(self.trade_pnls) == 0:
            return 0.0
        recent = self.trade_pnls[-30:]
        wins = sum(1 for p in recent if p > 0)
        return wins / len(recent) * 100.0

    @property
    def win_rate_100(self) -> float:
        """P20: Win rate over last 100 trades."""
        if len(self.trade_pnls) == 0:
            return 0.0
        recent = self.trade_pnls[-100:]
        wins = sum(1 for p in recent if p > 0)
        return wins / len(recent) * 100.0


def parse_wal_event(line: str, state: DashboardState) -> None:
    """Parse a single WAL NDJSON line and update dashboard state."""
    try:
        event = json.loads(line.strip())
    except json.JSONDecodeError:
        return

    state.wal_depth += 1
    payload = event.get("payload", {})
    payload_type = payload.get("type", "")

    if payload_type == "SystemReady":
        state.mode = "READY"
    elif payload_type == "RoutedOrder":
        side = payload.get("side", "")
        if side != "Sell":
            sig = Signal(
                timestamp=event.get("event_id", "")[:19],
                ticker_id=payload.get("ticker_id", 0),
                approved=True,
                confidence=payload.get("confidence", 0.0),
                strategy=payload.get("strategy", ""),
            )
            state.recent_signals.append(sig)
            if len(state.recent_signals) > 10:
                state.recent_signals.pop(0)
    elif payload_type == "FillEvent":
        remaining = payload.get("remaining_qty", 1)
        if remaining == 0:
            tid = payload.get("ticker_id", 0)
            price = payload.get("price", 0.0)
            qty = payload.get("filled_qty", 0)
            state.positions[tid] = Position(
                ticker_id=tid,
                entry_price=price,
                qty=qty,
                current_price=price,
            )
    elif payload_type == "PositionClosed":
        tid = payload.get("ticker_id", 0)
        pnl = payload.get("final_pnl", 0.0)
        state.positions.pop(tid, None)
        state.total_trades += 1
        if pnl > 0:
            state.wins += 1
        else:
            state.losses += 1
        state.daily_pnl += pnl
        state.total_pnl += pnl
        state.current_equity += pnl

        trade = Trade(
            trade_num=state.total_trades,
            ticker_id=tid,
            pnl=pnl,
            reason=payload.get("reason", ""),
        )
        state.recent_trades.append(trade)
        if len(state.recent_trades) > 10:
            state.recent_trades.pop(0)
        # P20: Track PnL for WR trending
        state.trade_pnls.append(pnl)
    elif payload_type == "RiskStateChange":
        state.regime = payload.get("to", "Normal")
        state.last_regime_trigger = payload.get("trigger", "")
    elif payload_type == "StateSnapshot":
        state.current_equity = payload.get("equity", state.current_equity)
        state.high_water = payload.get("high_water", state.high_water)
    elif payload_type == "DailyReset":
        state.daily_pnl = 0.0
    elif payload_type == "ExitSignal":
        sig = Signal(
            timestamp=event.get("event_id", "")[:19],
            ticker_id=payload.get("ticker_id", 0),
            approved=False,
            reason=payload.get("reason", ""),
        )
        state.recent_signals.append(sig)
        if len(state.recent_signals) > 10:
            state.recent_signals.pop(0)


def build_equity_panel(state: DashboardState) -> Panel:
    """Build equity and P&L panel."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="bold")
    table.add_column("Value", justify="right")

    pnl_style = "green" if state.daily_pnl >= 0 else "red"
    total_style = "green" if state.total_pnl >= 0 else "red"
    dd = ((state.high_water - state.current_equity) / state.high_water * 100) if state.high_water > 0 else 0

    table.add_row("Starting", f"£{state.starting_equity:,.2f}")
    table.add_row("Current", f"£{state.current_equity:,.2f}")
    table.add_row("Daily P&L", Text(f"£{state.daily_pnl:+,.2f}", style=pnl_style))
    table.add_row("Total P&L", Text(f"£{state.total_pnl:+,.2f}", style=total_style))
    table.add_row("Win Rate", f"{state.win_rate:.1f}% ({state.wins}/{state.total_trades})")
    table.add_row("Max DD", f"-{dd:.1f}%")

    return Panel(table, title="EQUITY & P&L", border_style="green")


def build_system_panel(state: DashboardState) -> Panel:
    """Build system state panel."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="bold")
    table.add_column("Value", justify="right")

    regime_style = {
        "Normal": "green",
        "Reduce": "yellow",
        "Flatten": "red",
        "Halt": "red bold",
    }.get(state.regime, "white")

    table.add_row("Regime", Text(state.regime, style=regime_style))
    table.add_row("Mode", state.mode)
    table.add_row("Ticks", f"{state.ticks_received:,}")
    table.add_row("Signals", f"{state.signals_generated}")
    table.add_row("Vetoes", f"{state.signals_vetoed}")
    table.add_row("Python", Text("ALIVE" if state.python_alive else "DEAD",
                                  style="green" if state.python_alive else "red"))
    table.add_row("WAL depth", f"{state.wal_depth:,}")

    return Panel(table, title="SYSTEM STATE", border_style="blue")


def build_positions_panel(state: DashboardState) -> Panel:
    """Build open positions panel."""
    if not state.positions:
        return Panel(Text("No open positions", style="dim"), title="OPEN POSITIONS", border_style="cyan")

    table = Table(box=None, padding=(0, 1))
    table.add_column("Ticker", style="bold")
    table.add_column("Side")
    table.add_column("Qty", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("P&L %", justify="right")

    for pos in state.positions.values():
        pnl_pct = (pos.current_price - pos.entry_price) / pos.entry_price * 100 if pos.entry_price > 0 else 0
        pnl_style = "green" if pnl_pct >= 0 else "red"
        table.add_row(
            f"T{pos.ticker_id}",
            "Long",
            str(pos.qty),
            f"£{pos.entry_price:.4f}",
            f"£{pos.current_price:.4f}",
            Text(f"{pnl_pct:+.2f}%", style=pnl_style),
        )

    return Panel(table, title="OPEN POSITIONS", border_style="cyan")


def build_signals_panel(state: DashboardState) -> Panel:
    """Build recent signals panel."""
    if not state.recent_signals:
        return Panel(Text("No signals yet", style="dim"), title="LAST 10 SIGNALS", border_style="yellow")

    table = Table(box=None, padding=(0, 1))
    table.add_column("Time")
    table.add_column("Ticker")
    table.add_column("Status")
    table.add_column("Detail")

    for sig in reversed(state.recent_signals[-10:]):
        status_style = "green" if sig.approved else "red"
        status = "APPROVED" if sig.approved else "VETOED"
        detail = sig.strategy if sig.approved else sig.reason
        table.add_row(
            sig.timestamp[-8:] if len(sig.timestamp) >= 8 else sig.timestamp,
            f"T{sig.ticker_id}",
            Text(status, style=status_style),
            detail[:40],
        )

    return Panel(table, title="LAST 10 SIGNALS", border_style="yellow")


def build_trades_panel(state: DashboardState) -> Panel:
    """Build recent trades panel."""
    if not state.recent_trades:
        return Panel(Text("No trades yet", style="dim"), title="LAST 10 TRADES", border_style="magenta")

    table = Table(box=None, padding=(0, 1))
    table.add_column("#", justify="right")
    table.add_column("Ticker")
    table.add_column("P&L", justify="right")
    table.add_column("Reason")

    for trade in reversed(state.recent_trades[-10:]):
        pnl_style = "green" if trade.pnl >= 0 else "red"
        table.add_row(
            str(trade.trade_num),
            f"T{trade.ticker_id}",
            Text(f"£{trade.pnl:+.2f}", style=pnl_style),
            trade.reason[:30],
        )

    return Panel(table, title="LAST 10 TRADES", border_style="magenta")


def build_performance_panel(state: DashboardState) -> Panel:
    """P20: Build performance telemetry panel."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="bold")
    table.add_column("Value", justify="right")

    sharpe = state.sharpe
    sharpe_style = "green" if sharpe > 0 else "red" if sharpe < 0 else "white"
    table.add_row("Sharpe (30d)", Text(f"{sharpe:.2f}", style=sharpe_style))
    table.add_row("WR (30 trades)", f"{state.win_rate_30:.1f}%")
    table.add_row("WR (100 trades)", f"{state.win_rate_100:.1f}%")
    table.add_row("Total trades", f"{state.total_trades}")

    # Sector allocation
    if state.sector_exposure:
        for sector, pct in sorted(state.sector_exposure.items()):
            bar_len = int(pct / 5)
            bar = "█" * bar_len
            table.add_row(f"  {sector}", Text(f"{pct:.0f}% {bar}", style="cyan"))

    return Panel(table, title="PERFORMANCE", border_style="green")


def build_latency_panel(state: DashboardState) -> Panel:
    """P24: Build latency and mode panel."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="bold")
    table.add_column("Value", justify="right")

    mode_styles = {
        "DARK": "dim", "MODE_A": "blue", "MODE_B": "green",
        "AUCTION": "yellow", "CARRY": "magenta",
    }
    mode_style = mode_styles.get(state.session_mode, "white")
    table.add_row("Mode", Text(state.session_mode, style=mode_style))
    table.add_row("T2T p50", f"{state.latency_p50_ms:.1f}ms")
    table.add_row("T2T p99", f"{state.latency_p99_ms:.1f}ms")

    if state.last_regime_trigger:
        table.add_row("Last trigger", state.last_regime_trigger[:30])

    return Panel(table, title="LATENCY & MODE", border_style="bright_blue")


def build_dashboard(state: DashboardState) -> Layout:
    """Build the full dashboard layout (P20/P24 extended)."""
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="top", size=10),
        Layout(name="positions", size=6),
        Layout(name="middle", size=10),
        Layout(name="bottom", size=14),
    )

    # Header with mode clock (P24)
    mode_text = f"AEGIS V2 Dashboard  ─  {state.session_mode}  ─  {state.regime}"
    layout["header"].update(
        Panel(
            Text(mode_text, style="bold white", justify="center"),
            border_style="bright_white",
        )
    )

    # Top: Equity + System
    layout["top"].split_row(
        Layout(build_equity_panel(state), name="equity"),
        Layout(build_system_panel(state), name="system"),
    )

    # Positions
    layout["positions"].update(build_positions_panel(state))

    # Middle: Performance + Latency (P20/P24)
    layout["middle"].split_row(
        Layout(build_performance_panel(state), name="performance"),
        Layout(build_latency_panel(state), name="latency"),
    )

    # Bottom: Signals + Trades
    layout["bottom"].split_row(
        Layout(build_signals_panel(state), name="signals"),
        Layout(build_trades_panel(state), name="trades"),
    )

    return layout


def tail_wal_local(wal_path: Path, state: DashboardState, console: Console) -> None:
    """Tail a local WAL file and update dashboard."""
    # First: replay existing events
    if wal_path.exists():
        with open(wal_path) as f:
            for line in f:
                parse_wal_event(line, state)

    # Then: live tail
    with Live(build_dashboard(state), console=console, refresh_per_second=2) as live:
        last_size = wal_path.stat().st_size if wal_path.exists() else 0
        while True:
            try:
                time.sleep(0.5)
                if wal_path.exists():
                    current_size = wal_path.stat().st_size
                    if current_size > last_size:
                        with open(wal_path) as f:
                            f.seek(last_size)
                            for line in f:
                                parse_wal_event(line, state)
                        last_size = current_size
                live.update(build_dashboard(state))
            except KeyboardInterrupt:
                break


def tail_wal_ssh(host: str, ssh_key: str, state: DashboardState, console: Console) -> None:
    """Tail WAL from EC2 via SSH and update dashboard."""
    remote_path = "/home/ubuntu/nzt48-aegis-v2/events/current.ndjson"
    cmd = [
        "ssh", "-i", ssh_key, "-o", "StrictHostKeyChecking=no",
        f"ubuntu@{host}", f"tail -f -n +1 {remote_path}"
    ]

    console.print(f"Connecting to {host}...", style="dim")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    with Live(build_dashboard(state), console=console, refresh_per_second=2) as live:
        try:
            for line in proc.stdout or []:
                parse_wal_event(line, state)
                live.update(build_dashboard(state))
        except KeyboardInterrupt:
            proc.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description="AEGIS V2 Terminal Dashboard")
    parser.add_argument("--wal-path", type=str, help="Local path to WAL ndjson file")
    parser.add_argument("--wal-host", type=str, help="EC2 host for SSH WAL tail")
    parser.add_argument("--ssh-key", type=str, default=os.path.expanduser("~/.ssh/nzt48-key.pem"),
                        help="SSH key path")
    args = parser.parse_args()

    console = Console()
    state = DashboardState()

    if args.wal_path:
        tail_wal_local(Path(args.wal_path), state, console)
    elif args.wal_host:
        tail_wal_ssh(args.wal_host, args.ssh_key, state, console)
    else:
        console.print("ERROR: Specify --wal-path or --wal-host", style="red")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
