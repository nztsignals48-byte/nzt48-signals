"""
NZT-48 Exit Engine
Track-aware exit scoring for open positions.
Provides exit scores, kill conditions, sell intents, and batch sell plans.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("nzt48.exit_engine")

# ---------------------------------------------------------------------------
# Exit score contribution thresholds
# ---------------------------------------------------------------------------
_SCORE_REGIME_FLIP = 30          # Points if regime flipped against position
_SCORE_LOSING_POSITION = 25      # Points if current_r < -0.5
_SCORE_TIME_TIMEOUT = 20         # Points if in trade > 240 min (4 hours)
_SCORE_RSI_DIVERGENCE = 15       # Points if RSI divergence detected
_SCORE_PROFIT_TARGET = 10        # Points if at profit target (current_r > 2.0)

# Sell intent thresholds
_INTENT_HOLD_MAX = 30
_INTENT_TRAIL_MAX = 60
_INTENT_PARTIAL_MAX = 80
# Above _INTENT_PARTIAL_MAX = EXIT_NOW

# Time thresholds
_INTRADAY_TIMEOUT_MINUTES = 240  # 4 hours

# R thresholds
_LOSING_R_THRESHOLD = -0.5
_PROFIT_TARGET_R = 2.0

# RSI parameters for divergence detection
_RSI_PERIOD = 14
_RSI_DIVERGENCE_LOOKBACK = 20    # Bars to look back for divergence

# Liquidity buckets for batch sell
_LIQUIDITY_HIGH = 5_000_000      # Notional > 5M = high liquidity
_LIQUIDITY_MED = 1_000_000       # Notional > 1M = medium liquidity

# Regime classification for direction alignment
_BULLISH_REGIMES = {"RISK_ON", "TRENDING", "BULL", "EXPANSION", "MOMENTUM"}
_BEARISH_REGIMES = {"RISK_OFF", "BEAR", "CONTRACTION", "SHOCK", "DEFENSIVE"}


def _compute_rsi(closes: pd.Series, period: int = _RSI_PERIOD) -> float:
    """Compute RSI from a series of close prices. Returns 50.0 on insufficient data."""
    if len(closes) < period + 1:
        return 50.0
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = gain.rolling(window=period, min_periods=period).mean().iloc[-1]
    avg_loss = loss.rolling(window=period, min_periods=period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Compute ATR from OHLC DataFrame. Returns 0.0 on insufficient data."""
    if len(df) < period + 1:
        return 0.0
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_series = tr.rolling(window=period, min_periods=period).mean()
    val = atr_series.iloc[-1]
    return float(val) if pd.notna(val) else 0.0


def _detect_rsi_divergence(
    df: pd.DataFrame,
    direction: str,
    lookback: int = _RSI_DIVERGENCE_LOOKBACK,
) -> bool:
    """
    Detect bearish divergence (for LONG positions) or bullish divergence (for SHORT).

    Bearish divergence: price makes higher high but RSI makes lower high.
    Bullish divergence: price makes lower low but RSI makes higher low.

    Returns True if divergence is detected against the position direction.
    """
    if len(df) < lookback + _RSI_PERIOD:
        return False

    closes = df["Close"].iloc[-(lookback + _RSI_PERIOD):]
    if len(closes) < _RSI_PERIOD + 5:
        return False

    # Compute RSI for the lookback window
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = gain.rolling(window=_RSI_PERIOD, min_periods=_RSI_PERIOD).mean()
    avg_loss = loss.rolling(window=_RSI_PERIOD, min_periods=_RSI_PERIOD).mean()

    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
    rsi_series = 100.0 - (100.0 / (1.0 + rs))
    rsi_series = rsi_series.dropna()

    if len(rsi_series) < 5:
        return False

    # Use the last portion for comparison
    price_recent = closes.iloc[-lookback:]
    rsi_recent = rsi_series.iloc[-lookback:] if len(rsi_series) >= lookback else rsi_series

    if len(price_recent) < 4 or len(rsi_recent) < 4:
        return False

    half = len(price_recent) // 2

    if direction == "LONG":
        # Bearish divergence: price higher high, RSI lower high
        price_first_half_max = float(price_recent.iloc[:half].max())
        price_second_half_max = float(price_recent.iloc[half:].max())
        rsi_first_half_max = float(rsi_recent.iloc[:min(half, len(rsi_recent))].max())
        rsi_second_half_max = float(rsi_recent.iloc[min(half, len(rsi_recent)):].max()) \
            if len(rsi_recent) > half else float(rsi_recent.iloc[-1])

        if price_second_half_max > price_first_half_max and rsi_second_half_max < rsi_first_half_max:
            return True
    else:
        # Bullish divergence: price lower low, RSI higher low
        price_first_half_min = float(price_recent.iloc[:half].min())
        price_second_half_min = float(price_recent.iloc[half:].min())
        rsi_first_half_min = float(rsi_recent.iloc[:min(half, len(rsi_recent))].min())
        rsi_second_half_min = float(rsi_recent.iloc[min(half, len(rsi_recent)):].min()) \
            if len(rsi_recent) > half else float(rsi_recent.iloc[-1])

        if price_second_half_min < price_first_half_min and rsi_second_half_min > rsi_first_half_min:
            return True

    return False


