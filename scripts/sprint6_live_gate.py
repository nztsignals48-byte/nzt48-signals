#!/usr/bin/env python3
"""
NZT-48 Sprint 6 Live Trading Gate — Romano & Wolf (2005) StepM
================================================================
Mandate 6: Statistical gate for live capital deployment.

With 20 strategies, selection bias is severe.
Harvey, Liu & Zhu (2016): t ≥ 3.0 for single-strategy testing.
Romano & Wolf (2005) Bonferroni correction for N=20: t ≥ 4.3.

Gate criteria for live capital approval:
  1. t-stat (cost-adjusted Sharpe) ≥ 4.3   [Bonferroni-corrected, N=20]
  2. MTRL ≥ 63 trading days paper trades
  3. Cost-adjusted Sharpe ≥ 1.5             [NOT gross Sharpe]
  4. Max drawdown < 8%
  5. Holdout t-stat ≥ 3.0                   [last 30% of MTRL, out-of-sample]
  6. Consecutive profitable weeks ≥ 3
  7. Win rate (rolling 50) ≥ 50%
  8. No circuit breaker fires in MTRL period
  9. Profit factor ≥ 1.3
  10. Max consecutive losses ≤ 4

NOT ONE OF THESE IS NEGOTIABLE. 9/10 IS NOT ENOUGH.

References:
  - Romano & Wolf (2005) "Stepwise Multiple Testing", Econometrica
  - Harvey, Liu & Zhu (2016) "...Cross-Section of Expected Returns", RFS
  - Bailey & De Prado (2014) "The Deflated Sharpe Ratio"

Usage:
  python3 scripts/sprint6_live_gate.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.quant_math.dsr import calculate_deflated_sharpe


def load_outcomes(path: str = "data/outcomes.jsonl") -> list[dict]:
    """Load trade outcomes from JSONL file."""
    outcomes = []
    p = Path(path)
    if not p.exists():
        print(f"[ERROR] Outcomes file not found: {path}")
        return outcomes
    with open(p, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                outcomes.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return outcomes


def extract_returns(outcomes: list[dict]) -> np.ndarray:
    """Extract R-multiples (cost-adjusted returns proxy) from outcomes."""
    r_multiples = []
    for o in outcomes:
        r = o.get("r_multiple")
        if r is not None:
            try:
                r_multiples.append(float(r))
            except (TypeError, ValueError):
                continue
    return np.array(r_multiples, dtype=np.float64)


def compute_sharpe(returns: np.ndarray) -> float:
    """Annualised Sharpe ratio (assuming daily returns, 252 trading days)."""
    if len(returns) < 2:
        return 0.0
    mean_r = float(np.mean(returns))
    std_r = float(np.std(returns, ddof=1))
    if std_r <= 0:
        return 0.0
    return (mean_r / std_r) * np.sqrt(252)


def compute_t_stat(returns: np.ndarray) -> float:
    """t-statistic of mean return."""
    if len(returns) < 2:
        return 0.0
    mean_r = float(np.mean(returns))
    std_r = float(np.std(returns, ddof=1))
    n = len(returns)
    if std_r <= 0:
        return 0.0
    return (mean_r / (std_r / np.sqrt(n)))


def compute_max_drawdown(returns: np.ndarray) -> float:
    """Maximum drawdown from cumulative returns."""
    if len(returns) == 0:
        return 0.0
    cumulative = np.cumsum(returns)
    peak = np.maximum.accumulate(cumulative)
    drawdown = peak - cumulative
    return float(np.max(drawdown)) if len(drawdown) > 0 else 0.0


def compute_profit_factor(returns: np.ndarray) -> float:
    """Gross profit / gross loss."""
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    gross_win = float(np.sum(wins)) if len(wins) > 0 else 0.0
    gross_loss = abs(float(np.sum(losses))) if len(losses) > 0 else 0.001
    return gross_win / gross_loss if gross_loss > 0 else 0.0


def compute_max_consecutive_losses(returns: np.ndarray) -> int:
    """Maximum streak of consecutive losing trades."""
    max_streak = 0
    current_streak = 0
    for r in returns:
        if r < 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
    return max_streak


def compute_win_rate_rolling(returns: np.ndarray, window: int = 50) -> float:
    """Win rate over the most recent `window` trades."""
    if len(returns) == 0:
        return 0.0
    recent = returns[-window:]
    wins = np.sum(recent > 0)
    decisive = np.sum(recent != 0)
    return float(wins / decisive) if decisive > 0 else 0.0


def compute_consecutive_profitable_weeks(outcomes: list[dict]) -> int:
    """Count consecutive profitable weeks (most recent streak)."""
    # Group outcomes by ISO week
    weekly_pnl: dict[str, float] = {}
    for o in outcomes:
        ts = o.get("exit_time") or o.get("timestamp") or o.get("date")
        r = o.get("r_multiple", 0)
        if ts is None:
            continue
        try:
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            else:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            week_key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
            weekly_pnl[week_key] = weekly_pnl.get(week_key, 0.0) + float(r)
        except Exception:
            continue

    if not weekly_pnl:
        return 0

    # Count consecutive profitable weeks from the most recent
    sorted_weeks = sorted(weekly_pnl.keys(), reverse=True)
    streak = 0
    for week in sorted_weeks:
        if weekly_pnl[week] > 0:
            streak += 1
        else:
            break
    return streak


def run_gate(outcomes_path: str = "data/outcomes.jsonl") -> dict:
    """Run the full Go/No-Go gate evaluation.

    Returns dict with each criterion, its value, threshold, and pass/fail.
    """
    outcomes = load_outcomes(outcomes_path)
    returns = extract_returns(outcomes)
    n_trades = len(returns)

    # Split for holdout (last 30%)
    holdout_start = int(n_trades * 0.7)
    holdout_returns = returns[holdout_start:] if holdout_start > 0 else np.array([])

    # Compute metrics
    sharpe = compute_sharpe(returns)
    t_stat_full = compute_t_stat(returns)
    t_stat_holdout = compute_t_stat(holdout_returns) if len(holdout_returns) > 2 else 0.0
    max_dd = compute_max_drawdown(returns)
    profit_factor = compute_profit_factor(returns)
    max_consec_losses = compute_max_consecutive_losses(returns)
    win_rate_50 = compute_win_rate_rolling(returns, 50)
    consec_profit_weeks = compute_consecutive_profitable_weeks(outcomes)

    # Phase 23: Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014)
    # Tests if the estimated Sharpe is real, not data mining artifact
    dsr_p_value = calculate_deflated_sharpe(returns, num_trials=20) if len(returns) >= 30 else 0.0

    # Estimate trading days (unique dates in outcomes)
    trading_days = set()
    for o in outcomes:
        ts = o.get("exit_time") or o.get("timestamp") or o.get("date")
        if ts:
            try:
                if isinstance(ts, (int, float)):
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                else:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                trading_days.add(dt.strftime("%Y-%m-%d"))
            except Exception:
                continue
    mtrl_days = len(trading_days)

    # Circuit breaker fires (check for any in outcomes metadata)
    circuit_breaker_fires = sum(
        1 for o in outcomes
        if o.get("exit_reason") == "CIRCUIT_BREAKER_RED"
        or o.get("failure_category") == "CIRCUIT_BREAKER"
    )

    # Build gate results
    criteria = [
        {
            "id": 1,
            "name": "t-stat (Bonferroni N=20)",
            "value": round(t_stat_full, 2),
            "threshold": 4.3,
            "pass": t_stat_full >= 4.3,
            "note": "Romano & Wolf (2005) StepM correction",
        },
        {
            "id": 2,
            "name": "MTRL (trading days)",
            "value": mtrl_days,
            "threshold": 63,
            "pass": mtrl_days >= 63,
            "note": "Minimum track record length",
        },
        {
            "id": 3,
            "name": "Cost-adjusted Sharpe",
            "value": round(sharpe, 2),
            "threshold": 1.5,
            "pass": sharpe >= 1.5,
            "note": "NOT gross Sharpe — after slippage + commission",
        },
        {
            "id": 4,
            "name": "Max drawdown",
            "value": round(max_dd * 100, 2) if max_dd < 1 else round(max_dd, 2),
            "threshold": 8.0,
            "pass": (max_dd * 100 if max_dd < 1 else max_dd) < 8.0,
            "note": "% of equity",
        },
        {
            "id": 5,
            "name": "Holdout t-stat (last 30%)",
            "value": round(t_stat_holdout, 2),
            "threshold": 3.0,
            "pass": t_stat_holdout >= 3.0,
            "note": "Out-of-sample validation",
        },
        {
            "id": 6,
            "name": "Consecutive profitable weeks",
            "value": consec_profit_weeks,
            "threshold": 3,
            "pass": consec_profit_weeks >= 3,
            "note": "Most recent streak",
        },
        {
            "id": 7,
            "name": "Win rate (rolling 50)",
            "value": round(win_rate_50 * 100, 1),
            "threshold": 50.0,
            "pass": win_rate_50 >= 0.50,
            "note": "% of last 50 decisive trades",
        },
        {
            "id": 8,
            "name": "Circuit breaker fires",
            "value": circuit_breaker_fires,
            "threshold": 0,
            "pass": circuit_breaker_fires == 0,
            "note": "Zero tolerance in MTRL period",
        },
        {
            "id": 9,
            "name": "Profit factor",
            "value": round(profit_factor, 2),
            "threshold": 1.3,
            "pass": profit_factor >= 1.3,
            "note": "Gross profit / gross loss",
        },
        {
            "id": 10,
            "name": "Max consecutive losses",
            "value": max_consec_losses,
            "threshold": 4,
            "pass": max_consec_losses <= 4,
            "note": "≤4 consecutive losing trades",
        },
        {
            "id": 11,
            "name": "Deflated Sharpe p-value",
            "value": round(dsr_p_value, 3),
            "threshold": 0.95,
            "pass": dsr_p_value >= 0.95,
            "note": "Bailey & De Prado (2014): p≥0.95 = edge is real, not data mining",
        },
    ]

    all_pass = all(c["pass"] for c in criteria)
    passed_count = sum(1 for c in criteria if c["pass"])

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_trades": n_trades,
        "criteria": criteria,
        "all_pass": all_pass,
        "passed_count": passed_count,
        "total_criteria": len(criteria),
        "verdict": "GO ✅ — CLEARED FOR LIVE CAPITAL" if all_pass else f"NO-GO ❌ — {passed_count}/{len(criteria)} criteria met",
    }


def print_report(result: dict) -> None:
    """Pretty-print the Go/No-Go report."""
    print("=" * 72)
    print("  NZT-48 SPRINT 6 LIVE TRADING GATE — Go/No-Go Report")
    print(f"  Generated: {result['timestamp']}")
    print(f"  Total trades analysed: {result['total_trades']}")
    print("=" * 72)
    print()

    for c in result["criteria"]:
        status = "✅ PASS" if c["pass"] else "❌ FAIL"
        print(f"  [{status}]  {c['id']:2d}. {c['name']}")
        print(f"          Value: {c['value']}  |  Threshold: {c['threshold']}  |  {c['note']}")
        print()

    print("=" * 72)
    print(f"  VERDICT: {result['verdict']}")
    print(f"  Criteria passed: {result['passed_count']}/{result['total_criteria']}")
    print("=" * 72)

    if not result["all_pass"]:
        print()
        print("  FAILED CRITERIA:")
        for c in result["criteria"]:
            if not c["pass"]:
                gap = ""
                try:
                    if isinstance(c["value"], (int, float)) and isinstance(c["threshold"], (int, float)):
                        gap = f" (gap: {abs(c['value'] - c['threshold']):.2f})"
                except Exception:
                    pass
                print(f"    - {c['name']}: {c['value']} vs {c['threshold']}{gap}")
        print()
        print("  ACTION: Continue Sprints 1-3 to improve win rate and accumulate MTRL.")


if __name__ == "__main__":
    outcomes_path = sys.argv[1] if len(sys.argv) > 1 else "data/outcomes.jsonl"
    result = run_gate(outcomes_path)
    print_report(result)

    # Also save JSON report
    report_path = Path("data/sprint6_gate_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    print(f"\n  JSON report saved to: {report_path}")
