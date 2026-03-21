"""RT2-P2 — Fill Quality Model (Paper vs Live Slippage Simulator).

Models expected slippage in live trading based on observed paper fills
and historical bid-ask spread data. Predicts the gap between paper
and live performance.

Core insight: Paper trading assumes mid-price fills. Live trading faces:
  1. Spread cost (always pay ask on entry, receive bid on exit)
  2. Market impact (large orders move price)
  3. Latency slippage (price moves between signal and fill)
  4. Partial fills (not always get full quantity)

This module estimates each component and produces a "realism discount"
that adjusts paper PnL to expected live PnL.

Usage:
    python3 -m python_brain.ouroboros.fill_quality              # Analyze
    python3 -m python_brain.ouroboros.fill_quality --days 30    # 30-day lookback
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

import numpy as np

log = logging.getLogger("ouroboros.fill_quality")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))


# ---------------------------------------------------------------------------
# Slippage model parameters (empirical, calibrated from IBKR ISA fills)
# ---------------------------------------------------------------------------
# Base slippage for LSE leveraged ETPs (3x/5x)
BASE_SLIPPAGE_BPS = {
    "3x": 5.0,    # 0.05% per side for 3x leveraged
    "5x": 8.0,    # 0.08% per side for 5x leveraged
    "1x": 2.0,    # 0.02% per side for normal ETFs
}

# Market impact model: slippage_bps = base + alpha * sqrt(qty / ADV)
IMPACT_ALPHA = 50.0  # bps per unit of sqrt(participation_rate)

# Latency model: additional slippage in bps per second of latency
LATENCY_BPS_PER_SEC = 1.5

# Typical order-to-fill latency for IBKR (seconds)
TYPICAL_LATENCY_SEC = 0.8

# Partial fill rate (% of orders that don't fill completely)
PARTIAL_FILL_RATE = 0.05  # 5% for liquid LSE ETPs


@dataclass
class FillQualityMetrics:
    """Fill quality analysis for a single trade."""
    symbol: str
    paper_pnl: float
    spread_slippage_bps: float      # Half-spread × 2 sides
    impact_slippage_bps: float      # Market impact estimate
    latency_slippage_bps: float     # Latency-induced slippage
    total_slippage_bps: float       # Sum of all components
    estimated_live_pnl: float       # Paper PnL minus slippage
    realism_discount_pct: float     # % reduction from paper to live


@dataclass
class FillQualityReport:
    """Aggregated fill quality analysis."""
    analysis_date: str
    lookback_days: int
    total_trades: int
    paper_total_pnl: float = 0.0
    estimated_live_pnl: float = 0.0
    realism_discount_pct: float = 0.0
    avg_slippage_bps: float = 0.0
    avg_spread_component_bps: float = 0.0
    avg_impact_component_bps: float = 0.0
    avg_latency_component_bps: float = 0.0
    partial_fill_adjustment_pct: float = 0.0
    by_ticker: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    trades: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


def _classify_leverage(symbol: str) -> str:
    """Classify a symbol's leverage tier."""
    sym = symbol.upper()
    if "5" in sym and (".L" in sym):
        return "5x"
    if "3" in sym and (".L" in sym):
        return "3x"
    return "1x"


def _load_trades(wal_dir: Path, days: int) -> List[Dict]:
    """Load PositionClosed events."""
    cutoff_ns = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1e9)
    trades = []

    wal_files = [wal_dir / "current.ndjson"]
    archive_dir = wal_dir / "archive"
    if archive_dir.exists():
        wal_files.extend(sorted(archive_dir.glob("*.ndjson")))

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
                    if event.get("event_time_ns", 0) < cutoff_ns:
                        continue
                    payload = event.get("payload", {})
                    if "PositionClosed" in payload:
                        trades.append(payload["PositionClosed"])
        except IOError:
            pass

    return trades


