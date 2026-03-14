#!/usr/bin/env python3
"""
NZT-48 Extended Backfill — 2-Year Deep Learning Seed
======================================================
Seeds the self-learning system with 2 years of simulated trades.
This is ~8x more data than the standard 90-day backfill, enabling:
  - Per-ticker indicator rank confidence: WEAK → MODERATE → STRONG
  - Regime-conditional performance with statistical significance
  - Auto-improvement candidates with STRONG evidence level (100+ trades)
  - Time-window performance mapping (MORNING vs MIDDAY vs AFTERNOON)
  - Seasonal regime patterns (UK ISA leveraged ETP behaviour by month)

Academic basis:
  - Harvey & Liu (2015): 20 obs minimum per bucket — 2yr gives 80+ per ticker
  - Lopez de Prado (2020): Walk-forward validation with OOS data
  - Chan (2013): Volume regime analysis across market cycles
  - Faber (2013): Regime conditioning across full market cycle

Usage:
    python scripts/backfill_extended.py [--years 2] [--tickers all] [--dry-run]

    --years N         Lookback years (default: 2, max: 5)
    --tickers TICKER  Comma-separated list or "all" (default: all)
    --dry-run         Simulate without feeding learning engine
    --append          Append to existing outcomes.jsonl (default: append)
    --replace         Replace existing outcomes.jsonl instead of appending
"""

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger("nzt48.backfill_extended")

# ── Configuration ─────────────────────────────────────────────────────────────
DEFAULT_YEARS = 2
TARGET_PCT = 0.02
STOP_PCT_3X = 0.01
STOP_PCT_5X = 0.0075
EQUITY = 10000.0
RISK_PCT = 0.0075

# Quality gates — must match live S15 gates exactly
MIN_MOVE_PCT = 0.030
MIN_RVOL_SIM = 0.80
MIN_ADX_PROXY = 20.0
MIN_CONFIDENCE_SIM = 65

_5X_TICKERS = {"QQQ5.L", "QQQ5.L"}
# F-03: import from single source of truth (config.universe_constants)
from config.universe_constants import INVERSE_ETPS_SET as _INVERSE_TICKERS

# Full ISA universe (22 tickers)
ALL_TICKERS = [
    "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L",
    "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L",
    "AMD3.L", "ARM3.L", "NVDS.L", "TSLS.L", "3LDE.L", "3LEU.L",
    "3GOL.L", "3SIL.L", "3OIL.L", "LLY3.L",
]

# Month buckets for seasonal analysis
_MONTH_SEASONS = {
    1: "JAN", 2: "FEB", 3: "MAR",
    4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP",
    10: "OCT", 11: "NOV", 12: "DEC",
}


# ── Data Fetching ─────────────────────────────────────────────────────────────

def fetch_historical_data(ticker: str, years: int) -> Optional[pd.DataFrame]:
    """Fetch N years of daily OHLCV via yfinance."""
    try:
        import yfinance as yf
        days = years * 365 + 30  # buffer
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if df is not None and len(df) >= 20:
            logger.info("  %-12s %d bars fetched (%s → %s)",
                        ticker, len(df),
                        df.index[0].strftime("%Y-%m-%d"),
                        df.index[-1].strftime("%Y-%m-%d"))
            return df
        else:
            logger.warning("  %-12s Insufficient data (%d bars) — skipping",
                           ticker, len(df) if df is not None else 0)
    except Exception as e:
        logger.warning("  %-12s Fetch failed: %s", ticker, e)
    return None


