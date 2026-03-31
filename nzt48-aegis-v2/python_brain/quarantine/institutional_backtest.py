"""Institutional-Grade Backtest Program for AEGIS V2.

Runs the full 11-phase backtest program:
  Phase 1: Baseline full-system backtest (all types, all tickers, with costs)
  Phase 2: Fair entry-type decomposition (A-F standalone)
  Phase 3: Parameter sweeps per type
  Phase 4: Rolling walk-forward validation
  Phase 5: Capital-constrained Monte Carlo
  Phase 6: Ablation testing
  Phase 7: Regime and session analysis
  Phase 8: Portfolio combination tests
  Phase 9: Final verdicts (Keep/Tighten/Shadow/Disable/Abandon)

Usage:
  python3 -m python_brain.ouroboros.institutional_backtest
  python3 -m python_brain.ouroboros.institutional_backtest --days 730 --interval 60m
  python3 -m python_brain.ouroboros.institutional_backtest --quick  # 59d 5m for fast iteration
"""
from __future__ import annotations

import gc
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

from python_brain.ouroboros.backfill_simulator import (
    SimTrade, classify_entries, compute_atr, compute_rsi,
    detect_exchange, fetch_historical_data_parallel, load_yfinance_symbols,
    simulate_chandelier_exit, simulate_ticker, ENTRY_TYPE_CONFIG,
    COSTS_PER_EXCHANGE, FX_TO_GBP, STARTING_EQUITY,
    load_blacklist_from_config, load_universe_file,
)
try:
    from python_brain.ouroboros.monte_carlo import run_constrained_simulation, ConstrainedMCResult
except ImportError:
    try:
        from python_brain.monte_carlo.engine import run_constrained_simulation, ConstrainedMCResult
    except ImportError:
        run_constrained_simulation = None
        ConstrainedMCResult = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Institutional] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("institutional")

ALL_TYPES = ["TypeA", "TypeB", "TypeC", "TypeD", "TypeE", "TypeF"]
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------
def compute_metrics(trades: List[SimTrade], label: str = "") -> Dict[str, Any]:
    """Compute comprehensive metrics for a list of trades."""
    n = len(trades)
    if n == 0:
        return {"label": label, "trades": 0, "note": "NO TRADES"}

    # Filter trades with non-finite values to prevent NaN propagation
    valid = [t for t in trades if math.isfinite(t.net_pnl) and math.isfinite(t.net_pnl_pct)
             and math.isfinite(t.gbp_pnl) and math.isfinite(t.entry_price) and t.entry_price > 0]
    n_valid = len(valid)
    if n_valid == 0:
        return {"label": label, "trades": n, "note": f"ALL {n} TRADES HAD NON-FINITE VALUES"}

    wins = [t for t in valid if t.net_pnl > 0]
    losses = [t for t in valid if t.net_pnl <= 0]
    gross_w = sum(t.net_pnl for t in wins)
    gross_l = abs(sum(t.net_pnl for t in losses))

    wr = len(wins) / n_valid
    pf = gross_w / max(gross_l, 1e-9)
    avg_win = gross_w / max(len(wins), 1)
    avg_loss = gross_l / max(len(losses), 1)
    payoff = avg_win / max(avg_loss, 1e-9)
    expectancy = sum(t.net_pnl for t in valid) / n_valid
    avg_pnl_pct = sum(t.net_pnl_pct for t in valid) / n_valid
    avg_rung = sum(t.rung_achieved for t in valid) / n_valid
    avg_hold = sum(t.hold_bars for t in valid) / n_valid
    total_gbp = sum(t.gbp_pnl for t in valid)

    # Max drawdown (sequential)
    equity = STARTING_EQUITY
    peak_eq = equity
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: (x.date, x.entry_bar)):
        if t.entry_price <= 0 or not math.isfinite(t.entry_price) or not math.isfinite(t.net_pnl):
            continue
        pos_size = equity * 0.05  # 5% Kelly
        shares = max(1, min(int(pos_size / t.entry_price), 100000))
        equity += shares * t.net_pnl
        if not math.isfinite(equity) or equity <= 0:
            equity = 0
            break
        peak_eq = max(peak_eq, equity)
        dd = (peak_eq - equity) / peak_eq if peak_eq > 0 else 0
        max_dd = max(max_dd, dd)

    return {
        "label": label,
        "trades": n,
        "trades_valid": n_valid,
        "trades_filtered": n - n_valid,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(wr, 4),
        "profit_factor": round(pf, 3),
        "expectancy": round(expectancy, 6),
        "avg_win": round(avg_win, 6),
        "avg_loss": round(avg_loss, 6),
        "payoff_ratio": round(payoff, 3),
        "avg_pnl_pct": round(avg_pnl_pct, 4),
        "avg_rung": round(avg_rung, 2),
        "avg_hold_bars": round(avg_hold, 1),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "total_gbp_pnl": round(total_gbp, 2),
        "avg_cost_pct": round(sum(t.cost_pct for t in valid) / n_valid, 3),
    }


