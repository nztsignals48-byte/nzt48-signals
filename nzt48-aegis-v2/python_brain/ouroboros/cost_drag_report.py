"""RT3 — Cost Drag Daily Reporting.

Daily cost accounting report showing friction impact on profitability.
Computes spread cost, commission drag, and friction-adjusted PnL per trade,
per ticker, per session, and per regime.

Integrated into:
  - Claude morning briefing (N6b) — summary line
  - Google Sheets (Spread_Execution tab via N4a) — per-trade detail
  - Telegram daily digest — cost warning if friction > 1%

QUARANTINE: Read-only. Never writes to WAL, config, or live trading parameters.

Usage:
    python3 -m python_brain.ouroboros.cost_drag_report                   # Print report
    python3 -m python_brain.ouroboros.cost_drag_report --send-telegram   # Send summary
    python3 -m python_brain.ouroboros.cost_drag_report --days 7          # 7-day lookback
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("ouroboros.cost_drag")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))

# Alert thresholds
FRICTION_WARNING_PCT = 1.0   # Warn if daily friction > 1% of equity
FRICTION_CRITICAL_PCT = 1.5  # Critical if daily friction > 1.5% of equity
SPREAD_VICTIM_WARN = 3       # Warn if > 3 spread victims in a day
STARTING_EQUITY = 10_000.0   # For % calculations


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class TradeCostBreakdown:
    """Cost breakdown for a single trade."""
    symbol: str
    timestamp: str
    gross_pnl: float
    net_pnl: float
    commission: float
    spread_entry_pct: float
    spread_exit_pct: float
    spread_cost_gbp: float
    total_friction: float         # commission + spread_cost_gbp
    friction_pct: float           # total_friction / position_value * 100
    trade_class: str
    session_phase: str
    regime: str
    strategy: str
    is_spread_victim: bool


@dataclass
class CostDragSummary:
    """Aggregated daily cost drag report."""
    date: str
    lookback_days: int
    total_trades: int
    total_gross_pnl: float = 0.0
    total_net_pnl: float = 0.0
    total_commission: float = 0.0
    total_spread_cost: float = 0.0
    total_friction: float = 0.0
    friction_pct_of_equity: float = 0.0
    friction_pct_of_gross: float = 0.0
    spread_victim_count: int = 0
    avg_spread_entry: float = 0.0
    avg_spread_exit: float = 0.0

    # Breakdown by dimension
    by_ticker: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_session: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_regime: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_strategy: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Nightly_v6 decision triggers
    friction_warning: bool = False
    friction_critical: bool = False
    spread_victim_warning: bool = False
    recommended_action: str = ""

    # Individual trade costs
    trades: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


# ---------------------------------------------------------------------------
# WAL loading
# ---------------------------------------------------------------------------
def _load_closed_trades(wal_dir: Path, days: int) -> List[Dict]:
    """Load PositionClosed events from WAL within lookback period."""
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ns = int(cutoff_dt.timestamp() * 1e9)

    trades: List[Dict] = []

    # Scan all WAL files (current + archive)
    wal_files = [wal_dir / "current.ndjson"]
    archive_dir = wal_dir / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.ndjson")):
            wal_files.append(f)
    for f in sorted(wal_dir.glob("*.ndjson")):
        if f.name != "current.ndjson" and f not in wal_files:
            wal_files.append(f)

    for wal_path in wal_files:
        if not wal_path.exists():
            continue
        try:
            with open(wal_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event_time = event.get("event_time_ns", 0)
                    if event_time < cutoff_ns:
                        continue

                    payload = event.get("payload", {})
                    if "PositionClosed" in payload:
                        pc = payload["PositionClosed"]
                        pc["_event_time_ns"] = event_time
                        trades.append(pc)
        except IOError as e:
            log.warning("Error reading WAL %s: %s", wal_path, e)

    return trades


# ---------------------------------------------------------------------------
# Cost computation
# ---------------------------------------------------------------------------
def _compute_trade_cost(trade: Dict) -> TradeCostBreakdown:
    """Compute full cost breakdown for a single trade."""
    symbol = trade.get("symbol", f"TID_{trade.get('ticker_id', '?')}")
    entry_price = trade.get("entry_price", 0)
    qty = trade.get("qty", 0)
    position_value = max(entry_price * qty, 1.0)

    gross_pnl = trade.get("gross_pnl", trade.get("final_pnl", 0))
    net_pnl = trade.get("final_pnl", 0)
    commission = trade.get("total_commission", 0)
    spread_entry = trade.get("spread_at_entry_pct", 0)
    spread_exit = trade.get("spread_at_exit_pct", 0)

    spread_cost_gbp = (spread_entry + spread_exit) / 100.0 * position_value
    total_friction = commission + spread_cost_gbp
    friction_pct = total_friction / position_value * 100 if position_value > 0 else 0

    trade_class = trade.get("trade_class", "")
    is_spread_victim = trade_class == "spread_victim" or (
        net_pnl < 0 and abs(net_pnl) < 2 * spread_cost_gbp
    )

    # Timestamp
    event_ns = trade.get("_event_time_ns", 0)
    if event_ns:
        try:
            ts = datetime.fromtimestamp(event_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OSError, ValueError):
            ts = ""
    else:
        ts = ""

    return TradeCostBreakdown(
        symbol=symbol,
        timestamp=ts,
        gross_pnl=round(gross_pnl, 4),
        net_pnl=round(net_pnl, 4),
        commission=round(commission, 4),
        spread_entry_pct=round(spread_entry, 4),
        spread_exit_pct=round(spread_exit, 4),
        spread_cost_gbp=round(spread_cost_gbp, 4),
        total_friction=round(total_friction, 4),
        friction_pct=round(friction_pct, 4),
        trade_class=trade_class,
        session_phase=trade.get("entry_session_phase", ""),
        regime=trade.get("regime_at_entry", ""),
        strategy=trade.get("strategy", ""),
        is_spread_victim=is_spread_victim,
    )


def _aggregate_by_dimension(
    costs: List[TradeCostBreakdown],
    key_fn,
) -> Dict[str, Dict[str, Any]]:
    """Aggregate cost stats by a dimension (ticker, session, regime, strategy)."""
    groups: Dict[str, List[TradeCostBreakdown]] = defaultdict(list)
    for c in costs:
        k = key_fn(c) or "unknown"
        groups[k].append(c)

    result: Dict[str, Dict[str, Any]] = {}
    for key, group in sorted(groups.items()):
        n = len(group)
        total_friction = sum(c.total_friction for c in group)
        total_gross = sum(c.gross_pnl for c in group)
        total_net = sum(c.net_pnl for c in group)
        spread_victims = sum(1 for c in group if c.is_spread_victim)
        avg_friction_pct = sum(c.friction_pct for c in group) / n if n > 0 else 0

        result[key] = {
            "trades": n,
            "total_friction": round(total_friction, 4),
            "total_gross_pnl": round(total_gross, 4),
            "total_net_pnl": round(total_net, 4),
            "avg_friction_pct": round(avg_friction_pct, 4),
            "spread_victims": spread_victims,
            "friction_of_gross_pct": round(
                total_friction / max(abs(total_gross), 0.01) * 100, 1
            ),
        }

    return result


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------
def generate_cost_drag_report(
    wal_dir: Path = WAL_DIR,
    days: int = 1,
    equity: float = STARTING_EQUITY,
) -> CostDragSummary:
    """Generate daily cost drag report.

    Args:
        wal_dir: WAL events directory
        days: Lookback period (1 = yesterday only)
        equity: Current equity for % calculations

    Returns:
        CostDragSummary with full cost accounting.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("RT3: Generating cost drag report (%d-day lookback)", days)

    trades_raw = _load_closed_trades(wal_dir, days)
    log.info("Loaded %d PositionClosed events", len(trades_raw))

    if not trades_raw:
        return CostDragSummary(date=today, lookback_days=days, total_trades=0)

    # Compute per-trade costs
    costs = [_compute_trade_cost(t) for t in trades_raw]

    # Aggregates
    total_gross = sum(c.gross_pnl for c in costs)
    total_net = sum(c.net_pnl for c in costs)
    total_commission = sum(c.commission for c in costs)
    total_spread = sum(c.spread_cost_gbp for c in costs)
    total_friction = sum(c.total_friction for c in costs)
    spread_victims = sum(1 for c in costs if c.is_spread_victim)
    avg_spread_entry = sum(c.spread_entry_pct for c in costs) / len(costs)
    avg_spread_exit = sum(c.spread_exit_pct for c in costs) / len(costs)

    friction_of_equity = total_friction / max(equity, 1.0) * 100
    friction_of_gross = total_friction / max(abs(total_gross), 0.01) * 100

    # Breakdowns
    by_ticker = _aggregate_by_dimension(costs, lambda c: c.symbol)
    by_session = _aggregate_by_dimension(costs, lambda c: c.session_phase)
    by_regime = _aggregate_by_dimension(costs, lambda c: c.regime)
    by_strategy = _aggregate_by_dimension(costs, lambda c: c.strategy)

    # Decision triggers
    friction_warning = friction_of_equity > FRICTION_WARNING_PCT
    friction_critical = friction_of_equity > FRICTION_CRITICAL_PCT
    spread_victim_warning = spread_victims >= SPREAD_VICTIM_WARN

    recommended_action = ""
    if friction_critical:
        recommended_action = "REDUCE daily_trade_limit to 1 (friction > 1.5% equity)"
    elif friction_warning and total_net < 0:
        recommended_action = "REVIEW: friction > 1% equity AND net negative — consider wider spread gates"
    elif spread_victim_warning:
        recommended_action = f"REVIEW: {spread_victims} spread victims — tighten spread_pct gate or avoid thin instruments"

    report = CostDragSummary(
        date=today,
        lookback_days=days,
        total_trades=len(costs),
        total_gross_pnl=round(total_gross, 4),
        total_net_pnl=round(total_net, 4),
        total_commission=round(total_commission, 4),
        total_spread_cost=round(total_spread, 4),
        total_friction=round(total_friction, 4),
        friction_pct_of_equity=round(friction_of_equity, 4),
        friction_pct_of_gross=round(friction_of_gross, 1),
        spread_victim_count=spread_victims,
        avg_spread_entry=round(avg_spread_entry, 4),
        avg_spread_exit=round(avg_spread_exit, 4),
        by_ticker=by_ticker,
        by_session=by_session,
        by_regime=by_regime,
        by_strategy=by_strategy,
        friction_warning=friction_warning,
        friction_critical=friction_critical,
        spread_victim_warning=spread_victim_warning,
        recommended_action=recommended_action,
        trades=[asdict(c) for c in costs],
    )

    log.info(
        "RT3: %d trades, friction=GBP %.2f (%.2f%% equity, %.1f%% of gross), %d spread victims",
        len(costs), total_friction, friction_of_equity, friction_of_gross, spread_victims,
    )

    return report


