"""Book 19: Transaction Cost Analysis (TCA) Module.

Decomposes implementation shortfall into actionable cost components:
  1. Delay cost: price movement from decision to execution
  2. Spread cost: half spread at entry + half spread at exit
  3. Impact cost: estimated from volume vs ADV
  4. Opportunity cost: if only partially filled (default 0 for now)

Tracks per-strategy and per-instrument cost metrics. Provides spread benchmarks:
  - US large-cap: <3bps acceptable
  - LSE 3x ETP: <10bps acceptable
  - LSE inverse ETP: <15bps acceptable

Usage:
    python3 -m python_brain.execution.tca_analyzer              # Run nightly TCA
    python3 -m python_brain.execution.tca_analyzer --days 30    # 30-day lookback
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("execution.tca_analyzer")

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))

# ---------------------------------------------------------------------------
# Spread benchmarks (basis points)
# ---------------------------------------------------------------------------
SPREAD_BENCHMARKS = {
    "US_LARGE_CAP": 3.0,      # <3bps acceptable
    "LSE_3X_ETP": 10.0,       # <10bps acceptable
    "LSE_INVERSE_ETP": 15.0,  # <15bps acceptable
    "DEFAULT": 12.0,          # General threshold
}

# Impact cost constants (Almgren-Chriss simplified)
IMPACT_ALPHA = 50.0  # bps per unit of sqrt(participation_rate)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class TradeCostBreakdown:
    """Cost breakdown for a single trade."""
    symbol: str
    strategy: str
    entry_time_ns: int
    exit_time_ns: int
    pnl: float

    # Price points
    decision_price: float      # Signal price (entry_price as proxy)
    execution_price: float     # Actual fill price (entry_price)
    exit_price: float

    # Cost components (basis points)
    delay_cost_bps: float = 0.0       # Price movement from decision to execution
    spread_cost_bps: float = 0.0      # Half spread entry + half spread exit
    impact_cost_bps: float = 0.0      # Market impact from order size
    opportunity_cost_bps: float = 0.0 # Partial fill cost (future)

    # Total implementation shortfall
    total_shortfall_bps: float = 0.0

    # Benchmarks
    spread_benchmark_bps: float = 12.0
    spread_within_benchmark: bool = True

    # Cost drag on equity
    cost_drag_pct: float = 0.0  # Total costs as % of position value


@dataclass
class StrategyTCAMetrics:
    """Aggregated TCA metrics for a strategy."""
    strategy: str
    trade_count: int = 0
    avg_delay_cost_bps: float = 0.0
    avg_spread_cost_bps: float = 0.0
    avg_impact_cost_bps: float = 0.0
    avg_total_shortfall_bps: float = 0.0
    cost_as_pct_of_edge: float = 0.0  # Cost / avg_trade_pnl
    total_cost_drag_gbp: float = 0.0


@dataclass
class InstrumentTCAMetrics:
    """Aggregated TCA metrics for an instrument."""
    symbol: str
    trade_count: int = 0
    avg_spread_cost_bps: float = 0.0
    avg_impact_cost_bps: float = 0.0
    avg_total_shortfall_bps: float = 0.0
    spread_violations: int = 0  # Trades exceeding benchmark
    spread_benchmark_bps: float = 12.0


@dataclass
class TCAReport:
    """Comprehensive TCA report."""
    report_date: str
    lookback_days: int
    total_trades: int = 0

    # Portfolio-level metrics
    avg_delay_cost_bps: float = 0.0
    avg_spread_cost_bps: float = 0.0
    avg_impact_cost_bps: float = 0.0
    avg_total_shortfall_bps: float = 0.0

    # Cost drag
    total_cost_drag_gbp: float = 0.0
    cost_drag_pct_of_equity: float = 0.0

    # Quality metrics
    trades_within_benchmark: int = 0
    trades_outside_benchmark: int = 0
    benchmark_compliance_rate: float = 0.0

    # Per-strategy breakdown
    by_strategy: Dict[str, StrategyTCAMetrics] = field(default_factory=dict)

    # Per-instrument breakdown
    by_instrument: Dict[str, InstrumentTCAMetrics] = field(default_factory=dict)

    # Individual trade details (for deep dive)
    trade_details: List[TradeCostBreakdown] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


# ---------------------------------------------------------------------------
# TCA Analyzer
# ---------------------------------------------------------------------------
class TCAAnalyzer:
    """Transaction cost analysis engine."""

    def __init__(self, lookback_days: int = 7):
        self.lookback_days = lookback_days
        self.trades: List[Dict] = []

    def load_trades_from_wal(self) -> int:
        """Load completed trades from WAL files."""
        cutoff_ns = int((datetime.now(timezone.utc) - timedelta(days=self.lookback_days)).timestamp() * 1e9)
        trades = []

        wal_files = []
        current = WAL_DIR / "current.ndjson"
        if current.exists():
            wal_files.append(current)

        archive = WAL_DIR / "archive"
        if archive.exists():
            wal_files.extend(sorted(archive.glob("*.ndjson")))

        for wp in wal_files:
            if not wp.exists():
                continue
            try:
                with open(wp) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if ev.get("event_time_ns", 0) < cutoff_ns:
                            continue

                        payload = ev.get("payload", {})
                        if "PositionClosed" in payload:
                            pc = payload["PositionClosed"]
                            pc["_event_time_ns"] = ev.get("event_time_ns", 0)
                            trades.append(pc)
            except IOError:
                pass

        self.trades = trades
        log.info(f"Loaded {len(trades)} trades from WAL (last {self.lookback_days} days)")
        return len(trades)

    def _classify_instrument(self, symbol: str) -> str:
        """Classify instrument for spread benchmark selection."""
        sym = symbol.upper()

        # LSE inverse ETPs (check first - more specific)
        if any(x in sym for x in ["3USS", "3UKS", "SQQQ", "SPXS"]):
            return "LSE_INVERSE_ETP"

        # LSE 3x ETPs (check before US to catch QQQ3 before QQQ)
        if any(x in sym for x in ["3USL", "3UKL", "QQQ3", "SPY3", "TQQQ"]):
            return "LSE_3X_ETP"

        # US large-cap (plain symbols without leverage suffix)
        if any(x in sym for x in ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN"]):
            return "US_LARGE_CAP"

        return "DEFAULT"

    def _estimate_impact_cost(self, qty: int, adv: float, price: float, daily_vol: float = 0.02) -> float:
        """Estimate market impact using Almgren-Chriss square root model."""
        if adv <= 0 or qty <= 0 or price <= 0:
            return 0.0

        notional = qty * price
        participation = notional / adv
        impact = daily_vol * math.sqrt(participation) * IMPACT_ALPHA

        return round(impact, 2)

    def analyze_trade(self, trade: Dict) -> TradeCostBreakdown:
        """Decompose implementation shortfall for a single trade."""
        symbol = trade.get("symbol", "UNKNOWN")
        strategy = trade.get("strategy", "UNKNOWN")
        entry_price = trade.get("entry_price", 0.0)
        exit_price = trade.get("exit_price", 0.0)
        entry_time_ns = trade.get("entry_time_ns", 0)
        exit_time_ns = trade.get("exit_time_ns", 0)
        pnl = trade.get("final_pnl", 0.0)
        qty = trade.get("qty", 1)

        # Spread costs
        spread_entry_pct = trade.get("spread_at_entry_pct", 0.0)
        spread_exit_pct = trade.get("spread_at_exit_pct", 0.0)
        spread_cost_bps = (spread_entry_pct / 2 + spread_exit_pct / 2) * 10000

        # Delay cost (simplified: assume decision price = entry price for now)
        # In future, could extract signal decision price from RoutedOrder events
        decision_price = entry_price
        delay_cost_bps = 0.0  # Will enhance when signal decision price is available

        # Impact cost (simplified: no ADV data yet, use conservative estimate)
        # Assume ADV = 100K GBP for now (will enhance with actual ADV data)
        adv_estimate = 100000.0
        impact_cost_bps = self._estimate_impact_cost(qty, adv_estimate, entry_price)

        # Opportunity cost (future enhancement for partial fills)
        opportunity_cost_bps = 0.0

        # Total shortfall
        total_shortfall_bps = delay_cost_bps + spread_cost_bps + impact_cost_bps + opportunity_cost_bps

        # Benchmark classification
        instrument_class = self._classify_instrument(symbol)
        spread_benchmark_bps = SPREAD_BENCHMARKS.get(instrument_class, SPREAD_BENCHMARKS["DEFAULT"])
        spread_within_benchmark = spread_cost_bps <= spread_benchmark_bps

        # Cost drag
        position_value = qty * entry_price
        cost_drag_pct = (total_shortfall_bps / 10000) if position_value > 0 else 0.0

        return TradeCostBreakdown(
            symbol=symbol,
            strategy=strategy,
            entry_time_ns=entry_time_ns,
            exit_time_ns=exit_time_ns,
            pnl=pnl,
            decision_price=decision_price,
            execution_price=entry_price,
            exit_price=exit_price,
            delay_cost_bps=delay_cost_bps,
            spread_cost_bps=spread_cost_bps,
            impact_cost_bps=impact_cost_bps,
            opportunity_cost_bps=opportunity_cost_bps,
            total_shortfall_bps=total_shortfall_bps,
            spread_benchmark_bps=spread_benchmark_bps,
            spread_within_benchmark=spread_within_benchmark,
            cost_drag_pct=cost_drag_pct,
        )

    def generate_report(self) -> TCAReport:
        """Generate comprehensive TCA report."""
        report = TCAReport(
            report_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            lookback_days=self.lookback_days,
            total_trades=len(self.trades),
        )

        if not self.trades:
            log.warning("No trades to analyze")
            return report

        # Analyze each trade
        trade_breakdowns = [self.analyze_trade(t) for t in self.trades]
        report.trade_details = trade_breakdowns

        # Portfolio-level aggregates
        report.avg_delay_cost_bps = sum(tb.delay_cost_bps for tb in trade_breakdowns) / len(trade_breakdowns)
        report.avg_spread_cost_bps = sum(tb.spread_cost_bps for tb in trade_breakdowns) / len(trade_breakdowns)
        report.avg_impact_cost_bps = sum(tb.impact_cost_bps for tb in trade_breakdowns) / len(trade_breakdowns)
        report.avg_total_shortfall_bps = sum(tb.total_shortfall_bps for tb in trade_breakdowns) / len(trade_breakdowns)

        report.total_cost_drag_gbp = sum(tb.cost_drag_pct * tb.execution_price * 100 for tb in trade_breakdowns)
        report.trades_within_benchmark = sum(1 for tb in trade_breakdowns if tb.spread_within_benchmark)
        report.trades_outside_benchmark = len(trade_breakdowns) - report.trades_within_benchmark
        report.benchmark_compliance_rate = report.trades_within_benchmark / len(trade_breakdowns) * 100

        # Per-strategy aggregation
        strategy_data = defaultdict(lambda: {"trades": [], "pnl": 0.0})
        for tb in trade_breakdowns:
            strategy_data[tb.strategy]["trades"].append(tb)
            strategy_data[tb.strategy]["pnl"] += tb.pnl

        for strategy, data in strategy_data.items():
            trades = data["trades"]
            n = len(trades)
            total_pnl = data["pnl"]

            sm = StrategyTCAMetrics(
                strategy=strategy,
                trade_count=n,
                avg_delay_cost_bps=sum(t.delay_cost_bps for t in trades) / n,
                avg_spread_cost_bps=sum(t.spread_cost_bps for t in trades) / n,
                avg_impact_cost_bps=sum(t.impact_cost_bps for t in trades) / n,
                avg_total_shortfall_bps=sum(t.total_shortfall_bps for t in trades) / n,
                total_cost_drag_gbp=sum(t.cost_drag_pct * t.execution_price * 100 for t in trades),
            )

            # Cost as % of edge
            if total_pnl != 0:
                avg_shortfall_gbp = sm.avg_total_shortfall_bps / 10000 * sum(t.execution_price * 100 for t in trades) / n
                sm.cost_as_pct_of_edge = abs(avg_shortfall_gbp / (total_pnl / n)) * 100

            report.by_strategy[strategy] = sm

        # Per-instrument aggregation
        instrument_data = defaultdict(list)
        for tb in trade_breakdowns:
            instrument_data[tb.symbol].append(tb)

        for symbol, trades in instrument_data.items():
            n = len(trades)
            im = InstrumentTCAMetrics(
                symbol=symbol,
                trade_count=n,
                avg_spread_cost_bps=sum(t.spread_cost_bps for t in trades) / n,
                avg_impact_cost_bps=sum(t.impact_cost_bps for t in trades) / n,
                avg_total_shortfall_bps=sum(t.total_shortfall_bps for t in trades) / n,
                spread_violations=sum(1 for t in trades if not t.spread_within_benchmark),
                spread_benchmark_bps=trades[0].spread_benchmark_bps,
            )
            report.by_instrument[symbol] = im

        return report

    def save_report(self, report: TCAReport) -> Path:
        """Save TCA report to DATA_DIR."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        output_path = DATA_DIR / "tca_report.json"

        with open(output_path, "w") as f:
            f.write(report.to_json())

        log.info(f"TCA report saved to {output_path}")
        return output_path


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------
def run_tca_nightly(lookback_days: int = 7) -> Dict[str, Any]:
    """Run nightly TCA analysis. Returns summary dict for pipeline logging."""
    log.info(f"Starting TCA analysis (lookback: {lookback_days} days)")

    analyzer = TCAAnalyzer(lookback_days=lookback_days)
    trade_count = analyzer.load_trades_from_wal()

    if trade_count == 0:
        log.warning("No trades found in WAL")
        return {
            "status": "no_data",
            "trade_count": 0,
            "message": "No trades available for TCA analysis",
        }

    report = analyzer.generate_report()
    analyzer.save_report(report)

    # Return summary for pipeline
    return {
        "status": "success",
        "trade_count": report.total_trades,
        "avg_total_shortfall_bps": round(report.avg_total_shortfall_bps, 2),
        "avg_spread_cost_bps": round(report.avg_spread_cost_bps, 2),
        "avg_impact_cost_bps": round(report.avg_impact_cost_bps, 2),
        "benchmark_compliance_rate": round(report.benchmark_compliance_rate, 1),
        "total_cost_drag_gbp": round(report.total_cost_drag_gbp, 2),
        "strategies_analyzed": len(report.by_strategy),
        "instruments_analyzed": len(report.by_instrument),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [TCA] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Transaction Cost Analysis")
    parser.add_argument("--days", type=int, default=7, help="Lookback period in days")
    args = parser.parse_args()

    result = run_tca_nightly(lookback_days=args.days)

    print("\n" + "="*70)
    print("TRANSACTION COST ANALYSIS SUMMARY")
    print("="*70)
    print(json.dumps(result, indent=2))
    print("="*70 + "\n")
