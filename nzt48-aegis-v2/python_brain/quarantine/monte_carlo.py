"""N9b — Monte Carlo Risk-of-Ruin Simulation.

Uses trade results from N9a backtest (or live WAL) to simulate thousands of
random trade sequences and estimate survival probability.

Metrics:
  - P(ruin): probability of hitting equity floor (default 70% of starting)
  - P(target): probability of reaching 2x starting equity in 252 days
  - Drawdown distribution (5th, 25th, 50th, 75th, 95th percentiles)
  - Expected equity at 252 days (with confidence intervals)
  - Kelly fraction validation (does optimal sizing match config?)

Usage:
    python3 -m python_brain.ouroboros.monte_carlo                          # From latest backtest
    python3 -m python_brain.ouroboros.monte_carlo --trades-file <path>     # From specific file
    python3 -m python_brain.ouroboros.monte_carlo --wal                    # From live WAL trades
    python3 -m python_brain.ouroboros.monte_carlo --simulations 50000      # More sims
    python3 -m python_brain.ouroboros.monte_carlo --send-telegram          # Send report
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

log = logging.getLogger("monte_carlo")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
REPORT_DIR = DATA_DIR / "backtest_reports"
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", "/app/events"))

DEFAULT_SIMULATIONS = 10000
DEFAULT_TRADING_DAYS = 252      # 1 year
EQUITY_FLOOR_PCT = 0.70         # Ruin = 70% of starting equity
EQUITY_TARGET_MULT = 2.0        # Target = 2x starting equity
STARTING_EQUITY = 10000.0
TRADES_PER_DAY = 1.5            # Estimated average


@dataclass
class MonteCarloResult:
    """Result of Monte Carlo simulation."""
    simulations: int
    trading_days: int
    starting_equity: float
    trade_count_input: int

    # Core metrics
    probability_of_ruin: float       # P(equity < floor)
    probability_of_target: float     # P(equity >= 2x starting)
    median_final_equity: float
    mean_final_equity: float

    # Equity distribution at end
    equity_p5: float
    equity_p25: float
    equity_p50: float
    equity_p75: float
    equity_p95: float

    # Drawdown distribution
    max_dd_p5: float
    max_dd_p25: float
    max_dd_p50: float
    max_dd_p75: float
    max_dd_p95: float

    # Return distribution
    annual_return_p5: float
    annual_return_p50: float
    annual_return_p95: float

    # Kelly analysis
    optimal_kelly: float
    current_kelly: float
    kelly_ratio: float  # current/optimal


# ---------------------------------------------------------------------------
# Trade data loaders
# ---------------------------------------------------------------------------
def load_trades_from_backtest(trades_file: Optional[str] = None) -> List[float]:
    """Load trade P&L percentages from N9a backtest output."""
    if trades_file:
        path = Path(trades_file)
    else:
        # Find latest backtest trades file
        if not REPORT_DIR.exists():
            return []
        files = sorted(REPORT_DIR.glob("vanguard_backtest_*.trades.json"))
        if not files:
            return []
        path = files[-1]

    try:
        with open(path) as f:
            trades = json.load(f)
        pnl_pcts = [t.get("pnl_pct", 0) / 100 for t in trades]  # Convert % to fraction
        log.info("Loaded %d trades from %s", len(pnl_pcts), path)
        return pnl_pcts
    except (json.JSONDecodeError, OSError) as e:
        log.error("Failed to load trades: %s", e)
        return []


def load_trades_from_wal() -> List[float]:
    """Load trade P&L from live WAL PositionClosed events."""
    pnl_list = []
    if not WAL_DIR.exists():
        return pnl_list

    for wal_file in sorted(WAL_DIR.glob("*.ndjson")):
        try:
            with open(wal_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if event.get("type") == "PositionClosed":
                            pnl = event.get("final_pnl", 0)
                            entry = event.get("entry_price", 0)
                            qty = event.get("qty", 0)
                            if entry > 0 and qty > 0:
                                pnl_pct = pnl / (entry * qty)
                                pnl_list.append(pnl_pct)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue

    # Also check archive
    archive_dir = WAL_DIR / "archive"
    if archive_dir.exists():
        for wal_file in sorted(archive_dir.glob("*.ndjson")):
            try:
                with open(wal_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            if event.get("type") == "PositionClosed":
                                pnl = event.get("final_pnl", 0)
                                entry = event.get("entry_price", 0)
                                qty = event.get("qty", 0)
                                if entry > 0 and qty > 0:
                                    pnl_pct = pnl / (entry * qty)
                                    pnl_list.append(pnl_pct)
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue

    log.info("Loaded %d trades from WAL", len(pnl_list))
    return pnl_list


# ---------------------------------------------------------------------------
# Monte Carlo engine
# ---------------------------------------------------------------------------
def run_simulation(
    trade_returns: List[float],
    simulations: int = DEFAULT_SIMULATIONS,
    trading_days: int = DEFAULT_TRADING_DAYS,
    starting_equity: float = STARTING_EQUITY,
    trades_per_day: float = TRADES_PER_DAY,
    seed: Optional[int] = None,
) -> MonteCarloResult:
    """Run Monte Carlo simulation.

    For each simulation path:
      1. Draw `trades_per_day` random trades from historical distribution
      2. Apply to equity sequentially for `trading_days` days
      3. Track peak equity and maximum drawdown
      4. Check ruin (equity < floor) and target (equity >= 2x)

    Returns comprehensive risk metrics.
    """
    if not trade_returns or len(trade_returns) < 5:
        log.error("Insufficient trade data (%d trades, need >= 5)", len(trade_returns))
        return MonteCarloResult(
            simulations=0, trading_days=trading_days, starting_equity=starting_equity,
            trade_count_input=len(trade_returns),
            probability_of_ruin=1.0, probability_of_target=0.0,
            median_final_equity=0, mean_final_equity=0,
            equity_p5=0, equity_p25=0, equity_p50=0, equity_p75=0, equity_p95=0,
            max_dd_p5=0, max_dd_p25=0, max_dd_p50=0, max_dd_p75=0, max_dd_p95=0,
            annual_return_p5=0, annual_return_p50=0, annual_return_p95=0,
            optimal_kelly=0, current_kelly=0, kelly_ratio=0,
        )

    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)

    rng = np.random.default_rng(seed)
    returns = np.array(trade_returns)
    equity_floor = starting_equity * EQUITY_FLOOR_PCT
    equity_target = starting_equity * EQUITY_TARGET_MULT

    total_trades_per_path = int(trading_days * trades_per_day)

    final_equities = np.zeros(simulations)
    max_drawdowns = np.zeros(simulations)
    ruin_count = 0
    target_count = 0

    for sim in range(simulations):
        equity = starting_equity
        peak = starting_equity
        max_dd = 0.0
        ruined = False

        # Draw all trades at once for this path
        trade_indices = rng.integers(0, len(returns), size=total_trades_per_path)

        for idx in trade_indices:
            if ruined:
                break

            pnl_frac = returns[idx]
            # Position size: 10% of current equity
            position = equity * 0.10
            pnl = position * pnl_frac
            equity += pnl

            # Track drawdown
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

            # Check ruin
            if equity < equity_floor:
                ruined = True

        final_equities[sim] = equity
        max_drawdowns[sim] = max_dd
        if ruined:
            ruin_count += 1
        if equity >= equity_target:
            target_count += 1

    # Compute statistics
    annual_returns = (final_equities / starting_equity - 1) * 100  # as percentage

    # Kelly optimal calculation
    win_trades = returns[returns > 0]
    loss_trades = returns[returns < 0]
    if len(win_trades) > 0 and len(loss_trades) > 0:
        win_rate = len(win_trades) / len(returns)
        avg_win = np.mean(win_trades)
        avg_loss = abs(np.mean(loss_trades))
        if avg_loss > 0:
            optimal_kelly = win_rate - (1 - win_rate) / (avg_win / avg_loss)
        else:
            optimal_kelly = win_rate
    else:
        optimal_kelly = 0.0

    # Current kelly from config (read if available)
    current_kelly = 0.10  # Default position size

    return MonteCarloResult(
        simulations=simulations,
        trading_days=trading_days,
        starting_equity=starting_equity,
        trade_count_input=len(trade_returns),
        probability_of_ruin=round(ruin_count / simulations, 4),
        probability_of_target=round(target_count / simulations, 4),
        median_final_equity=round(float(np.median(final_equities)), 2),
        mean_final_equity=round(float(np.mean(final_equities)), 2),
        equity_p5=round(float(np.percentile(final_equities, 5)), 2),
        equity_p25=round(float(np.percentile(final_equities, 25)), 2),
        equity_p50=round(float(np.percentile(final_equities, 50)), 2),
        equity_p75=round(float(np.percentile(final_equities, 75)), 2),
        equity_p95=round(float(np.percentile(final_equities, 95)), 2),
        max_dd_p5=round(float(np.percentile(max_drawdowns, 5)) * 100, 2),
        max_dd_p25=round(float(np.percentile(max_drawdowns, 25)) * 100, 2),
        max_dd_p50=round(float(np.percentile(max_drawdowns, 50)) * 100, 2),
        max_dd_p75=round(float(np.percentile(max_drawdowns, 75)) * 100, 2),
        max_dd_p95=round(float(np.percentile(max_drawdowns, 95)) * 100, 2),
        annual_return_p5=round(float(np.percentile(annual_returns, 5)), 2),
        annual_return_p50=round(float(np.percentile(annual_returns, 50)), 2),
        annual_return_p95=round(float(np.percentile(annual_returns, 95)), 2),
        optimal_kelly=round(max(0, optimal_kelly), 4),
        current_kelly=current_kelly,
        kelly_ratio=round(current_kelly / max(optimal_kelly, 0.001), 2),
    )


# ---------------------------------------------------------------------------
# Capital-constrained Monte Carlo (institutional backtest)
# ---------------------------------------------------------------------------
@dataclass
class ConstrainedMCResult:
    """Result of capital-constrained Monte Carlo simulation."""
    simulations: int
    trading_days: int
    starting_equity: float
    trade_count_input: int
    max_concurrent: int
    kelly_fraction: float

    probability_of_ruin: float
    probability_of_target: float
    median_final_equity: float
    mean_final_equity: float

    equity_p5: float
    equity_p10: float
    equity_p25: float
    equity_p50: float
    equity_p75: float
    equity_p90: float
    equity_p95: float

    max_dd_p5: float
    max_dd_p25: float
    max_dd_p50: float
    max_dd_p75: float
    max_dd_p95: float

    annual_return_p5: float
    annual_return_p50: float
    annual_return_p95: float

    sharpe: float
    cagr_median: float
    avg_trades_per_path: float
    total_cost_drag_pct: float


def run_constrained_simulation(
    trades: List[Dict[str, Any]],
    simulations: int = 10000,
    trading_days: int = 252,
    starting_equity: float = STARTING_EQUITY,
    max_concurrent: int = 3,
    kelly_fraction: float = 0.05,
    costs_per_exchange: Optional[Dict[str, float]] = None,
    fx_rates: Optional[Dict[str, float]] = None,
    equity_floor_pct: float = EQUITY_FLOOR_PCT,
    seed: Optional[int] = None,
) -> ConstrainedMCResult:
    """Run capital-constrained Monte Carlo with realistic position sizing and costs.

    Args:
        trades: List of dicts with keys: net_pnl_pct, exchange, cost_pct, currency, entry_type
        simulations: Number of MC paths
        trading_days: Days to simulate
        starting_equity: Starting capital in GBP
        max_concurrent: Maximum concurrent positions
        kelly_fraction: Kelly fraction cap (e.g., 0.05 = 5% of equity per position)
        costs_per_exchange: Per-exchange round-trip cost as fraction (already in trade net_pnl_pct)
        fx_rates: Currency->GBP conversion rates
        equity_floor_pct: Halt if equity drops below this fraction of starting
        seed: Random seed

    Returns:
        ConstrainedMCResult with comprehensive metrics.
    """
    if not trades or len(trades) < 5:
        return ConstrainedMCResult(
            simulations=0, trading_days=trading_days, starting_equity=starting_equity,
            trade_count_input=len(trades), max_concurrent=max_concurrent,
            kelly_fraction=kelly_fraction,
            probability_of_ruin=1.0, probability_of_target=0.0,
            median_final_equity=0, mean_final_equity=0,
            equity_p5=0, equity_p10=0, equity_p25=0, equity_p50=0,
            equity_p75=0, equity_p90=0, equity_p95=0,
            max_dd_p5=0, max_dd_p25=0, max_dd_p50=0, max_dd_p75=0, max_dd_p95=0,
            annual_return_p5=0, annual_return_p50=0, annual_return_p95=0,
            sharpe=0, cagr_median=0, avg_trades_per_path=0, total_cost_drag_pct=0,
        )

    rng = np.random.default_rng(seed)

    # Extract net PnL percentages (already cost-adjusted from backfill simulator)
    net_returns = np.array([t.get("net_pnl_pct", 0) / 100.0 for t in trades])  # fraction
    cost_pcts = np.array([t.get("cost_pct", 0.3) for t in trades])  # percentage

    equity_floor = starting_equity * equity_floor_pct
    equity_target = starting_equity * EQUITY_TARGET_MULT

    # Estimate trades per day from data
    trades_per_day = max(1.0, len(trades) / max(trading_days, 1))
    # Cap at reasonable level
    trades_per_day = min(trades_per_day, 10.0)
    total_trades_per_path = int(trading_days * trades_per_day)

    final_equities = np.zeros(simulations)
    max_drawdowns = np.zeros(simulations)
    ruin_count = 0
    target_count = 0
    total_trades_executed = 0
    total_cost_accumulated = 0.0

    for sim in range(simulations):
        equity = starting_equity
        peak = starting_equity
        max_dd = 0.0
        ruined = False
        concurrent = 0
        trades_this_path = 0

        # Draw random trade sequence
        indices = rng.integers(0, len(net_returns), size=total_trades_per_path)

        for idx in indices:
            if ruined:
                break

            # Capital constraint: skip if at max concurrent positions
            if concurrent >= max_concurrent:
                # Simulate one position closing before opening new
                concurrent -= 1

            # Position sizing: Kelly fraction of current equity
            position_size = equity * kelly_fraction
            if position_size < 100:  # Below minimum viable trade
                continue

            # Apply trade return (already net of costs)
            pnl = position_size * net_returns[idx]
            equity += pnl
            concurrent += 1
            trades_this_path += 1
            total_cost_accumulated += position_size * cost_pcts[idx] / 100.0

            # Simulate position close (simplified: close after each trade)
            concurrent = max(0, concurrent - 1)

            # Track drawdown
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

            if equity < equity_floor:
                ruined = True

        final_equities[sim] = equity
        max_drawdowns[sim] = max_dd
        total_trades_executed += trades_this_path
        if ruined:
            ruin_count += 1
        if equity >= equity_target:
            target_count += 1

    # Compute statistics
    annual_returns = (final_equities / starting_equity - 1) * 100

    # Sharpe ratio (annualized)
    daily_returns = np.diff(np.insert(final_equities, 0, starting_equity)) / starting_equity
    mean_return = np.mean(annual_returns)
    std_return = np.std(annual_returns)
    sharpe = mean_return / std_return if std_return > 0 else 0

    # CAGR from median
    median_equity = float(np.median(final_equities))
    years = trading_days / 252.0
    cagr = ((median_equity / starting_equity) ** (1 / years) - 1) * 100 if years > 0 and median_equity > 0 else 0

    avg_cost_drag = (total_cost_accumulated / max(total_trades_executed, 1)) / starting_equity * 100

    return ConstrainedMCResult(
        simulations=simulations,
        trading_days=trading_days,
        starting_equity=starting_equity,
        trade_count_input=len(trades),
        max_concurrent=max_concurrent,
        kelly_fraction=kelly_fraction,
        probability_of_ruin=round(ruin_count / simulations, 4),
        probability_of_target=round(target_count / simulations, 4),
        median_final_equity=round(median_equity, 2),
        mean_final_equity=round(float(np.mean(final_equities)), 2),
        equity_p5=round(float(np.percentile(final_equities, 5)), 2),
        equity_p10=round(float(np.percentile(final_equities, 10)), 2),
        equity_p25=round(float(np.percentile(final_equities, 25)), 2),
        equity_p50=round(float(np.percentile(final_equities, 50)), 2),
        equity_p75=round(float(np.percentile(final_equities, 75)), 2),
        equity_p90=round(float(np.percentile(final_equities, 90)), 2),
        equity_p95=round(float(np.percentile(final_equities, 95)), 2),
        max_dd_p5=round(float(np.percentile(max_drawdowns, 5)) * 100, 2),
        max_dd_p25=round(float(np.percentile(max_drawdowns, 25)) * 100, 2),
        max_dd_p50=round(float(np.percentile(max_drawdowns, 50)) * 100, 2),
        max_dd_p75=round(float(np.percentile(max_drawdowns, 75)) * 100, 2),
        max_dd_p95=round(float(np.percentile(max_drawdowns, 95)) * 100, 2),
        annual_return_p5=round(float(np.percentile(annual_returns, 5)), 2),
        annual_return_p50=round(float(np.percentile(annual_returns, 50)), 2),
        annual_return_p95=round(float(np.percentile(annual_returns, 95)), 2),
        sharpe=round(sharpe, 2),
        cagr_median=round(cagr, 2),
        avg_trades_per_path=round(total_trades_executed / max(simulations, 1), 1),
        total_cost_drag_pct=round(avg_cost_drag, 4),
    )


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------
def format_report(result: MonteCarloResult) -> str:
    """Format Monte Carlo results for Telegram."""
    ruin_icon = "\u2705" if result.probability_of_ruin < 0.05 else "\U0001f534"
    target_icon = "\u2705" if result.probability_of_target > 0.50 else "\U0001f7e1"

    lines = [
        f"\U0001f3b2 <b>MONTE CARLO SIMULATION</b>",
        f"",
        f"Paths: {result.simulations:,} | Days: {result.trading_days} | Trades: {result.trade_count_input}",
        f"Starting: {result.starting_equity:,.0f} GBP",
        f"",
        f"{ruin_icon} <b>P(ruin): {result.probability_of_ruin:.1%}</b> (equity &lt; {result.starting_equity * EQUITY_FLOOR_PCT:,.0f})",
        f"{target_icon} <b>P(2x): {result.probability_of_target:.1%}</b> (equity &gt;= {result.starting_equity * EQUITY_TARGET_MULT:,.0f})",
        f"",
        f"<b>Final Equity Distribution:</b>",
        f"  5th: {result.equity_p5:,.0f} | 25th: {result.equity_p25:,.0f}",
        f"  <b>50th: {result.equity_p50:,.0f}</b> | 75th: {result.equity_p75:,.0f}",
        f"  95th: {result.equity_p95:,.0f}",
        f"",
        f"<b>Max Drawdown Distribution:</b>",
        f"  5th: {result.max_dd_p5:.1f}% | 50th: {result.max_dd_p50:.1f}%",
        f"  95th: {result.max_dd_p95:.1f}%",
        f"",
        f"<b>Annual Return:</b>",
        f"  5th: {result.annual_return_p5:+.1f}% | 50th: {result.annual_return_p50:+.1f}%",
        f"  95th: {result.annual_return_p95:+.1f}%",
        f"",
        f"<b>Kelly Analysis:</b>",
        f"  Optimal: {result.optimal_kelly:.1%} | Current: {result.current_kelly:.1%}",
        f"  Ratio: {result.kelly_ratio:.1f}x",
    ]

    # Verdict
    if result.probability_of_ruin < 0.05 and result.probability_of_target > 0.30:
        lines.append(f"\n\u2705 <b>VERDICT: SAFE TO TRADE</b>")
    elif result.probability_of_ruin < 0.10:
        lines.append(f"\n\U0001f7e1 <b>VERDICT: MARGINAL — reduce position size</b>")
    else:
        lines.append(f"\n\U0001f534 <b>VERDICT: DO NOT TRADE — ruin risk too high</b>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [MonteCarlo] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Monte Carlo Risk-of-Ruin (N9b)")
    parser.add_argument("--trades-file", type=str, help="Path to trades JSON file")
    parser.add_argument("--wal", action="store_true", help="Use live WAL trades")
    parser.add_argument("--simulations", type=int, default=DEFAULT_SIMULATIONS, help="Number of simulations")
    parser.add_argument("--days", type=int, default=DEFAULT_TRADING_DAYS, help="Trading days to simulate")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--send-telegram", action="store_true", help="Send report via Telegram")
    parser.add_argument("--seed", type=int, help="Random seed for reproducibility")
    args = parser.parse_args()

    # Load trades
    if args.wal:
        trade_returns = load_trades_from_wal()
    else:
        trade_returns = load_trades_from_backtest(args.trades_file)

    if not trade_returns:
        print("ERROR: No trade data found. Run N9a backtest first or use --wal for live trades.")
        return

    # Run simulation
    result = run_simulation(
        trade_returns,
        simulations=args.simulations,
        trading_days=args.days,
        seed=args.seed,
    )

    # Save report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"monte_carlo_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    try:
        from dataclasses import asdict
        with open(report_path, "w") as f:
            json.dump(asdict(result), f, indent=2)
        log.info("Report saved: %s", report_path)
    except OSError as e:
        log.error("Failed to save report: %s", e)

    # Output
    if args.json:
        from dataclasses import asdict
        print(json.dumps(asdict(result), indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"  MONTE CARLO RISK-OF-RUIN SIMULATION (N9b)")
        print(f"{'='*60}")
        print(f"  Input trades: {result.trade_count_input}")
        print(f"  Simulations: {result.simulations:,}")
        print(f"  Trading days: {result.trading_days}")
        print(f"  Starting equity: {result.starting_equity:,.0f} GBP")
        print()
        print(f"  P(ruin):   {result.probability_of_ruin:.2%} (equity < {result.starting_equity * EQUITY_FLOOR_PCT:,.0f})")
        print(f"  P(target): {result.probability_of_target:.2%} (equity >= {result.starting_equity * EQUITY_TARGET_MULT:,.0f})")
        print()
        print(f"  Final Equity (percentiles):")
        print(f"    5th: {result.equity_p5:>10,.0f} | 25th: {result.equity_p25:>10,.0f}")
        print(f"   50th: {result.equity_p50:>10,.0f} | 75th: {result.equity_p75:>10,.0f}")
        print(f"   95th: {result.equity_p95:>10,.0f}")
        print()
        print(f"  Max Drawdown (percentiles):")
        print(f"    5th: {result.max_dd_p5:>6.1f}% | 50th: {result.max_dd_p50:>6.1f}% | 95th: {result.max_dd_p95:>6.1f}%")
        print()
        print(f"  Annual Return (percentiles):")
        print(f"    5th: {result.annual_return_p5:>+7.1f}% | 50th: {result.annual_return_p50:>+7.1f}% | 95th: {result.annual_return_p95:>+7.1f}%")
        print()
        print(f"  Kelly: optimal={result.optimal_kelly:.1%} current={result.current_kelly:.1%} ratio={result.kelly_ratio:.1f}x")

    if args.send_telegram:
        try:
            from python_brain.ouroboros.telegram_notify import send_message
            msg = format_report(result)
            send_message(msg)
            log.info("Report sent via Telegram")
        except Exception as e:
            log.error("Telegram send failed: %s", e)


if __name__ == "__main__":
    main()
