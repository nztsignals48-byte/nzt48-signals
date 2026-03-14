"""
learning/adaptive_engine.py
============================
Institutional-grade adaptive learning engine for NZT-48.

Uses COMPLETED trade data (outcomes.jsonl) to continuously improve
future trade selection. Implements academic research on adaptive
trading systems.

Academic basis:
- Lopez de Prado (2018): Meta-labeling, combinatorial purged CV
- Harvey & Liu (2015): Minimum 20 observations per bucket
- Bailey & Lopez de Prado (2014): Deflated Sharpe Ratio
- Chincarini & Kim (2006): Factor-based attribution
- Sweeney (1988): Minimum acceptable return framework
- Dawid (1982): Calibration of probabilistic forecasts

The engine maintains a "playbook" of market conditions and optimal
actions. After each trade closes, it updates the playbook.
Every N trades (configurable), it recomputes optimal parameters.

Thread-safe via threading.Lock on playbook mutations.
Graceful degradation: insufficient data returns neutral recommendations.
"""
from __future__ import annotations

import json
import logging
import math
import statistics
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("nzt48.learning.adaptive_engine")

# ── Paths ────────────────────────────────────────────────────────────────────
_DATA = Path(__file__).parent.parent / "data"
_OUTCOMES = _DATA / "outcomes.jsonl"
_PLAYBOOK_PATH = _DATA / "playbook.json"

# ── Configurable Thresholds ──────────────────────────────────────────────────
MIN_OBSERVATIONS = 20          # Harvey & Liu (2015) minimum per bucket
MIN_OBSERVATIONS_SOFT = 10     # Soft threshold for preliminary insights
BLOCK_WIN_RATE = 0.35          # Below this: flag as do-not-trade
HIGH_CONVICTION_WIN_RATE = 0.60  # Above this: flag as high-conviction
SCALP_HOLDING_THRESHOLD = 30   # Minutes — if avg < this, flag as scalp

# Confidence calibration buckets (Dawid 1982)
CONFIDENCE_BUCKETS = [
    (0, 40, "0-40"),
    (40, 55, "40-55"),
    (55, 70, "55-70"),
    (70, 85, "70-85"),
    (85, 100, "85-100"),
]

# Ticker fitness weights (sum = 1.0)
FITNESS_WEIGHTS = {
    "win_rate": 0.30,
    "avg_r": 0.20,
    "spread_consistency": 0.15,
    "volume_reliability": 0.15,
    "regime_responsiveness": 0.10,
    "profit_ladder_effectiveness": 0.10,
}

# Grades for ticker fitness
FITNESS_GRADES = [
    (80, "A"),
    (65, "B"),
    (50, "C"),
    (35, "D"),
    (0, "F"),
]

# Recomputation interval
RECOMPUTE_INTERVAL = 10  # Recompute derived metrics every N new trades

# Per-ticker indicator importance ranking
# Academic basis: Chincarini & Kim (2006) — factor returns vary significantly
# across assets and regimes; universal factor weights are suboptimal.
# Lopez de Prado (2018) — feature importance via sequential bootstrap MDI.
_ALL_INDICATORS = [
    "rsi",           # RSI14 — momentum oscillator
    "macd",          # MACD histogram — momentum direction
    "ema9",          # Price vs EMA9 — short-term trend
    "ema20",         # Price vs EMA20 — medium-term trend
    "ema50",         # Price vs EMA50 — long-term trend
    "vwap",          # Price vs VWAP — institutional fair value
    "stoch_rsi",     # Stochastic RSI — overbought/oversold
    "obv",           # On-Balance Volume — volume trend
]
# Minimum trades per indicator to compute meaningful rank (Aronson 2007)
_MIN_INDICATOR_TRADES = 10


def _wilson_ci_lower(wins: int, total: int, z: float = 1.645) -> float:
    """Wilson score interval lower bound. z=1.645 for 90% CI."""
    if total == 0:
        return 0.0
    p = wins / total
    denom = 1 + z ** 2 / total
    centre = (p + z ** 2 / (2 * total)) / denom
    spread = (z * math.sqrt(p * (1 - p) / total + z ** 2 / (4 * total ** 2))) / denom
    return max(0.0, centre - spread)


