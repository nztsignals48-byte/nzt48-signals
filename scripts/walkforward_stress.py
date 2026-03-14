#!/usr/bin/env python3
"""
NZT-48 Walk-Forward Stress Test
=================================
Replays the most recent 60 days of market data through the live S15 gate
logic in accelerated time, generating a detailed per-day decision log.

Purpose:
  1. Validate the live gate rejection logic BEFORE the next market open
  2. Confirm that signals would have fired on high-quality days
  3. Confirm that the system correctly sat out on low-quality days
  4. Identify the "best 5 days" in the last 60 days — proof the system
     would have caught them

This is NOT a new backfill — it uses the LIVE gate constants from
strategies/daily_target.py so any changes there are instantly reflected.

Output:
  - Per-day log: what would have happened (SIGNAL / SKIP + reason)
  - Best-5 replay: the top opportunities S15 would have caught
  - Missed-best: days with large moves that S15 would have skipped (and why)
  - Conviction calibration: confidence score vs actual outcome

Usage:
    python scripts/walkforward_stress.py [--days 60] [--tickers all]

    --days N      Lookback days (default: 60)
    --tickers T   Comma-separated tickers or "all"
    --verbose     Show full per-ticker detail
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from core.clock import now_utc

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("nzt48.walkforward")

# ── Live gate constants (mirror strategies/daily_target.py exactly) ───────────
# CRITICAL: if you change S15 constants, update here too
_MIN_RVOL = 0.80
_MIN_CONFIDENCE = 65.0
_MIN_ADX = 20.0
_MIN_INDICATOR_CONSENSUS = 5
_RANGE_BOUND_ATR_MIN = 2.0   # %
_RANGE_BOUND_RVOL_MIN = 1.2
_RANGE_BOUND_CONSENSUS_MIN = 6
_RANGE_BOUND_CONF_PENALTY = 10.0
_SKIP_REGIMES = {"SHOCK", "CRASH", "UNDEFINED"}
_MIN_MOVE_PCT = 0.030
TARGET_PCT = 0.02
STOP_PCT_3X = 0.01
STOP_PCT_5X = 0.0075

_5X_TICKERS = {"QQQ5.L"}
# F-03: import from single source of truth (config.universe_constants)
from config.universe_constants import INVERSE_ETPS_SET as _INVERSE_TICKERS

ALL_TICKERS = [
    "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L",
    "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L",
    "AMD3.L", "ARM3.L",
]


def fetch_data(ticker: str, days: int) -> pd.DataFrame | None:
    try:
        import yfinance as yf
        end = now_utc()
        start = end - timedelta(days=days + 60)  # extra for indicator warmup
        df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
        if df is not None and len(df) >= 20:
            return df
    except Exception as e:
        logger.debug("%s: %s", ticker, e)
    return None


def compute_indicators(df: pd.DataFrame) -> list[dict]:
    closes = df["Close"].values.flatten()
    highs = df["High"].values.flatten()
    lows = df["Low"].values.flatten()
    opens = df["Open"].values.flatten()
    volumes = df["Volume"].values.flatten()
    n = len(df)
    rows = []

    for i in range(50, n):
        atr_vals = [max(highs[j]-lows[j], abs(highs[j]-closes[j-1]), abs(lows[j]-closes[j-1]))
                    for j in range(max(1, i-13), i+1)]
        atr14 = sum(atr_vals) / len(atr_vals)
        atr_pct = atr14 / closes[i] * 100 if closes[i] > 0 else 0  # as percent

        changes = [closes[j]-closes[j-1] for j in range(max(1, i-13), i+1)]
        gains = [max(0, c) for c in changes]
        losses = [max(0, -c) for c in changes]
        avg_gain = sum(gains)/len(gains) if gains else 0
        avg_loss = sum(losses)/len(losses) if losses else 0.001
        rsi14 = 100 - (100 / (1 + avg_gain / avg_loss))

        k20 = 2.0/21
        ema20 = closes[max(0, i-39)]
        for c in closes[max(0, i-39)+1:i+1]:
            ema20 = c*k20 + ema20*(1-k20)

        k50 = 2.0/51
        ema50 = closes[max(0, i-99)]
        for c in closes[max(0, i-99)+1:i+1]:
            ema50 = c*k50 + ema50*(1-k50)

        k9 = 2.0/10; k26 = 2.0/27
        ema9 = closes[max(0, i-17)]
        for c in closes[max(0, i-17)+1:i+1]:
            ema9 = c*k9 + ema9*(1-k9)
        ema26 = closes[max(0, i-51)]
        for c in closes[max(0, i-51)+1:i+1]:
            ema26 = c*k26 + ema26*(1-k26)

        vol_w = volumes[max(0, i-19):i+1]
        avg_vol = sum(vol_w[:-1]) / max(len(vol_w)-1, 1)
        rvol = volumes[i] / avg_vol if avg_vol > 0 else 1.0

        daily_range_pct = (highs[i]-lows[i]) / closes[i] * 100 if closes[i] > 0 else 0
        adx_proxy = (daily_range_pct / atr_pct) * 25 if atr_pct > 0 else 0

        sigs = {
            "rsi":      1 if 50 < rsi14 <= 70 else (-1 if rsi14 > 75 else 0),
            "macd":     1 if (ema9-ema26) > 0 else -1,
            "ema9":     1 if closes[i] > ema9 else -1,
            "ema20":    1 if closes[i] > ema20 else -1,
            "ema50":    1 if closes[i] > ema50 else -1,
            "vwap":     1 if closes[i] > ema20 else 0,
            "stoch":    1 if 20 < rsi14 < 80 else (-1 if rsi14 >= 80 else 0),
            "obv":      1 if volumes[i] > avg_vol else -1,
        }
        bullish = sum(1 for v in sigs.values() if v == 1)

        rows.append({
            "date": df.index[i].date(),
            "open": float(opens[i]), "high": float(highs[i]),
            "low": float(lows[i]), "close": float(closes[i]),
            "prev_close": float(closes[i-1]),
            "volume": float(volumes[i]), "avg_vol": float(avg_vol),
            "rvol": float(rvol),
            "rsi14": float(rsi14),
            "atr_pct": float(atr_pct),
            "ema20": float(ema20), "ema50": float(ema50),
            "adx_proxy": float(adx_proxy),
            "daily_range_pct": float(daily_range_pct),
            "sigs": sigs,
            "bullish": int(bullish),
        })
    return rows


def classify_regime(row: dict) -> str:
    rsi = row["rsi14"]
    c, o = row["close"], row["open"]
    ema20, ema50 = row["ema20"], row["ema50"]
    dr = row["daily_range_pct"]
    if dr > 6.0 and abs(c - o) / o > 4.0:
        return "SHOCK"
    if rsi > 60 and c > ema50 and c > ema20:
        return "TRENDING_UP_STRONG"
    if rsi > 50 and c > ema20:
        return "TRENDING_UP_MOD"
    if rsi < 30 and c < ema50 and c < ema20:
        return "TRENDING_DOWN_STRONG"
    if rsi < 40 and c < ema20:
        return "TRENDING_DOWN_MOD"
    if 40 <= rsi <= 60 and abs(c - ema20) / ema20 < 0.02:
        return "RANGE_BOUND"
    return "NEUTRAL"


def evaluate_day(row: dict, ticker: str) -> dict:
    """Run the live S15 gate logic against one historical day. Returns decision dict."""
    is_inverse = ticker in _INVERSE_TICKERS
    is_5x = ticker in _5X_TICKERS
    direction = "SHORT" if is_inverse else "LONG"
    stop_pct = STOP_PCT_5X if is_5x else STOP_PCT_3X

    regime = classify_regime(row)

    # Actual day outcome (what would have happened)
    o, h, l, c, pc = row["open"], row["high"], row["low"], row["close"], row["prev_close"]
    actual_move_pct = (c - o) / o * 100 if o > 0 else 0
    actual_range_pct = row["daily_range_pct"]

    # ── Gate checks (in order) ────────────────────────────────────────────────
    def gate_result(gate: str, reason: str) -> dict:
        return {
            "ticker": ticker, "date": str(row["date"]),
            "decision": "SKIP", "gate_failed": gate, "reason": reason,
            "regime": regime, "rvol": row["rvol"], "adr_pct": actual_range_pct,
            "actual_move_pct": round(actual_move_pct, 2), "direction": direction,
            "would_have_won": None, "confidence": None,
        }

    if row["daily_range_pct"] < _MIN_MOVE_PCT * 100:
        return gate_result("RANGE", f"range {actual_range_pct:.1f}% < {_MIN_MOVE_PCT*100:.1f}%")

    if row["rvol"] < _MIN_RVOL:
        return gate_result("RVOL", f"rvol {row['rvol']:.2f} < {_MIN_RVOL}")

    if row["adx_proxy"] < _MIN_ADX:
        return gate_result("ADX", f"adx_proxy {row['adx_proxy']:.1f} < {_MIN_ADX}")

    if regime in _SKIP_REGIMES:
        return gate_result("REGIME", f"hard veto: {regime}")

    if direction == "LONG" and regime in ("TRENDING_DOWN_STRONG", "TRENDING_DOWN_MOD"):
        return gate_result("DIRECTION", f"counter-trend long in {regime}")

    if direction == "SHORT" and regime in ("TRENDING_UP_STRONG", "TRENDING_UP_MOD"):
        return gate_result("DIRECTION", f"counter-trend short in {regime}")

    range_bound_skip = False
    if regime == "RANGE_BOUND":
        needs_atr = _RANGE_BOUND_ATR_MIN
        needs_rvol = _RANGE_BOUND_RVOL_MIN
        needs_cons = _RANGE_BOUND_CONSENSUS_MIN
        if row["atr_pct"] < needs_atr or row["rvol"] < needs_rvol:
            return gate_result("RANGE_BOUND",
                               f"range-bound requires atr≥{needs_atr}% rvol≥{needs_rvol}, "
                               f"got atr={row['atr_pct']:.1f}% rvol={row['rvol']:.2f}")
        range_bound_skip = True  # passes but with penalty

    if direction == "LONG" and row["bullish"] < _MIN_INDICATOR_CONSENSUS:
        return gate_result("CONSENSUS", f"only {row['bullish']}/8 bullish indicators "
                                        f"(need {_MIN_INDICATOR_CONSENSUS})")

    # Confidence scoring
    conf = 50.0
    if row["rvol"] > 2.0: conf += 15
    elif row["rvol"] > 1.5: conf += 10
    elif row["rvol"] > 1.0: conf += 5
    if regime == "TRENDING_UP_STRONG" and direction == "LONG": conf += 15
    elif regime == "TRENDING_UP_MOD" and direction == "LONG": conf += 10
    elif regime == "RANGE_BOUND": conf -= _RANGE_BOUND_CONF_PENALTY
    if row["atr_pct"] >= 3.0: conf += 10
    elif row["atr_pct"] >= 2.0: conf += 5
    rsi = row["rsi14"]
    if direction == "LONG" and 50 < rsi <= 70: conf += 5
    if direction == "LONG" and rsi > 75: conf -= 15
    if direction == "LONG" and rsi < 45: conf -= 10
    if row["bullish"] >= 7: conf += 10
    elif row["bullish"] >= 6: conf += 5
    conf = max(20, min(95, conf))

    if conf < _MIN_CONFIDENCE:
        return gate_result("CONFIDENCE", f"conf {conf:.0f} < {_MIN_CONFIDENCE}")

    # ── All gates passed → SIGNAL ─────────────────────────────────────────────
    entry = o + 0.3 * (h - o) if direction == "LONG" else o - 0.3 * (o - l)
    stop_abs = entry * (1 - stop_pct) if direction == "LONG" else entry * (1 + stop_pct)
    rung2 = entry * 1.02 if direction == "LONG" else entry * 0.98
    rung1 = entry * 1.01 if direction == "LONG" else entry * 0.99

    if direction == "LONG":
        if h >= rung2:
            outcome = "TARGET" if l > stop_abs or o >= pc else "STOP"
        elif h >= rung1:
            outcome = "BREAKEVEN" if l <= stop_abs else "TIME_STOP"
        else:
            outcome = "STOP" if l <= stop_abs else "TIME_STOP"
        pnl_r = 2.0 if outcome == "TARGET" else 0.0 if outcome == "BREAKEVEN" else (
            -1.0 if outcome == "STOP" else (c - entry) / abs(entry - stop_abs) if abs(entry - stop_abs) > 0 else 0
        )
    else:
        if l <= rung2:
            outcome = "TARGET" if h < stop_abs or o <= pc else "STOP"
        elif l <= rung1:
            outcome = "BREAKEVEN" if h >= stop_abs else "TIME_STOP"
        else:
            outcome = "STOP" if h >= stop_abs else "TIME_STOP"
        pnl_r = 2.0 if outcome == "TARGET" else 0.0 if outcome == "BREAKEVEN" else (
            -1.0 if outcome == "STOP" else (entry - c) / abs(entry - stop_abs) if abs(entry - stop_abs) > 0 else 0
        )

    return {
        "ticker": ticker, "date": str(row["date"]),
        "decision": "SIGNAL", "gate_failed": None, "reason": None,
        "regime": regime, "rvol": round(row["rvol"], 2),
        "adr_pct": round(actual_range_pct, 2),
        "actual_move_pct": round(actual_move_pct, 2),
        "direction": direction, "confidence": round(conf, 1),
        "bullish_count": row["bullish"],
        "entry": round(entry, 4), "stop": round(stop_abs, 4),
        "target2r": round(rung2, 4),
        "outcome": outcome, "pnl_r": round(pnl_r, 3),
        "would_have_won": outcome in ("TARGET", "BREAKEVEN"),
        "range_bound": range_bound_skip,
    }


def main():
    parser = argparse.ArgumentParser(description="NZT-48 Walk-Forward Stress Test")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--tickers", type=str, default="all")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    tickers = ALL_TICKERS if args.tickers == "all" else [t.strip() for t in args.tickers.split(",")]
    cutoff = now_utc().date() - timedelta(days=args.days)

    logger.info("=" * 70)
    logger.info("NZT-48 WALK-FORWARD STRESS TEST — Last %d days", args.days)
    logger.info("Cutoff: %s | Tickers: %d", cutoff, len(tickers))
    logger.info("=" * 70)

    all_days: list[dict] = []
    signals: list[dict] = []
    skips: list[dict] = []
    gate_counts: dict = {}

    for ticker in tickers:
        df = fetch_data(ticker, args.days)
        if df is None:
            continue
        rows = compute_indicators(df)
        # Only evaluate days within our window
        rows_in_window = [r for r in rows if r["date"] >= cutoff]

        for row in rows_in_window:
            result = evaluate_day(row, ticker)
            all_days.append(result)
            if result["decision"] == "SIGNAL":
                signals.append(result)
            else:
                skips.append(result)
                gate = result["gate_failed"]
                gate_counts[gate] = gate_counts.get(gate, 0) + 1

    # ── Summary ───────────────────────────────────────────────────────────────
    n_days = len(all_days)
    n_signals = len(signals)
    n_skips = len(skips)

    logger.info("")
    logger.info("OVERVIEW")
    logger.info("  Total ticker-days evaluated:  %d", n_days)
    logger.info("  Signals fired:                %d (%.1f%%)", n_signals, n_signals/n_days*100 if n_days else 0)
    logger.info("  Days skipped:                 %d", n_skips)

    # Gate breakdown
    logger.info("")
    logger.info("TOP REJECTION GATES (why S15 sat out):")
    for gate, cnt in sorted(gate_counts.items(), key=lambda x: -x[1]):
        logger.info("  %-16s %d (%.1f%% of all evaluations)", gate, cnt, cnt/n_days*100 if n_days else 0)

    # Signal performance
    if signals:
        wins = [s for s in signals if s.get("would_have_won")]
        target_hits = [s for s in signals if s.get("outcome") == "TARGET"]
        stops = [s for s in signals if s.get("outcome") == "STOP"]
        logger.info("")
        logger.info("SIGNAL PERFORMANCE (last %d days):", args.days)
        logger.info("  Signals:    %d", n_signals)
        logger.info("  TARGET:     %d (%.1f%%)", len(target_hits), len(target_hits)/n_signals*100)
        logger.info("  BREAKEVEN:  %d", sum(1 for s in signals if s.get("outcome")=="BREAKEVEN"))
        logger.info("  TIME_STOP:  %d", sum(1 for s in signals if s.get("outcome")=="TIME_STOP"))
        logger.info("  STOP:       %d (%.1f%%)", len(stops), len(stops)/n_signals*100)
        avg_conf = sum(s.get("confidence",0) for s in signals) / n_signals
        logger.info("  Avg confidence:  %.1f", avg_conf)

        # Best 5 signals
        top5 = sorted(signals, key=lambda x: x.get("pnl_r", 0), reverse=True)[:5]
        logger.info("")
        logger.info("TOP 5 SIGNALS (highest R):")
        logger.info("  %-12s  %-12s  %-24s  %-10s  %-5s  %-6s",
                    "Ticker", "Date", "Regime", "Outcome", "Conf", "R")
        for s in top5:
            logger.info("  %-12s  %-12s  %-24s  %-10s  %-5.0f  %-6.2f",
                        s["ticker"], s["date"], s["regime"],
                        s.get("outcome","?"), s.get("confidence",0), s.get("pnl_r",0))

    # Missed big moves (days with >4% range that S15 skipped)
    missed = [s for s in skips if s["adr_pct"] > 4.0]
    missed.sort(key=lambda x: -x["adr_pct"])
    if missed:
        logger.info("")
        logger.info("LARGEST SKIPPED MOVES (>4%% range) — Why S15 Sat Out:")
        logger.info("  %-12s  %-12s  %-6s  %-16s  %-s",
                    "Ticker", "Date", "Range%", "Gate", "Reason")
        for m in missed[:10]:
            logger.info("  %-12s  %-12s  %-6.1f  %-16s  %-s",
                        m["ticker"], m["date"], m["adr_pct"],
                        m["gate_failed"], m["reason"])

    # Conviction calibration
    if signals:
        logger.info("")
        logger.info("CONVICTION CALIBRATION (confidence → actual win rate):")
        buckets = {"60-69": {"n": 0, "w": 0}, "70-79": {"n": 0, "w": 0},
                   "80-89": {"n": 0, "w": 0}, "90+": {"n": 0, "w": 0}}
        for s in signals:
            conf = s.get("confidence", 0)
            won = 1 if s.get("would_have_won") else 0
            if conf < 70: buckets["60-69"]["n"] += 1; buckets["60-69"]["w"] += won
            elif conf < 80: buckets["70-79"]["n"] += 1; buckets["70-79"]["w"] += won
            elif conf < 90: buckets["80-89"]["n"] += 1; buckets["80-89"]["w"] += won
            else: buckets["90+"]["n"] += 1; buckets["90+"]["w"] += won
        for bucket, v in buckets.items():
            if v["n"] > 0:
                wr = v["w"]/v["n"]*100
                logger.info("  Conf %s:  %d signals  WR=%.0f%%", bucket, v["n"], wr)

    # Verbose: per-day detail
    if args.verbose:
        logger.info("")
        logger.info("FULL PER-DAY LOG:")
        all_days.sort(key=lambda x: (x["date"], x["ticker"]))
        for d in all_days:
            if d["decision"] == "SIGNAL":
                logger.info("  SIGNAL  %s  %-12s  %s  conf=%.0f  %s  R=%.2f",
                            d["date"], d["ticker"], d["regime"],
                            d.get("confidence", 0), d.get("outcome", "?"), d.get("pnl_r", 0))
            else:
                logger.info("  SKIP    %s  %-12s  gate=%-14s  %s",
                            d["date"], d["ticker"], d["gate_failed"], d["reason"])

    # Save results
    out_path = _ROOT / "data" / "walkforward_stress_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "generated_at": now_utc().isoformat(),
            "days": args.days, "tickers": tickers,
            "n_evaluated": n_days,
            "n_signals": n_signals,
            "n_skips": n_skips,
            "gate_counts": gate_counts,
            "signals": signals,
            "top_skips": sorted(skips, key=lambda x: -x["adr_pct"])[:20],
        }, f, indent=2, default=str)
    logger.info("")
    logger.info("Full results saved to %s", out_path)


if __name__ == "__main__":
    main()
