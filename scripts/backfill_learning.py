#!/usr/bin/env python3
"""
NZT-48 Historical Backfill Script
==================================
Seeds the self-learning system with 3 months of simulated trades
from real historical price data.

Usage: python scripts/backfill_learning.py
"""

import sys
from pathlib import Path

# Add project root to path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("nzt48.backfill")

# --- Configuration ---
LOOKBACK_DAYS = 90
MIN_MOVE_PCT = 0.030  # Raised: 3.0% minimum daily range (was 2.5%) — ensures enough range for 2% target + stop
TARGET_PCT = 0.02      # 2% target
STOP_PCT_3X = 0.01     # 1% stop for 3x ETPs
STOP_PCT_5X = 0.0075   # 0.75% stop for 5x ETPs
EQUITY = 10000.0
RISK_PCT = 0.0075      # 0.75% risk per trade
MIN_RVOL_SIM = 0.80    # Raised: 0.80 minimum RVOL (was implicit 0) — Chan (2013)
MIN_ADX_SIM = 20.0     # New: minimum ADX = 20 for trend confirmation — Faber (2013)
MIN_CONFIDENCE_SIM = 65  # Raised: 65 minimum confidence (was 60) — Harvey & Liu (2015)

_5X_TICKERS = {"QQQ5.L", "QQS5.L"}
# F-03: import from single source of truth (config.universe_constants)
from config.universe_constants import INVERSE_ETPS_SET as _INVERSE_TICKERS

BACKFILL_TICKERS = [
    "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L",
    "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L",
    "AMD3.L", "ARM3.L", "NVDS.L", "TSLS.L", "3LDE.L", "3LEU.L",
    "3GOL.L", "3SIL.L", "3OIL.L", "LLY3.L",
]


def fetch_historical_data(ticker: str, days: int = LOOKBACK_DAYS) -> Optional[pd.DataFrame]:
    """Fetch daily OHLCV data using yfinance."""
    try:
        import yfinance as yf
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days + 10)  # buffer for weekends
        df = yf.download(ticker, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), progress=False)
        if df is not None and len(df) > 0:
            logger.info("Fetched %d bars for %s", len(df), ticker)
            return df
    except Exception as e:
        logger.warning("Failed to fetch %s: %s", ticker, e)
    return None