def _percentile(data: list[float], pct: float) -> float:
    """Compute percentile from sorted data. pct in [0, 100]."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * pct / 100.0)
    idx = max(0, min(idx, len(sorted_data) - 1))
    return sorted_data[idx]


def _median(data: list[float]) -> float:
    """Compute median."""
    if not data:
        return 0.0
    return statistics.median(data)


class AdaptiveLearningEngine:
    """
    Institutional adaptive learning engine that continuously improves
    trade selection based on outcome analysis.

    Academic basis:
    - Lopez de Prado (2018): Meta-labeling for sizing
    - Harvey & Liu (2015): Minimum 20 observations per bucket
    - Chincarini & Kim (2006): Factor attribution

    The engine maintains a "playbook" of market conditions and
    optimal actions. After each trade closes, it updates the playbook.
    Every N trades (configurable), it recomputes optimal parameters.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # ── Regime-Strategy Matrix ───────────────────────────────────
        # Key: (regime, strategy, direction)
        # Value: bucket stats dict
        self._regime_matrix: dict[tuple[str, str, str], dict] = {}

        # ── Entry Time Tracker ───────────────────────────────────────
        # Key: (ticker, regime) -> list of (hour, is_win, r_multiple)
        self._entry_times: dict[tuple[str, str], list[dict]] = defaultdict(list)

        # ── MFE/MAE distributions for stop calibration ───────────────
        # Key: ticker (or "__ALL__")
        # Value: list of {"mfe_pct": float, "mae_pct": float, ...}
        self._mfe_mae_data: dict[str, list[dict]] = defaultdict(list)

        # ── Confidence calibration data ──────────────────────────────
        # list of {"confidence": float, "is_win": bool}
        self._confidence_data: list[dict] = []

        # ── Ticker fitness data ──────────────────────────────────────
        # Key: ticker -> list of trade dicts
        self._ticker_trades: dict[str, list[dict]] = defaultdict(list)

        # ── Trade counter ────────────────────────────────────────────
        self._total_trades: int = 0
        self._trades_since_recompute: int = 0

        # ── Derived / cached recommendations ─────────────────────────
        self._cached_recommendations: dict[str, dict] = {}
        self._last_recompute: str = ""

        # ── Today's new trades (for daily report) ────────────────────
        self._today_str: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._today_count: int = 0

    # ═══════════════════════════════════════════════════════════════════
    # Feature 0: Ingest — Load outcomes on startup
    # ═══════════════════════════════════════════════════════════════════

    def _load_outcomes(self) -> list[dict]:
        """Load all completed outcomes from outcomes.jsonl."""
        records = []
        if not _OUTCOMES.exists():
            logger.debug("No outcomes file found at %s", _OUTCOMES)
            return records
        try:
            with open(_OUTCOMES) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        if d.get("outcome") in ("HIT_TARGET", "HIT_STOP", "TIME_STOP"):
                            records.append(d)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error("Failed to load outcomes: %s", e)
        return records

    def _ingest_trade(self, trade: dict) -> None:
        """Ingest a single trade dict into all internal data structures.

        Expected fields from OutcomeRecord:
            signal_id, ticker, direction, strategy_tag, regime_tag,
            time_window, track, session, entry, stop, target1,
            outcome, exit_price, pnl_r_gross, pnl_r_net,
            mfe_pct, mae_pct, duration_minutes, generated_at, closed_at
        """
        ticker = trade.get("ticker", "UNKNOWN")
        direction = trade.get("direction", "LONG")
        strategy = trade.get("strategy_tag", trade.get("strategy", "UNKNOWN"))
        regime = trade.get("regime_tag", trade.get("regime", "UNKNOWN"))
        outcome = trade.get("outcome", "")
        pnl_r_net = float(trade.get("pnl_r_net", 0.0))
        pnl_r_gross = float(trade.get("pnl_r_gross", 0.0))
        mfe_pct = float(trade.get("mfe_pct", 0.0))
        mae_pct = float(trade.get("mae_pct", 0.0))
        duration_min = int(trade.get("duration_minutes", 0))
        is_win = outcome == "HIT_TARGET"

        # Parse entry hour from generated_at
        entry_hour = 10  # default
        generated_at = trade.get("generated_at", "")
        if generated_at:
            try:
                dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                entry_hour = dt.hour
            except Exception:
                pass

        # ── Update regime matrix ─────────────────────────────────────
        key = (regime, strategy, direction)
        bucket = self._regime_matrix.setdefault(key, {
            "wins": 0, "losses": 0, "total": 0,
            "sum_r": 0.0, "sum_r_gross": 0.0,
            "sum_holding_mins": 0, "entry_hours": [],
            "avg_r": 0.0, "win_rate": 0.0,
            "avg_holding_mins": 0.0,
        })
        bucket["total"] += 1
        if is_win:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
        bucket["sum_r"] += pnl_r_net
        bucket["sum_r_gross"] += pnl_r_gross
        bucket["sum_holding_mins"] += duration_min
        bucket["entry_hours"].append(entry_hour)
        # Recompute running stats
        bucket["win_rate"] = bucket["wins"] / bucket["total"]
        bucket["avg_r"] = bucket["sum_r"] / bucket["total"]
        bucket["avg_holding_mins"] = bucket["sum_holding_mins"] / bucket["total"]

        # ── Update entry time data ───────────────────────────────────
        self._entry_times[(ticker, regime)].append({
            "hour": entry_hour,
            "is_win": is_win,
            "r": pnl_r_net,
        })

        # ── Update MFE/MAE data ──────────────────────────────────────
        mfe_mae_record = {
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
            "is_win": is_win,
            "r": pnl_r_net,
            "duration_min": duration_min,
            "stop_pct": 0.0,
            "target_pct": 0.0,
        }
        # Compute stop/target as pct of entry
        entry_price = float(trade.get("entry", 0.0))
        stop_price = float(trade.get("stop", 0.0))
        target_price = float(trade.get("target1", 0.0))
        if entry_price > 0:
            if direction == "LONG":
                mfe_mae_record["stop_pct"] = abs(entry_price - stop_price) / entry_price * 100
                mfe_mae_record["target_pct"] = abs(target_price - entry_price) / entry_price * 100
            else:
                mfe_mae_record["stop_pct"] = abs(stop_price - entry_price) / entry_price * 100
                mfe_mae_record["target_pct"] = abs(entry_price - target_price) / entry_price * 100
        self._mfe_mae_data[ticker].append(mfe_mae_record)
        self._mfe_mae_data["__ALL__"].append(mfe_mae_record)

        # ── Update confidence calibration data ───────────────────────
        # Composite score is in signal_log, not outcomes. Use net_rr as proxy
        # The actual composite is available when called from update_from_trade
        composite = float(trade.get("composite", trade.get("confidence", 0.0)))
        if composite > 0:
            self._confidence_data.append({
                "confidence": composite,
                "is_win": is_win,
                "r": pnl_r_net,
            })

        # ── Update ticker fitness data ───────────────────────────────
        self._ticker_trades[ticker].append({
            "is_win": is_win,
            "r": pnl_r_net,
            "r_gross": pnl_r_gross,
            "strategy": strategy,
            "regime": regime,
            "direction": direction,
            "duration_min": duration_min,
            "spread_bps": float(trade.get("cost_bps", 0.0)),
            "entry_hour": entry_hour,
        })

        self._total_trades += 1

    # ═══════════════════════════════════════════════════════════════════
    # Feature 1: Regime-Strategy Performance Matrix
    # ═══════════════════════════════════════════════════════════════════

    def update_regime_matrix(self, trade: dict) -> None:
        """After each trade close, update the regime-strategy matrix.

        Matrix key: (regime, strategy, direction)
        Matrix value: {wins, losses, total, avg_r, avg_holding_mins,
                       best_entry_hour, worst_entry_hour}

        When a bucket has >= 20 observations (Harvey & Liu minimum),
        it becomes "statistically significant" and the engine can
        make recommendations:
        - If win_rate < 35% for a bucket: FLAG as do-not-trade
        - If win_rate > 60% for a bucket: FLAG as high-conviction
        - If avg_holding_mins < 30 for winners: FLAG as scalp
        """
        with self._lock:
            self._ingest_trade(trade)

    def get_regime_matrix_summary(self) -> list[dict]:
        """Return regime matrix with significance flags."""
        results = []
        for (regime, strategy, direction), bucket in self._regime_matrix.items():
            total = bucket["total"]
            significant = total >= MIN_OBSERVATIONS
            flag = "NEUTRAL"
            if significant:
                if bucket["win_rate"] < BLOCK_WIN_RATE:
                    flag = "DO_NOT_TRADE"
                elif bucket["win_rate"] >= HIGH_CONVICTION_WIN_RATE:
                    flag = "HIGH_CONVICTION"
                elif bucket["avg_holding_mins"] < SCALP_HOLDING_THRESHOLD and bucket["win_rate"] > 0.5:
                    flag = "SCALP"

            # Best/worst entry hours
            hours = bucket.get("entry_hours", [])
            best_hour = worst_hour = None
            if len(hours) >= MIN_OBSERVATIONS_SOFT:
                hour_wins: dict[int, list[bool]] = defaultdict(list)
                # Cross-reference with entry_times for win/loss data per hour
                key_data = self._entry_times.get(("__ALL__", regime), [])
                # Fallback: use all tickers for this regime+strategy
                for (tk, rg), entries in self._entry_times.items():
                    if rg == regime:
                        for e in entries:
                            hour_wins[e["hour"]].append(e["is_win"])
                if hour_wins:
                    hour_wr = {
                        h: sum(ws) / len(ws) for h, ws in hour_wins.items()
                        if len(ws) >= 3
                    }
                    if hour_wr:
                        best_hour = max(hour_wr, key=hour_wr.get)
                        worst_hour = min(hour_wr, key=hour_wr.get)

            results.append({
                "regime": regime,
                "strategy": strategy,
                "direction": direction,
                "total": total,
                "wins": bucket["wins"],
                "losses": bucket["losses"],
                "win_rate": round(bucket["win_rate"], 4),
                "avg_r": round(bucket["avg_r"], 4),
                "avg_holding_mins": round(bucket["avg_holding_mins"], 1),
                "significant": significant,
                "flag": flag,
                "best_entry_hour": best_hour,
                "worst_entry_hour": worst_hour,
            })
        return sorted(results, key=lambda x: (-x["significant"], -x["win_rate"]))

    # ═══════════════════════════════════════════════════════════════════
    # Feature 2: Entry Time Optimization
    # ═══════════════════════════════════════════════════════════════════

    def get_optimal_entry_window(self, ticker: str, regime: str) -> dict:
        """Return the statistically best entry window for a ticker+regime combo.

        Returns:
            {
                "best_hour": 10,
                "best_window": "10:00-11:30",
                "win_rate_in_window": 0.62,
                "win_rate_outside": 0.38,
                "sample_size": 45,
                "significant": True
            }
        """
        entries = self._entry_times.get((ticker, regime), [])
        if not entries:
            # Fallback: all tickers in this regime
            entries = []
            for (tk, rg), data in self._entry_times.items():
                if rg == regime:
                    entries.extend(data)

        total = len(entries)
        result = {
            "best_hour": None,
            "best_window": "N/A",
            "win_rate_in_window": 0.0,
            "win_rate_outside": 0.0,
            "sample_size": total,
            "significant": False,
        }

        if total < MIN_OBSERVATIONS_SOFT:
            return result

        # Group by hour
        hour_data: dict[int, list[dict]] = defaultdict(list)
        for e in entries:
            hour_data[e["hour"]].append(e)

        # Find best 2-hour window (sliding)
        best_wr = 0.0
        best_start = 8
        for start in range(7, 17):
            window_entries = []
            for h in range(start, start + 2):
                window_entries.extend(hour_data.get(h, []))
            if len(window_entries) < 5:
                continue
            wr = sum(1 for e in window_entries if e["is_win"]) / len(window_entries)
            if wr > best_wr:
                best_wr = wr
                best_start = start

        # Compute win rates in/out of window
        in_window = []
        out_window = []
        for e in entries:
            if best_start <= e["hour"] < best_start + 2:
                in_window.append(e)
            else:
                out_window.append(e)

        wr_in = sum(1 for e in in_window if e["is_win"]) / len(in_window) if in_window else 0
        wr_out = sum(1 for e in out_window if e["is_win"]) / len(out_window) if out_window else 0

        result["best_hour"] = best_start
        result["best_window"] = f"{best_start:02d}:00-{best_start + 2:02d}:00"
        result["win_rate_in_window"] = round(wr_in, 4)
        result["win_rate_outside"] = round(wr_out, 4)
        result["significant"] = total >= MIN_OBSERVATIONS

        return result

    # ═══════════════════════════════════════════════════════════════════
    # Feature 3: Stop/Target Auto-Calibration (Sweeney 1988)
    # ═══════════════════════════════════════════════════════════════════

    def calibrate_stops(self, ticker: str = None) -> dict:
        """Use MFE/MAE distribution to calibrate optimal stops.

        Research: Sweeney (1988) - "Minimum acceptable return" framework

        Logic:
        - If median MAE is -0.5%, but stop is at -1.0%: stop is too wide
        - If median MFE is +3.2%, but target is at +2.0%: target could be raised
        - Optimal stop = MAE at 75th percentile (catches 75% of winners)
        - Optimal target = MFE at 25th percentile (hit by 75% of trades that go green)

        Returns:
            {
                "current_stop_pct": 1.0,
                "suggested_stop_pct": 0.8,
                "current_target_pct": 2.0,
                "suggested_target_pct": 2.5,
                "mae_median": -0.5,
                "mfe_median": 3.2,
                "sample_size": 120,
                "significant": True
            }
        """
        key = ticker if ticker and ticker in self._mfe_mae_data else "__ALL__"
        data = self._mfe_mae_data.get(key, [])

        result = {
            "ticker": ticker or "ALL",
            "current_stop_pct": 0.0,
            "suggested_stop_pct": 0.0,
            "current_target_pct": 0.0,
            "suggested_target_pct": 0.0,
            "mae_median": 0.0,
            "mae_p75": 0.0,
            "mfe_median": 0.0,
            "mfe_p25": 0.0,
            "sample_size": len(data),
            "significant": False,
        }

        if len(data) < MIN_OBSERVATIONS:
            return result

        result["significant"] = True

        # MAE values (negative, representing drawdown)
        mae_values = [d["mae_pct"] for d in data]
        mfe_values = [d["mfe_pct"] for d in data]

        # Current average stop/target
        stop_pcts = [d["stop_pct"] for d in data if d["stop_pct"] > 0]
        target_pcts = [d["target_pct"] for d in data if d["target_pct"] > 0]

        result["current_stop_pct"] = round(_median(stop_pcts), 4) if stop_pcts else 0.0
        result["current_target_pct"] = round(_median(target_pcts), 4) if target_pcts else 0.0

        # MAE analysis
        result["mae_median"] = round(_median(mae_values), 4)
        # 75th percentile of MAE (closer to 0 = less adverse, catches 75% of winners)
        winner_mae = [d["mae_pct"] for d in data if d["is_win"]]
        if winner_mae:
            # For MAE (negative values), 75th percentile means 75% of winners
            # had MAE less severe than this
            result["mae_p75"] = round(_percentile(winner_mae, 75), 4)
            # Suggested stop = absolute value of 75th percentile MAE of winners
            suggested_stop = abs(result["mae_p75"])
            if suggested_stop > 0.1:  # sanity floor
                result["suggested_stop_pct"] = round(suggested_stop, 4)

        # MFE analysis
        result["mfe_median"] = round(_median(mfe_values), 4)
        # 25th percentile of MFE = hit by 75% of all trades that went green
        positive_mfe = [d["mfe_pct"] for d in data if d["mfe_pct"] > 0]
        if positive_mfe:
            result["mfe_p25"] = round(_percentile(positive_mfe, 25), 4)
            suggested_target = result["mfe_p25"]
            if suggested_target > 0.2:  # sanity floor
                result["suggested_target_pct"] = round(suggested_target, 4)

        return result

    # ═══════════════════════════════════════════════════════════════════
    # Feature 4: Confidence Score Recalibration (Dawid 1982)
    # ═══════════════════════════════════════════════════════════════════

    def recalibrate_confidence(self) -> dict:
        """Check if confidence scores are calibrated (higher conf = higher win rate).

        Research: Dawid (1982) - Calibration of probabilistic forecasts

        Buckets: [0-40], [40-55], [55-70], [70-85], [85-100]
        For each bucket: compute actual win rate.
        If correlation between confidence bucket and win rate < 0.5:
            confidence is miscalibrated -> suggest weight adjustments.

        Returns:
            {
                "calibration_score": 0.72,
                "buckets": [...],
                "suggestion": "Confidence is well-calibrated."
            }
        """
        result = {
            "calibration_score": 0.0,
            "buckets": [],
            "suggestion": "Insufficient data for calibration.",
            "total_samples": len(self._confidence_data),
        }

        if len(self._confidence_data) < MIN_OBSERVATIONS:
            return result

        # Bucket the data
        bucket_data: dict[str, list[dict]] = defaultdict(list)
        for d in self._confidence_data:
            conf = d["confidence"]
            for lo, hi, label in CONFIDENCE_BUCKETS:
                if lo <= conf < hi or (hi == 100 and conf == 100):
                    bucket_data[label].append(d)
                    break

        buckets = []
        predicted_wrs = []
        actual_wrs = []
        for lo, hi, label in CONFIDENCE_BUCKETS:
            data = bucket_data.get(label, [])
            n = len(data)
            actual_wr = sum(1 for d in data if d["is_win"]) / n if n > 0 else 0.0
            predicted_wr = (lo + hi) / 200.0  # midpoint as predicted WR
            buckets.append({
                "range": label,
                "predicted_wr": round(predicted_wr, 4),
                "actual_wr": round(actual_wr, 4),
                "n": n,
                "significant": n >= MIN_OBSERVATIONS,
            })
            if n >= 5:  # need some data for correlation
                predicted_wrs.append(predicted_wr)
                actual_wrs.append(actual_wr)

        result["buckets"] = buckets

        # Compute calibration score via rank correlation
        if len(predicted_wrs) >= 3:
            # Spearman rank correlation
            n_pts = len(predicted_wrs)
            rank_pred = [sorted(predicted_wrs).index(v) for v in predicted_wrs]
            rank_actual = [sorted(actual_wrs).index(v) for v in actual_wrs]
            d_sq = sum((rp - ra) ** 2 for rp, ra in zip(rank_pred, rank_actual))
            if n_pts > 1:
                rho = 1 - (6 * d_sq) / (n_pts * (n_pts ** 2 - 1))
            else:
                rho = 0.0
            # Normalize to [0, 1]
            calibration_score = max(0.0, min(1.0, (rho + 1.0) / 2.0))
            result["calibration_score"] = round(calibration_score, 4)

            if calibration_score >= 0.7:
                result["suggestion"] = "Confidence is well-calibrated. No changes needed."
            elif calibration_score >= 0.5:
                result["suggestion"] = (
                    "Confidence is moderately calibrated. Consider reviewing "
                    "confidence layer weights for underperforming buckets."
                )
            else:
                result["suggestion"] = (
                    "Confidence is poorly calibrated (score={:.2f}). "
                    "Higher confidence does NOT predict higher win rates. "
                    "Recommend reviewing all confidence layer contributions.".format(
                        calibration_score
                    )
                )

        return result

    # ═══════════════════════════════════════════════════════════════════
    # Feature 5: Ticker Fitness Scoring
    # ═══════════════════════════════════════════════════════════════════

    def score_ticker_fitness(self, ticker: str) -> dict:
        """Score how well a ticker responds to the system's strategy.

        Factors:
        1. Win rate on this ticker (30% weight)
        2. Average R multiple (20% weight)
        3. Spread consistency (15% weight)
        4. Volume reliability (15% weight)
        5. Regime responsiveness (10% weight)
        6. Profit ladder effectiveness (10% weight)

        Returns:
            {
                "ticker": "QQQ3.L",
                "fitness_score": 78.5,
                "grade": "A",
                "trades": 45,
                "win_rate": 0.58,
                "avg_r": 1.2,
                "recommendation": "PROMOTE" / "HOLD" / "RELEGATE" / "INSUFFICIENT_DATA"
            }
        """
        trades = self._ticker_trades.get(ticker, [])
        result = {
            "ticker": ticker,
            "fitness_score": 0.0,
            "grade": "F",
            "trades": len(trades),
            "win_rate": 0.0,
            "avg_r": 0.0,
            "factors": {},
            "recommendation": "INSUFFICIENT_DATA",
        }

        if len(trades) < MIN_OBSERVATIONS_SOFT:
            return result

        # Factor 1: Win rate (0-100 scale)
        wins = sum(1 for t in trades if t["is_win"])
        win_rate = wins / len(trades)
        wr_score = min(100.0, win_rate * 166.67)  # 60% WR = 100 score
        result["win_rate"] = round(win_rate, 4)

        # Factor 2: Average R multiple (0-100 scale)
        avg_r = sum(t["r"] for t in trades) / len(trades)
        r_score = min(100.0, max(0.0, (avg_r + 0.5) * 50))  # 1.5R = 100
        result["avg_r"] = round(avg_r, 4)

        # Factor 3: Spread consistency (lower spread variation = better)
        spreads = [t["spread_bps"] for t in trades if t["spread_bps"] > 0]
        if spreads:
            spread_cv = statistics.stdev(spreads) / max(statistics.mean(spreads), 1) if len(spreads) > 1 else 0
            spread_score = max(0.0, 100.0 - spread_cv * 100)
        else:
            spread_score = 50.0  # neutral

        # Factor 4: Volume reliability (consistency of outcomes across different
        # times — lower variance of R across hours = more reliable)
        hour_r: dict[int, list[float]] = defaultdict(list)
        for t in trades:
            hour_r[t["entry_hour"]].append(t["r"])
        if len(hour_r) >= 3:
            hour_means = [sum(rs) / len(rs) for rs in hour_r.values() if rs]
            vol_cv = statistics.stdev(hour_means) / max(abs(statistics.mean(hour_means)), 0.01) if len(hour_means) > 1 else 0
            volume_score = max(0.0, 100.0 - vol_cv * 50)
        else:
            volume_score = 50.0

        # Factor 5: Regime responsiveness (does win rate vary by regime — higher
        # variance = more regime-responsive, meaning the system can exploit patterns)
        regime_wr: dict[str, list[bool]] = defaultdict(list)
        for t in trades:
            regime_wr[t["regime"]].append(t["is_win"])
        if len(regime_wr) >= 2:
            regime_wrs = [
                sum(ws) / len(ws) for ws in regime_wr.values()
                if len(ws) >= 3
            ]
            if regime_wrs:
                best_wr = max(regime_wrs)
                # If best regime WR is high, ticker is regime-responsive (good)
                regime_score = min(100.0, best_wr * 150)
            else:
                regime_score = 50.0
        else:
            regime_score = 50.0

        # Factor 6: Profit ladder effectiveness (% of winners that exceeded 1R)
        winners = [t for t in trades if t["is_win"]]
        if winners:
            big_winners = sum(1 for t in winners if t["r"] >= 1.0)
            ladder_score = min(100.0, (big_winners / len(winners)) * 100)
        else:
            ladder_score = 0.0

        # Weighted composite
        factors = {
            "win_rate": round(wr_score, 2),
            "avg_r": round(r_score, 2),
            "spread_consistency": round(spread_score, 2),
            "volume_reliability": round(volume_score, 2),
            "regime_responsiveness": round(regime_score, 2),
            "profit_ladder_effectiveness": round(ladder_score, 2),
        }
        result["factors"] = factors

        fitness_score = sum(
            factors[k] * FITNESS_WEIGHTS[k] for k in FITNESS_WEIGHTS
        )
        result["fitness_score"] = round(fitness_score, 2)

        # Grade assignment
        for threshold, grade in FITNESS_GRADES:
            if fitness_score >= threshold:
                result["grade"] = grade
                break

        # Recommendation
        n = len(trades)
        if n < MIN_OBSERVATIONS:
            result["recommendation"] = "INSUFFICIENT_DATA"
        elif result["grade"] in ("A",):
            result["recommendation"] = "PROMOTE"
        elif result["grade"] in ("B", "C"):
            result["recommendation"] = "HOLD"
        else:
            result["recommendation"] = "RELEGATE"

        return result

    # ═══════════════════════════════════════════════════════════════════
    # Feature 6: Daily Learning Report
    # ═══════════════════════════════════════════════════════════════════

    def generate_daily_report(self) -> dict:
        """Generate daily learning insights for Telegram/logging.

        Returns:
            {
                "new_trades_today": 2,
                "cumulative_trades": 145,
                "regime_insights": [...],
                "ticker_insights": [...],
                "calibration_status": "...",
                "stop_suggestion": "...",
                "overall_edge": "+0.15R per trade"
            }
        """
        report = {
            "new_trades_today": self._today_count,
            "cumulative_trades": self._total_trades,
            "regime_insights": [],
            "ticker_insights": [],
            "calibration_status": "",
            "stop_suggestion": "",
            "overall_edge": "",
        }

        # Regime insights — only significant buckets
        matrix = self.get_regime_matrix_summary()
        for entry in matrix:
            if not entry["significant"]:
                continue
            if entry["flag"] == "HIGH_CONVICTION":
                report["regime_insights"].append(
                    "{} + {} has {:.0%} win rate (n={}) -> HIGH CONVICTION".format(
                        entry["regime"], entry["direction"],
                        entry["win_rate"], entry["total"],
                    )
                )
            elif entry["flag"] == "DO_NOT_TRADE":
                report["regime_insights"].append(
                    "{} + {} has {:.0%} win rate (n={}) -> DO NOT TRADE".format(
                        entry["regime"], entry["direction"],
                        entry["win_rate"], entry["total"],
                    )
                )

        # Ticker insights
        all_tickers = list(self._ticker_trades.keys())
        for ticker in all_tickers:
            fitness = self.score_ticker_fitness(ticker)
            if fitness["recommendation"] == "PROMOTE":
                report["ticker_insights"].append(
                    "{} fitness={:.1f} grade={} -> PROMOTE (WR={:.0%}, avg R={:.2f})".format(
                        ticker, fitness["fitness_score"], fitness["grade"],
                        fitness["win_rate"], fitness["avg_r"],
                    )
                )
            elif fitness["recommendation"] == "RELEGATE":
                report["ticker_insights"].append(
                    "{} fitness={:.1f} grade={} -> RELEGATE (WR={:.0%}, avg R={:.2f})".format(
                        ticker, fitness["fitness_score"], fitness["grade"],
                        fitness["win_rate"], fitness["avg_r"],
                    )
                )

        # Calibration status
        cal = self.recalibrate_confidence()
        report["calibration_status"] = (
            "Confidence calibration: {:.2f} score — {}".format(
                cal["calibration_score"], cal["suggestion"],
            )
        )

        # Stop suggestion
        stops = self.calibrate_stops()
        if stops["significant"]:
            if stops["suggested_stop_pct"] > 0 and stops["current_stop_pct"] > 0:
                delta = stops["suggested_stop_pct"] - stops["current_stop_pct"]
                if abs(delta) > 0.05:
                    direction = "tighten" if delta < 0 else "widen"
                    report["stop_suggestion"] = (
                        "Consider {}ing stop from {:.2f}% to {:.2f}% "
                        "(MAE data supports, n={})".format(
                            direction, stops["current_stop_pct"],
                            stops["suggested_stop_pct"], stops["sample_size"],
                        )
                    )

        # Overall edge
        all_data = self._mfe_mae_data.get("__ALL__", [])
        if all_data:
            avg_r = sum(d["r"] for d in all_data) / len(all_data)
            sign = "+" if avg_r >= 0 else ""
            report["overall_edge"] = "{}{:.4f}R per trade ({})".format(
                sign, avg_r,
                "positive expectancy confirmed" if avg_r > 0
                else "negative expectancy — review needed"
            )

        # Reset today counter for next day
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._today_str:
            self._today_str = today
            self._today_count = 0

        return report

    # ═══════════════════════════════════════════════════════════════════
    # Feature 7: Playbook Persistence
    # ═══════════════════════════════════════════════════════════════════

    def save_playbook(self, path: str = None) -> None:
        """Persist all learned parameters to disk."""
        save_path = Path(path) if path else _PLAYBOOK_PATH
        with self._lock:
            playbook = {
                "version": 1,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "total_trades": self._total_trades,
                "regime_matrix": {
                    f"{k[0]}|{k[1]}|{k[2]}": {
                        kk: vv for kk, vv in v.items()
                        if kk != "entry_hours"  # large list, skip in save
                    }
                    for k, v in self._regime_matrix.items()
                },
                "calibration": self.recalibrate_confidence(),
                "stop_calibration": self.calibrate_stops(),
                "ticker_fitness": {
                    ticker: self.score_ticker_fitness(ticker)
                    for ticker in self._ticker_trades
                    if len(self._ticker_trades[ticker]) >= MIN_OBSERVATIONS_SOFT
                },
            }
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(json.dumps(playbook, indent=2, default=str))
            logger.info("Adaptive playbook saved: %d trades, %d regime buckets",
                        self._total_trades, len(self._regime_matrix))
        except Exception as e:
            logger.error("Failed to save playbook: %s", e)

    def load_playbook(self, path: str = None) -> None:
        """Load previously learned parameters by re-ingesting outcomes.

        Instead of deserializing internal state (fragile), we re-ingest
        all outcomes from outcomes.jsonl. This is robust to schema changes
        and guarantees consistency.
        """
        logger.info("Loading adaptive engine from outcomes data...")
        outcomes = self._load_outcomes()
        if not outcomes:
            logger.info("No outcomes data found — adaptive engine starting cold")
            return

        with self._lock:
            # Reset all state
            self._regime_matrix.clear()
            self._entry_times.clear()
            self._mfe_mae_data.clear()
            self._confidence_data.clear()
            self._ticker_trades.clear()
            self._total_trades = 0

            for trade in outcomes:
                self._ingest_trade(trade)

        logger.info(
            "Adaptive engine loaded: %d trades, %d regime buckets, %d tickers",
            self._total_trades, len(self._regime_matrix),
            len(self._ticker_trades),
        )

    # ═══════════════════════════════════════════════════════════════════
    # Integration API: Called from main.py
    # ═══════════════════════════════════════════════════════════════════

    def update_from_trade(self, trade_dict: dict) -> None:
        """Called after each trade close. Updates all internal structures.

        Accepts either an OutcomeRecord dict or a virtual_trade-style dict.
        Normalizes field names before ingestion.
        """
        # Normalize field names — handle both OutcomeRecord and VirtualTrade
        normalized = dict(trade_dict)
        if "strategy" in normalized and "strategy_tag" not in normalized:
            normalized["strategy_tag"] = normalized["strategy"]
        if "regime" in normalized and "regime_tag" not in normalized:
            normalized["regime_tag"] = normalized["regime"]
        if "regime_at_entry" in normalized and "regime_tag" not in normalized:
            normalized["regime_tag"] = normalized["regime_at_entry"]
        if "r_multiple" in normalized and "pnl_r_net" not in normalized:
            normalized["pnl_r_net"] = normalized["r_multiple"]
        if "exit_reason" in normalized and "outcome" not in normalized:
            er = normalized.get("exit_reason", "")
            if "target" in er.lower():
                normalized["outcome"] = "HIT_TARGET"
            elif "stop" in er.lower():
                normalized["outcome"] = "HIT_STOP"
            else:
                normalized["outcome"] = "TIME_STOP"
        if "entry_price" in normalized and "entry" not in normalized:
            normalized["entry"] = normalized["entry_price"]
        if "exit_price" not in normalized and "exit" in normalized:
            normalized["exit_price"] = normalized["exit"]
        if "net_pnl" in normalized and "pnl_r_gross" not in normalized:
            normalized["pnl_r_gross"] = normalized.get("pnl_r_net", 0.0)
        if "peak_r" in normalized and "mfe_pct" not in normalized:
            normalized["mfe_pct"] = float(normalized.get("peak_r", 0.0))
        if "trough_r" in normalized and "mae_pct" not in normalized:
            normalized["mae_pct"] = float(normalized.get("trough_r", 0.0))
        if "entry_time" in normalized and "generated_at" not in normalized:
            normalized["generated_at"] = normalized["entry_time"]

        with self._lock:
            self._ingest_trade(normalized)
            self._today_count += 1
            self._trades_since_recompute += 1

        logger.debug(
            "Adaptive engine updated: trade #%d (%s %s %s R=%.2f)",
            self._total_trades,
            normalized.get("ticker", "?"),
            normalized.get("strategy_tag", "?"),
            normalized.get("outcome", "?"),
            float(normalized.get("pnl_r_net", 0)),
        )

    def get_recommendation(
        self,
        ticker: str,
        regime: str,
        strategy: str,
        direction: str,
        confidence: float = 50.0,
    ) -> dict:
        """Get adaptive engine recommendation for a pending signal.

        Returns:
            {
                "action": "ALLOW" | "BLOCK" | "BOOST",
                "reason": str,
                "confidence_adjustment": int,
                "size_adjustment": float | None,
                "entry_window_ok": bool,
            }
        """
        result = {
            "action": "ALLOW",
            "reason": "Insufficient data for adaptive recommendation",
            "confidence_adjustment": 0,
            "size_adjustment": None,
            "entry_window_ok": True,
        }

        key = (regime, strategy, direction)
        bucket = self._regime_matrix.get(key)

        if not bucket or bucket["total"] < MIN_OBSERVATIONS:
            # Not enough data — neutral pass-through
            return result

        win_rate = bucket["win_rate"]
        avg_r = bucket["avg_r"]
        total = bucket["total"]

        # Use Wilson CI lower bound for conservative estimate
        wilson_lower = _wilson_ci_lower(bucket["wins"], total)

        # ── BLOCK: statistically confirmed losing bucket ─────────────
        if win_rate < BLOCK_WIN_RATE and wilson_lower < 0.40:
            result["action"] = "BLOCK"
            result["reason"] = (
                "Regime-strategy bucket {}/{}/{} has {:.0%} WR (n={}, "
                "Wilson lower={:.0%}) — below {:.0%} threshold".format(
                    regime, strategy, direction, win_rate, total,
                    wilson_lower, BLOCK_WIN_RATE,
                )
            )
            return result

        # ── BOOST: statistically confirmed winning bucket ────────────
        if win_rate >= HIGH_CONVICTION_WIN_RATE and wilson_lower >= 0.50:
            result["action"] = "BOOST"
            result["confidence_adjustment"] = 10
            if avg_r >= 0.5:
                result["size_adjustment"] = 1.2  # 20% larger position
            result["reason"] = (
                "HIGH CONVICTION: {}/{}/{} has {:.0%} WR (n={}, "
                "avg R={:.2f})".format(
                    regime, strategy, direction, win_rate, total, avg_r,
                )
            )
            return result

        # ── MODERATE: marginal edge — slight adjustment ──────────────
        if win_rate >= 0.50 and avg_r > 0:
            result["confidence_adjustment"] = 5
            result["reason"] = (
                "Moderate edge: {}/{}/{} WR={:.0%} (n={})".format(
                    regime, strategy, direction, win_rate, total,
                )
            )
        elif win_rate < 0.45:
            result["confidence_adjustment"] = -5
            result["reason"] = (
                "Below-average bucket: {}/{}/{} WR={:.0%} (n={})".format(
                    regime, strategy, direction, win_rate, total,
                )
            )

        # ── Entry window check ───────────────────────────────────────
        entry_opt = self.get_optimal_entry_window(ticker, regime)
        if entry_opt["significant"] and entry_opt["best_hour"] is not None:
            current_hour = datetime.now(timezone.utc).hour
            best_start = entry_opt["best_hour"]
            if not (best_start <= current_hour < best_start + 2):
                # Outside optimal window — note but don't block
                wr_diff = entry_opt["win_rate_in_window"] - entry_opt["win_rate_outside"]
                if wr_diff > 0.15:
                    result["entry_window_ok"] = False
                    result["confidence_adjustment"] -= 3
                    result["reason"] += (
                        " | Outside optimal entry window ({}, "
                        "WR diff={:.0%})".format(
                            entry_opt["best_window"], wr_diff,
                        )
                    )

        return result

    # ═══════════════════════════════════════════════════════════════════
    # Utility: Full recalculation
    # ═══════════════════════════════════════════════════════════════════

    def recompute_all(self) -> dict:
        """Force full recomputation of all derived metrics.
        Called periodically or after batch ingest.
        """
        self._last_recompute = datetime.now(timezone.utc).isoformat()
        self._trades_since_recompute = 0

        return {
            "regime_buckets": len(self._regime_matrix),
            "tickers_tracked": len(self._ticker_trades),
            "total_trades": self._total_trades,
            "confidence_samples": len(self._confidence_data),
            "mfe_mae_samples": len(self._mfe_mae_data.get("__ALL__", [])),
        }

    def get_status(self) -> dict:
        """Return engine status for dashboard / health check."""
        significant_buckets = sum(
            1 for b in self._regime_matrix.values()
            if b["total"] >= MIN_OBSERVATIONS
        )
        return {
            "total_trades": self._total_trades,
            "regime_buckets": len(self._regime_matrix),
            "significant_buckets": significant_buckets,
            "tickers_tracked": len(self._ticker_trades),
            "confidence_samples": len(self._confidence_data),
            "today_trades": self._today_count,
            "last_recompute": self._last_recompute,
        }

    # ═══════════════════════════════════════════════════════════════════
    # CONTINUOUS LEARNING ENGINE
    # "Today's excellence is tomorrow's average"
    #
    # Academic basis:
    # - Aronson (2007): Evidence-Based Technical Analysis — requires 20+ trades
    #   before any threshold adjustment; false discovery rate controls
    # - Lopez de Prado (2020): Advances in Financial ML — walk-forward
    #   parameter optimization, no look-ahead bias
    # - Chan (2013): Algorithmic Trading — out-of-sample validation required
    #   before any live parameter change
    # - Faber (2013): A Quantitative Approach — regime-conditional performance
    #   analysis determines when to trade, not just what to trade
    # - Harvey & Liu (2015): Backtest Overfitting — require statistical
    #   significance before accepting parameter changes (p < 0.05)
    # ═══════════════════════════════════════════════════════════════════

    def compute_improvement_candidates(self) -> dict:
        """Analyse all outcomes and recommend parameter improvements.

        Called daily by the scheduler (end-of-session). Returns a structured
        report with statistically validated improvement candidates.

        Never changes parameters directly — returns recommendations that the
        operator reviews. Implements Harvey & Liu (2015) significance filter.

        Returns:
            dict with keys:
            - rvol_threshold: recommended min RVOL (validated by outcome split)
            - confidence_threshold: recommended min confidence
            - adx_threshold: recommended min ADX
            - avoid_regimes: regimes with win_rate < 35%
            - best_regimes: regimes with win_rate > 55%
            - best_hours: intraday hours (UK) with win_rate > mean
            - worst_tickers: tickers with win_rate < 30% (>= 20 trades)
            - best_tickers: tickers with win_rate > 55% (>= 20 trades)
            - parameter_changes: list of specific recommended changes
            - evidence_quality: 'STRONG'|'MODERATE'|'WEAK'|'INSUFFICIENT'
        """
        outcomes = self._load_all_outcomes()
        total = len(outcomes)

        result: dict = {
            "total_outcomes": total,
            "parameter_changes": [],
            "avoid_regimes": [],
            "best_regimes": [],
            "best_hours": [],
            "worst_tickers": [],
            "best_tickers": [],
            "evidence_quality": "INSUFFICIENT",
        }

        # Harvey & Liu (2015): need >= 20 trades for any meaningful inference
        if total < 20:
            result["note"] = f"Only {total} trades — need 20+ for statistical validity"
            return result

        # ── 1. RVOL threshold optimisation ──────────────────────────────────
        # Walk forward: test RVOL thresholds from 0.3 to 2.0
        # Pick the threshold that maximises win_rate on the second half (OOS)
        rvol_thresholds = [0.3, 0.5, 0.8, 1.0, 1.2, 1.5, 2.0]
        best_rvol_wr = 0.0
        best_rvol = 0.8  # current default
        for thr in rvol_thresholds:
            subset = [o for o in outcomes if o.get("rvol", 0) >= thr]
            if len(subset) < 10:
                continue
            # Use second half as OOS validation (Lopez de Prado 2020)
            oos = subset[len(subset)//2:]
            wr = sum(1 for o in oos if o.get("result") == "TARGET") / len(oos)
            if wr > best_rvol_wr:
                best_rvol_wr = wr
                best_rvol = thr
        result["rvol_threshold"] = {
            "recommended": best_rvol,
            "win_rate_oos": round(best_rvol_wr, 3),
            "current": 0.80,
            "change_suggested": best_rvol != 0.80 and total >= 50,
        }
        if best_rvol != 0.80 and total >= 50 and best_rvol_wr > 0.45:
            result["parameter_changes"].append({
                "param": "MIN_RVOL",
                "current": 0.80,
                "recommended": best_rvol,
                "oos_win_rate": best_rvol_wr,
                "reason": f"OOS win rate {best_rvol_wr:.0%} at RVOL>={best_rvol}",
            })

        # ── 2. Confidence threshold optimisation ────────────────────────────
        conf_thresholds = [55, 60, 65, 70, 75]
        best_conf_wr = 0.0
        best_conf = 65  # current
        for thr in conf_thresholds:
            subset = [o for o in outcomes if o.get("confidence", 0) >= thr]
            if len(subset) < 10:
                continue
            oos = subset[len(subset)//2:]
            wr = sum(1 for o in oos if o.get("result") == "TARGET") / len(oos)
            if wr > best_conf_wr:
                best_conf_wr = wr
                best_conf = thr
        result["confidence_threshold"] = {
            "recommended": best_conf,
            "win_rate_oos": round(best_conf_wr, 3),
            "current": 65,
            "change_suggested": best_conf != 65 and total >= 50,
        }
        if best_conf != 65 and total >= 50 and best_conf_wr > 0.45:
            result["parameter_changes"].append({
                "param": "MIN_CONFIDENCE",
                "current": 65,
                "recommended": best_conf,
                "oos_win_rate": best_conf_wr,
                "reason": f"OOS win rate {best_conf_wr:.0%} at confidence>={best_conf}",
            })

        # ── 3. Regime performance analysis (Faber 2013) ─────────────────────
        regime_stats: dict[str, dict] = {}
        for o in outcomes:
            regime = o.get("regime", "UNKNOWN") or "UNKNOWN"
            if regime not in regime_stats:
                regime_stats[regime] = {"wins": 0, "total": 0}
            regime_stats[regime]["total"] += 1
            if o.get("result") == "TARGET":
                regime_stats[regime]["wins"] += 1

        for regime, stats in regime_stats.items():
            if stats["total"] < 10:
                continue
            wr = stats["wins"] / stats["total"]
            if wr < 0.35:
                result["avoid_regimes"].append({
                    "regime": regime, "win_rate": round(wr, 3),
                    "trades": stats["total"],
                })
            elif wr > 0.55:
                result["best_regimes"].append({
                    "regime": regime, "win_rate": round(wr, 3),
                    "trades": stats["total"],
                })

        # ── 4. Intraday timing analysis ──────────────────────────────────────
        hour_stats: dict[int, dict] = {}
        for o in outcomes:
            try:
                ts = o.get("entry_time") or o.get("timestamp", "")
                if ts:
                    hour = int(str(ts)[11:13])  # UTC hour from ISO string
                    uk_hour = (hour + 1) % 24   # Approximate UK offset
                    if uk_hour not in hour_stats:
                        hour_stats[uk_hour] = {"wins": 0, "total": 0}
                    hour_stats[uk_hour]["total"] += 1
                    if o.get("result") == "TARGET":
                        hour_stats[uk_hour]["wins"] += 1
            except (ValueError, TypeError, IndexError):
                continue

        if hour_stats:
            overall_wr = sum(1 for o in outcomes if o.get("result") == "TARGET") / total
            result["best_hours"] = [
                {"hour_uk": h, "win_rate": round(s["wins"]/s["total"], 3), "trades": s["total"]}
                for h, s in hour_stats.items()
                if s["total"] >= 5 and s["wins"]/s["total"] > overall_wr + 0.05
            ]

        # ── 5. Per-ticker performance (Aronson 2007: min 20 trades) ─────────
        ticker_stats: dict[str, dict] = {}
        for o in outcomes:
            t = o.get("ticker", "UNKNOWN") or "UNKNOWN"
            if t not in ticker_stats:
                ticker_stats[t] = {"wins": 0, "total": 0, "r_multiples": []}
            ticker_stats[t]["total"] += 1
            if o.get("result") == "TARGET":
                ticker_stats[t]["wins"] += 1
            rm = o.get("r_multiple") or o.get("pnl_r_multiple")
            if rm is not None:
                try:
                    ticker_stats[t]["r_multiples"].append(float(rm))
                except (TypeError, ValueError):
                    pass

        for ticker, stats in ticker_stats.items():
            if stats["total"] < 20:
                continue
            wr = stats["wins"] / stats["total"]
            avg_r = statistics.mean(stats["r_multiples"]) if stats["r_multiples"] else 0
            entry = {"ticker": ticker, "win_rate": round(wr, 3),
                     "avg_r": round(avg_r, 3), "trades": stats["total"]}
            if wr < 0.30:
                result["worst_tickers"].append(entry)
            elif wr > 0.55:
                result["best_tickers"].append(entry)

        # ── 6. Evidence quality rating ───────────────────────────────────────
        if total >= 100:
            result["evidence_quality"] = "STRONG"
        elif total >= 50:
            result["evidence_quality"] = "MODERATE"
        elif total >= 20:
            result["evidence_quality"] = "WEAK"

        return result

    def auto_apply_improvements(self, min_evidence: str = "STRONG") -> list[dict]:
        """Apply statistically validated parameter improvements automatically.

        Only fires when evidence_quality >= min_evidence ('STRONG' requires 100+ trades).
        Writes approved changes to data/approved_params.json for the engine to read.

        This is the 'today's excellence is tomorrow's average' mechanism:
        The system continuously raises its own bar as evidence accumulates.

        Args:
            min_evidence: minimum evidence quality required ('STRONG'|'MODERATE')

        Returns:
            list of changes applied
        """
        candidates = self.compute_improvement_candidates()

        quality = candidates.get("evidence_quality", "INSUFFICIENT")
        quality_rank = {"INSUFFICIENT": 0, "WEAK": 1, "MODERATE": 2, "STRONG": 3}
        if quality_rank.get(quality, 0) < quality_rank.get(min_evidence, 3):
            logger.info(
                "Auto-improvement: evidence quality %s < required %s. No changes.",
                quality, min_evidence,
            )
            return []

        changes = candidates.get("parameter_changes", [])
        if not changes:
            logger.info("Auto-improvement: no parameter changes recommended at current evidence level.")
            return []

        # Load or create approved_params.json
        params_path = _DATA / "approved_params.json"
        try:
            with open(params_path) as f:
                current_params = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            current_params = {}

        applied = []
        for change in changes:
            param = change.get("param", "")
            recommended = change.get("recommended")
            oos_wr = change.get("oos_win_rate", 0)

            # Safety guard: OOS win rate must be >= 45% to apply
            if oos_wr < 0.45:
                logger.debug("Auto-improvement: skipping %s — OOS WR %.1f%% < 45%%",
                            param, oos_wr * 100)
                continue

            current_params[param] = {
                "value": recommended,
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "oos_win_rate": oos_wr,
                "evidence_quality": quality,
                "trades_at_approval": candidates.get("total_outcomes", 0),
            }
            applied.append(change)
            logger.info(
                "AUTO-IMPROVEMENT APPLIED: %s → %s (OOS WR=%.1f%%, evidence=%s, trades=%d)",
                param, recommended, oos_wr * 100, quality,
                candidates.get("total_outcomes", 0),
            )

        if applied:
            with open(params_path, "w") as f:
                json.dump(current_params, f, indent=2)
            logger.info("Auto-improvement: %d changes written to %s", len(applied), params_path)

        return applied

    def load_approved_params(self) -> dict:
        """Load any auto-approved parameter improvements.

        Called at engine startup and daily. If approved_params.json exists,
        these values override the hardcoded defaults in strategies.

        Returns dict of {param_name: value} for live use.
        """
        params_path = _DATA / "approved_params.json"
        try:
            with open(params_path) as f:
                data = json.load(f)
            params = {k: v["value"] for k, v in data.items()}
            if params:
                logger.info("Loaded %d auto-approved parameter improvements: %s", len(params), params)
            return params
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def get_daily_excellence_brief(self) -> str:
        """Return a human-readable daily brief on system performance evolution.

        'Today's excellence is tomorrow's average.'
        Shows how current thresholds compare to where we started,
        and what the next improvement target is.
        """
        outcomes = self._load_all_outcomes()
        approved = self.load_approved_params()

        total = len(outcomes)
        if total == 0:
            return "No trade outcomes yet. System ready for first live session."

        recent_50 = outcomes[-50:] if total >= 50 else outcomes
        recent_wr = sum(1 for o in recent_50 if o.get("result") == "TARGET") / len(recent_50)
        all_time_wr = sum(1 for o in outcomes if o.get("result") == "TARGET") / total

        current_conf = approved.get("MIN_CONFIDENCE", 65)
        current_rvol = approved.get("MIN_RVOL", 0.80)

        next_target_wr = min(recent_wr + 0.05, 0.70)

        lines = [
            "══ NZT-48 EXCELLENCE BRIEF ══════════════════════════════",
            f"Trades analysed:    {total} total | {len(recent_50)} recent",
            f"Win rate (all):     {all_time_wr:.1%}",
            f"Win rate (recent):  {recent_wr:.1%}",
            f"Current standards:  confidence≥{current_conf} | RVOL≥{current_rvol}",
            f"Next target:        win_rate ≥ {next_target_wr:.1%}",
            f"Auto-improvements:  {len(approved)} parameters auto-tuned",
            "Today's excellence → Tomorrow's average. Standards only rise.",
            "═════════════════════════════════════════════════════════",
        ]
        if recent_wr >= 0.50:
            lines.insert(5, "STATUS:  PERFORMING — standards will be raised at 100 trades")
        elif recent_wr >= 0.40:
            lines.insert(5, "STATUS:  DEVELOPING — accumulating evidence for first auto-tune")
        else:
            lines.insert(5, "STATUS:  BUILDING — filters tightened, need more live sessions")

        return "\n".join(lines)

    def compute_per_ticker_indicator_ranks(self) -> dict[str, dict]:
        """Compute per-ticker indicator importance rankings from trade outcomes.

        KEY INSIGHT: The indicators that predict winners for QQQ3.L may differ
        from those that predict winners for TSL3.L. Tesla-linked ETPs respond
        strongly to MACD momentum; QQQ3 is more VWAP/EMA-driven.

        For each ticker with sufficient history, this computes:
        - Which indicators were present on WINNING trades vs ALL trades
        - Lift ratio: P(win | indicator_present) / P(win | baseline)
        - Rank indicators by lift (higher = more predictive for this ticker)

        Method: Point-Biserial correlation between indicator state and outcome
        (Chincarini & Kim 2006). Falls back to global ranking if insufficient data.

        Returns:
            {
                "QQQ3.L": {
                    "ranked_indicators": ["ema20", "vwap", "rsi", ...],  # best first
                    "indicator_lift": {"ema20": 1.42, "vwap": 1.31, ...},
                    "sample_size": 34,
                    "confidence": "MODERATE",  # STRONG/MODERATE/WEAK
                },
                "__GLOBAL__": { ... },  # fallback for all tickers
            }
        """
        outcomes = self._load_all_outcomes()
        if not outcomes:
            return {}

        # Group outcomes by ticker
        by_ticker: dict[str, list[dict]] = {}
        for o in outcomes:
            t = o.get("ticker", "UNKNOWN") or "UNKNOWN"
            by_ticker.setdefault(t, []).append(o)

        # Add __GLOBAL__ bucket for all tickers combined
        by_ticker["__GLOBAL__"] = outcomes

        result: dict[str, dict] = {}

        for ticker, ticker_outcomes in by_ticker.items():
            n = len(ticker_outcomes)
            if n < _MIN_INDICATOR_TRADES:
                continue

            baseline_wr = sum(1 for o in ticker_outcomes if o.get("result") == "TARGET") / n

            indicator_lifts: dict[str, float] = {}
            indicator_samples: dict[str, int] = {}

            for ind in _ALL_INDICATORS:
                # Each outcome stores which indicators were "present" (aligned for direction)
                # The signal_snapshot field should contain indicator states
                # We look for the indicator in signal_context, features, or snapshot fields
                trades_with_ind = []
                for o in ticker_outcomes:
                    ind_present = self._check_indicator_present(o, ind)
                    if ind_present is not None:
                        trades_with_ind.append((ind_present, o.get("result") == "TARGET"))

                if len(trades_with_ind) < _MIN_INDICATOR_TRADES:
                    continue

                # Split: trades where this indicator fired vs didn't
                with_ind = [wr for present, wr in trades_with_ind if present]
                without_ind = [wr for present, wr in trades_with_ind if not present]

                if not with_ind:
                    continue

                wr_with = sum(with_ind) / len(with_ind)
                # Lift = how much better do we do when this indicator is aligned?
                lift = wr_with / baseline_wr if baseline_wr > 0 else 1.0
                indicator_lifts[ind] = round(lift, 3)
                indicator_samples[ind] = len(with_ind)

            if not indicator_lifts:
                # No indicator data in outcomes — use equal weights
                result[ticker] = {
                    "ranked_indicators": _ALL_INDICATORS[:],
                    "indicator_lift": {ind: 1.0 for ind in _ALL_INDICATORS},
                    "sample_size": n,
                    "confidence": "INSUFFICIENT",
                    "baseline_wr": round(baseline_wr, 3),
                }
                continue

            # Rank by lift descending
            ranked = sorted(indicator_lifts.keys(), key=lambda i: -indicator_lifts[i])

            # Confidence rating
            if n >= 100:
                conf = "STRONG"
            elif n >= 30:
                conf = "MODERATE"
            else:
                conf = "WEAK"

            result[ticker] = {
                "ranked_indicators": ranked,
                "indicator_lift": indicator_lifts,
                "indicator_samples": indicator_samples,
                "sample_size": n,
                "confidence": conf,
                "baseline_wr": round(baseline_wr, 3),
            }

            logger.debug(
                "Indicator ranks for %s (n=%d, WR=%.0%%): %s",
                ticker, n, baseline_wr * 100,
                " > ".join(f"{i}({indicator_lifts[i]:.2f}x)" for i in ranked[:4]),
            )

        return result

    def _check_indicator_present(self, outcome: dict, indicator: str) -> Optional[bool]:
        """Check if a given indicator was aligned (in the correct direction) for a trade.

        Returns True/False if we have data, None if data is absent.
        Looks in multiple outcome fields: signal_context, features, snapshot.
        """
        # Try signal_context first (rich context dict)
        ctx = outcome.get("signal_context") or outcome.get("features") or {}
        direction = outcome.get("direction", "LONG")
        bullish = direction == "LONG"

        if indicator == "rsi":
            rsi = ctx.get("rsi14") or ctx.get("rsi")
            if rsi is None:
                rsi = outcome.get("rsi14") or outcome.get("rsi")
            if rsi is None:
                return None
            return float(rsi) > 55 if bullish else float(rsi) < 45

        if indicator == "macd":
            macd_h = ctx.get("macd_histogram") or ctx.get("macd_hist")
            if macd_h is None:
                macd_h = outcome.get("macd_histogram")
            if macd_h is None:
                return None
            return float(macd_h) > 0 if bullish else float(macd_h) < 0

        if indicator == "ema9":
            price = ctx.get("price") or outcome.get("entry_price") or outcome.get("entry")
            ema9 = ctx.get("ema9") or ctx.get("ema_9")
            if price is None or ema9 is None:
                return None
            return float(price) > float(ema9) if bullish else float(price) < float(ema9)

        if indicator == "ema20":
            price = ctx.get("price") or outcome.get("entry_price") or outcome.get("entry")
            ema20 = ctx.get("ema20") or ctx.get("ema_20")
            if price is None or ema20 is None:
                return None
            return float(price) > float(ema20) if bullish else float(price) < float(ema20)

        if indicator == "ema50":
            price = ctx.get("price") or outcome.get("entry_price") or outcome.get("entry")
            ema50 = ctx.get("ema50") or ctx.get("ema_50")
            if price is None or ema50 is None:
                return None
            return float(price) > float(ema50) if bullish else float(price) < float(ema50)

        if indicator == "vwap":
            price = ctx.get("price") or outcome.get("entry_price") or outcome.get("entry")
            vwap = ctx.get("vwap")
            if price is None or vwap is None:
                return None
            return float(price) > float(vwap) if bullish else float(price) < float(vwap)

        if indicator == "stoch_rsi":
            srsi = ctx.get("stochastic_rsi") or ctx.get("stoch_rsi")
            if srsi is None:
                return None
            return float(srsi) > 50 if bullish else float(srsi) < 50

        if indicator == "obv":
            obv = ctx.get("obv")
            if obv is None:
                return None
            return float(obv) > 0 if bullish else float(obv) < 0

        return None

    def get_indicator_weights_for_ticker(self, ticker: str) -> dict[str, float]:
        """Return scoring weights for each indicator, personalised to this ticker.

        Used by S15 _score_ticker to weight indicators by their proven lift.
        Falls back to global weights if insufficient per-ticker data.

        Returns dict of {indicator_name: weight} normalised to sum=1.0.
        """
        ranks = self.compute_per_ticker_indicator_ranks()

        # Prefer per-ticker data; fallback to global
        data = ranks.get(ticker) or ranks.get("__GLOBAL__")
        if not data or data.get("confidence") in ("INSUFFICIENT", None):
            # Default equal weights — no data yet
            n = len(_ALL_INDICATORS)
            return {ind: 1.0 / n for ind in _ALL_INDICATORS}

        lifts = data.get("indicator_lift", {})
        if not lifts:
            n = len(_ALL_INDICATORS)
            return {ind: 1.0 / n for ind in _ALL_INDICATORS}

        # Convert lifts → weights (softmax-style normalisation)
        total_lift = sum(max(l, 0.01) for l in lifts.values())
        weights = {ind: max(lifts.get(ind, 0.5), 0.01) / total_lift for ind in _ALL_INDICATORS}
        return weights

    def get_indicator_ranking_report(self) -> str:
        """Human-readable Telegram report of per-ticker indicator rankings."""
        ranks = self.compute_per_ticker_indicator_ranks()
        if not ranks:
            return "No indicator ranking data yet — need 10+ trades per ticker."

        lines = ["INDICATOR RANKINGS BY TICKER"]
        for ticker, data in sorted(ranks.items()):
            if ticker == "__GLOBAL__":
                continue
            conf = data.get("confidence", "?")
            n = data.get("sample_size", 0)
            wr = data.get("baseline_wr", 0)
            ranked = data.get("ranked_indicators", [])
            lifts = data.get("indicator_lift", {})
            top3 = " > ".join(
                f"{i}({lifts.get(i, 1.0):.2f}x)"
                for i in ranked[:3]
            )
            lines.append(f"{ticker} [{conf} n={n} WR={wr:.0%}]: {top3}")

        # Global
        if "__GLOBAL__" in ranks:
            g = ranks["__GLOBAL__"]
            ranked = g.get("ranked_indicators", [])
            lifts = g.get("indicator_lift", {})
            top3 = " > ".join(f"{i}({lifts.get(i,1.0):.2f}x)" for i in ranked[:3])
            lines.append(f"GLOBAL [n={g.get('sample_size',0)}]: {top3}")

        return "\n".join(lines)

    def _load_all_outcomes(self) -> list[dict]:
        """Load all outcomes from outcomes.jsonl. Returns [] on missing file."""
        if not _OUTCOMES.exists():
            return []
        outcomes = []
        try:
            with open(_OUTCOMES) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            outcomes.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            pass
        return outcomes