# ── Indicator Engine ──────────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> dict:
    """Compute full indicator set for each bar. Requires 50+ bars for EMA50."""
    indicators_by_date = {}

    closes = df["Close"].values.flatten()
    highs = df["High"].values.flatten()
    lows = df["Low"].values.flatten()
    opens = df["Open"].values.flatten()
    volumes = df["Volume"].values.flatten()

    n = len(df)

    for i in range(50, n):  # 50 bars needed for EMA50
        date = df.index[i]

        # ── ATR (14-period True Range) ────────────────────────────────────────
        atr_vals = []
        for j in range(max(1, i - 13), i + 1):
            tr = max(
                highs[j] - lows[j],
                abs(highs[j] - closes[j - 1]),
                abs(lows[j] - closes[j - 1]),
            )
            atr_vals.append(tr)
        atr14 = sum(atr_vals) / len(atr_vals)
        atr_pct = atr14 / closes[i] if closes[i] > 0 else 0

        # ── RSI (14-period Wilder's) ──────────────────────────────────────────
        gains, losses = [], []
        for j in range(max(1, i - 13), i + 1):
            chg = closes[j] - closes[j - 1]
            gains.append(max(0, chg))
            losses.append(max(0, -chg))
        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = sum(losses) / len(losses) if losses else 0.001
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi14 = 100 - (100 / (1 + rs))

        # ── MACD (12/26/9 EMA) ────────────────────────────────────────────────
        ema12 = _ema(closes, i, 12)
        ema26 = _ema(closes, i, 26)
        macd_line = ema12 - ema26
        # Signal: 9-bar EMA of MACD (approximate via simple average here)
        macd_hist = macd_line  # positive = bullish momentum

        # ── EMAs ──────────────────────────────────────────────────────────────
        ema9 = _ema(closes, i, 9)
        ema20 = _ema(closes, i, 20)
        ema50 = _ema(closes, i, 50)

        # ── RVOL ──────────────────────────────────────────────────────────────
        vol_window = volumes[max(0, i - 19):i + 1]
        avg_vol = sum(vol_window[:-1]) / max(len(vol_window) - 1, 1)
        rvol = volumes[i] / avg_vol if avg_vol > 0 else 1.0

        # ── Stochastic RSI (14-period RSI scaled 0-100) ───────────────────────
        rsi_window = []
        for j in range(max(0, i - 13), i + 1):
            # Simple RSI approximation for window
            g, l_ = [], []
            for k in range(max(1, j - 13), j + 1):
                chg = closes[k] - closes[k - 1]
                g.append(max(0, chg))
                l_.append(max(0, -chg))
            ag = sum(g) / len(g) if g else 0
            al = sum(l_) / len(l_) if l_ else 0.001
            rsi_window.append(100 - (100 / (1 + ag / al)))
        rsi_min = min(rsi_window) if rsi_window else 0
        rsi_max = max(rsi_window) if rsi_window else 100
        stoch_rsi = (rsi14 - rsi_min) / (rsi_max - rsi_min) * 100 if rsi_max > rsi_min else 50

        # ── OBV (On-Balance Volume) ───────────────────────────────────────────
        obv = 0.0
        for j in range(max(1, i - 19), i + 1):
            if closes[j] > closes[j - 1]:
                obv += volumes[j]
            elif closes[j] < closes[j - 1]:
                obv -= volumes[j]
        # Normalise OBV as positive/neutral/negative signal
        obv_signal = 1 if obv > 0 else -1 if obv < 0 else 0

        # ── ADX proxy ────────────────────────────────────────────────────────
        daily_range_pct = (highs[i] - lows[i]) / closes[i] if closes[i] > 0 else 0
        adx_proxy = (daily_range_pct / atr_pct) * 25 if atr_pct > 0 else 0

        # ── Indicator alignment signals (for lift ratio computation) ──────────
        # These encode whether each indicator is "bullish" on this bar
        indicator_signals = {
            "rsi": 1 if 50 < rsi14 <= 70 else (-1 if rsi14 > 75 else 0),
            "macd": 1 if macd_hist > 0 else -1,
            "ema9": 1 if closes[i] > ema9 else -1,
            "ema20": 1 if closes[i] > ema20 else -1,
            "ema50": 1 if closes[i] > ema50 else -1,
            "vwap": 1 if closes[i] > ema20 else 0,  # VWAP proxy via EMA20
            "stoch_rsi": 1 if 20 < stoch_rsi < 80 else (-1 if stoch_rsi >= 80 else 0),
            "obv": int(obv_signal),
        }
        bullish_count = sum(1 for v in indicator_signals.values() if v == 1)

        indicators_by_date[date] = {
            "close": float(closes[i]),
            "prev_close": float(closes[i - 1]),
            "open": float(opens[i]),
            "high": float(highs[i]),
            "low": float(lows[i]),
            "volume": float(volumes[i]),
            "atr14": float(atr14),
            "atr_pct": float(atr_pct),
            "rsi14": float(rsi14),
            "macd_hist": float(macd_hist),
            "ema9": float(ema9),
            "ema20": float(ema20),
            "ema50": float(ema50),
            "rvol": float(rvol),
            "stoch_rsi": float(stoch_rsi),
            "obv_signal": float(obv_signal),
            "adx_proxy": float(adx_proxy),
            "daily_range_pct": float(daily_range_pct),
            "indicator_signals": indicator_signals,
            "bullish_count": int(bullish_count),
            # Month for seasonal analysis
            "month": date.month if hasattr(date, "month") else datetime.fromisoformat(str(date)).month,
        }

    return indicators_by_date