def metrics_by_dimension(trades: List[SimTrade], key_fn, label: str = "") -> Dict[str, Dict]:
    """Group trades by a dimension function and compute metrics per group."""
    groups: Dict[str, List[SimTrade]] = defaultdict(list)
    for t in trades:
        k = key_fn(t)
        groups[k].append(t)
    return {k: compute_metrics(v, f"{label}/{k}") for k, v in sorted(groups.items())}


# ---------------------------------------------------------------------------
# Phase 1: Baseline full-system backtest
# ---------------------------------------------------------------------------
def phase_1_baseline(all_trades: List[SimTrade]) -> Dict[str, Any]:
    """Baseline: all types, all tickers, with costs."""
    log.info("PHASE 1: Baseline full-system backtest (%d trades)", len(all_trades))
    result = {
        "overall": compute_metrics(all_trades, "baseline_all"),
        "by_entry_type": metrics_by_dimension(all_trades, lambda t: t.entry_type, "type"),
        "by_exchange": metrics_by_dimension(all_trades, lambda t: t.exchange, "exchange"),
        "by_regime": metrics_by_dimension(all_trades, lambda t: t.regime, "regime"),
        "by_hour": metrics_by_dimension(
            [t for t in all_trades if t.entry_hour >= 0],
            lambda t: f"{t.entry_hour:02d}", "hour"
        ),
    }
    return result


# ---------------------------------------------------------------------------
# Phase 2: Fair entry-type decomposition
# ---------------------------------------------------------------------------
def phase_2_standalone(all_trades: List[SimTrade]) -> Dict[str, Any]:
    """Standalone performance for each entry type."""
    log.info("PHASE 2: Fair entry-type decomposition")
    result = {}
    for etype in ALL_TYPES:
        typed = [t for t in all_trades if t.entry_type == etype]
        result[etype] = {
            "overall": compute_metrics(typed, f"standalone_{etype}"),
            "by_exchange": metrics_by_dimension(typed, lambda t: t.exchange, f"{etype}/exchange"),
            "by_regime": metrics_by_dimension(typed, lambda t: t.regime, f"{etype}/regime"),
            "by_hour": metrics_by_dimension(
                [t for t in typed if t.entry_hour >= 0],
                lambda t: f"{t.entry_hour:02d}", f"{etype}/hour"
            ),
        }
        n = result[etype]["overall"]["trades"]
        wr = result[etype]["overall"].get("win_rate", 0)
        pf = result[etype]["overall"].get("profit_factor", 0)
        log.info("  %s: %d trades, WR=%.1f%%, PF=%.3f", etype, n, wr * 100, pf)
    return result