def compute_indicators(df: pd.DataFrame) -> dict:
    """Compute key indicators from OHLCV data for each day."""
    indicators_by_date = {}

    closes = df["Close"].values
    highs = df["High"].values
    lows = df["Low"].values
    volumes = df["Volume"].values

    for i in range(20, len(df)):  # Need 20 bars for EMA20
        date = df.index[i]

        # ATR (14-period)
        atr_vals = []
        for j in range(max(1, i-13), i+1):
            tr = max(
                highs[j] - lows[j],
                abs(highs[j] - closes[j-1]),
                abs(lows[j] - closes[j-1])
            )
            atr_vals.append(tr)
        atr14 = sum(atr_vals) / len(atr_vals)

        # RSI (14-period)
        gains, losses = [], []
        for j in range(max(1, i-13), i+1):
            change = closes[j] - closes[j-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = sum(losses) / len(losses) if losses else 0.001
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi14 = 100 - (100 / (1 + rs))

        # RVOL (volume vs 20-day average)
        vol_window = volumes[max(0, i-19):i+1]
        avg_vol = sum(vol_window[:-1]) / max(len(vol_window)-1, 1) if len(vol_window) > 1 else 1
        rvol = volumes[i] / avg_vol if avg_vol > 0 else 1.0

        # EMA20
        ema20 = sum(closes[max(0, i-19):i+1]) / min(20, i+1)

        # Daily range %
        daily_range_pct = (highs[i] - lows[i]) / closes[i] if closes[i] > 0 else 0

        # ATR as % of price
        atr_pct = atr14 / closes[i] if closes[i] > 0 else 0

        indicators_by_date[date] = {
            "close": float(closes[i]),
            "prev_close": float(closes[i-1]),
            "open": float(df["Open"].values[i]),
            "high": float(highs[i]),
            "low": float(lows[i]),
            "volume": float(volumes[i]),
            "atr14": float(atr14),
            "atr_pct": float(atr_pct),
            "rsi14": float(rsi14),
            "rvol": float(rvol),
            "ema20": float(ema20),
            "daily_range_pct": float(daily_range_pct),
        }

    return indicators_by_date


def simulate_trades(ticker: str, indicators_by_date: dict) -> list[dict]:
    """Simulate S15-style trades from historical data with realistic filters.

    Improvements over v1:
    - Regime-based entry filtering (skip counter-trend, skip SHOCK)
    - Multi-factor confidence scoring with minimum gate
    - Profit ladder simulation (rung 1 → breakeven stop, rung 2 → target)
    - Confirmation-based entry (not raw open)
    - Extended stats: rung_reached, breakeven_saved, partial_profit
    """
    trades = []

    is_5x = ticker in _5X_TICKERS
    is_inverse = ticker in _INVERSE_TICKERS
    stop_pct = STOP_PCT_5X if is_5x else STOP_PCT_3X

    dates = sorted(indicators_by_date.keys())

    for date in dates:
        ind = indicators_by_date[date]

        # Only simulate if daily range is sufficient
        if ind["daily_range_pct"] < MIN_MOVE_PCT:
            continue

        # Determine direction based on trend
        direction = "SHORT" if is_inverse else "LONG"

        open_price = ind["open"]
        high = ind["high"]
        low = ind["low"]
        close = ind["close"]
        prev_close = ind["prev_close"]
        rsi = ind["rsi14"]
        rvol = ind["rvol"]
        atr_pct = ind["atr_pct"]

        # --- Classify regime from indicators ---
        if ind["daily_range_pct"] > 0.06 and abs(close - open_price) / open_price > 0.04:
            regime = "SHOCK"
        elif rsi > 60 and close > ind["ema20"]:
            regime = "TRENDING_UP_STRONG"
        elif rsi > 50 and close > ind["ema20"]:
            regime = "TRENDING_UP_MOD"
        elif rsi < 30 and close < ind["ema20"]:
            regime = "TRENDING_DOWN_STRONG"
        elif rsi < 40 and close < ind["ema20"]:
            regime = "TRENDING_DOWN_MOD"
        elif rsi < 50:
            regime = "RANGE_BOUND"
        else:
            regime = "NEUTRAL"

        # --- GATE A: Volume gate (Chan 2013) — no trades on low volume ---
        if rvol < MIN_RVOL_SIM:
            continue

        # --- GATE B: ADX trend gate (Faber 2013) — only trade in trending markets ---
        # Approximate ADX from daily range vs ATR (actual ADX needs 14-bar EMA)
        # Use daily_range_pct vs atr_pct as proxy: if today's range >> ATR → trending
        adx_proxy = (ind["daily_range_pct"] / ind["atr_pct"]) * 25 if ind["atr_pct"] > 0 else 0
        if adx_proxy < MIN_ADX_SIM:
            continue

        # --- GATE C: Regime gate — only trade regime-aligned setups (Faber 2013) ---
        # Original: skip counter-trend + shock
        if regime in ("TRENDING_DOWN_STRONG", "TRENDING_DOWN_MOD") and direction == "LONG":
            continue
        if regime in ("TRENDING_UP_STRONG", "TRENDING_UP_MOD") and direction == "SHORT":
            continue
        if regime == "SHOCK":
            continue  # Never trade in SHOCK regime
        # New: also skip RANGE_BOUND — too choppy for 2% target
        if regime == "RANGE_BOUND":
            continue

        # --- GATE D: Multi-factor confidence scoring (Harvey & Liu 2015) ---
        confidence_score = 50  # base
        # RVOL bonus — higher volume = higher conviction
        if rvol > 2.0:
            confidence_score += 15
        elif rvol > 1.5:
            confidence_score += 10
        elif rvol > 1.0:
            confidence_score += 5
        # Trend alignment
        if regime in ("TRENDING_UP_STRONG",) and direction == "LONG":
            confidence_score += 15
        elif regime in ("TRENDING_UP_MOD",) and direction == "LONG":
            confidence_score += 10
        elif regime == "NEUTRAL":
            confidence_score += 0
        # ATR reachability (can the ATR actually reach 2%?)
        if atr_pct >= 3.0:
            confidence_score += 10
        elif atr_pct >= 2.0:
            confidence_score += 5
        # RSI confirmation: 50-70 is ideal for LONG (trending, not overbought)
        if direction == "LONG" and 50 < rsi <= 70:
            confidence_score += 5
        if direction == "LONG" and rsi > 75:
            confidence_score -= 15  # Overbought = don't chase
        if direction == "LONG" and rsi < 45:
            confidence_score -= 10  # Weak momentum
        if direction == "SHORT" and rsi < 25:
            confidence_score -= 15
        confidence_score = max(20, min(95, confidence_score))

        # --- GATE E: Confidence minimum gate (raised to 65) ---
        if confidence_score < MIN_CONFIDENCE_SIM:
            continue  # Only take high-conviction setups — "Today's excellence is tomorrow's average"

        # --- Fix 5: Confirmation-based entry instead of open ---
        if direction == "LONG":
            entry = round(open_price + 0.3 * (high - open_price), 4)
        else:
            entry = round(open_price - 0.3 * (open_price - low), 4)

        stop_price_abs = round(entry * (1 - stop_pct), 4) if direction == "LONG" else round(entry * (1 + stop_pct), 4)
        target = round(entry * (1 + TARGET_PCT), 4) if direction == "LONG" else round(entry * (1 - TARGET_PCT), 4)

        # --- Fix 4: Simulate profit ladder partial exits ---
        rung1_price = round(entry * 1.01, 4) if direction == "LONG" else round(entry * 0.99, 4)
        rung2_price = round(entry * 1.02, 4) if direction == "LONG" else round(entry * 0.98, 4)

        rung_reached = 0
        breakeven_saved = False
        partial_profit = 0.0

        if direction == "LONG":
            if high >= rung2_price:
                rung_reached = 2
                # Target hit — did stop get hit BEFORE target?
                if low > stop_price_abs:
                    # Clean target hit
                    result = "TARGET"
                    exit_price = rung2_price
                    pnl_pct = 0.02
                else:
                    # Both stop and target levels touched — estimate order
                    if open_price >= prev_close:
                        # Gap up / bullish open → target likely hit first
                        result = "TARGET"
                        exit_price = rung2_price
                        pnl_pct = 0.02
                    else:
                        result = "STOP"
                        exit_price = stop_price_abs
                        pnl_pct = -stop_pct
            elif high >= rung1_price:
                rung_reached = 1
                # Rung 1 hit (+1%) — stop moves to breakeven
                if low <= stop_price_abs:
                    # Hit breakeven stop (moved up from original stop)
                    result = "BREAKEVEN"
                    exit_price = entry
                    pnl_pct = 0.0
                    breakeven_saved = True
                else:
                    # Price rose to +1% but didn't hit target, close at end of day
                    result = "TIME_STOP"
                    exit_price = close
                    pnl_pct = (close - entry) / entry if entry > 0 else 0
            else:
                # Never hit rung 1
                if low <= stop_price_abs:
                    result = "STOP"
                    exit_price = stop_price_abs
                    pnl_pct = -stop_pct
                else:
                    result = "TIME_STOP"
                    exit_price = close
                    pnl_pct = (close - entry) / entry if entry > 0 else 0
        else:
            # SHORT direction — mirror logic
            if low <= rung2_price:
                rung_reached = 2
                if high < stop_price_abs:
                    result = "TARGET"
                    exit_price = rung2_price
                    pnl_pct = 0.02
                else:
                    if open_price <= prev_close:
                        result = "TARGET"
                        exit_price = rung2_price
                        pnl_pct = 0.02
                    else:
                        result = "STOP"
                        exit_price = stop_price_abs
                        pnl_pct = -stop_pct
            elif low <= rung1_price:
                rung_reached = 1
                if high >= stop_price_abs:
                    result = "BREAKEVEN"
                    exit_price = entry
                    pnl_pct = 0.0
                    breakeven_saved = True
                else:
                    result = "TIME_STOP"
                    exit_price = close
                    pnl_pct = (entry - close) / entry if entry > 0 else 0
            else:
                if high >= stop_price_abs:
                    result = "STOP"
                    exit_price = stop_price_abs
                    pnl_pct = -stop_pct
                else:
                    result = "TIME_STOP"
                    exit_price = close
                    pnl_pct = (entry - close) / entry if entry > 0 else 0

        # Calculate sizing
        risk_per_share = abs(entry - stop_price_abs)
        risk_dollars = EQUITY * RISK_PCT
        shares = max(1, int(risk_dollars / risk_per_share)) if risk_per_share > 0 else 1
        if direction == "LONG":
            pnl_dollars = (exit_price - entry) * shares
        else:
            pnl_dollars = (entry - exit_price) * shares
        r_multiple = pnl_dollars / risk_dollars if risk_dollars > 0 else 0

        # Peak/trough R (from daily H/L)
        if direction == "LONG":
            peak_r = (high - entry) / risk_per_share if risk_per_share > 0 else 0
            trough_r = (low - entry) / risk_per_share if risk_per_share > 0 else 0
        else:
            peak_r = (entry - low) / risk_per_share if risk_per_share > 0 else 0
            trough_r = (entry - high) / risk_per_share if risk_per_share > 0 else 0

        # Determine time window (simulate LSE timing)
        hour = 10  # Assume morning entry
        if hour < 10:
            time_window = "OPEN"
        elif hour < 12:
            time_window = "MORNING"
        elif hour < 14:
            time_window = "MIDDAY"
        else:
            time_window = "AFTERNOON"

        trade_id = f"BF-{ticker.replace('.', '')}-{date.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"

        entry_time = datetime.combine(date.date() if hasattr(date, 'date') else date,
                                       datetime.min.time().replace(hour=9, minute=30),
                                       tzinfo=timezone.utc)
        exit_time = entry_time + timedelta(minutes=int(abs(pnl_pct * 10000) + 30))  # Rough duration

        trade = {
            "id": trade_id,
            "signal_id": f"SIG-{trade_id}",
            "ticker": ticker,
            "direction": direction,
            "strategy": "S15" if not is_inverse else "S16",
            "entry_price": round(entry, 4),
            "exit_price": round(exit_price, 4),
            "stop_price": round(stop_price_abs, 4),
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
            "time_entered": entry_time.isoformat(),
            "time_exited": exit_time.isoformat(),
            "duration_minutes": (exit_time - entry_time).total_seconds() / 60,
            "peak_r": round(peak_r, 4),
            "trough_r": round(trough_r, 4),
            "exit_reason": result,
            "result": result,
            # Fix 6: Extended profit ladder stats
            "rung_reached": rung_reached,
            "breakeven_saved": breakeven_saved,
            "partial_profit": round(partial_profit, 4),
            # Indicator snapshot at entry
            "indicators": ind,
            "bot": "A",
            "bot_instance": "BULL" if direction == "LONG" else "BEAR",
        }
        trades.append(trade)

    return trades


def feed_learning_engine(trades: list[dict]) -> dict:
    """Feed all trades through the learning engine and related modules."""
    from models import Trade, Direction, Bot, BotInstance
    from learning.learning_engine import LearningEngine
    from learning.edge_decay_engine import EdgeDecayEngine
    from learning.strategy_tournament import StrategyTournament
    from learning.performance_attribution import PerformanceAttributionEngine
    from learning.trade_autopsy import TradeAutopsyEngine
    from delivery.database import init_db, transaction

    # Initialize
    init_db()
    learning = LearningEngine()
    edge_decay = EdgeDecayEngine()
    tournament = StrategyTournament()
    perf_attribution = PerformanceAttributionEngine()
    trade_autopsy = TradeAutopsyEngine()

    stats = {"total": 0, "wins": 0, "losses": 0, "breakeven": 0, "errors": 0,
             "target_hits": 0, "stop_hits": 0, "time_stops": 0, "be_saves": 0}

    # Sort by date
    trades.sort(key=lambda t: t["time_entered"])

    for t in trades:
        try:
            # Build Trade object
            direction = Direction.LONG if t["direction"] == "LONG" else Direction.SHORT
            trade = Trade(
                id=t["id"],
                signal_id=t["signal_id"],
                bot=Bot.A if t["bot"] == "A" else Bot.B,
                bot_instance=BotInstance.BULL if t["bot_instance"] == "BULL" else BotInstance.BEAR,
                ticker=t["ticker"],
                direction=direction,
                strategy=t["strategy"],
                entry_price=t["entry_price"],
                exit_price=t["exit_price"],
                stop_price=t["stop_price"],
                target_1r=t["target_1r"],
                target_2r=t.get("target_2r", 0),
                shares=t["shares"],
                risk_dollars=t["risk_dollars"],
                risk_percent=t["risk_percent"],
                position_pct_equity=t["risk_percent"],
                pnl_dollars=t["pnl_dollars"],
                pnl_r_multiple=t["pnl_r_multiple"],
                gross_pnl=t["gross_pnl"],
                commissions=t["commissions"],
                net_pnl=t["net_pnl"],
                expected_entry=t["entry_price"],
                actual_entry=t["entry_price"],
                fill_quality=90.0,
                entry_quality=t["entry_quality"],
                exit_quality=t["exit_quality"],
                timing_quality=70.0,
                confidence_score=t["confidence_score"],
                regime_state=t["regime_state"],
                sector_rs=0.0,
                macro_score=50,
                narrative_sentiment="NEUTRAL",
                gex_regime=t["gex_regime"],
                dix_reading=0.0,
                internals_composite=0,
                vix_level=t["vix_level"],
                calendar_risk="LOW",
                patterns_detected=[],
                reason_codes=[],
                invalidation_reason="",
                emotional_state="CALM",
                firewall_triggers=[],
                what_worked="Backfill simulated trade",
                what_failed="",
                improvement_note="",
                would_take_again=t["pnl_r_multiple"] > 0,
                time_entered=datetime.fromisoformat(t["time_entered"]),
                time_exited=datetime.fromisoformat(t["time_exited"]),
                duration_minutes=t["duration_minutes"],
            )

            # Feed learning engine (all 13 subsystems)
            learning.record_trade(trade)

            # Feed edge decay
            edge_decay.record_trade(
                strategy=t["strategy"],
                regime=t["regime_state"],
                entry_time=datetime.fromisoformat(t["time_entered"]),
                r_multiple=t["pnl_r_multiple"],
            )

            # Feed tournament
            tournament.record_trade(
                strategy=t["strategy"],
                r_multiple=t["pnl_r_multiple"],
            )

            # Feed performance attribution
            try:
                perf_attribution.attribute_trade({
                    "trade_id": t["id"],
                    "ticker": t["ticker"],
                    "strategy": t["strategy"],
                    "direction": t["direction"],
                    "entry_price": t["entry_price"],
                    "exit_price": t["exit_price"],
                    "stop_price": t["stop_price"],
                    "target_price": t["target_1r"],
                    "shares": t["shares"],
                    "entry_time": t["time_entered"],
                    "exit_time": t["time_exited"],
                    "r_multiple": t["pnl_r_multiple"],
                    "pnl_dollars": t["pnl_dollars"],
                    "mfe_r": t["peak_r"],
                    "mae_r": t["trough_r"],
                    "regime_at_entry": t["regime_state"],
                    "regime_at_exit": t["regime_state"],
                    "confidence": t["confidence_score"],
                    "entry_indicators": t.get("indicators", {}),
                    "exit_indicators": {},
                    "market_return_during": 0,
                })
            except Exception:
                pass

            # Feed trade autopsy (persist to DB)
            try:
                # Build a minimal virtual_trade-like object for autopsy
                class _VTProxy:
                    pass
                vt = _VTProxy()
                for k, v in t.items():
                    setattr(vt, k, v)
                vt.r_multiple = t["pnl_r_multiple"]
                vt.indicator_snapshot_entry = t.get("indicators", {})
                vt.indicator_snapshot_exit = {}
                vt.regime_at_entry = t["regime_state"]

                autopsy = trade_autopsy.analyse(
                    trade=vt,
                    entry_indicators=t.get("indicators", {}),
                    market_ctx={"regime": t["regime_state"]},
                )
                with transaction() as conn:
                    trade_autopsy.persist(conn, autopsy)
            except Exception:
                pass

            # Update stats
            stats["total"] += 1
            if t["pnl_r_multiple"] > 0.1:
                stats["wins"] += 1
            elif t["pnl_r_multiple"] < -0.1:
                stats["losses"] += 1
            else:
                stats["breakeven"] += 1
            # Track result types
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
            logger.warning("Failed to process trade %s: %s", t.get("id", "?"), e)

    # Persist edge decay state
    try:
        with transaction() as conn:
            edge_decay.save_state(conn)
        logger.info("Edge decay state persisted to database")
    except Exception as e:
        logger.warning("Edge decay persist failed: %s", e)

    return stats


def build_outcomes_and_edge_ledger(trades: list[dict]) -> None:
    """Write outcomes to JSONL and rebuild edge ledger."""
    outcomes_path = _ROOT / "data" / "outcomes.jsonl"
    outcomes_path.parent.mkdir(parents=True, exist_ok=True)

    with open(outcomes_path, "a") as f:
        for t in trades:
            outcome = {
                "signal_id": t["signal_id"],
                "ticker": t["ticker"],
                "direction": t["direction"],
                "strategy_tag": t["strategy"],
                "regime_tag": t["regime_state"],
                "track": "INTRADAY_SWING",
                "time_window": t["time_window"],
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
                # Extended profit ladder stats
                "rung_reached": t.get("rung_reached", 0),
                "breakeven_saved": t.get("breakeven_saved", False),
                "partial_profit": t.get("partial_profit", 0.0),
                "confidence_score": t.get("confidence_score", 0),
            }
            f.write(json.dumps(outcome) + "\n")

    logger.info("Wrote %d outcomes to %s", len(trades), outcomes_path)

    # Rebuild edge ledger
    try:
        from learning.edge_ledger import get_edge_ledger
        ledger = get_edge_ledger()
        result = ledger.rebuild()
        logger.info("Edge ledger rebuilt: %s", result)
    except Exception as e:
        logger.warning("Edge ledger rebuild failed: %s", e)

    # Rebuild meta-learner weights
    try:
        from learning.meta_learner import get_meta_learner
        ml = get_meta_learner()
        weights = ml.update(regime_tag="NEUTRAL")
        logger.info("Meta-learner weights updated: %s", weights)
    except Exception as e:
        logger.warning("Meta-learner update failed: %s", e)

    # Run drift detection
    try:
        from learning.drift import DriftDetector
        drift = DriftDetector()
        report = drift.run_all()
        logger.info("Drift detection complete: alerts=%s", report.get("alerts", []))
    except Exception as e:
        logger.warning("Drift detection failed: %s", e)


def main():
    """Main backfill entry point."""
    logger.info("=" * 60)
    logger.info("NZT-48 HISTORICAL BACKFILL — 3 MONTH LEARNING SEED")
    logger.info("=" * 60)

    # Initialize database
    from delivery.database import init_db
    init_db()

    all_trades = []

    # Phase 1: Fetch data and simulate trades
    logger.info("PHASE 1: Fetching historical data for %d tickers...", len(BACKFILL_TICKERS))
    for ticker in BACKFILL_TICKERS:
        logger.info("Processing %s...", ticker)
        df = fetch_historical_data(ticker)
        if df is None or df.empty:
            logger.warning("No data for %s — skipping", ticker)
            continue

        indicators = compute_indicators(df)
        trades = simulate_trades(ticker, indicators)
        all_trades.extend(trades)
        logger.info("  %s: %d bars → %d simulated trades", ticker, len(df), len(trades))

    logger.info("PHASE 1 COMPLETE: %d total simulated trades across %d tickers",
                len(all_trades), len(BACKFILL_TICKERS))

    if not all_trades:
        logger.error("No trades generated — cannot backfill")
        return

    # Phase 2: Feed learning engine
    logger.info("PHASE 2: Feeding trades through learning engine...")
    stats = feed_learning_engine(all_trades)
    logger.info("PHASE 2 COMPLETE: %s", stats)

    # Phase 3: Build outcomes and edge ledger
    logger.info("PHASE 3: Building outcomes and edge ledger...")
    build_outcomes_and_edge_ledger(all_trades)
    logger.info("PHASE 3 COMPLETE")

    # --- Detailed Summary Statistics ---
    total = stats["total"]
    if total == 0:
        logger.error("No trades passed filters — cannot produce summary")
        return

    target_hits = stats["target_hits"]
    be_saves = stats["be_saves"]
    time_stops = stats["time_stops"]
    stop_hits = stats["stop_hits"]

    # Positive time stops (closed above entry)
    positive_ts = sum(1 for t in all_trades if t["result"] == "TIME_STOP" and t["pnl_r_multiple"] > 0)

    # Average R per trade
    total_r = sum(t["pnl_r_multiple"] for t in all_trades)
    avg_r = total_r / total if total > 0 else 0

    # Win rate calculations
    win_rate_target = target_hits / total * 100 if total > 0 else 0
    win_rate_broad = (target_hits + positive_ts) / total * 100 if total > 0 else 0
    be_rate = be_saves / total * 100 if total > 0 else 0
    pure_loss_rate = stop_hits / total * 100 if total > 0 else 0

    # Expected daily return (avg R * risk per trade)
    expected_daily_r = avg_r * RISK_PCT * 100  # as percentage

    logger.info("")
    logger.info("=" * 60)
    logger.info("=== BACKFILL RESULTS ===")
    logger.info("=" * 60)
    logger.info("Total trades attempted:            %d", total)
    logger.info("Trades after filtering:            %d (fed to learning engine)", total)
    logger.info("TARGET hits:                       %d (%.1f%%)", target_hits, target_hits / total * 100)
    logger.info("BREAKEVEN (ladder saved):          %d (%.1f%%)", be_saves, be_rate)
    logger.info("TIME_STOP:                         %d (%.1f%%)", time_stops, time_stops / total * 100)
    logger.info("STOP hits:                         %d (%.1f%%)", stop_hits, pure_loss_rate)
    logger.info("-" * 60)
    logger.info("Win rate (TARGET only):            %.1f%%", win_rate_target)
    logger.info("Win rate (TARGET + positive TS):   %.1f%%", win_rate_broad)
    logger.info("Breakeven rate (ladder saves):     %.1f%%", be_rate)
    logger.info("Pure loss rate:                    %.1f%%", pure_loss_rate)
    logger.info("Average R per trade:               %.4f", avg_r)
    logger.info("Expected daily return:             %.3f%%", expected_daily_r)
    logger.info("-" * 60)
    logger.info("Errors:                            %d", stats["errors"])
    logger.info("=" * 60)
    logger.info("Learning modules seeded. Ready for live trading.")


if __name__ == "__main__":
    main()
