"""Analytics Pack -- Friction-adjusted expectancy, comparison tables, data quality.

Usage:  python3 -m python_brain.ouroboros.analytics_pack [--date YYYY-MM-DD] [--json]
Library: from python_brain.ouroboros.analytics_pack import run_analytics_pack
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Path setup (mirrors nightly_v6.py)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AnalyticsPack] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("analytics_pack")

_INVERSE_RE = re.compile(
    r"^(\d)[SU]|QQQS|3USS|SDS|SPXS|SQQQ|TECS|SDOW|TZA|SRTY|YANG|FNGD|WEBS|FAZ",
    re.IGNORECASE,
)
_LEVER_RE = re.compile(r"(?:^|[A-Z])(\d)(?=[A-Z])|(\d)[xX]")


def _classify_leverage(symbol: str) -> str:
    """Derive leverage class from symbol (e.g. '3x', '-1x', '5x')."""
    sym = symbol.upper().replace(".L", "")

    # Dynamic leverage lookup from contracts.toml
    from python_brain.ouroboros.contract_loader import load_leverage_map
    lev_map = load_leverage_map()
    if symbol in lev_map:
        lev = lev_map[symbol]
        is_inv = bool(_INVERSE_RE.search(sym))
        return f"{'-' if is_inv else ''}{lev}x"

    # Fallback: explicit well-known products (checked before regex to avoid false positives)
    # Long leveraged
    if "QQQ5" in sym or "5SPY" in sym or "SP5L" in sym:
        return "5x"
    if "QQQ3" in sym or "3LUS" in sym or "3SEM" in sym or "GPT3" in sym:
        return "3x"
    if "NVD3" in sym or "TSL3" in sym or "TSM3" in sym:
        return "3x"
    if "MU2" in sym:
        return "2x"
    # Inverse leveraged
    if "3USS" in sym:
        return "-3x"
    if "QQQS" in sym:
        return "-1x"

    # Generic classification via regex
    is_inverse = bool(_INVERSE_RE.search(sym))
    multiplier = 1
    m = _LEVER_RE.search(sym)
    if m:
        digit = int(m.group(1) or m.group(2))
        if digit in (2, 3, 5):
            multiplier = digit
    return f"{'-' if is_inverse else ''}{multiplier}x"


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Division that never raises ZeroDivisionError."""
    if denominator == 0.0:
        return default
    return numerator / denominator


# ============================================================================
# Part 1: Friction-Adjusted Expectancy
# ============================================================================