# ---------------------------------------------------------------------------
# Phase 3: Parameter sweeps
# ---------------------------------------------------------------------------
def phase_3_sweeps(data_dict: Dict, tickers: List[str]) -> Dict[str, Any]:
    """Parameter sweeps for each entry type with sufficient trades."""
    log.info("PHASE 3: Parameter sweeps")
    results = {}

    # Type B sweep: momentum_bars [2, 3, 4]
    log.info("  Sweeping TypeB momentum_bars...")
    b_sweep = {}
    for bars in [2, 3, 4]:
        cfg_override = dict(ENTRY_TYPE_CONFIG)
        cfg_override["type_b_momentum_bars"] = bars
        trades = _simulate_with_config(data_dict, tickers, cfg_override, type_filter="TypeB")
        m = compute_metrics(trades, f"TypeB_bars={bars}")
        b_sweep[f"momentum_bars={bars}"] = m
        log.info("    bars=%d: %d trades, WR=%.1f%%, PF=%.3f", bars, m["trades"], m.get("win_rate", 0) * 100, m.get("profit_factor", 0))
    results["TypeB_momentum_bars"] = b_sweep

    # Type E sweep: IBS threshold [0.05, 0.10, 0.15, 0.20]
    log.info("  Sweeping TypeE IBS threshold...")
    e_sweep = {}
    for ibs_t in [0.05, 0.10, 0.15, 0.20]:
        cfg_override = dict(ENTRY_TYPE_CONFIG)
        cfg_override["type_e_ibs_threshold"] = ibs_t
        trades = _simulate_with_config(data_dict, tickers, cfg_override, type_filter="TypeE")
        m = compute_metrics(trades, f"TypeE_ibs={ibs_t}")
        e_sweep[f"ibs_threshold={ibs_t}"] = m
        log.info("    ibs=%.2f: %d trades, WR=%.1f%%, PF=%.3f", ibs_t, m["trades"], m.get("win_rate", 0) * 100, m.get("profit_factor", 0))
    results["TypeE_ibs_threshold"] = e_sweep

    # Type F sweep: OBV-RSI threshold [20, 25, 30, 35]
    log.info("  Sweeping TypeF OBV-RSI threshold...")
    f_sweep = {}
    for obv_t in [20.0, 25.0, 30.0, 35.0]:
        cfg_override = dict(ENTRY_TYPE_CONFIG)
        cfg_override["type_f_obv_rsi_threshold"] = obv_t
        trades = _simulate_with_config(data_dict, tickers, cfg_override, type_filter="TypeF")
        m = compute_metrics(trades, f"TypeF_obv={obv_t}")
        f_sweep[f"obv_rsi_threshold={obv_t}"] = m
        log.info("    obv=%.0f: %d trades, WR=%.1f%%, PF=%.3f", obv_t, m["trades"], m.get("win_rate", 0) * 100, m.get("profit_factor", 0))
    results["TypeF_obv_rsi_threshold"] = f_sweep

    return results


def _simulate_with_config(
    data_dict: Dict, tickers: List[str], cfg: Dict, type_filter: Optional[str] = None
) -> List[SimTrade]:
    """Re-simulate with custom entry type config. Returns trades of specified type only."""
    from python_brain.ouroboros.backfill_simulator import (
        compute_rsi, compute_atr, classify_entries, simulate_chandelier_exit,
        detect_exchange, COSTS_PER_EXCHANGE, FX_TO_GBP, _load_currency_map,
        FX_CONVERSION_COST, ENTRY_TYPE_CONFIG as _ET_CFG,
    )
    from brain.indicators.hurst import classify_regime, estimate_hurst
    from brain.indicators.volume_analytics import calculate_rvol

    trades = []
    currency_map = _load_currency_map()

    for ticker in tickers:
        df = data_dict.get(ticker)
        if df is None:
            continue

        closes = df["Close"].values.astype(np.float64).flatten()
        highs = df["High"].values.astype(np.float64).flatten()
        lows = df["Low"].values.astype(np.float64).flatten()
        volumes = df["Volume"].values.astype(np.float64).flatten()

        if len(closes) < 30:
            continue

        rsi = compute_rsi(closes, 14)
        atr = compute_atr(highs, lows, closes, 14)
        rvol_arr = np.zeros(len(volumes))
        for i in range(21, len(volumes)):
            vol_list = volumes[i - 21:i].tolist()
            vol_list.append(volumes[i])
            rvol_arr[i] = calculate_rvol(vol_list, window=20)

        hurst = estimate_hurst(closes.tolist(), max_lag=20)
        regime = classify_regime(hurst)

        if hasattr(df.index, 'date'):
            dates = [str(d.date()) if hasattr(d, 'date') else str(d)[:10] for d in df.index]
        else:
            dates = [str(i) for i in range(len(df))]

        entries = classify_entries(
            closes, volumes, rsi, rvol_arr, regime,
            highs=highs, lows=lows, atr=atr, dates=dates, cfg=cfg,
        )

        exchange = detect_exchange(ticker)
        cost_pct = COSTS_PER_EXCHANGE.get(exchange, 0.003)
        currency = currency_map.get(ticker, "USD")
        fx_cost = FX_CONVERSION_COST if (exchange == "LSE" and currency != "GBP") else 0.0
        total_cost = cost_pct + fx_cost
        fx_rate = FX_TO_GBP.get(currency, 0.79)

        confidence_map = {
            "TypeA": cfg.get("type_a_confidence", 65.0),
            "TypeB": cfg.get("type_b_confidence", 82.0),
            "TypeC": cfg.get("type_c_confidence", 72.0),
            "TypeD": cfg.get("type_d_confidence", 80.0),
            "TypeE": cfg.get("type_e_confidence", 70.0),
            "TypeF": cfg.get("type_f_confidence", 68.0),
        }

        for entry_bar, entry_type in entries:
            if type_filter and entry_type != type_filter:
                continue
            entry_price = closes[entry_bar]
            if entry_price <= 0:
                continue

            exit_bar, exit_price, rung = simulate_chandelier_exit(
                closes, highs, lows, atr, entry_bar, entry_price,
            )
            pnl = exit_price - entry_price
            pnl_pct = pnl / entry_price * 100.0
            net_pnl = pnl - (entry_price * total_cost)
            net_pnl_pct = pnl_pct - (total_cost * 100.0)

            entry_hour = -1
            entry_weekday = -1
            index_list = list(df.index)
            if entry_bar < len(index_list) and hasattr(index_list[entry_bar], 'hour'):
                try:
                    entry_hour = index_list[entry_bar].hour
                    entry_weekday = index_list[entry_bar].weekday()
                except AttributeError:
                    pass

            trades.append(SimTrade(
                ticker=ticker, date=dates[entry_bar] if entry_bar < len(dates) else "unknown",
                entry_type=entry_type, entry_price=entry_price, exit_price=exit_price,
                entry_bar=entry_bar, exit_bar=exit_bar, rung_achieved=rung,
                pnl=pnl, pnl_pct=pnl_pct, hold_bars=exit_bar - entry_bar,
                regime=regime, exchange=exchange, entry_hour=entry_hour,
                entry_weekday=entry_weekday,
                confidence=confidence_map.get(entry_type, 70.0),
                cost_pct=total_cost * 100.0, net_pnl=net_pnl,
                net_pnl_pct=net_pnl_pct, currency=currency,
                gbp_pnl=net_pnl * fx_rate,
            ))

    return trades