def _is_regime_against(regime_tag: str, direction: str) -> bool:
    """Check if the current regime has flipped against the position direction."""
    tag = regime_tag.upper() if regime_tag else "NEUTRAL"
    if direction == "LONG" and tag in _BEARISH_REGIMES:
        return True
    if direction == "SHORT" and tag in _BULLISH_REGIMES:
        return True
    return False


def _estimate_liquidity_bucket(avg_volume: float, price: float) -> str:
    """Classify a position into a liquidity bucket based on notional volume."""
    notional = avg_volume * price
    if notional >= _LIQUIDITY_HIGH:
        return "HIGH"
    elif notional >= _LIQUIDITY_MED:
        return "MEDIUM"
    else:
        return "LOW"


class ExitEngine:
    """
    Track-aware exit scoring engine for open positions.

    Evaluates each open position against current market data and regime
    to produce exit scores, kill conditions, and sell intents. Also provides
    batch sell planning for coordinated exits.
    """

    def __init__(self) -> None:
        self._last_exit_scores: list[dict] = []

    def score_exits(
        self,
        positions: list[dict],
        bars_batch: dict[str, pd.DataFrame],
        regime_tag: str,
    ) -> list[dict]:
        """
        Score each open position for exit urgency.

        Args:
            positions: List of position dicts, each with keys:
                - ticker (str): Instrument ticker
                - direction (str): 'LONG' or 'SHORT'
                - entry_price (float): Entry price
                - current_price (float): Current/latest price
                - entry_time (str or datetime): When the position was entered
                - peak_r (float): Peak R multiple achieved during the trade
                - strategy (str): Strategy that originated the trade
                - shares (int/float): Number of shares held
            bars_batch: Dict mapping ticker -> DataFrame with OHLC + Volume bars.
            regime_tag: Current market regime string.

        Returns:
            List of exit score dicts, one per position, each containing:
                - ticker, direction, entry_price, current_price, shares
                - current_r, time_in_trade_minutes, rsi
                - exit_score (0-100), sell_intent, kill_conditions, reasoning
        """
        results: list[dict] = []

        for pos in positions:
            try:
                result = self._score_single_position(pos, bars_batch, regime_tag)
                if result is not None:
                    results.append(result)
            except Exception as exc:
                logger.warning(
                    "ExitEngine: error scoring position %s: %s",
                    pos.get("ticker", "UNKNOWN"), exc,
                )
                # Return a conservative EXIT_NOW for positions we cannot evaluate
                results.append(self._fallback_exit(pos, str(exc)))

        self._last_exit_scores = results

        logger.info(
            "ExitEngine: scored %d positions — %d HOLD, %d TRAIL, %d PARTIAL, %d EXIT_NOW",
            len(results),
            sum(1 for r in results if r["sell_intent"] == "HOLD"),
            sum(1 for r in results if r["sell_intent"] == "TRAIL"),
            sum(1 for r in results if r["sell_intent"] == "PARTIAL"),
            sum(1 for r in results if r["sell_intent"] == "EXIT_NOW"),
        )

        return results

    def batch_sell_plan(self, sell_intents: list[dict]) -> dict:
        """
        Build a coordinated batch sell plan from scored positions.

        Groups positions by liquidity bucket, orders execution by liquidity
        (most liquid first to minimise market impact), and flags correlated
        positions that would be dumped simultaneously.

        Args:
            sell_intents: List of exit score dicts from score_exits() where
                          sell_intent is not 'HOLD'.

        Returns:
            Dict with keys:
                - execution_order: list of dicts ordered for execution
                - warnings: list of warning strings (e.g. correlated positions)
                - estimated_impact: dict with summary metrics
                - plan_time: ISO timestamp
        """
        if not sell_intents:
            return {
                "execution_order": [],
                "warnings": [],
                "estimated_impact": {
                    "total_positions": 0,
                    "total_shares": 0,
                    "buckets": {},
                },
                "plan_time": datetime.now(timezone.utc).isoformat(),
            }

        # ----- Group by liquidity bucket -----
        buckets: dict[str, list[dict]] = {"HIGH": [], "MEDIUM": [], "LOW": []}
        for intent in sell_intents:
            bucket = intent.get("liquidity_bucket", "LOW")
            buckets[bucket].append(intent)

        # ----- Build execution order: HIGH first, then MEDIUM, then LOW -----
        # Within each bucket, sort by exit_score descending (most urgent first)
        execution_order: list[dict] = []
        for bucket_name in ["HIGH", "MEDIUM", "LOW"]:
            bucket_positions = sorted(
                buckets[bucket_name],
                key=lambda x: x.get("exit_score", 0),
                reverse=True,
            )
            for pos in bucket_positions:
                execution_order.append({
                    "ticker": pos.get("ticker"),
                    "direction": pos.get("direction"),
                    "shares": pos.get("shares", 0),
                    "sell_intent": pos.get("sell_intent"),
                    "exit_score": pos.get("exit_score", 0),
                    "liquidity_bucket": bucket_name,
                    "strategy": pos.get("strategy", "unknown"),
                    "current_r": pos.get("current_r", 0.0),
                })

        # ----- Detect correlated positions -----
        warnings: list[str] = []
        tickers_exiting = [p["ticker"] for p in execution_order]

        # LSE leveraged ETPs: group by underlying
        underlying_map: dict[str, list[str]] = {}
        _UNDERLYING_GROUPS = {
            "QQQ": ["QQQ3.L", "QQQ5.L", "QQQS.L"],
            "SP500": ["3LUS.L", "3USS.L", "SP5L.L"],
            "SEMI": ["3SEM.L"],
            "AI": ["GPT3.L"],
            "NVDA": ["NVD3.L"],
            "TSLA": ["TSL3.L"],
            "TSM": ["TSM3.L"],
            "MU": ["MU2.L"],
        }

        for underlying, group_tickers in _UNDERLYING_GROUPS.items():
            exiting_in_group = [t for t in tickers_exiting if t in group_tickers]
            if len(exiting_in_group) > 1:
                warnings.append(
                    f"CORRELATED EXIT: {', '.join(exiting_in_group)} share underlying "
                    f"{underlying} - dumping simultaneously may amplify market impact"
                )
            underlying_map[underlying] = exiting_in_group

        # Warn if selling many positions at once
        if len(execution_order) >= 4:
            warnings.append(
                f"VOLUME WARNING: {len(execution_order)} positions exiting simultaneously "
                f"- consider staggering exits over multiple scan cycles"
            )

        # ----- Estimated impact summary -----
        total_shares = sum(p.get("shares", 0) for p in execution_order)
        bucket_summary = {}
        for bucket_name in ["HIGH", "MEDIUM", "LOW"]:
            count = len(buckets[bucket_name])
            if count > 0:
                bucket_summary[bucket_name] = {
                    "count": count,
                    "tickers": [p.get("ticker") for p in buckets[bucket_name]],
                }

        plan = {
            "execution_order": execution_order,
            "warnings": warnings,
            "estimated_impact": {
                "total_positions": len(execution_order),
                "total_shares": total_shares,
                "buckets": bucket_summary,
                "exit_now_count": sum(
                    1 for p in execution_order if p["sell_intent"] == "EXIT_NOW"
                ),
                "partial_count": sum(
                    1 for p in execution_order if p["sell_intent"] == "PARTIAL"
                ),
                "trail_count": sum(
                    1 for p in execution_order if p["sell_intent"] == "TRAIL"
                ),
            },
            "plan_time": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "ExitEngine batch_sell_plan: %d positions, %d warnings, buckets=%s",
            len(execution_order), len(warnings),
            {k: v["count"] for k, v in bucket_summary.items()},
        )

        return plan

    def _score_single_position(
        self,
        pos: dict,
        bars_batch: dict[str, pd.DataFrame],
        regime_tag: str,
    ) -> Optional[dict]:
        """
        Compute exit score for a single position.

        Returns a complete exit score dict or None if the ticker
        has no bar data and cannot be evaluated.
        """
        ticker = pos.get("ticker", "")
        direction = pos.get("direction", "LONG")
        entry_price = float(pos.get("entry_price", 0))
        current_price = float(pos.get("current_price", 0))
        entry_time = pos.get("entry_time")
        peak_r = float(pos.get("peak_r", 0))
        strategy = pos.get("strategy", "unknown")
        shares = pos.get("shares", 0)

        if entry_price <= 0 or current_price <= 0:
            return self._fallback_exit(pos, "Invalid entry or current price")

        # ----- Compute current R -----
        df = bars_batch.get(ticker)
        if df is not None and len(df) >= 15:
            atr = _compute_atr(df)
        else:
            # Estimate ATR from price range if no bars available
            atr = entry_price * 0.02  # 2% fallback estimate

        risk_per_share = atr if atr > 0 else entry_price * 0.02
        if direction == "LONG":
            current_r = (current_price - entry_price) / risk_per_share
        else:
            current_r = (entry_price - current_price) / risk_per_share
        current_r = round(current_r, 2)

        # ----- Compute time in trade -----
        time_in_trade_minutes = 0
        if entry_time:
            try:
                if isinstance(entry_time, str):
                    # Try ISO format
                    et = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                elif isinstance(entry_time, datetime):
                    et = entry_time
                else:
                    et = None

                if et is not None:
                    if et.tzinfo is None:
                        et = et.replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    time_in_trade_minutes = int((now - et).total_seconds() / 60)
            except (ValueError, TypeError):
                time_in_trade_minutes = 0

        # ----- RSI from bars -----
        rsi = 50.0
        if df is not None and len(df) >= _RSI_PERIOD + 1:
            rsi = _compute_rsi(df["Close"])

        # ----- RSI divergence -----
        has_divergence = False
        if df is not None:
            has_divergence = _detect_rsi_divergence(df, direction)

        # ----- Regime check -----
        regime_flipped = _is_regime_against(regime_tag, direction)

        # ----- Exit score computation (0-100) -----
        exit_score = 0
        kill_conditions: list[str] = []
        reasoning_parts: list[str] = []

        # 1. Regime flipped against position (+30)
        if regime_flipped:
            exit_score += _SCORE_REGIME_FLIP
            kill_conditions.append(f"REGIME_FLIP: {regime_tag} against {direction}")
            reasoning_parts.append(
                f"Regime {regime_tag} has flipped against {direction} direction (+{_SCORE_REGIME_FLIP})"
            )

        # 2. Losing position: current_r < -0.5 (+25)
        if current_r < _LOSING_R_THRESHOLD:
            exit_score += _SCORE_LOSING_POSITION
            kill_conditions.append(f"LOSING: current R = {current_r:.2f}")
            reasoning_parts.append(
                f"Position is losing with R = {current_r:.2f} below {_LOSING_R_THRESHOLD} threshold (+{_SCORE_LOSING_POSITION})"
            )

        # 3. Time timeout: > 240 min (+20)
        if time_in_trade_minutes > _INTRADAY_TIMEOUT_MINUTES:
            exit_score += _SCORE_TIME_TIMEOUT
            kill_conditions.append(
                f"TIME_EXCEEDED: {time_in_trade_minutes} min > {_INTRADAY_TIMEOUT_MINUTES} min"
            )
            reasoning_parts.append(
                f"Trade has been open {time_in_trade_minutes} min, exceeding "
                f"{_INTRADAY_TIMEOUT_MINUTES} min intraday limit (+{_SCORE_TIME_TIMEOUT})"
            )

        # 4. RSI divergence detected (+15)
        if has_divergence:
            exit_score += _SCORE_RSI_DIVERGENCE
            kill_conditions.append("RSI_DIVERGENCE: momentum diverging from price")
            reasoning_parts.append(
                f"RSI divergence detected against {direction} position (+{_SCORE_RSI_DIVERGENCE})"
            )

        # 5. At profit target: current_r > 2.0 (+10)
        if current_r > _PROFIT_TARGET_R:
            exit_score += _SCORE_PROFIT_TARGET
            kill_conditions.append(f"PROFIT_TARGET: R = {current_r:.2f} > {_PROFIT_TARGET_R}")
            reasoning_parts.append(
                f"At profit target with R = {current_r:.2f} exceeding {_PROFIT_TARGET_R}R (+{_SCORE_PROFIT_TARGET})"
            )

        exit_score = min(exit_score, 100)

        # ----- Sell intent -----
        if exit_score < _INTENT_HOLD_MAX:
            sell_intent = "HOLD"
        elif exit_score < _INTENT_TRAIL_MAX:
            sell_intent = "TRAIL"
        elif exit_score < _INTENT_PARTIAL_MAX:
            sell_intent = "PARTIAL"
        else:
            sell_intent = "EXIT_NOW"

        # ----- Reasoning summary -----
        if not reasoning_parts:
            reasoning_parts.append(
                f"Position is stable: R = {current_r:.2f}, "
                f"time = {time_in_trade_minutes} min, RSI = {rsi:.0f}, "
                f"regime = {regime_tag}"
            )

        reasoning = " | ".join(reasoning_parts)

        # ----- Liquidity bucket for batch planning -----
        avg_volume = 0.0
        if df is not None and len(df) >= 5:
            avg_volume = float(df["Volume"].iloc[-5:].mean())
        liquidity_bucket = _estimate_liquidity_bucket(avg_volume, current_price)

        return {
            "ticker": ticker,
            "direction": direction,
            "entry_price": entry_price,
            "current_price": current_price,
            "shares": shares,
            "strategy": strategy,
            "current_r": current_r,
            "peak_r": peak_r,
            "time_in_trade_minutes": time_in_trade_minutes,
            "rsi": rsi,
            "has_divergence": has_divergence,
            "regime_flipped": regime_flipped,
            "exit_score": exit_score,
            "sell_intent": sell_intent,
            "kill_conditions": kill_conditions,
            "reasoning": reasoning,
            "liquidity_bucket": liquidity_bucket,
            "scored_at": datetime.now(timezone.utc).isoformat(),
        }

    def _fallback_exit(self, pos: dict, reason: str) -> dict:
        """
        Generate a conservative EXIT_NOW result when a position cannot
        be properly evaluated.
        """
        logger.warning(
            "ExitEngine: fallback EXIT_NOW for %s — %s",
            pos.get("ticker", "UNKNOWN"), reason,
        )
        return {
            "ticker": pos.get("ticker", "UNKNOWN"),
            "direction": pos.get("direction", "LONG"),
            "entry_price": float(pos.get("entry_price", 0)),
            "current_price": float(pos.get("current_price", 0)),
            "shares": pos.get("shares", 0),
            "strategy": pos.get("strategy", "unknown"),
            "current_r": 0.0,
            "peak_r": float(pos.get("peak_r", 0)),
            "time_in_trade_minutes": 0,
            "rsi": 50.0,
            "has_divergence": False,
            "regime_flipped": False,
            "exit_score": 100,
            "sell_intent": "EXIT_NOW",
            "kill_conditions": [f"EVALUATION_FAILED: {reason}"],
            "reasoning": f"Cannot evaluate position — defaulting to EXIT_NOW for safety: {reason}",
            "liquidity_bucket": "LOW",
            "scored_at": datetime.now(timezone.utc).isoformat(),
        }
