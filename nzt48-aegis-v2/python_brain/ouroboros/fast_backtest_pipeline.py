"""Fast Backtest Pipeline — chains backfill_simulator output through RiskArbiterPy.

Runs the backfill simulator to generate raw signals for all tickers (~2 min),
then post-filters every signal through the 33-CHECK Python risk arbiter.
Produces a comprehensive JSON report with breakdowns by exchange, entry type,
ticker, day of week, and hour of day.

The key insight: backfill_simulator generates signals fast (no bridge.py IPC).
We just chain its output through the risk arbiter as a post-filter.

Usage:
    # CLI
    python3 -m python_brain.ouroboros.fast_backtest_pipeline --days 730 --interval 60m

    # Module import
    from python_brain.ouroboros.fast_backtest_pipeline import run_pipeline
    report = run_pipeline(days=730, interval="60m")
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Path setup — must happen before local imports
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

from python_brain.ouroboros.backfill_simulator import (
    SimTrade,
    simulate_ticker,
    STARTING_EQUITY,
)
# Handle renamed function (agent a665f97 renamed to parallel version)
try:
    from python_brain.ouroboros.backfill_simulator import fetch_historical_data_parallel as fetch_historical_data
except ImportError:
    from python_brain.ouroboros.backfill_simulator import fetch_historical_data
from python_brain.ouroboros.risk_arbiter_py import (
    Direction,
    EvalContext,
    MacroIndicator,
    PortfolioState,
    RiskArbiterPy,
    RiskRegime,
)
from python_brain.ouroboros.contract_loader import (
    load_yfinance_symbols,
    load_leverage_map,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [FastBT] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fast_backtest_pipeline")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_DAYS = 730
DEFAULT_INTERVAL = "60m"
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "data" / "backtest_reports"
DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "config.toml"


# ---------------------------------------------------------------------------
# Exchange inference from yfinance symbol
# ---------------------------------------------------------------------------
def infer_exchange(yf_symbol: str) -> str:
    """Infer the exchange from a yfinance symbol suffix.

    Maps yfinance suffixes to the exchange names used in contracts.toml.
    Falls back to "UNKNOWN" if no suffix match.
    """
    if yf_symbol.endswith(".L"):
        return "LSE"
    elif yf_symbol.endswith(".T"):
        return "TSE"
    elif yf_symbol.endswith(".HK"):
        return "HKEX"
    elif yf_symbol.endswith(".SI"):
        return "SGX"
    elif yf_symbol.endswith(".DE"):
        return "XETRA"
    elif yf_symbol.endswith(".PA"):
        return "EURONEXT"
    elif yf_symbol.endswith(".AS"):
        return "EURONEXT"
    else:
        # No suffix = US (SMART) or XETRA/EURONEXT plain ticker
        # Heuristic: if purely alphabetic (no dots), likely US
        return "US"


# ---------------------------------------------------------------------------
# Build exchange map from contracts.toml for precise mapping
# ---------------------------------------------------------------------------
def _build_exchange_map() -> Dict[str, str]:
    """Build yfinance_symbol -> exchange mapping from contracts.toml.

    More precise than suffix heuristics because it reads the actual exchange
    field from the contract definitions.
    """
    exchange_map: Dict[str, str] = {}
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]

        contracts_path = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config")) / "contracts.toml"
        if not contracts_path.exists():
            contracts_path = _PROJECT_ROOT / "config" / "contracts.toml"
        if contracts_path.exists():
            with open(contracts_path, "rb") as f:
                data = tomllib.load(f)
            for c in data.get("contracts", []):
                sym = c.get("symbol", "")
                exchange = c.get("exchange", "")
                if not sym or not exchange:
                    continue
                # Build the yfinance symbol the same way contract_loader does
                if exchange == "LSEETF":
                    yf_sym = f"{sym}.L" if not sym.endswith(".L") else sym
                    exchange_map[yf_sym] = "LSE"
                elif exchange == "TSE":
                    yf_sym = f"{sym}.T"
                    exchange_map[yf_sym] = "TSE"
                elif exchange == "HKEX":
                    yf_sym = f"{sym:>04s}.HK"
                    exchange_map[yf_sym] = "HKEX"
                elif exchange == "SGX":
                    yf_sym = f"{sym}.SI"
                    exchange_map[yf_sym] = "SGX"
                elif exchange == "XETRA":
                    exchange_map[sym] = "XETRA"
                elif exchange == "EURONEXT":
                    exchange_map[sym] = "EURONEXT"
                else:
                    # SMART (US), etc.
                    exchange_map[sym] = "US"
    except Exception as e:
        log.warning("Failed to build exchange map from contracts.toml: %s", e)
    return exchange_map


# ---------------------------------------------------------------------------
# Data containers for pipeline results
# ---------------------------------------------------------------------------
@dataclass
class FilteredTrade:
    """A trade with risk arbiter evaluation result attached."""
    trade: SimTrade
    approved: bool
    vetoes: List[str]
    exchange: str
    day_of_week: str   # "Monday", "Tuesday", etc.
    hour_of_day: int   # 0-23


@dataclass
class PipelineResult:
    """Full pipeline output with raw and filtered results."""
    # Meta
    run_timestamp: str
    days: int
    interval: str
    tickers_requested: int
    tickers_with_data: int
    elapsed_fetch_secs: float
    elapsed_simulate_secs: float
    elapsed_filter_secs: float
    elapsed_total_secs: float

    # Raw (pre-filter) summary
    raw_total_trades: int
    raw_wins: int
    raw_losses: int
    raw_win_rate: float
    raw_profit_factor: float
    raw_total_pnl: float

    # Filtered (post-filter) summary
    filtered_total_trades: int
    filtered_wins: int
    filtered_losses: int
    filtered_win_rate: float
    filtered_profit_factor: float
    filtered_total_pnl: float

    # Veto analysis
    veto_counts: Dict[str, int]        # CHECK_N -> count
    veto_rate: float                   # % of raw trades vetoed

    # Breakdowns (filtered trades only)
    by_exchange: Dict[str, Dict[str, Any]]
    by_entry_type: Dict[str, Dict[str, Any]]
    by_ticker: Dict[str, Dict[str, Any]]
    by_day_of_week: Dict[str, Dict[str, Any]]
    by_hour_of_day: Dict[str, Dict[str, Any]]

    # Equity curve (filtered)
    starting_equity: float
    ending_equity: float
    return_pct: float
    max_drawdown_pct: float

    # Top vetoed trades (trades that the arbiter blocked but would have been winners)
    missed_winners: List[Dict[str, Any]]
    # Top passed trades that lost (trades the arbiter approved but were losers)
    bad_approvals: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Helper: compute breakdown stats for a group of trades
# ---------------------------------------------------------------------------
def _compute_group_stats(trades: List[SimTrade]) -> Dict[str, Any]:
    """Compute summary stats for a group of SimTrade objects."""
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_pnl": 0.0,
            "avg_pnl_pct": 0.0,
            "avg_rung": 0.0,
            "avg_hold_bars": 0.0,
        }
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_wins = sum(t.pnl for t in wins)
    gross_losses = abs(sum(t.pnl for t in losses))
    n = len(trades)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n, 4) if n > 0 else 0.0,
        "profit_factor": round(gross_wins / max(gross_losses, 1e-9), 3),
        "total_pnl": round(sum(t.pnl for t in trades), 4),
        "avg_pnl_pct": round(sum(t.pnl_pct for t in trades) / n, 4) if n > 0 else 0.0,
        "avg_rung": round(sum(t.rung_achieved for t in trades) / n, 2) if n > 0 else 0.0,
        "avg_hold_bars": round(sum(t.hold_bars for t in trades) / n, 1) if n > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# Core: risk arbiter filter
# ---------------------------------------------------------------------------
def filter_trades_through_arbiter(
    all_trades: List[SimTrade],
    arbiter: RiskArbiterPy,
    exchange_map: Dict[str, str],
    leverage_map: Dict[str, int],
) -> List[FilteredTrade]:
    """Run every raw trade through the risk arbiter and record the verdict.

    Constructs a realistic EvalContext for each trade:
    - time_secs derived from the trade's date string
    - Spread set to a realistic 0.05% (tight enough to pass most checks)
    - Broker connected, WAL available (simulation assumptions)
    - Leverage from contracts.toml leverage_map
    - Confidence assigned per entry type from config.toml [entry_types]

    The arbiter runs in simulation_mode=True with paper_uses_live_gates=False,
    which relaxes cash buffer, portfolio heat, and drawdown checks -- matching
    paper trading configuration.
    """
    results: List[FilteredTrade] = []

    # Entry type -> base confidence from config
    entry_type_confidence = {
        "TypeA": arbiter.config.confidence_floor + 5.0,   # Above floor
        "TypeB": 82.0,   # EarlyRunner — strongest signal
        "TypeC": 72.0,   # OverboughtFade
        "TypeD": 80.0,   # SupportBounce
    }

    # Try to load entry type confidences from config.toml
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        cfg_path = DEFAULT_CONFIG_PATH
        if cfg_path.exists():
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
            et = cfg.get("entry_types", {})
            entry_type_confidence["TypeA"] = float(et.get("type_a_confidence", entry_type_confidence["TypeA"]))
            entry_type_confidence["TypeB"] = float(et.get("type_b_confidence", entry_type_confidence["TypeB"]))
            entry_type_confidence["TypeC"] = float(et.get("type_c_confidence", entry_type_confidence["TypeC"]))
            entry_type_confidence["TypeD"] = float(et.get("type_d_confidence", entry_type_confidence["TypeD"]))
    except Exception:
        pass

    # Fresh portfolio for each simulation run
    portfolio = PortfolioState(
        equity=STARTING_EQUITY,
        initial_equity=STARTING_EQUITY,
        cash=STARTING_EQUITY,
        filled_positions=0,
        pending_positions=0,
    )

    # Day-of-week names
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    for trade in all_trades:
        # Determine exchange
        exchange = exchange_map.get(trade.ticker, infer_exchange(trade.ticker))

        # Parse date for time-of-day and day-of-week
        try:
            dt = datetime.fromisoformat(trade.date.replace("Z", "+00:00"))
            time_secs = dt.hour * 3600 + dt.minute * 60 + dt.second
            day_of_week = day_names[dt.weekday()]
            hour_of_day = dt.hour
        except (ValueError, AttributeError):
            # Fallback: assume mid-session
            time_secs = 10 * 3600  # 10:00
            day_of_week = "Unknown"
            hour_of_day = 10

        # Leverage from contracts.toml
        # Strip suffix for leverage lookup (leverage_map uses IBKR symbols)
        ibkr_sym = trade.ticker
        leverage = leverage_map.get(ibkr_sym, 1)

        # Confidence from entry type config
        confidence = entry_type_confidence.get(trade.entry_type, 70.0)

        # Construct a realistic bid/ask from entry price (tight spread for simulation)
        spread_pct = 0.0005  # 0.05% = 5bps — realistic for liquid ETPs
        mid = trade.entry_price
        bid = mid * (1.0 - spread_pct / 2)
        ask = mid * (1.0 + spread_pct / 2)

        # Build EvalContext — simulation-friendly defaults
        ctx = EvalContext(
            time_secs=time_secs,
            last_tick_age_secs=1,        # Fresh data
            bid=bid,
            ask=ask,
            broker_connected=True,
            wal_available=True,
            now_ns=int(time.time() * 1e9),
            volatilities={},
            ticker_halted=False,
            garch_sigma=-1.0,            # Sentinel: skips GARCH check
            leverage_factor=leverage,
            scanner_score=-1.0,          # Sentinel: skips scanner check
            kelly_fraction_raw=0.05,     # Reasonable Kelly for simulation
            macro_indicator=MacroIndicator(
                vix=15.0,
                dxy=100.0,
                credit_spread_bps=100.0,
                fear_greed=50.0,
                last_update_ns=int(time.time() * 1e9),
            ),
            ticker_ic=0.0,
            ticker_trade_count=0,
            ticker_locked=False,
            ticker_position_count=0,
            evt_cvar=0.0,
            kalman_divergence=0.0,
            native_spread_bps=spread_pct * 10000,
            structural_score=50.0,
        )

        # Evaluate through arbiter
        approved, vetoes = arbiter.evaluate(
            ticker=trade.ticker,
            side=Direction.Long,
            confidence=confidence,
            kelly=0.05,
            portfolio=portfolio,
            ctx=ctx,
        )

        results.append(FilteredTrade(
            trade=trade,
            approved=approved,
            vetoes=vetoes,
            exchange=exchange,
            day_of_week=day_of_week,
            hour_of_day=hour_of_day,
        ))

    return results


# ---------------------------------------------------------------------------
# Core: compute equity curve with max drawdown
# ---------------------------------------------------------------------------
def _compute_equity_curve(trades: List[SimTrade]) -> Tuple[float, float, float]:
    """Compute ending equity, return %, and max drawdown % from filtered trades.

    Uses a conservative 10% Kelly fraction position sizing.
    Returns (ending_equity, return_pct, max_drawdown_pct).
    """
    equity = STARTING_EQUITY
    kelly_frac = 0.10
    peak = equity
    max_dd_pct = 0.0

    for t in sorted(trades, key=lambda x: (x.date, x.entry_bar)):
        if math.isnan(t.entry_price) or math.isnan(t.pnl) or t.entry_price <= 0:
            continue
        position_size = equity * kelly_frac
        shares = math.floor(position_size / max(t.entry_price, 1e-9))
        if shares <= 0:
            continue
        trade_pnl = shares * t.pnl
        equity += trade_pnl
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak * 100.0
            max_dd_pct = max(max_dd_pct, dd)

    return_pct = ((equity - STARTING_EQUITY) / STARTING_EQUITY) * 100.0
    return equity, return_pct, max_dd_pct


# ---------------------------------------------------------------------------
# Core: build the full pipeline result
# ---------------------------------------------------------------------------
def build_pipeline_result(
    all_trades: List[SimTrade],
    filtered_results: List[FilteredTrade],
    days: int,
    interval: str,
    tickers_requested: int,
    tickers_with_data: int,
    elapsed_fetch: float,
    elapsed_simulate: float,
    elapsed_filter: float,
    elapsed_total: float,
) -> PipelineResult:
    """Assemble the comprehensive pipeline result from raw and filtered trades."""

    # Approved and vetoed trade lists
    approved_trades = [ft.trade for ft in filtered_results if ft.approved]
    vetoed_results = [ft for ft in filtered_results if not ft.approved]

    # Raw stats
    raw_wins = [t for t in all_trades if t.pnl > 0]
    raw_losses = [t for t in all_trades if t.pnl <= 0]
    raw_gross_wins = sum(t.pnl for t in raw_wins)
    raw_gross_losses = abs(sum(t.pnl for t in raw_losses))

    # Filtered stats
    filt_wins = [t for t in approved_trades if t.pnl > 0]
    filt_losses = [t for t in approved_trades if t.pnl <= 0]
    filt_gross_wins = sum(t.pnl for t in filt_wins)
    filt_gross_losses = abs(sum(t.pnl for t in filt_losses))

    # Veto analysis: count which CHECKs fire most
    veto_counts: Dict[str, int] = defaultdict(int)
    for ft in vetoed_results:
        for veto in ft.vetoes:
            # Extract CHECK_N prefix
            check_id = veto.split(":")[0].strip() if ":" in veto else veto
            veto_counts[check_id] += 1
    veto_counts = dict(sorted(veto_counts.items(), key=lambda x: -x[1]))

    # Breakdowns (filtered/approved trades only)
    by_exchange: Dict[str, List[SimTrade]] = defaultdict(list)
    by_entry_type: Dict[str, List[SimTrade]] = defaultdict(list)
    by_ticker: Dict[str, List[SimTrade]] = defaultdict(list)
    by_dow: Dict[str, List[SimTrade]] = defaultdict(list)
    by_hour: Dict[str, List[SimTrade]] = defaultdict(list)

    for ft in filtered_results:
        if ft.approved:
            by_exchange[ft.exchange].append(ft.trade)
            by_entry_type[ft.trade.entry_type].append(ft.trade)
            by_ticker[ft.trade.ticker].append(ft.trade)
            by_dow[ft.day_of_week].append(ft.trade)
            by_hour[str(ft.hour_of_day)].append(ft.trade)

    # Equity curve
    ending_equity, return_pct, max_dd_pct = _compute_equity_curve(approved_trades)

    # Missed winners: vetoed trades that would have been profitable
    missed_winners = []
    for ft in vetoed_results:
        if ft.trade.pnl > 0:
            missed_winners.append({
                "ticker": ft.trade.ticker,
                "date": ft.trade.date,
                "entry_type": ft.trade.entry_type,
                "pnl": round(ft.trade.pnl, 4),
                "pnl_pct": round(ft.trade.pnl_pct, 2),
                "rung_achieved": ft.trade.rung_achieved,
                "vetoes": ft.vetoes,
                "exchange": ft.exchange,
            })
    missed_winners.sort(key=lambda x: -x["pnl"])
    missed_winners = missed_winners[:20]  # Top 20

    # Bad approvals: approved trades that lost money
    bad_approvals = []
    for ft in filtered_results:
        if ft.approved and ft.trade.pnl < 0:
            bad_approvals.append({
                "ticker": ft.trade.ticker,
                "date": ft.trade.date,
                "entry_type": ft.trade.entry_type,
                "pnl": round(ft.trade.pnl, 4),
                "pnl_pct": round(ft.trade.pnl_pct, 2),
                "rung_achieved": ft.trade.rung_achieved,
                "exchange": ft.exchange,
            })
    bad_approvals.sort(key=lambda x: x["pnl"])
    bad_approvals = bad_approvals[:20]  # Top 20 worst

    raw_total = len(all_trades)
    filt_total = len(approved_trades)

    return PipelineResult(
        run_timestamp=datetime.now(timezone.utc).isoformat(),
        days=days,
        interval=interval,
        tickers_requested=tickers_requested,
        tickers_with_data=tickers_with_data,
        elapsed_fetch_secs=round(elapsed_fetch, 1),
        elapsed_simulate_secs=round(elapsed_simulate, 1),
        elapsed_filter_secs=round(elapsed_filter, 1),
        elapsed_total_secs=round(elapsed_total, 1),
        raw_total_trades=raw_total,
        raw_wins=len(raw_wins),
        raw_losses=len(raw_losses),
        raw_win_rate=round(len(raw_wins) / max(raw_total, 1), 4),
        raw_profit_factor=round(raw_gross_wins / max(raw_gross_losses, 1e-9), 3),
        raw_total_pnl=round(sum(t.pnl for t in all_trades), 4),
        filtered_total_trades=filt_total,
        filtered_wins=len(filt_wins),
        filtered_losses=len(filt_losses),
        filtered_win_rate=round(len(filt_wins) / max(filt_total, 1), 4),
        filtered_profit_factor=round(filt_gross_wins / max(filt_gross_losses, 1e-9), 3),
        filtered_total_pnl=round(sum(t.pnl for t in approved_trades), 4),
        veto_counts=veto_counts,
        veto_rate=round(len(vetoed_results) / max(raw_total, 1), 4),
        by_exchange={k: _compute_group_stats(v) for k, v in sorted(by_exchange.items())},
        by_entry_type={k: _compute_group_stats(v) for k, v in sorted(by_entry_type.items())},
        by_ticker={k: _compute_group_stats(v) for k, v in sorted(by_ticker.items())},
        by_day_of_week={k: _compute_group_stats(v) for k, v in sorted(by_dow.items())},
        by_hour_of_day={k: _compute_group_stats(v) for k, v in sorted(by_hour.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 99)},
        starting_equity=STARTING_EQUITY,
        ending_equity=round(ending_equity, 2),
        return_pct=round(return_pct, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        missed_winners=missed_winners,
        bad_approvals=bad_approvals,
    )


# ---------------------------------------------------------------------------
# Save report
# ---------------------------------------------------------------------------
def save_report(result: PipelineResult, output_dir: Path) -> Path:
    """Save the pipeline result as a JSON report. Returns the output path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"fast_backtest_{result.days}d_{result.interval}_{timestamp}.json"
    output_path = output_dir / filename

    # Convert dataclass to dict for JSON serialization
    report_dict = asdict(result)
    # SimTrade dataclasses inside missed_winners/bad_approvals are already dicts via asdict

    output_path.write_text(json.dumps(report_dict, indent=2, default=str))
    log.info("Report saved: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Print summary to console
# ---------------------------------------------------------------------------
def print_summary(result: PipelineResult) -> None:
    """Print a human-readable summary to stdout."""
    sep = "=" * 72
    print(f"\n{sep}")
    print(f"  FAST BACKTEST PIPELINE — {result.days}d {result.interval}")
    print(f"  {result.run_timestamp}")
    print(f"{sep}\n")

    print(f"TIMING")
    print(f"  Data fetch:    {result.elapsed_fetch_secs:.1f}s")
    print(f"  Simulation:    {result.elapsed_simulate_secs:.1f}s")
    print(f"  Risk filter:   {result.elapsed_filter_secs:.1f}s")
    print(f"  Total:         {result.elapsed_total_secs:.1f}s")
    print(f"  Tickers:       {result.tickers_with_data}/{result.tickers_requested}")
    print()

    print(f"RAW (pre-filter)")
    print(f"  Trades: {result.raw_total_trades}  |  Wins: {result.raw_wins}  |  WR: {result.raw_win_rate:.1%}")
    print(f"  PF: {result.raw_profit_factor:.2f}  |  PnL/share: {result.raw_total_pnl:+.4f}")
    print()

    print(f"FILTERED (post-risk-arbiter)")
    print(f"  Trades: {result.filtered_total_trades}  |  Wins: {result.filtered_wins}  |  WR: {result.filtered_win_rate:.1%}")
    print(f"  PF: {result.filtered_profit_factor:.2f}  |  PnL/share: {result.filtered_total_pnl:+.4f}")
    print(f"  Veto rate: {result.veto_rate:.1%} ({result.raw_total_trades - result.filtered_total_trades} trades blocked)")
    print()

    print(f"EQUITY CURVE (10% Kelly fraction)")
    print(f"  Start: GBP {result.starting_equity:,.2f}")
    print(f"  End:   GBP {result.ending_equity:,.2f}")
    print(f"  Return: {result.return_pct:+.2f}%  |  Max DD: {result.max_drawdown_pct:.2f}%")
    print()

    if result.veto_counts:
        print(f"VETO BREAKDOWN (top 10)")
        print(f"  {'CHECK':<20s} {'Count':>6s} {'% of vetoes':>12s}")
        total_vetoes = sum(result.veto_counts.values())
        for check, count in list(result.veto_counts.items())[:10]:
            pct = count / max(total_vetoes, 1) * 100
            print(f"  {check:<20s} {count:6d} {pct:11.1f}%")
        print()

    if result.by_exchange:
        print(f"BY EXCHANGE (filtered)")
        print(f"  {'Exchange':<12s} {'Trades':>7s} {'Wins':>6s} {'WR':>7s} {'PF':>7s} {'PnL/sh':>10s}")
        for exch, stats in result.by_exchange.items():
            print(
                f"  {exch:<12s} {stats['trades']:7d} {stats['wins']:6d} "
                f"{stats['win_rate']:6.0%} {stats['profit_factor']:7.2f} "
                f"{stats['total_pnl']:+10.4f}"
            )
        print()

    if result.by_entry_type:
        print(f"BY ENTRY TYPE (filtered)")
        print(f"  {'Type':<10s} {'Trades':>7s} {'Wins':>6s} {'WR':>7s} {'PF':>7s} {'AvgRung':>8s}")
        for etype, stats in result.by_entry_type.items():
            print(
                f"  {etype:<10s} {stats['trades']:7d} {stats['wins']:6d} "
                f"{stats['win_rate']:6.0%} {stats['profit_factor']:7.2f} "
                f"{stats['avg_rung']:8.1f}"
            )
        print()

    if result.missed_winners:
        print(f"TOP MISSED WINNERS (vetoed but would have profited)")
        for i, mw in enumerate(result.missed_winners[:5], 1):
            print(
                f"  {i}. {mw['ticker']} {mw['date']} {mw['entry_type']} "
                f"PnL={mw['pnl']:+.4f} ({mw['pnl_pct']:+.1f}%) "
                f"Veto: {mw['vetoes'][0] if mw['vetoes'] else 'none'}"
            )
        print()

    if result.bad_approvals:
        print(f"WORST APPROVED LOSERS (passed arbiter but lost)")
        for i, ba in enumerate(result.bad_approvals[:5], 1):
            print(
                f"  {i}. {ba['ticker']} {ba['date']} {ba['entry_type']} "
                f"PnL={ba['pnl']:+.4f} ({ba['pnl_pct']:+.1f}%)"
            )
        print()

    print(sep)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_pipeline(
    days: int = DEFAULT_DAYS,
    interval: str = DEFAULT_INTERVAL,
    universe_file: Optional[str] = None,
    output_dir: Optional[str] = None,
    config_path: Optional[str] = None,
) -> PipelineResult:
    """Execute the full fast backtest pipeline.

    1. Fetch historical data for all tickers
    2. Simulate trades (backfill_simulator logic)
    3. Post-filter through RiskArbiterPy (33 CHECKs)
    4. Build comprehensive report

    Args:
        days: Lookback period in days.
        interval: Bar interval (e.g., "60m", "1h", "1d").
        universe_file: Optional path to a text file with one ticker per line.
                       If None, uses all tickers from contracts.toml.
        output_dir: Directory for JSON report output.
        config_path: Path to config.toml for risk arbiter.

    Returns:
        PipelineResult with all stats and breakdowns.
    """
    total_start = time.monotonic()
    log.info("Fast Backtest Pipeline starting (%dd, %s)", days, interval)

    # --- Resolve output dir and config ---
    out_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    cfg_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH

    # --- Load ticker universe ---
    if universe_file:
        universe_path = Path(universe_file)
        if not universe_path.exists():
            log.error("Universe file not found: %s", universe_path)
            raise FileNotFoundError(f"Universe file not found: {universe_path}")
        tickers = [
            line.strip() for line in universe_path.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        log.info("Loaded %d tickers from %s", len(tickers), universe_path)
    else:
        tickers = load_yfinance_symbols()
        log.info("Loaded %d tickers from contracts.toml", len(tickers))

    if not tickers:
        log.error("No tickers to simulate. Aborting.")
        raise ValueError("No tickers available for simulation")

    tickers_requested = len(tickers)

    # --- Load exchange and leverage maps ---
    exchange_map = _build_exchange_map()
    leverage_map = load_leverage_map()
    log.info("Exchange map: %d entries, Leverage map: %d entries",
             len(exchange_map), len(leverage_map))

    # --- Load blacklist from config ---
    blacklist: Set[str] = set()
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        if cfg_path.exists():
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
            blacklist = set(cfg.get("blacklist", {}).get("tickers", []))
            if blacklist:
                log.info("Blacklist: %d tickers (%s)", len(blacklist), ", ".join(sorted(blacklist)))
    except Exception:
        pass

    # --- Step 1: Fetch historical data ---
    fetch_start = time.monotonic()
    period = f"{days}d"
    data = fetch_historical_data(tickers, period=period, interval=interval)
    elapsed_fetch = time.monotonic() - fetch_start

    if not data:
        log.error("No historical data fetched. Aborting.")
        raise RuntimeError("No historical data fetched")

    tickers_with_data = len(data)
    log.info("Data fetched for %d/%d tickers in %.1fs", tickers_with_data, tickers_requested, elapsed_fetch)

    # --- Step 2: Simulate trades (backfill_simulator logic) ---
    sim_start = time.monotonic()
    all_trades: List[SimTrade] = []
    for ticker, df in data.items():
        if ticker in blacklist:
            log.info("  %s: SKIPPED (blacklisted)", ticker)
            continue
        trades = simulate_ticker(ticker, df)
        all_trades.extend(trades)
        if trades:
            log.debug("  %s: %d trades", ticker, len(trades))

    elapsed_simulate = time.monotonic() - sim_start
    log.info("Simulation complete: %d raw trades in %.1fs", len(all_trades), elapsed_simulate)

    # --- Step 3: Initialize risk arbiter and filter ---
    filter_start = time.monotonic()

    arbiter = RiskArbiterPy.from_config_toml(cfg_path)
    # Enable simulation mode: relax cash buffer, portfolio heat, drawdown checks
    arbiter.simulation_mode = True
    arbiter.paper_uses_live_gates = False
    # Remove trade limits for exhaustive simulation
    arbiter.config.daily_trade_limit = 999999
    arbiter.config.system_velocity_max = 999999
    arbiter.config.velocity_check_max_intents = 999999

    log.info(
        "Risk arbiter loaded: confidence_floor=%.1f, spread_veto=%.3f%%, "
        "max_positions=%d, blacklist=%d",
        arbiter.config.confidence_floor,
        arbiter.config.spread_veto_pct,
        arbiter.config.max_positions,
        len(arbiter.ticker_blacklist),
    )

    filtered_results = filter_trades_through_arbiter(
        all_trades, arbiter, exchange_map, leverage_map,
    )
    elapsed_filter = time.monotonic() - filter_start
    log.info("Risk filter complete in %.1fs", elapsed_filter)

    approved_count = sum(1 for ft in filtered_results if ft.approved)
    vetoed_count = len(filtered_results) - approved_count
    log.info("Results: %d approved, %d vetoed (%.1f%% veto rate)",
             approved_count, vetoed_count,
             vetoed_count / max(len(filtered_results), 1) * 100)

    # --- Step 4: Build result ---
    elapsed_total = time.monotonic() - total_start

    result = build_pipeline_result(
        all_trades=all_trades,
        filtered_results=filtered_results,
        days=days,
        interval=interval,
        tickers_requested=tickers_requested,
        tickers_with_data=tickers_with_data,
        elapsed_fetch=elapsed_fetch,
        elapsed_simulate=elapsed_simulate,
        elapsed_filter=elapsed_filter,
        elapsed_total=elapsed_total,
    )

    # --- Save and display ---
    report_path = save_report(result, out_dir)
    print_summary(result)
    log.info("Pipeline complete. Report: %s", report_path)

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> int:
    """CLI entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description="Fast Backtest Pipeline: backfill simulator + risk arbiter post-filter",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS,
        help="Lookback period in days (max depends on interval: 60m=730, 5m=59, 1d=unlimited)",
    )
    parser.add_argument(
        "--interval", type=str, default=DEFAULT_INTERVAL,
        help="Bar interval: 1m, 5m, 15m, 30m, 60m, 1h, 1d",
    )
    parser.add_argument(
        "--universe", type=str, default=None,
        help="Path to ticker file (one ticker per line). Default: all contracts.toml tickers",
    )
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help=f"Output directory for JSON reports. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help=f"Path to config.toml. Default: {DEFAULT_CONFIG_PATH}",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Enforce yfinance period limits
    max_days = {
        "1m": 7, "2m": 59, "5m": 59, "15m": 59, "30m": 59,
        "60m": 730, "1h": 730, "90m": 59, "1d": 9999,
    }
    limit = max_days.get(args.interval, 59)
    actual_days = min(args.days, limit)
    if actual_days < args.days:
        log.warning(
            "Clamped --days from %d to %d (yfinance limit for %s interval)",
            args.days, actual_days, args.interval,
        )

    try:
        run_pipeline(
            days=actual_days,
            interval=args.interval,
            universe_file=args.universe,
            output_dir=args.output_dir,
            config_path=args.config,
        )
        return 0
    except Exception as e:
        log.error("Pipeline failed: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