# ---------------------------------------------------------------------------
# Phase 4: Rolling walk-forward
# ---------------------------------------------------------------------------
def phase_4_walk_forward(data_dict: Dict, tickers: List[str]) -> Dict[str, Any]:
    """Rolling walk-forward validation with 3 overlapping windows."""
    log.info("PHASE 4: Rolling walk-forward validation")
    results = {"windows": [], "summary": {}}

    # Determine date range from data
    all_dates = set()
    for df in data_dict.values():
        if hasattr(df.index, 'date'):
            for d in df.index:
                try:
                    all_dates.add(str(d.date()) if hasattr(d, 'date') else str(d)[:10])
                except Exception:
                    pass
    if not all_dates:
        log.warning("  No date data available for walk-forward")
        return results

    sorted_dates = sorted(all_dates)
    n_days = len(sorted_dates)
    log.info("  Date range: %s to %s (%d unique days)", sorted_dates[0], sorted_dates[-1], n_days)

    # Split into 3 windows: each ~50% train, ~20% test, overlapping
    window_size = n_days // 3
    windows = [
        (0, 2 * window_size, 2 * window_size, min(2 * window_size + window_size, n_days)),
        (window_size, 2 * window_size + window_size, 2 * window_size + window_size, n_days),
    ]
    # Also: full first half vs second half (classic)
    mid = n_days // 2
    windows.append((0, mid, mid, n_days))

    for w_idx, (train_start, train_end, test_start, test_end) in enumerate(windows):
        train_dates = set(sorted_dates[train_start:train_end])
        test_dates = set(sorted_dates[test_start:test_end])

        log.info("  Window %d: Train %d days, Test %d days", w_idx + 1, len(train_dates), len(test_dates))

        # Simulate all trades, then split by date
        all_trades = []
        for ticker in tickers:
            df = data_dict.get(ticker)
            if df is not None:
                all_trades.extend(simulate_ticker(ticker, df))

        train_trades = [t for t in all_trades if t.date[:10] in train_dates]
        test_trades = [t for t in all_trades if t.date[:10] in test_dates]

        window_result = {"window": w_idx + 1, "train_days": len(train_dates), "test_days": len(test_dates)}
        for etype in ALL_TYPES:
            train_typed = [t for t in train_trades if t.entry_type == etype]
            test_typed = [t for t in test_trades if t.entry_type == etype]
            train_m = compute_metrics(train_typed, f"W{w_idx+1}_train_{etype}")
            test_m = compute_metrics(test_typed, f"W{w_idx+1}_test_{etype}")

            train_wr = train_m.get("win_rate", 0)
            test_wr = test_m.get("win_rate", 0)
            wr_delta = (test_wr - train_wr) * 100  # percentage points

            window_result[etype] = {
                "train": train_m,
                "test": test_m,
                "wr_degradation_pp": round(wr_delta, 2),
                "stable": abs(wr_delta) <= 5,
            }

        results["windows"].append(window_result)
        del all_trades
        gc.collect()

    # Summary across windows per type
    for etype in ALL_TYPES:
        degradations = []
        for w in results["windows"]:
            if etype in w:
                degradations.append(w[etype]["wr_degradation_pp"])
        if degradations:
            results["summary"][etype] = {
                "mean_wr_degradation_pp": round(np.mean(degradations), 2),
                "std_wr_degradation_pp": round(np.std(degradations), 2),
                "stable": all(abs(d) <= 5 for d in degradations),
                "verdict": "STABLE" if all(abs(d) <= 5 for d in degradations) else "UNSTABLE",
            }

    return results


