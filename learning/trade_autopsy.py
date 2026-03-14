"""
NZT-48 Trade Autopsy — Automatic Post-Trade Analysis
After every closed trade, generates a structured analysis covering:
1. Setup Grade — was the pattern textbook?
2. Timing Grade — did we enter at the optimal point?
3. Management Grade — did the profit ladder perform optimally?
4. Market Context — did the broad market help or hurt?
5. What-If Analysis — what if we'd held to final target?

Feeds directly into the learning engine's indicator scoring
and regime performance matrix.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import httpx  # noqa: F401 — pre-import for AI enhancement
except ImportError:
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger("nzt48.autopsy")


@dataclass
class TradeAutopsy:
    """Complete post-trade analysis."""
    trade_id: str = ""
    ticker: str = ""
    strategy: str = ""
    direction: str = ""

    # Grades (0-100)
    setup_grade: float = 0.0
    setup_notes: str = ""

    timing_grade: float = 0.0
    timing_notes: str = ""

    management_grade: float = 0.0
    management_notes: str = ""

    market_context_grade: float = 0.0
    market_context_notes: str = ""

    # What-if
    held_to_target_pnl: float = 0.0
    held_to_target_r: float = 0.0
    optimal_exit_r: float = 0.0  # MFE-based
    actual_exit_r: float = 0.0
    exit_efficiency: float = 0.0  # actual / optimal (0-100%)

    # J-05: Entry Timing Score — (daily_high - entry) / (daily_high - daily_low)
    # For LONG: lower = better (entered near low). Target: < 0.50
    # For SHORT: (entry - daily_low) / (daily_high - daily_low). Target: < 0.50
    entry_timing_score: float = -1.0  # -1 = not computed (missing data)

    # Key lessons
    primary_lesson: str = ""
    indicator_verdict: str = ""  # Which indicator was most predictive

    # Overall
    overall_grade: float = 0.0  # Weighted average

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


class TradeAutopsyEngine:
    """Generates automatic post-trade analysis.

    Wired into VirtualTrader via callback — every closed trade
    gets an autopsy that feeds the learning engine.
    """

    def __init__(self) -> None:
        self._autopsies: list[TradeAutopsy] = []

    def analyse(self, trade, entry_indicators: dict = None,
                exit_indicators: dict = None, market_ctx: dict = None) -> TradeAutopsy:
        """Generate a full autopsy for a completed trade.

        Args:
            trade: VirtualTrade object (or dict with trade fields).
            entry_indicators: Indicator snapshot at entry time.
            exit_indicators: Indicator snapshot at exit time.
            market_ctx: Market context at trade time.

        Returns:
            TradeAutopsy with all grades and analysis.
        """
        entry_ind = entry_indicators or {}
        exit_ind = exit_indicators or {}
        ctx = market_ctx or {}

        autopsy = TradeAutopsy(
            trade_id=getattr(trade, 'id', '') or trade.get('id', ''),
            ticker=getattr(trade, 'ticker', '') or trade.get('ticker', ''),
            strategy=getattr(trade, 'strategy', '') or trade.get('strategy', ''),
            direction=getattr(trade, 'direction', '') or trade.get('direction', ''),
        )

        # --- 1. Setup Grade ---
        autopsy.setup_grade, autopsy.setup_notes = self._grade_setup(trade, entry_ind)

        # --- 2. Timing Grade ---
        autopsy.timing_grade, autopsy.timing_notes = self._grade_timing(trade)

        # --- 3. Management Grade ---
        autopsy.management_grade, autopsy.management_notes = self._grade_management(trade)

        # --- 4. Market Context Grade ---
        autopsy.market_context_grade, autopsy.market_context_notes = self._grade_context(trade, ctx)

        # --- 5. What-If Analysis ---
        self._analyse_what_if(autopsy, trade)

        # --- J-05: Entry Timing Score ---
        autopsy.entry_timing_score = self._compute_entry_timing_score(trade)

        # --- Indicator Verdict ---
        autopsy.indicator_verdict = self._identify_key_indicator(trade, entry_ind)

        # --- Primary Lesson ---
        autopsy.primary_lesson = self._extract_lesson(autopsy, trade)

        # --- Overall Grade (weighted) ---
        autopsy.overall_grade = (
            autopsy.setup_grade * 0.30 +
            autopsy.timing_grade * 0.20 +
            autopsy.management_grade * 0.30 +
            autopsy.market_context_grade * 0.20
        )

        self._autopsies.append(autopsy)

        r_mult = getattr(trade, 'r_multiple', 0) or trade.get('r_multiple', 0)
        logger.info(
            "AUTOPSY: %s %s %s R=%.2f | Setup=%d Timing=%d Mgmt=%d Mkt=%d | %s",
            autopsy.direction, autopsy.ticker, autopsy.strategy,
            r_mult,
            autopsy.setup_grade, autopsy.timing_grade,
            autopsy.management_grade, autopsy.market_context_grade,
            autopsy.primary_lesson[:60],
        )

        return autopsy

    def _grade_setup(self, trade, indicators: dict) -> tuple[float, str]:
        """Grade the setup quality based on entry conditions."""
        grade = 50.0  # Base
        notes = []

        confidence = getattr(trade, 'confidence', 0) or trade.get('confidence', 0)
        peak_r = getattr(trade, 'peak_r', 0) or trade.get('peak_r', 0)

        # High confidence = better setup identification
        if confidence >= 80:
            grade += 25
            notes.append("High confidence setup")
        elif confidence >= 70:
            grade += 15
            notes.append("Good confidence")
        elif confidence < 60:
            grade -= 15
            notes.append("Low confidence — setup was marginal")

        # Did it reach +1R? (confirms setup was correct)
        if peak_r >= 1.0:
            grade += 20
            notes.append("Reached +1R (setup confirmed)")
        elif peak_r >= 0.5:
            grade += 10
            notes.append("Reached +0.5R (setup partially confirmed)")
        elif peak_r < 0:
            grade -= 20
            notes.append("Never went positive (setup failed)")

        # RVOL check — was there volume confirming the setup?
        rvol = indicators.get("rvol", 0) or indicators.get("rvol", 0)
        if rvol >= 2.0:
            grade += 10
            notes.append(f"Strong volume confirmation (RVOL={rvol:.1f})")
        elif rvol < 1.0:
            grade -= 10
            notes.append(f"Weak volume (RVOL={rvol:.1f})")

        return max(0, min(100, grade)), "; ".join(notes) if notes else "Standard setup"

    def _grade_timing(self, trade) -> tuple[float, str]:
        """Grade entry timing based on MAE (Maximum Adverse Excursion)."""
        grade = 50.0
        notes = []

        trough_r = getattr(trade, 'trough_r', 0) or trade.get('trough_r', 0)
        peak_r = getattr(trade, 'peak_r', 0) or trade.get('peak_r', 0)

        # MAE-based timing: smaller MAE = better entry
        if abs(trough_r) < 0.3:
            grade += 30
            notes.append(f"Excellent timing (MAE={trough_r:.2f}R)")
        elif abs(trough_r) < 0.5:
            grade += 15
            notes.append(f"Good timing (MAE={trough_r:.2f}R)")
        elif abs(trough_r) < 0.8:
            notes.append(f"Acceptable timing (MAE={trough_r:.2f}R)")
        else:
            grade -= 25
            notes.append(f"Poor timing — went to {trough_r:.2f}R before recovering")

        # Duration factor: fast winners = good timing
        duration = getattr(trade, 'duration_minutes', 0) or trade.get('duration_minutes', 0)
        r_mult = getattr(trade, 'r_multiple', 0) or trade.get('r_multiple', 0)
        if r_mult > 0 and duration < 30:
            grade += 15
            notes.append("Fast winner (< 30min)")
        elif r_mult < 0 and duration > 120:
            grade -= 15
            notes.append("Slow loser (held > 2hrs)")

        return max(0, min(100, grade)), "; ".join(notes) if notes else "Standard timing"

    def _grade_management(self, trade) -> tuple[float, str]:
        """Grade trade management (profit ladder execution)."""
        grade = 50.0
        notes = []

        peak_r = getattr(trade, 'peak_r', 0) or trade.get('peak_r', 0)
        r_mult = getattr(trade, 'r_multiple', 0) or trade.get('r_multiple', 0)
        partials = getattr(trade, 'partials', []) or trade.get('partials', [])
        exit_reason = getattr(trade, 'exit_reason', '') or trade.get('exit_reason', '')

        # Exit efficiency: how much of the MFE did we capture?
        if peak_r > 0:
            efficiency = (r_mult / peak_r) * 100 if peak_r > 0 else 0
            if efficiency >= 70:
                grade += 30
                notes.append(f"Excellent exit efficiency ({efficiency:.0f}% of MFE)")
            elif efficiency >= 50:
                grade += 15
                notes.append(f"Good exit efficiency ({efficiency:.0f}% of MFE)")
            elif efficiency >= 30:
                notes.append(f"Fair exit efficiency ({efficiency:.0f}% of MFE)")
            else:
                grade -= 20
                notes.append(f"Poor exit — captured only {efficiency:.0f}% of +{peak_r:.1f}R MFE")

        # Did partials fire correctly?
        if isinstance(partials, list) and len(partials) > 0:
            grade += 10
            notes.append(f"Partials taken ({len(partials)} fills)")
        elif peak_r >= 1.0 and not partials:
            grade -= 15
            notes.append("No partials despite +1R — profit ladder may have failed")

        # Stopped out vs managed exit
        if exit_reason == "STOP_HIT" and peak_r >= 1.0:
            grade -= 20
            notes.append(f"STOPPED after reaching +{peak_r:.1f}R — stop management failed")

        return max(0, min(100, grade)), "; ".join(notes) if notes else "Standard management"

    def _grade_context(self, trade, ctx: dict) -> tuple[float, str]:
        """Grade how the broad market affected the trade."""
        grade = 50.0
        notes = []

        regime = getattr(trade, 'regime_at_entry', '') or trade.get('regime_at_entry', '')
        r_mult = getattr(trade, 'r_multiple', 0) or trade.get('r_multiple', 0)
        direction = getattr(trade, 'direction', '') or trade.get('direction', '')

        # Regime alignment
        up_regimes = {"TRENDING_UP_STRONG", "TRENDING_UP_MOD"}
        down_regimes = {"TRENDING_DOWN_STRONG", "TRENDING_DOWN_MOD"}

        if direction == "LONG" and regime in up_regimes:
            grade += 20
            notes.append("Regime-aligned (LONG in uptrend)")
        elif direction == "SHORT" and regime in down_regimes:
            grade += 20
            notes.append("Regime-aligned (SHORT in downtrend)")
        elif direction == "LONG" and regime in down_regimes:
            grade -= 20
            notes.append("Counter-regime (LONG in downtrend)")
        elif direction == "SHORT" and regime in up_regimes:
            grade -= 20
            notes.append("Counter-regime (SHORT in uptrend)")
        elif regime == "RANGE_BOUND":
            notes.append("Range-bound regime — direction neutral")

        # Winner in bad context = great trade
        if r_mult > 1.0 and regime in ("RANGE_BOUND", "HIGH_VOLATILITY"):
            grade += 15
            notes.append("Won despite difficult market context")

        return max(0, min(100, grade)), "; ".join(notes) if notes else "Neutral context"

    def _analyse_what_if(self, autopsy: TradeAutopsy, trade) -> None:
        """Calculate hypothetical outcomes with different management."""
        peak_r = getattr(trade, 'peak_r', 0) or trade.get('peak_r', 0)
        r_mult = getattr(trade, 'r_multiple', 0) or trade.get('r_multiple', 0)
        risk_dollars = getattr(trade, 'risk_dollars', 0) or trade.get('risk_dollars', 0)

        autopsy.optimal_exit_r = peak_r
        autopsy.actual_exit_r = r_mult

        if peak_r > 0 and risk_dollars > 0:
            autopsy.held_to_target_r = peak_r
            autopsy.held_to_target_pnl = peak_r * risk_dollars
            autopsy.exit_efficiency = (r_mult / peak_r * 100) if peak_r > 0 else 0
        else:
            autopsy.exit_efficiency = 0

    def _identify_key_indicator(self, trade, indicators: dict) -> str:
        """Identify which indicator was most predictive for this trade."""
        r_mult = getattr(trade, 'r_multiple', 0) or trade.get('r_multiple', 0)

        verdicts = []

        # RSI
        rsi = indicators.get("rsi", indicators.get("rsi14", 50))
        if rsi and r_mult > 0:
            if (rsi < 30 and (getattr(trade, 'direction', '') == "LONG")) or \
               (rsi > 70 and (getattr(trade, 'direction', '') == "SHORT")):
                verdicts.append("RSI was contrarian-correct")

        # VWAP
        vwap = indicators.get("vwap", 0)
        price = indicators.get("price", 0)
        if vwap and price and r_mult > 0:
            direction = getattr(trade, 'direction', '') or trade.get('direction', '')
            if direction == "LONG" and price > vwap:
                verdicts.append("VWAP alignment confirmed")
            elif direction == "SHORT" and price < vwap:
                verdicts.append("VWAP alignment confirmed")

        # EMA alignment
        ema9 = indicators.get("ema9", 0)
        ema20 = indicators.get("ema20", 0)
        if ema9 and ema20:
            if ema9 > ema20 and r_mult > 0:
                verdicts.append("EMA alignment predicted correctly")
            elif ema9 < ema20 and r_mult < 0:
                verdicts.append("EMA misalignment predicted loss")

        return verdicts[0] if verdicts else "No single indicator stood out"

    def _compute_entry_timing_score(self, trade) -> float:
        """J-05: Entry Timing Score: (daily_high - entry) / (daily_high - daily_low) for LONG.
        Lower is better (entered near the low). Target < 0.50."""
        if isinstance(trade, dict):
            high = trade.get('daily_high', 0)
            low = trade.get('daily_low', 0)
            entry = trade.get('entry_price', 0)
            direction = trade.get('direction', 'LONG')
        else:
            high = getattr(trade, 'daily_high', 0)
            low = getattr(trade, 'daily_low', 0)
            entry = getattr(trade, 'entry_price', 0)
            direction = getattr(trade, 'direction', 'LONG')
        if high <= low or high == 0:
            return 0.5  # default mid-range
        if direction == 'LONG':
            return (high - entry) / (high - low)
        else:  # SHORT/INVERSE
            return (entry - low) / (high - low)

    def _extract_lesson(self, autopsy: TradeAutopsy, trade) -> str:
        """Extract the primary lesson from this trade."""
        r_mult = getattr(trade, 'r_multiple', 0) or trade.get('r_multiple', 0)
        exit_reason = getattr(trade, 'exit_reason', '') or trade.get('exit_reason', '')

        # Winners
        if r_mult >= 2.0:
            if autopsy.exit_efficiency >= 70:
                return f"{autopsy.strategy} managed well — captured {autopsy.exit_efficiency:.0f}% of move"
            else:
                return f"{autopsy.strategy} left money on table — only {autopsy.exit_efficiency:.0f}% of +{autopsy.optimal_exit_r:.1f}R"

        if r_mult >= 1.0:
            return f"{autopsy.strategy} profitable — setup grade {autopsy.setup_grade:.0f}/100"

        # Losers
        if r_mult < 0:
            if autopsy.setup_grade >= 70:
                return f"Good setup, bad outcome — timing/context grade low ({autopsy.timing_grade:.0f}/{autopsy.market_context_grade:.0f})"
            elif autopsy.timing_grade < 40:
                return f"Entry timing was the problem (MAE-based grade: {autopsy.timing_grade:.0f})"
            elif autopsy.market_context_grade < 40:
                return f"Market context was against us (context grade: {autopsy.market_context_grade:.0f})"
            else:
                return f"Setup was weak — confidence was borderline"

        # Flat
        return "Flat trade — review if setup criteria are too loose"

    def persist(self, conn, autopsy: TradeAutopsy) -> None:
        """Save autopsy to the database."""
        try:
            conn.execute(
                """INSERT OR REPLACE INTO trade_autopsies
                   (trade_id, ticker, strategy, direction,
                    setup_grade, setup_notes, timing_grade, timing_notes,
                    management_grade, management_notes,
                    market_context_grade, market_context_notes,
                    exit_efficiency, optimal_exit_r, actual_exit_r,
                    primary_lesson, indicator_verdict, overall_grade)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (autopsy.trade_id, autopsy.ticker, autopsy.strategy,
                 autopsy.direction, autopsy.setup_grade, autopsy.setup_notes,
                 autopsy.timing_grade, autopsy.timing_notes,
                 autopsy.management_grade, autopsy.management_notes,
                 autopsy.market_context_grade, autopsy.market_context_notes,
                 autopsy.exit_efficiency, autopsy.optimal_exit_r,
                 autopsy.actual_exit_r, autopsy.primary_lesson,
                 autopsy.indicator_verdict, autopsy.overall_grade),
            )
        except Exception as e:
            logger.error("Failed to persist trade autopsy: %s", e)

    async def enhance_with_ai(self, autopsy: TradeAutopsy, trade,
                               ai_api_key: str = "", ai_model: str = "gemini-2.5-flash") -> None:
        """Enhance autopsy with AI-generated insights for significant trades.

        Only called for trades with |R| >= 1.0 — AI is expensive, so we
        only use it on trades worth learning from.

        Updates autopsy.primary_lesson in-place with AI-enhanced lesson.
        """
        if not ai_api_key:
            return

        r_mult = getattr(trade, 'r_multiple', 0) or trade.get('r_multiple', 0)
        if abs(r_mult) < 1.0:
            return  # Not significant enough

        summary = (
            f"Trade: {autopsy.direction} {autopsy.ticker} via {autopsy.strategy}\n"
            f"Result: {r_mult:+.2f}R | Peak: +{autopsy.optimal_exit_r:.2f}R | "
            f"Exit efficiency: {autopsy.exit_efficiency:.0f}%\n"
            f"Setup grade: {autopsy.setup_grade:.0f}/100 ({autopsy.setup_notes})\n"
            f"Timing grade: {autopsy.timing_grade:.0f}/100 ({autopsy.timing_notes})\n"
            f"Management grade: {autopsy.management_grade:.0f}/100 ({autopsy.management_notes})\n"
            f"Context grade: {autopsy.market_context_grade:.0f}/100 ({autopsy.market_context_notes})\n"
            f"Indicator verdict: {autopsy.indicator_verdict}\n"
            f"Current lesson: {autopsy.primary_lesson}"
        )

        prompt = (
            f"You are reviewing a single trade for an automated trading system. "
            f"Based on the data below, write a ONE SENTENCE actionable lesson "
            f"that should be remembered for future trades with this exact "
            f"ticker+strategy+regime combination. Be specific.\n\n{summary}"
        )

        try:
            if httpx is None:
                return  # httpx not installed
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{ai_model}:generateContent",
                    params={"key": ai_api_key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"maxOutputTokens": 100, "temperature": 0.2},
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            ai_lesson = parts[0].get("text", "").strip()
                            if ai_lesson:
                                autopsy.primary_lesson = f"[AI] {ai_lesson}"
                                logger.info("AI lesson for %s: %s", autopsy.ticker, ai_lesson[:80])
        except ImportError:
            pass
        except Exception as e:
            logger.debug("AI autopsy enhancement failed: %s", e)

    def get_aggregate_insights(self, conn) -> dict:
        """Get aggregate insights from all autopsies for the learning engine."""
        try:
            # Strategy performance breakdown
            strategy_grades = conn.execute(
                """SELECT strategy,
                          COUNT(*) as trades,
                          AVG(setup_grade) as avg_setup,
                          AVG(timing_grade) as avg_timing,
                          AVG(management_grade) as avg_mgmt,
                          AVG(market_context_grade) as avg_ctx,
                          AVG(overall_grade) as avg_overall
                   FROM trade_autopsies
                   GROUP BY strategy
                   ORDER BY avg_overall DESC"""
            ).fetchall()

            # Common lessons
            common_lessons = conn.execute(
                """SELECT primary_lesson, COUNT(*) as cnt
                   FROM trade_autopsies
                   GROUP BY primary_lesson
                   ORDER BY cnt DESC
                   LIMIT 10"""
            ).fetchall()

            # Most predictive indicators
            indicator_verdicts = conn.execute(
                """SELECT indicator_verdict, COUNT(*) as cnt
                   FROM trade_autopsies
                   WHERE indicator_verdict != 'No single indicator stood out'
                   GROUP BY indicator_verdict
                   ORDER BY cnt DESC
                   LIMIT 5"""
            ).fetchall()

            return {
                "strategy_grades": [
                    {
                        "strategy": row[0], "trades": row[1],
                        "setup": row[2], "timing": row[3],
                        "management": row[4], "context": row[5],
                        "overall": row[6],
                    }
                    for row in strategy_grades
                ],
                "common_lessons": [
                    {"lesson": row[0], "count": row[1]}
                    for row in common_lessons
                ],
                "top_indicators": [
                    {"indicator": row[0], "count": row[1]}
                    for row in indicator_verdicts
                ],
            }
        except Exception as e:
            logger.error("Aggregate insights failed: %s", e)
            return {"error": str(e)}
