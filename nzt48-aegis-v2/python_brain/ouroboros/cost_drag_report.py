"""RT3 — Cost Drag Daily Reporting.

Daily cost accounting report showing friction impact on profitability.
Computes spread cost, commission drag, and friction-adjusted PnL per trade,
per ticker, per session, and per regime.

Q-095 Enhancement: Per-ticker cost breakdown, cost efficiency metrics,
actual-vs-model comparison table, and --detailed-json output mode.
Uses CostModel.from_toml() for all cost parameters (no hardcoded values).

Integrated into:
  - Claude morning briefing (N6b) — summary line
  - Google Sheets (Spread_Execution tab via N4a) — per-trade detail
  - Telegram daily digest — cost warning if friction > 1%

QUARANTINE: Read-only. Never writes to WAL, config, or live trading parameters.

Usage:
    python3 -m python_brain.ouroboros.cost_drag_report                   # Print report
    python3 -m python_brain.ouroboros.cost_drag_report --send-telegram   # Send summary
    python3 -m python_brain.ouroboros.cost_drag_report --days 7          # 7-day lookback
    python3 -m python_brain.ouroboros.cost_drag_report --detailed-json   # Q-095 per-ticker JSON
    python3 -m python_brain.ouroboros.cost_drag_report --detailed-json --config-dir /app/config
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from python_brain.ouroboros.cost_model import CostModel, total_round_trip_cost

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
# Q-095: Per-ticker cost detail and enhanced analysis dataclasses
# ---------------------------------------------------------------------------
@dataclass
class TickerCostDetail:
    """Per-ticker cost breakdown with edge analysis (Q-095)."""
    symbol: str
    trade_count: int
    avg_spread_at_entry_pct: float
    avg_spread_at_exit_pct: float
    total_commission: float
    total_spread_cost: float
    fx_conversion_cost: float        # Estimated FX cost for USD-denominated ETPs
    total_cost: float                # commission + spread + FX
    total_gross_pnl: float
    total_net_pnl: float
    cost_pct_of_gross: float         # total_cost / |gross_pnl| * 100
    gross_edge_pct: float            # avg gross_pnl / avg position_value * 100
    total_cost_pct: float            # total_cost / total position_value * 100
    cost_adjusted_edge_pct: float    # gross_edge_pct - total_cost_pct
    currency: str                    # "GBP", "USD", "EUR" from contracts.toml
    is_fx_instrument: bool           # True if currency != "GBP"


@dataclass
class CostEfficiencyMetrics:
    """Portfolio-level cost efficiency metrics (Q-095)."""
    cost_to_edge_ratio: float         # total_cost / total_gross_pnl (0-1, lower=better)
    break_even_spread_pct: float      # Max spread where avg trade remains profitable
    optimal_position_size_gbp: float  # Size that minimizes cost drag per unit edge
    avg_cost_per_trade_gbp: float
    avg_edge_per_trade_gbp: float
    cost_efficiency_score: float      # 0-100, higher = more efficient


@dataclass
class CostModelComparison:
    """Comparison of actual costs vs CostModel defaults (Q-095)."""
    symbol: str
    actual_avg_spread_pct: float
    model_default_spread_pct: float   # spread_veto_pct from CostModel
    spread_ratio: float               # actual / model (>2.0 = flagged)
    spread_flag: bool                 # True if actual > 2x model default
    actual_fx_cost_pct: float         # Estimated actual FX drag
    model_fx_cost_pct: float          # CostModel.fx_conversion_pct
    fx_flag: bool                     # True if actual FX > expected
    recommended_spread_gate: float    # Suggested spread_veto_pct for this ticker
    recommended_action: str


@dataclass
class DetailedCostAnalysis:
    """Full Q-095 enhanced cost analysis output."""
    date: str
    lookback_days: int
    cost_model_source: str            # "config.toml" or "defaults"

    # Per-ticker breakdowns
    ticker_details: List[TickerCostDetail] = field(default_factory=list)

    # Cost efficiency metrics
    efficiency: Optional[CostEfficiencyMetrics] = None

    # Comparison table (actual vs model)
    comparisons: List[CostModelComparison] = field(default_factory=list)
    flagged_tickers: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


# ---------------------------------------------------------------------------
# Contracts.toml loader for currency lookup
# ---------------------------------------------------------------------------
def _load_contracts_currency_map(config_dir: Optional[Path] = None) -> Dict[str, str]:
    """Load symbol -> currency mapping from contracts.toml.

    Returns dict like {"QQQ3.L": "USD", "3LUS.L": "GBP", ...}.
    """
    cfg_dir = config_dir or Path(os.environ.get(
        "AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"
    ))
    toml_path = cfg_dir / "contracts.toml"
    if not toml_path.exists():
        log.warning("contracts.toml not found at %s — FX costs will be estimated", toml_path)
        return {}

    try:
        import tomli
    except ImportError:
        try:
            import tomllib as tomli  # type: ignore[no-redef]
        except ImportError:
            log.warning("No TOML parser available — FX costs will be estimated")
            return {}

    try:
        with open(toml_path, "rb") as f:
            data = tomli.load(f)
    except Exception as e:
        log.warning("Failed to parse contracts.toml: %s", e)
        return {}

    result: Dict[str, str] = {}
    for contract in data.get("contracts", []):
        symbol = contract.get("symbol", "")
        if symbol:
            result[symbol] = contract.get("currency", "USD")
    return result


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


# ---------------------------------------------------------------------------
# Q-095: Enhanced cost analysis — per-ticker, efficiency metrics, comparison
# ---------------------------------------------------------------------------
def _compute_ticker_details(
    costs: List[TradeCostBreakdown],
    trades_raw: List[Dict],
    currency_map: Dict[str, str],
    cost_model: CostModel,
) -> List[TickerCostDetail]:
    """Compute per-ticker cost breakdown with FX and edge analysis.

    Groups trades by symbol, computes:
      - Average spread at entry/exit
      - Total commissions
      - FX conversion cost estimate (for non-GBP instruments)
      - Cost as % of gross PnL
      - Cost-adjusted edge (gross_edge - total_cost_pct)
    """
    # Group costs and raw trades by symbol
    by_sym: Dict[str, List[TradeCostBreakdown]] = defaultdict(list)
    raw_by_sym: Dict[str, List[Dict]] = defaultdict(list)
    for c in costs:
        by_sym[c.symbol].append(c)
    for t in trades_raw:
        sym = t.get("symbol", f"TID_{t.get('ticker_id', '?')}")
        raw_by_sym[sym].append(t)

    details: List[TickerCostDetail] = []
    for symbol in sorted(by_sym.keys()):
        group = by_sym[symbol]
        raw_group = raw_by_sym.get(symbol, [])
        n = len(group)

        avg_spread_entry = sum(c.spread_entry_pct for c in group) / n
        avg_spread_exit = sum(c.spread_exit_pct for c in group) / n
        total_comm = sum(c.commission for c in group)
        total_spread = sum(c.spread_cost_gbp for c in group)
        total_gross = sum(c.gross_pnl for c in group)
        total_net = sum(c.net_pnl for c in group)

        # Position value from raw trades
        total_position_value = 0.0
        for t in raw_group:
            entry_p = t.get("entry_price", 0)
            qty = t.get("qty", 0)
            total_position_value += max(entry_p * qty, 1.0)

        # Currency & FX cost
        currency = currency_map.get(symbol, "USD" if ".L" in symbol else "GBP")
        is_fx = currency != "GBP"
        fx_cost = 0.0
        if is_fx:
            # 2x FX conversion (buy in foreign currency + sell back)
            fx_cost = total_position_value * cost_model.fx_conversion_pct * 2

        total_cost = total_comm + total_spread + fx_cost
        cost_pct_of_gross = total_cost / max(abs(total_gross), 0.01) * 100

        # Edge and cost as % of position value
        gross_edge_pct = total_gross / max(total_position_value, 1.0) * 100
        total_cost_pct = total_cost / max(total_position_value, 1.0) * 100
        cost_adjusted_edge = gross_edge_pct - total_cost_pct

        details.append(TickerCostDetail(
            symbol=symbol,
            trade_count=n,
            avg_spread_at_entry_pct=round(avg_spread_entry, 4),
            avg_spread_at_exit_pct=round(avg_spread_exit, 4),
            total_commission=round(total_comm, 4),
            total_spread_cost=round(total_spread, 4),
            fx_conversion_cost=round(fx_cost, 4),
            total_cost=round(total_cost, 4),
            total_gross_pnl=round(total_gross, 4),
            total_net_pnl=round(total_net, 4),
            cost_pct_of_gross=round(cost_pct_of_gross, 2),
            gross_edge_pct=round(gross_edge_pct, 4),
            total_cost_pct=round(total_cost_pct, 4),
            cost_adjusted_edge_pct=round(cost_adjusted_edge, 4),
            currency=currency,
            is_fx_instrument=is_fx,
        ))

    return details


def _compute_efficiency_metrics(
    costs: List[TradeCostBreakdown],
    trades_raw: List[Dict],
    ticker_details: List[TickerCostDetail],
    cost_model: CostModel,
    equity: float,
) -> CostEfficiencyMetrics:
    """Compute portfolio-level cost efficiency metrics.

    Returns:
      - cost_to_edge_ratio: fraction of edge consumed by costs (0=free, 1=all edge gone)
      - break_even_spread: max avg spread where avg trade is still profitable
      - optimal_position_size: size that minimizes cost drag per unit edge
      - cost_efficiency_score: 0-100 composite score
    """
    n = len(costs)
    if n == 0:
        return CostEfficiencyMetrics(
            cost_to_edge_ratio=0.0, break_even_spread_pct=0.0,
            optimal_position_size_gbp=0.0, avg_cost_per_trade_gbp=0.0,
            avg_edge_per_trade_gbp=0.0, cost_efficiency_score=100.0,
        )

    total_gross = sum(c.gross_pnl for c in costs)
    total_cost = sum(td.total_cost for td in ticker_details)

    # Cost-to-edge ratio: how much of gross edge is consumed by costs
    cost_to_edge = total_cost / max(abs(total_gross), 0.01)

    avg_cost_per_trade = total_cost / n
    avg_edge_per_trade = total_gross / n

    # Average position value
    total_pos_val = 0.0
    for t in trades_raw:
        entry_p = t.get("entry_price", 0)
        qty = t.get("qty", 0)
        total_pos_val += max(entry_p * qty, 1.0)
    avg_pos_val = total_pos_val / n if n > 0 else 1.0

    # Break-even spread: if average gross PnL = total_cost, solve for spread
    # gross_pnl_per_trade = spread_cost_per_trade + commission_per_trade + fx_per_trade
    # spread_cost = spread_pct * position_value  (entry + exit combined)
    # So: break_even_spread = (avg_gross - avg_commission - avg_fx) / avg_position_value
    avg_commission = sum(c.commission for c in costs) / n
    avg_fx = sum(td.fx_conversion_cost for td in ticker_details) / n if ticker_details else 0
    if avg_pos_val > 0:
        break_even_spread = max(
            (avg_edge_per_trade - avg_commission - avg_fx) / avg_pos_val * 100,
            0.0,
        )
    else:
        break_even_spread = 0.0

    # Optimal position size: minimize cost drag per unit of edge
    # Fixed costs (commission) are amortized over position size.
    # Variable costs (spread, FX) scale with size.
    # optimal_size = sqrt(commission_fixed / variable_rate)
    # where variable_rate = (avg_spread/100 + fx_rate)
    avg_spread = sum(c.spread_entry_pct + c.spread_exit_pct for c in costs) / n / 100
    fx_rate = cost_model.fx_conversion_pct * 2  # Round-trip FX
    variable_rate = max(avg_spread + fx_rate, 1e-6)
    commission_per_round_trip = cost_model.ibkr_commission_gbp * 2
    optimal_size = math.sqrt(commission_per_round_trip / variable_rate)

    # Cost efficiency score: 0-100
    # Based on: cost-to-edge ratio (lower=better), spread victim rate, cost vs model
    spread_victim_rate = sum(1 for c in costs if c.is_spread_victim) / n
    # Score components: edge retention (60%), victim rate (20%), cost model adherence (20%)
    edge_retention_score = max(0, min(60, 60 * (1 - cost_to_edge)))
    victim_score = max(0, 20 * (1 - spread_victim_rate * 5))  # 20% if no victims
    # Model adherence: check how many tickers stay within 2x model spread
    flagged_count = sum(
        1 for td in ticker_details
        if td.avg_spread_at_entry_pct > cost_model.spread_veto_pct * 200  # 2x model
    )
    adherence_score = max(0, 20 * (1 - flagged_count / max(len(ticker_details), 1)))
    efficiency_score = round(edge_retention_score + victim_score + adherence_score, 1)

    return CostEfficiencyMetrics(
        cost_to_edge_ratio=round(cost_to_edge, 4),
        break_even_spread_pct=round(break_even_spread, 4),
        optimal_position_size_gbp=round(optimal_size, 2),
        avg_cost_per_trade_gbp=round(avg_cost_per_trade, 4),
        avg_edge_per_trade_gbp=round(avg_edge_per_trade, 4),
        cost_efficiency_score=efficiency_score,
    )


def _compute_comparisons(
    ticker_details: List[TickerCostDetail],
    cost_model: CostModel,
) -> List[CostModelComparison]:
    """Compare actual per-ticker costs vs CostModel defaults.

    Flags:
      - Any ticker where actual spread > 2x the model spread_veto_pct
      - Any ticker where FX cost > expected fx_conversion_pct
    Recommends parameter adjustments.
    """
    comparisons: List[CostModelComparison] = []
    model_spread = cost_model.spread_veto_pct * 100  # Convert to percent

    for td in ticker_details:
        actual_spread = td.avg_spread_at_entry_pct
        spread_ratio = actual_spread / max(model_spread, 0.001)
        spread_flag = spread_ratio > 2.0

        # FX comparison: estimate actual FX drag from position
        # actual_fx_pct = fx_cost / position_value * 100
        if td.is_fx_instrument and td.total_cost > 0:
            total_pos = td.total_cost / max(td.total_cost_pct / 100, 1e-6)
            actual_fx_pct = td.fx_conversion_cost / max(total_pos, 1.0) * 100
        else:
            actual_fx_pct = 0.0
        model_fx_pct = cost_model.fx_conversion_pct * 100 * 2 if td.is_fx_instrument else 0.0
        fx_flag = actual_fx_pct > model_fx_pct * 1.5 if model_fx_pct > 0 else False

        # Recommended spread gate: halfway between actual and 2x actual
        recommended_spread = round(max(actual_spread * 1.5, model_spread), 4)

        # Build recommendation
        actions: List[str] = []
        if spread_flag:
            actions.append(
                f"WIDEN spread gate to {recommended_spread:.3f}% "
                f"(actual {actual_spread:.3f}% is {spread_ratio:.1f}x model)"
            )
        if fx_flag:
            actions.append(
                f"FX drag {actual_fx_pct:.3f}% exceeds model {model_fx_pct:.3f}% — "
                f"review FX timing or consider GBP-denominated alternative"
            )
        if td.cost_adjusted_edge_pct < 0:
            actions.append(
                f"NEGATIVE cost-adjusted edge ({td.cost_adjusted_edge_pct:.3f}%) — "
                f"consider excluding from universe"
            )

        comparisons.append(CostModelComparison(
            symbol=td.symbol,
            actual_avg_spread_pct=round(actual_spread, 4),
            model_default_spread_pct=round(model_spread, 4),
            spread_ratio=round(spread_ratio, 2),
            spread_flag=spread_flag,
            actual_fx_cost_pct=round(actual_fx_pct, 4),
            model_fx_cost_pct=round(model_fx_pct, 4),
            fx_flag=fx_flag,
            recommended_spread_gate=recommended_spread,
            recommended_action=" | ".join(actions) if actions else "OK",
        ))

    return comparisons


def generate_detailed_cost_analysis(
    wal_dir: Path = WAL_DIR,
    days: int = 1,
    equity: float = STARTING_EQUITY,
    config_dir: Optional[Path] = None,
) -> DetailedCostAnalysis:
    """Generate Q-095 enhanced cost analysis with per-ticker breakdowns.

    Args:
        wal_dir: WAL events directory
        days: Lookback period
        equity: Current equity for % calculations
        config_dir: Config directory (for contracts.toml and cost_model)

    Returns:
        DetailedCostAnalysis with full per-ticker breakdown, efficiency
        metrics, and comparison table.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("Q-095: Generating detailed cost analysis (%d-day lookback)", days)

    # Load cost model from config.toml
    cost_model = CostModel.from_toml(config_dir)
    cost_model_source = "config.toml" if (config_dir or Path(
        os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config")
    )) / "config.toml" else "defaults"

    # Load currency map from contracts.toml
    currency_map = _load_contracts_currency_map(config_dir)

    # Load trades
    trades_raw = _load_closed_trades(wal_dir, days)
    if not trades_raw:
        return DetailedCostAnalysis(
            date=today, lookback_days=days, cost_model_source=cost_model_source,
        )

    # Re-use existing per-trade cost computation
    costs = [_compute_trade_cost(t) for t in trades_raw]

    # 1. Per-ticker breakdown
    ticker_details = _compute_ticker_details(costs, trades_raw, currency_map, cost_model)

    # 2. Cost efficiency metrics
    efficiency = _compute_efficiency_metrics(costs, trades_raw, ticker_details, cost_model, equity)

    # 3. Comparison table
    comparisons = _compute_comparisons(ticker_details, cost_model)
    flagged = [c.symbol for c in comparisons if c.spread_flag or c.fx_flag]

    analysis = DetailedCostAnalysis(
        date=today,
        lookback_days=days,
        cost_model_source=cost_model_source,
        ticker_details=ticker_details,
        efficiency=efficiency,
        comparisons=comparisons,
        flagged_tickers=flagged,
    )

    log.info(
        "Q-095: %d tickers analysed, %d flagged, efficiency=%.1f/100",
        len(ticker_details), len(flagged),
        efficiency.cost_efficiency_score if efficiency else 0,
    )

    return analysis