# ---------------------------------------------------------------------------
# Phase 5: Capital-constrained Monte Carlo
# ---------------------------------------------------------------------------
def phase_5_monte_carlo(all_trades: List[SimTrade]) -> Dict[str, Any]:
    """Capital-constrained Monte Carlo for standalone types and combinations."""
    log.info("PHASE 5: Capital-constrained Monte Carlo")
    results = {}

    def _trades_to_dicts(trades: List[SimTrade]) -> List[Dict]:
        return [{"net_pnl_pct": t.net_pnl_pct, "exchange": t.exchange,
                 "cost_pct": t.cost_pct, "currency": t.currency} for t in trades]

    # Standalone per type
    for etype in ALL_TYPES:
        typed = [t for t in all_trades if t.entry_type == etype]
        if len(typed) < 10:
            results[etype] = {"note": f"INSUFFICIENT ({len(typed)} trades)"}
            continue
        mc = run_constrained_simulation(
            _trades_to_dicts(typed), simulations=5000, seed=42,
            max_concurrent=3, kelly_fraction=0.05,
        )
        results[etype] = asdict(mc)
        log.info("  %s: median=%.0f, P(ruin)=%.1f%%, Sharpe=%.2f",
                 etype, mc.median_final_equity, mc.probability_of_ruin * 100, mc.sharpe)

    # Key combinations
    combos = {
        "B_only": ["TypeB"],
        "B+E": ["TypeB", "TypeE"],
        "B+F": ["TypeB", "TypeF"],
        "B+E+F": ["TypeB", "TypeE", "TypeF"],
        "all_A-F": ALL_TYPES,
    }
    for combo_name, types in combos.items():
        combo_trades = [t for t in all_trades if t.entry_type in types]
        if len(combo_trades) < 10:
            results[combo_name] = {"note": f"INSUFFICIENT ({len(combo_trades)} trades)"}
            continue
        mc = run_constrained_simulation(
            _trades_to_dicts(combo_trades), simulations=5000, seed=42,
            max_concurrent=3, kelly_fraction=0.05,
        )
        results[combo_name] = asdict(mc)
        log.info("  %s: median=%.0f, P(ruin)=%.1f%%, Sharpe=%.2f",
                 combo_name, mc.median_final_equity, mc.probability_of_ruin * 100, mc.sharpe)

    return results


# ---------------------------------------------------------------------------
# Phase 6: Ablation testing
# ---------------------------------------------------------------------------
def phase_6_ablation(all_trades: List[SimTrade]) -> Dict[str, Any]:
    """Remove components one at a time and measure impact."""
    log.info("PHASE 6: Ablation testing")
    baseline = compute_metrics(all_trades, "baseline")
    results = {"baseline": baseline, "ablations": {}}

    for etype in ALL_TYPES:
        ablated = [t for t in all_trades if t.entry_type != etype]
        m = compute_metrics(ablated, f"remove_{etype}")
        delta_wr = (m.get("win_rate", 0) - baseline.get("win_rate", 0)) * 100
        delta_pf = m.get("profit_factor", 0) - baseline.get("profit_factor", 0)
        verdict = "REMOVE" if delta_wr > 0 and delta_pf > 0 else "KEEP"
        results["ablations"][f"remove_{etype}"] = {
            "metrics": m,
            "delta_wr_pp": round(delta_wr, 2),
            "delta_pf": round(delta_pf, 3),
            "verdict": verdict,
        }
        log.info("  remove_%s: WR %+.2fpp, PF %+.3f -> %s",
                 etype, delta_wr, delta_pf, verdict)

    return results


