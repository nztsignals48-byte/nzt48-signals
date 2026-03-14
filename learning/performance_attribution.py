"""
NZT-48 Performance Attribution Engine
Decomposes every trade and portfolio period into actionable components
so the system can learn WHAT worked and WHY, not just whether it won or lost.

Attribution dimensions:
1. Entry Quality — how close to optimal entry price
2. Exit Quality — how much of MFE was captured
3. Timing — holding period vs optimal for the strategy type
4. Market Context — regime alignment and VIX impact
5. Sizing — position size appropriateness for outcome
6. Strategy Selection — was this the right strategy for conditions

Each dimension scores -100 to +100. Composite weighted grade A+ through F.
Rolling analytics surface the weakest component for targeted improvement.
"""
from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.performance_attribution")

# ── Strategy category mappings ──────────────────────────────────────────
MOMENTUM_STRATEGIES = {"S1", "S2", "S11", "S13"}
MEAN_REVERSION_STRATEGIES = {"S3"}
SWING_STRATEGIES = {"S4", "S5", "S6", "S7"}

# Optimal holding time ranges in minutes
OPTIMAL_HOLD_MINUTES = {
    "momentum": (30, 120),
    "mean_reversion": (15, 60),
    "swing": (120, 480),
}

# Attribution component weights for composite grade
COMPONENT_WEIGHTS = {
    "entry_quality": 0.25,
    "exit_quality": 0.25,
    "timing": 0.15,
    "market_context": 0.15,
    "sizing": 0.10,
    "strategy_selection": 0.10,
}

GRADE_THRESHOLDS = [
    (80, "A+"),
    (60, "A"),
    (40, "B"),
    (20, "C"),
    (0, "D"),
    (-float("inf"), "F"),
]

UP_REGIMES = {
    "TRENDING_UP", "TRENDING_UP_STRONG", "TRENDING_UP_MOD",
    "BULLISH", "STRONG_BULLISH",
}
DOWN_REGIMES = {
    "TRENDING_DOWN", "TRENDING_DOWN_STRONG", "TRENDING_DOWN_MOD",
    "BEARISH", "STRONG_BEARISH",
}
NEUTRAL_REGIMES = {
    "RANGE_BOUND", "CHOPPY", "LOW_VOL", "NEUTRAL",
}