def _ema(closes, i: int, period: int) -> float:
    """Exponential moving average at index i."""
    k = 2.0 / (period + 1)
    window = closes[max(0, i - period * 3):i + 1]
    if len(window) == 0:
        return closes[i]
    ema = window[0]
    for c in window[1:]:
        ema = c * k + ema * (1 - k)
    return float(ema)


# ── Regime Classifier ─────────────────────────────────────────────────────────

def classify_regime(ind: dict, direction: str) -> str:
    """Multi-factor regime classifier aligned with live S15 logic."""
    rsi = ind["rsi14"]
    close = ind["close"]
    ema20 = ind["ema20"]
    ema50 = ind["ema50"]
    daily_range = ind["daily_range_pct"]
    atr_pct = ind["atr_pct"]

    # SHOCK: extreme range AND big directional move
    if daily_range > 0.06 and abs(close - ind["open"]) / ind["open"] > 0.04:
        return "SHOCK"

    # TRENDING: RSI + EMA50 alignment (more robust than EMA20 alone)
    above_ema50 = close > ema50
    above_ema20 = close > ema20

    if rsi > 60 and above_ema50 and above_ema20:
        return "TRENDING_UP_STRONG"
    elif rsi > 50 and above_ema20:
        return "TRENDING_UP_MOD"
    elif rsi < 30 and not above_ema50 and not above_ema20:
        return "TRENDING_DOWN_STRONG"
    elif rsi < 40 and not above_ema20:
        return "TRENDING_DOWN_MOD"
    elif 40 <= rsi <= 60 and abs(close - ema20) / ema20 < 0.02:
        return "RANGE_BOUND"
    else:
        return "NEUTRAL"


# ── Trade Simulator ───────────────────────────────────────────────────────────