# ---------------------------------------------------------------------------
# Phase 7: Regime and session analysis
# ---------------------------------------------------------------------------
def phase_7_regime_session(all_trades: List[SimTrade]) -> Dict[str, Any]:
    """Per-regime and per-session analysis."""
    log.info("PHASE 7: Regime and session analysis")
    result = {}

    # Per type per regime
    per_type_regime = {}
    for etype in ALL_TYPES:
        typed = [t for t in all_trades if t.entry_type == etype]
        per_type_regime[etype] = metrics_by_dimension(typed, lambda t: t.regime, f"{etype}/regime")
    result["per_type_per_regime"] = per_type_regime

    # Per type per exchange
    per_type_exchange = {}
    for etype in ALL_TYPES:
        typed = [t for t in all_trades if t.entry_type == etype]
        per_type_exchange[etype] = metrics_by_dimension(typed, lambda t: t.exchange, f"{etype}/exchange")
    result["per_type_per_exchange"] = per_type_exchange

    # Per type per hour
    per_type_hour = {}
    for etype in ALL_TYPES:
        typed = [t for t in all_trades if t.entry_type == etype and t.entry_hour >= 0]
        per_type_hour[etype] = metrics_by_dimension(typed, lambda t: f"{t.entry_hour:02d}", f"{etype}/hour")
    result["per_type_per_hour"] = per_type_hour

    return result


# ---------------------------------------------------------------------------
# Phase 8: Portfolio combination tests
# ---------------------------------------------------------------------------
def phase_8_combinations(all_trades: List[SimTrade]) -> Dict[str, Any]:
    """Test portfolio combinations of entry types."""
    log.info("PHASE 8: Portfolio combination tests")
    combos = {
        "A": ["TypeA"], "B": ["TypeB"], "C": ["TypeC"],
        "D": ["TypeD"], "E": ["TypeE"], "F": ["TypeF"],
        "B+E": ["TypeB", "TypeE"],
        "B+F": ["TypeB", "TypeF"],
        "B+C": ["TypeB", "TypeC"],
        "E+F": ["TypeE", "TypeF"],
        "B+E+F": ["TypeB", "TypeE", "TypeF"],
        "B+C+E": ["TypeB", "TypeC", "TypeE"],
        "A+B+C+D+E+F": ALL_TYPES,
    }

    results = {}
    for name, types in combos.items():
        combo_trades = [t for t in all_trades if t.entry_type in types]
        m = compute_metrics(combo_trades, f"combo_{name}")
        results[name] = m
        log.info("  %s: %d trades, WR=%.1f%%, PF=%.3f",
                 name, m["trades"], m.get("win_rate", 0) * 100, m.get("profit_factor", 0))
    return results


# ---------------------------------------------------------------------------
# Phase 9: Final verdicts
# ---------------------------------------------------------------------------
def phase_9_verdicts(
    standalone: Dict, walk_forward: Dict, monte_carlo: Dict, ablation: Dict
) -> Dict[str, Any]:
    """Apply promotion rules to determine Keep/Tighten/Shadow/Disable/Abandon."""
    log.info("PHASE 9: Final verdicts")
    verdicts = {}

    for etype in ALL_TYPES:
        # Standalone metrics
        st = standalone.get(etype, {}).get("overall", {})
        wr = st.get("win_rate", 0)
        pf = st.get("profit_factor", 0)
        n = st.get("trades", 0)

        # Walk-forward stability
        wf = walk_forward.get("summary", {}).get(etype, {})
        wf_stable = wf.get("stable", False)
        wf_degrade = wf.get("mean_wr_degradation_pp", -99)

        # Monte Carlo
        mc = monte_carlo.get(etype, {})
        p_ruin = mc.get("probability_of_ruin", 1.0)
        median_eq = mc.get("median_final_equity", 0)

        # Ablation
        abl = ablation.get("ablations", {}).get(f"remove_{etype}", {})
        abl_verdict = abl.get("verdict", "KEEP")

        # Decision logic
        if n < 20:
            verdict = "INSUFFICIENT_DATA"
            reason = f"Only {n} trades"
        elif wr >= 0.55 and pf >= 1.5 and wf_stable and p_ruin < 0.10:
            verdict = "KEEP_LIVE"
            reason = f"WR={wr:.1%}, PF={pf:.2f}, walk-forward stable, P(ruin)={p_ruin:.1%}"
        elif wr >= 0.50 and pf >= 1.2 and wf_stable:
            verdict = "TIGHTEN"
            reason = f"WR={wr:.1%}, PF={pf:.2f}, stable but borderline. Raise confidence floor."
        elif wr >= 0.45 and pf >= 1.0:
            verdict = "SHADOW"
            reason = f"WR={wr:.1%}, PF={pf:.2f}. Log signals, don't trade. Promote after 100+ shadows."
        elif wr >= 0.40 or pf >= 0.8:
            verdict = "DISABLE"
            reason = f"WR={wr:.1%}, PF={pf:.2f}. Net detractor."
        else:
            verdict = "ABANDON"
            reason = f"WR={wr:.1%}, PF={pf:.2f}. No edge."

        # Override: if ablation says removing this type IMPROVES the book, downgrade
        if abl_verdict == "REMOVE" and verdict in ("KEEP_LIVE", "TIGHTEN"):
            verdict = "SHADOW"
            reason += " [DOWNGRADED: ablation shows removal improves portfolio]"

        verdicts[etype] = {
            "verdict": verdict,
            "reason": reason,
            "trade_count": n,
            "win_rate": round(wr, 4),
            "profit_factor": round(pf, 3),
            "walk_forward_stable": wf_stable,
            "wf_degradation_pp": round(wf_degrade, 2) if wf_degrade != -99 else None,
            "mc_p_ruin": round(p_ruin, 4) if isinstance(p_ruin, float) else None,
            "mc_median_equity": round(median_eq, 2) if isinstance(median_eq, (int, float)) else None,
        }
        log.info("  %s: %s — %s", etype, verdict, reason)

    return verdicts