def _clamp(value: float, lo: float = -100.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _strategy_category(strategy: str) -> str:
    """Map a strategy code to its category."""
    s = strategy.upper().strip()
    if s in MOMENTUM_STRATEGIES:
        return "momentum"
    if s in MEAN_REVERSION_STRATEGIES:
        return "mean_reversion"
    if s in SWING_STRATEGIES:
        return "swing"
    # Default to momentum if unknown
    return "momentum"


def _safe_get(d: dict, key: str, default: Any = 0) -> Any:
    """Safely get a value from a dict, returning default for None."""
    val = d.get(key, default)
    return val if val is not None else default


class PerformanceAttributionEngine:
    """Decomposes trade-level and portfolio-level performance into
    independent, actionable attribution components.

    Usage:
        engine = PerformanceAttributionEngine()
        attr = engine.attribute_trade(trade_dict)
        grade, suggestions = engine.grade_trade(attr)
        rolling = engine.get_rolling_attribution(all_trades, window=50)
    """

    def __init__(self, strategy_regime_stats: dict[str, dict[str, dict]] | None = None):
        """
        Args:
            strategy_regime_stats: Optional pre-loaded stats of shape
                {strategy: {regime: {"win_rate": float, "trades": int, "avg_r": float}}}
                Used for strategy selection scoring. If not provided, strategy
                selection scoring uses simplified heuristics.
        """
        self._strategy_regime_stats = strategy_regime_stats or {}
        self._attribution_history: list[dict] = []

    # ════════════════════════════════════════════════════════════════════
    #  1. TRADE-LEVEL ATTRIBUTION
    # ════════════════════════════════════════════════════════════════════

    def attribute_trade(self, trade: dict) -> dict:
        """Decompose a single trade into 6 attribution scores.

        Args:
            trade: dict with keys:
                ticker, direction, strategy, entry_price, exit_price,
                entry_time, exit_time, shares, r_multiple, risk_dollars,
                regime_at_entry, regime_at_exit, confidence, mae_r, mfe_r,
                indicator_snapshot_entry (dict), indicator_snapshot_exit (dict)

                Optional: session_high, session_low, equity, position_risk_pct,
                          other_strategies_available (list[str])

        Returns:
            dict with keys: ticker, direction, strategy, r_multiple,
            entry_quality, exit_quality, timing, market_context, sizing,
            strategy_selection, composite_score, grade, suggestions
        """
        entry_score = self._score_entry_quality(trade)
        exit_score = self._score_exit_quality(trade)
        timing_score = self._score_timing(trade)
        market_score = self._score_market_context(trade)
        sizing_score = self._score_sizing(trade)
        strategy_score = self._score_strategy_selection(trade)

        composite = (
            entry_score * COMPONENT_WEIGHTS["entry_quality"]
            + exit_score * COMPONENT_WEIGHTS["exit_quality"]
            + timing_score * COMPONENT_WEIGHTS["timing"]
            + market_score * COMPONENT_WEIGHTS["market_context"]
            + sizing_score * COMPONENT_WEIGHTS["sizing"]
            + strategy_score * COMPONENT_WEIGHTS["strategy_selection"]
        )

        grade_letter = self._letter_grade(composite)

        attribution = {
            "ticker": _safe_get(trade, "ticker", ""),
            "direction": _safe_get(trade, "direction", ""),
            "strategy": _safe_get(trade, "strategy", ""),
            "r_multiple": _safe_get(trade, "r_multiple", 0.0),
            "entry_quality": round(entry_score, 1),
            "exit_quality": round(exit_score, 1),
            "timing": round(timing_score, 1),
            "market_context": round(market_score, 1),
            "sizing": round(sizing_score, 1),
            "strategy_selection": round(strategy_score, 1),
            "composite_score": round(composite, 1),
            "grade": grade_letter,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self._attribution_history.append(attribution)

        logger.info(
            "ATTRIBUTION %s %s %s R=%.2f | E=%+.0f X=%+.0f T=%+.0f M=%+.0f Sz=%+.0f St=%+.0f | %s (%.1f)",
            attribution["direction"], attribution["ticker"], attribution["strategy"],
            attribution["r_multiple"],
            entry_score, exit_score, timing_score, market_score,
            sizing_score, strategy_score,
            grade_letter, composite,
        )

        return attribution

    # ── Entry Quality ───────────────────────────────────────────────────

    def _score_entry_quality(self, trade: dict) -> float:
        """Score entry quality from -100 to +100.

        Uses session range positioning and MAE (max adverse excursion).
        """
        score = 0.0
        components = 0

        # (a) Session range positioning
        session_high = _safe_get(trade, "session_high", 0)
        session_low = _safe_get(trade, "session_low", 0)
        entry_price = _safe_get(trade, "entry_price", 0)
        direction = _safe_get(trade, "direction", "LONG").upper()

        session_range = session_high - session_low
        if session_range > 0 and entry_price > 0:
            if direction == "LONG":
                # For longs: closer to session low = better entry
                position_pct = (session_high - entry_price) / session_range
                range_score = -100 + 200 * position_pct  # 0 at high, +100 at low
            else:
                # For shorts: closer to session high = better entry
                position_pct = (entry_price - session_low) / session_range
                range_score = -100 + 200 * position_pct  # 0 at low, +100 at high
            score += range_score
            components += 1

        # (b) MAE-based scoring
        mae_r = abs(_safe_get(trade, "mae_r", 0.5))
        if mae_r < 0.3:
            mae_score = 50 + (0.3 - mae_r) / 0.3 * 50  # 50 to 100
        elif mae_r < 0.5:
            mae_score = 20 + (0.5 - mae_r) / 0.2 * 30  # 20 to 50
        elif mae_r < 0.8:
            mae_score = -20 + (0.8 - mae_r) / 0.3 * 40  # -20 to 20
        elif mae_r < 1.5:
            mae_score = -70 + (1.5 - mae_r) / 0.7 * 50  # -70 to -20
        else:
            mae_score = -100 + (2.0 - min(mae_r, 2.0)) / 0.5 * 30  # -100 to -70

        score += mae_score
        components += 1

        return _clamp(score / max(components, 1))

    # ── Exit Quality ────────────────────────────────────────────────────

    def _score_exit_quality(self, trade: dict) -> float:
        """Score exit quality from -100 to +100.

        Based on exit efficiency (r_multiple / mfe_r).
        """
        r_multiple = _safe_get(trade, "r_multiple", 0.0)
        mfe_r = _safe_get(trade, "mfe_r", 0.0)

        # If MFE is zero or negative, trade never went in our favour
        if mfe_r <= 0:
            # Loser that never worked — exit is neutral (stop did its job)
            if r_multiple <= 0:
                return 0.0
            return 0.0

        exit_efficiency = r_multiple / mfe_r if mfe_r > 0 else 0.0

        # Stopped out at exactly stop level — neutral
        if r_multiple <= -0.95 and mfe_r < 0.2:
            return 0.0

        # Scored by efficiency bands
        if exit_efficiency >= 0.8:
            score = 60 + (exit_efficiency - 0.8) / 0.2 * 40  # 60 to 100
        elif exit_efficiency >= 0.6:
            score = 30 + (exit_efficiency - 0.6) / 0.2 * 30  # 30 to 60
        elif exit_efficiency >= 0.4:
            score = 0 + (exit_efficiency - 0.4) / 0.2 * 30  # 0 to 30
        elif exit_efficiency >= 0.2:
            score = -40 + (exit_efficiency - 0.2) / 0.2 * 40  # -40 to 0
        else:
            score = -80 + exit_efficiency / 0.2 * 40  # -80 to -40

        # Extra penalty for leaving large MFE on the table
        if exit_efficiency < 0.3 and mfe_r > 1.0:
            left_on_table = mfe_r - r_multiple
            penalty = min(30, left_on_table * 15)
            score -= penalty

        # Bonus: captured profit on a trade that went significantly in favour
        if exit_efficiency >= 0.7 and mfe_r >= 2.0:
            score = min(100, score + 10)

        return _clamp(score)

    # ── Timing Score ────────────────────────────────────────────────────

    def _score_timing(self, trade: dict) -> float:
        """Score holding time vs optimal for the strategy type."""
        strategy = _safe_get(trade, "strategy", "")
        entry_time = _safe_get(trade, "entry_time", "")
        exit_time = _safe_get(trade, "exit_time", "")

        # Calculate holding time in minutes
        hold_minutes = _safe_get(trade, "duration_minutes", 0)
        if hold_minutes <= 0 and entry_time and exit_time:
            try:
                if isinstance(entry_time, str):
                    et = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                else:
                    et = entry_time
                if isinstance(exit_time, str):
                    xt = datetime.fromisoformat(exit_time.replace("Z", "+00:00"))
                else:
                    xt = exit_time
                hold_minutes = (xt - et).total_seconds() / 60
            except (ValueError, TypeError):
                hold_minutes = 60  # Fallback

        if hold_minutes <= 0:
            return 0.0

        # Get optimal range for strategy category
        category = _strategy_category(strategy)
        opt_min, opt_max = OPTIMAL_HOLD_MINUTES.get(category, (30, 120))

        # Within optimal range
        if opt_min <= hold_minutes <= opt_max:
            # Center of range is best
            range_mid = (opt_min + opt_max) / 2
            distance_from_mid = abs(hold_minutes - range_mid) / (opt_max - opt_min) * 2
            score = 80 - distance_from_mid * 30  # 50 to 80
            return _clamp(score)

        # Below optimal range
        if hold_minutes < opt_min:
            ratio = hold_minutes / opt_min
            if ratio < 0.25:
                return _clamp(-30 - (0.25 - ratio) / 0.25 * 40)  # -70 to -30
            return _clamp(-30 + (ratio - 0.25) / 0.75 * 80)  # -30 to 50

        # Above optimal range
        ratio = hold_minutes / opt_max
        if ratio >= 3.0:
            return _clamp(-50 - min((ratio - 3.0) / 3.0 * 30, 30))  # -50 to -80
        elif ratio >= 2.0:
            return _clamp(-20 - (ratio - 2.0) * 30)  # -20 to -50
        else:
            return _clamp(50 - (ratio - 1.0) * 70)  # 50 to -20

    # ── Market Context Score ────────────────────────────────────────────

    def _score_market_context(self, trade: dict) -> float:
        """Score whether the trade was aligned with market regime."""
        direction = _safe_get(trade, "direction", "LONG").upper()
        regime_entry = _safe_get(trade, "regime_at_entry", "").upper()
        regime_exit = _safe_get(trade, "regime_at_exit", "").upper()
        indicator_entry = _safe_get(trade, "indicator_snapshot_entry", {}) or {}
        indicator_exit = _safe_get(trade, "indicator_snapshot_exit", {}) or {}

        score = 0.0

        # (a) Regime alignment baseline
        if direction == "LONG":
            if regime_entry in UP_REGIMES:
                score += 50
            elif regime_entry in DOWN_REGIMES:
                score -= 50
            elif regime_entry in NEUTRAL_REGIMES:
                score += 0
        elif direction == "SHORT":
            if regime_entry in DOWN_REGIMES:
                score += 50
            elif regime_entry in UP_REGIMES:
                score -= 50
            elif regime_entry in NEUTRAL_REGIMES:
                score += 0

        # (b) Regime change penalty
        if regime_exit and regime_entry and regime_exit != regime_entry:
            score -= 20

        # (c) VIX impact
        vix_entry = indicator_entry.get("vix", 0) or 0
        vix_exit = indicator_exit.get("vix", 0) or 0
        if vix_entry > 0 and vix_exit > 0:
            vix_change = vix_exit - vix_entry
            if direction == "LONG" and vix_change > 0:
                # Rising VIX hurts longs
                penalty = min(30, vix_change * 5)
                score -= penalty
            elif direction == "SHORT" and vix_change < 0:
                # Falling VIX hurts shorts
                penalty = min(30, abs(vix_change) * 5)
                score -= penalty
            elif direction == "LONG" and vix_change < 0:
                # Falling VIX helps longs
                bonus = min(15, abs(vix_change) * 3)
                score += bonus
            elif direction == "SHORT" and vix_change > 0:
                # Rising VIX helps shorts
                bonus = min(15, vix_change * 3)
                score += bonus

        return _clamp(score)

    # ── Sizing Score ────────────────────────────────────────────────────

    def _score_sizing(self, trade: dict) -> float:
        """Score whether position sizing was appropriate for the outcome."""
        r_multiple = _safe_get(trade, "r_multiple", 0.0)
        risk_dollars = _safe_get(trade, "risk_dollars", 0)
        equity = _safe_get(trade, "equity", 0)
        position_risk_pct = _safe_get(trade, "position_risk_pct", 0.0)

        # Calculate position risk % if not provided
        if position_risk_pct <= 0 and equity > 0 and risk_dollars > 0:
            position_risk_pct = (risk_dollars / equity) * 100

        score = 0.0

        # If we have no sizing data, return neutral
        if position_risk_pct <= 0:
            return 0.0

        is_winner = r_multiple > 0
        is_big_winner = r_multiple > 1.0
        is_big_loser = r_multiple < -1.0

        # Undersized on a winner — missed opportunity
        if is_big_winner and position_risk_pct < 0.3:
            score = -30  # Too conservative on a clear winner
        elif is_big_winner and position_risk_pct < 0.5:
            score = -10  # Slightly undersized

        # Oversized on a loser — excessive damage
        elif is_big_loser and position_risk_pct > 0.6:
            score = -50  # Too aggressive, took a big hit
        elif r_multiple < 0 and position_risk_pct > 0.6:
            score = -30  # Oversized on any loser

        # Well-sized: risk was 0.4-0.6% and outcome was reasonable
        elif 0.4 <= position_risk_pct <= 0.6:
            if is_winner:
                score = 40  # Standard risk, won
            else:
                score = 10  # Standard risk, lost — acceptable

        # Conservative sizing with a win
        elif position_risk_pct < 0.5 and is_winner:
            score = -10  # Could have risked more

        # Aggressive sizing with a win
        elif position_risk_pct > 0.6 and is_big_winner:
            score = 20  # Aggressive but it paid off — mild positive

        # Default: neutral-ish
        else:
            score = 0

        return _clamp(score)

    # ── Strategy Selection Score ────────────────────────────────────────

    def _score_strategy_selection(self, trade: dict) -> float:
        """Score whether this was the right strategy for current conditions."""
        strategy = _safe_get(trade, "strategy", "")
        regime = _safe_get(trade, "regime_at_entry", "").upper()
        r_multiple = _safe_get(trade, "r_multiple", 0.0)

        score = 0.0

        # (a) Check historical strategy performance in this regime
        regime_stats = self._strategy_regime_stats.get(strategy, {}).get(regime, {})
        hist_wr = regime_stats.get("win_rate", 0)
        hist_trades = regime_stats.get("trades", 0)

        if hist_trades >= 10:
            if hist_wr > 60:
                score += 40
            elif hist_wr > 50:
                score += 20
            elif hist_wr < 40:
                score -= 40
            elif hist_wr < 50:
                score -= 20

        # (b) Strategy-regime logic heuristics (when no historical data)
        if hist_trades < 10:
            category = _strategy_category(strategy)

            if category == "momentum" and regime in UP_REGIMES:
                score += 30  # Momentum works in trends
            elif category == "momentum" and regime in NEUTRAL_REGIMES:
                score -= 20  # Momentum struggles in chop
            elif category == "mean_reversion" and regime in NEUTRAL_REGIMES:
                score += 30  # Mean reversion loves ranges
            elif category == "mean_reversion" and regime in UP_REGIMES:
                score -= 10  # Mean reversion fights trends
            elif category == "swing" and regime in DOWN_REGIMES:
                score -= 20  # Swing longs in downtrends are risky

        # (c) Check if better strategy was available
        other_strategies = _safe_get(trade, "other_strategies_available", [])
        if other_strategies and isinstance(other_strategies, list):
            best_other_wr = 0
            for other_strat in other_strategies:
                other_stats = self._strategy_regime_stats.get(other_strat, {}).get(regime, {})
                other_wr = other_stats.get("win_rate", 0)
                other_count = other_stats.get("trades", 0)
                if other_count >= 10 and other_wr > best_other_wr:
                    best_other_wr = other_wr

            if best_other_wr > hist_wr + 15:
                score -= 20  # A clearly better strategy was available

        # (d) Outcome adjustment — mild. This prevents pure hindsight bias
        # but gives slight credit/debit for actual result alignment
        if r_multiple > 1.0:
            score += 10  # Strategy worked well
        elif r_multiple < -0.5:
            score -= 10  # Strategy didn't deliver

        return _clamp(score)

    # ════════════════════════════════════════════════════════════════════
    #  2. COMPOSITE GRADE
    # ════════════════════════════════════════════════════════════════════

    @staticmethod
    def _letter_grade(composite: float) -> str:
        for threshold, letter in GRADE_THRESHOLDS:
            if composite > threshold:
                return letter
        return "F"

    def grade_trade(self, attribution: dict) -> tuple[str, list[str]]:
        """Grade a trade and generate specific improvement suggestions.

        Args:
            attribution: dict returned by attribute_trade()

        Returns:
            (grade_letter, list_of_suggestions)
        """
        grade = attribution.get("grade", "F")
        suggestions = []

        entry = attribution.get("entry_quality", 0)
        exit_q = attribution.get("exit_quality", 0)
        timing = attribution.get("timing", 0)
        market = attribution.get("market_context", 0)
        sizing = attribution.get("sizing", 0)
        strat = attribution.get("strategy_selection", 0)
        r_mult = attribution.get("r_multiple", 0)
        mfe_approx = max(r_mult, 0)  # Approximate MFE from R if not available

        # Entry suggestions
        if entry < -30:
            suggestions.append(
                f"Entry was poor (score {entry:+.0f}). "
                "Consider waiting for a pullback to VWAP or key support before entering, "
                "or use limit orders near session lows instead of market orders."
            )
        elif entry < 0:
            suggestions.append(
                f"Entry was below average (score {entry:+.0f}). "
                "Review whether entry could have been timed closer to a support level."
            )

        # Exit suggestions
        if exit_q < -30:
            r_left = mfe_approx - r_mult if mfe_approx > r_mult else 0
            suggestions.append(
                f"Exit was poor (score {exit_q:+.0f}). "
                f"Left approximately {r_left:.1f}R on the table. "
                "Consider implementing trailing stops or partial exits at +1R."
            )
        elif exit_q < 0 and mfe_approx > 1.0:
            suggestions.append(
                f"Exit left profit on the table (score {exit_q:+.0f}). "
                "Consider a trailing stop at 0.8R instead of a fixed target."
            )

        # Timing suggestions
        if timing < -30:
            suggestions.append(
                f"Timing was significantly off (score {timing:+.0f}). "
                "Holding period deviated from strategy optimal range. "
                "Review if you overstayed or bailed too early."
            )

        # Market context suggestions
        if market < -30:
            suggestions.append(
                f"Traded against the market context (score {market:+.0f}). "
                "Check regime before entering — avoid fighting the tape in strong trends."
            )

        # Sizing suggestions
        if sizing < -20:
            if r_mult > 0:
                suggestions.append(
                    f"Position was undersized on this winner (score {sizing:+.0f}). "
                    "With a clear setup, consider standard risk allocation (0.5% equity)."
                )
            else:
                suggestions.append(
                    f"Position was oversized on this loser (score {sizing:+.0f}). "
                    "Reduce risk per trade when conviction is lower."
                )

        # Strategy suggestions
        if strat < -20:
            suggestions.append(
                f"Strategy selection was suboptimal (score {strat:+.0f}). "
                "Consider whether a different strategy type was better suited to the regime."
            )

        # Positive feedback for strong areas
        best_component = max(
            [("Entry", entry), ("Exit", exit_q), ("Timing", timing),
             ("Market", market), ("Sizing", sizing), ("Strategy", strat)],
            key=lambda x: x[1],
        )
        if best_component[1] > 50:
            suggestions.append(
                f"{best_component[0]} quality was excellent ({best_component[1]:+.0f}). "
                "Keep executing this aspect consistently."
            )

        if not suggestions:
            suggestions.append("All components were average. No major issues or strengths detected.")

        return grade, suggestions

    # ════════════════════════════════════════════════════════════════════
    #  3. ROLLING PERFORMANCE ANALYTICS
    # ════════════════════════════════════════════════════════════════════

    def get_rolling_attribution(self, trades: list[dict], window: int = 50) -> dict:
        """Compute rolling average of each attribution component.

        Args:
            trades: list of trade dicts (will be attributed if not already)
            window: number of recent trades to consider

        Returns:
            dict with avg scores per component, weakest component, and insight
        """
        # Attribute trades if needed
        attributions = []
        for t in trades[-window:]:
            if "entry_quality" in t and "exit_quality" in t:
                attributions.append(t)
            else:
                attributions.append(self.attribute_trade(t))

        if not attributions:
            return {
                "window": window,
                "trade_count": 0,
                "averages": {},
                "weakest_component": None,
                "strongest_component": None,
                "insight": "No trades to analyze.",
            }

        n = len(attributions)
        components = ["entry_quality", "exit_quality", "timing",
                      "market_context", "sizing", "strategy_selection"]

        averages = {}
        for comp in components:
            values = [a.get(comp, 0) for a in attributions]
            averages[comp] = round(sum(values) / n, 1)

        composite_values = [a.get("composite_score", 0) for a in attributions]
        avg_composite = round(sum(composite_values) / n, 1)

        # Grade distribution
        grade_counts = defaultdict(int)
        for a in attributions:
            grade_counts[a.get("grade", "F")] += 1

        # Identify weakest and strongest
        weakest = min(averages.items(), key=lambda x: x[1])
        strongest = max(averages.items(), key=lambda x: x[1])

        # R-multiple stats
        r_values = [a.get("r_multiple", 0) for a in attributions]
        avg_r = sum(r_values) / n if n > 0 else 0
        winners = [r for r in r_values if r > 0]
        losers = [r for r in r_values if r < 0]
        win_rate = len(winners) / n * 100 if n > 0 else 0

        # Generate actionable insight
        insight = self._generate_rolling_insight(
            averages, weakest, strongest, n, win_rate, avg_r, attributions,
        )

        return {
            "window": window,
            "trade_count": n,
            "averages": averages,
            "avg_composite": avg_composite,
            "avg_grade": self._letter_grade(avg_composite),
            "grade_distribution": dict(grade_counts),
            "weakest_component": {"name": weakest[0], "score": weakest[1]},
            "strongest_component": {"name": strongest[0], "score": strongest[1]},
            "avg_r": round(avg_r, 3),
            "win_rate": round(win_rate, 1),
            "insight": insight,
        }

    def _generate_rolling_insight(
        self, averages: dict, weakest: tuple, strongest: tuple,
        n: int, win_rate: float, avg_r: float, attributions: list[dict],
    ) -> str:
        """Generate a human-readable insight from rolling attribution data."""
        weak_name, weak_score = weakest
        strong_name, strong_score = strongest
        friendly_names = {
            "entry_quality": "Entry Quality",
            "exit_quality": "Exit Quality",
            "timing": "Timing",
            "market_context": "Market Context",
            "sizing": "Position Sizing",
            "strategy_selection": "Strategy Selection",
        }

        parts = []

        # Lead with the biggest leak
        parts.append(
            f"Your biggest leak is {friendly_names.get(weak_name, weak_name)} "
            f"(avg {weak_score:+.0f})."
        )

        # Add specific detail based on which component is weakest
        if weak_name == "exit_quality":
            # Calculate average exit efficiency
            r_vals = [a.get("r_multiple", 0) for a in attributions]
            mfe_est = max(max(r_vals) if r_vals else 0, 1)
            avg_capture = avg_r / mfe_est * 100 if mfe_est > 0 else 0
            parts.append(
                f"Over the last {n} trades, you captured approximately "
                f"{max(0, avg_capture):.0f}% of MFE. "
                "Consider implementing trailing stops or partial exits at +1R."
            )
        elif weak_name == "entry_quality":
            parts.append(
                f"Over the last {n} trades, entries are consistently poorly timed. "
                "Consider using limit orders at support/resistance or waiting for pullbacks."
            )
        elif weak_name == "timing":
            parts.append(
                f"Over the last {n} trades, holding periods are misaligned with strategy type. "
                "Review whether you are holding momentum trades too long or cutting swing trades short."
            )
        elif weak_name == "market_context":
            parts.append(
                f"Over the last {n} trades, many trades fought the prevailing regime. "
                "Add a regime filter to suppress signals that conflict with the market trend."
            )
        elif weak_name == "sizing":
            parts.append(
                f"Over the last {n} trades, position sizing consistently hurt performance. "
                "Review whether you are too conservative on high-conviction setups or too aggressive on weak ones."
            )
        elif weak_name == "strategy_selection":
            parts.append(
                f"Over the last {n} trades, strategy choices are not matching market conditions. "
                "Use the regime-strategy matrix to favour strategies with proven edge in the current regime."
            )

        # Acknowledge strongest area
        if strong_score > 30:
            parts.append(
                f"Strongest area: {friendly_names.get(strong_name, strong_name)} "
                f"(avg {strong_score:+.0f}). Keep this up."
            )

        return " ".join(parts)

    # ════════════════════════════════════════════════════════════════════
    #  4. STRATEGY-LEVEL ATTRIBUTION
    # ════════════════════════════════════════════════════════════════════

    def get_strategy_attribution(self, trades: list[dict], strategy: str) -> dict:
        """Filter trades by strategy and compute average attribution.

        Args:
            trades: list of trade dicts
            strategy: strategy code to filter by (e.g. "S1", "S3")

        Returns:
            dict with strategy-specific averages and comparison to system average
        """
        strat_trades = [t for t in trades if _safe_get(t, "strategy", "") == strategy]
        if not strat_trades:
            return {"strategy": strategy, "trade_count": 0, "error": "No trades for this strategy"}

        # Attribute all trades
        strat_attrs = []
        all_attrs = []
        for t in trades:
            if "entry_quality" in t and "exit_quality" in t:
                attr = t
            else:
                attr = self.attribute_trade(t)
            all_attrs.append(attr)
            if _safe_get(t, "strategy", "") == strategy:
                strat_attrs.append(attr)

        components = ["entry_quality", "exit_quality", "timing",
                      "market_context", "sizing", "strategy_selection"]

        # Strategy averages
        strat_avgs = {}
        for comp in components:
            vals = [a.get(comp, 0) for a in strat_attrs]
            strat_avgs[comp] = round(sum(vals) / len(vals), 1) if vals else 0

        # System averages (for comparison)
        system_avgs = {}
        for comp in components:
            vals = [a.get(comp, 0) for a in all_attrs]
            system_avgs[comp] = round(sum(vals) / len(vals), 1) if vals else 0

        # Delta: strategy vs system
        deltas = {}
        for comp in components:
            deltas[comp] = round(strat_avgs[comp] - system_avgs[comp], 1)

        # Strategy-specific weaknesses
        weaknesses = []
        for comp in components:
            if strat_avgs[comp] < -20 and deltas[comp] < -10:
                weaknesses.append(comp)

        strat_r = [_safe_get(t, "r_multiple", 0) for t in strat_trades]
        avg_r = sum(strat_r) / len(strat_r) if strat_r else 0
        wr = sum(1 for r in strat_r if r > 0) / len(strat_r) * 100 if strat_r else 0

        return {
            "strategy": strategy,
            "trade_count": len(strat_attrs),
            "averages": strat_avgs,
            "system_averages": system_avgs,
            "deltas_vs_system": deltas,
            "weaknesses": weaknesses,
            "avg_r": round(avg_r, 3),
            "win_rate": round(wr, 1),
            "composite_avg": round(
                sum(strat_avgs[c] * COMPONENT_WEIGHTS[c] for c in components), 1
            ),
        }

    # ════════════════════════════════════════════════════════════════════
    #  5. REGIME-LEVEL ATTRIBUTION
    # ════════════════════════════════════════════════════════════════════

    def get_regime_attribution(self, trades: list[dict], regime: str) -> dict:
        """Filter trades by entry regime and compute average attribution.

        Args:
            trades: list of trade dicts
            regime: regime string to filter by

        Returns:
            dict with regime-specific averages and insight
        """
        regime_upper = regime.upper()
        regime_trades = [
            t for t in trades
            if _safe_get(t, "regime_at_entry", "").upper() == regime_upper
        ]
        if not regime_trades:
            return {"regime": regime, "trade_count": 0, "error": "No trades in this regime"}

        # Attribute
        regime_attrs = []
        all_attrs = []
        for t in trades:
            if "entry_quality" in t and "exit_quality" in t:
                attr = t
            else:
                attr = self.attribute_trade(t)
            all_attrs.append(attr)
            if _safe_get(t, "regime_at_entry", "").upper() == regime_upper:
                regime_attrs.append(attr)

        components = ["entry_quality", "exit_quality", "timing",
                      "market_context", "sizing", "strategy_selection"]

        regime_avgs = {}
        for comp in components:
            vals = [a.get(comp, 0) for a in regime_attrs]
            regime_avgs[comp] = round(sum(vals) / len(vals), 1) if vals else 0

        system_avgs = {}
        for comp in components:
            vals = [a.get(comp, 0) for a in all_attrs]
            system_avgs[comp] = round(sum(vals) / len(vals), 1) if vals else 0

        deltas = {}
        for comp in components:
            deltas[comp] = round(regime_avgs[comp] - system_avgs[comp], 1)

        regime_r = [_safe_get(t, "r_multiple", 0) for t in regime_trades]
        avg_r = sum(regime_r) / len(regime_r) if regime_r else 0
        wr = sum(1 for r in regime_r if r > 0) / len(regime_r) * 100 if regime_r else 0

        # Strategy breakdown within regime
        strat_breakdown = defaultdict(lambda: {"count": 0, "total_r": 0.0, "wins": 0})
        for t in regime_trades:
            s = _safe_get(t, "strategy", "UNKNOWN")
            r = _safe_get(t, "r_multiple", 0)
            strat_breakdown[s]["count"] += 1
            strat_breakdown[s]["total_r"] += r
            if r > 0:
                strat_breakdown[s]["wins"] += 1

        best_strategy = None
        best_avg_r = -float("inf")
        for s, data in strat_breakdown.items():
            s_avg = data["total_r"] / data["count"] if data["count"] > 0 else 0
            if s_avg > best_avg_r and data["count"] >= 3:
                best_avg_r = s_avg
                best_strategy = s

        # Generate regime-specific insight
        weakest = min(regime_avgs.items(), key=lambda x: x[1])
        insight = (
            f"In {regime} regime ({len(regime_attrs)} trades): "
            f"WR={wr:.0f}%, Avg R={avg_r:+.2f}. "
            f"Weakest area is {weakest[0]} ({weakest[1]:+.0f}). "
        )
        if best_strategy:
            insight += f"Best strategy in this regime: {best_strategy} (avg R={best_avg_r:+.2f})."

        return {
            "regime": regime,
            "trade_count": len(regime_attrs),
            "averages": regime_avgs,
            "system_averages": system_avgs,
            "deltas_vs_system": deltas,
            "avg_r": round(avg_r, 3),
            "win_rate": round(wr, 1),
            "best_strategy": best_strategy,
            "strategy_breakdown": dict(strat_breakdown),
            "insight": insight,
        }

    # ════════════════════════════════════════════════════════════════════
    #  6. DAILY ATTRIBUTION REPORT
    # ════════════════════════════════════════════════════════════════════

    def generate_daily_report(self, trades_today: list[dict]) -> str:
        """Generate a formatted daily attribution report for Telegram.

        Args:
            trades_today: list of trade dicts from today's session

        Returns:
            Formatted string suitable for Telegram message
        """
        if not trades_today:
            return "DAILY ATTRIBUTION REPORT\n" + "=" * 30 + "\nNo trades today."

        # Attribute all today's trades
        attributions = []
        for t in trades_today:
            if "entry_quality" in t and "exit_quality" in t:
                attributions.append(t)
            else:
                attributions.append(self.attribute_trade(t))

        n = len(attributions)
        components = ["entry_quality", "exit_quality", "timing",
                      "market_context", "sizing", "strategy_selection"]
        friendly = {
            "entry_quality": "Entry",
            "exit_quality": "Exit",
            "timing": "Timing",
            "market_context": "Market",
            "sizing": "Sizing",
            "strategy_selection": "Strategy",
        }

        # Today's averages
        today_avgs = {}
        for comp in components:
            vals = [a.get(comp, 0) for a in attributions]
            today_avgs[comp] = sum(vals) / len(vals) if vals else 0

        # Composite
        composites = [a.get("composite_score", 0) for a in attributions]
        avg_composite = sum(composites) / n
        avg_grade = self._letter_grade(avg_composite)

        # R stats
        r_vals = [a.get("r_multiple", 0) for a in attributions]
        total_r = sum(r_vals)
        avg_r = total_r / n
        winners = sum(1 for r in r_vals if r > 0)
        losers = sum(1 for r in r_vals if r < 0)

        # Best and worst trade
        best_trade = max(attributions, key=lambda a: a.get("composite_score", 0))
        worst_trade = min(attributions, key=lambda a: a.get("composite_score", 0))

        # Rolling 50 comparison (from attribution history)
        rolling_avgs = {}
        recent_history = self._attribution_history[-50:]
        if len(recent_history) >= 10:
            for comp in components:
                vals = [a.get(comp, 0) for a in recent_history]
                rolling_avgs[comp] = sum(vals) / len(vals) if vals else 0

        lines = [
            "DAILY ATTRIBUTION REPORT",
            "=" * 30,
            f"Trades: {n} | W/L: {winners}/{losers} | Total R: {total_r:+.2f} | Avg R: {avg_r:+.2f}",
            f"Composite Grade: {avg_grade} ({avg_composite:+.1f})",
            "",
            "-- COMPONENT SCORES --",
        ]

        for comp in components:
            today_val = today_avgs[comp]
            delta_str = ""
            if comp in rolling_avgs:
                delta = today_val - rolling_avgs[comp]
                arrow = "^" if delta > 5 else "v" if delta < -5 else "="
                delta_str = f" [{arrow} {delta:+.0f} vs 50-avg]"
            lines.append(f"  {friendly[comp]:>10s}: {today_val:+6.1f}{delta_str}")

        # Best trade breakdown
        lines.append("")
        lines.append("-- BEST TRADE --")
        lines.append(
            f"  {best_trade.get('direction', '')} {best_trade.get('ticker', '')} "
            f"({best_trade.get('strategy', '')}) R={best_trade.get('r_multiple', 0):+.2f} "
            f"Grade={best_trade.get('grade', '?')}"
        )
        lines.append(
            f"  E={best_trade.get('entry_quality', 0):+.0f} "
            f"X={best_trade.get('exit_quality', 0):+.0f} "
            f"T={best_trade.get('timing', 0):+.0f} "
            f"M={best_trade.get('market_context', 0):+.0f} "
            f"Sz={best_trade.get('sizing', 0):+.0f} "
            f"St={best_trade.get('strategy_selection', 0):+.0f}"
        )

        # Worst trade breakdown
        lines.append("")
        lines.append("-- WORST TRADE --")
        lines.append(
            f"  {worst_trade.get('direction', '')} {worst_trade.get('ticker', '')} "
            f"({worst_trade.get('strategy', '')}) R={worst_trade.get('r_multiple', 0):+.2f} "
            f"Grade={worst_trade.get('grade', '?')}"
        )
        lines.append(
            f"  E={worst_trade.get('entry_quality', 0):+.0f} "
            f"X={worst_trade.get('exit_quality', 0):+.0f} "
            f"T={worst_trade.get('timing', 0):+.0f} "
            f"M={worst_trade.get('market_context', 0):+.0f} "
            f"Sz={worst_trade.get('sizing', 0):+.0f} "
            f"St={worst_trade.get('strategy_selection', 0):+.0f}"
        )

        # Suggestions from worst trade
        _, suggestions = self.grade_trade(worst_trade)
        if suggestions:
            lines.append("")
            lines.append("-- KEY IMPROVEMENT --")
            lines.append(f"  {suggestions[0]}")

        # Weakest component today
        weakest = min(today_avgs.items(), key=lambda x: x[1])
        strongest = max(today_avgs.items(), key=lambda x: x[1])
        lines.append("")
        lines.append("-- FOCUS AREAS --")
        lines.append(f"  Weakest: {friendly[weakest[0]]} ({weakest[1]:+.1f})")
        lines.append(f"  Strongest: {friendly[strongest[0]]} ({strongest[1]:+.1f})")

        return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════
#  SELF-TEST
# ════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import random

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    print("\n" + "=" * 60)
    print("NZT-48 Performance Attribution Engine — Self-Test")
    print("=" * 60)

    # Build some mock strategy-regime stats
    mock_strat_regime_stats = {
        "S1": {
            "TRENDING_UP": {"win_rate": 65, "trades": 40, "avg_r": 1.2},
            "RANGE_BOUND": {"win_rate": 42, "trades": 25, "avg_r": 0.1},
            "TRENDING_DOWN": {"win_rate": 35, "trades": 20, "avg_r": -0.3},
        },
        "S3": {
            "RANGE_BOUND": {"win_rate": 62, "trades": 35, "avg_r": 0.9},
            "TRENDING_UP": {"win_rate": 38, "trades": 22, "avg_r": -0.1},
        },
        "S5": {
            "TRENDING_UP": {"win_rate": 58, "trades": 30, "avg_r": 1.5},
            "TRENDING_DOWN": {"win_rate": 30, "trades": 15, "avg_r": -0.6},
        },
    }

    engine = PerformanceAttributionEngine(strategy_regime_stats=mock_strat_regime_stats)

    # Create 10 mock trades with varying quality
    mock_trades = [
        {
            "ticker": "AAPL", "direction": "LONG", "strategy": "S1",
            "entry_price": 175.20, "exit_price": 178.50,
            "entry_time": "2025-03-15T10:05:00+00:00", "exit_time": "2025-03-15T11:30:00+00:00",
            "shares": 100, "r_multiple": 2.1, "risk_dollars": 150, "mae_r": 0.2, "mfe_r": 2.5,
            "regime_at_entry": "TRENDING_UP", "regime_at_exit": "TRENDING_UP",
            "confidence": 82, "session_high": 179.00, "session_low": 174.50,
            "equity": 100000, "position_risk_pct": 0.15,
            "indicator_snapshot_entry": {"vix": 14.2}, "indicator_snapshot_exit": {"vix": 13.8},
            "duration_minutes": 85,
        },
        {
            "ticker": "TSLA", "direction": "LONG", "strategy": "S1",
            "entry_price": 245.80, "exit_price": 243.00,
            "entry_time": "2025-03-15T09:45:00+00:00", "exit_time": "2025-03-15T10:50:00+00:00",
            "shares": 50, "r_multiple": -1.0, "risk_dollars": 140, "mae_r": 1.2, "mfe_r": 0.3,
            "regime_at_entry": "TRENDING_DOWN", "regime_at_exit": "TRENDING_DOWN",
            "confidence": 58, "session_high": 248.00, "session_low": 242.50,
            "equity": 100000, "position_risk_pct": 0.14,
            "indicator_snapshot_entry": {"vix": 18.5}, "indicator_snapshot_exit": {"vix": 20.1},
            "duration_minutes": 65,
        },
        {
            "ticker": "NVDA", "direction": "LONG", "strategy": "S3",
            "entry_price": 890.00, "exit_price": 895.50,
            "entry_time": "2025-03-15T10:20:00+00:00", "exit_time": "2025-03-15T10:55:00+00:00",
            "shares": 20, "r_multiple": 1.1, "risk_dollars": 100, "mae_r": 0.4, "mfe_r": 1.8,
            "regime_at_entry": "RANGE_BOUND", "regime_at_exit": "RANGE_BOUND",
            "confidence": 71, "session_high": 898.00, "session_low": 887.00,
            "equity": 100000, "position_risk_pct": 0.10,
            "indicator_snapshot_entry": {"vix": 15.0}, "indicator_snapshot_exit": {"vix": 14.8},
            "duration_minutes": 35,
        },
        {
            "ticker": "META", "direction": "SHORT", "strategy": "S3",
            "entry_price": 510.20, "exit_price": 507.00,
            "entry_time": "2025-03-15T11:00:00+00:00", "exit_time": "2025-03-15T11:40:00+00:00",
            "shares": 30, "r_multiple": 1.5, "risk_dollars": 65, "mae_r": 0.15, "mfe_r": 1.6,
            "regime_at_entry": "RANGE_BOUND", "regime_at_exit": "RANGE_BOUND",
            "confidence": 75, "session_high": 512.00, "session_low": 505.50,
            "equity": 100000, "position_risk_pct": 0.065,
            "indicator_snapshot_entry": {"vix": 16.0}, "indicator_snapshot_exit": {"vix": 16.3},
            "duration_minutes": 40,
        },
        {
            "ticker": "AMZN", "direction": "LONG", "strategy": "S5",
            "entry_price": 185.40, "exit_price": 188.20,
            "entry_time": "2025-03-15T09:35:00+00:00", "exit_time": "2025-03-15T14:00:00+00:00",
            "shares": 80, "r_multiple": 1.8, "risk_dollars": 125, "mae_r": 0.6, "mfe_r": 2.8,
            "regime_at_entry": "TRENDING_UP", "regime_at_exit": "TRENDING_UP",
            "confidence": 78, "session_high": 189.50, "session_low": 184.80,
            "equity": 100000, "position_risk_pct": 0.125,
            "indicator_snapshot_entry": {"vix": 13.5}, "indicator_snapshot_exit": {"vix": 12.9},
            "duration_minutes": 265,
        },
        {
            "ticker": "MSFT", "direction": "LONG", "strategy": "S1",
            "entry_price": 420.50, "exit_price": 419.00,
            "entry_time": "2025-03-15T10:10:00+00:00", "exit_time": "2025-03-15T10:25:00+00:00",
            "shares": 40, "r_multiple": -0.5, "risk_dollars": 120, "mae_r": 0.8, "mfe_r": 0.15,
            "regime_at_entry": "RANGE_BOUND", "regime_at_exit": "TRENDING_DOWN",
            "confidence": 62, "session_high": 422.00, "session_low": 418.50,
            "equity": 100000, "position_risk_pct": 0.12,
            "indicator_snapshot_entry": {"vix": 15.5}, "indicator_snapshot_exit": {"vix": 17.0},
            "duration_minutes": 15,
        },
        {
            "ticker": "GOOG", "direction": "SHORT", "strategy": "S5",
            "entry_price": 155.80, "exit_price": 157.20,
            "entry_time": "2025-03-15T10:30:00+00:00", "exit_time": "2025-03-15T15:00:00+00:00",
            "shares": 100, "r_multiple": -0.7, "risk_dollars": 200, "mae_r": 1.5, "mfe_r": 0.4,
            "regime_at_entry": "TRENDING_UP", "regime_at_exit": "TRENDING_UP",
            "confidence": 55, "session_high": 158.50, "session_low": 155.00,
            "equity": 100000, "position_risk_pct": 0.20,
            "indicator_snapshot_entry": {"vix": 14.0}, "indicator_snapshot_exit": {"vix": 13.2},
            "duration_minutes": 270,
        },
        {
            "ticker": "AMD", "direction": "LONG", "strategy": "S1",
            "entry_price": 178.30, "exit_price": 182.10,
            "entry_time": "2025-03-15T09:40:00+00:00", "exit_time": "2025-03-15T11:10:00+00:00",
            "shares": 70, "r_multiple": 2.5, "risk_dollars": 105, "mae_r": 0.1, "mfe_r": 2.8,
            "regime_at_entry": "TRENDING_UP", "regime_at_exit": "TRENDING_UP",
            "confidence": 85, "session_high": 183.00, "session_low": 177.50,
            "equity": 100000, "position_risk_pct": 0.105,
            "indicator_snapshot_entry": {"vix": 13.0}, "indicator_snapshot_exit": {"vix": 12.5},
            "duration_minutes": 90,
        },
        {
            "ticker": "SPY", "direction": "LONG", "strategy": "S11",
            "entry_price": 512.00, "exit_price": 510.50,
            "entry_time": "2025-03-15T10:00:00+00:00", "exit_time": "2025-03-15T13:30:00+00:00",
            "shares": 50, "r_multiple": -0.6, "risk_dollars": 125, "mae_r": 1.0, "mfe_r": 0.5,
            "regime_at_entry": "RANGE_BOUND", "regime_at_exit": "TRENDING_DOWN",
            "confidence": 60, "session_high": 513.50, "session_low": 509.80,
            "equity": 100000, "position_risk_pct": 0.125,
            "indicator_snapshot_entry": {"vix": 16.0}, "indicator_snapshot_exit": {"vix": 18.5},
            "duration_minutes": 210,
        },
        {
            "ticker": "QQQ", "direction": "LONG", "strategy": "S13",
            "entry_price": 440.20, "exit_price": 443.80,
            "entry_time": "2025-03-15T09:50:00+00:00", "exit_time": "2025-03-15T10:45:00+00:00",
            "shares": 60, "r_multiple": 1.9, "risk_dollars": 115, "mae_r": 0.25, "mfe_r": 2.1,
            "regime_at_entry": "TRENDING_UP", "regime_at_exit": "TRENDING_UP",
            "confidence": 80, "session_high": 444.50, "session_low": 439.00,
            "equity": 100000, "position_risk_pct": 0.115,
            "indicator_snapshot_entry": {"vix": 13.8}, "indicator_snapshot_exit": {"vix": 13.2},
            "duration_minutes": 55,
        },
    ]

    # ── Attribute each trade ────────────────────────────────────────
    print("\n--- INDIVIDUAL TRADE ATTRIBUTIONS ---\n")
    all_attributions = []
    for i, trade in enumerate(mock_trades, 1):
        attr = engine.attribute_trade(trade)
        grade, suggestions = engine.grade_trade(attr)
        all_attributions.append(attr)
        print(f"Trade {i}: {attr['direction']} {attr['ticker']} ({attr['strategy']})")
        print(f"  R-Multiple: {attr['r_multiple']:+.2f}")
        print(f"  Entry={attr['entry_quality']:+.0f}  Exit={attr['exit_quality']:+.0f}  "
              f"Timing={attr['timing']:+.0f}  Market={attr['market_context']:+.0f}  "
              f"Sizing={attr['sizing']:+.0f}  Strategy={attr['strategy_selection']:+.0f}")
        print(f"  Composite: {attr['composite_score']:+.1f} ({grade})")
        if suggestions:
            print(f"  Suggestion: {suggestions[0][:100]}")
        print()

    # ── Rolling analytics ───────────────────────────────────────────
    print("\n--- ROLLING ATTRIBUTION (last 10 trades) ---\n")
    rolling = engine.get_rolling_attribution(mock_trades, window=10)
    print(f"Window: {rolling['trade_count']} trades")
    print(f"Avg Composite: {rolling['avg_composite']:+.1f} ({rolling['avg_grade']})")
    print(f"Win Rate: {rolling['win_rate']:.1f}%  Avg R: {rolling['avg_r']:+.3f}")
    print(f"Grade Distribution: {rolling['grade_distribution']}")
    print(f"Weakest: {rolling['weakest_component']['name']} ({rolling['weakest_component']['score']:+.1f})")
    print(f"Strongest: {rolling['strongest_component']['name']} ({rolling['strongest_component']['score']:+.1f})")
    print(f"\nInsight: {rolling['insight']}")

    # ── Strategy attribution ────────────────────────────────────────
    print("\n--- STRATEGY ATTRIBUTION: S1 ---\n")
    s1_attr = engine.get_strategy_attribution(mock_trades, "S1")
    print(f"Strategy: {s1_attr['strategy']} ({s1_attr['trade_count']} trades)")
    print(f"Avg R: {s1_attr['avg_r']:+.3f}  WR: {s1_attr['win_rate']:.1f}%")
    print(f"Averages: {s1_attr['averages']}")
    print(f"vs System: {s1_attr['deltas_vs_system']}")
    if s1_attr.get("weaknesses"):
        print(f"Weaknesses: {s1_attr['weaknesses']}")

    # ── Regime attribution ──────────────────────────────────────────
    print("\n--- REGIME ATTRIBUTION: TRENDING_UP ---\n")
    regime_attr = engine.get_regime_attribution(mock_trades, "TRENDING_UP")
    print(f"Regime: {regime_attr['regime']} ({regime_attr['trade_count']} trades)")
    print(f"Avg R: {regime_attr['avg_r']:+.3f}  WR: {regime_attr['win_rate']:.1f}%")
    print(f"Best Strategy: {regime_attr.get('best_strategy', 'N/A')}")
    print(f"Averages: {regime_attr['averages']}")
    print(f"Insight: {regime_attr.get('insight', '')}")

    # ── Daily report ────────────────────────────────────────────────
    print("\n--- DAILY REPORT ---\n")
    report = engine.generate_daily_report(mock_trades)
    print(report)

    print("\n" + "=" * 60)
    print("Self-test complete. All attribution methods executed successfully.")
    print("=" * 60)