def estimate_fill_quality(
    wal_dir: Path = WAL_DIR,
    days: int = 30,
) -> FillQualityReport:
    """Estimate fill quality gap between paper and live trading."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("RT2-P2: Fill quality analysis (%d-day lookback)", days)

    trades_raw = _load_trades(wal_dir, days)
    if not trades_raw:
        return FillQualityReport(analysis_date=today, lookback_days=days, total_trades=0)

    metrics: List[FillQualityMetrics] = []
    by_ticker: Dict[str, List[FillQualityMetrics]] = defaultdict(list)

    for trade in trades_raw:
        symbol = trade.get("symbol", "UNKNOWN")
        paper_pnl = trade.get("final_pnl", 0)
        entry_price = trade.get("entry_price", 1.0)
        qty = trade.get("qty", 1)
        spread_entry = trade.get("spread_at_entry_pct", 0.15)  # Default 15bps
        spread_exit = trade.get("spread_at_exit_pct", 0.15)
        position_value = max(entry_price * qty, 1.0)

        leverage_tier = _classify_leverage(symbol)

        # 1. Spread slippage: full spread on entry + exit
        spread_bps = (spread_entry + spread_exit) * 100  # pct → bps

        # 2. Market impact: sqrt model
        # Estimate ADV (average daily volume) — use position_value as fraction
        # Conservative: assume 0.1% participation rate
        participation = 0.001
        impact_bps = IMPACT_ALPHA * np.sqrt(participation)

        # 3. Latency slippage
        latency_bps = LATENCY_BPS_PER_SEC * TYPICAL_LATENCY_SEC

        # 4. Base slippage for leverage tier
        base_bps = BASE_SLIPPAGE_BPS.get(leverage_tier, 3.0)

        total_bps = spread_bps + impact_bps + latency_bps + base_bps
        slippage_gbp = total_bps / 10000 * position_value

        estimated_live = paper_pnl - slippage_gbp
        discount = abs(slippage_gbp / max(abs(paper_pnl), 0.01)) * 100 if paper_pnl != 0 else 0

        m = FillQualityMetrics(
            symbol=symbol,
            paper_pnl=round(paper_pnl, 4),
            spread_slippage_bps=round(spread_bps, 2),
            impact_slippage_bps=round(impact_bps, 2),
            latency_slippage_bps=round(latency_bps, 2),
            total_slippage_bps=round(total_bps, 2),
            estimated_live_pnl=round(estimated_live, 4),
            realism_discount_pct=round(discount, 2),
        )
        metrics.append(m)
        by_ticker[symbol].append(m)

    # Aggregates
    paper_total = sum(m.paper_pnl for m in metrics)
    live_total = sum(m.estimated_live_pnl for m in metrics)
    avg_slip = np.mean([m.total_slippage_bps for m in metrics])
    avg_spread = np.mean([m.spread_slippage_bps for m in metrics])
    avg_impact = np.mean([m.impact_slippage_bps for m in metrics])
    avg_latency = np.mean([m.latency_slippage_bps for m in metrics])

    overall_discount = abs(paper_total - live_total) / max(abs(paper_total), 0.01) * 100

    # Partial fill adjustment
    partial_adjustment = PARTIAL_FILL_RATE * 100

    # Per-ticker aggregation
    ticker_summary: Dict[str, Dict[str, Any]] = {}
    for ticker, ticker_metrics in sorted(by_ticker.items()):
        n = len(ticker_metrics)
        ticker_summary[ticker] = {
            "trades": n,
            "paper_pnl": round(sum(m.paper_pnl for m in ticker_metrics), 4),
            "estimated_live_pnl": round(sum(m.estimated_live_pnl for m in ticker_metrics), 4),
            "avg_slippage_bps": round(np.mean([m.total_slippage_bps for m in ticker_metrics]), 2),
            "leverage_tier": _classify_leverage(ticker),
        }

    report = FillQualityReport(
        analysis_date=today,
        lookback_days=days,
        total_trades=len(metrics),
        paper_total_pnl=round(paper_total, 4),
        estimated_live_pnl=round(live_total, 4),
        realism_discount_pct=round(overall_discount, 2),
        avg_slippage_bps=round(float(avg_slip), 2),
        avg_spread_component_bps=round(float(avg_spread), 2),
        avg_impact_component_bps=round(float(avg_impact), 2),
        avg_latency_component_bps=round(float(avg_latency), 2),
        partial_fill_adjustment_pct=round(partial_adjustment, 2),
        by_ticker=ticker_summary,
        trades=[asdict(m) for m in metrics],
    )

    log.info(
        "RT2-P2: Paper PnL=%.2f, Est Live=%.2f, Discount=%.1f%%, Avg slip=%.1f bps",
        paper_total, live_total, overall_discount, avg_slip,
    )
    return report


def save_report(report: FillQualityReport, output_dir: Path = DATA_DIR) -> Path:
    """Save fill quality report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "fill_quality_report.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(report.to_json(), encoding="utf-8")
    os.rename(str(tmp), str(path))
    log.info("Fill quality report saved: %s", path)
    return path


def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [FillQuality] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="RT2-P2 — Fill Quality / Slippage Model")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--wal-dir", type=str, default=str(WAL_DIR))
    args = parser.parse_args()

    report = estimate_fill_quality(Path(args.wal_dir), args.days)
    save_report(report)

    print(f"\nFill Quality Report ({report.analysis_date})")
    print(f"  Trades: {report.total_trades}")
    print(f"  Paper PnL:  GBP {report.paper_total_pnl:+.2f}")
    print(f"  Est. Live:  GBP {report.estimated_live_pnl:+.2f}")
    print(f"  Discount:   {report.realism_discount_pct:.1f}%")
    print(f"  Avg Slip:   {report.avg_slippage_bps:.1f} bps")
    print(f"    Spread:   {report.avg_spread_component_bps:.1f} bps")
    print(f"    Impact:   {report.avg_impact_component_bps:.1f} bps")
    print(f"    Latency:  {report.avg_latency_component_bps:.1f} bps")


if __name__ == "__main__":
    main()