def save_cost_drag_report(report: CostDragSummary, output_dir: Path = DATA_DIR) -> Path:
    """Save cost drag report to JSON."""
    report_dir = output_dir / "cost_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    output_path = report_dir / f"{report.date}_cost_report.json"
    tmp = output_path.with_suffix(".json.tmp")
    try:
        tmp.write_text(report.to_json(), encoding="utf-8")
        os.rename(str(tmp), str(output_path))
        log.info("Cost report saved: %s", output_path)
        return output_path
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def format_telegram_summary(report: CostDragSummary) -> str:
    """Format cost drag report for Telegram."""
    lines = [
        f"<b>RT3 COST DRAG REPORT — {report.date}</b>",
        "",
    ]

    if report.total_trades == 0:
        lines.append("No trades in period.")
        return "\n".join(lines)

    # Summary
    icon = "\u26a0\ufe0f" if report.friction_warning else "\u2705"
    lines.append(f"{icon} <b>Friction:</b> GBP {report.total_friction:.2f} "
                 f"({report.friction_pct_of_equity:.2f}% equity)")
    lines.append(f"  Commission: GBP {report.total_commission:.2f}")
    lines.append(f"  Spread cost: GBP {report.total_spread_cost:.2f}")
    lines.append(f"  Avg spread: {report.avg_spread_entry:.2f}% entry, {report.avg_spread_exit:.2f}% exit")
    lines.append("")

    # P&L impact
    lines.append(f"\U0001f4b0 <b>P&L Impact:</b>")
    lines.append(f"  Gross: GBP {report.total_gross_pnl:+.2f}")
    lines.append(f"  Net:   GBP {report.total_net_pnl:+.2f}")
    lines.append(f"  Drag:  {report.friction_pct_of_gross:.1f}% of gross")
    lines.append("")

    # Spread victims
    if report.spread_victim_count > 0:
        lines.append(f"\U0001f534 <b>Spread Victims:</b> {report.spread_victim_count}/{report.total_trades}")

    # Worst tickers by friction
    if report.by_ticker:
        lines.append("")
        lines.append(f"\U0001f4ca <b>By Ticker:</b>")
        sorted_tickers = sorted(
            report.by_ticker.items(),
            key=lambda x: -x[1]["total_friction"],
        )
        for ticker, data in sorted_tickers[:5]:
            lines.append(
                f"  {ticker}: GBP {data['total_friction']:.2f} friction "
                f"({data['avg_friction_pct']:.2f}% avg, {data['spread_victims']} victims)"
            )

    # Recommended action
    if report.recommended_action:
        lines.append("")
        lines.append(f"\U0001f6a8 <b>Action:</b> {report.recommended_action}")

    return "\n".join(lines)


def send_telegram(report: CostDragSummary) -> bool:
    """Send cost drag summary via Telegram."""
    try:
        from python_brain.ouroboros.telegram_notify import send_message
        msg = format_telegram_summary(report)
        send_message(msg)
        log.info("Cost drag report sent via Telegram")
        return True
    except ImportError:
        log.warning("telegram_notify not available")
        return False
    except Exception as e:
        log.error("Telegram send failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [CostDrag] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="RT3 — Cost Drag Daily Reporting")
    parser.add_argument("--days", type=int, default=1, help="Lookback days (default: 1)")
    parser.add_argument("--equity", type=float, default=STARTING_EQUITY, help="Current equity")
    parser.add_argument("--send-telegram", action="store_true", help="Send via Telegram")
    parser.add_argument("--wal-dir", type=str, default=str(WAL_DIR))
    args = parser.parse_args()

    report = generate_cost_drag_report(
        wal_dir=Path(args.wal_dir),
        days=args.days,
        equity=args.equity,
    )

    save_cost_drag_report(report)

    # Print summary
    print(format_telegram_summary(report))

    if args.send_telegram:
        send_telegram(report)


if __name__ == "__main__":
    main()