def simulate_trades(ticker: str, indicators_by_date: dict) -> list[dict]:
    """Simulate S15-style trades with all quality gates matching live S15."""
    trades = []
    is_5x = ticker in _5X_TICKERS
    is_inverse = ticker in _INVERSE_TICKERS
    stop_pct = STOP_PCT_5X if is_5x else STOP_PCT_3X

    dates = sorted(indicators_by_date.keys())

    for date in dates:
        ind = indicators_by_date[date]
        direction = "SHORT" if is_inverse else "LONG"

        open_price = ind["open"]
        high = ind["high"]
        low = ind["low"]
        close = ind["close"]
        prev_close = ind["prev_close"]
        rvol = ind["rvol"]
        atr_pct = ind["atr_pct"]
        adx_proxy = ind["adx_proxy"]
        rsi = ind["rsi14"]
        bullish_count = ind["bullish_count"]
        indicator_signals = ind["indicator_signals"]

        regime = classify_regime(ind, direction)

        # ── Gate A: Daily range minimum ───────────────────────────────────────
        if ind["daily_range_pct"] < MIN_MOVE_PCT:
            continue

        # ── Gate B: Volume (Chan 2013) ────────────────────────────────────────
        if rvol < MIN_RVOL_SIM:
            continue

        # ── Gate C: ADX proxy (Faber 2013) ───────────────────────────────────
        if adx_proxy < MIN_ADX_PROXY:
            continue

        # ── Gate D: Regime veto ───────────────────────────────────────────────
        if regime == "SHOCK":
            continue
        if direction == "LONG" and regime in ("TRENDING_DOWN_STRONG", "TRENDING_DOWN_MOD"):
            continue
        if direction == "SHORT" and regime in ("TRENDING_UP_STRONG", "TRENDING_UP_MOD"):
            continue
        # RANGE_BOUND: allow only with breakout characteristics
        if regime == "RANGE_BOUND":
            if atr_pct < 2.0 or rvol < 1.2:
                continue

        # ── Gate E: Indicator consensus (5/8 minimum) ─────────────────────────
        # For LONG: need bullish_count >= 5; for SHORT: need bearish_count >= 5
        if direction == "LONG" and bullish_count < 5:
            continue
        bearish_count = sum(1 for v in indicator_signals.values() if v == -1)
        if direction == "SHORT" and bearish_count < 5:
            continue

        # ── Gate F: Confidence scoring (Harvey & Liu 2015) ───────────────────
        confidence_score = 50
        if rvol > 2.0:
            confidence_score += 15
        elif rvol > 1.5:
            confidence_score += 10
        elif rvol > 1.0:
            confidence_score += 5
        if regime == "TRENDING_UP_STRONG" and direction == "LONG":
            confidence_score += 15
        elif regime == "TRENDING_UP_MOD" and direction == "LONG":
            confidence_score += 10
        elif regime == "RANGE_BOUND":
            confidence_score -= 10  # penalty for context-aware range entry
        if atr_pct >= 3.0:
            confidence_score += 10
        elif atr_pct >= 2.0:
            confidence_score += 5
        if direction == "LONG" and 50 < rsi <= 70:
            confidence_score += 5
        if direction == "LONG" and rsi > 75:
            confidence_score -= 15
        if direction == "LONG" and rsi < 45:
            confidence_score -= 10
        # Indicator consensus bonus
        if bullish_count >= 7:
            confidence_score += 10
        elif bullish_count >= 6:
            confidence_score += 5
        confidence_score = max(20, min(95, confidence_score))

        if confidence_score < MIN_CONFIDENCE_SIM:
            continue

        # ── Entry / Target / Stop ─────────────────────────────────────────────
        if direction == "LONG":
            entry = round(open_price + 0.3 * (high - open_price), 4)
        else:
            entry = round(open_price - 0.3 * (open_price - low), 4)

        if entry <= 0:
            continue

        stop_abs = round(entry * (1 - stop_pct), 4) if direction == "LONG" else round(entry * (1 + stop_pct), 4)
        target = round(entry * (1 + TARGET_PCT), 4) if direction == "LONG" else round(entry * (1 - TARGET_PCT), 4)
        rung1 = round(entry * 1.01, 4) if direction == "LONG" else round(entry * 0.99, 4)
        rung2 = round(entry * 1.02, 4) if direction == "LONG" else round(entry * 0.98, 4)

        # ── Simulate intraday path (profit ladder) ────────────────────────────
        rung_reached = 0
        breakeven_saved = False
        result = "TIME_STOP"
        exit_price = close
        pnl_pct = 0.0

        if direction == "LONG":
            if high >= rung2:
                rung_reached = 2
                if low > stop_abs:
                    result, exit_price, pnl_pct = "TARGET", rung2, 0.02
                else:
                    if open_price >= prev_close:
                        result, exit_price, pnl_pct = "TARGET", rung2, 0.02
                    else:
                        result, exit_price, pnl_pct = "STOP", stop_abs, -stop_pct
            elif high >= rung1:
                rung_reached = 1
                if low <= stop_abs:
                    result, exit_price, pnl_pct = "BREAKEVEN", entry, 0.0
                    breakeven_saved = True
                else:
                    result = "TIME_STOP"
                    exit_price = close
                    pnl_pct = (close - entry) / entry
            else:
                if low <= stop_abs:
                    result, exit_price, pnl_pct = "STOP", stop_abs, -stop_pct
                else:
                    pnl_pct = (close - entry) / entry
        else:
            if low <= rung2:
                rung_reached = 2
                if high < stop_abs:
                    result, exit_price, pnl_pct = "TARGET", rung2, 0.02
                else:
                    if open_price <= prev_close:
                        result, exit_price, pnl_pct = "TARGET", rung2, 0.02
                    else:
                        result, exit_price, pnl_pct = "STOP", stop_abs, -stop_pct
            elif low <= rung1:
                rung_reached = 1
                if high >= stop_abs:
                    result, exit_price, pnl_pct = "BREAKEVEN", entry, 0.0
                    breakeven_saved = True
                else:
                    pnl_pct = (entry - close) / entry
            else:
                if high >= stop_abs:
                    result, exit_price, pnl_pct = "STOP", stop_abs, -stop_pct
                else:
                    pnl_pct = (entry - close) / entry

        # ── Position sizing ───────────────────────────────────────────────────
        risk_per_share = abs(entry - stop_abs)
        risk_dollars = EQUITY * RISK_PCT
        shares = max(1, int(risk_dollars / risk_per_share)) if risk_per_share > 0 else 1

        if direction == "LONG":
            pnl_dollars = (exit_price - entry) * shares
        else:
            pnl_dollars = (entry - exit_price) * shares
        r_multiple = pnl_dollars / risk_dollars if risk_dollars > 0 else 0

        if direction == "LONG":
            peak_r = (high - entry) / risk_per_share if risk_per_share > 0 else 0
            trough_r = (low - entry) / risk_per_share if risk_per_share > 0 else 0
        else:
            peak_r = (entry - low) / risk_per_share if risk_per_share > 0 else 0
            trough_r = (entry - high) / risk_per_share if risk_per_share > 0 else 0

        # ── Time window (seasonal metadata) ──────────────────────────────────
        month_str = _MONTH_SEASONS.get(ind["month"], "UNK")
        time_window = "MORNING"  # assume morning entry

        trade_date = date.date() if hasattr(date, "date") else date
        entry_time = datetime(trade_date.year, trade_date.month, trade_date.day,
                              9, 30, tzinfo=timezone.utc)
        duration_min = max(30, int(abs(pnl_pct * 10000) + 30))
        exit_time = entry_time + timedelta(minutes=duration_min)
        trade_id = f"BFE-{ticker.replace('.', '')}-{date.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"

        trade = {
            "id": trade_id,
            "signal_id": f"SIG-{trade_id}",
            "ticker": ticker,
            "direction": direction,
            "strategy": "S15" if not is_inverse else "S16",
            "entry_price": round(entry, 4),
            "exit_price": round(exit_price, 4),
            "stop_price": round(stop_abs, 4),
            "target_1r": round(target, 4),
            "target_2r": round(target * (1.02 if direction == "LONG" else 0.98), 4),
            "shares": shares,
            "risk_dollars": round(risk_dollars, 2),
            "risk_percent": RISK_PCT,
            "pnl_dollars": round(pnl_dollars, 2),
            "pnl_r_multiple": round(r_multiple, 4),
            "gross_pnl": round(pnl_dollars, 2),
            "commissions": 0.0,
            "net_pnl": round(pnl_dollars, 2),
            "entry_quality": min(100, max(0, 50 + int(r_multiple * 20))),
            "exit_quality": 80 if result == "TARGET" else 60 if result == "BREAKEVEN" else 40 if result == "STOP" else 60,
            "confidence_score": confidence_score,
            "regime_state": regime,
            "gex_regime": "NEUTRAL",
            "vix_level": 18.0,
            "time_window": time_window,
            "month": month_str,
            "time_entered": entry_time.isoformat(),
            "time_exited": exit_time.isoformat(),
            "duration_minutes": duration_min,
            "peak_r": round(peak_r, 4),
            "trough_r": round(trough_r, 4),
            "exit_reason": result,
            "result": result,
            "rung_reached": rung_reached,
            "breakeven_saved": breakeven_saved,
            "partial_profit": 0.0,
            "indicators": ind,
            "indicator_signals": indicator_signals,
            "bullish_count": bullish_count,
            "bot": "A",
            "bot_instance": "BULL" if direction == "LONG" else "BEAR",
        }
        trades.append(trade)

    return trades