# ---------------------------------------------------------------------------
# Executive summary
# ---------------------------------------------------------------------------
def generate_executive_summary(
    baseline: Dict, standalone: Dict, verdicts: Dict, all_trades: List[SimTrade]
) -> str:
    """Generate human-readable executive summary."""
    lines = [
        "=" * 80,
        "  AEGIS V2 — INSTITUTIONAL BACKTEST EXECUTIVE SUMMARY",
        f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "=" * 80,
        "",
        "BASELINE (all types, post-cost):",
    ]

    bl = baseline.get("overall", {})
    lines.append(f"  Trades: {bl.get('trades', 0):,}")
    lines.append(f"  Win Rate: {bl.get('win_rate', 0):.1%}")
    lines.append(f"  Profit Factor: {bl.get('profit_factor', 0):.3f}")
    lines.append(f"  Avg PnL%: {bl.get('avg_pnl_pct', 0):+.4f}%")
    lines.append(f"  Max Drawdown: {bl.get('max_drawdown_pct', 0):.1f}%")
    lines.append(f"  Total GBP PnL: {bl.get('total_gbp_pnl', 0):+,.2f}")
    lines.append(f"  Avg Cost: {bl.get('avg_cost_pct', 0):.3f}%")
    lines.append("")

    lines.append("PER-TYPE STANDALONE (post-cost):")
    lines.append(f"  {'Type':<8} {'Trades':>8} {'WR':>7} {'PF':>8} {'Avg PnL%':>10} {'GBP PnL':>12}")
    lines.append(f"  {'-'*55}")
    for etype in ALL_TYPES:
        st = standalone.get(etype, {}).get("overall", {})
        n = st.get("trades", 0)
        wr = st.get("win_rate", 0)
        pf = st.get("profit_factor", 0)
        avg_pnl = st.get("avg_pnl_pct", 0)
        gbp = st.get("total_gbp_pnl", 0)
        lines.append(f"  {etype:<8} {n:>8,} {wr:>6.1%} {pf:>8.3f} {avg_pnl:>+10.4f} {gbp:>+12.2f}")
    lines.append("")

    lines.append("VERDICTS:")
    lines.append(f"  {'Type':<8} {'Verdict':<20} {'Reason'}")
    lines.append(f"  {'-'*70}")
    for etype in ALL_TYPES:
        v = verdicts.get(etype, {})
        verdict = v.get("verdict", "UNKNOWN")
        reason = v.get("reason", "")
        lines.append(f"  {etype:<8} {verdict:<20} {reason}")
    lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
