"""
NZT-48 Opportunity Scanner
Scans universe for tickers capable of delivering +2% NET AFTER FEES intraday.
Objective: Find the best daily candidates where a 2% net move is feasible.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from execution.cost_model import SPREAD_BPS

logger = logging.getLogger("nzt48.opportunity_scanner")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_NET_TARGET_PCT = 2.0               # Net target after all costs
_MIN_ATR_PCT_THRESHOLD = 3.0        # Need >= 3% ATR for 2% net feasibility
_SLIPPAGE_BPS_PER_SIDE = 5.0        # Market impact, per side
_PLATFORM_FEE_BPS_PER_SIDE = 2.0    # Brokerage/platform fee per side
_DEFAULT_SPREAD_BPS = 20.0           # Fallback when ticker not in SPREAD_BPS

# Feasibility score weights (must sum to 1.0)
_W_ATR = 0.30           # ATR% relative to net target
_W_MOMENTUM = 0.20      # RSI alignment with direction
_W_RVOL = 0.15          # Relative volume
_W_REGIME = 0.15        # Regime fit
_W_EMA_ALIGN = 0.10     # EMA 9/20 stack
_W_LIQUIDITY = 0.10     # Volume * price proxy

# Decision thresholds
_MIN_FEASIBILITY_SCORE = 75
_MIN_EXPECTED_NET_R = 0.3

# RSI parameters
_RSI_PERIOD = 14
_EMA_FAST = 9
_EMA_SLOW = 20

# Regime mappings for scoring
_REGIME_SCORES = {
    "aligned": 0.9,
    "neutral": 0.6,
    "headwind": 0.3,
}


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


def _compute_ema(series: pd.Series, span: int) -> float:
    """Return the latest EMA value from a price series."""
    if len(series) < span:
        return float("nan")
    ema = series.ewm(span=span, adjust=False).mean()
    return float(ema.iloc[-1])


def _compute_rvol(volume: pd.Series, lookback: int = 20) -> float:
    """Relative volume: current volume / average volume over lookback."""
    if len(volume) < lookback + 1:
        return 1.0
    current = float(volume.iloc[-1])
    avg = float(volume.iloc[-(lookback + 1):-1].mean())
    if avg <= 0:
        return 1.0
    return round(current / avg, 2)


def _classify_regime_fit(regime_tag: str, direction: str) -> str:
    """
    Classify how well the current regime aligns with the trade direction.
    Returns 'aligned', 'neutral', or 'headwind'.
    """
    tag = regime_tag.upper() if regime_tag else "NEUTRAL"

    bullish_regimes = {"RISK_ON", "TRENDING", "BULL", "EXPANSION", "MOMENTUM"}
    bearish_regimes = {"RISK_OFF", "BEAR", "CONTRACTION", "SHOCK", "DEFENSIVE"}
    neutral_regimes = {"NEUTRAL", "RANGING", "CHOPPY", "UNKNOWN"}

    if direction == "LONG":
        if tag in bullish_regimes:
            return "aligned"
        elif tag in bearish_regimes:
            return "headwind"
        else:
            return "neutral"
    else:  # SHORT
        if tag in bearish_regimes:
            return "aligned"
        elif tag in bullish_regimes:
            return "headwind"
        else:
            return "neutral"


class OpportunityScanner:
    """
    Daily opportunity scanner for +2% NET AFTER FEES candidates.

    Scans the ISA universe to find tickers where a 2% net intraday move
    is feasible, scoring each by ATR coverage, momentum, volume, regime,
    EMA alignment, and liquidity.
    """

    def __init__(self) -> None:
        self._last_scan_results: list[dict] = []
        self._scan_time: Optional[str] = None

    def scan(
        self,
        bars_batch: dict[str, pd.DataFrame],
        regime_tag: str,
        cost_model: Optional[dict[str, float]] = None,
    ) -> list[dict]:
        """
        Scan all tickers in bars_batch for 2% net feasibility.

        Args:
            bars_batch: Dict mapping ticker -> DataFrame with Open/High/Low/Close/Volume columns.
            regime_tag: Current market regime string (e.g. 'RISK_ON', 'NEUTRAL', 'BEAR').
            cost_model: Optional override for spread_bps per ticker. If None, uses
                        SPREAD_BPS from execution.cost_model.

        Returns:
            List of candidate dicts sorted by feasibility_score descending.
            Each dict contains: ticker, direction, feasibility_score, decision,
            execution_plan, why, and all component scores.
        """
        results: list[dict] = []
        spread_lookup = cost_model if cost_model is not None else SPREAD_BPS

        for ticker, df in bars_batch.items():
            try:
                candidate = self._evaluate_ticker(ticker, df, regime_tag, spread_lookup)
                if candidate is not None:
                    results.append(candidate)
            except Exception as exc:
                logger.warning(
                    "OpportunityScanner: skipping %s due to error: %s",
                    ticker, exc,
                )
                continue

        # Sort by feasibility score descending
        results.sort(key=lambda c: c["feasibility_score"], reverse=True)

        self._last_scan_results = results
        self._scan_time = datetime.now(timezone.utc).isoformat()

        logger.info(
            "OpportunityScanner: scanned %d tickers, %d candidates, %d tradeable",
            len(bars_batch),
            len(results),
            sum(1 for r in results if r["decision"] == "TRADE"),
        )

        return results

    def get_top_candidates(self, n: int = 20) -> list[dict]:
        """
        Return the top N candidates from the most recent scan, sorted by feasibility_score.

        Args:
            n: Maximum number of candidates to return. Defaults to 20.

        Returns:
            List of candidate dicts (up to n), sorted by feasibility_score descending.
        """
        if not self._last_scan_results:
            logger.debug("OpportunityScanner.get_top_candidates: no scan results available")
            return []
        return self._last_scan_results[:n]

    def _evaluate_ticker(
        self,
        ticker: str,
        df: pd.DataFrame,
        regime_tag: str,
        spread_lookup: dict[str, float],
    ) -> Optional[dict]:
        """
        Evaluate a single ticker for 2% net feasibility.

        Returns None if the ticker has insufficient data or fails minimum ATR gate.
        Otherwise returns a full candidate dict.
        """
        # ----- Validate data -----
        required_cols = {"Open", "High", "Low", "Close", "Volume"}
        if not required_cols.issubset(set(df.columns)):
            logger.warning(
                "OpportunityScanner: %s missing required columns (has: %s)",
                ticker, list(df.columns),
            )
            return None

        if len(df) < 20:
            logger.debug("OpportunityScanner: %s has only %d bars, need >= 20", ticker, len(df))
            return None

        closes = df["Close"]
        current_price = float(closes.iloc[-1])
        if current_price <= 0:
            return None

        # ----- Cost computation -----
        spread_bps = spread_lookup.get(ticker, _DEFAULT_SPREAD_BPS)
        round_trip_cost_pct = (
            spread_bps + 2 * _SLIPPAGE_BPS_PER_SIDE + 2 * _PLATFORM_FEE_BPS_PER_SIDE
        ) / 100.0
        net_target_pct = _NET_TARGET_PCT + round_trip_cost_pct

        # ----- ATR computation -----
        atr = _compute_atr(df)
        if atr <= 0:
            return None
        atr_pct = (atr / current_price) * 100.0

        # Gate: need >= 3% ATR for 2% net feasibility
        if atr_pct < _MIN_ATR_PCT_THRESHOLD:
            return None

        # ----- Indicator computations -----
        rsi = _compute_rsi(closes)
        rvol = _compute_rvol(df["Volume"])
        ema9 = _compute_ema(closes, _EMA_FAST)
        ema20 = _compute_ema(closes, _EMA_SLOW)
        current_volume = float(df["Volume"].iloc[-1])

        # ----- Determine direction -----
        long_signals = 0
        short_signals = 0

        # RSI
        if rsi > 50:
            long_signals += 1
        elif rsi < 50:
            short_signals += 1

        # EMA alignment
        if not np.isnan(ema9) and not np.isnan(ema20):
            if ema9 > ema20:
                long_signals += 1
            elif ema9 < ema20:
                short_signals += 1

        # Price vs EMA9
        if not np.isnan(ema9):
            if current_price > ema9:
                long_signals += 1
            elif current_price < ema9:
                short_signals += 1

        # Price vs EMA20
        if not np.isnan(ema20):
            if current_price > ema20:
                long_signals += 1
            elif current_price < ema20:
                short_signals += 1

        if long_signals >= short_signals:
            direction = "LONG"
        else:
            direction = "SHORT"

        # ----- Feasibility score components (0-1 each) -----

        # 1. ATR% relative to net target (30% weight)
        #    Capped at 1.0 when ATR% >= 2 * net_target_pct
        atr_ratio = atr_pct / (2.0 * net_target_pct) if net_target_pct > 0 else 0
        atr_component = min(atr_ratio, 1.0)

        # 2. Momentum alignment (20% weight)
        #    RSI > 50 for LONG, < 50 for SHORT
        if direction == "LONG":
            # 50-100 mapped to 0-1
            momentum_component = max(0.0, min((rsi - 50.0) / 50.0, 1.0))
        else:
            # 0-50 mapped to 1-0
            momentum_component = max(0.0, min((50.0 - rsi) / 50.0, 1.0))

        # 3. RVOL (15% weight) — higher relative volume = more feasible
        rvol_component = min(rvol / 3.0, 1.0)

        # 4. Regime fit (15% weight)
        regime_fit = _classify_regime_fit(regime_tag, direction)
        regime_component = _REGIME_SCORES.get(regime_fit, 0.6)

        # 5. EMA alignment (10% weight) — 9/20 stack in correct direction
        if np.isnan(ema9) or np.isnan(ema20):
            ema_component = 0.5  # Neutral when unavailable
        else:
            ema_gap_pct = abs(ema9 - ema20) / current_price * 100
            ema_direction_correct = (
                (direction == "LONG" and ema9 > ema20) or
                (direction == "SHORT" and ema9 < ema20)
            )
            if ema_direction_correct:
                ema_component = min(ema_gap_pct / 2.0, 1.0)  # Wider gap = stronger
            else:
                ema_component = 0.0  # Wrong direction

        # 6. Liquidity proxy (10% weight) — volume * price
        liquidity_proxy = current_volume * current_price
        # Normalize: assume 10M notional is "very liquid" for LSE leveraged ETPs
        liquidity_component = min(liquidity_proxy / 10_000_000.0, 1.0)

        # ----- Weighted feasibility score (0-100) -----
        raw_score = (
            atr_component * _W_ATR
            + momentum_component * _W_MOMENTUM
            + rvol_component * _W_RVOL
            + regime_component * _W_REGIME
            + ema_component * _W_EMA_ALIGN
            + liquidity_component * _W_LIQUIDITY
        )
        feasibility_score = round(raw_score * 100, 1)

        # ----- Expected net R approximation -----
        frac = feasibility_score / 100.0
        expected_net_r = round(frac * 1.5 - (1.0 - frac) * 1.0, 3)

        # ----- Decision -----
        if feasibility_score >= _MIN_FEASIBILITY_SCORE and expected_net_r > _MIN_EXPECTED_NET_R:
            decision = "TRADE"
        else:
            decision = "WATCH"

        # ----- Execution plan -----
        spread_gate = "PASS"
        if spread_bps > 32:
            spread_gate = "VETO"
        elif spread_bps > 22:
            spread_gate = "WATCH"

        execution_plan = {
            "order_type": "LIMIT" if rvol < 1.5 else "MARKETABLE_LIMIT",
            "max_slippage_bps": round(spread_bps / 2 + _SLIPPAGE_BPS_PER_SIDE, 1),
            "spread_gate": spread_gate,
            "time_limit_minutes": 15 if rvol >= 1.5 else 30,
            "round_trip_cost_pct": round(round_trip_cost_pct, 4),
            "net_target_pct": round(net_target_pct, 4),
        }

        # Override decision if spread is vetoed
        if spread_gate == "VETO":
            decision = "WATCH"

        # ----- Why string -----
        why_parts = []
        why_parts.append(
            f"ATR={atr_pct:.1f}% vs {net_target_pct:.2f}% net target "
            f"({atr_pct/net_target_pct:.1f}x coverage)"
        )
        why_parts.append(f"RSI={rsi:.0f} favours {direction}")
        why_parts.append(f"RVOL={rvol:.1f}x {'strong' if rvol >= 1.5 else 'moderate'} volume")
        why_parts.append(f"Regime={regime_tag} ({regime_fit} for {direction})")
        why_parts.append(
            f"EMA9/20 {'aligned' if ema_component > 0.3 else 'misaligned'} for {direction}"
        )
        why_parts.append(f"Spread={spread_bps:.0f}bps, RT cost={round_trip_cost_pct:.2f}%")
        why_parts.append(f"Score={feasibility_score:.0f}/100, E[net R]={expected_net_r:.2f}")

        if decision == "TRADE":
            why_parts.append("TRADEABLE: score >= 75 and expected net R > 0.3")
        else:
            reasons = []
            if feasibility_score < _MIN_FEASIBILITY_SCORE:
                reasons.append(f"score {feasibility_score:.0f} < {_MIN_FEASIBILITY_SCORE}")
            if expected_net_r <= _MIN_EXPECTED_NET_R:
                reasons.append(f"E[net R] {expected_net_r:.2f} <= {_MIN_EXPECTED_NET_R}")
            if spread_gate == "VETO":
                reasons.append(f"spread vetoed at {spread_bps:.0f}bps")
            why_parts.append(f"WATCH ONLY: {', '.join(reasons)}")

        why = " | ".join(why_parts)

        return {
            "ticker": ticker,
            "direction": direction,
            "current_price": round(current_price, 4),
            "feasibility_score": feasibility_score,
            "expected_net_r": expected_net_r,
            "decision": decision,
            "execution_plan": execution_plan,
            "why": why,
            "scan_time": datetime.now(timezone.utc).isoformat(),
            # Component detail
            "components": {
                "atr_pct": round(atr_pct, 2),
                "atr_component": round(atr_component, 3),
                "rsi": rsi,
                "momentum_component": round(momentum_component, 3),
                "rvol": rvol,
                "rvol_component": round(rvol_component, 3),
                "regime_fit": regime_fit,
                "regime_component": regime_component,
                "ema9": round(ema9, 4) if not np.isnan(ema9) else None,
                "ema20": round(ema20, 4) if not np.isnan(ema20) else None,
                "ema_component": round(ema_component, 3),
                "liquidity_proxy": round(liquidity_proxy, 0),
                "liquidity_component": round(liquidity_component, 3),
            },
            # Cost detail
            "costs": {
                "spread_bps": spread_bps,
                "slippage_bps_per_side": _SLIPPAGE_BPS_PER_SIDE,
                "platform_fee_bps_per_side": _PLATFORM_FEE_BPS_PER_SIDE,
                "round_trip_cost_pct": round(round_trip_cost_pct, 4),
                "net_target_pct": round(net_target_pct, 4),
            },
        }