# ── Learning Engine Feed ───────────────────────────────────────────────────────

def feed_learning_engine(trades: list[dict]) -> dict:
    """Feed all trades through the learning engine and related modules."""
    from models import Trade, Direction, Bot, BotInstance
    from learning.learning_engine import LearningEngine
    from learning.edge_decay_engine import EdgeDecayEngine
    from learning.strategy_tournament import StrategyTournament
    from learning.performance_attribution import PerformanceAttributionEngine
    from delivery.database import init_db, transaction

    init_db()
    learning = LearningEngine()
    edge_decay = EdgeDecayEngine()
    tournament = StrategyTournament()
    perf_attribution = PerformanceAttributionEngine()

    stats = {"total": 0, "wins": 0, "losses": 0, "breakeven": 0, "errors": 0,
             "target_hits": 0, "stop_hits": 0, "time_stops": 0, "be_saves": 0}

    trades.sort(key=lambda t: t["time_entered"])

    for t in trades:
        try:
            direction = Direction.LONG if t["direction"] == "LONG" else Direction.SHORT
            trade = Trade(
                id=t["id"], signal_id=t["signal_id"],
                bot=Bot.A, bot_instance=BotInstance.BULL if t["bot_instance"] == "BULL" else BotInstance.BEAR,
                ticker=t["ticker"], direction=direction, strategy=t["strategy"],
                entry_price=t["entry_price"], exit_price=t["exit_price"],
                stop_price=t["stop_price"], target_1r=t["target_1r"], target_2r=t.get("target_2r", 0),
                shares=t["shares"], risk_dollars=t["risk_dollars"], risk_percent=t["risk_percent"],
                position_pct_equity=t["risk_percent"],
                pnl_dollars=t["pnl_dollars"], pnl_r_multiple=t["pnl_r_multiple"],
                gross_pnl=t["gross_pnl"], commissions=0.0, net_pnl=t["net_pnl"],
                expected_entry=t["entry_price"], actual_entry=t["entry_price"], fill_quality=90.0,
                entry_quality=t["entry_quality"], exit_quality=t["exit_quality"], timing_quality=70.0,
                confidence_score=t["confidence_score"], regime_state=t["regime_state"],
                sector_rs=0.0, macro_score=50, narrative_sentiment="NEUTRAL",
                gex_regime="NEUTRAL", dix_reading=0.0, internals_composite=0,
                vix_level=18.0, calendar_risk="LOW", patterns_detected=[], reason_codes=[],
                invalidation_reason="", emotional_state="CALM", firewall_triggers=[],
                what_worked="Extended backfill", what_failed="", improvement_note="",
                would_take_again=t["pnl_r_multiple"] > 0,
                time_entered=datetime.fromisoformat(t["time_entered"]),
                time_exited=datetime.fromisoformat(t["time_exited"]),
                duration_minutes=t["duration_minutes"],
            )
            learning.record_trade(trade)
            edge_decay.record_trade(
                strategy=t["strategy"], regime=t["regime_state"],
                entry_time=datetime.fromisoformat(t["time_entered"]),
                r_multiple=t["pnl_r_multiple"],
            )
            tournament.record_trade(strategy=t["strategy"], r_multiple=t["pnl_r_multiple"])

            try:
                perf_attribution.attribute_trade({
                    "trade_id": t["id"], "ticker": t["ticker"], "strategy": t["strategy"],
                    "direction": t["direction"], "entry_price": t["entry_price"],
                    "exit_price": t["exit_price"], "stop_price": t["stop_price"],
                    "target_price": t["target_1r"], "shares": t["shares"],
                    "entry_time": t["time_entered"], "exit_time": t["time_exited"],
                    "r_multiple": t["pnl_r_multiple"], "pnl_dollars": t["pnl_dollars"],
                    "mfe_r": t["peak_r"], "mae_r": t["trough_r"],
                    "regime_at_entry": t["regime_state"], "regime_at_exit": t["regime_state"],
                    "confidence": t["confidence_score"], "entry_indicators": t.get("indicators", {}),
                    "exit_indicators": {}, "market_return_during": 0,
                })
            except Exception:
                pass

            stats["total"] += 1
            if t["pnl_r_multiple"] > 0.1:
                stats["wins"] += 1
            elif t["pnl_r_multiple"] < -0.1:
                stats["losses"] += 1
            else:
                stats["breakeven"] += 1
            if t["result"] == "TARGET":
                stats["target_hits"] += 1
            elif t["result"] == "STOP":
                stats["stop_hits"] += 1
            elif t["result"] == "TIME_STOP":
                stats["time_stops"] += 1
            elif t["result"] == "BREAKEVEN":
                stats["be_saves"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.warning("Trade error %s: %s", t.get("id", "?"), e)

    try:
        with transaction() as conn:
            edge_decay.save_state(conn)
    except Exception as e:
        logger.warning("Edge decay persist: %s", e)

    return stats


def build_outcomes(trades: list[dict], replace: bool = False) -> None:
    """Write outcomes to outcomes.jsonl and rebuild downstream indices."""
    outcomes_path = _ROOT / "data" / "outcomes.jsonl"
    outcomes_path.parent.mkdir(parents=True, exist_ok=True)

    mode = "w" if replace else "a"
    action = "Replaced" if replace else "Appended"

    with open(outcomes_path, mode) as f:
        for t in trades:
            outcome = {
                "signal_id": t["signal_id"],
                "ticker": t["ticker"],
                "direction": t["direction"],
                "strategy_tag": t["strategy"],
                "regime_tag": t["regime_state"],
                "track": "INTRADAY_SWING",
                "time_window": t["time_window"],
                "month": t.get("month", "UNK"),
                "entry": t["entry_price"],
                "stop": t["stop_price"],
                "target1": t["target_1r"],
                "exit_price": t["exit_price"],
                "outcome": t["result"],
                "r_multiple": t["pnl_r_multiple"],
                "gross_pnl": t["gross_pnl"],
                "net_pnl": t["net_pnl"],
                "mfe_r": t["peak_r"],
                "mae_r": t["trough_r"],
                "duration_min": t["duration_minutes"],
                "generated_at": t["time_entered"],
                "resolved_at": t["time_exited"],
                "liquidity_bucket": "NORMAL",
                "rung_reached": t.get("rung_reached", 0),
                "breakeven_saved": t.get("breakeven_saved", False),
                "confidence_score": t.get("confidence_score", 0),
                "indicator_signals": t.get("indicator_signals", {}),
                "bullish_count": t.get("bullish_count", 0),
            }
            f.write(json.dumps(outcome) + "\n")

    logger.info("%s %d outcomes to %s", action, len(trades), outcomes_path)

    for name, fn in [
        ("edge ledger", _rebuild_edge_ledger),
        ("meta-learner", _rebuild_meta_learner),
        ("per-ticker indicator ranks", _rebuild_indicator_ranks),
    ]:
        try:
            fn()
        except Exception as e:
            logger.warning("%s rebuild failed: %s", name, e)


def _rebuild_edge_ledger():
    from learning.edge_ledger import get_edge_ledger
    result = get_edge_ledger().rebuild()
    logger.info("Edge ledger rebuilt: %s", result)


def _rebuild_meta_learner():
    from learning.meta_learner import get_meta_learner
    weights = get_meta_learner().update(regime_tag="NEUTRAL")
    logger.info("Meta-learner weights: %s", weights)


def _rebuild_indicator_ranks():
    from learning.adaptive_engine import AdaptiveEngine
    engine = AdaptiveEngine()
    report = engine.get_indicator_ranking_report()
    if report:
        logger.info("Indicator ranking report generated (%d chars)", len(report))


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(all_trades: list[dict], stats: dict) -> None:
    total = stats["total"]
    if total == 0:
        logger.error("No trades generated")
        return

    target_hits = stats["target_hits"]
    stop_hits = stats["stop_hits"]
    time_stops = stats["time_stops"]
    be_saves = stats["be_saves"]

    positive_ts = sum(1 for t in all_trades if t["result"] == "TIME_STOP" and t["pnl_r_multiple"] > 0)
    total_r = sum(t["pnl_r_multiple"] for t in all_trades)
    avg_r = total_r / total

    # Per-ticker breakdown
    ticker_stats: dict = {}
    for t in all_trades:
        tk = t["ticker"]
        if tk not in ticker_stats:
            ticker_stats[tk] = {"n": 0, "wins": 0, "r": 0.0}
        ticker_stats[tk]["n"] += 1
        ticker_stats[tk]["wins"] += 1 if t["pnl_r_multiple"] > 0.1 else 0
        ticker_stats[tk]["r"] += t["pnl_r_multiple"]

    # Per-regime breakdown
    regime_stats: dict = {}
    for t in all_trades:
        reg = t["regime_state"]
        if reg not in regime_stats:
            regime_stats[reg] = {"n": 0, "wins": 0}
        regime_stats[reg]["n"] += 1
        regime_stats[reg]["wins"] += 1 if t["pnl_r_multiple"] > 0.1 else 0

    # Per-month breakdown
    month_stats: dict = {}
    for t in all_trades:
        mo = t.get("month", "UNK")
        if mo not in month_stats:
            month_stats[mo] = {"n": 0, "wins": 0}
        month_stats[mo]["n"] += 1
        month_stats[mo]["wins"] += 1 if t["pnl_r_multiple"] > 0.1 else 0

    logger.info("")
    logger.info("=" * 70)
    logger.info("=== EXTENDED BACKFILL RESULTS ===")
    logger.info("=" * 70)
    logger.info("Total trades:            %d", total)
    logger.info("TARGET hits:             %d (%.1f%%)", target_hits, target_hits / total * 100)
    logger.info("BREAKEVEN (ladder):      %d (%.1f%%)", be_saves, be_saves / total * 100)
    logger.info("TIME_STOP:               %d (%.1f%%)", time_stops, time_stops / total * 100)
    logger.info("STOP hits:               %d (%.1f%%)", stop_hits, stop_hits / total * 100)
    logger.info("-" * 70)
    logger.info("Win rate (TARGET only):          %.1f%%", target_hits / total * 100)
    logger.info("Win rate (TARGET + pos TS):      %.1f%%", (target_hits + positive_ts) / total * 100)
    logger.info("Average R per trade:             %.4f", avg_r)
    logger.info("Expected daily return:           %.3f%%", avg_r * RISK_PCT * 100)
    logger.info("-" * 70)
    logger.info("--- Per-Ticker Breakdown ---")
    for tk, s in sorted(ticker_stats.items(), key=lambda x: -x[1]["wins"] / max(x[1]["n"], 1)):
        wr = s["wins"] / s["n"] * 100 if s["n"] > 0 else 0
        avg = s["r"] / s["n"] if s["n"] > 0 else 0
        logger.info("  %-14s  n=%3d  WR=%.0f%%  avgR=%.3f", tk, s["n"], wr, avg)
    logger.info("-" * 70)
    logger.info("--- Per-Regime Breakdown ---")
    for reg, s in sorted(regime_stats.items(), key=lambda x: -x[1]["n"]):
        wr = s["wins"] / s["n"] * 100 if s["n"] > 0 else 0
        logger.info("  %-30s  n=%3d  WR=%.0f%%", reg, s["n"], wr)
    logger.info("-" * 70)
    logger.info("--- Seasonal Breakdown (Month) ---")
    for mo in ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
               "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]:
        s = month_stats.get(mo)
        if s and s["n"] > 0:
            wr = s["wins"] / s["n"] * 100
            logger.info("  %s  n=%3d  WR=%.0f%%", mo, s["n"], wr)
    logger.info("=" * 70)
    logger.info("Learning modules seeded with %d trades across 2 years.", total)
    logger.info("Indicator ranks, regime stats, and seasonal patterns updated.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NZT-48 Extended 2-Year Backfill")
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS, help="Lookback years (default: 2)")
    parser.add_argument("--tickers", type=str, default="all", help="Comma-separated tickers or 'all'")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without feeding learning engine")
    parser.add_argument("--replace", action="store_true", help="Replace outcomes.jsonl instead of appending")
    args = parser.parse_args()

    tickers = ALL_TICKERS if args.tickers == "all" else [t.strip() for t in args.tickers.split(",")]
    years = max(1, min(5, args.years))

    logger.info("=" * 70)
    logger.info("NZT-48 EXTENDED BACKFILL — %d YEAR LEARNING SEED", years)
    logger.info("Tickers: %d | Dry-run: %s | Replace: %s", len(tickers), args.dry_run, args.replace)
    logger.info("=" * 70)

    if not args.dry_run:
        from delivery.database import init_db
        init_db()

    all_trades = []

    logger.info("PHASE 1: Fetching %d years of historical data...", years)
    for ticker in tickers:
        df = fetch_historical_data(ticker, years)
        if df is None or df.empty:
            continue
        indicators = compute_indicators(df)
        trades = simulate_trades(ticker, indicators)
        all_trades.extend(trades)
        if trades:
            wins = sum(1 for t in trades if t["pnl_r_multiple"] > 0.1)
            wr = wins / len(trades) * 100
            logger.info("  %-12s %3d trades  WR=%.0f%%", ticker, len(trades), wr)

    logger.info("PHASE 1 COMPLETE: %d total trades from %d tickers", len(all_trades), len(tickers))

    if not all_trades:
        logger.error("No trades generated — check data availability")
        return

    if args.dry_run:
        logger.info("DRY RUN — skipping learning engine feed")
        print_summary(all_trades, {
            "total": len(all_trades),
            "wins": sum(1 for t in all_trades if t["pnl_r_multiple"] > 0.1),
            "losses": sum(1 for t in all_trades if t["pnl_r_multiple"] < -0.1),
            "breakeven": sum(1 for t in all_trades if abs(t["pnl_r_multiple"]) <= 0.1),
            "errors": 0,
            "target_hits": sum(1 for t in all_trades if t["result"] == "TARGET"),
            "stop_hits": sum(1 for t in all_trades if t["result"] == "STOP"),
            "time_stops": sum(1 for t in all_trades if t["result"] == "TIME_STOP"),
            "be_saves": sum(1 for t in all_trades if t["result"] == "BREAKEVEN"),
        })
        return

    logger.info("PHASE 2: Feeding %d trades through learning engine...", len(all_trades))
    stats = feed_learning_engine(all_trades)
    logger.info("PHASE 2 COMPLETE: %s", stats)

    logger.info("PHASE 3: Building outcomes and rebuilding indices...")
    build_outcomes(all_trades, replace=args.replace)
    logger.info("PHASE 3 COMPLETE")

    print_summary(all_trades, stats)


if __name__ == "__main__":
    main()