def run_institutional_backtest(
    days: int = 730,
    interval: str = "60m",
    universe_path: Optional[str] = None,
    quick: bool = False,
) -> int:
    """Run the full institutional backtest program."""
    if quick:
        days = 59
        interval = "5m"
        log.info("QUICK MODE: %dd %s", days, interval)

    start_time = time.monotonic()
    log.info("=" * 60)
    log.info("AEGIS V2 INSTITUTIONAL BACKTEST PROGRAM")
    log.info("  Days: %d, Interval: %s", days, interval)
    log.info("=" * 60)

    # --- Load tickers ---
    if universe_path:
        tickers = load_universe_file(universe_path)
    else:
        tickers = list(load_yfinance_symbols())

    blacklist = load_blacklist_from_config()
    if blacklist:
        tickers = [t for t in tickers if t not in blacklist]

    log.info("Universe: %d tickers (after blacklist)", len(tickers))

    # Enforce yfinance limits
    max_days = {"1m": 7, "5m": 59, "60m": 730, "1h": 730, "1d": 9999}
    days = min(days, max_days.get(interval, 59))
    period = f"{days}d"

    # --- Phase 0: Fetch data ---
    log.info("Fetching historical data...")
    data_dict = fetch_historical_data_parallel(tickers, period=period, interval=interval)
    tickers_with_data = list(data_dict.keys())
    log.info("Data fetched: %d/%d tickers", len(tickers_with_data), len(tickers))

    if not data_dict:
        log.error("No data fetched. Aborting.")
        return 1

    # --- Phase 0.5: Simulate all trades ---
    log.info("Simulating trades across all tickers...")
    all_trades: List[SimTrade] = []
    for ticker, df in data_dict.items():
        all_trades.extend(simulate_ticker(ticker, df))

    log.info("Total simulated trades: %d", len(all_trades))
    if not all_trades:
        log.error("No trades simulated. Aborting.")
        return 1

    # Distribution
    type_counts = defaultdict(int)
    for t in all_trades:
        type_counts[t.entry_type] += 1
    for etype in ALL_TYPES:
        log.info("  %s: %d trades", etype, type_counts.get(etype, 0))

    # --- Output directory ---
    out_dir = DATA_DIR / f"institutional_backtest_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("Output directory: %s", out_dir)

    # --- Phase 1 ---
    p1 = phase_1_baseline(all_trades)
    _save(out_dir / "01_baseline.json", p1)

    # --- Phase 2 ---
    p2 = phase_2_standalone(all_trades)
    standalone_dir = out_dir / "02_standalone"
    standalone_dir.mkdir(exist_ok=True)
    for etype in ALL_TYPES:
        _save(standalone_dir / f"{etype}.json", p2.get(etype, {}))

    # --- Phase 3 (parameter sweeps — skip in fast mode, very slow) ---
    skip_sweeps = os.environ.get("AEGIS_SKIP_SWEEPS", "1") == "1"
    if skip_sweeps:
        log.info("PHASE 3: SKIPPED (set AEGIS_SKIP_SWEEPS=0 to enable, takes ~30min)")
        p3 = {"note": "SKIPPED — re-simulation too slow. Use AEGIS_SKIP_SWEEPS=0 to enable."}
    else:
        p3 = phase_3_sweeps(data_dict, tickers_with_data)
    _save(out_dir / "03_sweeps.json", p3)

    # --- Phase 4 ---
    p4 = phase_4_walk_forward(data_dict, tickers_with_data)
    _save(out_dir / "04_walk_forward.json", p4)

    # Free data_dict to save memory
    del data_dict
    gc.collect()

    # --- Phase 5 ---
    p5 = phase_5_monte_carlo(all_trades)
    _save(out_dir / "05_monte_carlo.json", p5)

    # --- Phase 6 ---
    p6 = phase_6_ablation(all_trades)
    _save(out_dir / "06_ablation.json", p6)

    # --- Phase 7 ---
    p7 = phase_7_regime_session(all_trades)
    _save(out_dir / "07_regime_session.json", p7)

    # --- Phase 8 ---
    p8 = phase_8_combinations(all_trades)
    _save(out_dir / "08_combinations.json", p8)

    # --- Phase 9 ---
    p9 = phase_9_verdicts(p2, p4, p5, p6)
    _save(out_dir / "09_verdicts.json", p9)

    # --- Executive summary ---
    summary = generate_executive_summary(p1, p2, p9, all_trades)
    (out_dir / "EXECUTIVE_SUMMARY.txt").write_text(summary)
    print(summary)

    elapsed = time.monotonic() - start_time
    log.info("Institutional backtest complete in %.1f seconds", elapsed)
    log.info("Output: %s", out_dir)

    return 0


def _save(path: Path, data: Any) -> None:
    """Save data as JSON."""
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        log.error("Failed to save %s: %s", path, e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="AEGIS V2 Institutional Backtest")
    parser.add_argument("--days", type=int, default=730, help="Lookback days (default: 730)")
    parser.add_argument("--interval", type=str, default="60m", help="Bar interval (default: 60m)")
    parser.add_argument("--universe", type=str, help="Path to universe file")
    parser.add_argument("--quick", action="store_true", help="Quick mode: 59d 5m bars")
    args = parser.parse_args()

    sys.exit(run_institutional_backtest(
        days=args.days,
        interval=args.interval,
        universe_path=args.universe,
        quick=args.quick,
    ))


if __name__ == "__main__":
    main()