def compute_friction_adjusted_expectancy(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute net expectancy per ticker/session/exchange/leverage after all costs."""
    if not trades:
        return {
            "per_ticker_expectancy": {},
            "per_session_expectancy": {},
            "per_exchange_expectancy": {},
            "per_leverage_expectancy": {},
            "overall_net_expectancy": 0.0,
            "friction_ratio": 0.0,
        }

    # Per-trade edge decomposition
    enriched: List[Dict[str, Any]] = []
    for t in trades:
        entry_price = t.get("entry_price", 0.0) or 0.0
        qty = t.get("qty", 1) or 1
        entry_notional = max(entry_price * qty, 1e-9)

        gross_pnl = t.get("gross_pnl", t.get("final_pnl", 0.0))
        commission = t.get("total_commission", 0.0)
        spread_entry = t.get("spread_at_entry_pct", 0.0) / 100.0  # convert pct to frac
        spread_exit = t.get("spread_at_exit_pct", 0.0) / 100.0

        gross_edge = gross_pnl / entry_notional
        spread_drag = spread_entry + spread_exit
        commission_drag = commission / entry_notional
        net_edge = gross_edge - spread_drag - commission_drag

        symbol = t.get("symbol", t.get("ticker", "UNKNOWN"))
        enriched.append({
            "symbol": symbol,
            "session_phase": t.get("entry_session_phase", "unknown") or "unknown",
            "exchange": t.get("exchange", "unknown") or "unknown",
            "strategy": t.get("strategy", "unknown") or "unknown",
            "leverage_class": _classify_leverage(symbol),
            "gross_edge": gross_edge,
            "spread_drag": spread_drag,
            "commission_drag": commission_drag,
            "net_edge": net_edge,
            "gross_pnl": gross_pnl,
            "total_friction": (spread_drag + commission_drag) * entry_notional,
        })

    def _aggregate(group: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = len(group)
        avg_net = mean(e["net_edge"] for e in group) if n else 0.0
        avg_gross = mean(e["gross_edge"] for e in group) if n else 0.0
        total_friction = sum(e["total_friction"] for e in group)
        total_gross = sum(abs(e["gross_pnl"]) for e in group)
        fr = _safe_div(total_friction, total_gross)
        wins = sum(1 for e in group if e["net_edge"] > 0)
        return {
            "n_trades": n,
            "avg_net_edge": round(avg_net, 6),
            "avg_gross_edge": round(avg_gross, 6),
            "avg_spread_drag": round(mean(e["spread_drag"] for e in group), 6) if n else 0.0,
            "avg_commission_drag": round(mean(e["commission_drag"] for e in group), 6) if n else 0.0,
            "friction_ratio": round(fr, 4),
            "win_rate_net": round(_safe_div(wins, n), 4),
        }

    # Group and aggregate
    def _group_by(key: str) -> Dict[str, Dict[str, Any]]:
        buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for e in enriched:
            buckets[e[key]].append(e)
        return {k: _aggregate(v) for k, v in sorted(buckets.items())}

    per_ticker = _group_by("symbol")
    per_session = _group_by("session_phase")
    per_exchange = _group_by("exchange")
    per_leverage = _group_by("leverage_class")

    # Overall
    total_friction = sum(e["total_friction"] for e in enriched)
    total_gross_abs = sum(abs(e["gross_pnl"]) for e in enriched)
    overall_net = mean(e["net_edge"] for e in enriched)
    friction_ratio = _safe_div(total_friction, total_gross_abs)

    return {
        "per_ticker_expectancy": per_ticker,
        "per_session_expectancy": per_session,
        "per_exchange_expectancy": per_exchange,
        "per_leverage_expectancy": per_leverage,
        "overall_net_expectancy": round(overall_net, 6),
        "friction_ratio": round(friction_ratio, 4),
    }


# ============================================================================
# Part 2: Comparison Tables
# ============================================================================

def generate_comparison_tables(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare performance across session/exchange/leverage/strategy dimensions."""
    if not trades:
        return {
            "session_table": {},
            "exchange_table": {},
            "leverage_table": {},
            "strategy_table": {},
        }

    def _compute_row(group: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = len(group)
        if n == 0:
            return {}
        wins = sum(1 for t in group if t.get("final_pnl", t.get("gross_pnl", 0.0)) > 0)
        pnls = [t.get("final_pnl", t.get("gross_pnl", 0.0)) for t in group]
        hold_times = [t.get("hold_time_mins", 0) for t in group]
        maes = [abs(t.get("mae", 0.0)) for t in group]
        mfes = [abs(t.get("mfe", 0.0)) for t in group]

        # Friction ratio for this group
        total_friction = 0.0
        total_gross_abs = 0.0
        for t in group:
            ep = t.get("entry_price", 0.0) or 0.0
            qty = t.get("qty", 1) or 1
            notional = max(ep * qty, 1e-9)
            se = t.get("spread_at_entry_pct", 0.0) / 100.0
            sx = t.get("spread_at_exit_pct", 0.0) / 100.0
            comm = t.get("total_commission", 0.0)
            total_friction += (se + sx) * notional + comm
            total_gross_abs += abs(t.get("gross_pnl", t.get("final_pnl", 0.0)))

        return {
            "n_trades": n,
            "win_rate": round(_safe_div(wins, n), 4),
            "avg_pnl": round(mean(pnls), 4),
            "total_pnl": round(sum(pnls), 4),
            "avg_hold_time_mins": round(mean(hold_times), 1),
            "avg_mae": round(mean(maes), 4),
            "avg_mfe": round(mean(mfes), 4),
            "friction_ratio": round(_safe_div(total_friction, total_gross_abs), 4),
        }

    def _build_table(key_fn) -> Dict[str, Dict[str, Any]]:
        buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for t in trades:
            k = key_fn(t)
            buckets[k].append(t)
        return {k: _compute_row(v) for k, v in sorted(buckets.items()) if v}

    session_table = _build_table(
        lambda t: t.get("entry_session_phase", "unknown") or "unknown"
    )
    exchange_table = _build_table(
        lambda t: t.get("exchange", "unknown") or "unknown"
    )
    leverage_table = _build_table(
        lambda t: _classify_leverage(t.get("symbol", t.get("ticker", "UNKNOWN")))
    )
    strategy_table = _build_table(
        lambda t: t.get("strategy", "unknown") or "unknown"
    )

    return {
        "session_table": session_table,
        "exchange_table": exchange_table,
        "leverage_table": leverage_table,
        "strategy_table": strategy_table,
    }


# ============================================================================
# Part 3: Data Quality Scorecard
# ============================================================================

def compute_data_quality_scorecard(
    trades: List[Dict[str, Any]],
    available_tickers: List[str],
) -> Dict[str, Any]:
    """Score data completeness per ticker (0-100, five 20-pt components)."""
    # Group trades by ticker
    by_ticker: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in trades:
        sym = t.get("symbol", t.get("ticker", "UNKNOWN"))
        by_ticker[sym].append(t)

    # Build the full universe: available + anything traded
    universe = set(available_tickers)
    universe.update(by_ticker.keys())

    per_ticker: Dict[str, Dict[str, Any]] = {}
    missing_fields: Dict[str, int] = defaultdict(int)  # field -> count of defaults

    for sym in sorted(universe):
        ticker_trades = by_ticker.get(sym, [])
        n = len(ticker_trades)

        if n == 0:
            per_ticker[sym] = {"score": 0, "n_trades": 0, "breakdown": {
                "trade_volume": 0, "spread_data": 0, "mae_mfe": 0,
                "session_phase": 0, "confidence": 0,
            }}
            continue

        # Component 1: trade volume (20 pts if >= 5 trades)
        c_volume = 20 if n >= 5 else int(20 * (n / 5))

        # Component 2: spread data completeness
        has_spread = sum(1 for t in ticker_trades if t.get("spread_at_entry_pct", 0.0) > 0)
        missing_fields["spread_at_entry_pct"] += (n - has_spread)
        c_spread = int(20 * _safe_div(has_spread, n))

        # Component 3: MAE/MFE tracking
        has_mae_mfe = sum(
            1 for t in ticker_trades
            if t.get("mae", 0.0) != 0.0 and t.get("mfe", 0.0) != 0.0
        )
        missing_fields["mae"] += sum(1 for t in ticker_trades if t.get("mae", 0.0) == 0.0)
        missing_fields["mfe"] += sum(1 for t in ticker_trades if t.get("mfe", 0.0) == 0.0)
        c_mae_mfe = int(20 * _safe_div(has_mae_mfe, n))

        # Component 4: session phase
        has_phase = sum(
            1 for t in ticker_trades
            if (t.get("entry_session_phase", "") or "").strip() != ""
        )
        missing_fields["entry_session_phase"] += (n - has_phase)
        c_phase = int(20 * _safe_div(has_phase, n))

        # Component 5: confidence
        has_conf = sum(1 for t in ticker_trades if t.get("confidence", 0.0) > 0)
        missing_fields["confidence"] += (n - has_conf)
        c_conf = int(20 * _safe_div(has_conf, n))

        score = c_volume + c_spread + c_mae_mfe + c_phase + c_conf
        per_ticker[sym] = {
            "score": score,
            "n_trades": n,
            "breakdown": {
                "trade_volume": c_volume,
                "spread_data": c_spread,
                "mae_mfe": c_mae_mfe,
                "session_phase": c_phase,
                "confidence": c_conf,
            },
        }

    # Aggregates
    traded_tickers = [sym for sym, v in per_ticker.items() if v["n_trades"] > 0]
    total_coverage = _safe_div(len(traded_tickers), len(universe)) if universe else 0.0
    traded_scores = [per_ticker[sym]["score"] for sym in traded_tickers]
    avg_completeness = mean(traded_scores) if traded_scores else 0.0
    worst_tickers = sorted(
        [sym for sym in traded_tickers if per_ticker[sym]["score"] < 40],
        key=lambda s: per_ticker[s]["score"],
    )

    return {
        "per_ticker_scorecard": per_ticker,
        "total_coverage": round(total_coverage, 4),
        "avg_completeness": round(avg_completeness, 2),
        "worst_tickers": worst_tickers,
        "missing_fields_summary": {k: v for k, v in missing_fields.items() if v > 0},
    }


# ============================================================================
# Integration: run_analytics_pack
# ============================================================================

def run_analytics_pack(
    trades: List[Dict[str, Any]],
    available_tickers: List[str],
) -> Dict[str, Any]:
    """Run all three analytics modules and return combined dict."""
    log.info("Running analytics pack on %d trades, %d available tickers",
             len(trades), len(available_tickers))

    friction = compute_friction_adjusted_expectancy(trades)
    tables = generate_comparison_tables(trades)
    scorecard = compute_data_quality_scorecard(trades, available_tickers)

    # Log key metrics
    log.info("Overall net expectancy: %.4f%%  |  Friction ratio: %.2f%%",
             friction["overall_net_expectancy"] * 100,
             friction["friction_ratio"] * 100)

    if tables["session_table"]:
        best_session = max(tables["session_table"].items(),
                           key=lambda kv: kv[1].get("avg_pnl", 0))
        log.info("Best session phase: %s (avg PnL=%.4f, WR=%.0f%%)",
                 best_session[0], best_session[1]["avg_pnl"],
                 best_session[1]["win_rate"] * 100)

    if tables["leverage_table"]:
        best_lev = max(tables["leverage_table"].items(),
                       key=lambda kv: kv[1].get("avg_pnl", 0))
        log.info("Best leverage class: %s (avg PnL=%.4f, WR=%.0f%%)",
                 best_lev[0], best_lev[1]["avg_pnl"],
                 best_lev[1]["win_rate"] * 100)

    log.info("Data quality: coverage=%.0f%%  avg_completeness=%.1f  worst=%s",
             scorecard["total_coverage"] * 100,
             scorecard["avg_completeness"],
             ", ".join(scorecard["worst_tickers"][:5]) or "(none)")

    return {
        "friction_expectancy": friction,
        "comparison_tables": tables,
        "data_quality_scorecard": scorecard,
    }


# ============================================================================
# CLI: standalone execution
# ============================================================================

from python_brain.ouroboros.contract_loader import load_lse_symbols
PRIMARY_TICKERS = load_lse_symbols()


def _load_trades_from_wal(date_str: str) -> List[Dict[str, Any]]:
    """Load PositionClosed events from WAL (same pattern as nightly_v6)."""
    candidates = [WAL_DIR / "current.ndjson", WAL_DIR / f"{date_str}.ndjson",
                  WAL_DIR / f"wal_{date_str}.ndjson"]
    archive_dir = WAL_DIR / "archive"
    if archive_dir.exists():
        candidates.extend(f for f in sorted(archive_dir.glob("*.ndjson"))
                          if f not in candidates)
    _FIELDS = ["symbol", "ticker_id", "final_pnl", "gross_pnl", "total_commission",
               "spread_at_entry_pct", "spread_at_exit_pct", "entry_price", "exit_price",
               "qty", "exchange", "strategy", "entry_session_phase", "confidence",
               "mae", "mfe", "hold_time_mins", "highest_rung"]
    _DEFAULTS = {"symbol": "UNKNOWN", "ticker_id": -1, "qty": 1,
                 "strategy": "Unclassified"}
    trades: List[Dict[str, Any]] = []
    for wp in candidates:
        if not wp.exists():
            continue
        try:
            with open(wp) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    pc = ev.get("payload", {}).get("PositionClosed")
                    if pc is None:
                        continue
                    trades.append({k: pc.get(k, _DEFAULTS.get(k, 0.0)) for k in _FIELDS})
        except Exception as e:
            log.warning("Error reading %s: %s", wp, e)
    log.info("Loaded %d trades from WAL for %s", len(trades), date_str)
    return trades


def _print_results(result: Dict[str, Any]) -> None:
    """Pretty-print analytics pack results to stdout."""
    fe, ct, dq = (result["friction_expectancy"], result["comparison_tables"],
                  result["data_quality_scorecard"])
    sep = "=" * 72
    print(f"{sep}\n  ANALYTICS PACK\n{sep}")
    print(f"  Net expectancy: {fe['overall_net_expectancy']*100:+.4f}%  "
          f"Friction: {fe['friction_ratio']*100:.2f}%")
    # Comparison tables
    for label, tbl in [("SESSION", ct["session_table"]), ("EXCHANGE", ct["exchange_table"]),
                       ("LEVERAGE", ct["leverage_table"]), ("STRATEGY", ct["strategy_table"])]:
        if not tbl:
            continue
        print(f"\n  {label}:")
        print(f"  {'Dim':<18s} {'N':>5s} {'WR':>5s} {'AvgPnL':>9s} {'Hold':>6s} {'Fric':>6s}")
        for dim, r in tbl.items():
            print(f"  {dim:<18s} {r['n_trades']:>5d} {r['win_rate']:>4.0%} "
                  f"{r['avg_pnl']:>+9.4f} {r['avg_hold_time_mins']:>5.0f}m "
                  f"{r['friction_ratio']:>5.1%}")
    # Data quality
    print(f"\n  DATA QUALITY: coverage={dq['total_coverage']:.0%}  "
          f"avg={dq['avg_completeness']:.0f}/100  "
          f"worst={', '.join(dq['worst_tickers'][:5]) or 'none'}")
    if dq["missing_fields_summary"]:
        for fld, cnt in sorted(dq["missing_fields_summary"].items(), key=lambda x: -x[1]):
            print(f"    {fld:<25s} {cnt:>5d} default")
    print(sep)


def main():
    """CLI entry point."""
    ap = argparse.ArgumentParser(description="AEGIS V2 Analytics Pack")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args()
    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trades = _load_trades_from_wal(date_str)
    result = run_analytics_pack(trades, PRIMARY_TICKERS)
    print(json.dumps(result, indent=2, default=str)) if args.json else _print_results(result)


if __name__ == "__main__":
    main()