def save_detailed_analysis(
    analysis: DetailedCostAnalysis,
    output_dir: Path = DATA_DIR,
) -> Path:
    """Save detailed cost analysis to JSON."""
    report_dir = output_dir / "cost_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    output_path = report_dir / f"{analysis.date}_detailed_cost_analysis.json"
    tmp = output_path.with_suffix(".json.tmp")
    try:
        tmp.write_text(analysis.to_json(), encoding="utf-8")
        os.rename(str(tmp), str(output_path))
        log.info("Detailed cost analysis saved: %s", output_path)
        return output_path
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def format_detailed_summary(analysis: DetailedCostAnalysis) -> str:
    """Format the detailed cost analysis as a human-readable text report."""
    lines = [
        f"Q-095 DETAILED COST ANALYSIS — {analysis.date} ({analysis.lookback_days}d lookback)",
        f"Cost model source: {analysis.cost_model_source}",
        "=" * 72,
        "",
    ]

    if not analysis.ticker_details:
        lines.append("No trades in period.")
        return "\n".join(lines)

    # Per-ticker table
    lines.append("PER-TICKER COST BREAKDOWN:")
    lines.append("-" * 72)
    lines.append(
        f"{'Ticker':<12} {'#':>3} {'AvgSprd%':>8} {'Comm':>8} {'Spread':>8} "
        f"{'FX':>8} {'Total':>8} {'Edge%':>7} {'AdjEdge%':>8} {'Ccy':>4}"
    )
    lines.append("-" * 72)

    for td in sorted(analysis.ticker_details, key=lambda x: -x.total_cost):
        flag = " !" if td.cost_adjusted_edge_pct < 0 else ""
        lines.append(
            f"{td.symbol:<12} {td.trade_count:>3} {td.avg_spread_at_entry_pct:>8.3f} "
            f"{td.total_commission:>8.2f} {td.total_spread_cost:>8.2f} "
            f"{td.fx_conversion_cost:>8.2f} {td.total_cost:>8.2f} "
            f"{td.gross_edge_pct:>7.3f} {td.cost_adjusted_edge_pct:>8.3f} "
            f"{td.currency:>4}{flag}"
        )

    # Efficiency metrics
    if analysis.efficiency:
        eff = analysis.efficiency
        lines.extend([
            "",
            "COST EFFICIENCY METRICS:",
            "-" * 72,
            f"  Cost-to-edge ratio:        {eff.cost_to_edge_ratio:.3f} "
            f"({'GOOD' if eff.cost_to_edge_ratio < 0.3 else 'WARN' if eff.cost_to_edge_ratio < 0.6 else 'POOR'})",
            f"  Break-even spread:         {eff.break_even_spread_pct:.3f}%",
            f"  Optimal position size:     GBP {eff.optimal_position_size_gbp:,.2f}",
            f"  Avg cost per trade:        GBP {eff.avg_cost_per_trade_gbp:.4f}",
            f"  Avg edge per trade:        GBP {eff.avg_edge_per_trade_gbp:.4f}",
            f"  Efficiency score:          {eff.cost_efficiency_score:.1f}/100",
        ])

    # Comparison table
    if analysis.comparisons:
        lines.extend([
            "",
            "ACTUAL vs MODEL COMPARISON:",
            "-" * 72,
            f"{'Ticker':<12} {'ActSprd%':>8} {'ModSprd%':>8} {'Ratio':>6} "
            f"{'SprdFlg':>7} {'ActFX%':>7} {'ModFX%':>7} {'FXFlg':>5} {'Action':<30}",
            "-" * 72,
        ])
        for c in analysis.comparisons:
            sflag = "FLAG" if c.spread_flag else "ok"
            fflag = "FLAG" if c.fx_flag else "ok"
            action = c.recommended_action[:30]
            lines.append(
                f"{c.symbol:<12} {c.actual_avg_spread_pct:>8.3f} "
                f"{c.model_default_spread_pct:>8.3f} {c.spread_ratio:>6.1f} "
                f"{sflag:>7} {c.actual_fx_cost_pct:>7.3f} {c.model_fx_cost_pct:>7.3f} "
                f"{fflag:>5} {action:<30}"
            )

    # Flagged tickers summary
    if analysis.flagged_tickers:
        lines.extend([
            "",
            f"FLAGGED TICKERS ({len(analysis.flagged_tickers)}):",
            "  " + ", ".join(analysis.flagged_tickers),
        ])

    return "\n".join(lines)


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
    parser.add_argument(
        "--detailed-json", action="store_true",
        help="Q-095: Output full per-ticker breakdown as JSON",
    )
    parser.add_argument(
        "--config-dir", type=str, default=None,
        help="Config directory (for config.toml and contracts.toml)",
    )
    args = parser.parse_args()

    config_dir = Path(args.config_dir) if args.config_dir else None

    report = generate_cost_drag_report(
        wal_dir=Path(args.wal_dir),
        days=args.days,
        equity=args.equity,
    )

    save_cost_drag_report(report)

    # Print standard summary
    print(format_telegram_summary(report))

    # Q-095: Detailed cost analysis
    if args.detailed_json:
        analysis = generate_detailed_cost_analysis(
            wal_dir=Path(args.wal_dir),
            days=args.days,
            equity=args.equity,
            config_dir=config_dir,
        )
        save_detailed_analysis(analysis)
        print()
        print(format_detailed_summary(analysis))
        print()
        print("--- DETAILED JSON ---")
        print(analysis.to_json())

    if args.send_telegram:
        send_telegram(report)


if __name__ == "__main__":
    main()
